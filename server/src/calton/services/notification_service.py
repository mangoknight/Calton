"""Reading notifications, marking them all read, and the per-notification toggle.

Three operations. ``GET /notifications`` lists the caller's own; ``POST /notifications``
marks them all read; ``POST /notifications/{id}`` toggles a single one's read state. The
per-notification toggle is **not in upstream's API-token route registry** — no token can
reach it (measured: 401 code 11 with every notification permission granted) — so its route
is mounted but deliberately not registered, which leaves it JWT-only. The other two are
token-grantable.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Select, func, select, update
from sqlalchemy.orm import Session

from calton.core.errors import CaltonError
from calton.core.policy import ForbiddenError
from calton.db.base import utcnow
from calton.db.types import ZERO_TIME
from calton.models import Notification
from calton.schemas.notification import NotificationMarkRead, NotificationRead


def notification_view(row: Notification) -> NotificationRead:
    """The wire shape for ``GET /notifications``. ``notification`` is parsed from JSON text."""
    payload: dict[str, Any] | None = None
    if row.notification:
        parsed = json.loads(row.notification)
        payload = parsed if isinstance(parsed, dict) else None
    return NotificationRead(
        id=row.id,
        name=row.name,
        notification=payload,
        read_at=row.read_at or ZERO_TIME,
        created=row.created,
    )


def own_notifications_query(user_id: int) -> Select[tuple[Notification]]:
    """The caller's notifications, **newest first**.

    ``id`` descending, not ascending and not by ``created``: the seed's rows happen to
    agree on all three, so only a fixture where they disagree could tell them apart. Taken
    from the measured order rather than assumed.
    """
    return (
        select(Notification)
        .where(Notification.notifiable_id == user_id)
        .order_by(Notification.id.desc())
    )


def count_own(session: Session, user_id: int) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.notifiable_id == user_id)
        )
        or 0
    )


def mark_all_read(session: Session, user_id: int) -> None:
    """``POST /notifications``. Answers ``{"message": "success"}`` whatever happens.

    ⚠️ **Every row is rewritten, not only the unread ones.** Upstream's UPDATE carries no
    ``read_at IS NULL`` predicate, so calling this twice moves ``read_at`` forward again —
    measured, one second apart, and the timestamps differ. An implementation that skipped
    already-read rows is indistinguishable on the first call and diverges on the second,
    which is the call nobody thinks to test.

    Scoped to the caller, and a caller with no notifications still gets a 200.
    """
    session.execute(
        update(Notification).where(Notification.notifiable_id == user_id).values(read_at=utcnow())
    )
    session.flush()


def mark_one_read(session: Session, notification_id: int, user_id: int) -> NotificationMarkRead:
    """``POST /notifications/{id}`` — toggle one notification's read state.

    Returns the notification with a ``read`` boolean (``read_at`` is set). The route is
    JWT-only upstream: no API token reaches it, so its route is mounted but deliberately
    not registered — see the router's note.

    Order is the measured one: a missing notification is 404, and one that exists but
    belongs to somebody else is 403. Scoping the lookup by ``notifiable_id`` instead would
    fold the two into a single 404 and let a caller distinguish "no such notification"
    from "somebody else's" by timing, which upstream does not.
    """
    row = session.get(Notification, notification_id)
    if row is None:
        raise CaltonError.from_name("models.ErrNotificationDoesNotExist")
    if row.notifiable_id != user_id:
        raise ForbiddenError()

    # Toggle: a null read_at becomes now, a set one becomes null again. ``mark_all_read``
    # rewrites every row unconditionally; the per-id route is the only one that unreads.
    row.read_at = None if row.read_at is not None else utcnow()
    session.flush()
    session.commit()
    session.refresh(row)

    payload: dict[str, Any] | None = None
    if row.notification:
        parsed = json.loads(row.notification)
        payload = parsed if isinstance(parsed, dict) else None

    return NotificationMarkRead(
        id=row.id,
        name=row.name,
        notification=payload,
        read=row.read_at is not None,
        read_at=row.read_at or ZERO_TIME,
        created=row.created,
    )
