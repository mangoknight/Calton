"""Declarative base and shared column mixins.

Index and constraint names follow xorm's, because the T03/T09 schema diff compares
``sqlite_master`` against a database built by the Go binary: ``IDX_<table>_<columns>``
for plain indexes, ``UQE_<table>_<columns>`` for unique ones. Where xorm used an
explicit name (``UQE_tasks_tasks_project_index``), the model states it explicitly.

Note that the Go schema has **no foreign key constraints at all** — xorm only indexes
the columns. Models therefore declare relationships without emitting ``FOREIGN KEY``
clauses, so the generated DDL stays comparable.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from calton.db.types import CaltonDateTime

NAMING_CONVENTION = {
    "ix": "IDX_%(table_name)s_%(column_0_N_name)s",
    "uq": "UQE_%(table_name)s_%(column_0_N_name)s",
    "ck": "CK_%(table_name)s_%(constraint_name)s",
    "fk": "FK_%(table_name)s_%(column_0_N_name)s",
    "pk": "PK_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def utcnow() -> datetime:
    return datetime.now(UTC)


def created_column(nullable: bool = False) -> Mapped[datetime]:
    """xorm's ``created`` tag: set once on insert.

    Deliberately not a mixin. Upstream's column order varies — ``project_views`` lists
    ``updated`` before ``created``, ``tasks`` puts both in the middle — and the schema
    diff compares column order, so each model spells its timestamps out in place.
    """
    return mapped_column(CaltonDateTime, nullable=nullable, default=utcnow)


def updated_column(nullable: bool = False) -> Mapped[datetime]:
    """xorm's ``updated`` tag: refreshed on insert and on every update."""
    return mapped_column(CaltonDateTime, nullable=nullable, default=utcnow, onupdate=utcnow)
