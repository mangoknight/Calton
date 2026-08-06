"""The task collection: one implementation behind three entry points.

``GET /tasks``, ``GET /projects/{p}/tasks`` and ``GET /projects/{p}/views/{v}/tasks`` all
land here. They differ only in how the project scope and the view are derived; everything
after that — filter, search, sort, pagination — is shared, which is the point: three
copies would drift, and the drift would be silent because each copy would keep passing
its own tests.

**The view entry point returns two different top-level types.** With a bucket
configuration and no ``bucket_id`` in the filter, the body is ``Bucket[]`` with tasks
nested inside each bucket; otherwise it is a flat ``Task[]``. Same URL shape, same
project, different type depending on the view's configuration.

Three things about that branch are measured and none of them is guessable:

* **The trigger is ``bucket_configuration_mode``, not ``view_kind``.** The two happen to
  agree on every view the server creates automatically, so branching on ``view_kind``
  passes every test except one deliberately mismatched view.
* **The pagination headers change dimension**: both count *buckets*, while ``per_page``
  limits *tasks within each bucket*. There is no arithmetic relating the header to the
  number of tasks in the body.
* **The caller's ``sort_by`` is discarded**, not merged — buckets are always ordered by
  stored position.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Select, exists, func, or_, select
from sqlalchemy.orm import Session

from calton.core.errors import CaltonError
from calton.core.pagination import Paginator
from calton.filters.compiler import compile_filter_string
from calton.models.bucket import Bucket
from calton.models.project import Project
from calton.models.project_view import ProjectView
from calton.models.saved_filter import Favorite, SavedFilter
from calton.models.task import Task, base_task_query
from calton.models.task_position import TaskBucket, TaskPosition
from calton.models.task_relation import TaskRelation
from calton.permissions.project import NO_PERMISSION, max_permissions_for_projects
from calton.permissions.pseudo import (
    FAVORITES_PSEUDO_PROJECT_ID,
    RealProject,
    SavedFilterProject,
    resolve,
)
from calton.services import task_sort
from calton.services.task_expand import Expandable
from calton.services.task_sort import SortParam

#: ``BucketConfigurationMode`` — ``none`` is the only value that keeps the flat shape.
BUCKET_MODE_NONE = 0

#: The substring upstream looks for to decide the flat fallback. Deliberately a substring
#: test and not a parsed-field test: see :func:`_filters_for_bucket`.
BUCKET_ID_FIELD = "bucket_id"

#: ``RelationKind``. A subtask relation is stored twice, once in each direction, so the
#: parent lookup and the child walk read different rows rather than one row backwards.
RELATION_KIND_SUBTASK = "subtask"
RELATION_KIND_PARENT = "parenttask"

#: ``models.FavoriteKindTask``.
FAVORITE_KIND_TASK = 1

#: ``getTaskIndexFromSearchString``. Finds ``#12`` anywhere in the search text.
_SEARCH_INDEX = re.compile(r"#([0-9]+)")


@dataclass(frozen=True)
class CollectionQuery:
    """The query parameters shared by all three entry points."""

    search: str = ""
    sort_by: tuple[str, ...] = ()
    order_by: tuple[str, ...] = ()
    filter: str = ""
    filter_timezone: str = ""
    filter_include_nulls: bool = False
    expand: tuple[Expandable, ...] = ()


@dataclass
class FlatTasks:
    """A flat ``Task[]`` page, with counts for the pagination headers."""

    tasks: list[Task]
    result_count: int
    total_items: int


@dataclass
class BucketWithTasks:
    """One bucket and the slice of its tasks this page carries.

    ``count`` is the bucket's **total** task count and does not shrink when ``per_page``
    truncates ``tasks`` — the board shows "50 of 60" from these two numbers, so collapsing
    them into one loses the distinction with no visible error.
    """

    bucket: Bucket
    tasks: list[Task]
    count: int


@dataclass
class BucketList:
    """A ``Bucket[]`` page. Both counts are bucket counts, not task counts."""

    buckets: list[BucketWithTasks]
    result_count: int
    total_items: int


CollectionResult = FlatTasks | BucketList


def _timezone(name: str) -> tzinfo:
    """The filter's timezone, or 400/2003 naming the offending value.

    The bad value is echoed back in ``i18n_params`` as well as the message, so clients can
    render it in their own language.
    """
    if not name:
        return UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        raise CaltonError.from_name("models.ErrInvalidTimezone", name=name) from None


def readable_project_ids(session: Session, user_id: int) -> list[int]:
    """Every project the user can read, including ones reached through a parent.

    Deliberately routed through ``max_permissions_for_projects`` rather than a purpose-
    built "projects I own or was granted" query. Visibility here has to agree with the
    permission check on the single-project entry point, and the recursive CTE is the only
    place that rule is written down — a second implementation would be a second rule, and
    inherited access is exactly what a hand-rolled version forgets (a user who owns a
    parent can read tasks in a child owned by someone else).
    """
    all_ids = list(session.scalars(select(Project.id)))
    if not all_ids:
        return []
    permissions = max_permissions_for_projects(session, user_id, all_ids)
    # Archived projects (directly or inherited) are excluded from the default
    # ``GET /tasks``: upstream's ``getRelevantProjectsFromCollection`` calls
    # ``getRawProjectsForUser`` with ``getArchived=false`` by default, which
    # drops ``is_archived`` rows from the recursive descent through ``Project``
    # membership (pkg/models/project.go:742-744, the ``HAVING MAX(...) = 0``
    # clause). Tasks in seed project 21 (inherited-archived) and 22 (directly
    # archived) match the read permission but do not appear upstream; their
    # absence is what the ``auth.token_scope.allowed_group`` corpus case checks.
    # The pseudo-projects (favourites, saved filters, negative ids) never reach
    # this path: ``resolve`` dispatches before any permission query.
    from calton.services.project_service import archived_project_ids

    archived_set: set[int] = set(session.scalars(archived_project_ids(session)))
    return [
        project_id
        for project_id, level in permissions.items()
        if level > NO_PERMISSION and project_id not in archived_set
    ]


def _project_scope(session: Session, *, user_id: int, project_id: int) -> list[int]:
    """The projects a request covers, or 403 if it named one it cannot read.

    Note the denial is code **7003**, not the CRUD pipeline's code 0 — a fourth distinct
    403 body in this API, thrown at a different place from the other three.

    Negative ids never reach the permission query: it raises on them by design, because a
    pseudo id silently matching nothing is how "my favourites are empty" bugs start.
    """
    if project_id == 0:
        return readable_project_ids(session, user_id)

    # Favorites (-1) and saved filters (< -1) are assembled from other tables and have no
    # project row, so they resolve to "everything the user can read" and then narrow with
    # a filter. -1 is checked first: it maps to saved filter 0, which does not exist, so
    # testing `< -1` before `== -1` would 404 the user's favourites.
    if not isinstance(resolve(project_id), RealProject):
        return readable_project_ids(session, user_id)

    allowed, _ = max_permission_of(session, user_id, project_id)
    if not allowed:
        raise CaltonError.from_name(
            "models.ErrUserDoesNotHaveAccessToProject", project_id=project_id, user_id=user_id
        )
    return [project_id]


def max_permission_of(session: Session, user_id: int, project_id: int) -> tuple[bool, int]:
    from calton.permissions.project import can_read

    return can_read(session, user_id, project_id)


def _with_filter(query: CollectionQuery, extra: str) -> CollectionQuery:
    """AND another expression onto the request's filter, parenthesising both sides."""
    combined = f"({query.filter}) && ({extra})" if query.filter else extra
    return CollectionQuery(
        search=query.search,
        sort_by=query.sort_by,
        order_by=query.order_by,
        filter=combined,
        filter_timezone=query.filter_timezone,
        filter_include_nulls=query.filter_include_nulls,
        expand=query.expand,
    )


def _apply_pseudo_project(
    session: Session, query: CollectionQuery, *, user_id: int, project_id: int
) -> CollectionQuery:
    """Narrow the query for the two project ids that have no project row.

    Favorites keeps the request's own filter and adds nothing to it — the narrowing is by
    task id, applied by the caller — while a saved filter contributes its stored
    expression. A saved filter the caller cannot read is refused before its expression is
    read, so a filter's contents cannot be inferred by watching which rows come back.
    """
    pseudo = resolve(project_id)

    if isinstance(pseudo, SavedFilterProject):
        stored = session.scalars(
            select(SavedFilter).where(SavedFilter.id == pseudo.filter_id)
        ).one_or_none()
        if stored is None:
            raise CaltonError.from_name("models.ErrSavedFilterDoesNotExist")
        if stored.owner_id != user_id:
            raise CaltonError.from_name("models.ErrGenericForbidden")
        expression = _saved_filter_expression(stored)
        return _with_filter(query, expression) if expression else query

    return query


def _saved_filter_expression(stored: SavedFilter) -> str:
    """The filter DSL out of a saved filter's stored ``filters`` payload.

    Stored as the serialised TaskCollection, so the expression sits under ``filter`` — the
    column name is ``filters`` (plural) and the field inside is ``filter`` (singular),
    which is an easy place to read the wrong one and silently filter nothing.
    """
    import json

    if not stored.filters:
        return ""
    try:
        payload = json.loads(stored.filters)
    except (ValueError, TypeError):
        # Older rows hold the bare expression rather than a JSON object.
        return str(stored.filters)
    if isinstance(payload, dict):
        return str(payload.get("filter") or "")
    return ""


def favorited_task_ids(session: Session, user_id: int) -> list[int]:
    """Task ids this user has favourited, for the Favorites pseudo project."""
    return list(
        session.scalars(
            select(Favorite.entity_id).where(
                Favorite.user_id == user_id, Favorite.kind == FAVORITE_KIND_TASK
            )
        )
    )


def _merge_view_filter(query: CollectionQuery, view: ProjectView | None) -> CollectionQuery:
    """Fold the view's saved filter into the request's, as ``ReadAll`` does.

    Both are ANDed and each is parenthesised, so a view filter of ``done = false`` and a
    request filter of ``a || b`` cannot combine into ``a || (b && done = false)``.
    """
    if view is None or not view.filter:
        return query

    view_filter = _view_filter_string(view)
    if not view_filter:
        return query

    combined = f"({query.filter}) && ({view_filter})" if query.filter else view_filter
    return CollectionQuery(
        search=query.search,
        sort_by=query.sort_by,
        order_by=query.order_by,
        filter=combined,
        filter_timezone=query.filter_timezone,
        filter_include_nulls=query.filter_include_nulls,
        expand=query.expand,
    )


def _view_filter_string(view: ProjectView) -> str:
    """The ``filter`` string out of a view's stored filter JSON."""
    import json

    if not view.filter:
        return ""
    try:
        stored = json.loads(view.filter)
    except (ValueError, TypeError):
        return ""
    if not isinstance(stored, dict):
        return ""
    return str(stored.get("filter") or "")


def _filters_for_bucket(filter_string: str) -> bool:
    """Whether the filter mentions ``bucket_id`` — a **substring** test, as upstream.

    ``strings.Contains(opts.filter, "bucket_id")`` (task_collection.go:178). Tightening
    this to "the parsed filter has a bucket_id condition" is more correct in every
    ordinary sense and still wrong here: a filter like ``title like 'bucket_id'`` uses the
    text as a search term, yet upstream still falls back to the flat shape. The stricter
    version would answer with ``Bucket[]`` there and diverge on response *type*.
    """
    return BUCKET_ID_FIELD in filter_string


def _search_condition(search: str) -> Any:
    """Title/description match, OR-ed with an index match when the text holds ``#12``.

    Note it is a union, not a replacement: ``s=#12`` matches both the task at index 12 and
    any task whose text contains the literal "#12".
    """
    like = f"%{search}%"
    condition = or_(Task.title.ilike(like), Task.description.ilike(like))

    found = _SEARCH_INDEX.search(search)
    if found:
        return or_(condition, Task.index == int(found.group(1)))
    return condition


def _ordered(statement: Select[tuple[Task]], sort: list[SortParam]) -> Select[tuple[Task]]:
    """Apply the sort keys, joining ``task_positions`` for any ``position`` key."""
    for param in sort:
        if param.sort_by == task_sort.RELEVANCE:
            # Accepted upstream but only meaningful on a database that scores the search.
            # SQLite does not, and upstream skips it in the same situation.
            continue

        if param.sort_by == "position":
            statement = statement.outerjoin(
                TaskPosition,
                (TaskPosition.task_id == Task.id)
                & (TaskPosition.project_view_id == param.project_view_id),
            )
            column: Any = TaskPosition.position
        else:
            column = getattr(Task, param.sort_by)

        statement = statement.order_by(column.desc() if param.descending else column.asc())

    return statement


def _base_statement(
    project_ids: list[int],
    query: CollectionQuery,
    location: tzinfo,
    favorite_ids: list[int] | None = None,
) -> Select[tuple[Task]]:
    statement = base_task_query().where(Task.project_id.in_(project_ids))

    # The Favorites pseudo project is a task-id set, not a project: its members live in
    # whichever real projects the user can read, so the scope stays "everything readable"
    # and the narrowing happens here.
    if favorite_ids is not None:
        statement = statement.where(Task.id.in_(favorite_ids))

    condition = compile_filter_string(
        query.filter, include_nulls=query.filter_include_nulls, location=location
    )
    if condition is not None:
        statement = statement.where(condition)

    if query.search:
        statement = statement.where(_search_condition(query.search))

    return statement


def _is_root_condition(
    project_ids: list[int],
    query: CollectionQuery,
    location: tzinfo,
    favorite_ids: list[int] | None,
) -> Any:
    """A task is a root unless its parent is also in the result set.

    Written as NOT EXISTS over a correlated subquery rather than NOT over an outer join:
    SQL's three-valued logic makes a NULL inside NOT collapse the whole predicate to
    unknown, which would drop the child from the results entirely instead of keeping it
    as a root.

    The parent has to satisfy the same scope *and* the same filter — a parent excluded by
    the filter is not in the result set, so its child is a root and must still appear.
    """
    parent_in_scope = _base_statement(project_ids, query, location, favorite_ids).with_only_columns(
        Task.id
    )
    return ~exists(
        select(TaskRelation.id).where(
            TaskRelation.task_id == Task.id,
            TaskRelation.relation_kind == RELATION_KIND_PARENT,
            TaskRelation.other_task_id.in_(parent_in_scope),
        )
    )


def _descendants_of(session: Session, root_ids: list[int]) -> list[Task]:
    """Every task below these roots, at any depth, excluding the roots themselves.

    Recursive because subtasks nest: a grandchild has to come back too, and a client that
    only ever saw one level would render an incomplete tree with no way to tell.
    Soft-deleted tasks are excluded here as everywhere — ``base_task_query`` does it for
    the roots, and this walk has to match or deleted children reappear under live parents.
    """
    seed = select(TaskRelation.other_task_id.label("task_id")).where(
        TaskRelation.task_id.in_(root_ids),
        TaskRelation.relation_kind == RELATION_KIND_SUBTASK,
    )
    walk = seed.cte("sub_tasks", recursive=True)
    walk = walk.union_all(
        select(TaskRelation.other_task_id).where(
            TaskRelation.task_id == walk.c.task_id,
            TaskRelation.relation_kind == RELATION_KIND_SUBTASK,
        )
    )

    statement = base_task_query().where(
        Task.id.in_(select(walk.c.task_id)), Task.id.not_in(root_ids)
    )
    return list(session.scalars(statement).unique())


def _count(session: Session, statement: Select[tuple[Task]]) -> int:
    return int(session.scalar(select(func.count()).select_from(statement.subquery())) or 0)


def read_all(
    session: Session,
    *,
    user_id: int,
    project_id: int = 0,
    view: ProjectView | None = None,
    query: CollectionQuery,
    paginator: Paginator,
) -> CollectionResult:
    """The shared implementation. Returns either a flat page or a bucket page."""
    project_ids = _project_scope(session, user_id=user_id, project_id=project_id)
    query = _apply_pseudo_project(session, query, user_id=user_id, project_id=project_id)
    favorite_ids = (
        favorited_task_ids(session, user_id) if project_id == FAVORITES_PSEUDO_PROJECT_ID else None
    )
    merged = _merge_view_filter(query, view)
    location = _timezone(merged.filter_timezone)

    # The three conditions are a flat conjunction, not a nesting: "does the filter mention
    # bucket_id" is *not* a question asked only of kanban views. Writing it as a check
    # inside the bucket branch makes a mismatched view (kind=list, mode=manual) keep
    # returning Bucket[] when a bucket_id filter is present, where upstream flattens.
    if (
        view is not None
        and view.bucket_configuration_mode != BUCKET_MODE_NONE
        and not _filters_for_bucket(merged.filter)
    ):
        return _read_buckets(
            session,
            project_ids=project_ids,
            view=view,
            query=merged,
            paginator=paginator,
            location=location,
            favorite_ids=favorite_ids,
        )

    return _read_flat(
        session,
        project_ids=project_ids,
        view=view,
        query=merged,
        paginator=paginator,
        location=location,
        favorite_ids=favorite_ids,
    )


def _read_flat(
    session: Session,
    *,
    project_ids: list[int],
    view: ProjectView | None,
    query: CollectionQuery,
    paginator: Paginator,
    location: tzinfo,
    favorite_ids: list[int] | None = None,
) -> FlatTasks:
    sort = task_sort.parse_sort(
        query.sort_by, query.order_by, view_id=view.id if view is not None else None
    )
    if view is not None:
        sort = task_sort.with_view_position(sort, view.id)
    sort = task_sort.with_id_tiebreaker(sort)

    if not project_ids:
        return FlatTasks(tasks=[], result_count=0, total_items=0)

    statement = _base_statement(project_ids, query, location, favorite_ids)

    expanding_subtasks = Expandable.SUBTASKS in query.expand
    if expanding_subtasks:
        # Only roots are paginated. A task stops being a root when its own parent is in
        # this result set, so a parent and child on the same page are not counted twice.
        statement = statement.where(_is_root_condition(project_ids, query, location, favorite_ids))

    total = _count(session, statement)

    statement = _ordered(statement, sort)
    if paginator.limit > 0:
        statement = statement.limit(paginator.limit).offset(paginator.offset)

    tasks = list(session.scalars(statement).unique())

    if expanding_subtasks and tasks:
        # Appended after the window, which is what makes the response longer than
        # per_page. result_count then reports what was actually sent — see task_expand.
        tasks.extend(_descendants_of(session, [task.id for task in tasks]))

    return FlatTasks(tasks=tasks, result_count=len(tasks), total_items=total)


def _read_buckets(
    session: Session,
    *,
    project_ids: list[int],
    view: ProjectView,
    query: CollectionQuery,
    paginator: Paginator,
    location: tzinfo,
    favorite_ids: list[int] | None = None,
) -> BucketList:
    """One query per bucket, each limited to ``per_page`` tasks.

    The caller's sort is replaced outright — ``opts.sortby`` is *assigned* a single
    ``position asc`` in ``GetTasksInBucketsForView``, not appended to — so a request
    asking for ``priority desc`` gets position order back with no error and no indication
    that the parameter was ignored.
    """
    buckets = list(
        session.scalars(
            select(Bucket).where(Bucket.project_view_id == view.id).order_by(Bucket.position)
        )
    )

    sort = [SortParam(sort_by="position", order_by=task_sort.ASCENDING, project_view_id=view.id)]

    with_tasks: list[BucketWithTasks] = []
    for bucket in buckets:
        statement = _base_statement(project_ids, query, location, favorite_ids).where(
            Task.id.in_(
                select(TaskBucket.task_id).where(
                    TaskBucket.bucket_id == bucket.id,
                    TaskBucket.project_view_id == view.id,
                )
            )
        )
        count = _count(session, statement)

        limited = _ordered(statement, sort)
        # per_page truncates each bucket independently, and the *default* per_page counts
        # too — guarding this with "only when the caller passed per_page" returns all 60
        # tasks, and since `count` is also 60 the response looks entirely self-consistent.
        #
        # The guard is on `limit`, not on `per_page`, because page=0 means "everything"
        # here exactly as it does on the flat branch: measured, page=0 returns every task
        # in each bucket even when per_page is also given, while total-pages still divides
        # by per_page. Capping on per_page instead silently truncates a request upstream
        # leaves whole, and every other bucket case still passes.
        if paginator.limit > 0:
            limited = limited.limit(paginator.limit)

        with_tasks.append(
            BucketWithTasks(
                bucket=bucket, tasks=list(session.scalars(limited).unique()), count=count
            )
        )

    # Both counts are the number of buckets. total_items feeds total-pages, so the page
    # count is over buckets while the body is paginated over tasks.
    return BucketList(buckets=with_tasks, result_count=len(with_tasks), total_items=len(with_tasks))


__all__ = [
    "BucketList",
    "BucketWithTasks",
    "CollectionQuery",
    "CollectionResult",
    "FlatTasks",
    "read_all",
    "readable_project_ids",
]
