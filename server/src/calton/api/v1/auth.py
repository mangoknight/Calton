"""Authentication endpoints: login, refresh and the token probe.

Four upstream behaviours are copied here that a reasonable implementation would
get differently. All four are measured (``tests/fixtures/go_jwt.json``):

1. **A failed login is 403 with code 1011, not 401**, and a wrong password is
   answered *identically* to an unknown username — upstream declines to leak
   whether an account exists. Do not "fix" this to 401.
2. **A missing username or password is 400 with code 1004**, a different error
   from a wrong one, and not the 412 validation shape.
3. **``POST /user/token`` is a 400 that tells you to use the refresh endpoint.**
   It is not a renewal endpoint despite its name, so it is implemented as the
   refusal rather than left unrouted (a 404 would be a different answer).
4. **The refresh endpoint's 401s use the bare ``{"message": ...}`` shape with no
   ``code``**, unlike the middleware's ``{"code": 11, ...}``. This is a third 401
   layer the design does not describe — see the module note below.

⚠️ The 401 story has three layers, not two. The design records "middleware 401 is
always code 11" and "a domain error keeps its own code"; measurement adds a third:
a handler that calls ``echo.NewHTTPError(401, "some text")`` produces
``{"message": "..."}`` with **no code at all**. Both refresh failures are of this
kind. Rendering them as code 11 would be wrong in the parity harness.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from calton.auth import jwt as jwt_auth
from calton.auth import sessions
from calton.auth.deps import CurrentSubject
from calton.auth.password import verify_password
from calton.config import Settings
from calton.core.errors import CaltonError, EchoStringError
from calton.db.session import get_db
from calton.models.user import User
from calton.schemas.auth import LoginRequest, MessageResponse, TokenResponse

#: Measured on both login and refresh. Without it a shared cache could serve one
#: user's token to another.
NO_STORE = "no-store"

#: The two refresh failures, verbatim. Distinct messages, unlike the middleware's
#: single one, because they distinguish "you sent nothing" from "what you sent is
#: no good" — which upstream considers non-sensitive here.
NO_REFRESH_TOKEN_MESSAGE = "No refresh token provided."
INVALID_REFRESH_TOKEN_MESSAGE = "Invalid or expired refresh token."

#: ``POST /token/test``. A literal U+1F375 TEACUP WITHOUT HANDLE, and the whole
#: body — upstream sends `{"message": "🍵"}` and nothing else.
TEAPOT_MESSAGE = "🍵"

#: ``POST /user/token`` refuses in these words.
RENEWAL_MOVED_MESSAGE = (
    "User tokens cannot be renewed via this endpoint. "
    "Use POST /user/token/refresh with a refresh token."
)


def build_router() -> APIRouter:
    router = APIRouter()

    @router.post("/login", response_model=TokenResponse)
    def login(
        body: LoginRequest,
        request: Request,
        response: Response,
        db: Annotated[DbSession, Depends(get_db)],
    ) -> TokenResponse:
        settings: Settings = request.app.state.settings

        if not body.username or not body.password:
            raise CaltonError.from_name("user.ErrNoUsernamePassword")

        user = db.scalars(select(User).where(User.username == body.username)).one_or_none()
        # One branch for both failures so the response cannot say which occurred.
        if user is None or not user.password or not verify_password(body.password, user.password):
            raise CaltonError.from_name("user.ErrWrongUsernameOrPassword")

        issued = sessions.create_session(
            db,
            user_id=user.id,
            is_long=body.long_token,
            device_info=request.headers.get("user-agent"),
            ip_address=request.client.host if request.client else None,
        )
        db.commit()

        _set_refresh_cookie(response, issued, settings)
        response.headers["Cache-Control"] = NO_STORE
        return TokenResponse(
            token=jwt_auth.issue_user_token(
                user_id=user.id,
                username=user.username,
                is_admin=user.is_admin,
                session_id=issued.session_id,
                settings=settings,
            )
        )

    @router.post("/user/token/refresh", response_model=TokenResponse)
    def refresh_token(
        request: Request,
        response: Response,
        db: Annotated[DbSession, Depends(get_db)],
    ) -> TokenResponse:
        settings: Settings = request.app.state.settings

        presented = request.cookies.get(sessions.REFRESH_COOKIE_NAME)
        if not presented:
            raise EchoStringError(401, NO_REFRESH_TOKEN_MESSAGE)

        rotated = sessions.rotate_session(db, presented)
        if rotated is None:
            raise EchoStringError(401, INVALID_REFRESH_TOKEN_MESSAGE)

        row, issued = rotated
        user = db.get(User, row.user_id)
        if user is None:
            raise EchoStringError(401, INVALID_REFRESH_TOKEN_MESSAGE)
        db.commit()

        _set_refresh_cookie(response, issued, settings)
        response.headers["Cache-Control"] = NO_STORE
        return TokenResponse(
            token=jwt_auth.issue_user_token(
                user_id=user.id,
                username=user.username,
                is_admin=user.is_admin,
                # The session survives the rotation, so sid is unchanged while a
                # fresh jti is minted. Clients key device lists on sid.
                session_id=row.id,
                settings=settings,
            )
        )

    @router.post("/user/token", response_model=MessageResponse)
    def renew_user_token(subject: CurrentSubject) -> MessageResponse:
        """Kept as the measured refusal rather than as a working renewal.

        The design's "keep both, the old one renews" reading is contradicted by
        the reference server, which answers 400 with this text.
        """
        raise EchoStringError(400, RENEWAL_MOVED_MESSAGE)

    @router.get("/token/test", response_model=MessageResponse)
    def test_token(subject: CurrentSubject) -> MessageResponse:
        return MessageResponse(message="ok")

    @router.post("/token/test", response_model=MessageResponse)
    def test_token_post(subject: CurrentSubject) -> MessageResponse:
        """Registered so it can refuse — upstream answers 418 ``🍵`` here.

        Measured on a live reference server: authenticated ``POST`` is 418 with
        that body, unauthenticated ``POST`` is the ordinary 401/11 (so the
        credential check runs *first*), and ``PUT``/``DELETE``/``PATCH``/``HEAD``
        are 405 carrying ``Allow: OPTIONS, GET, POST``.

        The teapot is the visible half; the registration is the load-bearing
        half. Leaving ``POST`` unregistered answers 405, which is a different
        status *and* changes the `Allow` header every other method reports —
        which is why the corpus asserts `Allow` rather than only this status.
        """
        raise EchoStringError(418, TEAPOT_MESSAGE)

    return router


def _set_refresh_cookie(
    response: Response, issued: sessions.IssuedSession, settings: Settings
) -> None:
    response.headers.append(
        "set-cookie",
        sessions.refresh_cookie_header(
            issued.plaintext,
            max_age=sessions.cookie_max_age(settings, issued.is_long),
        ),
    )
