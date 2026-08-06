"""Task endpoints.

Hand-written rather than mounted through ``CRUDRouter``, because tasks do not have the
shape it registers: creation hangs off ``/projects/{project}/tasks`` while the item
routes hang off ``/tasks/{task}``, and the collection reads are the three-entry-point
``TaskCollection`` (T23). What is shared with ``CRUDRouter`` is everything that is a wire
contract — the inverted verbs, ``read_one_response``'s ``x-max-permission`` header and
``deleted_response``'s body — so those come from that module rather than being re-spelled.

**Path parameters are parsed here, not by FastAPI.** Declaring ``task: int`` would answer
422 with FastAPI's ``{"detail": ...}`` for ``/tasks/abc``, where upstream answers
400/2004 ``Invalid model provided: Bad Request``. Measured, and the two are far apart:
one is a JSON body an MCP client can parse against the v1 error contract and the other
is not.

The one place a non-numeric segment is *valid* is ``by-index``, whose ``{project}`` also
accepts a project identifier string (``ResolveProjectIdentifier``, ``routes.go:676``).
That is unique to this endpoint — no other ``/projects/{id}/*`` route takes one — so it is
not generalised.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from calton.core.crud_router import deleted_response, path_param_as_id, read_one_response
from calton.core.errors import CaltonError, EchoStringError, UnauthorizedError
from calton.core.pagination import Paginator, paginated_response
from calton.core.policy import FORBIDDEN_READ_MESSAGE, ForbiddenError
from calton.db.session import get_db
from calton.models import Project
from calton.models.project_view import ProjectView
from calton.permissions import task as task_permissions
from calton.permissions.project import can_write as project_can_write
from calton.permissions.pseudo import RealProject, resolve
from calton.schemas.bucket import BucketRead
from calton.schemas.bulk_task import BulkTaskRead, BulkTaskWrite
from calton.schemas.message import Message
from calton.schemas.task import TaskRead, TaskWrite, TaskWriteResponse
from calton.services import (
    project_service,
    task_bulk,
    task_collection,
    task_expansion,
    task_service,
)
from calton.services.task_expand import Expandable, parse_expand

#: ``ResolveProjectIdentifier`` raises this straight from the route middleware, so it is a
#: bare string error with **no code** — not the 3001 the business layer uses for a missing
#: project. Measured: ``{"message": "Project not found"}``. Adding a code would fork the
#: contract for clients that branch on its presence.
PROJECT_NOT_FOUND_BY_IDENTIFIER = "Project not found"


def _auth_user_id(request: Request) -> int:
    """The authenticated user's id.

    ``request.state.auth`` is populated by the JWT middleware (T14) and the API token
    middleware (T15). Until one of them runs there is no subject, and the correct answer
    is the middleware's 401 — never a silent fallback to some default user, which would
    make every endpoint here publicly writable.
    """
    auth = getattr(request.state, "auth", None)
    user_id = getattr(auth, "id", None)
    if not isinstance(user_id, int):
        raise UnauthorizedError()
    return user_id


def _is_integer(raw: str) -> bool:
    try:
        int(raw)
    except ValueError:
        return False
    return True


def _resolve_project(session: Session, raw: str) -> int:
    """``{project}`` as an id, accepting a project identifier string.

    The numeric branch goes through ``path_param_as_id``, so a project id here is bounded
    to int64 exactly as a task id is; only a value that is not an integer at all falls
    through to the identifier lookup. Upstream identifiers are never all-digits, so taking
    the numeric branch first shadows nothing.
    """
    if _is_integer(raw):
        return path_param_as_id(raw)

    project = session.scalars(select(Project).where(Project.identifier == raw)).one_or_none()
    if project is None:
        raise EchoStringError(404, PROJECT_NOT_FOUND_BY_IDENTIFIER)
    return project.id


def _query_of(request: Request) -> task_collection.CollectionQuery:
    """Collection parameters off the query string.

    ``sort_by`` and ``order_by`` repeat, and their **order matters** — they are paired by
    position, so ``getlist`` is required where a plain ``get`` would silently keep only
    the last one and turn a two-key sort into a one-key sort.
    """
    params = request.query_params
    return task_collection.CollectionQuery(
        search=params.get("s", ""),
        sort_by=tuple(params.getlist("sort_by")),
        order_by=tuple(params.getlist("order_by")),
        filter=params.get("filter", ""),
        filter_timezone=params.get("filter_timezone", ""),
        filter_include_nulls=params.get("filter_include_nulls", "") == "true",
        expand=tuple(parse_expand(params.getlist("expand"))),
    )


def _load_view(session: Session, project_id: int, view_id: int) -> ProjectView:
    """The view, which must belong to the project named in the path.

    A view that exists but belongs to another project answers exactly the same 404/3014 as
    one that does not exist, so this endpoint does not disclose that a foreign view is
    real. (The project endpoints do disclose existence — do not generalise either way.)
    """
    view = session.scalars(
        select(ProjectView).where(ProjectView.id == view_id, ProjectView.project_id == project_id)
    ).one_or_none()
    if view is None:
        raise CaltonError.from_name("models.ErrProjectViewDoesNotExist")
    return view


def _bucket_view(
    session: Session,
    entry: task_collection.BucketWithTasks,
    user_id: int,
    expand: tuple[Expandable, ...] = (),
    view_id: int | None = None,
    prefetch: task_expansion.PagePrefetch | None = None,
) -> BucketRead:
    """A bucket plus its tasks, leaving ``tasks`` unset when the bucket is empty.

    ``None`` rather than ``[]`` is what makes the key disappear; see ``schemas.bucket``.

    ``prefetch`` must cover **every task on the board**, not this column's — see
    :func:`_collection`. The bucket's own ``created_by`` goes through it too: boards are
    usually created in one sitting, so ten columns share one creator and thus one query.
    """
    tasks = task_expansion.expanded_views(
        session,
        entry.tasks,
        user_id=user_id,
        expand=expand,
        view_id=view_id,
        bucketed=True,
        prefetch=prefetch,
    )
    return BucketRead(
        id=entry.bucket.id,
        title=entry.bucket.title,
        project_view_id=entry.bucket.project_view_id,
        tasks=tasks or None,
        limit=entry.bucket.limit or 0,
        count=entry.count,
        position=entry.bucket.position or 0,
        created=entry.bucket.created,
        updated=entry.bucket.updated,
        created_by=task_service.user_view(
            session, entry.bucket.created_by_id, prefetch.rows if prefetch else None
        ),
    )


def build_router() -> APIRouter:
    router = APIRouter()

    # ⚠️ ROUTE ORDER IS LOAD-BEARING, and only for the two routes below.
    #
    # FastAPI matches in registration order, so `/tasks/all` must be registered before
    # `/tasks/{task}` or the parameterised route swallows it: "all" is then parsed as a
    # task id, fails, and the request answers 400 instead of listing tasks. That is
    # precisely what upstream does — Calton never registered the alias, so `/tasks/all`
    # returns 400 there — and reproducing the bug is the one thing this alias exists to
    # avoid. `test_the_alias_is_registered_before_the_parameterised_route` swaps the two
    # and asserts the 400 comes back, so the ordering is checked rather than hoped for.
    @router.get("/tasks/all", response_model=list[TaskRead])
    def read_all_tasks_alias(
        request: Request,
        paginator: Paginator = Depends(),
        session: Session = Depends(get_db),
    ) -> Response:
        return _collection(request, session, paginator, project_id=0)

    @router.get("/tasks", response_model=list[TaskRead])
    def read_all_tasks(
        request: Request,
        paginator: Paginator = Depends(),
        session: Session = Depends(get_db),
    ) -> Response:
        return _collection(request, session, paginator, project_id=0)

    @router.get("/projects/{project}/tasks", response_model=list[TaskRead])
    def read_project_tasks(
        request: Request,
        # Declared so the operation documents its path parameter: FastAPI emits no
        # `parameters` block for an id read off `request`, and schemathesis refuses to
        # fuzz such an operation (`InvalidSchema: Path parameter ... is not defined`).
        # `str`, never `int` — an int annotation answers 422 where upstream answers
        # 400/2004. See core.crud_router.path_parameter_block.
        project: Annotated[str, Path(min_length=1)],
        paginator: Paginator = Depends(),
        session: Session = Depends(get_db),
    ) -> Response:
        project_id = path_param_as_id(project)
        return _collection(request, session, paginator, project_id=project_id)

    @router.get(
        "/projects/{project}/views/{view}/tasks",
        response_model=list[TaskRead] | list[BucketRead],
    )
    def read_view_tasks(
        request: Request,
        project: Annotated[str, Path(min_length=1)],
        view: Annotated[str, Path(min_length=1)],
        paginator: Paginator = Depends(),
        session: Session = Depends(get_db),
    ) -> Response:
        project_id = path_param_as_id(project)
        view_id = path_param_as_id(view)
        return _collection(request, session, paginator, project_id=project_id, view_id=view_id)

    def _collection(
        request: Request,
        session: Session,
        paginator: Paginator,
        *,
        project_id: int,
        view_id: int | None = None,
    ) -> Response:
        user_id = _auth_user_id(request)
        view = _load_view(session, project_id, view_id) if view_id is not None else None

        result = task_collection.read_all(
            session,
            user_id=user_id,
            project_id=project_id,
            view=view,
            query=_query_of(request),
            paginator=paginator,
        )

        if isinstance(result, task_collection.BucketList):
            board_view_id = view.id if view is not None else None
            # One prefetch for the whole board rather than one per column. The columns are
            # a presentation split — the same tables, the same view, the same caller — so
            # batching per column leaves the fan-out multiplied by however many columns the
            # board happens to have, which is the factor a ten-column board notices most.
            prefetch = task_expansion.build_prefetch(
                session,
                [task for entry in result.buckets for task in entry.tasks],
                user_id=user_id,
                view_id=board_view_id,
            )
            body = [
                _bucket_view(
                    session,
                    entry,
                    user_id,
                    _query_of(request).expand,
                    view_id=board_view_id,
                    prefetch=prefetch,
                ).model_dump(mode="json")
                for entry in result.buckets
            ]
        else:
            body = [
                view.model_dump(mode="json")
                for view in task_expansion.expanded_views(
                    session,
                    result.tasks,
                    user_id=user_id,
                    expand=_query_of(request).expand,
                    view_id=view_id,
                )
            ]

        return paginated_response(
            body,
            total_items=result.total_items,
            per_page=paginator.per_page,
            result_count=result.result_count,
        )

    @router.put("/projects/{project}/tasks", status_code=201, response_model=TaskWriteResponse)
    def create_task(
        request: Request,
        project: Annotated[str, Path(min_length=1)],
        body: TaskWrite,
        session: Session = Depends(get_db),
    ) -> Any:
        user_id = _auth_user_id(request)
        project_id = path_param_as_id(project)

        # A pseudo project (Favorites at -1, saved filters below it) has no rows to hold a
        # task. Upstream refuses with the CRUD pipeline's 403/code 0 rather than a 400 or a
        # 404 — measured on /projects/-1/tasks — so an MCP client sees "not allowed",
        # not "malformed".
        if not isinstance(resolve(project_id), RealProject):
            raise ForbiddenError()

        # Ahead of permission on purpose — see _refuse_if_archived.
        _refuse_if_archived(session, project_id)

        if not project_can_write(session, user_id, project_id):
            # Existence is checked only once permission has been refused, so that a
            # project the caller cannot see reports 403 rather than confirming it exists.
            if _project_missing(session, project_id):
                raise CaltonError.from_name("models.ErrProjectDoesNotExist")
            raise ForbiddenError()

        return task_service.create_task(session, project_id=project_id, data=body, user_id=user_id)

    @router.get("/tasks/{task}", response_model=TaskRead)
    def read_task(
        request: Request,
        task: Annotated[str, Path(min_length=1)],
        session: Session = Depends(get_db),
    ) -> Response:
        user_id = _auth_user_id(request)
        task_id = path_param_as_id(task)

        # 404 before 403: a task that does not exist must not be reported as forbidden,
        # and permissions resolve through the task's project, which a missing task has no
        # way to name.
        # Named `loaded` because `task` is now the raw path parameter.
        loaded = task_service.get_task(session, task_id)
        allowed, max_permission = task_permissions.can_read(session, user_id, task_id)
        if not allowed:
            raise ForbiddenError(FORBIDDEN_READ_MESSAGE)

        view = task_service.read_view(session, loaded, user_id)
        return read_one_response(view.model_dump(mode="json"), max_permission)

    @router.get("/projects/{project}/tasks/by-index/{index}", response_model=TaskRead)
    def read_task_by_index(
        request: Request,
        project: Annotated[str, Path(min_length=1)],
        index: Annotated[str, Path(min_length=1)],
        session: Session = Depends(get_db),
    ) -> Response:
        user_id = _auth_user_id(request)
        project_id = _resolve_project(session, project)
        task_index = path_param_as_id(index)

        # The task is resolved first and the permission is then asked of *the task*, not
        # of the project. Checking the project first looks equivalent and is not: a
        # project that does not exist would answer 403 where upstream answers 404/4002,
        # because upstream never treats the project as a resource on this route at all.
        task = task_service.get_task_by_index(session, project_id, task_index)
        allowed, max_permission = task_permissions.can_read(session, user_id, task.id)
        if not allowed:
            raise ForbiddenError(FORBIDDEN_READ_MESSAGE)

        view = task_service.read_view(session, task, user_id)
        return read_one_response(view.model_dump(mode="json"), max_permission)

    # ⚠️ Must stay above POST /tasks/{task}. Starlette matches in registration order, so
    # the other way round "bulk" is parsed as a task id and every bulk request answers
    # 400/2004 from path_param_as_id — a plausible-looking error that never mentions
    # routing. Same reason GET /tasks/all sits above GET /tasks/{task}.
    # `test_bulk_route_is_not_shadowed_by_the_task_id_route` fails if they are swapped.
    @router.post("/tasks/bulk", response_model=BulkTaskRead)
    def bulk_update_tasks(
        request: Request, body: BulkTaskWrite, session: Session = Depends(get_db)
    ) -> Any:
        # No permission check here: unlike every other task route, the gates are part of
        # the operation (they depend on which rows the ids resolve to) and their order is
        # observable, so they live in the service. See task_bulk's module docstring.
        return task_bulk.bulk_update(
            session,
            task_ids=body.task_ids,
            fields=body.fields,
            values=body.values,
            user_id=_auth_user_id(request),
        )

    @router.post("/tasks/{task}", response_model=TaskWriteResponse)
    def update_task(
        request: Request,
        task: Annotated[str, Path(min_length=1)],
        body: TaskWrite,
        session: Session = Depends(get_db),
    ) -> Any:
        return _update(request, task, body, session)

    # ⛔ No PATCH — upstream registers only GET/POST/DELETE on `/tasks/:projecttask` and
    # answers 405 to PATCH. See the note in `core.crud_router` for why an extra verb is a
    # contract change rather than a convenience.

    def _update(request: Request, task: str, body: TaskWrite, session: Session) -> TaskRead:
        user_id = _auth_user_id(request)
        # ⚠️ A body `id` shadows the path segment. Echo binds path parameters before the
        # body, so the body wins and the update lands on whichever task it names.
        # Measured: `POST /tasks/950 {"id": 951, "title": "X"}` renames **951** and leaves
        # 950 alone. Using the path id instead writes to a different row and still answers
        # 200, so only a read-back distinguishes the two. The permission check below runs
        # on the effective id, so this is not a bypass — pointing it at somebody else's
        # task is still a 403.
        task_id = body.id or path_param_as_id(task)

        loaded = task_service.get_task(session, task_id)
        # The gate covers editing an existing task too, not only creating one — measured,
        # `POST /tasks/{id}` on a task in an archived project is 412/3008.
        _refuse_if_archived(session, loaded.project_id)
        if not task_permissions.can_update(session, user_id, task_id):
            raise ForbiddenError()

        # Moving a task needs write on the destination too, or a user could push tasks
        # into a project they cannot otherwise touch.
        moving = bool(body.project_id) and body.project_id != loaded.project_id
        if moving and not project_can_write(session, user_id, body.project_id):
            raise ForbiddenError()

        return task_service.update_task(session, task_id=task_id, data=body, user_id=user_id)

    @router.delete("/tasks/{task}", response_model=Message)
    def delete_task(
        request: Request,
        task: Annotated[str, Path(min_length=1)],
        session: Session = Depends(get_db),
    ) -> Response:
        user_id = _auth_user_id(request)
        task_id = path_param_as_id(task)

        loaded = task_service.get_task(session, task_id)
        # ...and deleting one. The error message says "editing or creating"; the behaviour
        # is wider than the wording, and the behaviour is the contract.
        _refuse_if_archived(session, loaded.project_id)
        if not task_permissions.can_delete(session, user_id, task_id):
            raise ForbiddenError()

        task_service.delete_task(session, task_id=task_id)
        return deleted_response()

    return router


def _refuse_if_archived(session: Session, project_id: int) -> None:
    """412/3008 when the task's project reads as archived — **before** the permission check.

    ⚠️ The ordering is measured, and it is not the intuitive one. Upstream answers
    412/3008 to a caller who holds **nothing at all** on an archived project, where a live
    project would give that same caller 403. So the archived gate sits *ahead* of
    permission, not behind it:

        project missing            -> 404 / 3001
        project archived           -> 412 / 3008   (even with no access, even with no title)
        no write permission        -> 403 / 0
        empty title                -> 400 / 4001

    Putting it after the permission check is the natural reading and gets two cells wrong:
    a stranger sees 403 instead of 412, and an archived project with an empty title
    reports the title rather than the archive.

    ⚠️ **Archived is the inherited value, not the column** — a task cannot be created
    under a project whose *parent* is archived either. Measured on seed project 21, whose
    own ``is_archived`` is 0 under archived parent 22.

    ⚠️ This discloses "archived" to a caller with no access, which upstream does and we
    copy. It is the same disclosure family as its 404-vs-403 split, already registered.

    Matrix in ``harness/probe_coder_e_archived_task_gate.py``.
    """
    project = session.get(Project, project_id)
    if project is None:
        return
    if project_service.reads_as_archived(session, project):
        raise CaltonError.from_name("models.ErrProjectIsArchived")


def _project_missing(session: Session, project_id: int) -> bool:
    return session.scalars(select(Project).where(Project.id == project_id)).one_or_none() is None


#: (method, path) for everything this module registers, so route_registry and the app can
#: never disagree about which routes exist. Paths are the /api/v1-prefixed templates the
#: registry expects — it derives group names from the literal segments.
REGISTERED_ROUTES = (
    ("GET", "/api/v1/tasks"),
    # Calton-only alias. group_name_of() folds "tasks_all" onto "tasks" exactly as
    # upstream's table does, so it needs no special case and shares tasks.read_all with
    # the canonical path — a token granted tasks.read_all reaches both.
    ("GET", "/api/v1/tasks/all"),
    ("GET", "/api/v1/projects/{project}/tasks"),
    ("GET", "/api/v1/projects/{project}/views/{view}/tasks"),
    ("PUT", "/api/v1/projects/{project}/tasks"),
    ("GET", "/api/v1/tasks/{task}"),
    # Measured on `GET /routes`: group "tasks", action "update_bulk" — the path has no
    # parameter, so the registry builds "tasks_bulk" and GROUP_RENAMES folds it back onto
    # "tasks". A token granted tasks.update_bulk reaches it; one granted tasks.update
    # does not.
    ("POST", "/api/v1/tasks/bulk"),
    ("POST", "/api/v1/tasks/{task}"),
    ("DELETE", "/api/v1/tasks/{task}"),
    ("GET", "/api/v1/projects/{project}/tasks/by-index/{index}"),
)
