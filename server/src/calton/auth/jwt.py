"""JWT issuance and verification.

Every claim name, the claim set's exact membership and the two TTLs were measured
against a running Go server, not read off the source — see
``scripts/dump_go_jwt.py`` and the fixture it writes. Three of those measurements
contradict what a reasonable reading would predict, so they are called out here:

* **There is no ``iat`` claim.** The design's acceptance criterion "``exp - iat``
  equals the configured TTL" is not checkable as written; ``exp`` is compared to
  the wall clock at issuance instead.
* **User tokens and link-share tokens use different TTLs.** A user token expires
  in ``service.jwtttlshort`` (600s by default), a link-share token in
  ``service.jwtttl`` (72h). One constant for both is wrong either way round.
* **``sharedByID`` is camelCase** while every other claim is snake_case. An
  upstream inconsistency that clients now depend on.

⚠️ The TTL is read from configuration on every call rather than captured at import
time: the integration suite turns ``service.jwtttlshort`` down to 5 seconds to
test expiry races, and a module-level constant would ignore it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import jwt

from calton.config import Settings

ALGORITHM = "HS256"

#: The ``type`` claim. Upstream calls these AuthTypeUser and AuthTypeLinkShare.
TYPE_USER = 1
TYPE_LINK_SHARE = 2

#: Exactly the keys a user token carries. Asserted against the recorded fixture:
#: an extra claim is as much a divergence as a missing one, because clients that
#: enumerate claims (and the parity harness) see both.
USER_CLAIM_KEYS = frozenset({"type", "id", "username", "is_admin", "exp", "sid", "jti"})

#: Exactly the keys a link-share token carries.
LINK_SHARE_CLAIM_KEYS = frozenset(
    {"type", "id", "hash", "project_id", "permission", "sharedByID", "exp"}
)


class InvalidTokenError(Exception):
    """A token that failed to decode, verify or had expired.

    One exception for every failure mode on purpose: the caller renders a single
    body (code 11) for all of them, and distinguishing "expired" from "forged" in
    the response would tell an attacker which one they achieved.
    """


def issue_user_token(
    *,
    user_id: int,
    username: str,
    is_admin: bool,
    session_id: str,
    settings: Settings,
    now: datetime | None = None,
) -> str:
    """Mint an access token for a user session.

    ``session_id`` is the ``sessions`` row this token belongs to; it survives a
    refresh, while ``jti`` is new on every issuance.
    """
    now = now or datetime.now(UTC)
    claims: dict[str, Any] = {
        "type": TYPE_USER,
        "id": user_id,
        "username": username,
        "is_admin": is_admin,
        "exp": int(now.timestamp()) + settings.service.jwtttlshort,
        "sid": session_id,
        "jti": str(uuid.uuid4()),
    }
    return encode(claims, settings)


def issue_link_share_token(
    *,
    share_id: int,
    share_hash: str,
    project_id: int,
    permission: int,
    shared_by_id: int,
    settings: Settings,
    now: datetime | None = None,
) -> str:
    """Mint a token for an authenticated link share.

    Note the TTL: ``jwtttl`` (72h), not the 600s a user token gets. Measured, and
    the opposite of what "short-lived access token" would suggest.
    """
    now = now or datetime.now(UTC)
    claims: dict[str, Any] = {
        "type": TYPE_LINK_SHARE,
        "id": share_id,
        "hash": share_hash,
        "project_id": project_id,
        "permission": permission,
        # camelCase, alone among the claims. Upstream inconsistency; copied.
        "sharedByID": shared_by_id,
        "exp": int(now.timestamp()) + settings.service.jwtttl,
    }
    return encode(claims, settings)


def encode(claims: dict[str, Any], settings: Settings) -> str:
    return jwt.encode(claims, settings.service.secret, algorithm=ALGORITHM)


def decode(token: str, settings: Settings) -> dict[str, Any]:
    """Verify a token and return its claims, or raise :class:`InvalidTokenError`.

    The algorithm is pinned to a single entry so a token whose header says
    ``{"alg": "none"}`` is rejected rather than accepted unsigned.
    """
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            settings.service.secret,
            algorithms=[ALGORITHM],
            # `iat` is not issued, so verifying it would reject every real token.
            options={"require": ["exp"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc

    if claims.get("type") not in (TYPE_USER, TYPE_LINK_SHARE):
        raise InvalidTokenError("unknown token type")
    return claims
