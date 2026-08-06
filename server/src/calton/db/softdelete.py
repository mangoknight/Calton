"""Soft deletion.

``tasks`` is the only table upstream that soft-deletes: ``pkg/models/tasks.go:142`` tags
``DeletedAt`` with xorm's ``deleted``, which makes every xorm query add
``deleted_at IS NULL`` for free. SQLAlchemy gives us no such thing, so the filter has to
be applied deliberately — and a query that forgets it hands deleted tasks back to MCP
clients without failing anywhere.

**Never write ``select(Task)`` directly.** Go through ``base_task_query()``, which T03
adds next to the ``Task`` model as a one-line wrapper over :func:`soft_delete_query`.

``deleted_at`` is NULL exactly when the row is live, which is the same condition that
makes the column vanish from JSON (Go tags it ``omitzero``, and NULL reads back as the
zero time — see :mod:`calton.db.types`).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Select, select
from sqlalchemy.orm import Mapped, mapped_column

from calton.db.types import CaltonDateTime


class SoftDeleteMixin:
    """``deleted_at DATETIME NULL`` plus its index, matching ``IDX_tasks_deleted_at``."""

    # sort_order pushes the column past the model's own ones; columns inherited from a
    # mixin otherwise lead, and the schema diff compares column order.
    deleted_at: Mapped[datetime] = mapped_column(
        CaltonDateTime, nullable=True, index=True, default=None, sort_order=1
    )


def soft_delete_query[M: SoftDeleteMixin](
    model: type[M], *, include_deleted: bool = False
) -> Select[tuple[M]]:
    """``select(model)`` with deleted rows filtered out unless asked for."""
    statement = select(model)
    if include_deleted:
        return statement
    return statement.where(model.deleted_at.is_(None))
