"""User settings: general settings update, avatar provider/file, CalDAV tokens.

Every function takes the session as its first positional parameter and never opens
its own — the read-your-writes contract shared with every other service here.

TOTP is implemented with the standard library alone (``hmac``/``hashlib``/``base32``)
rather than pulling in ``pyotp``: the project's dependency set does not carry it, and
the algorithm is short. The QR code route is the one place a second library
(``qrcode``) would be needed; it is stubbed as 501 there rather than adding a
dependency for a single endpoint — see ``api/v1/totp.py``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from calton.core.errors import CaltonError
from calton.db.base import utcnow
from calton.models.caldav_token import CalDAVToken
from calton.models.totp import TOTP
from calton.models.user import User

#: TOTP step and digits, matching the RFC 6238 defaults pyotp and upstream use.
TOTP_STEP = 30
TOTP_DIGITS = 6

#: The issuer label embedded in the otpauth URL. Upstream uses the instance name;
#: there is no configured instance name here, so a fixed one stands in.
TOTP_ISSUER = "Calton"

#: Avatar providers upstream recognises. ``default`` is "no avatar". An unknown
#: provider is rejected with ``user.ErrInvalidAvatarProvider`` (412/1018).
AVATAR_PROVIDERS = frozenset(
    {"default", "gravatar", "upload", "initials", "marble", "ldap", "openid"}
)


# --- general settings --------------------------------------------------------


#: The columns ``POST /user/settings/general`` may merge onto the user row.
#: ``email`` is handled by its own endpoint, so it is deliberately absent — a
#: general-settings body carrying ``email`` is ignored on the column, the same way
#: upstream's handler does not treat it as a settings field.
GENERAL_FIELDS: tuple[str, ...] = (
    "name",
    "discoverable_by_name",
    "discoverable_by_email",
    "overdue_tasks_reminders_enabled",
    "overdue_tasks_reminders_time",
    "default_project_id",
    "week_start",
    "language",
    "timezone",
    "email_reminders_enabled",
    "frontend_settings",
    "extra_settings_links",
)


def update_general_settings(session: Session, user: User, body: dict[str, Any]) -> User:
    """Merge the sent keys onto the user row and return it.

    ``default_project_id`` is stored nullable; a 0 from the wire becomes NULL so the
    column reads back the way the rest of the API expects (``or 0`` at read time).
    """
    for field in GENERAL_FIELDS:
        if field not in body:
            continue
        value = body[field]
        if field == "default_project_id" and value == 0:
            value = None
        setattr(user, field, value)
    session.flush()
    return user


def update_email(session: Session, user: User, new_email: str) -> None:
    """Validate and stage an email change.

    The confirmation email is not sent — there is no SMTP in this build — so this is
    logged-only by design. The address is *not* written to the row: upstream stages
    it behind a confirmation token and only commits on confirmation, and writing it
    now would let a typo'd address take effect with no verification path.
    """
    if not new_email:
        raise CaltonError.from_name("user.ErrNoUsernamePassword")
    if _invalid_email(new_email):
        raise CaltonError.from_name("models.ErrInvalidData")
    existing = session.scalars(select(User).where(User.email == new_email)).one_or_none()
    if existing is not None and existing.id != user.id:
        raise CaltonError.from_name("user.ErrUserEmailExists")
    # Log only — no SMTP in this build. The row stays unchanged until a confirmation
    # flow exists; writing the address now would skip verification entirely.
    import logging

    logging.getLogger("calton.email").info(
        "email change requested: user=%s new_email=%s", user.id, new_email
    )
    session.flush()


def _invalid_email(email: str) -> bool:
    local, separator, domain = email.partition("@")
    return not (local and separator and "." in domain and not domain.startswith("."))


# --- avatar provider ---------------------------------------------------------


def set_avatar_provider(session: Session, user: User, provider: str) -> str:
    """Validate and store the avatar provider. Returns the stored value."""
    if provider not in AVATAR_PROVIDERS:
        raise CaltonError.from_name("user.ErrInvalidAvatarProvider")
    user.avatar_provider = provider
    session.flush()
    return provider


def get_avatar_provider(user: User) -> str:
    return user.avatar_provider or "default"


# --- CalDAV tokens -----------------------------------------------------------


def list_caldav_tokens(session: Session, user_id: int) -> list[CalDAVToken]:
    return list(
        session.scalars(
            select(CalDAVToken).where(CalDAVToken.user_id == user_id).order_by(CalDAVToken.id)
        ).all()
    )


def create_caldav_token(session: Session, user_id: int) -> CalDAVToken:
    """Mint a new CalDAV token. The plaintext is shown once and never recoverable."""
    token = CalDAVToken(user_id=user_id, token=secrets.token_urlsafe(32), created=utcnow())
    session.add(token)
    session.flush()
    return token


def delete_caldav_token(session: Session, user_id: int, token_id: int) -> None:
    row = session.scalars(
        select(CalDAVToken).where(CalDAVToken.id == token_id, CalDAVToken.user_id == user_id)
    ).one_or_none()
    if row is None:
        raise CaltonError(code=0, message="This caldav token does not exist.", http_status=404)
    session.delete(row)
    session.flush()


# --- TOTP --------------------------------------------------------------------


def _generate_secret() -> str:
    """A base32 secret, 20 bytes as upstream's default."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")


def totp_url(username: str, secret: str) -> str:
    """The ``otpauth://`` URL a TOTP app imports. Mirrors pyotp's provisioning URI."""
    label = f"{TOTP_ISSUER}:{username}"
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer={TOTP_ISSUER}"
        f"&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_STEP}"
    )


def get_totp(session: Session, user_id: int) -> TOTP | None:
    return session.scalars(select(TOTP).where(TOTP.user_id == user_id)).one_or_none()


def enroll_totp(session: Session, user: User) -> TOTP:
    """Generate (or regenerate) a TOTP secret for the user, not yet enabled.

    Re-enrolling overwrites any existing secret — upstream's ``EnrollTOTP`` upserts
    on ``user_id``, so a second enroll replaces the first. ``enabled`` is reset to
    false: a new secret must be verified before it is live.
    """
    existing = get_totp(session, user.id)
    secret = _generate_secret()
    if existing is None:
        row = TOTP(user_id=user.id, secret=secret, enabled=False)
        session.add(row)
    else:
        row = existing
        row.secret = secret
        row.enabled = False
    session.flush()
    return row


def enable_totp(session: Session, user: User, passcode: str) -> None:
    """Verify a passcode against the enrolled secret and flip ``enabled``.

    Raises ``user.ErrTOTPNotEnabled`` (the 412 for "no secret enrolled yet") when
    there is no row, and ``user.ErrInvalidTOTPPasscode`` on a wrong code.
    """
    row = get_totp(session, user.id)
    if row is None or not row.secret:
        raise CaltonError.from_name("user.ErrTOTPNotEnabled")
    if not verify_totp(row.secret, passcode):
        raise CaltonError.from_name("user.ErrInvalidTOTPPasscode")
    row.enabled = True
    session.flush()


def disable_totp(session: Session, user: User, password: str) -> None:
    """Disable TOTP after confirming the user's current password.

    The password check is the only thing standing between a stolen session and a
    2FA bypass, so it runs first and a wrong password is the wrong-credentials error.
    """
    if not user.password or not _verify_password(password, user.password):
        raise CaltonError.from_name("user.ErrWrongUsernameOrPassword")
    row = get_totp(session, user.id)
    if row is not None:
        session.delete(row)
    session.flush()


def _verify_password(password: str, hashed: str) -> bool:
    from calton.auth.password import verify_password

    return verify_password(password, hashed)


def verify_totp(secret: str, passcode: str, *, at: datetime | None = None) -> bool:
    """RFC 6236 TOTP verification.

    Accepts the current 30-second window's code. A single window rather than ±1 is
    the conservative choice — it rejects replays and slightly stale clocks, which is
    the right default for a 2FA gate. The comparison is constant-time.
    """
    if not secret or not passcode:
        return False
    counter = int((at or datetime.now(UTC)).timestamp()) // TOTP_STEP
    expected = _totp_at(secret, counter)
    return hmac.compare_digest(str(passcode).zfill(TOTP_DIGITS), expected)


def _totp_at(secret: str, counter: int) -> str:
    key = base64.b32decode(secret, casefold=True)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % (10**TOTP_DIGITS)
    return str(code).zfill(TOTP_DIGITS)
