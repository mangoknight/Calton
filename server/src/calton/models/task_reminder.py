"""``task_reminders`` (``pkg/models/task_reminder.go``). No ``updated`` column."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from calton.db.base import Base, created_column
from calton.db.types import CaltonDateTime


class TaskReminder(Base):
    __tablename__ = "task_reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False)
    reminder: Mapped[datetime] = mapped_column(CaltonDateTime, nullable=False)
    created: Mapped[datetime] = created_column()
    # Set when the reminder is relative to another date field rather than absolute.
    relative_period: Mapped[int | None] = mapped_column(Integer, nullable=True)
    relative_to: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("UQE_task_reminders_id", "id", unique=True),
        Index("IDX_task_reminders_task_id", "task_id"),
        Index("IDX_task_reminders_reminder", "reminder"),
        {"sqlite_autoincrement": True},
    )
