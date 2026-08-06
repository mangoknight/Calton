"""Task assignees: list, assign, unassign, and the bulk replace.

Every rule below was measured against the running Go reference server, not read off
``pkg/models/task_assignees.go``. The assignee endpoints look like a copy of the label
endpoints — same four shapes, same nesting under a task — and are **not**. Four
behaviours are outright opposite, and none of them fails loudly when implemented the
other way (recorded in ``harness/corpus/_assignees.yaml``, which measured the same):

===================  ==========================  ============================
behaviour            labels                      assignees
===================  ==========================  ============================
assign twice         400 / 8001                  400 / **4021**
remove a non-link    **403**                     **200, idempotent**
id missing (0)       **403**                     **404 / 1005**
``created`` echoed   real timestamp              **zero value**
task not found       4002 "task does not exist"  **3001 "project does not exist"**
===================  ==========================  ============================

That last one is the trap: an assignee endpoint given a task id that does not exist
answers *"This project does not exist."* Upstream resolves task → project to check
permissions and fails at the project step, so the error names the project even though
the caller never mentioned one. Writing the obvious 4002 looks more correct and is
wrong, and the frontend branches on 3001 here to decide what to re-fetch.

Gate order is measured too, and matters because two gates can both be failable at once
(``probe_assignees2``):

1. task → project resolution — a missing task is 404/3001 *even when the assignee id is
   also bogus*;
2. the **caller's** write permission — a forbidden task is 403/0 *even when the assignee
   id is bogus*;
3. the **assignee's** existence (404/1005), then their project access (403/7003);
4. finally the duplicate check (400/4021).

Steps 3 and 4 are about a different person from step 2, which is the single easiest
thing to miss here: authentication middleware only ever looks at the caller, so an
implementation that checks "may I write this task?" and stops will happily assign
someone who cannot open the project. The task then shows an avatar for a user who gets a
403 on click, with no error anywhere.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from calton.core.errors import CaltonError
from calton.core.policy import ForbiddenError
from calton.db.types import ZERO_TIME
from calton.models import base_task_query
from calton.models.task import Task
from calton.models.task_assignee import TaskAssignee
from calton.models.user import User
from calton.permissions.project import can_read as project_can_read
from calton.permissions.project import can_write as project_can_write
from calton.schemas.assignee import AssigneeCreated, BulkAssignees
from calton.schemas.user import UserEcho, UserRead


def _project_of(session: Session, task_id: int) -> int:
    """The task's project, or the 404 upstream gives — which names the *project*.

    Goes through ``base_task_query`` so a soft-deleted task reads as absent here, the
    same as it does for every other task permission check.
    """
    task = session.scalars(base_task_query().where(Task.id == task_id)).one_or_none()
    if task is None:
        raise CaltonError.from_name("models.ErrProjectDoesNotExist")
    return int(task.project_id)


def _rows(session: Session, task_id: int) -> list[TaskAssignee]:
    """Assignment rows in insertion order.

    ⚠️ Ordered by the row id, which is the insertion order, **not** by ``user_id``.
    Measured: assigning alice (900) to a task already holding bob (901) lists
    ``[901, 900]``. Adding ``ORDER BY user_id`` is the natural way to make a listing
    "stable" and would produce ``[900, 901]`` — same set, same status, wrong order, and
    only an ordered assertion notices.
    """
    return list(
        session.scalars(
            select(TaskAssignee).where(TaskAssignee.task_id == task_id).order_by(TaskAssignee.id)
        )
    )


def list_assignees(session: Session, *, task_id: int, user_id: int) -> list[UserRead]:
    """The assigned users, as full user objects.

    Denial here is 403 **code 1** ("You're not allowed to do this."), which is a third
    distinct forbidden shape within the task-association family — the label list gives
    403/4005 and the label bulk gives 403/0. Unifying them is the obvious cleanup and
    breaks clients that branch on the code.
    """
    project_id = _project_of(session, task_id)
    allowed, _ = project_can_read(session, user_id, project_id)
    if not allowed:
        raise CaltonError.from_name("models.ErrGenericForbidden")

    assigned = _rows(session, task_id)
    if not assigned:
        return []

    users = {
        user.id: user
        for user in session.scalars(
            select(User).where(User.id.in_([row.user_id for row in assigned]))
        )
    }
    return [
        UserRead.model_validate(users[row.user_id], from_attributes=True)
        for row in assigned
        if row.user_id in users
    ]


def _require_caller_may_write(session: Session, *, task_id: int, user_id: int) -> int:
    """Gates 1 and 2, in that order. Returns the project id for later checks."""
    project_id = _project_of(session, task_id)
    if not project_can_write(session, user_id, project_id):
        # code 0 / "Forbidden", not the code 1 the read path uses. Same task, same
        # overreach, two different bodies — measured on both paths.
        raise ForbiddenError()
    return project_id


def _require_assignable(session: Session, *, project_id: int, assignee_id: int) -> User:
    """Gate 3: the *assignee* must exist and must be able to reach the project.

    ``assignee_id`` of 0 — which is what a request body with no ``user_id`` deserialises
    to — takes the same 404/1005 exit as an id that simply is not there. The label
    endpoints split those two into 403 and 404 respectively; these do not.
    """
    user = session.scalars(select(User).where(User.id == assignee_id)).one_or_none()
    if user is None:
        raise CaltonError.from_name("user.ErrUserDoesNotExist")

    can_read, _ = project_can_read(session, int(user.id), project_id)
    if not can_read:
        raise CaltonError.from_name("models.ErrUserDoesNotHaveAccessToProject")
    return user


def assign(
    session: Session,
    *,
    task_id: int,
    assignee_id: int,
    user_id: int,
    created: datetime | None = None,
) -> AssigneeCreated:
    """``PUT`` one assignee.

    The echoed ``created`` is the **zero time**, not the row's real timestamp: upstream
    responds with the struct it bound the request into and never fills the field in. The
    equivalent label endpoint does return a real timestamp, so this cannot be factored
    into one shared helper without breaking one of them.
    """
    project_id = _require_caller_may_write(session, task_id=task_id, user_id=user_id)
    _require_assignable(session, project_id=project_id, assignee_id=assignee_id)

    existing = session.scalars(
        select(TaskAssignee).where(
            TaskAssignee.task_id == task_id, TaskAssignee.user_id == assignee_id
        )
    ).one_or_none()
    if existing is not None:
        raise CaltonError.from_name("models.ErrUserAlreadyAssigned")

    session.add(TaskAssignee(task_id=task_id, user_id=assignee_id))
    session.commit()
    return AssigneeCreated(
        user_id=assignee_id, created=created if created is not None else ZERO_TIME
    )


def unassign(session: Session, *, task_id: int, assignee_id: int, user_id: int) -> None:
    """``DELETE`` one assignee — idempotent, deliberately.

    Removing a user who was never assigned, or one who does not exist at all, is a plain
    200. Only the caller's own permission is enforced; there is no existence check on the
    target, which is the exact opposite of ``assign`` two functions up. Adding the
    symmetric validation is a tempting tidy-up and turns two measured 200s into 404s.
    """
    _require_caller_may_write(session, task_id=task_id, user_id=user_id)

    row = session.scalars(
        select(TaskAssignee).where(
            TaskAssignee.task_id == task_id, TaskAssignee.user_id == assignee_id
        )
    ).one_or_none()
    if row is not None:
        session.delete(row)
        session.commit()


def replace_assignees_for_task_write(
    session: Session, *, task_id: int, project_id: int, assignee_ids: list[int]
) -> bool:
    """``updateTaskAssignees`` (task_assignees.go:64) — **the** set-replacement rule.

    Returns whether the task **had assignees before this call**. That fact decides the
    ``assignees`` value in a task write response (``null`` versus ``[]`` — see
    ``task_service._write_view``) and it is unrecoverable afterwards, since by then the
    rows are gone. Returned rather than re-queried by the caller for that reason.

    Used by every endpoint that replaces the whole set: ``POST .../assignees/bulk``,
    ``POST /tasks/bulk``, and the ``assignees`` field of a task create or update. All four
    were measured in one session and they agree; there is no per-endpoint variation.

    ⚠️ **The duplicate rule is conditional, and both obvious readings of it are wrong.**
    Whether a repeated id is an error depends on whether that id was *already assigned
    before the request*:

    ===================================  =========  ==================================
    request (id already assigned?)       answer     why
    ===================================  =========  ==================================
    ``[901, 901]``  901 assigned         **200**    both hit the "keep it" branch, which
                                                    returns before the duplicate check
    ``[902, 902]``  902 not assigned     **400**    the first inserts, the second then
                                                    finds 902 assigned → 4021
    ``[901, 902, 901]``  901 assigned    **200**    set becomes {901, 902}
    ``[901, 901, 902, 902]`` 901 only    **400**    the 902 pair collides
    ===================================  =========  ==================================

    This corrects two earlier readings, each of which was right about one column and
    generalised to both:

    * *"duplicates collapse"* — taken from a sample where the repeated user happened to be
      pre-assigned. Implemented as ``dict.fromkeys(...)``, it answers **200** where
      upstream answers 400/4021, and no test covered the case.
    * *"any duplicate is 4021"* — taken from a sample where the repeated user was new.
      It answers **400** where upstream answers 200/201.

    Both samples were same-solution: only a request mixing the two kinds tells them apart.

    Does not commit — the caller owns the transaction, which is what lets a failed batch
    leave the previous set untouched. Measured: ``[901, 99999]`` answers 404/1005 and 901
    is **not** assigned afterwards, on the task paths as well as the bulk endpoint.
    """
    current = {row.user_id: row for row in _rows(session, task_id)}

    # Validate the whole list before writing anything: a rejected batch must leave the
    # previous set exactly as it was. Measured on all four endpoints — [901, 99999] is
    # 404/1005 and 901 is *not* assigned afterwards.
    wanted: dict[int, None] = {}
    for assignee_id in assignee_ids:
        if assignee_id in current:
            # Already assigned before this request: upstream takes the "keep it" branch
            # and never reaches the duplicate check, so a repeat here is silently fine.
            wanted.setdefault(assignee_id, None)
            continue
        _require_assignable(session, project_id=project_id, assignee_id=assignee_id)
        if assignee_id in wanted:
            raise CaltonError.from_name("models.ErrUserAlreadyAssigned")
        wanted[assignee_id] = None

    for existing_id, row in current.items():
        if existing_id not in wanted:
            session.delete(row)
    for assignee_id in wanted:
        if assignee_id not in current:
            session.add(TaskAssignee(task_id=task_id, user_id=assignee_id))

    return bool(current)


def bulk_assign(
    session: Session, *, assignees: Sequence[UserEcho] | None, task_id: int, user_id: int
) -> BulkAssignees:
    """``POST .../bulk`` — replace the whole set.

    Measured properties, each of which rules out an obvious implementation:

    * **Replace, not append.** A task holding {901} given ``[902]`` ends up {902}.
    * **Kept rows are kept.** Given ``[902, 901]`` while 901 is already assigned, the
      result lists ``[901, 902]`` — 901 keeps its original row and 902 is appended, so
      the outcome does not follow the request order. Delete-everything-then-reinsert
      would answer ``[902, 901]``: same set, different order.
    * **All-or-nothing.** A batch naming a user who does not exist (404/1005), one
      without project access (403/7003), or a *newly added* user twice (400/4021) leaves
      the previous set exactly as it was. Validation therefore runs over the whole list
      before anything is written.
    * **A repeated id is only an error when that user was not already assigned** —
      ``[901, 901]`` with 901 already on the task is measured **201**, not 4021. The rule
      lives in ``replace_assignees_for_task_write``; this endpoint no longer has its own.
    * **A missing ``assignees`` key is not an empty list.** ``{}`` clears the set and
      echoes ``{"assignees": null}``; ``{"assignees": []}`` clears it and echoes
      ``{"assignees": []}``. Coercing null to ``[]`` — the natural defensive move —
      changes a response clients can see.
    """
    project_id = _require_caller_may_write(session, task_id=task_id, user_id=user_id)

    requested = list(assignees or ())

    # One implementation of the replacement rule, shared with the task write paths — see
    # `replace_assignees_for_task_write`. This endpoint used to carry its own copy, and
    # the copy had the duplicate rule wrong in the opposite direction from the other one.
    replace_assignees_for_task_write(
        session,
        task_id=task_id,
        project_id=project_id,
        assignee_ids=[entry.id for entry in requested],
    )
    session.commit()

    if assignees is None:
        return BulkAssignees(assignees=None)
    # The echo is the request objects themselves, not the stored rows — upstream parses
    # into the user struct and serialises that same struct straight back. A later GET on
    # the same task returns the hydrated users; filling them in here would be more useful
    # and is what the corpus exists to prevent.
    #
    # ⚠️ Rebuilding each entry as `id` plus zeros is the same answer for the id-only body
    # the corpus sends, and a different one for a client that posts whole user objects.
    # Measured: `{"id": 901, "username": "FORGED", "created": "1999-03-04…"}` comes back
    # with all of it intact. Duplicates survive the echo too, while storage holds one row.
    return BulkAssignees(assignees=list(requested))
