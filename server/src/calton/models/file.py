"""``files`` (``pkg/files/file.go``).

Unlike every other table here, ``created`` is nullable and there is no ``updated``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from calton.db.base import Base, created_column


class File(Base):
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    mime: Mapped[str | None] = mapped_column(Text, nullable=True)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    created: Mapped[datetime] = created_column(nullable=True)
    created_by_id: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index("UQE_files_id", "id", unique=True),
        # Go declares these ids AUTOINCREMENT, which stops SQLite reusing the ids of
        # deleted rows. Without it id allocation diverges after a delete.
        {"sqlite_autoincrement": True},
    )
