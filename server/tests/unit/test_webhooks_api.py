"""The project-webhook endpoints, driven over HTTP through ``create_app``.

Three upstream behaviours here read as bugs and are all copied, so most of this file
exists to stop someone repairing them:

* the update writes ``events`` and nothing else, while **requiring** ``target_url``;
* its response is the **request body**, so it reports a ``target_url`` the database did
  not keep — except for ``project_id``, which comes from the stored row;
* the project segment in the path does not scope the item lookup.

The world:

===  =========================================================================
940  P-ALICE, alice's. bob has read on it, carol has write.
941  P-CAROL, carol's. alice cannot see it at all.
===  =========================================================================

bob and carol are both non-owners and they are **not** interchangeable: read is enough to
list webhooks and not enough to create one, so a fixture with only one of them cannot
tell "any collaborator" from "a writer".
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
from calton.models import Project, ProjectUser, User, Webhook

ALICE, BOB, CAROL = 900, 901, 902
P_ALICE, P_CAROL = 940, 941

EPOCH = datetime(2026, 1, 1, tzinfo=UTC)

VALID = {"target_url": "https://example.test/hook", "events": ["task.created"]}


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
            Project(id=P_ALICE, title="P-ALICE", identifier="", owner_id=ALICE, position=1),
            Project(id=P_CAROL, title="P-CAROL", identifier="", owner_id=CAROL, position=2),
        ]
    )
    session.add_all(
        [
            ProjectUser(project_id=P_ALICE, user_id=BOB, permission=0),
            ProjectUser(project_id=P_ALICE, user_id=CAROL, permission=1),
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
    """Authenticated as alice, who owns P_ALICE."""
    return TestClient(app, headers={"X-Test-User": str(ALICE)}, raise_server_exceptions=False)


def stored(session: Session, webhook_id: int) -> Webhook:
    row = session.get(Webhook, webhook_id)
    assert row is not None, f"webhook {webhook_id} is not in the database"
    return row


# --- reading -----------------------------------------------------------------


class TestListingWebhooks:
    def test_an_empty_project_is_an_array(self, client: TestClient) -> None:
        resp = client.get(f"/api/v1/projects/{P_ALICE}/webhooks")

        assert resp.status_code == 200
        assert resp.json() == []
        assert resp.headers["x-pagination-result-count"] == "0"

    def test_read_permission_is_enough_to_list(self, app: FastAPI, client: TestClient) -> None:
        """bob has read and no more. Requiring write here — which every other route on
        this resource does need — would hide a project's webhooks from its readers."""
        client.put(f"/api/v1/projects/{P_ALICE}/webhooks", json=VALID)

        resp = as_user(app, BOB).get(f"/api/v1/projects/{P_ALICE}/webhooks")

        assert resp.status_code == 200
        assert [h["target_url"] for h in resp.json()] == [VALID["target_url"]]

    def test_a_stranger_is_403_code_1(self, app: FastAPI) -> None:
        """⚠️ Code **1**, not the 0 the write routes answer. Two different refusal bodies
        on one resource, from two different layers upstream; sharing one helper between
        them is the natural implementation and is wrong on one of the two."""
        resp = as_user(app, ALICE).get(f"/api/v1/projects/{P_CAROL}/webhooks")

        assert resp.status_code == 403
        assert resp.json() == {"code": 1, "message": "You're not allowed to do this."}

    def test_a_missing_project_is_404_3001(self, client: TestClient) -> None:
        """The project is looked up before the permission is judged, so "no such project"
        beats "not yours"."""
        resp = client.get("/api/v1/projects/99999/webhooks")

        assert resp.status_code == 404
        assert resp.json() == {"code": 3001, "message": "This project does not exist."}

    def test_a_non_numeric_project_is_400_not_422(self, client: TestClient) -> None:
        resp = client.get("/api/v1/projects/notanint/webhooks")

        assert resp.status_code == 400
        assert resp.json()["code"] == 2004

    def test_another_projects_webhooks_are_not_listed(
        self, app: FastAPI, client: TestClient
    ) -> None:
        """The list filters on ``project_id``. carol may write P_ALICE, so both rows are
        created by someone who can see both projects — the filter is the only thing
        keeping them apart."""
        client.put(f"/api/v1/projects/{P_ALICE}/webhooks", json=VALID)
        as_user(app, CAROL).put(
            f"/api/v1/projects/{P_CAROL}/webhooks",
            json={"target_url": "https://example.test/other", "events": ["task.created"]},
        )

        listed = client.get(f"/api/v1/projects/{P_ALICE}/webhooks").json()

        assert [h["target_url"] for h in listed] == [VALID["target_url"]]


# --- creating ----------------------------------------------------------------


class TestCreatingAWebhook:
    def test_the_owner_may_create(self, client: TestClient) -> None:
        resp = client.put(f"/api/v1/projects/{P_ALICE}/webhooks", json=VALID)

        assert resp.status_code == 201
        assert resp.json()["target_url"] == VALID["target_url"]
        assert resp.json()["project_id"] == P_ALICE

    def test_a_writer_may_create(self, app: FastAPI) -> None:
        resp = as_user(app, CAROL).put(f"/api/v1/projects/{P_ALICE}/webhooks", json=VALID)

        assert resp.status_code == 201

    def test_a_reader_may_not(self, app: FastAPI) -> None:
        """The pair to ``test_read_permission_is_enough_to_list``. Without both, "read"
        and "write" are the same rule on this resource."""
        resp = as_user(app, BOB).put(f"/api/v1/projects/{P_ALICE}/webhooks", json=VALID)

        assert resp.status_code == 403
        assert resp.json() == {"code": 0, "message": "Forbidden"}

    def test_a_missing_target_url_is_412(self, client: TestClient) -> None:
        resp = client.put(f"/api/v1/projects/{P_ALICE}/webhooks", json={"events": ["task.created"]})

        assert resp.status_code == 412
        assert resp.json()["invalid_fields"] == ["target_url: non zero value required"]

    def test_missing_events_are_412(self, client: TestClient) -> None:
        resp = client.put(
            f"/api/v1/projects/{P_ALICE}/webhooks", json={"target_url": "https://e.test/a"}
        )

        assert resp.status_code == 412
        assert resp.json()["invalid_fields"] == ["events: non zero value required"]

    def test_an_unknown_event_is_a_bare_field_name(self, client: TestClient) -> None:
        """⚠️ ``["events"]``, with no ``": ..."`` after it. Upstream raises this one by
        hand rather than through the validator, so there is no tag text to render — and
        the natural implementation, reusing the validator, produces the wrong string."""
        resp = client.put(
            f"/api/v1/projects/{P_ALICE}/webhooks",
            json={"target_url": "https://e.test/a", "events": ["nope.nope"]},
        )

        assert resp.status_code == 412
        assert resp.json()["invalid_fields"] == ["events"]

    def test_the_secret_is_never_returned(self, client: TestClient) -> None:
        """Masked on the way out — including on the 201 that just accepted it."""
        resp = client.put(
            f"/api/v1/projects/{P_ALICE}/webhooks",
            json={**VALID, "secret": "s3cret", "basic_auth_user": "u", "basic_auth_password": "p"},
        )

        assert resp.json()["secret"] == ""
        assert resp.json()["basic_auth_user"] == ""
        assert resp.json()["basic_auth_password"] == ""

    def test_but_all_three_credentials_are_actually_stored(
        self, client: TestClient, session: Session
    ) -> None:
        """⚠️ **The parity harness cannot check this, structurally**, which is why it is
        asserted here and against the database rather than against a response.

        An implementation that accepts the three write-only fields and **throws them
        away** is byte-identical on the wire to a correct one: both answer ``""`` on
        every route, forever. Diffing us against upstream can never tell them apart —
        and the difference is that HMAC signing and basic-auth delivery are silently
        broken.

        So "masked" and "discarded" have to be separated by looking at the row. The test
        above owns the wire half; this one owns the storage half, and all three fields
        are read back because the pair is as load-bearing as the secret.

        Handed over explicitly by coder-e while writing the corpus, rather than left for
        each of us to assume the other had it covered — the corpus case for the masking
        carries a note pointing here.
        """
        webhook_id = client.put(
            f"/api/v1/projects/{P_ALICE}/webhooks",
            json={**VALID, "secret": "s3cret", "basic_auth_user": "u", "basic_auth_password": "p"},
        ).json()["id"]
        session.expire_all()

        row = stored(session, webhook_id)
        assert (row.secret, row.basic_auth_user, row.basic_auth_password) == ("s3cret", "u", "p")

    def test_a_missing_project_is_404(self, client: TestClient) -> None:
        resp = client.put("/api/v1/projects/99999/webhooks", json=VALID)

        assert resp.status_code == 404
        assert resp.json()["code"] == 3001


# --- updating ----------------------------------------------------------------


class TestUpdatingAWebhook:
    @pytest.fixture
    def webhook_id(self, client: TestClient) -> int:
        body = client.put(f"/api/v1/projects/{P_ALICE}/webhooks", json=VALID).json()
        return int(body["id"])

    def test_the_events_are_written(
        self, client: TestClient, session: Session, webhook_id: int
    ) -> None:
        resp = client.post(
            f"/api/v1/projects/{P_ALICE}/webhooks/{webhook_id}",
            json={"target_url": VALID["target_url"], "events": ["task.deleted"]},
        )

        assert resp.status_code == 200
        session.expire_all()
        assert stored(session, webhook_id).events == '["task.deleted"]'

    def test_the_target_url_is_required_and_then_discarded(
        self, client: TestClient, session: Session, webhook_id: int
    ) -> None:
        """⚠️ Both halves in one case, because either alone invites the other to be
        "fixed": omitting ``target_url`` is a 412, and sending a new one changes nothing
        in the database. Upstream validates a column it does not write."""
        omitted = client.post(
            f"/api/v1/projects/{P_ALICE}/webhooks/{webhook_id}", json={"events": ["task.deleted"]}
        )
        assert omitted.status_code == 412
        assert omitted.json()["invalid_fields"] == ["target_url: non zero value required"]

        client.post(
            f"/api/v1/projects/{P_ALICE}/webhooks/{webhook_id}",
            json={"target_url": "https://example.test/CHANGED", "events": ["task.deleted"]},
        )
        session.expire_all()
        assert stored(session, webhook_id).target_url == VALID["target_url"]

    def test_the_response_reports_the_url_that_was_not_saved(
        self, client: TestClient, webhook_id: int
    ) -> None:
        """The response is the bound struct, so it echoes the ``target_url`` the previous
        test just showed is discarded. Answering the stored row instead is the obvious
        implementation and is what a reader of this endpoint would expect."""
        resp = client.post(
            f"/api/v1/projects/{P_ALICE}/webhooks/{webhook_id}",
            json={"target_url": "https://example.test/CHANGED", "events": ["task.deleted"]},
        )

        assert resp.json()["target_url"] == "https://example.test/CHANGED"

    def test_but_the_project_id_comes_from_the_stored_row(
        self, client: TestClient, webhook_id: int
    ) -> None:
        """⚠️ The exception that proves it is not a plain echo.

        ``canDoWebhook`` assigns ``project_id`` and ``user_id`` onto the bound struct
        while checking rights, so those two are the row's. Addressing the webhook through
        a *different* project's path is the only way to see it: the path says P_CAROL and
        the answer says P_ALICE.
        """
        resp = client.post(
            f"/api/v1/projects/{P_CAROL}/webhooks/{webhook_id}",
            json={"target_url": "https://example.test/x", "events": ["task.deleted"]},
        )

        assert resp.status_code == 200
        assert resp.json()["project_id"] == P_ALICE

    def test_a_minimal_body_answers_zero_created_and_null_created_by(
        self, client: TestClient, webhook_id: int
    ) -> None:
        resp = client.post(
            f"/api/v1/projects/{P_ALICE}/webhooks/{webhook_id}",
            json={"target_url": VALID["target_url"], "events": ["task.deleted"]},
        )

        assert resp.json()["created"] == "0001-01-01T00:00:00Z"
        assert resp.json()["created_by"] is None

    def test_a_round_tripped_body_answers_what_it_sent(
        self, client: TestClient, webhook_id: int
    ) -> None:
        """⚠️ The pair to the case above, and the reason ``created``/``created_by`` are
        declared on the *write* schema.

        A read-modify-write client — which the real MCP client is — GETs the webhook and
        POSTs the whole object back, so its ``created`` and ``created_by`` come back
        populated where a hand-written client's come back zero. Dropping those fields
        from the write schema (they are read-only, so dropping them is the natural
        choice) makes every RMW update answer zeros, and no client that does not
        round-trip can see the difference.
        """
        whole = client.get(f"/api/v1/projects/{P_ALICE}/webhooks").json()[0]

        resp = client.post(f"/api/v1/projects/{P_ALICE}/webhooks/{webhook_id}", json=whole)

        assert resp.status_code == 200
        assert resp.json()["created"] == whole["created"]
        assert resp.json()["created_by"] == whole["created_by"]

    def test_the_path_project_does_not_scope_the_lookup(
        self, client: TestClient, session: Session, webhook_id: int
    ) -> None:
        """⚠️ Not a hole; measured, and verified not to be exploitable.

        The webhook is found by id alone and the rights are checked against **its own**
        project, so alice — who cannot even read P_CAROL — updates her own webhook
        through P_CAROL's path. Scoping the query by the path project is the tidy reading
        and turns this measured 200 into a 403.
        """
        assert client.get(f"/api/v1/projects/{P_CAROL}").status_code == 403

        resp = client.post(
            f"/api/v1/projects/{P_CAROL}/webhooks/{webhook_id}",
            json={"target_url": VALID["target_url"], "events": ["task.overdue"]},
        )

        assert resp.status_code == 200
        session.expire_all()
        assert stored(session, webhook_id).events == '["task.overdue"]'

    def test_but_a_stranger_still_cannot_reach_it(
        self, app: FastAPI, session: Session, webhook_id: int
    ) -> None:
        """The security half of the case above. carol owns P_CAROL and has no rights on
        P_ALICE's webhook, so naming her own project does not get her to it — the check
        follows the row, not the path."""
        resp = as_user(app, BOB).post(
            f"/api/v1/projects/{P_ALICE}/webhooks/{webhook_id}",
            json={"target_url": VALID["target_url"], "events": ["task.overdue"]},
        )

        assert resp.status_code == 403
        session.expire_all()
        assert stored(session, webhook_id).events == '["task.created"]'

    def test_an_absent_webhook_is_403_not_404(self, client: TestClient) -> None:
        """Upstream's permission check loads the row, so a missing one is refused before
        anything reports it missing."""
        resp = client.post(
            f"/api/v1/projects/{P_ALICE}/webhooks/99999",
            json={"target_url": VALID["target_url"], "events": ["task.created"]},
        )

        assert resp.status_code == 403

    def test_validation_runs_before_the_lookup(self, client: TestClient) -> None:
        """A bad body against a webhook that does not exist answers 412, not 403 — the
        binding fails first. Ordering these the other way is invisible until a client
        sends both mistakes at once."""
        resp = client.post(
            f"/api/v1/projects/{P_ALICE}/webhooks/99999", json={"events": ["task.created"]}
        )

        assert resp.status_code == 412


# --- deleting ----------------------------------------------------------------


class TestDeletingAWebhook:
    @pytest.fixture
    def webhook_id(self, client: TestClient) -> int:
        return int(client.put(f"/api/v1/projects/{P_ALICE}/webhooks", json=VALID).json()["id"])

    def test_the_owner_may_delete(
        self, client: TestClient, session: Session, webhook_id: int
    ) -> None:
        resp = client.delete(f"/api/v1/projects/{P_ALICE}/webhooks/{webhook_id}")

        assert resp.status_code == 200
        assert resp.json() == {"message": "Successfully deleted."}
        session.expire_all()
        assert session.get(Webhook, webhook_id) is None

    def test_a_reader_may_not(self, app: FastAPI, session: Session, webhook_id: int) -> None:
        resp = as_user(app, BOB).delete(f"/api/v1/projects/{P_ALICE}/webhooks/{webhook_id}")

        assert resp.status_code == 403
        session.expire_all()
        assert session.get(Webhook, webhook_id) is not None

    def test_deleting_twice_is_403_the_second_time(
        self, client: TestClient, webhook_id: int
    ) -> None:
        """Not 404, and not an idempotent 200 either — the row is gone, so the permission
        check that loads it refuses. Different from the team-member delete, which is 200
        every time."""
        first = client.delete(f"/api/v1/projects/{P_ALICE}/webhooks/{webhook_id}")
        second = client.delete(f"/api/v1/projects/{P_ALICE}/webhooks/{webhook_id}")

        assert first.status_code == 200
        assert second.status_code == 403

    def test_an_absent_webhook_is_403(self, client: TestClient) -> None:
        assert client.delete(f"/api/v1/projects/{P_ALICE}/webhooks/99999").status_code == 403


# --- the config gate and the wiring -------------------------------------------


class TestTheWebhookRoutesFollowTheConfigFlag:
    """⚠️ The only conditionally-mounted routes in the app, and the reason is external.

    Upstream does not register these when ``webhooks.enabled`` is false, and it reports
    the flag through ``/info``. The parity harness runs the Go side with the flag **off**
    while the MCP gate runs it **on**, so any hardcoded answer here is wrong on one of
    the two devices — and ``/info`` is compared on every parity case.
    """

    def test_they_are_mounted_by_default(self, app: FastAPI) -> None:
        paths = app.openapi()["paths"]

        assert "/api/v1/projects/{project}/webhooks" in paths
        assert "/api/v1/projects/{project}/webhooks/{webhook}" in paths

    def test_info_reports_the_flag(self, client: TestClient) -> None:
        assert client.get("/api/v1/info").json()["webhooks_enabled"] is True

    def test_with_the_flag_off_the_routes_are_absent(self, engine: Engine) -> None:
        from calton.config import Settings

        settings = Settings()
        settings.webhooks.enabled = False
        off = create_app(settings=settings, engine=engine)

        assert "/api/v1/projects/{project}/webhooks" not in off.openapi()["paths"]

    def test_with_the_flag_off_info_says_so(self, engine: Engine) -> None:
        """The pair to the case above. Both halves matter: upstream turns the routes off
        *and* advertises it, and a client that reads ``/info`` to decide whether to offer
        webhooks would otherwise be told yes and then 404'd."""
        from calton.config import Settings

        settings = Settings()
        settings.webhooks.enabled = False
        off = create_app(settings=settings, engine=engine)

        with TestClient(off) as probe:
            assert probe.get("/api/v1/info").json()["webhooks_enabled"] is False

    def test_the_permission_group_is_projects_webhooks(self) -> None:
        """⚠️ Not ``webhooks``. That group exists and holds exactly one entry —
        ``GET /webhooks/events`` — and reading "the webhooks group rejects read_all" as
        "no API token can reach these four" was a wrong conclusion drawn from a correct
        observation. Measured against the reference server's own ``GET /routes``.
        """
        from calton.core.route_registry import registry

        create_app()
        assert set(registry.to_json()["projects_webhooks"]) == {
            "create",
            "read_all",
            "update",
            "delete",
        }


class TestTheEventCatalogue:
    """``GET /webhooks/events`` — the fifth route, and the odd one out.

    It was left unimplemented when the other four landed, deliberately: adding an
    endpoint bumps a hardcoded total, and that total exists so adding one is a decision
    somebody explains. team-lead's explanation, on approving it: four of a resource's
    five routes is worse than none of them, because the missing fifth reads as
    possibly-intentional and costs the next person time to rule out.
    """

    def test_it_serves_the_nineteen_event_names(self, client: TestClient) -> None:
        resp = client.get("/api/v1/webhooks/events")

        assert resp.status_code == 200
        assert len(resp.json()) == 19
        assert "task.created" in resp.json()

    def test_the_body_is_a_bare_array_not_an_object(self, client: TestClient) -> None:
        """Upstream sends a naked JSON array. Wrapping it in ``{"events": [...]}`` is the
        tidier shape and is a body no existing client can parse."""
        assert isinstance(client.get("/api/v1/webhooks/events").json(), list)

    def test_the_order_is_upstreams_not_pythons(self, client: TestClient) -> None:
        """⚠️ The list is served in the catalogue's order, which is the order the Go
        endpoint returns. It happens to be sortable, so ``sorted()`` would pass this — the
        assertion is against the transcribed tuple rather than against ``sorted()`` so
        that it keeps meaning something if upstream's order ever stops coinciding.
        """
        from calton.events.catalogue import WEBHOOK_EVENTS

        assert client.get("/api/v1/webhooks/events").json() == list(WEBHOOK_EVENTS)

    def test_it_does_not_re_list_the_names(self) -> None:
        """⚠️ A guard against a second copy, not against a wrong value.

        ``webhook_service.AVAILABLE_EVENTS`` briefly *was* a second literal — measured
        independently, identical, and one edit away from silently disagreeing. A drifted
        name is a subscriber that never fires, with nothing to report it. This asserts
        they are the same object, so re-inlining the list fails here rather than years
        later in somebody's undelivered webhook.
        """
        from calton.events.catalogue import WEBHOOK_EVENTS
        from calton.services.webhook_service import AVAILABLE_EVENTS

        assert AVAILABLE_EVENTS is WEBHOOK_EVENTS

    def test_an_unknown_event_is_rejected_against_that_same_list(self, client: TestClient) -> None:
        """The catalogue and the validator are one source: every name the endpoint
        advertises is accepted, and one it does not is not."""
        advertised = client.get("/api/v1/webhooks/events").json()

        ok = client.put(
            f"/api/v1/projects/{P_ALICE}/webhooks",
            json={"target_url": "https://example.test/a", "events": [advertised[0]]},
        )
        bad = client.put(
            f"/api/v1/projects/{P_ALICE}/webhooks",
            json={"target_url": "https://example.test/a", "events": ["project.created"]},
        )

        assert ok.status_code == 201
        # project.created is a real upstream event that is deliberately NOT webhook-
        # exposed — a sharper case than a nonsense string, which any typo check catches.
        assert bad.status_code == 412
        assert bad.json()["invalid_fields"] == ["events"]

    def test_it_disappears_with_the_flag_too(self, engine: Engine) -> None:
        from calton.config import Settings

        settings = Settings()
        settings.webhooks.enabled = False
        off = create_app(settings=settings, engine=engine)

        assert "/api/v1/webhooks/events" not in off.openapi()["paths"]

    def test_its_permission_group_is_webhooks_not_projects_webhooks(self) -> None:
        """The two groups on one resource. ``webhooks`` holds this route and nothing
        else; the four CRUD routes are ``projects_webhooks``."""
        from calton.core.route_registry import registry

        create_app()
        assert set(registry.to_json()["webhooks"]) == {"events"}
