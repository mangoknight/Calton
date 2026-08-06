"""``labels`` and ``label_tasks`` (``pkg/models/label.go``)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from calton.db.base import Base, created_column, updated_column


class Label(Base):
    __tablename__ = "labels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Stored without a leading '#'.
    hex_color: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created: Mapped[datetime] = created_column()
    updated: Mapped[datetime] = updated_column()

    __table_args__ = (
        Index("UQE_labels_id", "id", unique=True),
        # Go declares these ids AUTOINCREMENT, which stops SQLite reusing the ids of
        # deleted rows. Without it id allocation diverges after a delete.
        {"sqlite_autoincrement": True},
    )


class LabelTask(Base):
    __tablename__ = "label_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False)
    label_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created: Mapped[datetime] = created_column()

    __table_args__ = (
        Index("UQE_label_tasks_id", "id", unique=True),
        Index("IDX_label_tasks_task_id", "task_id"),
        Index("IDX_label_tasks_label_id", "label_id"),
        # Go declares these ids AUTOINCREMENT, which stops SQLite reusing the ids of
        # deleted rows. Without it id allocation diverges after a delete.
        {"sqlite_autoincrement": True},
    )
