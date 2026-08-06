"""Password management routes: change, reset and reset-token request.

These three are the ``other.user`` sub-routes that handle credentials. The change route
acts on the caller and so takes the auth subject; the reset and reset-token routes act
on whoever holds the token and are callable anonymously (you reset a password you have
forgotten, which by definition means you are not logged in). They reuse the
user-account service rather than touching the ORM directly so the error shapes stay
consistent with the rest of the API.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session as DbSession

from calton.auth.deps import CurrentSubject
from calton.db.session import get_db
from calton.schemas.auth import MessageResponse
from calton.services import user_account_service

PASSWORD_UPDATED = "The password was updated successfully."
RESET_SENT = "A link to reset your password was sent to you."
RESET_DONE = "The password was successfully changed."


class _PasswordChangeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    old_password: str
    new_password: str


class _PasswordResetBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_password: str
    token: str


class _PasswordTokenBody(BaseModel):
    # upstream's field is ``email``; the route also accepts a username in the same
    # slot, so the service resolves it as "email or username".
    model_config = ConfigDict(extra="forbid")

    email: str


def build_router() -> APIRouter:
    router = APIRouter()

    @router.post("/user/password", response_model=MessageResponse)
    def change_password(
        subject: CurrentSubject,
        body: _PasswordChangeBody,
        db: Annotated[DbSession, Depends(get_db)],
    ) -> MessageResponse:
        user_account_service.change_password(
            db, subject.user, old_password=body.old_password, new_password=body.new_password
        )
        db.commit()
        return MessageResponse(message=PASSWORD_UPDATED)

    @router.post("/user/password/reset", response_model=MessageResponse)
    def reset_password(
        body: _PasswordResetBody,
        db: Annotated[DbSession, Depends(get_db)],
    ) -> MessageResponse:
        user_account_service.reset_password(db, token=body.token, new_password=body.new_password)
        db.commit()
        return MessageResponse(message=RESET_DONE)

    @router.post("/user/password/token", response_model=MessageResponse)
    def request_password_reset(
        body: _PasswordTokenBody,
        db: Annotated[DbSession, Depends(get_db)],
    ) -> MessageResponse:
        user_account_service.request_password_reset(db, email_or_username=body.email)
        db.commit()
        return MessageResponse(message=RESET_SENT)

    return router


REGISTERED_ROUTES = (
    ("POST", "/api/v1/user/password"),
    ("POST", "/api/v1/user/password/reset"),
    ("POST", "/api/v1/user/password/token"),
)
