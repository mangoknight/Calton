"""Request and response models for the authentication endpoints.

Every handler names one of these explicitly. A handler annotated
``-> dict[str, Any]`` runs correctly but generates an empty response schema, so
the contract diff and the generated TypeScript client both see nothing — the
failure mode that made ``GET /info`` report 22 missing fields.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class LoginRequest(BaseModel):
    # strict: the CRUDRouter enforces this on write schemas, and login is the one
    # place a client's type sloppiness would be a security-relevant surprise.
    model_config = ConfigDict(strict=True, extra="ignore")

    # Both optional at the model layer even though both are required in practice:
    # a missing field must produce the measured 400/1004 "Please specify a
    # username and a password.", not the 412/2002 validation shape that a
    # required field would give.
    username: str | None = None
    password: str | None = None
    #: Lengthens the refresh cookie only — the access token TTL is unchanged.
    long_token: bool = False
    totp_passcode: str | None = None


class TokenResponse(BaseModel):
    """``POST /login`` and ``POST /user/token/refresh``.

    Exactly one key. Upstream returns no user object here; clients fetch
    ``GET /user`` separately, and adding a convenience field would diverge.
    """

    token: str


class MessageResponse(BaseModel):
    """``{"message": ...}`` — what ``GET /token/test`` returns on success."""

    message: str
