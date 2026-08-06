"""Admin panel routes.

Eight endpoints under ``/admin/*``. Every route is gated by ``user.is_admin``: a
non-admin caller gets a 403 before the handler runs. The prefix
``/api/v1/admin/`` is on the JWT-only list (``auth.deps.JWT_ONLY_PREFIXES``), so an
API token can never reach these — only a real admin session can.

User deletion is a soft delete (status -> disabled); the last admin cannot be
removed or demoted, guarding against locking the instance out.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from calton.auth.deps import auth_user_id
from calton.core.crud_router import path_param_as_id
from calton.core.errors import CaltonError
from calton.core.pagination import Paginator, paginated_response
from calton.db.session import get_db
from calton.models.user import User
from calton.schemas.user_settings import (
    AdminIsAdminPatch,
    AdminOverview,
    AdminProjectRead,
    AdminStatusPatch,
    AdminUserCreate,
    AdminUserRead,
    OwnerPatch,
)
from calton.services import admin_service

__all__ = ["REGISTERED_ROUTES", "build_router"]


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/admin/overview", response_model=AdminOverview)
    def overview(
        request: Request,
        db: Annotated[Session, Depends(get_db)],
    ) -> Response:
        _require_admin(request, db)
        return JSONResponse(content=admin_service.overview(db))

    @router.get("/admin/projects")
    def list_projects(
        request: Request,
        db: Annotated[Session, Depends(get_db)],
        paginator: Paginator = Depends(),
        s: Annotated[str | None, Query()] = None,
    ) -> Response:
        _require_admin(request, db)
        rows, total = admin_service.list_projects(
            db, search=s, offset=paginator.offset, limit=paginator.limit
        )
        items = [_project_view(r) for r in rows]
        return paginated_response(items, total_items=total, per_page=paginator.per_page)

    @router.patch("/admin/projects/{id}/owner", response_model=AdminProjectRead)
    def reassign_owner(
        request: Request,
        id: Annotated[str, Path(min_length=1)],
        body: OwnerPatch,
        db: Annotated[Session, Depends(get_db)],
    ) -> Response:
        _require_admin(request, db)
        project = admin_service.reassign_project(db, path_param_as_id(id), body.owner_id)
        db.commit()
        return JSONResponse(content=_project_view(project))

    @router.get("/admin/users")
    def list_users(
        request: Request,
        db: Annotated[Session, Depends(get_db)],
        paginator: Paginator = Depends(),
        s: Annotated[str | None, Query()] = None,
    ) -> Response:
        _require_admin(request, db)
        rows, total = admin_service.list_users(
            db, search=s, offset=paginator.offset, limit=paginator.limit
        )
        items = [_user_view(r) for r in rows]
        return paginated_response(items, total_items=total, per_page=paginator.per_page)

    @router.post("/admin/users", response_model=AdminUserRead)
    def create_user(
        request: Request,
        body: AdminUserCreate,
        db: Annotated[Session, Depends(get_db)],
    ) -> Response:
        _require_admin(request, db)
        user = admin_service.create_user(
            db,
            username=body.username,
            email=body.email,
            password=body.password,
            name=body.name,
            is_admin=body.is_admin,
        )
        db.commit()
        return JSONResponse(status_code=200, content=_user_view(user))

    @router.delete("/admin/users/{id}")
    def delete_user(
        request: Request,
        id: Annotated[str, Path(min_length=1)],
        db: Annotated[Session, Depends(get_db)],
    ) -> Response:
        _require_admin(request, db)
        admin_service.delete_user(db, path_param_as_id(id))
        db.commit()
        # 204, matching upstream's documented response.
        return Response(status_code=204)

    @router.patch("/admin/users/{id}/admin", response_model=AdminUserRead)
    def set_admin(
        request: Request,
        id: Annotated[str, Path(min_length=1)],
        body: AdminIsAdminPatch,
        db: Annotated[Session, Depends(get_db)],
    ) -> Response:
        _require_admin(request, db)
        user = admin_service.set_admin(db, path_param_as_id(id), body.is_admin)
        db.commit()
        return JSONResponse(content=_user_view(user))

    @router.patch("/admin/users/{id}/status", response_model=AdminUserRead)
    def set_status(
        request: Request,
        id: Annotated[str, Path(min_length=1)],
        body: AdminStatusPatch,
        db: Annotated[Session, Depends(get_db)],
    ) -> Response:
        _require_admin(request, db)
        user = admin_service.set_status(db, path_param_as_id(id), body.status)
        db.commit()
        return JSONResponse(content=_user_view(user))

    return router


def _require_admin(request: Request, db: Session) -> None:
    """The single admin gate. A non-admin gets a 403/1 (``models.ErrGenericForbidden``)
    before any handler logic runs, so the count queries and mutations are never
    reached by an unauthorised caller."""
    user_id = auth_user_id(request)
    user = db.get(User, user_id)
    if user is None or not user.is_admin:
        raise CaltonError.from_name("models.ErrGenericForbidden")


def _project_view(project) -> dict:  # type: ignore  # type: ignore
    from calton.db.types import format_rfc3339

    return {
        "id": project.id,
        "title": project.title,
        "identifier": project.identifier,
        "owner_id": project.owner_id,
        "is_archived": bool(project.is_archived),
        "created": format_rfc3339(project.created),
        "updated": format_rfc3339(project.updated),
    }


def _user_view(user) -> dict:  # type: ignore  # type: ignore
    from calton.db.types import format_rfc3339

    return {
        "id": user.id,
        "name": user.name or "",
        "username": user.username,
        "email": user.email or "",
        "status": user.status or 0,
        "is_admin": bool(user.is_admin),
        "created": format_rfc3339(user.created),
        "updated": format_rfc3339(user.updated),
    }


# Not registered: the prefix is JWT-only (see auth.deps.JWT_ONLY_PREFIXES), so an
# API token can never reach these routes. A registry entry would offer a grant
# that authorises nothing.
REGISTERED_ROUTES: tuple[tuple[str, str], ...] = ()
