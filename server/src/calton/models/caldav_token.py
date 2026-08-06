"""``caldav_tokens`` — CalDAV access tokens per user."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from calton.db.base import Base, created_column


class CalDAVToken(Base):
    __tablename__ = "caldav_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    token: Mapped[str] = mapped_column(Text, nullable=False)
    created: Mapped[datetime] = created_column()

    __table_args__ = (
        Index("UQE_caldav_tokens_id", "id", unique=True),
        Index("IDX_caldav_tokens_user_id", "user_id"),
        {"sqlite_autoincrement": True},
    )
