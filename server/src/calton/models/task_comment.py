"""``task_comments`` and ``task_attachments``.

Both have nullable timestamps, unlike most tables here.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from calton.db.base import Base, created_column, updated_column


class TaskComment(Base):
    __tablename__ = "task_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    author_id: Mapped[int] = mapped_column(Integer, nullable=False)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created: Mapped[datetime] = created_column(nullable=True)
    updated: Mapped[datetime] = updated_column(nullable=True)

    __table_args__ = (
        Index("UQE_task_comments_id", "id", unique=True),
        Index("IDX_task_comments_task_id", "task_id"),
        {"sqlite_autoincrement": True},
    )


class TaskAttachment(Base):
    __tablename__ = "task_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # The bytes live in `files`; this table only links them to a task.
    file_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created: Mapped[datetime] = created_column(nullable=True)

    __table_args__ = (
        Index("UQE_task_attachments_id", "id", unique=True),
        {"sqlite_autoincrement": True},
    )
