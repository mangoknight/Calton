"""``reactions`` — emoji reactions on tasks, comments, and other entities."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from calton.db.base import Base, created_column


class Reaction(Base):
    __tablename__ = "reactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    created: Mapped[datetime] = created_column()

    __table_args__ = (
        UniqueConstraint("kind", "entity_id", "user_id", "value", name="UQE_reactions"),
        Index("IDX_reactions_entity", "kind", "entity_id"),
        Index("IDX_reactions_user_id", "user_id"),
        {"sqlite_autoincrement": True},
    )
