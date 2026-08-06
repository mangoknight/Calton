"""``task_relations`` (``pkg/models/task_relation.go``).

``relation_kind`` is a plain string in the database and on the wire — one of eleven
values (subtask, parenttask, related, duplicateof, duplicates, blocking, blocked,
precedes, follows, copiedfrom, copiedto). No int/string conversion, unlike ``view_kind``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from calton.db.base import Base, created_column


class TaskRelation(Base):
    __tablename__ = "task_relations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False)
    other_task_id: Mapped[int] = mapped_column(Integer, nullable=False)
    relation_kind: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created: Mapped[datetime] = created_column()

    __table_args__ = (
        Index("UQE_task_relations_id", "id", unique=True),
        {"sqlite_autoincrement": True},
    )
