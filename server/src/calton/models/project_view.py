"""``project_views`` (``pkg/models/project_view.go``).

``view_kind`` and ``bucket_configuration_mode`` are integers in the database but strings
on the wire (``list``/``gantt``/``table``/``kanban``, ``none``/``manual``/``filter``).
That conversion is T17's; the columns stay integers here. Note ``priority`` and
``repeat_mode`` elsewhere are int on the wire too — the mapping is per field, not global
(design §1.5).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import REAL, Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from calton.db.base import Base, created_column, updated_column


class ProjectView(Base):
    __tablename__ = "project_views"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False)
    view_kind: Mapped[int] = mapped_column(Integer, nullable=False)
    filter: Mapped[str | None] = mapped_column(Text, nullable=True, server_default=text("null"))
    position: Mapped[float | None] = mapped_column(REAL, nullable=True)
    bucket_configuration_mode: Mapped[int | None] = mapped_column(
        Integer, nullable=True, server_default=text("0")
    )
    bucket_configuration: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_bucket_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    done_bucket_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Upstream lists updated before created on this table only.
    updated: Mapped[datetime] = updated_column()
    created: Mapped[datetime] = created_column()

    __table_args__ = (
        Index("UQE_project_views_id", "id", unique=True),
        Index("IDX_project_views_default_bucket_id", "default_bucket_id"),
        Index("IDX_project_views_done_bucket_id", "done_bucket_id"),
        Index("IDX_project_views_project_id", "project_id"),
        # Go declares these ids AUTOINCREMENT, which stops SQLite reusing the ids of
        # deleted rows. Without it id allocation diverges after a delete.
        {"sqlite_autoincrement": True},
    )
