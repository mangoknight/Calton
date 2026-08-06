"""Password management, email confirmation, account deletion and data export.

These are the ``other.user`` sub-routes the Go server files under ``pkg/user``: the
password change/reset flow, email confirmation, the scheduled-deletion trio and the
data-export trio. They are deliberately thin — the heavy lifting (bcrypt, JWT) lives in
``auth.password`` and ``auth.jwt`` — and they keep the route handlers free of ORM logic.

Conventions that match the rest of the codebase:

* errors come back as ``CaltonError.from_name(...)`` so they render with the upstream
  code/message/status triple;
* tokens are random URL-safe strings stored in ``user_tokens``; the ``kind`` column
  distinguishes the two flows (constants below);
* email is never actually sent — the reset/confirm token is logged, matching the
  "no SMTP" constraint in the design.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from calton.auth.password import hash_password, verify_password
from calton.core.errors import CaltonError
from calton.models.user import User, UserToken

logger = logging.getLogger("calton.user_account")

# --- user_tokens.kind ---------------------------------------------------------
#
# Upstream packs the token's purpose into the ``kind`` int. There is no shared enum
# in the Python side, so the two values are pinned here and the routes import them.
TOKEN_KIND_EMAIL_CONFIRM = 1
TOKEN_KIND_PASSWORD_RESET = 2

# --- user.status --------------------------------------------------------------
#
# Vikunja's status enum is active=0, email-confirm=1, disabled=2. Account deletion
# upstream is a hard delete; the design asks for a soft delete instead, which we
# represent by flipping the row to the disabled status. Keeping the named constant
# makes the intent readable at the call site.
USER_STATUS_ACTIVE = 0
USER_STATUS_EMAIL_CONFIRM = 1
USER_STATUS_DISABLED = 2
USER_STATUS_DELETED = USER_STATUS_DISABLED

#: ``POST /user/deletion/request`` schedules the cutover this far out. Measured
#: upstream (pkg/user/user.go: RequestDeletion) is 30 days, and the deletion cron
#: only fires past that point — so the value is load-bearing for the "cancel" route
#: being meaningful rather than a no-op.
DELETION_GRACE_PERIOD = timedelta(days=30)


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def change_password(session: Session, user: User, *, old_password: str, new_password: str) -> None:
    """Verify ``old_password`` then store ``new_password`` hashed with bcrypt.

    Empty old/new passwords reproduce the upstream 412s rather than a generic 400, and a
    wrong old password reproduces the login failure (403/1011) — the same error the
    login route raises, for the same reason.
    """
    if not old_password:
        raise CaltonError.from_name("user.ErrEmptyOldPassword")
    if not new_password:
        raise CaltonError.from_name("user.ErrEmptyNewPassword")

    if not user.password or not verify_password(old_password, user.password):
        raise CaltonError.from_name("user.ErrWrongUsernameOrPassword")

    user.password = hash_password(new_password)
    session.flush()


def request_password_reset(session: Session, *, email_or_username: str) -> None:
    """Issue a password-reset token for the user matching ``email_or_username``.

    Returns silently (200) when no user matches, so the endpoint cannot be used to
    enumerate accounts — the same behaviour upstream exposes. The token is logged rather
    than emailed, per the no-SMTP constraint.
    """
    user = _find_by_email_or_username(session, email_or_username)
    if user is None:
        return

    token = _new_token()
    session.add(UserToken(user_id=user.id, token=token, kind=TOKEN_KIND_PASSWORD_RESET))
    session.flush()
    logger.info("password reset token for user %s: %s", user.username, token)


def reset_password(session: Session, *, token: str, new_password: str) -> None:
    """Consume a password-reset token and set the matching user's new password."""
    if not token:
        raise CaltonError.from_name("user.ErrNoPasswordResetToken")
    if not new_password:
        raise CaltonError.from_name("user.ErrEmptyNewPassword")

    row = _find_token(session, token, TOKEN_KIND_PASSWORD_RESET)
    if row is None:
        raise CaltonError.from_name("user.ErrInvalidPasswordResetToken")

    user = session.get(User, row.user_id)
    if user is None:
        # The token points at a user that no longer exists. Treat the token as invalid
        # rather than crashing — the row is orphaned, not the request malformed.
        raise CaltonError.from_name("user.ErrInvalidPasswordResetToken")

    user.password = hash_password(new_password)
    session.delete(row)
    session.flush()


def confirm_email(session: Session, *, token: str) -> None:
    """Consume an email-confirm token and activate the matching user."""
    if not token:
        raise CaltonError.from_name("user.ErrInvalidEmailConfirmToken")

    row = _find_token(session, token, TOKEN_KIND_EMAIL_CONFIRM)
    if row is None:
        raise CaltonError.from_name("user.ErrInvalidEmailConfirmToken")

    user = session.get(User, row.user_id)
    if user is None:
        raise CaltonError.from_name("user.ErrInvalidEmailConfirmToken")

    user.status = USER_STATUS_ACTIVE
    session.delete(row)
    session.flush()


def request_deletion(session: Session, user: User) -> None:
    """Schedule the account for deletion ``DELETION_GRACE_PERIOD`` from now."""
    user.deletion_scheduled_at = datetime.now(UTC) + DELETION_GRACE_PERIOD
    session.flush()


def cancel_deletion(session: Session, user: User) -> None:
    """Clear a scheduled deletion. A no-op when none was scheduled."""
    user.deletion_scheduled_at = None  # type: ignore
    session.flush()


def confirm_deletion(session: Session, user: User) -> None:
    """Execute the deletion now: soft-delete by flipping status to disabled."""
    user.status = USER_STATUS_DELETED
    user.deletion_scheduled_at = None  # type: ignore
    session.flush()


def export_status(user: User) -> dict[str, object]:
    """``GET /user/export`` — whether an export is in flight and the file id if any.

    The flag is derived from ``export_file_id``: a non-null id means a finished export
    is downloadable, and the route reports ``export_in_progress=False`` in that case.
    There is no separate "in progress" column, so an in-flight export is signalled by
    the request route setting ``export_file_id`` to a sentinel only for the duration of
    the (stubbed) build — which never happens here, so the flag is always False.
    """
    file_id = user.export_file_id
    return {
        "export_in_progress": False,
        "export_file_id": file_id,
    }


def request_export(session: Session, user: User) -> None:
    """Record that the user asked for an export. The build itself is stubbed."""
    logger.info("data export requested for user %s", user.username)
    # No background worker exists; nothing to persist beyond the request log.
    session.flush()


def _find_by_email_or_username(session: Session, value: str) -> User | None:
    return session.scalars(
        select(User).where((User.email == value) | (User.username == value))
    ).one_or_none()


def _find_token(session: Session, token: str, kind: int) -> UserToken | None:
    return session.scalars(
        select(UserToken).where(UserToken.token == token, UserToken.kind == kind)
    ).one_or_none()
