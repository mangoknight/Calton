"""The six sharing routes: three creates + three GETs (§8 siblings).

The three creates — `PUT /projects/{id}/{users,teams,shares}` — are what a real
MCP client recorded. The three GET siblings (§8) close the "can write, can read
back" loop the opens only the writes had left unguarded.

⚠️ The link-share route is the one that used to be unreachable in the parity
harness: the old ``SHARED_ENV`` forced ``service.enablelinksharing`` off, so it
answered 404 and read as "upstream does not have this". The harness now runs
upstream's defaults, which is what made this group implementable at all.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from calton.core.crud_router import path_param_as_id
from calton.core.pagination import Paginator, paginated_response
from calton.core.pseudo_users import resolve_subject
from calton.db.session import get_db
from calton.db.types import ZERO_TIME
from calton.models import LinkShare, ProjectUser, Team, TeamProject, User
from calton.schemas.sharing import (
    LinkShareCreated,
    LinkShareWrite,
    ProjectTeamCreated,
    ProjectTeamWrite,
    ProjectUserCreated,
    ProjectUserWrite,
    UserWithPermission,
)
from calton.schemas.team import TeamWithPermission
from calton.schemas.user import UserRead
from calton.services import sharing_service
from calton.services.team_members import team_view
from calton.services.team_service import user_id_of


def build_router() -> APIRouter:
    router = APIRouter()

    @router.put("/projects/{project}/users", status_code=201, response_model=ProjectUserCreated)
    def share_with_user(
        request: Request,
        # `str`, never `int` — an int annotation answers 422 where upstream answers 400.
        project: Annotated[str, Path(min_length=1)],
        body: ProjectUserWrite,
        session: Session = Depends(get_db),
    ) -> Response:
        """Grant a user access by **username**.

        The 201 carries the relation row's id and timestamps — *not* the user's, which is
        what the sibling GET returns under the same key names. See ``schemas/sharing``.
        """
        user_id = user_id_of(getattr(request.state, "auth", None))
        project_id = path_param_as_id(project)

        grant = sharing_service.grant_to_user(session, user_id, project_id=project_id, body=body)
        payload = ProjectUserCreated(
            id=grant.id,
            username=body.username,
            permission=grant.permission,
            created=grant.created,
            updated=grant.updated,
        ).model_dump(mode="json")
        # `get_db` closes the session without committing.
        session.commit()
        return JSONResponse(status_code=201, content=payload)

    @router.put("/projects/{project}/teams", status_code=201, response_model=ProjectTeamCreated)
    def share_with_team(
        request: Request,
        project: Annotated[str, Path(min_length=1)],
        body: ProjectTeamWrite,
        session: Session = Depends(get_db),
    ) -> Response:
        """Grant a team access. The body's ``right`` key, if any, is ignored."""
        user_id = user_id_of(getattr(request.state, "auth", None))
        project_id = path_param_as_id(project)

        grant = sharing_service.grant_to_team(session, user_id, project_id=project_id, body=body)
        payload = ProjectTeamCreated(
            id=grant.id,
            team_id=grant.team_id,
            permission=grant.permission,
            created=grant.created,
            updated=grant.updated,
        ).model_dump(mode="json")
        session.commit()
        return JSONResponse(status_code=201, content=payload)

    @router.put("/projects/{project}/shares", status_code=201, response_model=LinkShareCreated)
    def create_link_share(
        request: Request,
        project: Annotated[str, Path(min_length=1)],
        body: LinkShareWrite,
        session: Session = Depends(get_db),
    ) -> Response:
        """Create a share link.

        ``shared_by`` echoes the authenticated subject with **zero timestamps** — the same
        create-response shape teams uses, where the body describes the request rather than
        the stored row. ``password`` comes back empty whatever was sent.
        """
        user_id = user_id_of(getattr(request.state, "auth", None))
        project_id = path_param_as_id(project)

        share = sharing_service.create_link_share(
            session, user_id, project_id=project_id, body=body
        )
        creator = session.get(User, user_id)
        zeroed = bool(getattr(getattr(request.state, "auth", None), "timestamps_are_zero", False))
        payload = LinkShareCreated(
            id=share.id,
            hash=share.hash,
            name=share.name or "",
            password="",
            permission=share.permission,
            sharing_type=share.sharing_type,
            # ⚠️ The embedded subject's timestamps depend on **how the caller
            # authenticated**, not on the row. A JWT subject is built from claims, which
            # carry no timestamps, so it serialises zeros; an API-token subject is loaded
            # from the database and carries real ones. Measured on both credentials.
            #
            # This is the same shape coder-e found on `PUT /projects`'s `owner`, and the
            # mechanism it built for that is reused here rather than rebuilt:
            # `AuthSubject.timestamps_are_zero` is named for the observable consequence
            # instead of for `credential == "jwt"`.
            #
            # ⚠️ It is invisible under an API token — both a "read the row" and an "echo
            # the subject" implementation answer the same thing. Only a JWT case can tell
            # them apart, so a suite that authenticates with tokens alone would certify
            # either one.
            shared_by=(
                UserRead(
                    id=creator.id,
                    name=creator.name or "",
                    username=creator.username or "",
                    created=ZERO_TIME if zeroed else creator.created,
                    updated=ZERO_TIME if zeroed else creator.updated,
                )
                if creator is not None
                else None
            ),
            created=share.created,
            updated=share.updated,
        ).model_dump(mode="json")
        session.commit()
        return JSONResponse(status_code=201, content=payload)

    @router.get("/projects/{project}/users", response_model=list[UserWithPermission])
    def list_users(
        request: Request,
        project: Annotated[str, Path(min_length=1)],
        paginator: Paginator = Depends(),
        session: Session = Depends(get_db),
    ) -> JSONResponse:
        """The user grants on a project. Read on the project is required; refusal
        is **403/3004** (``ErrNeedToHaveProjectReadAccess``), not the 403/0 PUT
        refused with.

        The wire shape echoes the **user**, not the relation row — ``id`` is the
        user id, ``created``/``updated`` are the user's, with ``permission``
        carried alongside. This is the complement of PUT's relation-row-shaped
        response, and the pair is the contract.
        """
        user_id = user_id_of(getattr(request.state, "auth", None))
        project_id = path_param_as_id(project)
        grants = sharing_service.list_users_for_project(session, user_id, project_id=project_id)
        body = _serialise_user_grants(session, grants)
        return paginated_response(body, total_items=len(body), per_page=paginator.per_page)

    @router.get("/projects/{project}/teams", response_model=list[TeamWithPermission])
    def list_team_grants(
        request: Request,
        project: Annotated[str, Path(min_length=1)],
        paginator: Paginator = Depends(),
        session: Session = Depends(get_db),
    ) -> JSONResponse:
        """The team grants on a project, with each team hydrated as a full
        ``TeamRead`` row (``created_by``, ``members``, ...). Same permission
        shape as ``GET /projects/{id}/users`` — read required, 403/3004 on refusal.
        """
        user_id = user_id_of(getattr(request.state, "auth", None))
        project_id = path_param_as_id(project)
        grants = sharing_service.list_teams_for_project(session, user_id, project_id=project_id)
        body = _serialise_team_grants(session, grants)
        return paginated_response(body, total_items=len(body), per_page=paginator.per_page)

    @router.get("/projects/{project}/shares", response_model=list[LinkShareCreated])
    def list_link_shares(
        request: Request,
        project: Annotated[str, Path(min_length=1)],
        paginator: Paginator = Depends(),
        session: Session = Depends(get_db),
    ) -> JSONResponse:
        """The link shares on a project. **Admin** is required (distinct from
        the read requirement of the two halves above) and refusal is the
        403 code 1 body, not 3004.
        """
        user_id = user_id_of(getattr(request.state, "auth", None))
        project_id = path_param_as_id(project)
        shares = sharing_service.list_link_shares(session, user_id, project_id=project_id)
        body = _serialise_link_shares(session, shares)
        return paginated_response(body, total_items=len(body), per_page=paginator.per_page)

    return router


# `grants: list` is intentionally typed — these three hold typed rows from the
# subject-resolver-friendly prefetch path, but mypy cannot follow the loop body
# without the type annotation, and `Any` is a second-rule violation. The actual
# element types come from `list_users_for_project` etc.
def _serialise_user_grants(session: Session, grants: list[ProjectUser]) -> list[dict[str, Any]]:
    """``GET /projects/{id}/users`` — each grant with the **user**'s fields
    (id/name/username/created/updated) plus the grant's ``permission``.
    """
    user_ids = [g.user_id for g in grants]
    users: dict[int, User] = (
        {u.id: u for u in session.scalars(select(User).where(User.id.in_(user_ids)))}
        if user_ids
        else {}
    )
    body: list[dict[str, Any]] = []
    for g in grants:
        u: User | None = users.get(g.user_id)
        body.append(
            {
                "id": g.user_id,
                "name": (u.name or "") if u is not None else "",
                "username": (u.username or "") if u is not None else "",
                "created": _ts(u.created if u is not None else ZERO_TIME),
                "updated": _ts(u.updated if u is not None else ZERO_TIME),
                "permission": g.permission,
            }
        )
    return body


def _serialise_team_grants(session: Session, grants: list[TeamProject]) -> list[dict[str, Any]]:
    """``GET /projects/{id}/teams`` — each grant as a full ``TeamRead`` row
    (with hydrated ``created_by`` and ``members``) plus the grant's ``permission``.
    """
    body: list[dict[str, Any]] = []
    for g in grants:
        team = session.get(Team, g.team_id)
        if team is None:
            continue  # Defensive: orphan team_project row. Should not happen.
        view = team_view(session, team).model_dump(mode="json")
        view["permission"] = g.permission
        body.append(view)
    return body


def _serialise_link_shares(session: Session, shares: list[LinkShare]) -> list[dict[str, Any]]:
    """``GET /projects/{id}/shares`` — each share with the same shape the PUT
    route answered with, except ``shared_by`` is loaded from the row here (the
    read path uses the database id, not the authenticated subject); the JWT-vs-
    timestamp distinction is irrelevant for an existing-link read.
    """
    body: list[dict[str, Any]] = []
    for share in shares:
        body.append(
            {
                "id": share.id,
                "hash": share.hash,
                "name": share.name or "",
                "permission": share.permission,
                "sharing_type": share.sharing_type,
                "password": "",
                "shared_by": _dump_user(resolve_subject(session, share.shared_by_id)),
                "created": _ts(share.created),
                "updated": _ts(share.updated),
            }
        )
    return body


def _ts(value: datetime | str | None) -> str:
    """Render a datetime as the RFC3339 wire form Go uses (UTC, trailing Z)."""
    if value is None:
        return "0001-01-01T00:00:00Z"
    if isinstance(value, str):
        return value
    return value.isoformat().replace("+00:00", "Z") if value.tzinfo else value.isoformat() + "Z"


def _dump_user(user: Any) -> dict[str, Any] | None:
    if user is None:
        return None
    return {
        "id": user.id,
        "name": user.name,
        "username": user.username,
        "created": _ts(user.created),
        "updated": _ts(user.updated),
    }


#: The six routes registered. The three creates file under ``projects_users`` /
#: ``projects_teams`` / ``projects_shares`` as the ``create`` action, and the three
#: reads as ``read_all`` (per isStandardCRUDRoute's GET-on-collection mapping).
#: ``projects_teams`` / ``projects_shares`` as the ``create`` action, and the three
#: reads as ``read_all`` (per isStandardCRUDRoute's GET-on-collection mapping).
#: Verified against the reference server's own ``GET /routes``.
REGISTERED_ROUTES = (
    ("PUT", "/api/v1/projects/{project}/users"),
    ("GET", "/api/v1/projects/{project}/users"),
    ("PUT", "/api/v1/projects/{project}/teams"),
    ("GET", "/api/v1/projects/{project}/teams"),
    ("PUT", "/api/v1/projects/{project}/shares"),
    ("GET", "/api/v1/projects/{project}/shares"),
)
