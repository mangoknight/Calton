"""``webhooks`` — the project-level webhook targets.

One table serves two resources upstream: a row with ``project_id`` set is a project
webhook (the four routes under ``/projects/{id}/webhooks``), and a row with ``user_id``
set is a user-level one (``/user/settings/webhooks``, not implemented). Both columns are
nullable and upstream treats them as mutually exclusive, but nothing in the schema
enforces that — so a query for a project's webhooks must filter on ``project_id`` rather
than assume ``user_id`` is null.

``events`` is a JSON array in a TEXT column (xorm's ``JSON`` tag), not a delimited
string.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from calton.db.base import Base, created_column, updated_column


class Webhook(Base):
    __tablename__ = "webhooks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    target_url: Mapped[str] = mapped_column(Text, nullable=False)
    #: The JSON array, stored as text. Held as a string here and (de)serialised in the
    #: service, so nothing depends on a dialect-specific JSON type.
    events: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    basic_auth_user: Mapped[str | None] = mapped_column(Text, nullable=True)
    basic_auth_password: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created: Mapped[datetime] = created_column()
    updated: Mapped[datetime] = updated_column()

    __table_args__ = (
        Index("UQE_webhooks_id", "id", unique=True),
        Index("IDX_webhooks_project_id", "project_id"),
        Index("IDX_webhooks_user_id", "user_id"),
        {"sqlite_autoincrement": True},
    )
