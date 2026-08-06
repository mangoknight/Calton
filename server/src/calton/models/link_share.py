"""``link_shares`` — the per-project share links.

⚠️ **Schema only for now.** ``PUT /projects/{id}/shares`` is implemented; the sibling
read and delete routes are not, so nothing queries this table yet beyond the create.

It has to exist as a Calton model regardless of which routes are built, because the
parity harness's per-case reset loads every table in ``seed.TABLE_ORDER`` into **both**
servers and Calton's ``PATCH /test/{table}`` rejects any name outside
``Base.metadata.tables``. Adding the table to that tuple without a model here answers
``500 unknown table link_shares`` and takes the entire reset down — 104 cases reporting
"the per-case reset did not load cleanly", which is what happened when ``notifications``
was added in the wrong order.

``hash`` is uniquely indexed and generated server-side; ``password`` is bcrypt when set
and is never returned. ``sharing_type`` is 1 without a password and 2 with one — it is
derived from whether a password was supplied, not sent by the client.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from calton.db.base import Base, created_column, updated_column


class LinkShare(Base):
    __tablename__ = "link_shares"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hash: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False)
    permission: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    sharing_type: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    password: Mapped[str | None] = mapped_column(Text, nullable=True)
    shared_by_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created: Mapped[datetime] = created_column()
    updated: Mapped[datetime] = updated_column()

    __table_args__ = (
        Index("UQE_link_shares_id", "id", unique=True),
        Index("UQE_link_shares_hash", "hash", unique=True),
        Index("IDX_link_shares_sharing_type", "sharing_type"),
        Index("IDX_link_shares_shared_by_id", "shared_by_id"),
        Index("IDX_link_shares_permission", "permission"),
        {"sqlite_autoincrement": True},
    )
