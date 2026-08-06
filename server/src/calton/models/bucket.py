"""``buckets`` (``pkg/models/kanban.go``)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import REAL, Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from calton.db.base import Base, created_column, updated_column


class Bucket(Base):
    __tablename__ = "buckets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    # Buckets hang off a view, not a project.
    project_view_id: Mapped[int] = mapped_column(Integer, nullable=False)
    limit: Mapped[int | None] = mapped_column(Integer, nullable=True, server_default=text("0"))
    position: Mapped[float | None] = mapped_column(REAL, nullable=True)
    created: Mapped[datetime] = created_column()
    updated: Mapped[datetime] = updated_column()
    created_by_id: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index("UQE_buckets_id", "id", unique=True),
        # Go declares these ids AUTOINCREMENT, which stops SQLite reusing the ids of
        # deleted rows. Without it id allocation diverges after a delete.
        {"sqlite_autoincrement": True},
    )
