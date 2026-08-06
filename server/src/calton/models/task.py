"""``tasks`` (``pkg/models/tasks.go``) — the only soft-deleting table upstream."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import REAL, Index, Integer, Select, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from calton.db.base import Base, created_column, updated_column
from calton.db.softdelete import SoftDeleteMixin, soft_delete_query
from calton.db.types import CaltonBoolean, CaltonDateTime


class Task(SoftDeleteMixin, Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    done: Mapped[bool | None] = mapped_column(CaltonBoolean, nullable=True)
    done_at: Mapped[datetime] = mapped_column(CaltonDateTime, nullable=True)
    due_date: Mapped[datetime] = mapped_column(CaltonDateTime, nullable=True)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False)
    repeat_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    repeat_mode: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    priority: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_date: Mapped[datetime] = mapped_column(CaltonDateTime, nullable=True)
    end_date: Mapped[datetime] = mapped_column(CaltonDateTime, nullable=True)
    hex_color: Mapped[str | None] = mapped_column(Text, nullable=True)
    percent_done: Mapped[float | None] = mapped_column(REAL, nullable=True)
    # Per-project counter, not a global id. UQE_tasks_tasks_project_index enforces it;
    # T18 allocates it as max(index)+1 and retries on conflict.
    index: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    uid: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_image_attachment_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, server_default=text("0")
    )
    created: Mapped[datetime] = created_column()
    updated: Mapped[datetime] = updated_column()
    # deleted_at sits here, before created_by_id, and comes from SoftDeleteMixin.
    created_by_id: Mapped[int] = mapped_column(Integer, nullable=False, sort_order=2)

    __table_args__ = (
        Index("UQE_tasks_id", "id", unique=True),
        Index("UQE_tasks_tasks_project_index", "project_id", "index", unique=True),
        Index("IDX_tasks_done", "done"),
        Index("IDX_tasks_done_at", "done_at"),
        Index("IDX_tasks_project_id", "project_id"),
        Index("IDX_tasks_start_date", "start_date"),
        Index("IDX_tasks_end_date", "end_date"),
        Index("IDX_tasks_due_date", "due_date"),
        Index("IDX_tasks_repeat_after", "repeat_after"),
        # Go declares these ids AUTOINCREMENT, which stops SQLite reusing the ids of
        # deleted rows. Without it id allocation diverges after a delete.
        {"sqlite_autoincrement": True},
    )


def base_task_query(*, include_deleted: bool = False) -> Select[tuple[Task]]:
    """The only sanctioned way to query tasks.

    A bare ``select(Task)`` skips the soft-delete filter and hands deleted tasks to
    clients without failing anywhere visible. Use this instead.
    """
    return soft_delete_query(Task, include_deleted=include_deleted)
