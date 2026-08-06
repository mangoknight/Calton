"""Notification wire shapes.

``notification`` is the event payload, and it comes out as a **JSON object** while the
column stores a JSON **string** (xorm's ``JSON`` tag on TEXT). Emitting the raw string is
the natural thing for a serializer that mirrors the column, and it is a different type on
the wire — every client would have to parse it a second time.

⚠️ The **list** items have no ``read`` field; ``POST /notifications/{id}`` (not
implemented — no API token can reach it) does return one. Same resource, two shapes. Only
``read_at`` carries the state here, and an unread row is the **zero time**, not null.
"""

from __future__ import annotations

from typing import Any

from calton.db.types import ZERO_TIME, Timestamp
from calton.schemas.base import CaltonModel


class NotificationRead(CaltonModel):
    """One entry of ``GET /notifications``. Five keys, and no ``read`` among them."""

    id: int
    notification: dict[str, Any] | None = None
    name: str = ""
    read_at: Timestamp = ZERO_TIME
    created: Timestamp = ZERO_TIME


class NotificationMarkRead(CaltonModel):
    """The body of ``POST /notifications/{id}`` — the per-notification (un-)read toggle.

    Same resource as :class:`NotificationRead` but a different shape: it carries a
    ``read`` boolean (``read_at`` is not null) alongside ``read_at`` itself, which the
    list view omits. The route is JWT-only upstream — no API token can reach it — so this
    shape is only ever produced by that one handler.
    """

    id: int
    notification: dict[str, Any] | None = None
    name: str = ""
    read: bool = False
    read_at: Timestamp = ZERO_TIME
    created: Timestamp = ZERO_TIME
