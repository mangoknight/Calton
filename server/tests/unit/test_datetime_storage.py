"""How a datetime reaches the database, checked against observed Go behaviour.

``tests/unit/test_datetime_serialization.py`` covers the JSON side. This covers storage,
which is where the dangerous failure lives: if a zero time were ever written as the
literal string ``0001-01-01 00:00:00`` instead of NULL, then ``deleted_at IS NULL`` would
stop matching and every task carrying a zero value would quietly vanish from the API —
with nothing failing anywhere.

The expected values in ``tests/fixtures/go_datetime.json`` were captured from the
reference server, not derived from the Go source. Regenerate with
``scripts/build_go_reference.sh``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from calton.config import DatabaseSettings, Settings
from calton.db.base import Base
from calton.db.session import build_engine, session_factory
from calton.db.types import ZERO_TIME, format_rfc3339
from calton.models import Task, base_task_query

FIXTURE = json.loads(
    (Path(__file__).resolve().parent.parent / "fixtures" / "go_datetime.json").read_text()
)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = build_engine(Settings(database=DatabaseSettings(path=":memory:")))
    Base.metadata.create_all(engine)
    with session_factory(engine)() as opened:
        yield opened


def _raw(session: Session, column: str, task_id: int) -> Any:
    return session.execute(
        text(f"SELECT {column} FROM tasks WHERE id = :id"), {"id": task_id}
    ).scalar()


class TestZeroTimeWriteDirection:
    """The write half of the NULL/zero equivalence. Reading was already covered."""

    def test_a_zero_value_is_stored_as_null(self, session: Session) -> None:
        session.add(
            Task(id=1, title="t", project_id=1, index=1, created_by_id=1, due_date=ZERO_TIME)
        )
        session.commit()

        assert _raw(session, "due_date", 1) is None

    def test_an_explicitly_zero_deleted_at_leaves_the_row_visible(self, session: Session) -> None:
        """The failure this guards against is silent disappearance, not an error."""
        session.add(
            Task(id=1, title="t", project_id=1, index=1, created_by_id=1, deleted_at=ZERO_TIME)
        )
        session.commit()

        assert _raw(session, "deleted_at", 1) is None
        assert [task.id for task in session.scalars(base_task_query())] == [1]

    def test_none_is_also_stored_as_null(self, session: Session) -> None:
        session.add(Task(id=1, title="t", project_id=1, index=1, created_by_id=1, due_date=None))
        session.commit()

        assert _raw(session, "due_date", 1) is None

    def test_a_zero_value_reads_back_as_the_zero_time(self, session: Session) -> None:
        session.add(
            Task(id=1, title="t", project_id=1, index=1, created_by_id=1, due_date=ZERO_TIME)
        )
        session.commit()
        session.expire_all()

        task = session.get(Task, 1)
        assert task is not None
        assert task.due_date == ZERO_TIME
        assert format_rfc3339(task.due_date) == FIXTURE["zero"]["json"]


class TestObservedRoundTrips:
    """Each case is a value that was actually sent to, stored by and read from Go."""

    @pytest.mark.parametrize("case", FIXTURE["round_trips"], ids=lambda c: c["sent"])
    def test_storage_matches_go(self, session: Session, case: dict[str, str]) -> None:
        sent = datetime.fromisoformat(case["sent"].replace("Z", "+00:00"))

        session.add(Task(id=1, title="t", project_id=1, index=1, created_by_id=1, due_date=sent))
        session.commit()

        assert _raw(session, "due_date", 1) == case["stored"]

    @pytest.mark.parametrize("case", FIXTURE["round_trips"], ids=lambda c: c["sent"])
    def test_what_comes_back_matches_go(self, session: Session, case: dict[str, str]) -> None:
        sent = datetime.fromisoformat(case["sent"].replace("Z", "+00:00"))

        session.add(Task(id=1, title="t", project_id=1, index=1, created_by_id=1, due_date=sent))
        session.commit()
        session.expire_all()

        task = session.get(Task, 1)
        assert task is not None
        assert format_rfc3339(task.due_date) == case["returned"]

    def test_sub_second_precision_is_discarded(self, session: Session) -> None:
        """Called out separately because it is the one that surprises people.

        A create response therefore reports microseconds that a later read will not —
        the same object's `created` differs between the two. Do not write a test that
        captures a timestamp from a write and asserts it against a subsequent read.
        """
        session.add(
            Task(
                id=1,
                title="t",
                project_id=1,
                index=1,
                created_by_id=1,
                due_date=datetime(2026, 12, 31, 23, 59, 59, 123456, tzinfo=UTC),
            )
        )
        session.commit()

        assert _raw(session, "due_date", 1) == "2026-12-31 23:59:59"


def test_stored_values_are_text_in_the_documented_format(session: Session) -> None:
    session.add(
        Task(
            id=1,
            title="t",
            project_id=1,
            index=1,
            created_by_id=1,
            due_date=datetime(2026, 8, 3, 10, 30, tzinfo=UTC),
        )
    )
    session.commit()

    stored_type = session.execute(text("SELECT typeof(due_date) FROM tasks WHERE id = 1")).scalar()

    assert stored_type == FIXTURE["storage"]["sqlite_type"]
    assert datetime.strptime(_raw(session, "due_date", 1), FIXTURE["storage"]["format"])


def test_naive_datetimes_are_written_as_utc(session: Session) -> None:
    """Naive input is read as UTC rather than local time, matching SetTZDatabase(GMT)."""
    session.add(
        Task(
            id=1,
            title="t",
            project_id=1,
            index=1,
            created_by_id=1,
            due_date=datetime(2026, 8, 3, 10, 30),
        )
    )
    session.commit()

    assert _raw(session, "due_date", 1) == "2026-08-03 10:30:00"
