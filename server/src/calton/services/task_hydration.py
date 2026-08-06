"""``bucket_id`` and ``position`` — the two task fields that only exist inside a view.

Every other field of a task is a property of the task. These two are properties of *the
task in a particular view*: they live on ``task_buckets`` and ``task_positions``, keyed by
``(task_id, project_view_id)``. The same task genuinely has different values in different
views, so a serialiser that only sees the task cannot fill them — which is why they are
attached here, by the collection, rather than in ``task_service.read_view``.

**History worth keeping, because it is the reason this module is now small.** It was
written for T28 and also carried assignees / labels / related_tasks, because
``read_view`` filled none of them. The relations line then landed a proper implementation
of those same three fields in ``task_service`` — with **permission filtering** this
module's version did not have, plus alphabetical key ordering and the correct nested
shape. Two implementations of "what a task looks like" is exactly what this module's own
docstring warned would drift, and the drift was already there and **security-relevant**:
without the permission check, ``related_tasks`` hands back the title, description and
dates of any task by id. So that half was deleted rather than reconciled, and what remains
is the half nothing else implements.

The one asymmetry to preserve is documented on :func:`with_placements`.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from calton.models.task import Task
from calton.models.task_position import TaskBucket, TaskPosition
from calton.schemas.task import TaskRead


class Placements:
    """``bucket_id`` / ``position`` for a set of tasks in one view, fetched once.

    A board serialises each column separately, so without this the two placement queries
    run once per column even though every column reads the same two tables in the same
    view. Holding them is what lets the whole board resolve them together; the mapping is
    keyed by task id, so which column a task was serialised under does not matter.
    """

    __slots__ = ("buckets", "positions", "view_id")

    def __init__(self, view_id: int, buckets: dict[int, int], positions: dict[int, float]) -> None:
        self.view_id = view_id
        self.buckets = buckets
        self.positions = positions


def placements_for(
    session: Session, task_ids: list[int], view_id: int
) -> tuple[dict[int, int], dict[int, float]]:
    """``bucket_id`` and ``position`` for these tasks **in one view**.

    A task with no row in either table gets 0, which is what upstream sends too.
    """
    if not task_ids:
        return {}, {}

    buckets = {
        row.task_id: int(row.bucket_id)
        for row in session.scalars(
            select(TaskBucket).where(
                TaskBucket.task_id.in_(task_ids), TaskBucket.project_view_id == view_id
            )
        )
    }
    positions = {
        row.task_id: float(row.position)
        for row in session.scalars(
            select(TaskPosition).where(
                TaskPosition.task_id.in_(task_ids), TaskPosition.project_view_id == view_id
            )
        )
    }
    return buckets, positions


def build_placements(session: Session, task_ids: list[int], view_id: int) -> Placements:
    buckets, positions = placements_for(session, task_ids, view_id)
    return Placements(view_id, buckets, positions)


def with_placements(
    session: Session,
    views: list[TaskRead],
    tasks: Sequence[Task],
    view_id: int,
    *,
    include_bucket_id: bool = True,
    placements: Placements | None = None,
) -> list[TaskRead]:
    """Attach the per-view ``bucket_id`` / ``position`` to an already-serialised page.

    ⚠️ ``include_bucket_id=False`` on the **flat** shape, and the two fields genuinely
    differ here — this is not a symmetry waiting to be restored. Measured: when a Kanban
    view falls back to a flat ``Task[]`` (because the filter mentions ``bucket_id``),
    upstream returns those tasks with ``bucket_id: 0`` while still carrying their real
    ``position``. The same tasks read as a board carry both.

    So ``bucket_id`` is a property of *the board shape*, not of the task-in-a-view. Filling
    it on the flat shape is the obvious thing to do — we have the view, the data is right
    there, and the value is even *correct* — and it is a wire difference on every flat read
    of a bucketed view.

    A supplied ``placements`` replaces the two queries and nothing else. It must have been
    built for ``view_id``: these values are per view, so a mapping from another view would
    fill plausible-looking positions from the wrong board.
    """
    if placements is not None and placements.view_id != view_id:
        raise ValueError(
            f"placements were built for view {placements.view_id}, not {view_id}; "
            "bucket_id and position are per view and do not carry across"
        )
    if placements is not None:
        buckets, positions = placements.buckets, placements.positions
    else:
        buckets, positions = placements_for(session, [task.id for task in tasks], view_id)
    return [
        entry.model_copy(
            update={
                "bucket_id": buckets.get(task.id, 0) if include_bucket_id else 0,
                "position": positions.get(task.id, 0.0),
            }
        )
        for entry, task in zip(views, tasks, strict=True)
    ]
