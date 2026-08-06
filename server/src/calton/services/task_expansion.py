"""Attaching the ``?expand=`` fields to task responses.

Kept apart from ``task_service.read_view`` on purpose: the un-expanded response is what
every existing byte-for-byte comparison was recorded against, so expansion adds keys to a
finished view rather than changing how that view is built. A field nobody asked for stays
absent, which is what keeps ``deleted_at``-style key-omission assertions meaningful.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from calton.models.bucket import Bucket
from calton.models.task import Task
from calton.models.task_comment import TaskComment
from calton.models.task_position import TaskBucket
from calton.schemas.bucket_summary import BucketSummary
from calton.schemas.task import TaskRead
from calton.schemas.task_comment import TaskCommentRead
from calton.services import task_hydration, task_service
from calton.services.task_expand import EMBEDDED_COMMENT_LIMIT, Expandable


class PagePrefetch:
    """The batched row sets a whole response needs, so a board can share one set.

    A flat page builds this implicitly and nothing changes. A **board** is why it is a
    separate object: ``_bucket_view`` serialises one column at a time, so every batch
    inside ``expanded_views`` would otherwise run once per column — correct, and ten times
    the queries for a ten-column board. The caller builds this once over every task on the
    board and hands the same instance to each column.
    """

    __slots__ = ("placements", "rows")

    def __init__(
        self, rows: task_service.ReadPrefetch, placements: task_hydration.Placements | None
    ) -> None:
        self.rows = rows
        self.placements = placements


def build_prefetch(
    session: Session, tasks: Sequence[Task], *, user_id: int, view_id: int | None = None
) -> PagePrefetch:
    """Everything :func:`expanded_views` will look up, for all of ``tasks`` at once."""
    return PagePrefetch(
        rows=task_service.build_prefetch(session, tasks, user_id),
        placements=(
            task_hydration.build_placements(session, [task.id for task in tasks], view_id)
            if view_id is not None
            else None
        ),
    )


def expanded_views(
    session: Session,
    tasks: Sequence[Task],
    *,
    user_id: int,
    expand: Sequence[Expandable] = (),
    view_id: int | None = None,
    bucketed: bool = False,
    prefetch: PagePrefetch | None = None,
) -> list[TaskRead]:
    """Serialise these tasks, attaching whatever the request asked to expand.

    Assignees / labels / relations come from ``read_view`` itself — upstream runs
    `addMoreInfoToTasks` on every read, expanded or not.

    `view_id` fills `bucket_id` and `position`, which live on association tables and so
    only exist relative to a view. Read a task outside one and both stay 0, as upstream.

    ``prefetch`` is where the rows come from, not what is done with them: absent one, this
    builds a prefetch covering exactly ``tasks``, so a caller that ignores the parameter
    gets the same bytes at the same cost as one that passes a page-wide instance.
    """
    if prefetch is None:
        prefetch = build_prefetch(session, tasks, user_id=user_id, view_id=view_id)
    views = [task_service.read_view(session, task, user_id, prefetch.rows) for task in tasks]
    if view_id is not None:
        views = task_hydration.with_placements(
            session,
            views,
            tasks,
            view_id,
            include_bucket_id=bucketed,
            placements=prefetch.placements,
        )
    if not expand or not views:
        return views

    task_ids = [task.id for task in tasks]
    updates: dict[int, dict[str, object]] = {task_id: {} for task_id in task_ids}

    if Expandable.BUCKETS in expand:
        for task_id, buckets in _buckets_for(session, task_ids).items():
            updates[task_id]["buckets"] = buckets

    if Expandable.COMMENTS in expand:
        for task_id, comments in _comments_for(session, task_ids, prefetch.rows).items():
            updates[task_id]["comments"] = comments

    if Expandable.COMMENT_COUNT in expand:
        counts = _comment_counts_for(session, task_ids)
        for task_id in task_ids:
            updates[task_id]["comment_count"] = counts.get(task_id, 0)

    return [
        view.model_copy(update=updates[task.id]) if updates[task.id] else view
        for view, task in zip(views, tasks, strict=True)
    ]


def _buckets_for(session: Session, task_ids: list[int]) -> dict[int, list[BucketSummary]]:
    """Every bucket each task sits in, across all of the project's views.

    A task has one bucket *per view*, so this is a list rather than a single value even
    though a board only ever shows one of them at a time.
    """
    rows = session.execute(
        select(TaskBucket.task_id, Bucket)
        .join(Bucket, Bucket.id == TaskBucket.bucket_id)
        .where(TaskBucket.task_id.in_(task_ids))
        .order_by(TaskBucket.task_id, Bucket.position)
    ).all()

    grouped: dict[int, list[BucketSummary]] = {}
    for task_id, bucket in rows:
        grouped.setdefault(task_id, []).append(
            BucketSummary.model_validate(bucket, from_attributes=True)
        )
    return grouped


def _comments_for(
    session: Session, task_ids: list[int], prefetch: task_service.ReadPrefetch
) -> dict[int, list[TaskCommentRead]]:
    """The first 50 comments per task, oldest first.

    The cap is per task, not per request: measured, a task with 55 comments embeds 50 of
    them while ``comment_count`` still reports 55. Truncating the count to match would
    lose the "showing 50 of 55" the two numbers exist to express.

    Which is exactly why the cap is applied by ``row_number() OVER (PARTITION BY task_id)``
    rather than by a ``LIMIT`` on the whole page: a page-wide limit is a different rule
    that agrees with this one only while a single task has comments. Ranking by ``id``,
    which is unique, leaves no ties for the window to break arbitrarily, so the 50 chosen
    here are the same 50 a per-task query returns.
    """
    if not task_ids:
        return {}

    ranked = (
        select(
            TaskComment,
            func.row_number()
            .over(partition_by=TaskComment.task_id, order_by=TaskComment.id)
            .label("rank"),
        )
        .where(TaskComment.task_id.in_(task_ids))
        .subquery()
    )
    comment = aliased(TaskComment, ranked)
    rows = list(
        session.scalars(
            select(comment)
            .where(ranked.c.rank <= EMBEDDED_COMMENT_LIMIT)
            .order_by(ranked.c.task_id, ranked.c.id)
        )
    )

    authors = {row.author_id for row in rows if row.author_id is not None}
    author_views = {
        author_id: task_service.user_view(session, author_id, prefetch) for author_id in authors
    }

    grouped: dict[int, list[TaskCommentRead]] = {}
    for row in rows:
        grouped.setdefault(row.task_id, []).append(
            TaskCommentRead(
                id=row.id,
                comment=row.comment or "",
                author=author_views.get(row.author_id),
                created=row.created,
                updated=row.updated,
            )
        )
    return grouped


def _comment_counts_for(session: Session, task_ids: list[int]) -> dict[int, int]:
    rows = session.execute(
        select(TaskComment.task_id, func.count())
        .where(TaskComment.task_id.in_(task_ids))
        .group_by(TaskComment.task_id)
    ).all()
    return {task_id: int(count) for task_id, count in rows}
