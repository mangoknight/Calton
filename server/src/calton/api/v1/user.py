"""User endpoints: registration, the current user, user search and logout.

⚠️ **`GET /users` must not be given pagination.** It is the one Phase 1 list
endpoint that does not go through the generic `WebHandler`
(pkg/routes/api/v1/user_list.go:43 calls `c.JSON` directly), so it gets neither
`read_all.go`'s nil→`[]` normalisation nor its header injection. Measured
consequences, all three of which a well-meaning "consistency" fix would break:

1. an empty result is **`null`**, not `[]`
2. **no pagination headers at all** — not `x-pagination-*`, not
   `access-control-expose-headers`; absent, not zero
3. **`page` and `per_page` are ignored** — passing them changes nothing

The frontend's `requestList` throws `ContractViolationError` when the headers are
missing, which is exactly the pressure that would tempt someone to add them here.
That belongs on the frontend's exemption list; adding headers here forks from Go.

Logout has its own surprise: it deletes the session row and clears the cookie,
but **the already-issued access token keeps working until it expires**. Upstream
does not revoke JWTs on logout, and neither do we.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session as DbSession

from calton.auth import sessions
from calton.auth.deps import CurrentSubject
from calton.db.session import get_db
from calton.models.user import User
from calton.schemas.auth import MessageResponse
from calton.schemas.user import (
    CurrentUser,
    ListedUser,
    RegisteredUser,
    RegisterRequest,
    UserSettings,
)
from calton.services import user_service

LOGOUT_MESSAGE = "Successfully logged out."


def build_router() -> APIRouter:
    router = APIRouter()

    @router.post("/register", response_model=RegisteredUser)
    def register(
        body: RegisterRequest,
        db: Annotated[DbSession, Depends(get_db)],
    ) -> RegisteredUser:
        """201 would be the conventional answer; upstream returns 200. Measured."""
        user = user_service.register_user(
            db, username=body.username, password=body.password, email=body.email
        )
        db.commit()

        return RegisteredUser(
            id=user.id,
            name=user.name or "",
            username=user.username,
            email=user.email or "",
            created=user.created,
            updated=user.updated,
        )

    @router.get("/user", response_model=CurrentUser)
    def current_user(subject: CurrentSubject) -> CurrentUser:
        return _as_current_user(subject.user)

    @router.get("/users", response_model=list[ListedUser] | None)
    def list_users(
        subject: CurrentSubject,
        db: Annotated[DbSession, Depends(get_db)],
        s: Annotated[str | None, Query()] = None,
    ) -> list[ListedUser] | None:
        """Returns ``None`` — rendered as ``null`` — when nothing matches.

        Not ``[]``. See the module docstring: this endpoint bypasses the handler
        that would otherwise normalise it. ``page``/``per_page`` are deliberately
        not declared as parameters, because upstream ignores them entirely.
        """
        found = user_service.search_users(db, s)
        if not found:
            return None

        return [
            ListedUser(
                id=user.id,
                name=user.name or "",
                username=user.username,
                created=user.created,
                updated=user.updated,
            )
            for user in found
        ]

    @router.post("/user/logout", response_model=MessageResponse)
    def logout(
        subject: CurrentSubject,
        response: Response,
        db: Annotated[DbSession, Depends(get_db)],
    ) -> MessageResponse:
        # Keyed on the token's sid, not on the cookie: the cookie is path-scoped
        # to the refresh endpoint, so the browser does not send it here. Reading
        # it instead would leave the session alive and still answer 200.
        if subject.session_id:
            sessions.revoke_session(db, subject.session_id)
            db.commit()

        # Same name and path as the cookie it replaces, or the browser keeps the
        # original alongside it and the next refresh succeeds. `Max-Age=0` with an
        # empty value is the whole clear — upstream sends no `Expires`.
        response.headers.append("set-cookie", sessions.refresh_cookie_header("", max_age=0))
        return MessageResponse(message=LOGOUT_MESSAGE)

    return router


def _as_current_user(user: User) -> CurrentUser:
    """Assemble ``GET /user``. Note what is absent: password, and also email."""
    return CurrentUser(
        id=user.id,
        name=user.name or "",
        username=user.username,
        created=user.created,
        updated=user.updated,
        deletion_scheduled_at=user.deletion_scheduled_at,
        is_local_user=True,
        auth_provider="local",
        is_admin=user.is_admin,
        settings=UserSettings(
            name=user.name or "",
            email_reminders_enabled=bool(user.email_reminders_enabled),
            discoverable_by_name=bool(user.discoverable_by_name),
            discoverable_by_email=bool(user.discoverable_by_email),
            overdue_tasks_reminders_enabled=bool(user.overdue_tasks_reminders_enabled),
            overdue_tasks_reminders_time=user.overdue_tasks_reminders_time,
            default_project_id=user.default_project_id or 0,
            week_start=user.week_start or 0,
            language=user.language or "",
            timezone=user.timezone or "",
            frontend_settings=user.frontend_settings,
            extra_settings_links=user.extra_settings_links,
        ),
    )
