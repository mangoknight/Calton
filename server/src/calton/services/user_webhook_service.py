"""User-level webhooks (``/user/settings/webhooks``).

The same ``Webhook`` table serves both project webhooks and user-level ones: a row
with ``project_id == 0`` and ``user_id`` set is a user-level webhook. Project
webhooks live under ``/projects/{project}/webhooks`` (see ``webhook_service``);
these five routes are the user-level half, scoped entirely to the caller's own
``user_id``.

Events are validated against the same catalogue the project webhooks use, so a
subscriber cannot register for a name nothing will ever fire.
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from calton.core.errors import CaltonError, ValidationError
from calton.db.base import utcnow
from calton.events.catalogue import WEBHOOK_EVENTS
from calton.models import Webhook

#: Re-exported so the events route does not carry a second copy of the literal —
#: see ``webhook_service.AVAILABLE_EVENTS`` for why a single source matters.
AVAILABLE_EVENTS: tuple[str, ...] = WEBHOOK_EVENTS


def _validate_events(events: list[str]) -> None:
    unknown = [name for name in events if name not in AVAILABLE_EVENTS]
    if unknown:
        raise ValidationError(["events"])


def list_user_webhooks(session: Session, user_id: int) -> list[Webhook]:
    return list(
        session.scalars(
            select(Webhook)
            .where(Webhook.user_id == user_id, Webhook.project_id == 0)
            .order_by(Webhook.id)
        ).all()
    )


def create_user_webhook(
    session: Session,
    user_id: int,
    *,
    target_url: str,
    events: list[str],
    secret: str = "",
    basic_auth_user: str = "",
    basic_auth_password: str = "",
) -> Webhook:
    if not target_url:
        raise ValidationError(["target_url"])
    if not events:
        raise ValidationError(["events"])
    _validate_events(events)
    webhook = Webhook(
        target_url=target_url,
        events=json.dumps(events),
        project_id=0,
        user_id=user_id,
        secret=secret or None,
        basic_auth_user=basic_auth_user or None,
        basic_auth_password=basic_auth_password or None,
        created_by_id=user_id,
        created=utcnow(),
        updated=utcnow(),
    )
    session.add(webhook)
    session.flush()
    return webhook


def load_user_webhook(session: Session, user_id: int, webhook_id: int) -> Webhook:
    """A webhook owned by this user, or 404. The ``user_id`` clause is the IDOR
    guard: without it a user could act on another user's webhook by id."""
    row = session.scalars(
        select(Webhook).where(
            Webhook.id == webhook_id, Webhook.user_id == user_id, Webhook.project_id == 0
        )
    ).one_or_none()
    if row is None:
        raise CaltonError(code=0, message="This webhook does not exist.", http_status=404)
    return row


def update_user_webhook(
    session: Session, user_id: int, webhook_id: int, *, target_url: str, events: list[str]
) -> Webhook:
    """Like the project update: ``target_url`` is required and then ignored, only
    ``events`` is written. Matches the measured upstream behaviour so a
    read-modify-write client does not accidentally clobber the URL."""
    if not target_url:
        raise ValidationError(["target_url"])
    if not events:
        raise ValidationError(["events"])
    _validate_events(events)
    webhook = load_user_webhook(session, user_id, webhook_id)
    webhook.events = json.dumps(events)
    webhook.updated = utcnow()
    session.flush()
    return webhook


def delete_user_webhook(session: Session, user_id: int, webhook_id: int) -> None:
    webhook = load_user_webhook(session, user_id, webhook_id)
    session.delete(webhook)
    session.flush()
