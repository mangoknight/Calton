"""``task_positions`` and ``task_buckets``.

Neither table has a primary key upstream — xorm created them keyed only by a unique
index. SQLAlchemy needs a key to map a class, so ``__mapper_args__`` supplies one at the
mapper level, which leaves the emitted DDL free of a ``PRIMARY KEY`` clause and matching
the Go schema.
"""

from __future__ import annotations

from sqlalchemy import REAL, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column

from calton.db.base import Base


class TaskPosition(Base):
    __tablename__ = "task_positions"

    task_id: Mapped[int] = mapped_column(Integer, nullable=False)
    project_view_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # float64 upstream: inserting between two tasks takes the midpoint, so a whole
    # column never needs renumbering (design §4).
    position: Mapped[float] = mapped_column(REAL, nullable=False)

    # RUF012 wants a ClassVar annotation; SQLAlchemy declares this as an instance
    # attribute on DeclarativeBase, so annotating it makes mypy complain instead.
    __mapper_args__ = {"primary_key": [task_id, project_view_id]}  # noqa: RUF012
    __table_args__ = (
        Index("UQE_task_positions_task_view", "task_id", "project_view_id", unique=True),
        Index("IDX_task_positions_project_view_id", "project_view_id"),
        Index("IDX_task_positions_task_id", "task_id"),
        Index("IDX_task_positions_view_position", "project_view_id", "position"),
    )


class TaskBucket(Base):
    __tablename__ = "task_buckets"

    bucket_id: Mapped[int] = mapped_column(Integer, nullable=False)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False)
    project_view_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # RUF012 wants a ClassVar annotation; SQLAlchemy declares this as an instance
    # attribute on DeclarativeBase, so annotating it makes mypy complain instead.
    __mapper_args__ = {"primary_key": [task_id, project_view_id]}  # noqa: RUF012
    __table_args__ = (
        Index("UQE_task_buckets_task_view", "task_id", "project_view_id", unique=True),
        Index("IDX_task_buckets_bucket_id", "bucket_id"),
        Index("IDX_task_buckets_task_id", "task_id"),
        Index("IDX_task_buckets_project_view_id", "project_view_id"),
    )
