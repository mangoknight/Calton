"""Kanban buckets and the task-into-bucket move (T28).

Every rule below was measured against a running Go reference server. The Go source was
read only to form hypotheses; where the two are quoted together it is because they agreed.

Three behaviours in here change data the caller did not mention, and none of them fails
loudly when left out:

1. **Moving a task into the view's done bucket completes it** — ``done`` becomes true and
   ``done_at`` is stamped. An implementation that writes only the ``task_buckets`` row
   moves the card correctly and leaves the task open, so "drag to Done" stops meaning
   done and every list, filter and completion statistic quietly disagrees with the board.
2. **Moving it back out re-opens it** — ``done`` false *and* ``done_at`` back to the zero
   time. The reverse direction is the one that gets forgotten, because the forward
   direction is what the feature request says; without it a task can never be un-completed
   from the board. Zeroing ``done_at`` matters on its own: keeping the old stamp leaves a
   "completed at" timestamp on a task that is not completed.
3. **Deleting a bucket rehomes its tasks into the view's default bucket** rather than
   deleting them or leaving them pointing at a bucket that no longer exists. Skipping it
   makes the tasks vanish from the board while remaining in the database and in every
   list view — the hardest kind of report to act on, because nothing was lost.

And one that inverts the first: a **repeating** task dragged into the done bucket is *not*
completed. See :func:`_apply_done_transition`.

⚠️ The scoping rules of the two write paths are **deliberately inconsistent** and must
stay that way:

======================================  ==========================================
``POST .../views/{v}/buckets/{b}``      does **not** check that b belongs to v →
(update a bucket)                       200, and the response echoes the *path's*
                                        view id while the row keeps its own
``POST .../views/{v}/buckets/{b}/tasks``**does** check → 400 / 10002
(move a task)
======================================  ==========================================

Both measured. Factoring the check into one helper used by both paths is the obvious
tidy-up when the two handlers sit next to each other — it is also the one change that
silently turns the first row into a 400. ``bucket.update.does_not_check_bucket_belongs_
to_view`` and ``buckettask.move.bucket_not_in_view_400_10002`` exist as a pair and have
to be read together.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from calton.core.errors import CaltonError
from calton.core.policy import ForbiddenError
from calton.db.types import ZERO_TIME
from calton.models import base_task_query
from calton.models.bucket import Bucket
from calton.models.project_view import ProjectView
from calton.models.task import Task
from calton.models.task_position import TaskBucket
from calton.permissions.project import can_read as project_can_read
from calton.permissions.project import can_write as project_can_write
from calton.schemas.bucket import BucketWrite
from calton.schemas.bucket_summary import BucketSummary
from calton.schemas.task_bucket import TaskBucketRead
from calton.services import task_service

# The repeat/reschedule rules come from the task line rather than being reimplemented:
# upstream's `updateDone` + `setTaskDates*Repeat`, which the bucket-move path needs
# verbatim. A second copy would be a second thing to keep in step with `repeat_mode`, and
# the two would drift silently, since only one of the two call sites has a corpus case
# for each mode. They are part of `task_service`'s public surface for exactly this reason.

#: ``calculateDefaultPosition`` (tasks.go:867): a new bucket's position is its own id
#: times 2^16, leaving room to drag things between neighbours without renumbering.
#: Measured — bucket 9951 came back with position 652148736 = 9951 * 65536 — which is
#: why the corpus asserts only that the field is a number: the value follows the id.
POSITION_SCALE = 2**16


def _view(session: Session, *, project_id: int, view_id: int) -> ProjectView:
    """The view, matched on **both** ids, or 404 / 3014.

    ``GetProjectViewByIDAndProject`` — the lookup is ``WHERE id = ? AND project_id = ?``,
    so a view that exists but belongs to a different project is simply not found.

    ⚠️ **The error names the view even when the project is what is missing.** Measured::

        GET /projects/9999/views/953/buckets
        ->  404 {"code": 3014, "message": "This project view does not exist."}

    Project 9999 does not exist and view 953 does; the answer still talks about the view.
    There is no separate project lookup to produce 3001, and adding one — reporting "this
    project does not exist", which is *more accurate* — is a wire change that reads as a
    correction and would pass review unchallenged.

    ⚠️ Do not simplify this to a lookup on ``view_id`` alone. That is a subtly different
    thing: it answers 200 for ``/projects/9999/views/953``, because the view is found and
    nothing then contradicts the bogus project. Both readings produce 3014 for a view id
    that does not exist, so the ordinary not-found case cannot tell them apart — only the
    mismatched-pair case can, which is what ``bucket.read_all.missing_project_reports_
    view_error`` is for. (This implementation had that bug; the case caught it.)
    """
    view = session.scalars(
        select(ProjectView).where(ProjectView.id == view_id, ProjectView.project_id == project_id)
    ).one_or_none()
    if view is None:
        raise CaltonError.from_name("models.ErrProjectViewDoesNotExist")
    return view


def _bucket(session: Session, *, bucket_id: int) -> Bucket:
    """The bucket, or 404 / 10001. Buckets have their own 100xx error range."""
    bucket = session.scalars(select(Bucket).where(Bucket.id == bucket_id)).one_or_none()
    if bucket is None:
        raise CaltonError.from_name("models.ErrBucketDoesNotExist")
    return bucket


def _summary(
    session: Session, bucket: Bucket, *, hydrate_creator: bool, count: int = 0
) -> BucketSummary:
    """A bucket as the wire sees it.

    Two fields vary by *call site* rather than by data, and both were measured on each of
    the three sites separately:

    ``count`` — 0 on the bucket list (that endpoint never loads tasks; see
    :func:`list_buckets`), but a **real number** on the bucket embedded in a move
    response, where upstream counts the target bucket and then increments it for the task
    it just placed. Measured: moving task 950 into bucket 952, which held one task, echoes
    ``count: 2``.

    ``created_by`` — a full user object on the list and on a create, ``null`` on an update
    and on the embedded bucket. Same struct, same column, different answers.
    """
    return BucketSummary(
        id=bucket.id,
        title=bucket.title,
        project_view_id=bucket.project_view_id,
        limit=bucket.limit or 0,
        count=count,
        position=bucket.position or 0,
        created=bucket.created,
        updated=bucket.updated,
        created_by=(
            task_service.user_view(session, bucket.created_by_id) if hydrate_creator else None
        ),
    )


def _buckets_of(session: Session, view_id: int) -> list[Bucket]:
    return list(
        session.scalars(
            select(Bucket).where(Bucket.project_view_id == view_id).order_by(Bucket.position)
        )
    )


def list_buckets(
    session: Session, *, project_id: int, view_id: int, user_id: int
) -> list[BucketSummary]:
    """``GET /projects/{p}/views/{v}/buckets``.

    Two absences are contractual and both are easy to "fix" into a wire difference:

    * **no ``tasks`` key at all** — not ``[]``, not ``null``. This endpoint does not load
      tasks, so the ``omitempty`` field never appears. :class:`BucketSummary` has no
      ``tasks`` field, so the absence falls out of the type rather than being stripped.
    * **``count`` is 0** even though the seed's three buckets hold 4, 2 and 1 tasks. A
      field literally named ``count`` that is permanently zero looks like an unfinished
      feature, and computing it is a one-line change that breaks parity. It is not
      unimplemented: the *same* buckets report 4/2/1 from
      ``GET .../views/{v}/tasks``. The number depends on which endpoint serialises the
      bucket, and ``bucket.read_all.ok_without_tasks`` /
      ``bucket.read_all.count_is_populated_only_via_tasks_endpoint`` pin both halves.

    A **non-kanban** view answers ``200 []`` rather than an error. Rejecting a bucket read
    on a list view is a reasonable-looking guard and it breaks the frontend, which polls
    this endpoint on every view switch.
    """
    view = _view(session, project_id=project_id, view_id=view_id)
    allowed, _ = project_can_read(session, user_id, view.project_id)
    if not allowed:
        # code 1 / "You're not allowed to do this." — the *read* shape. The write paths
        # on the same resource answer code 0 / "Forbidden". Measured on both.
        raise CaltonError.from_name("models.ErrGenericForbidden")

    return [
        _summary(session, bucket, hydrate_creator=True) for bucket in _buckets_of(session, view.id)
    ]


def _require_write(session: Session, view: ProjectView, user_id: int) -> None:
    if not project_can_write(session, user_id, view.project_id):
        raise ForbiddenError()


def create_bucket(
    session: Session, *, project_id: int, view_id: int, data: BucketWrite, user_id: int
) -> BucketSummary:
    """``PUT /projects/{p}/views/{v}/buckets`` → **201**.

    Position is assigned in two steps upstream, and it has to be, because it is derived
    from the id the insert allocates: insert, then ``position = id * 2^16`` and update.
    """
    view = _view(session, project_id=project_id, view_id=view_id)
    _require_write(session, view, user_id)

    bucket = Bucket(
        title=data.title,
        project_view_id=view.id,
        limit=data.limit,
        position=data.position,
        created_by_id=user_id,
    )
    session.add(bucket)
    session.flush()

    if not bucket.position:
        bucket.position = float(bucket.id) * POSITION_SCALE
    session.flush()
    # Committed here, not by the caller. Only CRUDRouter commits at the request
    # boundary; a hand-written router gets a session that is closed without one, so a
    # service that merely flushes has its whole write rolled back — the response still
    # renders the new values from the in-memory objects, so it looks completely
    # correct and only a read-back notices. Matches assignee_service/task_service.
    session.commit()

    # Hydrated here, unlike on the update path — measured: a create resolves the doer and
    # reports the real `created`, whichever of them the body carried, because the response
    # is assembled after the insert rather than merged over it. `count` is still an echo on
    # both paths: a create sending `count: 99` answers 99.
    return _summary(session, bucket, hydrate_creator=True, count=data.count)


def update_bucket(
    session: Session,
    *,
    project_id: int,
    view_id: int,
    bucket_id: int,
    data: BucketWrite,
    user_id: int,
) -> BucketSummary:
    """``POST /projects/{p}/views/{v}/buckets/{b}`` — a **full replacement**.

    ``Cols("title", "limit", "position")`` unconditionally (kanban.go:348), so a body
    carrying only ``title`` zeroes the other two in the database. See
    :class:`~calton.schemas.bucket.BucketWrite` for the measurement.

    **No ownership check between the bucket and the view in the path** — see the module
    docstring. The response then echoes the *path's* view id while the stored row keeps
    its own, so the two disagree by design::

        POST /projects/950/views/950/buckets/950  {"title": "HIJACKED"}
        -> 200 {"id": 950, "project_view_id": 950, …}
        row:    (950, 'HIJACKED', project_view_id=953)   # unchanged

    Returning the row's real ``project_view_id`` is more truthful and is a wire change.
    """
    view = _view(session, project_id=project_id, view_id=view_id)
    _require_write(session, view, user_id)
    bucket = _bucket(session, bucket_id=bucket_id)

    bucket.title = data.title
    bucket.limit = data.limit
    bucket.position = data.position
    session.flush()
    # Committed here, not by the caller. Only CRUDRouter commits at the request
    # boundary; a hand-written router gets a session that is closed without one, so a
    # service that merely flushes has its whole write rolled back — the response still
    # renders the new values from the in-memory objects, so it looks completely
    # correct and only a read-back notices. Matches assignee_service/task_service.
    session.commit()

    summary = _summary(session, bucket, hydrate_creator=False)
    # Echo the path's view, not the row's. The row is deliberately left alone: the bucket
    # is not moved between views by this endpoint, only its own columns are replaced.
    return summary.model_copy(
        update={
            "project_view_id": view.id,
            # `created`, `created_by` and `count` are echoed from the request, not read
            # off the row. All three survive in the row untouched — this is a gap in what
            # upstream *echoes*, which is exactly the distinction `limit` and `position`
            # are on the other side of.
            #
            # ⚠️ These used to be hardcoded to the zero time / null / 0, which is the same
            # answer for a body that omits them and a different one for the shape that
            # actually matters. Measured: reading the bucket and posting that body back
            # answers with the real `created` and the full `created_by`, because upstream
            # is handing back what it was given. A read-modify-write client hits that path
            # on every update and our own frontend never does.
            "created": data.created,
            "created_by": data.created_by,
            "count": data.count,
        }
    )


def _default_bucket_id(session: Session, view: ProjectView) -> int:
    """``getDefaultBucketID``: the view's configured default, else its first bucket."""
    if view.default_bucket_id:
        return int(view.default_bucket_id)
    first = session.scalars(
        select(Bucket).where(Bucket.project_view_id == view.id).order_by(Bucket.position).limit(1)
    ).one_or_none()
    return int(first.id) if first is not None else 0


def delete_bucket(
    session: Session, *, project_id: int, view_id: int, bucket_id: int, user_id: int
) -> None:
    """``DELETE /projects/{p}/views/{v}/buckets/{b}``.

    Order matters: **the last-bucket rule is checked before the bucket is looked up**, so
    it is reachable at all. Both exits were measured (412/10003 on the last one, 404/10001
    on a missing one).

    The done bucket is **not** protected — deleting it is a plain 200 even though the
    view's ``done_bucket_id`` then dangles. Guarding it is defensible and is a 4xx where
    upstream gives a 200; the view's pointer is cleared instead, which is what upstream
    does.
    """
    view = _view(session, project_id=project_id, view_id=view_id)
    _require_write(session, view, user_id)
    bucket = _bucket(session, bucket_id=bucket_id)

    total = session.scalar(
        select(func.count()).select_from(Bucket).where(Bucket.project_view_id == view.id)
    )
    if (total or 0) <= 1:
        # 412 with **no invalid_fields** — this is a business rule, not body validation.
        # The other two 412s in this group (empty title here, empty comment) come out of
        # the shared validation exit and do carry the array. Binding "412" to "has
        # invalid_fields" adds a key here that upstream does not send.
        raise CaltonError.from_name("models.ErrCannotRemoveLastBucket")

    # A view pointing at a bucket that is about to disappear has its pointer cleared,
    # before the default is resolved — so deleting the default bucket rehomes into the
    # *next* one rather than into itself.
    if view.default_bucket_id == bucket.id:
        view.default_bucket_id = 0
    if view.done_bucket_id == bucket.id:
        view.done_bucket_id = 0
    session.flush()

    target = _default_bucket_id(session, view)

    # Rehome, do not delete. The tasks keep their place in the view and are appended to
    # the end of the default bucket's column — the corpus asserts the resulting order,
    # because "appended" rather than "merged by id" is itself the measured behaviour.
    for row in session.scalars(select(TaskBucket).where(TaskBucket.bucket_id == bucket.id)):
        row.bucket_id = target

    session.delete(bucket)
    session.flush()
    # Committed here, not by the caller. Only CRUDRouter commits at the request
    # boundary; a hand-written router gets a session that is closed without one, so a
    # service that merely flushes has its whole write rolled back — the response still
    # renders the new values from the in-memory objects, so it looks completely
    # correct and only a read-back notices. Matches assignee_service/task_service.
    session.commit()


def _task(session: Session, task_id: int) -> Task:
    """The task, or 404 / **4002** — the task domain's code, not a bucket one.

    A body with no ``task_id`` deserialises to 0 and takes this same exit, so "missing id"
    and "unknown id" are one answer here. They are not elsewhere: the same omission is
    403 on labels, 404/1005 on assignees and 400/4007 on relations. Four endpoints in one
    family, four answers, no rule connecting them — each was measured separately.
    """
    task = session.scalars(base_task_query().where(Task.id == task_id)).one_or_none()
    if task is None:
        raise CaltonError.from_name("models.ErrTaskDoesNotExist")
    return task


def _check_limit(session: Session, bucket: Bucket) -> None:
    """412 / 10004 when the target bucket is full.

    Only ever called when the task is actually changing buckets: reordering inside a full
    bucket stays legal upstream, and checking unconditionally would forbid it.

    Note the status: 412, not the 400 or 409 a "conflict" suggests. Upstream files every
    unmet business precondition under 412 and keeps 409 for duplicate relations.
    """
    if not bucket.limit:
        return
    held = session.scalar(
        select(func.count()).select_from(TaskBucket).where(TaskBucket.bucket_id == bucket.id)
    )
    if (held or 0) >= bucket.limit:
        raise CaltonError.from_name("models.ErrBucketLimitExceeded")


def _apply_done_transition(
    session: Session,
    *,
    task: Task,
    view: ProjectView,
    target_bucket_id: int,
    previous_bucket_id: int,
) -> int:
    """The done-state side effects. Returns the bucket the task should actually land in.

    Three outcomes, and the third is the one that inverts the feature's own description:

    **Ordinary task into the done bucket** → ``done=True``, ``done_at=now``.

    **Out of the done bucket** → ``done=False``, ``done_at=`` the zero time.

    **Repeating task into the done bucket** → *not completed*. ``done`` stays False,
    ``done_at`` stays zero, and the **due date rolls forward** to the next occurrence;
    the task is then routed back to the view's default bucket. The semantics are "finish
    this occurrence and schedule the next", not "mark done".

    That third case is why "into the done bucket ⇒ done = true" is not a safe
    simplification. Applied to a repeating task it marks the series complete, so a task
    that should have reappeared tomorrow never comes back — no error, no data loss, just a
    recurring task that silently stops. Measured on task 922 (``repeat_after`` 86400)::

        POST .../views/923/buckets/922/tasks  {"task_id": 922}
        -> done=false, done_at=0001-01-01T00:00:00Z, due_date rolled forward
           task_buckets: (920, 922)   # the default bucket, not the done bucket

    The rolled-forward date is **the next occurrence strictly after now**, not "tomorrow":
    ``_add_repeat_interval`` advances by whole intervals until it passes the current
    instant. With a 24-hour interval and a due time of 12:00Z that lands on *today* when
    the request happens before 12:00Z and on *tomorrow* after it. Anything asserting a
    fixed day here is only correct for part of the day.
    """
    done_bucket = int(view.done_bucket_id or 0)
    if not done_bucket:
        return target_bucket_id

    now = datetime.now(UTC)

    if done_bucket == target_bucket_id and not task.done:
        if task_service.is_repeating(task.repeat_after or 0, task.repeat_mode):
            dates = task_service.RepeatDates(
                due=task.due_date or ZERO_TIME,
                start=task.start_date or ZERO_TIME,
                end=task.end_date or ZERO_TIME,
            )
            rolled = task_service.reschedule(task, dates, now)
            task.due_date, task.start_date, task.end_date = rolled.due, rolled.start, rolled.end
            # Stays open, and done_at is cleared rather than stamped: upstream sets
            # done=True, runs the repeat helper which sets it back to False, and only
            # then branches on the *resulting* value to choose the timestamp.
            task.done = False
            task.done_at = ZERO_TIME
            session.flush()
            # Routed back out of the done column so the next occurrence is visible as
            # outstanding work. Falls back to staying put when the view has no default.
            return int(view.default_bucket_id or 0) or previous_bucket_id

        task.done = True
        task.done_at = now
        session.flush()
        return target_bucket_id

    if previous_bucket_id == done_bucket and task.done and target_bucket_id != done_bucket:
        task.done = False
        task.done_at = ZERO_TIME
        session.flush()

    return target_bucket_id


def move_task(
    session: Session,
    *,
    project_id: int,
    view_id: int,
    bucket_id: int,
    task_id: int,
    user_id: int,
) -> TaskBucketRead:
    """``POST /projects/{p}/views/{v}/buckets/{b}/tasks`` → **200**, not 201.

    It creates a placement row, so 201 is the defensible reading; upstream answers 200.

    Gate order is measured, and the bucket/view ownership check comes **before** the task
    is resolved — a bucket from the wrong view is 400/10002 even when the task id is also
    bogus.
    """
    view = _view(session, project_id=project_id, view_id=view_id)
    _require_write(session, view, user_id)
    bucket = _bucket(session, bucket_id=bucket_id)

    # Unlike the update path above, this one *does* enforce it. See the module docstring
    # before making the two agree.
    if bucket.project_view_id != view.id:
        raise CaltonError.from_name("models.ErrBucketDoesNotBelongToProjectView")

    task = _task(session, task_id)

    placement = session.scalars(
        select(TaskBucket).where(
            TaskBucket.task_id == task.id, TaskBucket.project_view_id == view.id
        )
    ).one_or_none()
    previous_bucket_id = int(placement.bucket_id) if placement is not None else 0

    if bucket.id == previous_bucket_id:
        # Already there: upstream returns before it resolves anything, so the two embedded
        # objects are never assembled and both come back **null** — while the three scalar
        # keys are still present. Measured::
        #
        #     POST .../buckets/950/tasks {"task_id": 950}   (already in 950)
        #     -> 200 {"bucket_id": 950, "bucket": null,
        #             "task_id": 950, "project_view_id": 953, "task": null}
        #
        # Filling them in anyway is the natural thing to do — the objects are right there
        # and a null is easy to read as an oversight — and it is a wire difference on a
        # request the frontend makes constantly, since dropping a card back where it was
        # is an ordinary drag. It also matters for limits: this early exit is what lets a
        # task be reordered inside a bucket that is already at its cap.
        return TaskBucketRead(
            bucket_id=bucket.id,
            bucket=None,
            task_id=task.id,
            project_view_id=view.id,
            task=None,
        )

    _check_limit(session, bucket)

    # Counted on the **requested** bucket and taken *before* the move. Upstream reads the
    # target's size, then increments it once for the task it placed — and it never
    # re-reads, so when a repeating task is diverted to the default bucket the echoed
    # count still describes the bucket that was asked for, plus a task that did not go
    # there. Measured: moving repeating task 922 into done bucket 922 (which holds one
    # task) echoes `count: 2` while bucket 922 still holds exactly one row.
    #
    # Counting the rows *after* the move gives the right answer for the ordinary case and
    # the wrong one here, which is why this is not a post-hoc count.
    requested_bucket_count = (
        session.scalar(
            select(func.count()).select_from(TaskBucket).where(TaskBucket.bucket_id == bucket.id)
        )
        or 0
    )

    landed_in = _apply_done_transition(
        session,
        task=task,
        view=view,
        target_bucket_id=bucket.id,
        previous_bucket_id=previous_bucket_id,
    )

    if placement is None:
        session.add(TaskBucket(task_id=task.id, project_view_id=view.id, bucket_id=landed_in))
    else:
        placement.bucket_id = landed_in
    session.flush()
    # Committed here, not by the caller. Only CRUDRouter commits at the request
    # boundary; a hand-written router gets a session that is closed without one, so a
    # service that merely flushes has its whole write rolled back — the response still
    # renders the new values from the in-memory objects, so it looks completely
    # correct and only a read-back notices. Matches assignee_service/task_service.
    session.commit()

    return TaskBucketRead(
        # The real destination …
        bucket_id=landed_in,
        # … and the requested one, echoed unchanged even when it disagrees. Deliberate:
        # see schemas.task_bucket.
        # Counted after the move: upstream reads the target bucket's size and then adds
        # the task it just placed.
        bucket=_summary(session, bucket, hydrate_creator=False, count=requested_bucket_count + 1),
        task_id=task.id,
        project_view_id=view.id,
        # `read_view` hydrates (assignees / labels / relations) on every read path now, so
        # the embedded task carries them without this module doing anything special.
        task=task_service.read_view(session, task, user_id),
    )
