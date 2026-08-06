"""``saved_filters``, ``favorites`` and ``subscriptions``."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from calton.db.base import Base, created_column, updated_column
from calton.db.types import CaltonBoolean


class SavedFilter(Base):
    """A stored filter, addressed through a pseudo project id.

    A filter and a project id map to each other as ``id * -1 - 1``, so filter 1 is
    project -2. Project -1 is the Favorites pseudo project, which is why the test for
    "is this a saved filter" is ``project_id < -1`` rather than ``<= -1`` (design R6).
    """

    __tablename__ = "saved_filters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # The filter DSL string, stored as written.
    filters: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    is_favorite: Mapped[bool | None] = mapped_column(
        CaltonBoolean, nullable=True, server_default=text("0")
    )
    created: Mapped[datetime] = created_column()
    updated: Mapped[datetime] = updated_column()

    __table_args__ = (
        Index("UQE_saved_filters_id", "id", unique=True),
        Index("IDX_saved_filters_owner_id", "owner_id"),
        {"sqlite_autoincrement": True},
    )


class Favorite(Base):
    """The only table here with a real composite primary key and no id column."""

    __tablename__ = "favorites"

    entity_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[int] = mapped_column(Integer, primary_key=True)


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_type: Mapped[int] = mapped_column(Integer, nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created: Mapped[datetime] = created_column()

    __table_args__ = (
        Index("UQE_subscriptions_id", "id", unique=True),
        Index("IDX_subscriptions_entity_type", "entity_type"),
        Index("IDX_subscriptions_entity_id", "entity_id"),
        Index("IDX_subscriptions_user_id", "user_id"),
        {"sqlite_autoincrement": True},
    )
