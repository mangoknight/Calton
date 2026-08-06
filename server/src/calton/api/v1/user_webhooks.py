"""User-level webhook routes.

The five endpoints under ``/user/settings/webhooks``. These reuse the ``Webhook``
model with ``project_id == 0`` and ``user_id`` set, scoped to the caller's own
``user_id``. Project webhooks live in ``api/v1/webhooks.py``; the two halves share
a table but not a path or a permission group.

The events catalogue route returns the same names project webhooks do, so a
subscriber cannot register for a name nothing will ever fire — the single source
is ``events.catalogue.WEBHOOK_EVENTS``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from calton.auth.deps import auth_user_id
from calton.core.crud_router import path_param_as_id
from calton.db.session import get_db
from calton.schemas.webhook import WebhookRead, WebhookWrite
from calton.services import user_webhook_service

__all__ = ["REGISTERED_ROUTES", "build_router"]


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/user/settings/webhooks", response_model=list[WebhookRead])
    def list_webhooks(
        request: Request,
        db: Annotated[Session, Depends(get_db)],
    ) -> Response:
        user_id = auth_user_id(request)
        hooks = user_webhook_service.list_user_webhooks(db, user_id)
        return JSONResponse(content=[_view(h) for h in hooks])

    @router.put("/user/settings/webhooks", response_model=WebhookRead)
    def create_webhook(
        request: Request,
        body: WebhookWrite,
        db: Annotated[Session, Depends(get_db)],
    ) -> Response:
        user_id = auth_user_id(request)
        hook = user_webhook_service.create_user_webhook(
            db,
            user_id,
            target_url=body.target_url,
            events=body.events,
            secret=body.secret,
            basic_auth_user=body.basic_auth_user,
            basic_auth_password=body.basic_auth_password,
        )
        db.commit()
        return JSONResponse(status_code=200, content=_view(hook))

    @router.get("/user/settings/webhooks/events", response_model=list[str])
    def list_events(request: Request) -> Response:
        """The catalogue of user-level webhook events. Authenticated but otherwise
        ungated — the same list for everyone, served as a bare JSON array of strings."""
        auth_user_id(request)
        return JSONResponse(content=list(user_webhook_service.AVAILABLE_EVENTS))

    @router.post("/user/settings/webhooks/{id}", response_model=WebhookRead)
    def update_webhook(
        request: Request,
        id: Annotated[str, Path(min_length=1)],
        body: WebhookWrite,
        db: Annotated[Session, Depends(get_db)],
    ) -> Response:
        user_id = auth_user_id(request)
        webhook_id = path_param_as_id(id)
        hook = user_webhook_service.update_user_webhook(
            db, user_id, webhook_id, target_url=body.target_url, events=body.events
        )
        db.commit()
        return JSONResponse(content=_view(hook))

    @router.delete("/user/settings/webhooks/{id}")
    def delete_webhook(
        request: Request,
        id: Annotated[str, Path(min_length=1)],
        db: Annotated[Session, Depends(get_db)],
    ) -> Response:
        user_id = auth_user_id(request)
        user_webhook_service.delete_user_webhook(db, user_id, path_param_as_id(id))
        db.commit()
        return JSONResponse(status_code=200, content={"message": "Webhook deleted."})

    return router


def _view(hook) -> dict:  # type: ignore  # type: ignore
    """The same shape ``WebhookRead`` produces, with the write-only fields masked
    to ``""`` — the secret and basic-auth pair are never readable back, even on the
    create that just accepted them."""
    import json

    from calton.db.types import format_rfc3339

    return {
        "id": hook.id,
        "target_url": hook.target_url,
        "events": json.loads(hook.events) if hook.events else [],
        "project_id": hook.project_id or 0,
        "user_id": hook.user_id or 0,
        "secret": "",
        "basic_auth_user": "",
        "basic_auth_password": "",
        "created_by": None,
        "created": format_rfc3339(hook.created),
        "updated": format_rfc3339(hook.updated),
    }


# Registered so an API token granting the matching permission group can reach them.
REGISTERED_ROUTES = (
    ("GET", "/api/v1/user/settings/webhooks"),
    ("PUT", "/api/v1/user/settings/webhooks"),
    ("GET", "/api/v1/user/settings/webhooks/events"),
    ("POST", "/api/v1/user/settings/webhooks/{id}"),
    ("DELETE", "/api/v1/user/settings/webhooks/{id}"),
)
