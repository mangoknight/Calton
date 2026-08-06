"""API tokens: ``tk_``-prefixed bearer credentials with per-route permissions.

The hashing recipe has two details that the natural implementation gets wrong,
both confirmed by hashing the same token four ways and checking which digest the
reference server stored (``tests/fixtures/go_api_tokens.json.hashing``):

* **the ``tk_`` prefix is part of the hashed secret.** Stripping it — which reads
  like removing a display artefact — produces a digest that never verifies.
* **the salt is used as its ten literal characters**, not hex-decoded. The stored
  salt is not even hex (``46xOw1EZUr``), so decoding it raises rather than
  silently differing, but only for salts that happen to contain a non-hex letter.

Verification goes through ``token_last_eight`` to narrow to candidate rows and
then re-hashes with **each candidate's own salt**. One shared salt would let a
single precomputation attack every token at once.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from calton.models.api_token import APIToken

#: Measured: "tk_" plus 40 hex characters.
TOKEN_PREFIX = "tk_"
TOKEN_RANDOM_BYTES = 20
SALT_LENGTH = 10
PBKDF2_ITERATIONS = 10000
PBKDF2_DKLEN = 50

#: Upstream refuses anything shorter than the prefix plus the eight-character
#: index before it touches the database — a guard against slicing a short string.
MIN_TOKEN_LENGTH = len(TOKEN_PREFIX) + 8

#: The salt alphabet upstream draws from. Only its length is load-bearing for
#: verification; generation just has to stay printable-ASCII so the column round
#: trips through TEXT unchanged.
SALT_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


@dataclass(frozen=True)
class MintedToken:
    """A new token: the row's fields plus the plaintext, shown exactly once."""

    plaintext: str
    salt: str
    hash: str
    last_eight: str


def mint() -> MintedToken:
    plaintext = TOKEN_PREFIX + secrets.token_hex(TOKEN_RANDOM_BYTES)
    salt = "".join(secrets.choice(SALT_ALPHABET) for _ in range(SALT_LENGTH))
    return MintedToken(
        plaintext=plaintext,
        salt=salt,
        hash=hash_token(plaintext, salt),
        last_eight=plaintext[-8:],
    )


def hash_token(plaintext: str, salt: str) -> str:
    """PBKDF2-SHA256 over the **whole** plaintext, salted with the raw characters."""
    return hashlib.pbkdf2_hmac(
        "sha256",
        plaintext.encode(),
        salt.encode(),
        PBKDF2_ITERATIONS,
        dklen=PBKDF2_DKLEN,
    ).hex()


def looks_like_api_token(credential: str) -> bool:
    """Whether to route this credential to the token path rather than to JWT."""
    return credential.startswith(TOKEN_PREFIX)


def verify(db: DbSession, plaintext: str, *, now: datetime | None = None) -> APIToken | None:
    """The token row this plaintext authenticates, or None.

    None for every failure — too short, unknown, expired. The caller renders one
    body for all of them, so distinguishing here would only invite a caller that
    leaks which.
    """
    if len(plaintext) < MIN_TOKEN_LENGTH:
        return None

    now = now or datetime.now(UTC)
    candidates = db.scalars(
        select(APIToken).where(APIToken.token_last_eight == plaintext[-8:])
    ).all()

    for candidate in candidates:
        # Each row's own salt: a shared one would make a single precomputation
        # usable against every token.
        expected = hash_token(plaintext, candidate.token_salt)
        if not secrets.compare_digest(expected, candidate.token_hash):
            continue
        if _expired(candidate, now):
            return None
        return candidate

    return None


def _expired(token: APIToken, now: datetime) -> bool:
    expires_at = token.expires_at
    if expires_at is None:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= now


def granted_permissions(token: APIToken) -> dict[str, list[str]]:
    """The token's permission map, decoded from its JSON column."""
    try:
        decoded = json.loads(token.permissions or "{}")
    except json.JSONDecodeError:
        # A corrupt row authorises nothing rather than everything.
        return {}
    if not isinstance(decoded, dict):
        return {}
    return {
        str(group): [str(action) for action in actions]
        for group, actions in decoded.items()
        if isinstance(actions, list)
    }


def authorises(token: APIToken, group: str, action: str) -> bool:
    return action in granted_permissions(token).get(group, [])


def encode_permissions(permissions: dict[str, list[str]]) -> str:
    return json.dumps(permissions)
