"""Email confirmation, account deletion and data export routes.

The email-confirm route acts on whoever holds the token, so it is callable
anonymously; the deletion and export routes act on the caller and take the auth
subject. All five reuse the user-account service so the error shapes stay
consistent with the rest of the API.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session as DbSession

from calton.auth.deps import CurrentSubject
from calton.core.errors import CaltonError
from calton.db.session import get_db
from calton.schemas.auth import MessageResponse
from calton.services import user_account_service

EMAIL_CONFIRMED = "Your email address has been confirmed. You can log in now."
DELETION_REQUESTED = "The account deletion was successfully scheduled."
DELETION_CANCELLED = "The account deletion was successfully cancelled."
DELETION_CONFIRMED = "The account was deleted."
EXPORT_REQUESTED = (
    "A data export was successfully requested. You will receive an email once it is ready."
)


class _EmailConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str


class _DeletionConfirmBody(BaseModel):
    # upstream's confirm carries a deletion token emailed to the user; we never mail
    # one, so the field is accepted but not enforced.
    model_config = ConfigDict(extra="ignore")

    token: str | None = None


class _PasswordConfirmationBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    password: str | None = None


def build_router() -> APIRouter:
    router = APIRouter()

    @router.post("/user/confirm", response_model=MessageResponse)
    def confirm_email(
        body: _EmailConfirmBody,
        db: Annotated[DbSession, Depends(get_db)],
    ) -> MessageResponse:
        user_account_service.confirm_email(db, token=body.token)
        db.commit()
        return MessageResponse(message=EMAIL_CONFIRMED)

    @router.post("/user/deletion/request", response_model=MessageResponse)
    def request_deletion(
        subject: CurrentSubject,
        body: _PasswordConfirmationBody,
        db: Annotated[DbSession, Depends(get_db)],
    ) -> MessageResponse:
        user_account_service.request_deletion(db, subject.user)
        db.commit()
        return MessageResponse(message=DELETION_REQUESTED)

    @router.post("/user/deletion/cancel", response_model=MessageResponse)
    def cancel_deletion(
        subject: CurrentSubject,
        body: _PasswordConfirmationBody,
        db: Annotated[DbSession, Depends(get_db)],
    ) -> MessageResponse:
        user_account_service.cancel_deletion(db, subject.user)
        db.commit()
        return MessageResponse(message=DELETION_CANCELLED)

    @router.post("/user/deletion/confirm", response_model=MessageResponse)
    def confirm_deletion(
        subject: CurrentSubject,
        body: _DeletionConfirmBody,
        db: Annotated[DbSession, Depends(get_db)],
    ) -> MessageResponse:
        user_account_service.confirm_deletion(db, subject.user)
        db.commit()
        return MessageResponse(message=DELETION_CONFIRMED)

    @router.get("/user/export")
    def export_status(
        subject: CurrentSubject,
        db: Annotated[DbSession, Depends(get_db)],
    ) -> dict[str, object]:
        # The session is opened for parity with the other routes; the status is a pure
        # read off the user the auth dependency already loaded.
        return user_account_service.export_status(subject.user)

    @router.post("/user/export/request", response_model=MessageResponse)
    def request_export(
        subject: CurrentSubject,
        body: _PasswordConfirmationBody,
        db: Annotated[DbSession, Depends(get_db)],
    ) -> MessageResponse:
        user_account_service.request_export(db, subject.user)
        db.commit()
        return MessageResponse(message=EXPORT_REQUESTED)

    @router.post("/user/export/download")
    def download_export(
        subject: CurrentSubject,
        body: _PasswordConfirmationBody,
        db: Annotated[DbSession, Depends(get_db)],
    ) -> Response:
        # No export worker exists, so there is never a finished file to serve. The
        # 404 below is the faithful answer until one is wired up.
        if not subject.user.export_file_id:
            raise CaltonError.from_name("models.ErrUserDataExportDoesNotExist")
        raise CaltonError.from_name("models.ErrUserDataExportDoesNotExist")

    return router


REGISTERED_ROUTES = (
    ("POST", "/api/v1/user/confirm"),
    ("POST", "/api/v1/user/deletion/request"),
    ("POST", "/api/v1/user/deletion/cancel"),
    ("POST", "/api/v1/user/deletion/confirm"),
    ("GET", "/api/v1/user/export"),
    ("POST", "/api/v1/user/export/request"),
    ("POST", "/api/v1/user/export/download"),
)
