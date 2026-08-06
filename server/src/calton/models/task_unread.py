"""``task_unread_statuses`` — tracks which tasks each user has read."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, Integer
from sqlalchemy.orm import Mapped, mapped_column

from calton.db.base import Base, created_column
from calton.db.types import CaltonDateTime


class TaskUnreadStatus(Base):
    __tablename__ = "task_unread_statuses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False)
    read_at: Mapped[datetime] = mapped_column(CaltonDateTime, nullable=True)
    created: Mapped[datetime] = created_column()

    __table_args__ = (
        Index("UQE_task_unread_statuses_id", "id", unique=True),
        Index("IDX_task_unread_user_id", "user_id"),
        Index("IDX_task_unread_task_id", "task_id"),
        {"sqlite_autoincrement": True},
    )
