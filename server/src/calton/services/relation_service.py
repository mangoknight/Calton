"""Task relations: create and delete, both **bidirectional** (T31).

Measured against the running Go reference server.

**A relation is two rows.** Creating 951 --subtask--> 952 also writes 952 --parenttask-->
951; deleting either direction removes both. An implementation that writes only the
direction the request named still answers 201, and the originating task still looks
correct — the damage only shows when you look from the *other* end, where the parent
cannot see its child. The delete half is worse: the orphaned inverse row can never be
removed again, because deletion is only reachable from a side that still has a row.

**The same malformed input gives different answers on the two verbs**, and they must not
share a validator:

    PUT    /tasks/951/relations         {"relation_kind": "nosuch"}   400 / 4007
    DELETE /tasks/950/relations/nosuch/953                            404 / 4009

Delete does not validate the kind at all; it looks a relation up by it and finds nothing.
Factoring the kind check into one place — which is what anyone would do — turns that 404
into a 400.

Gate order on create, measured, including the combinations no one thinks to try:

1. **kind validity** — 400/4007, *before* the tasks are looked at. A bad kind against a
   task that does not exist is 400, not 404, and against a forbidden task it is 400, not
   403. Both measured.
2. **the base task** — absent 404/4002, not writable 403/0.
3. **the other task** — absent 404/4002 (the *same* body as an absent base task: the two
   are indistinguishable from outside), unreadable 403/0. This is the only check on the
   far end, and skipping it — natural, because authentication only ever sees the path
   parameter — lets a caller relate their own task to one they cannot see and then read
   its title, description and dates out of ``related_tasks``.
4. **self-relation** — 400/4010, message without a trailing full stop where 4007/4008/4009
   all have one.
5. **duplicate** — 409/4008, the only 409 in the whole corpus. The same "already there"
   is 400/8001 for labels and 400/4021 for assignees.
6. **cycle** — 409/4023, and only for ``subtask``/``parenttask``. Measured: 950 blocking
   954 plus 954 blocking 950 is two 201s, while 950 subtask 951 plus 951 subtask 950 is a
   409. No corpus case covers this; ``test_relations.py`` carries one.
"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from calton.core.errors import CaltonError
from calton.core.policy import ForbiddenError
from calton.models import base_task_query
from calton.models.task import Task
from calton.models.task_relation import TaskRelation
from calton.permissions import task as task_permissions
from calton.schemas.task_relation import (
    HIERARCHICAL_KINDS,
    INVERSE_RELATION,
    RelationKind,
    TaskRelationCreated,
    TaskRelationWrite,
)
from calton.services import task_service


def _task_exists(session: Session, task_id: int) -> bool:
    return session.scalars(base_task_query().where(Task.id == task_id)).one_or_none() is not None


def _require_kind(raw: str) -> RelationKind:
    """400/4007 for anything outside the eleven, including the empty string.

    Deliberately not a Pydantic enum on the request schema: that would answer 412/2002
    with ``invalid_fields``, and upstream binds the value happily and refuses it here.
    """
    try:
        return RelationKind(raw)
    except ValueError:
        raise CaltonError.from_name("models.ErrInvalidRelationKind") from None


def _would_cycle(session: Session, *, task_id: int, other_task_id: int, kind: RelationKind) -> bool:
    """``checkTaskRelationCycle`` (``task_relation.go:140``), walking the same edges.

    Follows rows *of this kind* whose ``other_task_id`` is the node being visited — i.e.
    upwards through the parents of the base task — and reports a cycle when the far end of
    the proposed relation is reachable that way, or when a node repeats. Run only for the
    hierarchical kinds, because only those have an inverse pointing the other way up a
    tree.
    """
    visited: set[int] = set()
    stack = [task_id]
    while stack:
        current = stack.pop()
        if current in visited:
            continue
        if current == other_task_id:
            return True
        visited.add(current)

        stack.extend(
            session.scalars(
                select(TaskRelation.task_id).where(
                    TaskRelation.other_task_id == current,
                    TaskRelation.relation_kind == kind.value,
                )
            )
        )
    return False


def create_relation(
    session: Session, *, task_id: int, data: TaskRelationWrite, user_id: int
) -> TaskRelationCreated:
    """``PUT /tasks/{task}/relations`` — writes both directions, answers 201.

    ``task_id`` here is already the effective one: the router resolves the body's
    ``task_id`` over the path segment, matching Echo's bind order.
    """
    kind = _require_kind(data.relation_kind)
    other_task_id = data.other_task_id

    if not _task_exists(session, task_id):
        raise CaltonError.from_name("models.ErrTaskDoesNotExist")
    if not task_permissions.can_write(session, user_id, task_id):
        raise ForbiddenError()

    if not _task_exists(session, other_task_id):
        raise CaltonError.from_name("models.ErrTaskDoesNotExist")
    other_readable, _ = task_permissions.can_read(session, user_id, other_task_id)
    if not other_readable:
        raise ForbiddenError()

    if task_id == other_task_id:
        raise CaltonError.from_name("models.ErrRelationTasksCannotBeTheSame")

    existing = session.scalars(
        select(TaskRelation).where(
            TaskRelation.task_id == task_id,
            TaskRelation.other_task_id == other_task_id,
            TaskRelation.relation_kind == kind.value,
        )
    ).one_or_none()
    if existing is not None:
        raise CaltonError.from_name("models.ErrRelationAlreadyExists")

    if kind in HIERARCHICAL_KINDS and _would_cycle(
        session, task_id=task_id, other_task_id=other_task_id, kind=kind
    ):
        raise CaltonError.from_name("models.ErrTaskRelationCycle")

    relation = TaskRelation(
        task_id=task_id,
        other_task_id=other_task_id,
        relation_kind=kind.value,
        created_by_id=user_id,
    )
    # The inverse row carries the same creator and the same timestamp origin; nothing on
    # the wire ever shows it, which is exactly why it is easy to leave out.
    inverse = TaskRelation(
        task_id=other_task_id,
        other_task_id=task_id,
        relation_kind=INVERSE_RELATION[kind].value,
        created_by_id=user_id,
    )
    session.add_all([relation, inverse])
    session.commit()

    return TaskRelationCreated(
        task_id=task_id,
        other_task_id=other_task_id,
        relation_kind=kind.value,
        created_by=task_service.user_view(session, user_id),
        created=relation.created,
    )


def delete_relation(
    session: Session, *, task_id: int, relation_kind: str, other_task_id: int, user_id: int
) -> None:
    """``DELETE /tasks/{task}/relations/{relationKind}/{otherTask}`` — removes both rows.

    The kind is **not** validated. An unknown kind simply matches no row and takes the
    404/4009 exit, which is how upstream answers and is the opposite of the create path.

    Permission is checked before the lookup, so the 404 cannot be used to probe which
    relations exist on a task the caller cannot see.
    """
    if not _task_exists(session, task_id):
        raise CaltonError.from_name("models.ErrTaskDoesNotExist")
    if not task_permissions.can_write(session, user_id, task_id):
        raise ForbiddenError()

    relation = session.scalars(
        select(TaskRelation).where(
            TaskRelation.task_id == task_id,
            TaskRelation.other_task_id == other_task_id,
            TaskRelation.relation_kind == relation_kind,
        )
    ).one_or_none()
    if relation is None:
        raise CaltonError.from_name("models.ErrRelationDoesNotExist")

    # The kind is known to be valid by now — it matched a stored row — so the inverse
    # lookup cannot miss. Deleting only the matched row leaves the far end holding a
    # relation that can no longer be reached, let alone removed.
    inverse_kind = INVERSE_RELATION[RelationKind(relation_kind)].value
    for row in session.scalars(
        select(TaskRelation).where(
            or_(
                (TaskRelation.task_id == task_id)
                & (TaskRelation.other_task_id == other_task_id)
                & (TaskRelation.relation_kind == relation_kind),
                (TaskRelation.task_id == other_task_id)
                & (TaskRelation.other_task_id == task_id)
                & (TaskRelation.relation_kind == inverse_kind),
            )
        )
    ):
        session.delete(row)
    session.commit()
