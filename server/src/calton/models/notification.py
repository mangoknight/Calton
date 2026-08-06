"""``notifications`` — the in-app notification rows.

⚠️ **Schema only. No endpoint reads this table yet.**

It exists because the parity harness's per-case reset loads every table in
``seed.TABLE_ORDER`` into **both** servers, and Calton's ``PATCH /test/{table}`` rejects
any name that is not in ``Base.metadata.tables``. Adding ``notifications`` to that tuple
without this model answers ``500 unknown table notifications`` on the Calton side and
takes the whole reset down with it — which is not a subtle failure: every case after it
reports "the per-case reset did not load cleanly", 104 of them in one run.

So the criterion for seeding a table is narrower than "something reads these rows".
It is **"both servers can hold them, and something reads them"** — the first half is a
precondition of the harness working at all, and it is easy to miss because the Go side
accepts the table happily.

``GET /notifications`` is not implemented. Until it is, these rows are read on the Go
side only, and any corpus case covering them has to pin the two sides separately rather
than compare them.

Column notes: ``notification`` is the event payload as a JSON string (xorm's ``JSON``
tag on a TEXT column), and ``read_at`` is nullable — the zero time is what an unread row
serialises as, not NULL, so the read path will have to convert.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from calton.db.base import Base, created_column
from calton.db.types import CaltonDateTime


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    notifiable_id: Mapped[int] = mapped_column(Integer, nullable=False)
    notification: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    read_at: Mapped[datetime | None] = mapped_column(CaltonDateTime, nullable=True)
    created: Mapped[datetime] = created_column()

    __table_args__ = (
        Index("UQE_notifications_id", "id", unique=True),
        Index("IDX_notifications_project_id", "project_id"),
        Index("IDX_notifications_name", "name"),
        {"sqlite_autoincrement": True},
    )
