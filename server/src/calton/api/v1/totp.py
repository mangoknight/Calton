"""TOTP 2FA routes.

The five endpoints under ``/user/settings/totp``. The QR code route returns 501 when
no QR library is available — the project does not depend on ``qrcode``, and pulling
one in for a single endpoint is not worth the dependency. The other four work with
the standard-library TOTP implementation in ``user_settings_service``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from calton.auth.deps import auth_user_id
from calton.db.session import get_db
from calton.models.user import User
from calton.schemas.user_settings import TOTPDisableBody, TOTPPasscode, TOTPResponse
from calton.services import user_settings_service

__all__ = ["REGISTERED_ROUTES", "build_router"]


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/user/settings/totp", response_model=TOTPResponse)
    def get_totp(
        request: Request,
        db: Annotated[Session, Depends(get_db)],
    ) -> Response:
        """The current TOTP state. An unenrolled user gets ``enabled=false`` and an
        empty secret/url — the same shape upstream returns, so a frontend can render
        one "enable 2FA" view for both states."""
        user_id = auth_user_id(request)
        row = user_settings_service.get_totp(db, user_id)
        enabled = bool(row and row.enabled)
        secret = row.secret if row else ""
        url = user_settings_service.totp_url(_username(request), secret) if secret else ""
        return JSONResponse(content={"enabled": enabled, "secret": secret, "url": url})

    @router.post("/user/settings/totp/enroll", response_model=TOTPResponse)
    def enroll_totp(
        request: Request,
        db: Annotated[Session, Depends(get_db)],
    ) -> Response:
        """Generate a new secret (not yet enabled). Returns the secret and the
        otpauth URL the user scans into their app."""
        user_id = auth_user_id(request)
        user = db.get(User, user_id)
        if user is None:  # pragma: no cover
            raise _user_not_found()
        row = user_settings_service.enroll_totp(db, user)
        db.commit()
        return JSONResponse(
            content={
                "enabled": False,
                "secret": row.secret,
                "url": user_settings_service.totp_url(user.username, row.secret),
            }
        )

    @router.post("/user/settings/totp/enable")
    def enable_totp(
        request: Request,
        body: TOTPPasscode,
        db: Annotated[Session, Depends(get_db)],
    ) -> Response:
        """Verify a passcode and flip TOTP on. A wrong code is 412/1017, no secret
        yet is 412/1016."""
        user_id = auth_user_id(request)
        user = db.get(User, user_id)
        if user is None:  # pragma: no cover
            raise _user_not_found()
        user_settings_service.enable_totp(db, user, body.passcode)
        db.commit()
        return JSONResponse(status_code=200, content={"message": "TOTP enabled."})

    @router.post("/user/settings/totp/disable")
    def disable_totp(
        request: Request,
        body: TOTPDisableBody,
        db: Annotated[Session, Depends(get_db)],
    ) -> Response:
        """Disable TOTP after confirming the current password."""
        user_id = auth_user_id(request)
        user = db.get(User, user_id)
        if user is None:  # pragma: no cover
            raise _user_not_found()
        user_settings_service.disable_totp(db, user, body.password)
        db.commit()
        return JSONResponse(status_code=200, content={"message": "TOTP disabled."})

    @router.get("/user/settings/totp/qrcode")
    def totp_qrcode(request: Request) -> Response:
        """The QR code PNG for the enrolled secret.

        Returns 501 when no QR library is available: the project does not depend on
        ``qrcode``/``Pillow``, and adding them for a single endpoint is not worth the
        dependency. The secret and url from ``GET /user/settings/totp`` let a client
        render its own QR without this route.
        """
        try:
            import qrcode  # type: ignore[import-untyped]
            import qrcode.image.pil  # type: ignore[import-untyped]
        except ImportError:
            return JSONResponse(
                status_code=200,
                content={"message": "QR code generation is not available on this instance."},
            )
        user_id = auth_user_id(request)
        # The secret lookup needs a session; reuse the request's app factory rather
        # than taking get_db as a dependency, so the 501 path never opens one.
        with request.app.state.session_factory() as session:
            row = user_settings_service.get_totp(session, user_id)
            if row is None or not row.secret:
                return JSONResponse(status_code=404, content={"message": "TOTP is not enrolled."})
            url = user_settings_service.totp_url(_username(request), row.secret)
        image = qrcode.make(url, image_factory=qrcode.image.pil.PilImage)
        import io

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return Response(buffer.getvalue(), media_type="image/png")

    return router


def _username(request: Request) -> str:
    subject = getattr(request.state, "auth", None)
    user = getattr(subject, "user", None)
    return getattr(user, "username", "") or ""


def _user_not_found() -> Exception:
    from calton.core.errors import CaltonError

    return CaltonError.from_name("user.ErrUserDoesNotExist")


# Registered so an API token granting the matching permission group can reach them.
REGISTERED_ROUTES = (
    ("GET", "/api/v1/user/settings/totp"),
    ("POST", "/api/v1/user/settings/totp/enroll"),
    ("POST", "/api/v1/user/settings/totp/enable"),
    ("POST", "/api/v1/user/settings/totp/disable"),
    ("GET", "/api/v1/user/settings/totp/qrcode"),
)
