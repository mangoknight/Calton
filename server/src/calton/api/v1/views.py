"""Project view endpoints.

Hand-written rather than mounted through ``CRUDRouter``, for the same reason tasks are:
the collection hangs off ``/projects/{project}/views`` and the item off
``/projects/{project}/views/{id}``, so **both** paths carry the project id — while
CRUDRouter's model is one prefix plus one item parameter and hands the policy only the
item key. What is shared with it is everything that is a wire contract: the inverted verbs,
``read_one_response``'s header and ``deleted_response``'s body.

**The permission ladder is not uniform, and neither are the refusals.** Measured against
project 902, which perm.yml gives a subject at each level — bob has write, carol has admin,
neither owns it, and dave has nothing:

==========  ==================  ============================================
operation   needs               refusal
==========  ==================  ============================================
read_all    read on the project 403 ``{"code": 1, ...}`` — ErrGenericForbidden
read_one    read on the project 403 ``{"code": 0, "message": "You don't have
                                the permission to see this"}``
create      **admin**           403 ``{"code": 0, "message": "Forbidden"}``
update      **admin**           same
delete      **admin**           same
==========  ==================  ============================================

Two things there are easy to get wrong. Writing a view needs **admin**, not write — bob,
with write access on 902, is refused all three writes while carol, with admin, is allowed
all three. That is the opposite of the project endpoints, where write is enough to update.
And ``read_all`` and ``read_one`` refuse with **different bodies**: one carries code 1 and
the other code 0 with the read-specific wording. A single shared "forbidden" helper
produces one of them everywhere and passes any test that only checks the status.

⚠️ **The order of the checks is not the same on every route**, and it decides which code a
request carrying two problems gets. All measured:

* A **non-numeric path segment** is 400/2004 and beats everything, including a body that
  would not validate. That is why the ids are parsed in a dependency: inside the handler
  they would be parsed *after* the body, and ``POST /projects/902/views/abc`` with an
  empty body would answer 412 where upstream answers 400.
* On every write route the **body is bound and validated first** — before existence and
  before permission. ``POST`` to a view that does not exist, as a subject with no access
  at all, with no title, is **412**.
* ``read_all`` and ``read_one`` check the **project** first: a missing project is
  404/3001, and that is what an unauthorised caller gets too.
* ``update`` and ``delete`` do **not** check the project at all. They resolve the view
  under the path project and answer 404/**3014** when there is no such pair — so
  ``POST /projects/999999/views/1`` is 3014, not the 3001 the read routes give. Only then
  is admin checked, which means a caller with no access can tell an existing view from a
  missing one. Reproduced rather than corrected.

⚠️ **The body overrides the path.** Echo binds path parameters onto the struct and then
unmarshals the body over the top. Measured: ``POST /projects/{p}/views/{a}`` with
``{"id": b}`` updates view *b* and leaves *a* alone; ``PUT /projects/950/views`` with
``{"project_id": 903}`` is refused against project 903 even though the caller owns 950;
and ``DELETE`` does it too, because echo binds a body whenever one is present regardless of
the verb. Making the path authoritative is the safer design and would diverge on all
three. ``id`` is the one exception on create, where upstream zeroes it before inserting.

⚠️ **Pagination is accepted and ignored.** ``per_page=2`` against four views returns all
four with ``x-pagination-result-count: 4`` — but ``x-pagination-total-pages`` is computed
from ``per_page`` anyway and says 2, and ``page=2`` returns the same four. So the headers
describe a pagination the body does not perform. Measured; reproduced rather than
corrected.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from calton.core.crud_router import deleted_response, path_param_as_id, read_one_response
from calton.core.errors import CaltonError, UnauthorizedError
from calton.core.pagination import Paginator, paginated_response
from calton.core.policy import ADMIN, FORBIDDEN_READ_MESSAGE, READ, ForbiddenError
from calton.db.session import get_db
from calton.db.types import ZERO_TIME
from calton.models.project import Project
from calton.models.project_view import ProjectView
from calton.permissions import project as project_permissions
from calton.permissions import pseudo
from calton.permissions.pseudo import Favorites, SavedFilterProject, resolve
from calton.schemas.message import Message
from calton.schemas.project_view import (
    ProjectViewRead,
    ProjectViewWrite,
    kind_to_index,
    mode_to_index,
    view_read,
)
from calton.services import project_view_service


@dataclass(frozen=True)
class ViewPath:
    """The path ids, parsed before the body so a bad segment wins.

    FastAPI solves dependencies before it reports body validation errors, which is the
    only reason these are dependencies rather than three lines at the top of each
    handler: parsed inside the handler they run *after* the body, and
    ``POST /projects/{p}/views/abc`` with an empty body answers 412 where upstream
    answers 400.

    ⚠️ They are declared ``str``, and the item one is named ``id``. Both details are
    load-bearing and pull in opposite directions:

    * ``int`` makes the framework answer 422 where upstream answers 400/2004, so the
      conversion has to be ``path_param_as_id``'s rather than pydantic's.
    * Declaring them at all is what puts them in the generated OpenAPI. Reading them off
      ``request.path_params`` instead — which is what every other resource here does —
      leaves them undefined in the schema, and schemathesis then cannot build a URL for
      the route at all. Thirteen endpoints on the main line already fail that way.
    * The name has to be upstream's. Its swagger calls this parameter ``id``
      (``/projects/{project}/views/{id}``) and the contract diff compares names, so a
      parameter called ``view`` reads as one upstream does not have. The sibling route
      ``/projects/{project}/views/{view}/tasks`` spells it ``view`` because *its* swagger
      does — the name follows upstream per route, not per codebase.
    """

    project: int
    view: int | None


def collection_path(project: str) -> ViewPath:
    return ViewPath(project=path_param_as_id(project), view=None)


# `id` shadows the builtin, and has to: FastAPI matches the parameter name against the
# path template, and upstream's template is `{id}`.
def item_path(project: str, id: str) -> ViewPath:
    return ViewPath(project=path_param_as_id(project), view=path_param_as_id(id))


def _auth_user_id(request: Request) -> int:
    """The caller, or the middleware's 401. Never a silent default subject."""
    auth = getattr(request.state, "auth", None)
    user_id = getattr(auth, "id", None)
    if not isinstance(user_id, int):
        raise UnauthorizedError()
    return user_id


def _require_project(session: Session, project_id: int) -> None:
    """Establish that the project exists, whatever kind of project it is.

    ⚠️ Three kinds, three different 404s — and a raw ``session.get(Project, id)`` reports
    the wrong one for two of them, because a negative id finds no row and "not found" is
    the right answer for the wrong reason:

    ==================  ==========================================================
    id                  missing answers
    ==================  ==========================================================
    ordinary            404/3001 ``This project does not exist.``
    ``-1`` (Favorites)  never missing — it has no row and always exists
    ``-N-1`` (filter)   404/**11001** ``This saved filter does not exist.``
    ==================  ==========================================================

    ⚠️ The saved-filter case is **not** handled here even though it belongs in the table
    above. Its 11001 comes out of :func:`_can_read_project`, which every caller of this
    function runs immediately afterwards and which has to load the filter anyway to answer
    the ownership question. A load here as well passes every test — and only because that
    second path produces the identical error, so no mutation of it can go red. A line that
    cannot be shown to carry weight is left out rather than kept for symmetry.
    """
    target = resolve(project_id)
    if isinstance(target, Favorites | SavedFilterProject):
        return
    if session.get(Project, project_id) is None:
        raise CaltonError.from_name("models.ErrProjectDoesNotExist")


def _can_read_project(session: Session, user_id: int, project_id: int) -> tuple[bool, int]:
    """``(allowed, max_permission)`` for any of the three kinds of project id.

    ``permissions.project.can_read`` raises on a pseudo id by design — it guards against
    exactly this call being made without resolving first — so the two virtual kinds are
    answered here:

    * **Favorites** is readable by every authenticated caller. Its three views are the
      same constants for alice, bob and dave, so there is nothing per-user to check, and
      it reports ``x-max-permission: 0`` — **read, not admin**. Measured for two different
      users; the obvious "it is mine, so I own it" guess gives 2 and is wrong.
    * **A saved filter** is readable by its owner and reports **2**, delegated to the
      shared helper T29 also uses so the rule is not transcribed twice.
    """
    target = resolve(project_id)
    if isinstance(target, Favorites):
        return True, READ
    if isinstance(target, SavedFilterProject):
        return pseudo.can_read_saved_filter(session, user_id, target.filter_id), ADMIN
    return project_permissions.can_read(session, user_id, project_id)


def _is_admin_of_project(session: Session, user_id: int, project_id: int) -> bool:
    """Whether the caller may write a view here.

    ☠️ **Favorites refuses every write**, measured 403/code 0 on create, update and
    delete — its views are a compiled-in struct, so there is nothing to write to. A saved
    filter's views *are* rows and its owner may create, update and delete them normally.
    """
    target = resolve(project_id)
    if isinstance(target, Favorites):
        return False
    if isinstance(target, SavedFilterProject):
        return pseudo.can_read_saved_filter(session, user_id, target.filter_id)
    return project_permissions.is_admin(session, user_id, project_id)


def _load_view(session: Session, project_id: int, view_id: int) -> ProjectView:
    """The view under this project, or 404/3014.

    A view that exists but belongs to another project answers the **same** 404/3014 as one
    that does not exist, so this route does not disclose that a foreign view is real. (The
    project endpoints do disclose existence — do not generalise either way.)
    """
    view = project_view_service.load_view(session, project_id, view_id)
    if view is None:
        raise CaltonError.from_name("models.ErrProjectViewDoesNotExist")
    return view


async def delete_overrides(request: Request) -> tuple[int | None, int | None]:
    """``(project_id, id)`` out of a DELETE body — either may be ``None``.

    Echo binds a body whenever one is present, whatever the verb, so
    ``DELETE /projects/{p}/views/{a}`` carrying ``{"id": b}`` deletes *b* — measured, with
    *a* still there afterwards. It does **not** validate: a body with no title deletes
    happily, unlike create and update where the same body is 412.

    Read here rather than declared as a route parameter on purpose. A declared body would
    put a request body on DELETE in the generated OpenAPI, which upstream's swagger does
    not have, and the contract diff compares that. A ``Request``-based dependency is
    invisible to the schema and still runs before the handler.

    Only ``int`` values count. A string id is a bind failure upstream rather than a
    silently ignored key, but that is a shape no client sends and it is not reproduced
    here; anything that is not an integer is left to the path.
    """
    raw = await request.body()
    if not raw:
        return None, None
    try:
        document = json.loads(raw)
    except (TypeError, ValueError):
        return None, None
    if not isinstance(document, dict):
        return None, None
    project = document.get("project_id")
    view = document.get("id")
    return (
        project if isinstance(project, int) and not isinstance(project, bool) else None,
        view if isinstance(view, int) and not isinstance(view, bool) else None,
    )


def _effective(path: ViewPath, body: ProjectViewWrite) -> tuple[int, int | None]:
    """The project and view the request really addresses, body first.

    An explicit ``0`` counts: ``{"project_id": 0}`` is measured 404/3001 on create and
    404/3014 on update, so this cannot fall back on falsiness. ``model_fields_set`` is what
    separates "sent as zero" from "not sent".
    """
    sent = body.model_fields_set
    project_id = path.project
    if "project_id" in sent and body.project_id is not None:
        project_id = body.project_id
    view_id = path.view
    if "id" in sent and body.id is not None:
        view_id = body.id
    return project_id, view_id


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/projects/{project}/views", response_model=list[ProjectViewRead])
    def read_all(
        request: Request,
        path: ViewPath = Depends(collection_path),
        paginator: Paginator = Depends(),
        session: Session = Depends(get_db),
    ) -> Response:
        user_id = _auth_user_id(request)
        _require_project(session, path.project)

        allowed, _ = _can_read_project(session, user_id, path.project)
        if not allowed:
            # code 1, not the pipeline's code 0 — ReadAll raises models.ErrGenericForbidden
            # itself rather than being refused by the handler. read_one uses the other body.
            raise CaltonError.from_name("models.ErrGenericForbidden")

        views = project_view_service.views_of(session, path.project)
        body = [view_read(view).model_dump(mode="json") for view in views]
        # result_count and total_items are both the full count: the query is never
        # limited, so every page carries everything. See the module docstring.
        return paginated_response(
            body,
            total_items=len(views),
            per_page=paginator.per_page,
            result_count=len(views),
        )

    @router.get("/projects/{project}/views/{id}", response_model=ProjectViewRead)
    def read_one(
        request: Request,
        path: ViewPath = Depends(item_path),
        session: Session = Depends(get_db),
    ) -> Response:
        user_id = _auth_user_id(request)
        _require_project(session, path.project)

        allowed, max_permission = _can_read_project(session, user_id, path.project)
        if not allowed:
            raise ForbiddenError(FORBIDDEN_READ_MESSAGE)

        assert path.view is not None
        view = _load_view(session, path.project, path.view)
        return read_one_response(view_read(view).model_dump(mode="json"), max_permission)

    @router.put("/projects/{project}/views", status_code=201, response_model=ProjectViewRead)
    def create(
        request: Request,
        body: ProjectViewWrite,
        path: ViewPath = Depends(collection_path),
        session: Session = Depends(get_db),
    ) -> Response:
        user_id = _auth_user_id(request)
        project_id, _ = _effective(path, body)

        # Create checks the project, then admin on it — the opposite way round from
        # update/delete, which never look the project up at all.
        _require_project(session, project_id)
        if not _is_admin_of_project(session, user_id, project_id):
            raise ForbiddenError()

        view = project_view_service.create_view(
            session,
            project_id=project_id,
            title=body.title,
            view_kind=kind_to_index(body.view_kind),
            view_filter=body.filter,
            position=body.position,
            bucket_configuration_mode=mode_to_index(body.bucket_configuration_mode),
            bucket_configuration=body.bucket_configuration,
            owner_id=user_id,
        )
        session.commit()
        return JSONResponse(status_code=201, content=view_read(view).model_dump(mode="json"))

    @router.post("/projects/{project}/views/{id}", response_model=ProjectViewRead)
    def update(
        request: Request,
        body: ProjectViewWrite,
        path: ViewPath = Depends(item_path),
        session: Session = Depends(get_db),
    ) -> Response:
        user_id = _auth_user_id(request)
        project_id, view_id = _effective(path, body)
        assert view_id is not None

        # The view resolves first and its absence is 3014 even for a project that does not
        # exist; admin is only asked afterwards. Swapping these two turns every 404 on this
        # route into a 403 for callers without access.
        view = _load_view(session, project_id, view_id)
        if not _is_admin_of_project(session, user_id, project_id):
            raise ForbiddenError()

        project_view_service.update_view(
            session,
            view=view,
            title=body.title,
            view_kind=kind_to_index(body.view_kind),
            view_filter=body.filter,
            position=body.position,
            bucket_configuration_mode=mode_to_index(body.bucket_configuration_mode),
            bucket_configuration=body.bucket_configuration,
        )
        session.commit()
        # ⚠️ The response carries the **zero time** for ``created`` while the row keeps its
        # real value — measured on both sides of the same request. Upstream serialises the
        # struct it bound from the body, and ``created`` was never in it. Reading the row
        # back instead is the natural implementation and differs on every update.
        return JSONResponse(content=view_read(view, created=ZERO_TIME).model_dump(mode="json"))

    @router.delete("/projects/{project}/views/{id}", response_model=Message)
    def delete(
        request: Request,
        path: ViewPath = Depends(item_path),
        overrides: tuple[int | None, int | None] = Depends(delete_overrides),
        session: Session = Depends(get_db),
    ) -> Response:
        user_id = _auth_user_id(request)
        body_project, body_view = overrides
        project_id = body_project if body_project is not None else path.project
        view_id = body_view if body_view is not None else path.view
        assert view_id is not None

        view = _load_view(session, project_id, view_id)
        if not _is_admin_of_project(session, user_id, project_id):
            raise ForbiddenError()

        project_view_service.delete_view(session, view=view)
        session.commit()
        return deleted_response()

    return router


#: (method, path) for everything this module registers, so route_registry and the app can
#: never disagree about which routes exist.
REGISTERED_ROUTES = (
    ("GET", "/api/v1/projects/{project}/views"),
    ("PUT", "/api/v1/projects/{project}/views"),
    ("GET", "/api/v1/projects/{project}/views/{id}"),
    ("POST", "/api/v1/projects/{project}/views/{id}"),
    ("DELETE", "/api/v1/projects/{project}/views/{id}"),
)

__all__: list[str] = ["REGISTERED_ROUTES", "ViewPath", "build_router"]
