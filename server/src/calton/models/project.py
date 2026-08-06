"""``projects`` (``pkg/models/project.go``)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import REAL, Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from calton.db.base import Base, created_column, updated_column
from calton.db.types import CaltonBoolean


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    identifier: Mapped[str | None] = mapped_column(Text, nullable=True)
    hex_color: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # Nullable and also meaningfully 0: omitted means "do not move", an explicit 0 means
    # "move to the top level" (design R9). Both states have to be storable.
    parent_project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_archived: Mapped[bool] = mapped_column(
        CaltonBoolean, nullable=False, server_default=text("0")
    )
    background_file_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    background_blur_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    position: Mapped[float | None] = mapped_column(REAL, nullable=True)
    created: Mapped[datetime] = created_column()
    updated: Mapped[datetime] = updated_column()

    __table_args__ = (
        Index("UQE_projects_id", "id", unique=True),
        Index("IDX_projects_owner_id", "owner_id"),
        Index("IDX_projects_parent_project_id", "parent_project_id"),
        # Go declares these ids AUTOINCREMENT, which stops SQLite reusing the ids of
        # deleted rows. Without it id allocation diverges after a delete.
        {"sqlite_autoincrement": True},
    )
