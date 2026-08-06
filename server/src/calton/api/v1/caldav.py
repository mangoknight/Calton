"""CalDAV token routes.

Three endpoints under ``/user/settings/token/caldav``. The path prefix is on the
JWT-only list (``auth.deps.JWT_ONLY_PREFIXES``), so an API token can never reach
these — only a real session/JWT can manage a user's own CalDAV tokens.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from calton.auth.deps import auth_user_id
from calton.core.crud_router import path_param_as_id
from calton.db.session import get_db
from calton.schemas.user_settings import CalDAVTokenCreated, CalDAVTokenRead
from calton.services import user_settings_service

__all__ = ["REGISTERED_ROUTES", "build_router"]


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/user/settings/token/caldav", response_model=list[CalDAVTokenRead])
    def list_tokens(
        request: Request,
        db: Annotated[Session, Depends(get_db)],
    ) -> Response:
        user_id = auth_user_id(request)
        rows = user_settings_service.list_caldav_tokens(db, user_id)
        return JSONResponse(content=[_token_view(r) for r in rows])

    @router.put("/user/settings/token/caldav", response_model=CalDAVTokenCreated)
    def create_token(
        request: Request,
        db: Annotated[Session, Depends(get_db)],
    ) -> Response:
        user_id = auth_user_id(request)
        token = user_settings_service.create_caldav_token(db, user_id)
        db.commit()
        # 200, not 201 — upstream returns 200 on this PUT (it is an upsert-by-create,
        # not a REST create of a new sub-resource).
        return JSONResponse(
            status_code=200,
            content={"id": token.id, "token": token.token, "created": _ts(token.created)},
        )

    @router.delete("/user/settings/token/caldav/{id}")
    def delete_token(
        request: Request,
        id: Annotated[str, Path(min_length=1)],
        db: Annotated[Session, Depends(get_db)],
    ) -> Response:
        user_id = auth_user_id(request)
        user_settings_service.delete_caldav_token(db, user_id, path_param_as_id(id))
        db.commit()
        return JSONResponse(status_code=200, content={"message": "Token deleted."})

    return router


def _token_view(row) -> dict:  # type: ignore  # type: ignore
    return {"id": row.id, "created": _ts(row.created)}


def _ts(value) -> str:  # type: ignore
    from calton.db.types import format_rfc3339

    return format_rfc3339(value)


# Not registered: the prefix is JWT-only (see auth.deps.JWT_ONLY_PREFIXES), so an
# API token can never reach these routes and a registry entry would offer a grant
# that authorises nothing — the same reason /tokens itself is unregistered.
REGISTERED_ROUTES: tuple[tuple[str, str], ...] = ()
