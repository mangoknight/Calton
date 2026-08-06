"""Project view create/update/delete (``project_view.go``).

The create path is shared with T16: a project's four default views are made by the same
``createProjectView``, which is also what creates a Kanban view's three buckets. So a
hand-made Kanban view with a manual bucket configuration gets To-Do / Doing / Done too,
and its ``default_bucket_id``/``done_bucket_id`` point at the first and last — measured on
a view created through ``PUT /projects/{p}/views``, not only on the ones a new project
comes with.

**Creating any view rewrites the project's task positions.** ``createProjectView`` ends in
``RecalculateTaskPositions``, so a project with seven tasks gains seven ``task_positions``
rows for the new view *whatever its kind* — measured on a plain List view as well as on a
Kanban one. A manually bucketed Kanban additionally gets seven ``task_buckets`` rows
pointing at its backlog. Skipping this is invisible until something reads the view: the
tasks are still there, they just have no position in it.

**Update is a whole-model replace with no exceptions at all**, which is what makes this
resource different from projects. All eight columns in ``Update()``'s ``Cols(...)`` are
written unconditionally with no nil guard anywhere — including ``filter``,
``bucket_configuration``, ``default_bucket_id`` and ``done_bucket_id``. Measured: renaming
a Kanban view whose buckets were 9951/9953 leaves those two columns at **0**, so the view
keeps its buckets while forgetting which one is the default and which is done. There is no
equivalent of ``Project.ParentProjectID`` here.

⚠️ **Delete does not delete the view's buckets.** It removes the ``project_views`` row,
then ``task_buckets`` and ``task_positions`` **by view id** — and leaves ``buckets``
alone. Measured: after deleting a Kanban view its three bucket rows are still in the
table, orphaned, while its seven ``task_buckets`` rows are gone. Cleaning them up as well
is the obvious tidy-up and is a divergence: bucket ids are ``AUTOINCREMENT``, so the rows
being orphaned rather than removed is observable in the ids handed out afterwards.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from calton.db.types import ZERO_TIME
from calton.filters.parser import parse_task_filter
from calton.models.bucket import Bucket
from calton.models.project_view import ProjectView
from calton.models.task import Task
from calton.models.task_position import TaskBucket, TaskPosition
from calton.permissions.pseudo import FAVORITES_PSEUDO_PROJECT_ID
from calton.schemas.project_view import (
    BucketConfiguration,
    ViewFilter,
    stored_bucket_configuration_of,
    stored_filter_of,
)
from calton.services.project_service import (
    BucketConfigurationMode,
    ProjectViewKind,
    calculate_default_position,
    create_default_buckets,
)

#: ``RecalculateTaskPositions`` spreads tasks across the same span upstream uses, so the
#: nth of ``count`` tasks sits at ``(n + 1) * 2**32 / count``. Measured: seven tasks in
#: project 950 land on 613566756.571…, twice that, three times that, and so on.
_POSITION_SPAN = 2**32


def _favorites_view(
    view_id: int, title: str, kind: int, position: float, filter_: str | None
) -> ProjectView:
    """One of the Favorites pseudo project's views, built rather than queried."""
    return ProjectView(
        id=view_id,
        project_id=FAVORITES_PSEUDO_PROJECT_ID,
        title=title,
        view_kind=kind,
        position=position,
        filter=filter_,
        bucket_configuration_mode=0,
        bucket_configuration=None,
        default_bucket_id=0,
        done_bucket_id=0,
        created=ZERO_TIME,
        updated=ZERO_TIME,
    )


#: The Favorites views as plain data. ⚠️ **THREE, and no Kanban** — "a project gets four
#: default views" is not a universal rule, and this is the counter-example. Upstream keeps
#: them in a hardcoded struct (``FavoritesPseudoProject.Views``), never as rows, so they
#: carry negative ids and the **zero time**. Measured identical for every authenticated
#: caller, so they are constants rather than per-user data.
#:
#: ⚠️ The filter is the marshalled **document**, not the bare expression, because that is
#: what ``project_views.filter`` holds everywhere else and what ``view_read`` parses. A
#: second copy of this table once spelled it as the expression; the day the project body
#: started rendering through the same serializer, its List view silently lost its filter.
_FAVORITES_VIEW_DATA: tuple[tuple[int, str, int, float, str | None], ...] = (
    (-1, "List", 0, 100.0, stored_filter_of(ViewFilter(filter="done = false"))),
    (-2, "Gantt", 1, 200.0, None),
    (-3, "Table", 2, 300.0, None),
)


def favorites_views() -> list[ProjectView]:
    """Fresh instances of the three Favorites views.

    ⚠️ A function rather than a module-level tuple of ORM objects. These are
    ``ProjectView`` instances, and a single shared instance handed to every request is one
    ``session.add`` away from being persisted — at which point Favorites acquires real
    rows with negative ids and stops being synthetic. Rebuilding is cheap; the failure it
    prevents is not.
    """
    return [_favorites_view(*row) for row in _FAVORITES_VIEW_DATA]


def views_of(session: Session, project_id: int) -> list[ProjectView]:
    """Every view of a project, in position order.

    Upstream orders by ``position asc`` and nothing else, which leaves ties to the
    database. The id is added as a tiebreak so two views sharing a position — easy to
    produce, since an update sets position to 0 whenever the body omits it — come back in
    a stable order rather than in whatever order the rows happen to be scanned.

    ☠️ **Favorites answers ``[]`` here even though three views exist for it.** The
    collection is built by ``getViewsForProject``, which only ever queries by
    ``project_id`` and finds no rows, while the *item* route reads the hardcoded struct —
    so ``GET /projects/-1/views`` lists nothing and ``GET /projects/-1/views/-1`` returns a
    view. Measured on both. Serving :func:`favorites_views` from here would be the tidy,
    consistent thing and diverges: an implementation that "fixes" the inconsistency
    changes the collection every favourites client reads.
    """
    return list(
        session.scalars(
            select(ProjectView)
            .where(ProjectView.project_id == project_id)
            .order_by(ProjectView.position.asc(), ProjectView.id.asc())
        )
    )


def load_view(session: Session, project_id: int, view_id: int) -> ProjectView | None:
    """The view, which must belong to the project given — ``GetProjectViewByIDAndProject``.

    A view that exists under a *different* project is not found here, and the route turns
    that into the same 404/3014 a missing view gets. Upstream resolves the pair before
    deleting anything for exactly this reason: the bucket and position cleanup below runs
    by view id alone, so a delete scoped to the wrong parent would still wipe another
    project's rows while matching no ``project_views`` row at all.
    """
    if project_id == FAVORITES_PSEUDO_PROJECT_ID:
        # ``GetProjectViewByIDAndProject`` short-circuits here, before any query, and only
        # for negative view ids. Favorites owns no rows, so without this the three views
        # its own item route serves would be unreachable.
        return next((view for view in favorites_views() if view.id == view_id), None)

    return session.scalars(
        select(ProjectView).where(ProjectView.id == view_id, ProjectView.project_id == project_id)
    ).one_or_none()


def validate_filter(view_filter: ViewFilter | None) -> None:
    """Reject a filter expression the task filter parser will not accept.

    Upstream runs ``getTaskFiltersFromFilterString`` before touching the database on both
    create and update, so an unknown field is **400/4016** and a malformed expression is
    **400/4024** — both measured, and both arriving *instead of* the write rather than
    after it. An empty expression is not parsed and is accepted.
    """
    if view_filter is None or not view_filter.filter:
        return
    parse_task_filter(view_filter.filter)


def validate_bucket_configuration(mode: int, entries: list[BucketConfiguration] | None) -> None:
    """Same parse, for each entry of a filtered view's bucket configuration.

    Only in ``filter`` mode: upstream guards the loop on the mode, so a configuration left
    over on a manual view is stored without ever being parsed.
    """
    if mode != BucketConfigurationMode.FILTER or not entries:
        return
    for entry in entries:
        validate_filter(entry.filter)


def _recalculate_task_positions(session: Session, view: ProjectView) -> None:
    """Give every task in the view's project a position in it (``RecalculateTaskPositions``)."""
    task_ids = list(
        session.scalars(
            select(Task.id).where(Task.project_id == view.project_id).order_by(Task.id.asc())
        )
    )
    if not task_ids:
        return

    session.execute(delete(TaskPosition).where(TaskPosition.project_view_id == view.id))
    step = _POSITION_SPAN / len(task_ids)
    for index, task_id in enumerate(task_ids):
        session.add(
            TaskPosition(task_id=task_id, project_view_id=view.id, position=step * (index + 1))
        )
    session.flush()


def _add_tasks_to_bucket(session: Session, view: ProjectView, bucket: Bucket) -> None:
    """Drop every existing task of the project into the view's backlog (``addTasksToView``)."""
    task_ids = list(
        session.scalars(
            select(Task.id).where(Task.project_id == view.project_id).order_by(Task.id.asc())
        )
    )
    for task_id in task_ids:
        session.add(TaskBucket(bucket_id=bucket.id, task_id=task_id, project_view_id=view.id))
    session.flush()


def create_view(
    session: Session,
    *,
    project_id: int,
    title: str,
    view_kind: int,
    view_filter: ViewFilter | None,
    position: float,
    bucket_configuration_mode: int,
    bucket_configuration: list[BucketConfiguration] | None,
    owner_id: int,
) -> ProjectView:
    """Create one view, its buckets when it is a manually bucketed Kanban, and its positions."""
    validate_filter(view_filter)
    validate_bucket_configuration(bucket_configuration_mode, bucket_configuration)

    view = ProjectView(
        project_id=project_id,
        title=title,
        view_kind=view_kind,
        filter=stored_filter_of(view_filter),
        position=position,
        bucket_configuration_mode=bucket_configuration_mode,
        bucket_configuration=stored_bucket_configuration_of(bucket_configuration),
    )
    session.add(view)
    session.flush()

    # Needs the id, so it can only happen after the insert. A position of zero means
    # "unset" and is derived from the id, exactly as for projects and tasks. A position
    # the client *did* send is kept: measured, ``position: 12345`` stays 12345.
    view.position = calculate_default_position(view.id, position)
    session.flush()

    if (
        view.view_kind == ProjectViewKind.KANBAN
        and view.bucket_configuration_mode == BucketConfigurationMode.MANUAL
    ):
        buckets = create_default_buckets(session, view, owner_id)
        if buckets:
            _add_tasks_to_bucket(session, view, buckets[0])

    _recalculate_task_positions(session, view)
    return view


def update_view(
    session: Session,
    *,
    view: ProjectView,
    title: str,
    view_kind: int,
    view_filter: ViewFilter | None,
    position: float,
    bucket_configuration_mode: int,
    bucket_configuration: list[BucketConfiguration] | None,
) -> ProjectView:
    """Replace the whole view. Every field the body omitted is reset to its zero value.

    ⚠️ That includes ``filter`` (omitting it clears it to NULL), ``bucket_configuration``,
    and both bucket pointers. It is not an oversight to be guarded against; see the module
    docstring. Buckets are **not** created here either: only the create path makes them,
    so switching a view to Kanban by update leaves it without any — measured, and it is
    upstream's behaviour too. ``created`` is untouched in the row; the *response* carries
    the zero time, which is the route's business, not this function's.
    """
    validate_filter(view_filter)
    validate_bucket_configuration(bucket_configuration_mode, bucket_configuration)

    view.title = title
    view.view_kind = view_kind
    view.filter = stored_filter_of(view_filter)
    view.position = position
    view.bucket_configuration_mode = bucket_configuration_mode
    view.bucket_configuration = stored_bucket_configuration_of(bucket_configuration)
    view.default_bucket_id = 0
    view.done_bucket_id = 0
    session.flush()
    return view


def delete_view(session: Session, *, view: ProjectView) -> None:
    """Delete a view, its task-to-bucket links and its task positions — but not its buckets.

    The ``buckets`` rows are deliberately left behind: measured, upstream's ``Delete``
    touches ``project_views``, ``task_buckets`` and ``task_positions`` and nothing else, so
    a deleted Kanban view's three buckets stay in the table unreachable. Removing them
    here would be tidier and would diverge — bucket ids are ``AUTOINCREMENT``, so which
    ids the next bucket gets is observable.
    """
    session.execute(delete(TaskBucket).where(TaskBucket.project_view_id == view.id))
    session.execute(delete(TaskPosition).where(TaskPosition.project_view_id == view.id))
    session.delete(view)
    session.flush()
