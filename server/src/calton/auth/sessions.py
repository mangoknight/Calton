"""Refresh sessions: the store behind the ``calton_refresh_token`` cookie.

The four behaviours here were measured against the Go server
(``tests/fixtures/go_jwt.json``) because each has a plausible alternative that
would look correct in isolation:

* A refresh **keeps the session row and rotates the secret in place**. The ``sid``
  claim therefore survives a refresh while ``jti`` does not. Deleting and
  re-inserting would give the client a new ``sid`` on every refresh and break any
  "sign out this device" list keyed on it.
* The replaced cookie **stops working immediately** — refresh tokens are
  single-use.
* ``token_hash`` is ``sha256`` of the cookie's **ASCII text**. The cookie is 256
  hex characters; hashing the 128 bytes they encode gives a different digest.
* ``long_token`` at login lengthens **only the cookie's Max-Age** (30 days rather
  than 72 hours). The access token's own TTL is unaffected, which is not what the
  name suggests.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from calton.config import Settings
from calton.models.session import Session

#: Cookie name and path, both measured. The path scoping means the cookie is not
#: attached to ordinary API calls, so it cannot leak through them.
REFRESH_COOKIE_NAME = "calton_refresh_token"
REFRESH_COOKIE_PATH = "/api/v1/user/token/refresh"

#: 128 random bytes rendered as 256 hex characters, matching the measured length.
REFRESH_TOKEN_BYTES = 128


@dataclass(frozen=True)
class IssuedSession:
    """A session row plus the one-time plaintext to hand to the client."""

    session_id: str
    plaintext: str
    is_long: bool


def hash_refresh_token(plaintext: str) -> str:
    """SHA-256 of the cookie text. See the module docstring — not of its hex bytes."""
    return hashlib.sha256(plaintext.encode()).hexdigest()


def refresh_cookie_header(value: str, max_age: int) -> str:
    """The ``Set-Cookie`` header for the refresh cookie, byte for byte as Go emits it.

    Built by hand rather than through ``Response.set_cookie`` because Starlette
    renders through :class:`http.cookies.SimpleCookie`, which differs from Go's
    ``http.SetCookie`` in two ways that both reached the wire:

    * it lowercases nothing but *is* typed ``Literal["lax", ...]``, and the value
      is emitted verbatim — so the annotation pushes callers to write ``lax``
      and ship ``SameSite=lax`` where upstream sends ``SameSite=Lax``;
    * it **quotes an empty value**, so clearing the cookie sends
      ``calton_refresh_token=""`` where upstream sends ``calton_refresh_token=``.

    Neither is cosmetic in the way it looks: cookie attributes are compared
    case-sensitively by some proxies, and a client that stores the literal two
    characters ``""`` as its refresh token no longer has a cleared cookie.

    Attribute order follows Go's so the two headers can be eyeballed. ``Secure``
    is absent upstream and ``Expires`` is never sent, not even on the clear —
    measured on a live reference server, not read off the source.
    """
    return (
        f"{REFRESH_COOKIE_NAME}={value}; Path={REFRESH_COOKIE_PATH}; "
        f"Max-Age={max_age}; HttpOnly; SameSite=Lax"
    )


def cookie_max_age(settings: Settings, is_long: bool) -> int:
    """How long the browser keeps the refresh cookie.

    ``jwtttllong`` for a long session, ``jwtttl`` otherwise — neither is the
    access token's ``jwtttlshort``.
    """
    return settings.service.jwtttllong if is_long else settings.service.jwtttl


def create_session(
    db: DbSession,
    *,
    user_id: int,
    is_long: bool = False,
    device_info: str | None = None,
    ip_address: str | None = None,
    now: datetime | None = None,
) -> IssuedSession:
    now = now or datetime.now(UTC)
    plaintext = secrets.token_hex(REFRESH_TOKEN_BYTES)
    session_id = str(uuid.uuid4())

    db.add(
        Session(
            id=session_id,
            user_id=user_id,
            token_hash=hash_refresh_token(plaintext),
            device_info=device_info,
            ip_address=ip_address,
            is_long_session=is_long,
            last_active=now,
            created=now,
        )
    )
    db.flush()
    return IssuedSession(session_id=session_id, plaintext=plaintext, is_long=is_long)


def rotate_session(
    db: DbSession, plaintext: str, *, now: datetime | None = None
) -> tuple[Session, IssuedSession] | None:
    """Redeem a refresh token, returning the session and its replacement secret.

    None when the token matches no row, which covers both a forged token and one
    that a previous rotation already replaced. The row is updated rather than
    replaced so ``sid`` stays stable.
    """
    now = now or datetime.now(UTC)
    row = db.scalars(
        select(Session).where(Session.token_hash == hash_refresh_token(plaintext))
    ).one_or_none()
    if row is None:
        return None

    replacement = secrets.token_hex(REFRESH_TOKEN_BYTES)
    row.token_hash = hash_refresh_token(replacement)
    row.last_active = now
    db.flush()

    return row, IssuedSession(
        session_id=row.id, plaintext=replacement, is_long=bool(row.is_long_session)
    )


def revoke_session(db: DbSession, session_id: str) -> bool:
    """Drop a session by its id. True if one was found.

    Keyed on the id — the JWT's ``sid`` claim — rather than on the refresh
    cookie, because that is what logout has to work from: the cookie is scoped to
    ``/api/v1/user/token/refresh`` and browsers therefore do **not** send it to
    ``/api/v1/user/logout``. A cookie-based implementation silently logs nobody
    out while still answering 200. Measured: logout with a bearer token and no
    cookie at all deletes the row.
    """
    row = db.get(Session, session_id)
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True
