"""User settings, avatar and timezones.

The authenticated half mounts under ``/user/settings/*`` and ``/user/timezones``.
The public avatar route ``GET /{username}/avatar`` mounts separately
(:func:`build_avatar_router`) without the auth dependency — avatars are public in
upstream, served to anonymous viewers on shared boards, so requiring auth here
would break the frontend.

Avatar upload stores the bytes through the same ``FileStorage`` attachments use,
keyed by the ``files`` row's id, and points ``user.avatar_file_id`` at it. Serving
reads those bytes back; a user without an uploaded avatar answers 404 rather than a
fabricated image — a generated default is a Phase 2 nicety and not in scope here.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from calton.auth.deps import auth_user_id
from calton.config import get_settings
from calton.db.session import get_db
from calton.models.file import File
from calton.models.user import User
from calton.schemas.user_settings import (
    AvatarProviderResponse,
    AvatarProviderUpdate,
    EmailUpdate,
    GeneralSettingsUpdate,
)
from calton.services import file_storage, user_settings_service
from calton.services.file_storage import FileStorage

__all__ = ["REGISTERED_ROUTES", "build_avatar_router", "build_router"]


def build_router() -> APIRouter:
    router = APIRouter()

    @router.post("/user/settings/general")
    def update_general(
        request: Request,
        body: GeneralSettingsUpdate,
        db: Annotated[Session, Depends(get_db)],
    ) -> Response:
        """Merge the sent settings onto the current user and return the updated user.

        Returns the full ``GET /user`` shape (the same one ``CurrentUser`` produces)
        so a single round-trip updates and reads back — the frontend relies on the
        response carrying the new settings rather than re-fetching.
        """
        user_id = auth_user_id(request)
        user = db.get(User, user_id)
        if user is None:  # pragma: no cover - auth guarantees a real user
            raise _user_not_found()
        user_settings_service.update_general_settings(db, user, body.model_dump(exclude_unset=True))
        db.commit()
        return JSONResponse(content=_current_user_view(user))

    @router.post("/user/settings/email")
    def update_email(
        request: Request,
        body: EmailUpdate,
        db: Annotated[Session, Depends(get_db)],
    ) -> Response:
        user_id = auth_user_id(request)
        user = db.get(User, user_id)
        if user is None:  # pragma: no cover
            raise _user_not_found()
        user_settings_service.update_email(db, user, body.new_email)
        db.commit()
        return JSONResponse(status_code=200, content={"message": "Email change requested."})

    @router.get("/user/settings/avatar", response_model=AvatarProviderResponse)
    def get_avatar(request: Request) -> Response:
        user_id = auth_user_id(request)
        # No session needed: the avatar provider lives on the user already resolved
        # by the auth dependency. Re-fetching would be a second query for nothing.
        user = _user_from_request(request, user_id)
        return JSONResponse(
            content={"avatar_provider": user_settings_service.get_avatar_provider(user)}
        )

    @router.post("/user/settings/avatar", response_model=AvatarProviderResponse)
    def set_avatar(
        request: Request,
        body: AvatarProviderUpdate,
        db: Annotated[Session, Depends(get_db)],
    ) -> Response:
        user_id = auth_user_id(request)
        user = db.get(User, user_id)
        if user is None:  # pragma: no cover
            raise _user_not_found()
        provider = user_settings_service.set_avatar_provider(db, user, body.avatar_provider)
        db.commit()
        return JSONResponse(content={"avatar_provider": provider})

    @router.put("/user/settings/avatar/upload")
    async def upload_avatar(
        request: Request,
        avatar: Annotated[UploadFile, Path(description="The avatar as a single file")],
        db: Annotated[Session, Depends(get_db)],
    ) -> Response:
        """Store the uploaded avatar file and point ``avatar_file_id`` at it.

        The multipart field name is ``avatar`` (upstream's), and the bytes are read
        fully into memory before storage — avatars are small and the size limit is
        the attachments one, so a streaming write buys nothing here.
        """
        from calton.db.base import utcnow

        user_id = auth_user_id(request)
        user = db.get(User, user_id)
        if user is None:  # pragma: no cover
            raise _user_not_found()

        content = await avatar.read()
        if len(content) > file_storage.MAX_SIZE_BYTES:
            raise _avatar_too_large()

        stored = File(
            name=avatar.filename or "avatar",
            mime=file_storage.detect_mime(content),
            size=len(content),
            created=utcnow(),
            created_by_id=user_id,
        )
        db.add(stored)
        db.flush()
        storage = FileStorage(get_settings().files.basepath)
        storage.save(stored.id, content)
        user.avatar_file_id = stored.id
        user.avatar_provider = "upload"
        db.commit()
        return JSONResponse(status_code=200, content={"message": "Avatar uploaded."})

    @router.get("/user/timezones")
    def list_timezones() -> Response:
        """The timezones this instance knows about. Python's ``zoneinfo`` pulls the
        system's available zones, which is the same source upstream's Go list comes
        from (the IANA tz database)."""
        try:
            from zoneinfo import available_timezones

            zones = sorted(available_timezones())
        except Exception:  # pragma: no cover - zoneinfo is always present on 3.12
            zones = []
        return JSONResponse(content=zones)

    return router


def build_avatar_router() -> APIRouter:
    """The public avatar-serving route. Mounted WITHOUT the auth dependency so an
    anonymous viewer can fetch an avatar for a shared board."""
    router = APIRouter()

    @router.get("/{username}/avatar")
    def serve_avatar(
        username: Annotated[str, Path(min_length=1)],
        db: Annotated[Session, Depends(get_db)],
    ) -> Response:
        user = db.scalars(select(User).where(User.username == username)).one_or_none()
        if user is None or not user.avatar_file_id:
            raise _avatar_not_found()
        stored = db.get(File, user.avatar_file_id)
        if stored is None:
            raise _avatar_not_found()
        storage = FileStorage(get_settings().files.basepath)
        content = storage.load(stored.id)
        if content is None:
            raise _avatar_not_found()
        return Response(
            content,
            status_code=200,
            media_type=stored.mime or "application/octet-stream",
            headers={"Cache-Control": "max-age=3600"},
        )

    return router


# --- helpers -----------------------------------------------------------------


def _user_not_found() -> Exception:
    from calton.core.errors import CaltonError

    return CaltonError.from_name("user.ErrUserDoesNotExist")


def _avatar_not_found() -> Exception:
    from calton.core.errors import CaltonError

    return CaltonError(code=0, message="This avatar does not exist.", http_status=404)


def _avatar_too_large() -> Exception:
    from calton.core.errors import CaltonError

    return CaltonError(
        code=4012,
        message="File exceeds the configured file size of 0 bytes (limit is 20MB).",
        http_status=400,
    )


def _user_from_request(request: Request, user_id: int) -> User:
    """The user off ``request.state.auth`` — avoids a second query when the route
    only reads columns the auth dependency already loaded."""
    subject = getattr(request.state, "auth", None)
    user = getattr(subject, "user", None)
    if user is not None and getattr(user, "id", None) == user_id:
        return user  # type: ignore
    from calton.core.errors import CaltonError

    raise CaltonError.from_name("user.ErrUserDoesNotExist")


def _current_user_view(user: User) -> dict:  # type: ignore
    """The ``GET /user`` shape, reused from the user router so a settings update
    returns the same body the frontend reads from ``GET /user``."""
    from calton.api.v1.user import _as_current_user

    return _as_current_user(user).model_dump(mode="json")


# --- route registry ----------------------------------------------------------
#
# The authenticated routes are registered so an API token granted the matching
# permission group can reach them (the prefix is not JWT-only). The public avatar
# route is deliberately absent: it carries no auth, so there is nothing for the
# token check to authorise against.

REGISTERED_ROUTES = (
    ("POST", "/api/v1/user/settings/general"),
    ("POST", "/api/v1/user/settings/email"),
    ("GET", "/api/v1/user/settings/avatar"),
    ("POST", "/api/v1/user/settings/avatar"),
    ("PUT", "/api/v1/user/settings/avatar/upload"),
    ("GET", "/api/v1/user/timezones"),
)
