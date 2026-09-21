"""Where a task sits in its project's views: ``task_buckets`` and ``task_positions``.

Upstream writes these as a side effect of creating and completing tasks, not only when a
card is dragged. Leave them out and every request still answers 200 — but the task has no
``task_buckets`` row, and a manually bucketed Kanban lists tasks *through* that table, so
the board renders empty columns with ``count: 0`` while the list view shows the very same
tasks. That is exactly how it was found: project 2 had three open tasks and an empty board.

Ported from:

* ``setTasksInBucketInViews`` (``tasks.go:1145``) — create;
* ``moveTaskToDoneBuckets`` (``tasks.go:1630``) — a ``done`` flip on update;
* ``calculateNewPositionsForTasks`` / ``defaultPositionsForEmptyView`` — positions.

Lives in its own module because ``bucket_service`` imports ``task_service``; the helpers
both of them need cannot sit in either without closing that cycle.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from calton.models import Bucket, ProjectView
from calton.models.task import Task, base_task_query
from calton.models.task_position import TaskBucket, TaskPosition
from calton.services.project_service import (
    BucketConfigurationMode,
    ProjectViewKind,
    calculate_default_position,
)

#: ``MinPositionSpacing`` (``task_position.go:36``). Below this, halving the lowest
#: position stops producing distinct floats worth relying on.
MIN_POSITION_SPACING = 0.01


def _views(session: Session, project_id: int) -> list[ProjectView]:
    return list(
        session.scalars(
            select(ProjectView)
            .where(ProjectView.project_id == project_id)
            .order_by(ProjectView.id.asc())
        )
    )


def _is_manual_kanban(view: ProjectView) -> bool:
    return (
        view.view_kind == ProjectViewKind.KANBAN
        and view.bucket_configuration_mode == BucketConfigurationMode.MANUAL
    )


def default_bucket_id(session: Session, view: ProjectView) -> int:
    """``getDefaultBucketID``: the configured default, else the view's first bucket."""
    if view.default_bucket_id:
        return int(view.default_bucket_id)
    first = session.scalars(
        select(Bucket).where(Bucket.project_view_id == view.id).order_by(Bucket.position).limit(1)
    ).one_or_none()
    return int(first.id) if first is not None else 0


def _set_bucket(session: Session, *, task_id: int, view_id: int, bucket_id: int) -> None:
    """Upsert the one ``task_buckets`` row a task has per view (``UQE_task_buckets_task_view``)."""
    if not bucket_id:
        # A Kanban view with no buckets at all. Upstream would write bucket 0; a row that
        # points nowhere helps nobody, and the unique index would then block the real one.
        return
    row = session.scalars(
        select(TaskBucket).where(
            TaskBucket.task_id == task_id, TaskBucket.project_view_id == view_id
        )
    ).one_or_none()
    if row is None:
        session.add(TaskBucket(task_id=task_id, project_view_id=view_id, bucket_id=bucket_id))
    else:
        row.bucket_id = bucket_id


def _set_position(session: Session, *, task_id: int, view_id: int, position: float) -> None:
    row = session.scalars(
        select(TaskPosition).where(
            TaskPosition.task_id == task_id, TaskPosition.project_view_id == view_id
        )
    ).one_or_none()
    if row is None:
        session.add(TaskPosition(task_id=task_id, project_view_id=view_id, position=position))
    else:
        row.position = position


def _new_position(session: Session, task: Task, view: ProjectView) -> float:
    """A new task goes to the **top** of the view: half of the lowest position in it.

    An empty view has nothing to halve, so the task takes its index-derived default
    (``defaultPositionsForEmptyView``). When halving would drop under the minimum spacing
    upstream renumbers the whole view; here the task falls back to its default position,
    which stays unique per task and keeps the write local to this one row.
    """
    lowest = session.scalars(
        select(TaskPosition.position)
        .where(TaskPosition.project_view_id == view.id)
        .order_by(TaskPosition.position.asc())
        .limit(1)
    ).one_or_none()
    if lowest is None:
        return calculate_default_position(task.index, 0)
    step = float(lowest) / 2
    if step < MIN_POSITION_SPACING:
        return calculate_default_position(task.index, 0)
    return step


def place_new_task(session: Session, task: Task) -> None:
    """Create-time placement: a bucket in every manual Kanban, a position in every view.

    A task created already ``done`` lands in the view's done bucket when it has one,
    otherwise in the default bucket — the same choice ``setTasksInBucketInViews`` makes.
    **Does not commit.**
    """
    for view in _views(session, task.project_id):
        if _is_manual_kanban(view):
            bucket_id = int(view.done_bucket_id or 0)
            if not task.done or not bucket_id:
                bucket_id = default_bucket_id(session, view)
            _set_bucket(session, task_id=task.id, view_id=view.id, bucket_id=bucket_id)
        _set_position(
            session, task_id=task.id, view_id=view.id, position=_new_position(session, task, view)
        )
    session.flush()


def move_for_done_change(session: Session, task: Task) -> None:
    """``moveTaskToDoneBuckets``: keep the board in step with a ``done`` flip.

    Per manual Kanban view, and each "do nothing" is upstream's:

    * done, and the view has no done bucket → untouched;
    * reopened, and the task is not in the done bucket → untouched (it stays wherever the
      user dragged it);
    * done → the done bucket;
    * reopened out of the done bucket → the default bucket.

    A task with no row yet (created before placement existed) reads as bucket 0, which is
    never the done bucket — so completing it still files it under done. **Does not commit.**
    """
    for view in _views(session, task.project_id):
        if not _is_manual_kanban(view):
            continue
        done_bucket = int(view.done_bucket_id or 0)
        current = session.scalars(
            select(TaskBucket.bucket_id).where(
                TaskBucket.task_id == task.id, TaskBucket.project_view_id == view.id
            )
        ).one_or_none()
        current_bucket = int(current or 0)

        if task.done and not done_bucket:
            continue
        if not task.done and current_bucket != done_bucket:
            continue

        target = done_bucket if task.done else default_bucket_id(session, view)
        _set_bucket(session, task_id=task.id, view_id=view.id, bucket_id=target)
        _set_position(
            session,
            task_id=task.id,
            view_id=view.id,
            position=calculate_default_position(task.index, 0),
        )
    session.flush()


def backfill_project(session: Session, project_id: int) -> int:
    """Give every live task that has no bucket in a manual Kanban its rightful one.

    Repairs data written before ``place_new_task`` existed. Additive only: a task that
    already has a row for the view is left exactly where it is. Returns rows added.
    """
    added = 0
    for view in _views(session, project_id):
        if not _is_manual_kanban(view):
            continue
        placed = set(
            session.scalars(
                select(TaskBucket.task_id).where(TaskBucket.project_view_id == view.id)
            )
        )
        fallback = default_bucket_id(session, view)
        done_bucket = int(view.done_bucket_id or 0)
        # base_task_query, not select(Task): it carries the deleted_at filter, and a
        # soft-deleted task must not reappear on the board.
        tasks = session.scalars(base_task_query().where(Task.project_id == project_id))
        for task in tasks:
            if task.id in placed:
                continue
            bucket_id = done_bucket if (task.done and done_bucket) else fallback
            if not bucket_id:
                continue
            session.add(TaskBucket(task_id=task.id, project_view_id=view.id, bucket_id=bucket_id))
            added += 1
    session.flush()
    return added
