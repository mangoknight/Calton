"""The notification endpoints, driven over HTTP through ``create_app``.

Two routes, and **which two** is the first measured result here. The card asked for
``GET /notifications`` and ``POST /notifications/{id}``; measurement inverted both halves:
the per-id toggle is absent from upstream's API-token registry (401/11 with every
notification permission granted), and the reachable write is the collection-level mark-all
that was not on the card at all.

The world mirrors the seed's own notification rows, which is the point — this resource's
fixtures only started loading this phase, and before that every measurement of it had to
manufacture rows by triggering a real event:

===  =========================================================================
1,2  alice's, unread
3    bob's, unread — so "only your own" is discriminating in both directions
===  =========================================================================

carol has none, which is a third case: an empty list is not an error.
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
from calton.db.types import ZERO_TIME
from calton.main import create_app
from calton.models import Notification, User

ALICE, BOB, CAROL = 900, 901, 902
EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


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
    session.add_all(
        [
            User(id=ALICE, username="alice", created=EPOCH, updated=EPOCH),
            User(id=BOB, username="bob", created=EPOCH, updated=EPOCH),
            User(id=CAROL, username="carol", created=EPOCH, updated=EPOCH),
        ]
    )
    session.add_all(
        [
            Notification(
                id=1,
                notifiable_id=ALICE,
                notification='{"test":"one"}',
                name="test.notification",
                subject_id=1,
                read_at=None,
                created=EPOCH,
            ),
            Notification(
                id=2,
                notifiable_id=ALICE,
                notification='{"test":"two"}',
                name="test.notification",
                subject_id=2,
                read_at=None,
                created=EPOCH,
            ),
            Notification(
                id=3,
                notifiable_id=BOB,
                notification='{"test":"other user"}',
                name="test.notification",
                subject_id=3,
                read_at=None,
                created=EPOCH,
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

    application.dependency_overrides[get_auth_subject] = lambda: None
    return application


def as_user(app: FastAPI, user_id: int) -> TestClient:
    return TestClient(app, headers={"X-Test-User": str(user_id)}, raise_server_exceptions=False)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, headers={"X-Test-User": str(ALICE)}, raise_server_exceptions=False)


def stored(session: Session, notification_id: int) -> Notification:
    row = session.get(Notification, notification_id)
    assert row is not None, f"notification {notification_id} is not in the database"
    return row


class TestReadingNotifications:
    def test_only_your_own(self, app: FastAPI) -> None:
        assert [n["id"] for n in as_user(app, ALICE).get("/api/v1/notifications").json()] == [2, 1]
        assert [n["id"] for n in as_user(app, BOB).get("/api/v1/notifications").json()] == [3]

    def test_newest_first(self, client: TestClient) -> None:
        """id descending. The rows share a ``created`` here on purpose: if they differed,
        an implementation ordering by ``created`` would pass this too, and the two rules
        would be indistinguishable."""
        assert [n["id"] for n in client.get("/api/v1/notifications").json()] == [2, 1]

    def test_no_notifications_is_an_empty_array(self, app: FastAPI) -> None:
        resp = as_user(app, CAROL).get("/api/v1/notifications")

        assert resp.status_code == 200
        assert resp.json() == []
        assert resp.headers["x-pagination-result-count"] == "0"
        assert resp.headers["x-pagination-total-pages"] == "0"

    def test_the_payload_is_an_object_not_a_string(self, client: TestClient) -> None:
        """⚠️ The column stores JSON **text**; the wire carries a JSON **object**. A
        serializer that mirrored the column would emit ``"{\\"test\\":\\"one\\"}"`` — a
        different type, which every client would have to parse a second time."""
        body = client.get("/api/v1/notifications").json()

        assert body[0]["notification"] == {"test": "two"}
        assert isinstance(body[0]["notification"], dict)

    def test_an_unread_notification_is_the_zero_time_not_null(self, client: TestClient) -> None:
        """The column is nullable and the wire is not. ``null`` here would make clients
        branch on a value upstream never sends."""
        assert client.get("/api/v1/notifications").json()[0]["read_at"] == "0001-01-01T00:00:00Z"

    def test_the_item_has_no_read_field(self, client: TestClient) -> None:
        """Five keys. ``POST /notifications/{id}`` does return a ``read`` — but it is not
        implemented and cannot be reached by a token, so the list shape is the only one
        this resource has here."""
        assert set(client.get("/api/v1/notifications").json()[0]) == {
            "id",
            "name",
            "notification",
            "read_at",
            "created",
        }

    def test_pagination_slices_and_counts_the_whole_set(self, client: TestClient) -> None:
        first = client.get("/api/v1/notifications?page=1&per_page=1")
        second = client.get("/api/v1/notifications?page=2&per_page=1")

        assert [n["id"] for n in first.json()] == [2]
        assert [n["id"] for n in second.json()] == [1]
        assert first.headers["x-pagination-total-pages"] == "2"

    def test_a_page_past_the_end_is_empty_not_an_error(self, client: TestClient) -> None:
        resp = client.get("/api/v1/notifications?page=99")

        assert resp.status_code == 200
        assert resp.json() == []


class TestMarkingAllRead:
    def test_it_answers_success_not_the_delete_message(self, client: TestClient) -> None:
        """``{"message": "success"}`` — a different string from the ``Successfully
        deleted.`` every other write on this project answers."""
        resp = client.post("/api/v1/notifications")

        assert resp.status_code == 200
        assert resp.json() == {"message": "success"}

    def test_it_marks_the_callers_notifications(self, client: TestClient, session: Session) -> None:
        client.post("/api/v1/notifications")
        session.expire_all()

        assert stored(session, 1).read_at != ZERO_TIME
        assert stored(session, 2).read_at != ZERO_TIME

    def test_it_leaves_everybody_elses_alone(self, client: TestClient, session: Session) -> None:
        """Scoped by ``notifiable_id``. Without bob's row the query could have no WHERE
        clause at all and every test above would still pass."""
        client.post("/api/v1/notifications")
        session.expire_all()

        assert stored(session, 3).read_at in (None, ZERO_TIME)

    def test_a_second_call_moves_read_at_forward_again(
        self, client: TestClient, session: Session
    ) -> None:
        """⚠️ Every row is rewritten, not only the unread ones.

        Upstream's UPDATE carries no ``read_at IS NULL`` predicate — measured, two calls a
        second apart, and the timestamps differ. An implementation that skipped
        already-read rows is identical on the first call and diverges on the second, which
        is the call nobody writes a test for.
        """
        client.post("/api/v1/notifications")
        session.expire_all()
        first = stored(session, 1).read_at

        stored(session, 1).read_at = datetime(2020, 1, 1, tzinfo=UTC)
        session.commit()

        client.post("/api/v1/notifications")
        session.expire_all()
        assert stored(session, 1).read_at != datetime(2020, 1, 1, tzinfo=UTC)
        assert first is not None

    def test_a_user_with_no_notifications_still_gets_200(self, app: FastAPI) -> None:
        assert as_user(app, CAROL).post("/api/v1/notifications").status_code == 200

    def test_a_request_body_is_ignored(self, client: TestClient) -> None:
        """Upstream binds nothing here, so a body must not make it 412 or 422."""
        assert client.post("/api/v1/notifications", json={"read": False}).status_code == 200


class TestTheNotificationRoutesAreWired:
    def test_both_are_in_the_openapi_document(self, app: FastAPI) -> None:
        assert "/api/v1/notifications" in app.openapi()["paths"]
        assert set(app.openapi()["paths"]["/api/v1/notifications"]) >= {"get", "post"}

    def test_the_per_id_route_is_deliberately_absent(self, app: FastAPI) -> None:
        """⚠️ Not a gap — unreachable upstream.

        ``POST /notifications/{id}`` is missing from the API-token route registry, so a
        token granted every notification permission still gets 401/11. It is out of scope
        for the same reason ``GET /tokens`` and the caldav routes were cut. Implementing
        it would add an endpoint no token client can call.
        """
        assert "/api/v1/notifications/{id}" not in app.openapi()["paths"]

    def test_the_permission_keys_match_the_reference(self) -> None:
        """Measured from the reference server's own ``GET /routes``: read_all for the GET
        and **update** for the collection POST — the mark-all takes the ordinary CRUD
        action name rather than one of its own."""
        from calton.core.route_registry import registry

        create_app()
        assert set(registry.to_json()["notifications"]) == {"read_all", "update"}
