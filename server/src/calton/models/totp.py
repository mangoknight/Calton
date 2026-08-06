"""``totp`` — TOTP 2FA settings per user."""

from __future__ import annotations

from sqlalchemy import Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from calton.db.base import Base


class TOTP(Base):
    __tablename__ = "totp"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    secret: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(default=False, server_default="0")

    __table_args__ = (
        Index("UQE_totp_user_id", "user_id", unique=True),
        {"sqlite_autoincrement": True},
    )
