"""``sessions`` — what a refresh cookie redeems against.

Not one of the 24 tables in the Phase 1 list (design §1.3), and deliberately so:
that list was drawn up around the resource endpoints. T14 needs this table
regardless, because every user JWT carries a ``sid`` claim naming a row here and
``POST /user/token/refresh`` is only implementable against a store. Issuing a
refresh cookie with nothing behind it would be the half-built variant the design
warns about, so the table comes with the feature.

Columns are transcribed from the schema the Go binary builds — see
``tests/unit/test_session_schema.py``, which diffs this against a recorded dump
rather than against a reading of the Go model.

⚠️ ``token_hash`` is the SHA-256 of the cookie's **ASCII text**, not of the bytes
its 256 hex digits encode (measured: ``go_jwt.json.session_store``). Hex-decoding
first produces a different digest and every refresh fails.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from calton.db.base import Base, created_column
from calton.db.types import CaltonBoolean, CaltonDateTime


class Session(Base):
    __tablename__ = "sessions"

    # A UUID string, not an integer: it is what the JWT's `sid` claim carries.
    id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    device_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_long_session: Mapped[bool] = mapped_column(
        CaltonBoolean, nullable=False, server_default=text("0")
    )
    oidcid_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    oidc_provider_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_active: Mapped[datetime] = mapped_column(CaltonDateTime, nullable=False)
    created: Mapped[datetime] = created_column()

    __table_args__ = (
        Index("UQE_sessions_id", "id", unique=True),
        Index("UQE_sessions_token_hash", "token_hash", unique=True),
        Index("IDX_sessions_user_id", "user_id"),
    )
