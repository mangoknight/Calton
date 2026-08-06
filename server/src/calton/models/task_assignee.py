"""``task_assignees`` (``pkg/models/task_assignees.go``).

The table is ``task_assignees``, not ``task_assignee`` — a name worth double-checking,
since a typo here only surfaces as a parity failure.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, Integer
from sqlalchemy.orm import Mapped, mapped_column

from calton.db.base import Base, created_column


class TaskAssignee(Base):
    __tablename__ = "task_assignees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created: Mapped[datetime] = created_column()

    __table_args__ = (
        Index("UQE_task_assignees_id", "id", unique=True),
        Index("IDX_task_assignees_task_id", "task_id"),
        Index("IDX_task_assignees_user_id", "user_id"),
        # Go declares these ids AUTOINCREMENT, which stops SQLite reusing the ids of
        # deleted rows. Without it id allocation diverges after a delete.
        {"sqlite_autoincrement": True},
    )
