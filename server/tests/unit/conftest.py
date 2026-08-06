"""Fixtures for the task tests.

The seed mirrors ``harness/corpus-incoming/seed/overlay/tasks.yml`` — same ids, same
titles, same soft-deleted row — so a case written here and a case written in the parity
corpus describe the same world. When the corpus seed changes, this changes with it.

Authentication is stubbed by a middleware that reads ``X-Test-User``. T14/T15 will replace
it with the real JWT and API-token middleware; the contract between them and the routers
is only that ``request.state.auth`` ends up holding an object with an ``id``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from calton.auth.deps import get_auth_subject
from calton.db.base import Base
from calton.db.session import session_factory
from calton.main import create_app
from calton.models import Project, Task, User

ALICE = 900
BOB = 901

#: The host project. Its identifier is deliberately empty so task identifiers degrade to
#: "#<index>" — the form most tests assert.
PROJECT = 920
#: Bob's project. Alice holds nothing on it, which is what separates 403 from 404.
BOBS_PROJECT = 903


@pytest.fixture
def engine() -> Iterator[Engine]:
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    built = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(built)
    yield built
    built.dispose()


def _seed(session: Session) -> None:
    epoch = datetime(2026, 2, 1, tzinfo=UTC)
    session.add_all(
        [
            User(id=ALICE, username="alice", created=epoch, updated=epoch),
            User(id=BOB, username="bob", created=epoch, updated=epoch),
            Project(id=PROJECT, title="TaskFixture", identifier="", owner_id=ALICE),
            Project(id=BOBS_PROJECT, title="Bobs", identifier="", owner_id=BOB),
        ]
    )
    session.add_all(
        [
            Task(
                id=920,
                project_id=PROJECT,
                index=1,
                title="T-empty",
                created_by_id=ALICE,
                done=False,
            ),
            # Soft-deleted. Holds index 2, which must stay reserved and stay unreadable.
            Task(
                id=921,
                project_id=PROJECT,
                index=2,
                title="T-soft-deleted",
                created_by_id=ALICE,
                done=False,
                deleted_at=epoch,
            ),
            Task(
                id=922,
                project_id=PROJECT,
                index=3,
                title="T-full",
                created_by_id=ALICE,
                done=False,
                description="<p>full task</p>",
                due_date=datetime(2026, 3, 1, 12, tzinfo=UTC),
                start_date=datetime(2026, 2, 25, 8, tzinfo=UTC),
                end_date=datetime(2026, 3, 2, 8, tzinfo=UTC),
                priority=4,
                percent_done=0.5,
                hex_color="aabbcc",
                repeat_after=86400,
            ),
            Task(
                id=923,
                project_id=PROJECT,
                index=4,
                title="T-done",
                created_by_id=ALICE,
                done=True,
                done_at=datetime(2026, 2, 10, 10, tzinfo=UTC),
            ),
            Task(
                id=927,
                project_id=BOBS_PROJECT,
                index=1,
                title="T-bobs-private",
                created_by_id=BOB,
                done=False,
            ),
        ]
    )
    session.commit()


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    factory = session_factory(engine)
    with factory() as session:
        _seed(session)
    return factory


@pytest.fixture
def session(sessions: sessionmaker[Session]) -> Iterator[Session]:
    with sessions() as opened:
        yield opened


@pytest.fixture
def app(engine: Engine, sessions: sessionmaker[Session]) -> FastAPI:
    application = create_app(engine=engine)
    application.state.session_factory = sessions

    @application.middleware("http")
    async def _stub_auth(request, call_next):  # type: ignore[no-untyped-def]
        header = request.headers.get("x-test-user")
        if header:
            request.state.auth = SimpleNamespace(id=int(header))
        return await call_next(request)

    # The resource routers now hang off `get_auth_subject`, which resolves a real
    # credential. These tests act as a user via the X-Test-User header instead of
    # logging in, so the real resolver would 401 every one of them. Overriding the
    # dependency — rather than loosening it in `deps.py` — keeps the production
    # path single-entry: nothing in `src/` accepts a pre-set `request.state.auth`.
    #
    # ⚠️ This override means these tests do NOT cover the auth wiring itself. That
    # is what TestTheAuthChainIsWired in test_api_tokens.py is for; it builds an app
    # with no override and logs in for real.
    application.dependency_overrides[get_auth_subject] = lambda: None

    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """Authenticated as alice. Pass ``headers={"X-Test-User": "901"}`` to act as bob."""
    return TestClient(app, headers={"X-Test-User": str(ALICE)}, raise_server_exceptions=False)


# --------------------------------------------------------------------------------------
# Collection fixture, mirroring harness seed/overlay/viewshape.yml (project 970).
#
# Two design choices in here are load-bearing and must survive edits:
#   * view 974 is view_kind=List (0) but bucket_configuration_mode=manual (1). It is the
#     only thing separating "branch on mode" from "branch on view_kind", because every
#     automatically created view has the two agreeing.
#   * the three tasks' priority (1/5/3) runs *opposite* to their position (10/20/30), so
#     "position asc", "priority desc" and "priority asc" are three different orders. Make
#     them agree and the sort-override test passes no matter what the code does.
# --------------------------------------------------------------------------------------

VIEW_PROJECT = 970


@pytest.fixture
def collection_seed(sessions: sessionmaker[Session]) -> None:
    from calton.models import Bucket, ProjectView, TaskBucket, TaskPosition

    epoch = datetime(2026, 2, 1, tzinfo=UTC)
    with sessions() as session:
        session.add(Project(id=VIEW_PROJECT, title="ViewShape", identifier="", owner_id=ALICE))
        session.add_all(
            [
                # filter deliberately left empty: this project tests *shape*, and a
                # content filter on top would entangle the two.
                ProjectView(
                    id=970,
                    project_id=VIEW_PROJECT,
                    title="List",
                    view_kind=0,
                    position=100,
                    bucket_configuration_mode=0,
                ),
                ProjectView(
                    id=973,
                    project_id=VIEW_PROJECT,
                    title="Kanban",
                    view_kind=3,
                    position=400,
                    bucket_configuration_mode=1,
                ),
                # The mismatched one.
                ProjectView(
                    id=974,
                    project_id=VIEW_PROJECT,
                    title="ListWithBuckets",
                    view_kind=0,
                    position=500,
                    bucket_configuration_mode=1,
                ),
            ]
        )
        session.add_all(
            [
                Bucket(
                    id=970,
                    project_view_id=973,
                    title="To-Do",
                    position=100,
                    created_by_id=ALICE,
                    limit=0,
                ),
                Bucket(
                    id=971,
                    project_view_id=973,
                    title="Doing",
                    position=200,
                    created_by_id=ALICE,
                    limit=0,
                ),
                Bucket(
                    id=972,
                    project_view_id=973,
                    title="Done",
                    position=300,
                    created_by_id=ALICE,
                    limit=0,
                ),
                Bucket(
                    id=974,
                    project_view_id=974,
                    title="Only",
                    position=100,
                    created_by_id=ALICE,
                    limit=0,
                ),
            ]
        )
        for offset, (task_id, priority, position) in enumerate(
            [(9700, 1, 10.0), (9701, 5, 20.0), (9702, 3, 30.0)]
        ):
            session.add(
                Task(
                    id=task_id,
                    project_id=VIEW_PROJECT,
                    index=offset + 1,
                    title=f"T-{task_id}",
                    created_by_id=ALICE,
                    done=False,
                    priority=priority,
                    created=epoch,
                    updated=epoch,
                )
            )
            session.add(TaskBucket(task_id=task_id, bucket_id=970, project_view_id=973))
            session.add(TaskBucket(task_id=task_id, bucket_id=974, project_view_id=974))
            session.add(TaskPosition(task_id=task_id, project_view_id=973, position=position))
            session.add(TaskPosition(task_id=task_id, project_view_id=970, position=position))
            session.add(TaskPosition(task_id=task_id, project_view_id=974, position=position))

        # Bucket 971 holds more tasks than one page, which is what makes "count is the
        # bucket total, len(tasks) is the page" two distinguishable numbers.
        for n in range(60):
            task_id = 9710 + n
            session.add(
                Task(
                    id=task_id,
                    project_id=VIEW_PROJECT,
                    index=100 + n,
                    title=f"D-{n}",
                    created_by_id=ALICE,
                    done=False,
                    created=epoch,
                    updated=epoch,
                )
            )
            session.add(TaskBucket(task_id=task_id, bucket_id=971, project_view_id=973))
            # Positions start past the first three so the flat view lists 9700-9702
            # before these; the corpus fixture orders them the same way.
            session.add(TaskPosition(task_id=task_id, project_view_id=973, position=100.0 + n))
            session.add(TaskPosition(task_id=task_id, project_view_id=970, position=100.0 + n))
        # A task whose *title* contains the text "bucket_id". It exists so the
        # substring-fallback test has something to return: upstream decides the flat
        # fallback with strings.Contains on the raw filter, so a filter that merely
        # mentions the text — without filtering on the field — still flattens, and an
        # empty result could not tell the two shapes apart.
        session.add(
            Task(
                id=9799,
                project_id=VIEW_PROJECT,
                index=999,
                title="mentions bucket_id here",
                created_by_id=ALICE,
                done=False,
                created=epoch,
                updated=epoch,
            )
        )
        # Positioned last in every view so it never displaces the ordering the other
        # cases assert. It is in no bucket, so the bucket branch never sees it.
        for view_id in (970, 973, 974):
            session.add(TaskPosition(task_id=9799, project_view_id=view_id, position=9999.0))
        session.commit()


@pytest.fixture
def expand_seed(sessions: sessionmaker[Session]) -> None:
    """A parent with two children and a grandchild, plus comments and a bucket.

    Mirrors the fixture the reference-server measurements were taken against: two roots
    (the parent and a lonely task) so ``per_page=2`` admits both, and a grandchild so the
    descendant walk has to recurse rather than fetch one level.
    """
    from calton.models import Bucket, ProjectView, TaskBucket, TaskComment, TaskRelation

    epoch = datetime(2026, 2, 1, tzinfo=UTC)
    with sessions() as session:
        session.add(Project(id=980, title="Expand", identifier="", owner_id=ALICE))
        session.add(
            ProjectView(
                id=980,
                project_id=980,
                title="Kanban",
                view_kind=3,
                position=100,
                bucket_configuration_mode=1,
            )
        )
        session.add(
            Bucket(
                id=980,
                project_view_id=980,
                title="To-Do",
                position=100,
                created_by_id=ALICE,
                limit=0,
            )
        )
        names = {"parent": 9800, "childA": 9801, "childB": 9802, "grandchild": 9803, "lonely": 9804}
        for index, (name, task_id) in enumerate(names.items(), start=1):
            session.add(
                Task(
                    id=task_id,
                    project_id=980,
                    index=index,
                    title=name,
                    created_by_id=ALICE,
                    done=False,
                    created=epoch,
                    updated=epoch,
                )
            )
            session.add(TaskBucket(task_id=task_id, bucket_id=980, project_view_id=980))

        # Stored in both directions, as upstream does: the root test reads the
        # "parenttask" rows and the descendant walk reads the "subtask" rows.
        for parent, child in (("parent", "childA"), ("parent", "childB"), ("childA", "grandchild")):
            session.add(
                TaskRelation(
                    task_id=names[parent],
                    other_task_id=names[child],
                    relation_kind="subtask",
                    created_by_id=ALICE,
                )
            )
            session.add(
                TaskRelation(
                    task_id=names[child],
                    other_task_id=names[parent],
                    relation_kind="parenttask",
                    created_by_id=ALICE,
                )
            )

        # 55 comments so the 50-cap and the untruncated count are two different numbers.
        for n in range(55):
            session.add(
                TaskComment(
                    id=9000 + n,
                    task_id=names["parent"],
                    comment=f"comment-{n:03d}",
                    author_id=ALICE,
                    created=epoch,
                    updated=epoch,
                )
            )
        session.commit()
