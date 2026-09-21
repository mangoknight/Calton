"""A task has to show up on its project's board, not only in its list.

Found on a live instance: project 2 listed three open tasks while its Kanban rendered
three columns with ``count: 0``. Nothing had failed — ``create_task`` simply never wrote
the ``task_buckets`` row upstream writes in ``setTasksInBucketInViews``, and a manual
Kanban lists tasks *through* that table. Every request answered 200 the whole time, which
is why the only test that can catch a regression is one that reads the board back.

Driven over HTTP through ``create_app``: the project is created through the API too, so
its four default views and three default buckets are the real ones.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from calton.auth.deps import get_auth_subject
from calton.db.base import Base
from calton.db.session import session_factory
from calton.main import create_app
from calton.models import User
from calton.models.task_position import TaskBucket, TaskPosition
from calton.services import task_placement

ALICE = 900
EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def engine() -> Iterator[Engine]:
    built = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(built)
    yield built
    built.dispose()


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    factory = session_factory(engine)
    with factory() as session:
        session.add(User(id=ALICE, username="alice", created=EPOCH, updated=EPOCH))
        session.commit()
    return factory


@pytest.fixture
def client(engine: Engine, sessions: sessionmaker[Session]) -> TestClient:
    application: FastAPI = create_app(engine=engine)
    application.state.session_factory = sessions

    @application.middleware("http")
    async def _stub_auth(request, call_next):  # type: ignore[no-untyped-def]
        header = request.headers.get("x-test-user")
        if header:
            request.state.auth = SimpleNamespace(id=int(header))
        return await call_next(request)

    application.dependency_overrides[get_auth_subject] = lambda: None
    return TestClient(
        application, headers={"X-Test-User": str(ALICE)}, raise_server_exceptions=False
    )


def _ok(response: Any) -> Any:
    assert response.status_code in (200, 201), response.text
    return response.json()


@pytest.fixture
def board(client: TestClient) -> dict[str, Any]:
    """A fresh project, with its Kanban view and that view's bucket ids by role."""
    project = _ok(client.put("/api/v1/projects", json={"title": "placement"}))
    views = _ok(client.get(f"/api/v1/projects/{project['id']}/views"))
    kanban = next(view for view in views if view["view_kind"] == "kanban")
    assert kanban["default_bucket_id"], "a new project's Kanban should name a default bucket"
    assert kanban["done_bucket_id"], "a new project's Kanban should name a done bucket"
    return {
        "project": project["id"],
        "view": kanban["id"],
        "default": kanban["default_bucket_id"],
        "done": kanban["done_bucket_id"],
    }


def _columns(client: TestClient, board: dict[str, Any]) -> dict[int, list[int]]:
    """``{bucket_id: [task ids]}`` as the board endpoint reports it."""
    buckets = _ok(client.get(f"/api/v1/projects/{board['project']}/views/{board['view']}/tasks"))
    return {bucket["id"]: [task["id"] for task in bucket.get("tasks") or []] for bucket in buckets}


def _create(client: TestClient, board: dict[str, Any], **fields: Any) -> int:
    body = {"title": "t", **fields}
    return int(_ok(client.put(f"/api/v1/projects/{board['project']}/tasks", json=body))["id"])


def _update(client: TestClient, task_id: int, **fields: Any) -> None:
    current = _ok(client.get(f"/api/v1/tasks/{task_id}"))
    _ok(client.post(f"/api/v1/tasks/{task_id}", json={**current, **fields}))


class TestCreate:
    def test_a_new_task_appears_in_the_default_bucket(
        self, client: TestClient, board: dict[str, Any]
    ) -> None:
        task = _create(client, board)
        assert _columns(client, board)[board["default"]] == [task]

    def test_a_task_created_done_goes_straight_to_the_done_bucket(
        self, client: TestClient, board: dict[str, Any]
    ) -> None:
        task = _create(client, board, done=True)
        columns = _columns(client, board)
        assert columns[board["done"]] == [task]
        assert columns[board["default"]] == []

    def test_every_view_gets_a_position_and_the_newest_task_is_on_top(
        self, client: TestClient, board: dict[str, Any], sessions: sessionmaker[Session]
    ) -> None:
        first = _create(client, board)
        second = _create(client, board)
        with sessions() as session:
            views = {row.project_view_id for row in session.scalars(select(TaskPosition))}
            assert len(views) == 4, "list, gantt, table and kanban each need a position"
            position = {
                row.task_id: row.position
                for row in session.scalars(
                    select(TaskPosition).where(TaskPosition.project_view_id == board["view"])
                )
            }
        assert position[second] < position[first]


class TestDoneFlip:
    def test_completing_moves_the_card_to_done_and_reopening_brings_it_back(
        self, client: TestClient, board: dict[str, Any]
    ) -> None:
        task = _create(client, board)

        _update(client, task, done=True)
        assert _columns(client, board)[board["done"]] == [task]

        _update(client, task, done=False)
        columns = _columns(client, board)
        assert columns[board["default"]] == [task]
        assert columns[board["done"]] == []

    def test_reopening_a_card_that_is_not_in_done_leaves_it_where_the_user_put_it(
        self, client: TestClient, board: dict[str, Any]
    ) -> None:
        """Upstream's "do nothing" branch: only the done bucket is ever vacated."""
        columns = _columns(client, board)
        middle = next(b for b in columns if b not in (board["default"], board["done"]))
        task = _create(client, board)
        _ok(
            client.post(
                f"/api/v1/projects/{board['project']}/views/{board['view']}/buckets/{middle}/tasks",
                json={"task_id": task},
            )
        )

        _update(client, task, priority=3)  # done stays False: not a flip at all
        assert _columns(client, board)[middle] == [task]


class TestBackfill:
    def test_it_places_orphans_by_done_state_and_never_moves_a_placed_card(
        self, client: TestClient, board: dict[str, Any], sessions: sessionmaker[Session]
    ) -> None:
        open_task = _create(client, board)
        done_task = _create(client, board, done=True)
        kept = _create(client, board)
        middle = next(
            b for b in _columns(client, board) if b not in (board["default"], board["done"])
        )
        _ok(
            client.post(
                f"/api/v1/projects/{board['project']}/views/{board['view']}/buckets/{middle}/tasks",
                json={"task_id": kept},
            )
        )

        # Recreate the pre-fix data: the two orphans lose their rows, `kept` keeps its own.
        with sessions() as session:
            session.execute(delete(TaskBucket).where(TaskBucket.task_id.in_([open_task, done_task])))
            session.commit()
        assert _columns(client, board)[board["default"]] == []

        with sessions() as session:
            added = task_placement.backfill_project(session, board["project"])
            session.commit()

        assert added == 2
        columns = _columns(client, board)
        assert columns[board["default"]] == [open_task]
        assert columns[board["done"]] == [done_task]
        assert columns[middle] == [kept]

        with sessions() as session:
            assert task_placement.backfill_project(session, board["project"]) == 0
