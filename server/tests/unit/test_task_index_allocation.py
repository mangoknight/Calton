"""Per-project index allocation.

``index`` is not the primary key: it counts from 1 within each project and is what
``identifier`` and the ``by-index`` endpoint are built from. Two tasks sharing one inside a
project make two different tasks answer to the same identifier, so the interesting
question is not "does it count up" but "does it stay unique when it is raced".

These tests use a file-backed SQLite database rather than the shared in-memory one:
``StaticPool`` pins every session to a single connection, which serialises writers and
would make the concurrency test prove nothing at all.
"""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from calton.db.base import Base
from calton.db.session import session_factory
from calton.models import Project, User
from calton.models.task import Task, base_task_query
from calton.schemas.task import TaskWrite
from calton.services import task_service

OWNER = 900


@pytest.fixture
def sessions(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine: Engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'tasks.db'}")

    @event.listens_for(engine, "connect")
    def _pragmas(connection, _record):  # type: ignore[no-untyped-def]
        cursor = connection.cursor()
        # Same settings the app uses: without a busy timeout the racing writers below
        # fail with "database is locked" instead of exercising the retry.
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA busy_timeout = 5000")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = session_factory(engine)
    epoch = datetime(2026, 2, 1, tzinfo=UTC)
    with factory() as session:
        session.add(User(id=OWNER, username="alice", created=epoch, updated=epoch))
        session.add(Project(id=1, title="One", identifier="", owner_id=OWNER))
        session.add(Project(id=2, title="Two", identifier="ABC", owner_id=OWNER))
        session.commit()
    yield factory
    engine.dispose()


def _create(session: Session, project_id: int, title: str, **extra: Any) -> int:
    view = task_service.create_task(
        session, project_id=project_id, data=TaskWrite(title=title, **extra), user_id=OWNER
    )
    return view.index


def test_indexes_start_at_one_and_count_up(sessions: sessionmaker[Session]) -> None:
    with sessions() as session:
        assert [_create(session, 1, f"t{n}") for n in range(3)] == [1, 2, 3]


def test_each_project_counts_independently(sessions: sessionmaker[Session]) -> None:
    """A global counter would give the second project 4, 5, 6 — and identifiers that skip."""
    with sessions() as session:
        [_create(session, 1, f"a{n}") for n in range(3)]

        assert [_create(session, 2, f"b{n}") for n in range(3)] == [1, 2, 3]


def test_a_soft_deleted_task_keeps_its_index_reserved(sessions: sessionmaker[Session]) -> None:
    """calculateNextTaskIndex scans Unscoped, deliberately.

    Reusing the index of a deleted task would give a new task the deleted one's
    identifier, and by-index would then be ambiguous for anything holding a link to it.
    """
    with sessions() as session:
        first = _create(session, 1, "doomed")
        task = session.scalars(
            base_task_query().where(Task.index == first, Task.project_id == 1)
        ).one()
        task_service.delete_task(session, task_id=task.id)

        assert _create(session, 1, "next") == first + 1


def test_a_requested_index_is_honoured_when_free(sessions: sessionmaker[Session]) -> None:
    """setNewTaskIndexes keeps a preset index; the file importer relies on it."""
    with sessions() as session:
        assert _create(session, 1, "preset", index=50) == 50
        assert _create(session, 1, "after") == 51


def test_ten_concurrent_creates_get_ten_distinct_indexes(
    sessions: sessionmaker[Session],
) -> None:
    """★ The load-bearing one. max(index) + 1 is not atomic.

    Ten threads read the same high-water mark, compute the same index and race to insert
    it. Uniqueness comes from UQE_tasks_tasks_project_index rejecting the losers and the
    retry recomputing — so this fails loudly if the constraint is dropped from the model
    or the IntegrityError is swallowed.
    """

    def create(n: int) -> int:
        with sessions() as session:
            return _create(session, 1, f"racer-{n}")

    with ThreadPoolExecutor(max_workers=10) as pool:
        indexes = sorted(pool.map(create, range(10)))

    assert indexes == list(range(1, 11))


def test_the_retry_is_what_makes_that_work(sessions: sessionmaker[Session]) -> None:
    """Mutation check: force a collision and show the allocator recovers rather than 500s.

    ``_next_index`` is pinned to a number that is already taken, which is exactly the
    state a losing racer finds itself in. A first attempt that gives up here would raise
    IntegrityError; the retry re-reads and lands on the next free number.
    """
    with sessions() as session:
        _create(session, 1, "incumbent")  # takes index 1

        calls = {"n": 0}
        real = task_service._next_index

        def collide_once(inner: Session, project_id: int) -> int:
            calls["n"] += 1
            return 1 if calls["n"] == 1 else real(inner, project_id)

        task_service._next_index = collide_once  # type: ignore[assignment]
        try:
            allocated = _create(session, 1, "challenger")
        finally:
            task_service._next_index = real

        assert calls["n"] >= 2, "the collision never happened, so nothing was proven"
        assert allocated == 2
