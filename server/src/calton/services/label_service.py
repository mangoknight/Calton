"""Labels, and their attachment to tasks.

The permission model here is **three-way, not two-way**, and that is the thing to get
right. It is tempting to split on "mine versus someone else's"; upstream splits on
"read/use versus edit/delete":

======================  =================================================
action                  who may do it
======================  =================================================
read a label            the creator, **or** anyone who can see a task it
                        is attached to
attach it to a task     the same set — visible is usable, being the
                        creator is *not* required
edit or delete it       the creator only
======================  =================================================

Narrowing "attach" to the creator breaks shared labels in collaborative projects, and it
fails silently: the label appears in the picker and 403s on click. Widening "edit" to
anyone who can see it lets a user rename another user's label. Neither raises, so both
directions are pinned by tests.

The "not found" behaviour is deliberately inconsistent and is copied rather than
harmonised — reads hide existence, writes reveal it. See :func:`get_label_for_read`
against :func:`get_label_for_write`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from calton.core.errors import CaltonError
from calton.core.policy import ForbiddenError
from calton.db.base import utcnow
from calton.models import Label, LabelTask, Task, User
from calton.models.task import base_task_query
from calton.permissions import task as task_permissions
from calton.permissions.project import max_permissions_for_projects
from calton.schemas.label import LabelRead
from calton.schemas.user import UserRead


@dataclass(frozen=True)
class LabelAttachment:
    """What ``PUT /tasks/{task}/labels`` answers with.

    Two keys, not a hydrated label and not the updated task. Returning the whole label
    would be more useful and would break byte-level parity, so the shape is a type rather
    than an ad-hoc dict.
    """

    label_id: int
    created: datetime


def visible_project_ids(session: Session, user_id: int) -> list[int]:
    """Projects the user can read at all."""
    project_ids = list(session.scalars(select(Task.project_id).distinct()))
    if not project_ids:
        return []

    resolved = max_permissions_for_projects(session, user_id, project_ids)
    return [project_id for project_id, permission in resolved.items() if permission >= 0]


def visible_labels_query(session: Session, user_id: int) -> Select[tuple[Label]]:
    """Labels the user may read or attach: their own, plus any on a task they can see.

    Both halves are load-bearing. Only the first misses a collaborator's label attached
    to a shared task; only the second misses a label the user created and has not
    attached to anything yet.
    """
    projects = visible_project_ids(session, user_id)

    attached_to_visible_task = select(LabelTask.label_id).where(
        LabelTask.task_id.in_(
            base_task_query().with_only_columns(Task.id).where(Task.project_id.in_(projects))
        )
    )

    return (
        select(Label)
        .where(or_(Label.created_by_id == user_id, Label.id.in_(attached_to_visible_task)))
        .order_by(Label.id)
    )


def can_read_label(session: Session, user_id: int, label_id: int) -> bool:
    """Whether the label is visible — which is also whether it may be attached."""
    query = visible_labels_query(session, user_id).where(Label.id == label_id)
    return session.scalars(query).first() is not None


def can_modify_label(session: Session, user_id: int, label_id: int) -> bool:
    """Editing and deleting the label itself is the creator's alone."""
    label = session.get(Label, label_id)
    return label is not None and label.created_by_id == user_id


def labels_on_task(session: Session, task_id: int) -> list[Label]:
    """Labels attached to a task, id ascending — the order ``GET`` returns them in.

    Note this is *not* the order the bulk endpoint echoes; that one preserves the
    request's order instead.
    """
    query = (
        select(Label)
        .join(LabelTask, LabelTask.label_id == Label.id)
        .where(LabelTask.task_id == task_id)
        .order_by(Label.id)
    )
    return list(session.scalars(query))


def replace_task_labels(session: Session, task_id: int, label_ids: list[int]) -> None:
    """Set the task's labels to exactly ``label_ids``.

    Full replacement, not a merge: anything currently attached and absent from
    ``label_ids`` is detached. An **empty list clears the set** — returning early on an
    empty list is a natural optimisation that produces the correct response body while
    doing nothing, so the emptiness case takes the same path as any other.
    """
    wanted = set(label_ids)
    attached = {
        row.label_id: row
        for row in session.scalars(select(LabelTask).where(LabelTask.task_id == task_id))
    }

    for label_id, row in attached.items():
        if label_id not in wanted:
            session.delete(row)

    for label_id in label_ids:
        if label_id not in attached:
            session.add(LabelTask(task_id=task_id, label_id=label_id))

    session.flush()


def is_attached(session: Session, task_id: int, label_id: int) -> bool:
    query = select(LabelTask).where(LabelTask.task_id == task_id, LabelTask.label_id == label_id)
    return session.scalars(query).first() is not None


class LabelDoesNotExistError(CaltonError):
    """404 / 8002. Raised by the **write** paths only.

    ``POST`` and ``DELETE`` on a missing label reveal that it does not exist; ``GET``
    refuses with 403 instead and reveals nothing. That asymmetry is upstream's, and it
    falls out of the order in which it authorises versus looks up rather than from a
    decision — see :func:`load_for_read` against :func:`load_for_write`.
    """

    def __init__(self) -> None:
        super().__init__(code=8002, message="This label does not exist.", http_status=404)


def load_for_read(session: Session, user_id: int, label_id: int) -> Label:
    """A label the user may read, or refuse with 403.

    **A missing label and an invisible one are indistinguishable here**, on purpose:
    both are 403, so a caller cannot probe which label ids exist. Raising 404 for the
    missing case would be the tidier API and would leak existence.
    """
    if not can_read_label(session, user_id, label_id):
        raise ForbiddenError

    label = session.get(Label, label_id)
    if label is None:  # pragma: no cover - can_read_label already proved it exists
        raise ForbiddenError
    return label


def load_for_write(session: Session, user_id: int, label_id: int) -> Label:
    """A label the user may edit or delete.

    Missing is 404/8002 here, unlike the read path. Checked before ownership, which is
    what makes a missing label report 404 rather than the 403 a real one owned by
    somebody else reports.
    """
    label = session.get(Label, label_id)
    if label is None:
        raise LabelDoesNotExistError

    if label.created_by_id != user_id:
        raise ForbiddenError
    return label


def user_view(session: Session, user_id: int | None) -> UserRead | None:
    """The embedded ``created_by``, or None when there is no such user."""
    if user_id is None:
        return None
    user = session.get(User, user_id)
    return None if user is None else UserRead.model_validate(user, from_attributes=True)


def label_view(session: Session, label: Label) -> LabelRead:
    """One hydrated label, the shape every ``/labels`` response uses."""
    return LabelRead(
        id=label.id,
        title=label.title,
        description=label.description or "",
        hex_color=label.hex_color or "",
        created_by=user_view(session, label.created_by_id),
        created=label.created,
        updated=label.updated,
    )


# --------------------------------------------------------------------------------------
# Attaching labels to tasks
#
# The three task-label endpoints run their checks in a fixed order, and the order is what
# produces the error codes rather than any per-case decision. Measured against the
# reference server (probe, 2026-08-04) — the corpus pins most of these, the rest are here:
#
#   endpoint                      task missing   task forbidden   label missing   label unseen
#   ----------------------------  -------------  ---------------  --------------  ------------
#   PUT    /tasks/{t}/labels      404 / 4002     403 / 0          404 / 8002      403 / 0
#   DELETE /tasks/{t}/labels/{l}  404 / 4002     403 / 0          — not checked — — n/a —
#   POST   /tasks/{t}/labels/bulk 404 / 4002     403 / 0          404 / 8002      403 / **8003**
#
# Two things in that table are easy to get wrong by unifying them:
#
# * ``PUT`` checks the **label first** and the task second, so a request naming a
#   non-existent label against a task the caller cannot even see answers 8002 and thereby
#   discloses that the label is missing. Reordering to "task first" looks like a security
#   improvement and changes four measured status codes.
# * ``bulk`` reports an unseeable label as **8003**, where ``PUT`` reports the same
#   situation as the generic 403/0. Same user, same label, two codes. Clients branch on
#   ``code``, so collapsing them is a wire change.
# --------------------------------------------------------------------------------------


def _task_not_found() -> CaltonError:
    """404/4002. The message has no full stop, unlike the project one. Measured."""
    return CaltonError.from_name("models.ErrTaskDoesNotExist")


def load_task_for_label_write(session: Session, user_id: int, task_id: int) -> Task:
    """The task all three endpoints resolve before touching a label.

    Existence before permission: a task that does not exist answers 404/4002 rather than
    being reported as forbidden. That is the opposite of the label read path, where a
    missing label hides behind a 403 — the two layers of the same request use opposite
    disclosure rules, and both are measured.
    """
    task = session.scalars(base_task_query().where(Task.id == task_id)).one_or_none()
    if task is None:
        raise _task_not_found()

    if not task_permissions.can_write(session, user_id, task_id):
        raise ForbiddenError
    return task


def load_task_for_read(session: Session, user_id: int, task_id: int) -> Task:
    """The task ``GET /tasks/{task}/labels`` lists labels for.

    Refuses with the **task's own** 403/4005, not the CRUD pipeline's 403/0 that the write
    paths use. Same task, same user, same lack of permission, two different bodies — the
    read path goes through the resource's typed error and the write paths go through the
    generic handler. Clients branch on ``code``, so this is not interchangeable.
    """
    task = session.scalars(base_task_query().where(Task.id == task_id)).one_or_none()
    if task is None:
        raise _task_not_found()

    allowed, _ = task_permissions.can_read(session, user_id, task_id)
    if not allowed:
        raise CaltonError.from_name("models.ErrNoPermissionToSeeTask")
    return task


def load_label_for_attach(session: Session, user_id: int, label_id: int) -> Label:
    """The label being attached: it must exist, and the caller must be able to see it.

    Visible is usable. Being the creator is **not** required — narrowing this to the
    creator makes shared labels unattachable in collaborative projects, and it fails
    silently rather than raising, so only ``tasklabel.add.readable_others_label_ok``
    catches it.

    ``label_id == 0`` — which is what an empty request body produces — answers 403 rather
    than the 404 every other missing id gets. Upstream's cause is ``xorm``: the lookup
    builds its WHERE clause from the struct's non-zero fields, so a zero id matches
    **nothing in particular** and it returns the first row of the table, which the caller
    then usually cannot see. We deliberately reproduce only the *status*, not the
    mechanism: a request that says "attach label 0" must never attach whichever label
    happens to sort first. That is upstream operating out of control (silent wrong write),
    not upstream making a decision, and practice #23 says copy the latter and not the
    former.
    """
    if label_id == 0:
        raise ForbiddenError

    label = session.get(Label, label_id)
    if label is None:
        raise LabelDoesNotExistError

    if not can_read_label(session, user_id, label_id):
        raise ForbiddenError
    return label


def attach_label(session: Session, user_id: int, *, task_id: int, label_id: int) -> LabelAttachment:
    """``PUT /tasks/{task}/labels``. Label checked first, then the task. See the table."""
    load_label_for_attach(session, user_id, label_id)
    load_task_for_label_write(session, user_id, task_id)

    if is_attached(session, task_id, label_id):
        # 400/8001, with its own code and wording — not a silently idempotent 201.
        raise CaltonError.from_name("models.ErrLabelIsAlreadyOnTask")

    row = LabelTask(task_id=task_id, label_id=label_id, created=utcnow())
    session.add(row)
    session.flush()
    return LabelAttachment(label_id=label_id, created=row.created)


def detach_label(session: Session, user_id: int, *, task_id: int, label_id: int) -> None:
    """``DELETE /tasks/{task}/labels/{label}``.

    **Whether the label exists is never asked.** The only questions are "may you write to
    this task" and "is this attachment there right now", and a missing attachment answers
    **403**, not 404 and not an idempotent 200. Upstream reaches that by putting the
    existence test inside the permission callback, so "there is no such attachment" leaves
    the building as "you are not allowed". All three of the reasonable alternatives go red
    against the corpus, and nobody writing this endpoint would think to delete twice —
    ``tasklabel.remove.twice_is_403`` exists to make that unmissable.
    """
    load_task_for_label_write(session, user_id, task_id)

    row = session.scalars(
        select(LabelTask).where(LabelTask.task_id == task_id, LabelTask.label_id == label_id)
    ).first()
    if row is None:
        raise ForbiddenError

    session.delete(row)
    session.flush()


def replace_labels(session: Session, user_id: int, *, task_id: int, label_ids: list[int]) -> None:
    """``POST /tasks/{task}/labels/bulk`` — the whole set, replaced.

    Only the task is permission-checked up front; each *newly added* label is then checked
    individually. A label already on the task is skipped entirely and therefore **not**
    re-checked, which matters: a task can carry a label the caller cannot see, and
    resubmitting it must not 403.

    Every label is validated before anything is written. Upstream deletes first and
    validates while inserting, relying on the transaction to roll back — same visible
    outcome, but validating first means a partial write cannot survive a bug in the
    rollback path.
    """
    load_task_for_label_write(session, user_id, task_id)

    already_attached = {row.label_id for row in _attachments(session, task_id)}
    for label_id in label_ids:
        if label_id in already_attached:
            continue
        if session.get(Label, label_id) is None:
            raise LabelDoesNotExistError
        if not can_read_label(session, user_id, label_id):
            # 403/8003 here, where PUT answers 403/0 for the identical situation.
            raise CaltonError.from_name("models.ErrUserHasNoAccessToLabel")

    replace_task_labels(session, task_id, label_ids)


def _attachments(session: Session, task_id: int) -> list[LabelTask]:
    return list(session.scalars(select(LabelTask).where(LabelTask.task_id == task_id)))
