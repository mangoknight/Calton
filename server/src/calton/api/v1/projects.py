"""Project endpoints.

Five of the six routes come from :class:`~calton.core.crud_router.CRUDRouter`, so the
inverted verbs (PUT creates, POST updates), the ``x-max-permission`` header and the delete
message body are all inherited rather than re-spelled. ``projectusers`` is hand-written
because it is not one of the five standard operations.

**The collection and the item serialise the same project differently**, and both shapes
are measured:

* ``views`` is ``null`` on the collection and ``[]`` on an item with no views
* ``max_permission`` is ``null`` on the collection and ``0`` on an item — *always* ``0``,
  even for the owner, whose real permission travels in ``x-max-permission`` as ``2``

Filling ``max_permission`` in with the caller's actual permission is the obvious "fix" and
would diverge on every single project response. See ``schemas.project``.

⚠️ **Known gap: pseudo project bodies.** ``GET /projects/-1`` serves a synthetic Favorites
project upstream, and ``GET /projects/-2`` serves a saved filter; both are 403 here.
Favorites needs the favorites table assembled into a project and saved filters belong to
T29, so neither is invented here. The list endpoint *does* already append saved filters,
because that shape is visible on every ``GET /projects`` call and leaving it out would
change the response for every user who has one. ``test_projects_api`` pins the current
403s so the gap is a recorded difference rather than a silent one.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request
from sqlalchemy.orm import Session

from calton.core.crud_router import CRUDRouter, path_param_as_id
from calton.core.errors import EchoStringError, UnauthorizedError
from calton.db.session import get_db
from calton.db.types import ZERO_TIME
from calton.models.project import Project
from calton.permissions import project as project_permissions
from calton.schemas.message import Message
from calton.schemas.project import ProjectOwner, ProjectRead, ProjectUserRead, ProjectWrite
from calton.schemas.project_view import ProjectViewRead, view_read
from calton.services import project_crud, project_service
from calton.services.project_crud import (
    ARCHIVED_ATTR,
    SUBJECT_OWNER_ATTR,
    ProjectPolicy,
    ProjectService,
)

#: Upstream's own 403 body on this route carries **no code at all** — not even ``code: 0``.
#: Measured: ``GET /projects/900/projectusers`` as a user with no grant answers exactly
#: ``{"message": "Forbidden"}``. Every other 403 in the API has a code, so an
#: implementation that fills one in "for consistency" adds a key upstream does not send,
#: and a test asserting only the status never sees it.
PROJECTUSERS_FORBIDDEN = "Forbidden"


def _owner_of(session: Session, project: Project) -> ProjectOwner | None:
    """The embedded ``owner``.

    ⚠️ ``created``/``updated`` are Go's **zero time** on the paths where upstream assigns
    ``project.Owner = a.(*user.User)`` instead of reading the users row — the create
    response, the Favorites pseudo project and a saved filter's pseudo project — and then
    only when the caller authenticated with a **JWT**, because a JWT subject is built from
    claims that carry no timestamps. An API token subject is loaded from the table, so
    every cell of that 2x2 except those two is the real value; the measurements are in
    ``auth.deps.AuthSubject`` and ``harness/probe_coder_e_owner.py``.

    ``project_crud`` decides this, at the three sites that do the assigning, and leaves
    the answer on the object. Deciding it here instead would mean re-identifying those
    paths from a project's id, which works for the two negative ids and not at all for the
    create response.
    """
    from calton.models.user import User

    user = session.get(User, project.owner_id)
    if user is None:
        return None
    zeroed = bool(getattr(project, SUBJECT_OWNER_ATTR, False))
    return ProjectOwner(
        id=user.id,
        name=user.name or "",
        username=user.username or "",
        created=ZERO_TIME if zeroed else user.created,
        updated=ZERO_TIME if zeroed else user.updated,
    )


def _reads_as_archived(session: Session, project: Project) -> bool:
    """``is_archived`` as upstream reports it — the project's own flag OR an ancestor's.

    ⚠️ Not ``bool(project.is_archived)``. Seed project 21 stores 0 and sits under archived
    22; upstream answers ``true``, hides it from ``GET /projects``, and refuses to create
    under it. Measured in ``harness/probe_coder_e_archived.py``.

    Reads a value ``project_crud`` may have precomputed for the whole page — the marker is
    an optimisation, never the source of truth, so nothing here depends on remembering to
    set it.
    """
    marked = getattr(project, ARCHIVED_ATTR, None)
    if marked is not None:
        return bool(marked)
    return project_service.reads_as_archived(session, project)


def _project_read(session: Session, project: Project, *, in_collection: bool) -> ProjectRead:
    """Serialise a project for either shape.

    ``in_collection`` is the whole difference between the two, and it is not cosmetic:
    on the collection both ``views`` and ``max_permission`` are ``null``, on an item they
    are ``[]``/``0``. Serialising one shape for both makes half the project parity cases
    fail on a key that is present-but-null versus absent-but-empty.
    """
    stored = _sorted_views(session, project)
    # The collection sends null for a project with no views; the item sends []. A project
    # that *has* views carries them on both.
    views: list[ProjectViewRead] | None = None
    if stored or not in_collection:
        views = [view_read(view) for view in stored]

    return ProjectRead(
        id=project.id,
        title=project.title,
        description=project.description or "",
        identifier=project.identifier or "",
        hex_color=project.hex_color or "",
        parent_project_id=project.parent_project_id or 0,
        owner=_owner_of(session, project),
        # Inherited, not just the column: a project under an archived parent reports
        # `true` even though its own flag is 0. `project_crud` computes it for a whole
        # page in one query and leaves it here; the fallback recomputes for a single
        # project so a path that forgets to mark is slow rather than wrong.
        is_archived=_reads_as_archived(session, project),
        # `projects` has no is_favorite column — favourites are their own table — so a
        # real project's value is not implemented and stays False. A saved-filter pseudo
        # project *does* carry one, set on the transient object by
        # project_crud.pseudo_project_from_filter, and it is read here so the collection
        # and the item cannot disagree about it.
        is_favorite=bool(getattr(project, "is_favorite", False)),
        background_information=None,
        background_blur_hash=project.background_blur_hash or "",
        position=project.position or 0,
        views=views,
        # Never the caller's permission — see the module docstring.
        max_permission=None if in_collection else 0,
        created=project.created,
        updated=project.updated,
    )


def _sorted_views(session: Session, project: Project) -> list[Any]:
    from sqlalchemy import select

    from calton.models.project_view import ProjectView

    # The Favorites pseudo project (-1) carries its three views on the object: they are
    # constants in `project.go`, not rows, so the query below would find nothing and the
    # body would go out with `views: []`. Saved-filter pseudo projects are the opposite
    # case — their views *are* rows, created alongside the filter — so this is a check for
    # "were views supplied", not "is this a pseudo project".
    supplied = getattr(project, "synthetic_views", None)
    if supplied is not None:
        return list(supplied)

    return list(
        session.scalars(
            select(ProjectView)
            .where(ProjectView.project_id == project.id)
            .order_by(ProjectView.position.asc(), ProjectView.id.asc())
        )
    )


def _auth_user_id(request: Request) -> int:
    """The authenticated user's id, or the middleware's 401.

    Never a fallback to some default user: that would make every project route below
    publicly writable the moment the auth middleware is missing.
    """
    auth = getattr(request.state, "auth", None)
    user_id = getattr(auth, "id", None)
    if not isinstance(user_id, int):
        raise UnauthorizedError()
    return user_id


def _read_all_params(request: Request) -> dict[str, Any]:
    """``?is_archived=true`` includes archived projects; anything else hides them."""
    return {"is_archived": request.query_params.get("is_archived", "") == "true"}


def build_router() -> APIRouter:
    router = APIRouter()

    def serialize(project: Project, session: Session, in_collection: bool) -> Any:
        return _project_read(session, project, in_collection=in_collection).model_dump(mode="json")

    crud = CRUDRouter(
        prefix="/projects",
        item_param="project",
        service=ProjectService(),
        policy=ProjectPolicy(),
        read_schema=ProjectRead,
        write_schema=ProjectWrite,
        serialize=serialize,
        read_all_params=_read_all_params,
    )
    router.include_router(crud.router)

    @router.get("/projects/{project}/projectusers", response_model=list[ProjectUserRead])
    def project_users(
        request: Request,
        # Declared so the operation documents its path parameter — see
        # core.crud_router.path_parameter_block. `str`, never `int`.
        project: Annotated[str, Path(min_length=1)],
        session: Session = Depends(get_db),
    ) -> Any:
        user_id = _auth_user_id(request)
        project_id = path_param_as_id(project)

        # Existence is checked first here, unlike the write paths: a missing project is
        # 404/3001 and an unreadable one is the bare-message 403. Measured both ways.
        project_crud.load_project(session, project_id)

        allowed, _ = project_permissions.can_read(session, user_id, project_id)
        if not allowed:
            raise EchoStringError(403, PROJECTUSERS_FORBIDDEN)

        return [
            ProjectUserRead.model_validate(user, from_attributes=True)
            for user in project_crud.users_with_access(session, project_id)
        ]

    return router


#: (method, path) for everything this module registers, so route_registry and the app can
#: never disagree about which routes exist.
REGISTERED_ROUTES = (
    ("GET", "/api/v1/projects"),
    ("PUT", "/api/v1/projects"),
    ("GET", "/api/v1/projects/{project}"),
    ("POST", "/api/v1/projects/{project}"),
    # No PATCH: CRUDRouter does not register one, because upstream answers 405 here.
    # See the comment at the missing route in core.crud_router.
    ("DELETE", "/api/v1/projects/{project}"),
    ("GET", "/api/v1/projects/{project}/projectusers"),
)

__all__ = ["REGISTERED_ROUTES", "Message", "build_router"]
