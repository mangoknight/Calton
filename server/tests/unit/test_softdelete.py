"""Deleted rows must be invisible by default.

Uses a stand-in model rather than ``Task``, which lands in T03. ``base_task_query`` is a
one-line wrapper over the mechanism exercised here and gets its own test there.
"""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Mapped, Session, mapped_column

from calton.config import DatabaseSettings, Settings
from calton.db.base import Base
from calton.db.session import build_engine, session_factory
from calton.db.softdelete import SoftDeleteMixin, soft_delete_query
from calton.models import Task, base_task_query


class Widget(SoftDeleteMixin, Base):
    __tablename__ = "widgets"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column()


@pytest.fixture
def session() -> Iterator[Session]:
    engine: Engine = build_engine(Settings(database=DatabaseSettings(path=":memory:")))
    Base.metadata.create_all(engine, tables=[Base.metadata.tables["widgets"]])

    with session_factory(engine)() as opened:
        opened.add_all(
            [
                Widget(id=1, title="live"),
                Widget(id=2, title="also live"),
                Widget(id=3, title="deleted", deleted_at=datetime(2026, 8, 3, tzinfo=UTC)),
            ]
        )
        opened.commit()
        yield opened


def test_deleted_rows_are_excluded_by_default(session: Session) -> None:
    titles = [widget.title for widget in session.scalars(soft_delete_query(Widget))]

    assert titles == ["live", "also live"]


def test_include_deleted_returns_everything(session: Session) -> None:
    rows = session.scalars(soft_delete_query(Widget, include_deleted=True)).all()

    assert len(rows) == 3


def test_deleted_row_is_still_in_the_table(session: Session) -> None:
    """Soft delete, not delete — the row stays put."""
    assert session.get(Widget, 3) is not None


def test_filter_composes_with_further_conditions(session: Session) -> None:
    query = soft_delete_query(Widget).where(Widget.title.like("%live%"))

    assert len(session.scalars(query).all()) == 2


def test_live_rows_read_back_deleted_at_as_the_zero_time(session: Session) -> None:
    """NULL and the Go zero time are the same value; that is what makes ``omitzero`` work."""
    from calton.db.types import ZERO_TIME

    live = session.get(Widget, 1)
    assert live is not None
    assert live.deleted_at == ZERO_TIME


class TestBaseTaskQuery:
    """The Task-specific entry point deferred here from T02, now that Task exists."""

    @pytest.fixture
    def tasks(self) -> Iterator[Session]:
        engine = build_engine(Settings(database=DatabaseSettings(path=":memory:")))
        Base.metadata.create_all(engine)

        with session_factory(engine)() as opened:
            opened.add_all(
                [
                    Task(id=1, title="live", project_id=1, index=1, created_by_id=1),
                    Task(
                        id=2,
                        title="deleted",
                        project_id=1,
                        index=2,
                        created_by_id=1,
                        deleted_at=datetime(2026, 8, 3, tzinfo=UTC),
                    ),
                ]
            )
            opened.commit()
            yield opened

    def test_deleted_tasks_are_hidden(self, tasks: Session) -> None:
        assert [task.title for task in tasks.scalars(base_task_query())] == ["live"]

    def test_include_deleted_returns_them(self, tasks: Session) -> None:
        assert len(tasks.scalars(base_task_query(include_deleted=True)).all()) == 2

    def test_the_deleted_row_is_still_stored(self, tasks: Session) -> None:
        assert tasks.get(Task, 2) is not None
