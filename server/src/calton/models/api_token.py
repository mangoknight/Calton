"""``api_tokens`` (``pkg/models/api_tokens.go``).

The two indexes here are both load-bearing for T15's verification path: lookup goes
through ``token_last_eight`` to find candidate rows, then each candidate is re-hashed
with **its own** salt and compared. ``token_salt`` holds the ten salt characters as
written, not hex-decoded, and the hash covers the full plaintext including the ``tk_``
prefix.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from calton.db.base import Base, created_column
from calton.db.types import CaltonDateTime


class APIToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    token_salt: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    token_last_eight: Mapped[str] = mapped_column(Text, nullable=False)
    # The granted (group, action) pairs, as JSON.
    permissions: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(CaltonDateTime, nullable=False)
    created: Mapped[datetime] = created_column()
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index("UQE_api_tokens_id", "id", unique=True),
        Index("UQE_api_tokens_token_hash", "token_hash", unique=True),
        Index("IDX_api_tokens_token_last_eight", "token_last_eight"),
        {"sqlite_autoincrement": True},
    )
