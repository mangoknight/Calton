"""The project endpoints, driven over HTTP through ``create_app``.

Everything here goes through the real application — real router, real policy, real
service, real error handlers. A service-level test cannot see any of the failures this
file exists for: a router that was never mounted, a policy that turns 404 into 403, a
serializer that emits the item shape on the collection.

The world is the one ``harness/seed/overlay/perm.yml`` builds, with the same ids, so a
case here and a parity case describe the same fixture:

===  =========================================================================
900  P-root, alice's. 901 (C1) under it, 902 (C2) under 901.
904  A-parent, alice's — with 905 (B-child) under it owned by **bob**, which is
     what makes "visibility descends the parent chain" testable at all.
903  Bob-Own, bob's, top level. The destination for reparent attempts.
906  Full, alice's, description "will be reset" and hex_color "aabbcc".
===  =========================================================================

Grants: bob has write on 901 and 902, carol has admin on 902, dave has nothing.
That spread is not decoration — see ``TestReparentGates``, where owner/admin/write have
to be three distinguishable subjects or half the cells collapse into each other.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from calton.auth.deps import get_auth_subject
from calton.core.errors import INVALID_MODEL_MESSAGE
from calton.db.base import Base
from calton.db.session import get_db, session_factory
from calton.main import create_app
from calton.models import Project, Task, User
from calton.models.project_view import ProjectView
from calton.models.saved_filter import SavedFilter
from calton.models.team import ProjectUser

ALICE, BOB, CAROL, DAVE = 900, 901, 902, 903

P_ROOT, C1, C2 = 900, 901, 902
BOB_OWN = 903
A_PARENT, B_CHILD = 904, 905
FULL = 906

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
            User(id=DAVE, username="dave", created=EPOCH, updated=EPOCH),
        ]
    )
    session.add_all(
        [
            Project(id=P_ROOT, title="P-root", identifier="", owner_id=ALICE, position=1),
            Project(
                id=C1,
                title="C1",
                identifier="",
                owner_id=ALICE,
                parent_project_id=P_ROOT,
                position=2,
            ),
            Project(
                id=C2,
                title="C2",
                identifier="",
                owner_id=ALICE,
                parent_project_id=C1,
                position=3,
            ),
            Project(id=BOB_OWN, title="Bob-Own", identifier="", owner_id=BOB, position=4),
            Project(id=A_PARENT, title="A-parent", identifier="", owner_id=ALICE, position=5),
            # Owned by bob, parented under alice's project.
            Project(
                id=B_CHILD,
                title="B-child",
                identifier="",
                owner_id=BOB,
                parent_project_id=A_PARENT,
                position=6,
            ),
            Project(
                id=FULL,
                title="Full",
                identifier="",
                owner_id=ALICE,
                position=7,
                description="will be reset",
                hex_color="aabbcc",
            ),
        ]
    )
    session.add_all(
        [
            ProjectUser(project_id=C1, user_id=BOB, permission=1),
            ProjectUser(project_id=C2, user_id=BOB, permission=1),
            ProjectUser(project_id=C2, user_id=CAROL, permission=2),
        ]
    )
    session.add_all(
        [
            Task(id=900, project_id=P_ROOT, index=1, title="t-900-a", created_by_id=ALICE),
            Task(id=902, project_id=C1, index=1, title="t-901-a", created_by_id=ALICE),
            Task(id=903, project_id=C2, index=1, title="t-902-a", created_by_id=ALICE),
            Task(id=904, project_id=B_CHILD, index=1, title="bobs-precious", created_by_id=BOB),
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

    # Mirrors tests/unit/conftest.py. This file shadows the shared `app` fixture, so
    # the override has to be repeated here — the auth wiring landed on another branch
    # after this fixture was written, and the merge was textually clean while leaving
    # every test in this file answering 401.
    #
    # ⚠️ Overriding here means these tests do NOT cover the auth wiring. That is what
    # TestTheAuthChainIsWired in test_api_tokens.py is for.
    application.dependency_overrides[get_auth_subject] = lambda: None

    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """Authenticated as alice."""
    return TestClient(app, headers={"X-Test-User": str(ALICE)}, raise_server_exceptions=False)


def stored_project(session: Session, project_id: int) -> Project:
    """The row, failing by name if it is gone.

    ``session.get`` is typed ``Project | None`` and every caller below is asserting
    something *about* a row it expects to exist — so an unchecked ``None`` turns "the
    project was deleted" into an AttributeError several frames away instead of a named
    failure. Asserting here keeps the diagnosis at the call site and satisfies --strict.
    """
    stored = session.get(Project, project_id)
    assert stored is not None, f"project {project_id} is not in the database"
    return stored


def stored_user(session: Session, user_id: int) -> User:
    """As :func:`stored_project`, for users."""
    stored = session.get(User, user_id)
    assert stored is not None, f"user {user_id} is not in the database"
    return stored


def as_user(app: FastAPI, user_id: int) -> TestClient:
    return TestClient(app, headers={"X-Test-User": str(user_id)}, raise_server_exceptions=False)


# --- wiring ------------------------------------------------------------------


class TestTheRoutesAreActuallyMounted:
    """Practice 10: a module that is delivered but never included is a 404 in production
    while all of its own tests stay green. That is the state T16 was in."""

    def test_every_project_route_is_in_the_openapi_document(self, app: FastAPI) -> None:
        """Read from ``openapi()["paths"]``, **never** from ``app.routes``.

        Routers merged by ``include_router`` become ``_IncludedRouter`` entries with no
        ``.path`` attribute, so the obvious scan reports zero routes forever and a
        wiring check written that way is blind by construction.
        """
        paths = set(app.openapi()["paths"])

        assert {
            "/api/v1/projects",
            "/api/v1/projects/{project}",
            "/api/v1/projects/{project}/projectusers",
        } <= paths

    def test_the_inverted_verbs_are_the_ones_registered(self, app: FastAPI) -> None:
        """v1 inverts PUT and POST. Getting it backwards is a 404, not an error."""
        paths = app.openapi()["paths"]

        assert set(paths["/api/v1/projects"]) >= {"get", "put"}
        assert "post" not in paths["/api/v1/projects"]
        assert set(paths["/api/v1/projects/{project}"]) >= {"get", "post", "delete"}
        assert "put" not in paths["/api/v1/projects/{project}"]

    def test_the_routes_are_registered_for_api_token_permissions(self) -> None:
        """Mounting the router and registering its routes are two separate actions.

        Doing only the first leaves every token-authenticated request answering 403 while
        JWT requests work, which is a difference no test of either layer alone reports.
        """
        from calton.core.route_registry import registry
        from calton.main import create_app as _create_app

        _create_app()
        actions = registry.routes["projects"]

        # The five CRUD actions plus the hand-written one. A router mounted without this
        # registration answers 403 to every API-token request while JWT requests work.
        assert {"create", "read_one", "read_all", "update", "delete"} <= set(actions)
        assert "projectusers" in actions
        # POST and PATCH both map to "update" and the registry keeps one entry, so this
        # asserts the verb is not the create verb rather than which of the two won.
        assert actions["update"].method in ("POST", "PATCH")
        assert actions["create"].method == "PUT", "v1 creates with PUT"

    def test_reachable_through_the_real_app(self, client: TestClient) -> None:
        resp = client.get("/api/v1/projects")

        assert resp.status_code == 200


# --- the three cases the card names ------------------------------------------


class TestRecordedContractCasesOverHttp:
    """The card's items ④/⑤, exercised as HTTP rather than as schema validation.

    ``test_project_contract.py`` checks the same recorded bodies against ``ProjectWrite``
    directly. That proves the schema accepts them; it cannot prove a request carrying
    them reaches a handler, and for most of this file's life no request did.
    """

    def test_a_whitespace_only_title_is_created_not_rejected(self, client: TestClient) -> None:
        """Measured 201. ``required`` means non-zero, not non-blank.

        ``"   "`` rather than ``"x"`` is the whole point: a schema that added ``strip()``
        or ``str_strip_whitespace`` still passes with any ordinary title.
        """
        resp = client.put("/api/v1/projects", json={"title": "   "})

        assert resp.status_code == 201
        assert resp.json()["title"] == "   "

    def test_an_empty_title_is_412_with_the_invalid_fields_key(self, client: TestClient) -> None:
        """Not 400. Field validation exits through ErrInvalidData, and the third key is
        what the frontend draws per-field errors from."""
        resp = client.put("/api/v1/projects", json={"title": ""})

        assert resp.status_code == 412
        body = resp.json()
        assert body["code"] == 2002
        assert body["message"] == "Invalid Data"
        # govalidator's own wording, measured. The field name alone loses the reason,
        # which is what the frontend draws the field-level error from.
        assert body["invalid_fields"] == ["title: non zero value required"]

    def test_a_missing_title_reports_the_same_thing_as_an_empty_one(
        self, client: TestClient
    ) -> None:
        """Absent and empty are one case upstream.

        Go decodes a missing key to the zero value and validates afterwards, so
        ``required`` cannot tell them apart. Pydantic can, which is why ``title`` carries
        a default and ``validate_default`` rather than being a required field — without
        that this body reports a ``missing`` error with different wording.
        """
        resp = client.put("/api/v1/projects", json={})

        assert resp.status_code == 412
        assert resp.json()["invalid_fields"] == ["title: non zero value required"]

    def test_a_length_violation_quotes_the_value_and_the_rule(self, client: TestClient) -> None:
        """The second of the two shapes: the offending value, then the tag verbatim.

        251 characters rather than some round number: 250 is accepted, so this is the
        first rejected length and the bound itself is under test.
        """
        resp = client.put("/api/v1/projects", json={"title": "x" * 251})

        assert resp.status_code == 412
        assert resp.json()["invalid_fields"] == [
            f"title: {'x' * 251} does not validate as runelength(1|250)"
        ]

    def test_a_250_character_title_is_still_accepted(self, client: TestClient) -> None:
        """The bound is inclusive, and it counts **characters, not bytes**.

        ⚠️ The title is non-ASCII on purpose. govalidator's ``runelength`` counts runes,
        so 250 three-byte characters are 750 bytes and must still be accepted. An ASCII
        title of the same length cannot tell the two implementations apart — it is a
        fixed point under the byte/rune swap, and mutation confirmed that: replacing the
        rune count with a byte count left an all-ASCII version of this test green.
        """
        assert client.put("/api/v1/projects", json={"title": "測" * 250}).status_code == 201

    def test_a_251_character_non_ascii_title_is_still_rejected(self, client: TestClient) -> None:
        """The other side of the same bound, so "accept everything non-ASCII" does not
        pass the test above."""
        resp = client.put("/api/v1/projects", json={"title": "測" * 251})

        assert resp.status_code == 412

    def test_several_failing_fields_are_all_reported(self, client: TestClient) -> None:
        """⚠️ Compared as a **set**. govalidator collects into a map and Go randomises
        where a map walk starts, so this array comes back rotated by a random amount:
        40 samples of the same request gave the declaration order 28 times and two
        rotations of it the rest. Asserting a list passes locally and fails in CI
        roughly a third of the time."""
        resp = client.put(
            "/api/v1/projects", json={"title": "", "identifier": "A" * 11, "hex_color": "b" * 9}
        )

        assert resp.status_code == 412
        assert set(resp.json()["invalid_fields"]) == {
            "title: non zero value required",
            "identifier: AAAAAAAAAAA does not validate as runelength(0|10)",
            "hex_color: bbbbbbbbb does not validate as runelength(0|7)",
        }

    def test_create_answers_201_with_four_views_and_three_buckets(
        self, client: TestClient, session: Session
    ) -> None:
        """PUT creates — and creating a project creates its views and buckets with it,
        in the same request, not lazily on first board access."""
        resp = client.put("/api/v1/projects", json={"title": "parity project"})

        assert resp.status_code == 201
        body = resp.json()
        views = body["views"]
        assert [(v["title"], v["view_kind"], v["position"]) for v in views] == [
            ("List", "list", 100),
            ("Gantt", "gantt", 200),
            ("Table", "table", 300),
            ("Kanban", "kanban", 400),
        ]
        assert views[0]["filter"]["filter"] == "done = false"
        # The other three carry no filter at all, which is a different value from an
        # empty filter object.
        assert [v["filter"] for v in views[1:]] == [None, None, None]

        kanban = views[3]
        assert kanban["bucket_configuration_mode"] == "manual"
        # Non-zero and distinct: they point at To-Do and Done with Doing between them,
        # so an implementation that created the view before its buckets leaves 0s here.
        assert kanban["default_bucket_id"] != 0
        assert kanban["done_bucket_id"] != 0
        assert kanban["done_bucket_id"] - kanban["default_bucket_id"] == 2

    def test_read_modify_write_echo_with_views_is_accepted(self, client: TestClient) -> None:
        """Card item ⑤. **A tripwire, not a demonstration.**

        This passes today because ``ProjectWrite`` does not declare ``views``, so the
        echoed array is dropped by ``extra="ignore"`` before validation. The moment
        somebody adds a ``views`` field the nested ``view_kind`` strings are parsed under
        ``strict=True`` and every MCP update becomes a 422. **This is the only place that
        would notice. Do not delete it for being reliably green.**
        """
        created = client.put("/api/v1/projects", json={"title": "echo me"}).json()
        assert len(created["views"]) == 4, "the echo has to carry views or it proves nothing"
        assert [v["view_kind"] for v in created["views"]] == ["list", "gantt", "table", "kanban"]

        echoed = dict(created)
        echoed["title"] = "echo me, renamed"

        resp = client.post(f"/api/v1/projects/{created['id']}", json=echoed)

        assert resp.status_code == 200
        assert resp.json()["title"] == "echo me, renamed"

    def test_echoed_read_only_fields_do_not_change_the_owner(
        self, client: TestClient, session: Session
    ) -> None:
        """The protection is the column whitelist, not the schema.

        ``owner_id`` is simply not in ``colsToUpdate``, so a forged owner in the body is
        ignored. The assertion has to name the **real** owner (900) — asserting the value
        from the request body would pass precisely when the forgery succeeded.
        """
        resp = client.post(
            f"/api/v1/projects/{C2}",
            json={
                "id": C2,
                "title": "Child",
                "owner": {"id": 1, "username": "alice"},
                "max_right": 2,
                "max_permission": 2,
                "created": "2020-01-01T00:00:00Z",
                "updated": "2020-01-01T00:00:00Z",
                "unknown_future_field": "whatever",
            },
        )

        assert resp.status_code == 200
        session.expire_all()
        assert stored_project(session, C2).owner_id == ALICE


# --- the response shape ------------------------------------------------------


class TestTheCollectionAndTheItemDifferOnPurpose:
    def test_the_item_reports_max_permission_zero_and_the_header_reports_two(
        self, client: TestClient
    ) -> None:
        """The body field is not the caller's permission; the header is.

        Owner of the project, so these two numbers come out of the same request with
        different values. An implementation that "helpfully" fills the body field in
        would pass any test that only read the header.
        """
        resp = client.get(f"/api/v1/projects/{P_ROOT}")

        assert resp.status_code == 200
        assert resp.headers["x-max-permission"] == "2"
        assert resp.json()["max_permission"] == 0

    def test_the_collection_reports_max_permission_null(self, client: TestClient) -> None:
        body = client.get("/api/v1/projects").json()

        assert all(project["max_permission"] is None for project in body)

    def test_views_is_null_on_the_collection_and_a_list_on_the_item(
        self, client: TestClient
    ) -> None:
        """Present-but-null and present-but-empty are different bytes, and the reference
        server sends one of each depending on the route."""
        created_id = client.put("/api/v1/projects", json={"title": "shapes"}).json()["id"]

        item = client.get(f"/api/v1/projects/{created_id}").json()
        assert isinstance(item["views"], list) and len(item["views"]) == 4

        listed = {p["id"]: p for p in client.get("/api/v1/projects").json()}
        # A seeded project has no views at all, and on the collection that is null.
        assert listed[P_ROOT]["views"] is None
        assert "views" in listed[P_ROOT], "the key is present, it is the value that is null"

    def test_the_seeded_item_with_no_views_reports_an_empty_list(self, client: TestClient) -> None:
        assert client.get(f"/api/v1/projects/{P_ROOT}").json()["views"] == []

    def test_subscription_is_declared_but_never_sent(
        self, client: TestClient, app: FastAPI
    ) -> None:
        """Declared for the contract, omitted on the wire — the whole point of
        OmitEmptyPtr. Both halves are asserted; either alone is satisfiable by the
        wrong implementation."""
        schema = app.openapi()["components"]["schemas"]["ProjectRead"]
        assert "subscription" in schema["properties"]

        assert "subscription" not in client.get(f"/api/v1/projects/{P_ROOT}").json()

    def test_the_owner_is_embedded(self, client: TestClient) -> None:
        owner = client.get(f"/api/v1/projects/{P_ROOT}").json()["owner"]

        assert owner["id"] == ALICE
        assert owner["username"] == "alice"


# --- visibility --------------------------------------------------------------


class TestVisibility:
    def test_owning_a_parent_shows_children_owned_by_someone_else(self, client: TestClient) -> None:
        """Measured, and the opposite of what "owner or explicit grant" predicts.

        905 belongs to **bob**; alice holds nothing on it. She sees it because she owns
        904, its parent. A fixture where children share their parent's owner cannot tell
        the two rules apart, which is why 905 is owned by someone else.
        """
        ids = [project["id"] for project in client.get("/api/v1/projects").json()]

        assert B_CHILD in ids

    def test_visibility_does_not_ascend(self, app: FastAPI) -> None:
        """The rule is strictly downward: bob owns 905 and still does not see its
        parent 904. Without this the test above is also satisfied by "any project
        related to one you can see"."""
        ids = [project["id"] for project in as_user(app, BOB).get("/api/v1/projects").json()]

        assert B_CHILD in ids
        assert A_PARENT not in ids

    def test_a_user_with_no_grants_gets_an_empty_list_not_a_403(self, app: FastAPI) -> None:
        """DoReadAll runs no permission check; scoping is the service's job. An earlier
        CRUDRouter asked a keyless can_read here and turned this into a 403."""
        resp = as_user(app, DAVE).get("/api/v1/projects")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_write_and_admin_grants_are_both_visible(self, app: FastAPI) -> None:
        ids = [project["id"] for project in as_user(app, BOB).get("/api/v1/projects").json()]

        assert {C1, C2, BOB_OWN} <= set(ids)

    def test_every_project_route_is_401_before_it_touches_the_database(self, tmp_path: Any) -> None:
        """Identity is established before any row is read, on every route.

        ⚠️ **Run against a database with no tables at all**, and that is the whole
        assertion. The policy is the outermost layer CRUDRouter runs; querying before
        knowing who is asking sent unauthenticated requests to the database, which failed
        there, and schemathesis reported it as a 500 on four routes.

        Against a *populated* test database both orders answer 401, so a version of this
        test using the ordinary fixture passes whichever way round the policy is written
        — confirmed by mutation: swapping the order left it green. An empty schema is what
        makes "did not reach the database" observable.
        """
        from sqlalchemy import create_engine

        empty = create_engine(f"sqlite+pysqlite:///{tmp_path / 'no-tables.db'}")
        # Deliberately no Base.metadata.create_all: any query at all is an error here.
        application = create_app(engine=empty)
        application.state.session_factory = session_factory(empty)
        anonymous = TestClient(application, raise_server_exceptions=False)

        for method, path in (
            ("GET", "/api/v1/projects"),
            ("PUT", "/api/v1/projects"),
            ("GET", f"/api/v1/projects/{P_ROOT}"),
            ("POST", f"/api/v1/projects/{P_ROOT}"),
            # No PATCH: it is not registered, so it 405s in the router before auth
            # ever runs — which makes it the wrong shape for this assertion.
            ("DELETE", f"/api/v1/projects/{P_ROOT}"),
            ("GET", f"/api/v1/projects/{P_ROOT}/projectusers"),
        ):
            resp = anonymous.request(method, path, json={"title": "x"})

            assert resp.status_code == 401, (
                f"{method} {path} answered {resp.status_code}; a 500 here means the "
                f"policy queried before checking who was asking"
            )
            assert resp.json()["code"] == 11, f"{method} {path}"

        empty.dispose()

    def test_unauthenticated_is_401_code_11(self, app: FastAPI) -> None:
        """Found by schemathesis as a 500: read_all has no permission gate, so with no
        subject the service reached int(None). Failing closed is what upstream does."""
        resp = TestClient(app, raise_server_exceptions=False).get("/api/v1/projects")

        assert resp.status_code == 401
        assert resp.json()["code"] == 11


class TestArchivedFiltering:
    def test_archived_projects_are_hidden_by_default_and_shown_on_request(
        self, client: TestClient
    ) -> None:
        """``is_archived=true`` *includes* archived projects rather than selecting only
        them — the result is a superset of the default, not a disjoint set."""
        client.post(f"/api/v1/projects/{FULL}", json={"title": "Full", "is_archived": True})

        default_ids = {p["id"] for p in client.get("/api/v1/projects").json()}
        including = {p["id"] for p in client.get("/api/v1/projects?is_archived=true").json()}

        assert FULL not in default_ids
        assert FULL in including
        assert default_ids < including


# --- pagination --------------------------------------------------------------


class TestSavedFilterPseudoProjects:
    @pytest.fixture
    def a_saved_filter(self, sessions: sessionmaker[Session]) -> int:
        with sessions() as session:
            session.add(
                SavedFilter(
                    id=950,
                    title="X-saved",
                    filters="done = false",
                    owner_id=ALICE,
                    created=EPOCH,
                    updated=EPOCH,
                )
            )
            session.commit()
        return -951

    def test_a_saved_filter_appears_as_a_negative_project(
        self, client: TestClient, a_saved_filter: int
    ) -> None:
        ids = [project["id"] for project in client.get("/api/v1/projects").json()]

        assert a_saved_filter in ids

    def test_it_is_outside_pagination_and_the_count_header_disagrees_with_the_body(
        self, client: TestClient, a_saved_filter: int
    ) -> None:
        """Measured: on a page past the end the body still holds the pseudo project while
        the result-count header says 0. Asserting only the header, or only the length,
        misses that they are deliberately inconsistent."""
        resp = client.get("/api/v1/projects?page=99&per_page=5")

        assert resp.headers["x-pagination-result-count"] == "0"
        assert [project["id"] for project in resp.json()] == [a_saved_filter]

    def test_it_belongs_to_its_owner_only(self, app: FastAPI, a_saved_filter: int) -> None:
        ids = [project["id"] for project in as_user(app, BOB).get("/api/v1/projects").json()]

        assert a_saved_filter not in ids

    def test_the_pseudo_project_is_not_persisted(
        self, client: TestClient, a_saved_filter: int, session: Session
    ) -> None:
        """It is assembled per request. Adding it to the session would create a row in
        ``projects`` with a negative id, which nothing would ever clean up."""
        client.get("/api/v1/projects")

        assert session.get(Project, a_saved_filter) is None


# --- the reparent gates ------------------------------------------------------


class TestReparentGates:
    """The five cells of CVE-2026-35595 / CVE-2026-55064, over HTTP.

    ⚠️ Every cell that sends ``parent_project_id`` sends a value that **actually
    differs** from the current parent. A cell that resends the existing parent tests the
    "unchanged" rule instead, silently: measuring this on the reference server, a detach
    to 0 came back 200 for a write-only user and looked like proof that the gate does not
    exist — the project's parent was already 0, so no gate ever fired. Re-run against a
    project genuinely under a parent it answered 403. The gate is real; the input was a
    fixed point.
    """

    def test_write_user_editing_a_title_without_touching_parent_is_allowed(
        self, app: FastAPI
    ) -> None:
        """Cell 1. Guards the base: CanUpdate rests on CanWrite, not on Admin.

        Implemented as admin-only, this is the cell that goes red — and only this one, so
        without it the mistake ships.
        """
        resp = as_user(app, BOB).post(f"/api/v1/projects/{C2}", json={"title": "by-bob"})

        assert resp.status_code == 200
        assert resp.json()["title"] == "by-bob"

    def test_write_user_resending_the_same_parent_is_allowed(self, app: FastAPI) -> None:
        """Cell 2, the most easily missed one: the gates key off *change*, not presence.
        Firing them on presence alone breaks every read-modify-write client, which always
        sends the field back."""
        resp = as_user(app, BOB).post(
            f"/api/v1/projects/{C2}", json={"title": "by-bob", "parent_project_id": C1}
        )

        assert resp.status_code == 200
        assert resp.json()["parent_project_id"] == C1

    def test_write_user_moving_to_a_different_parent_is_refused(self, app: FastAPI) -> None:
        """Cell 3. Gate 2: Admin on the project being moved. bob has write on C2 only."""
        resp = as_user(app, BOB).post(
            f"/api/v1/projects/{C2}", json={"title": "x", "parent_project_id": BOB_OWN}
        )

        assert resp.status_code == 403
        assert resp.json() == {"code": 1, "message": "You're not allowed to do this."}

    def test_admin_on_the_project_still_needs_admin_on_the_new_parent(
        self, app: FastAPI, session: Session
    ) -> None:
        """Cell 4 — the one that catches "only checked CanWrite on the new parent",
        i.e. an implementation that has not fixed CVE-2026-35595. carol is admin on C2
        and holds nothing on 903."""
        resp = as_user(app, CAROL).post(
            f"/api/v1/projects/{C2}", json={"title": "x", "parent_project_id": BOB_OWN}
        )

        assert resp.status_code == 403
        session.expire_all()
        assert stored_project(session, C2).parent_project_id == C1, "the move must not happen"

    def test_detaching_to_the_top_level_needs_admin_on_the_project(
        self, app: FastAPI, session: Session
    ) -> None:
        """Cell 5, refusal half (CVE-2026-55064). C2's parent is C1, so sending 0 is a
        real change and gate 2 fires."""
        resp = as_user(app, BOB).post(
            f"/api/v1/projects/{C2}", json={"title": "x", "parent_project_id": 0}
        )

        assert resp.status_code == 403
        session.expire_all()
        assert stored_project(session, C2).parent_project_id == C1

    def test_an_admin_may_detach_to_the_top_level(self, app: FastAPI, session: Session) -> None:
        """Cell 5, permitted half. Without it the cell above is satisfied by refusing
        every detach."""
        resp = as_user(app, CAROL).post(
            f"/api/v1/projects/{C2}", json={"title": "x", "parent_project_id": 0}
        )

        assert resp.status_code == 200
        session.expire_all()
        assert stored_project(session, C2).parent_project_id == 0


class TestParentProjectIdIsExemptFromTheFullReplace:
    """The four cells of AC-6. Omitted and null must be indistinguishable."""

    def test_omitting_the_parent_keeps_it(self, client: TestClient, session: Session) -> None:
        resp = client.post(f"/api/v1/projects/{C2}", json={"title": "renamed"})

        assert resp.status_code == 200
        assert resp.json()["parent_project_id"] == C1

    def test_an_explicit_null_also_keeps_it(self, client: TestClient) -> None:
        """The cell our own UI can never produce.

        Go's decoder gives a nil pointer for both a missing key and an explicit null, so
        the two are identical upstream. Pydantic *can* tell them apart, and a
        ``model_fields_set`` check reads this as "detach" and flattens the project tree —
        for third-party and MCP clients only.
        """
        resp = client.post(
            f"/api/v1/projects/{C2}", json={"title": "renamed", "parent_project_id": None}
        )

        assert resp.status_code == 200
        assert resp.json()["parent_project_id"] == C1

    def test_an_explicit_zero_detaches(self, client: TestClient) -> None:
        """The cell that stops "keep the parent" being implemented as "never write it"."""
        resp = client.post(
            f"/api/v1/projects/{C2}", json={"title": "Child", "parent_project_id": 0}
        )

        assert resp.status_code == 200
        assert resp.json()["parent_project_id"] == 0

    def test_an_explicit_value_moves_it(self, client: TestClient) -> None:
        resp = client.post(
            f"/api/v1/projects/{C2}", json={"title": "Child", "parent_project_id": A_PARENT}
        )

        assert resp.status_code == 200
        assert resp.json()["parent_project_id"] == A_PARENT


class TestDescriptionCanBeOverwrittenButNeverCleared:
    """The second whitelist exception, and the one a pointer scan misses entirely.

    ``description`` is not a pointer — the mechanism is ``if project.Description != ""``,
    so an empty string *is* the "leave it alone" signal and there is no way to express
    "clear it". ``hex_color`` rides along in every case as the control: it is in the
    whitelist unconditionally, so it resets. Asserting them **in the same request** is
    what shows the boundary between the two rules; separately, each looks like noise.
    """

    def test_omitting_description_keeps_it_while_hex_color_resets(self, client: TestClient) -> None:
        resp = client.post(f"/api/v1/projects/{FULL}", json={"title": "T1"})

        assert resp.status_code == 200
        assert resp.json()["description"] == "will be reset"
        assert resp.json()["hex_color"] == ""

    def test_an_empty_string_does_not_clear_it(self, client: TestClient) -> None:
        resp = client.post(f"/api/v1/projects/{FULL}", json={"title": "T2", "description": ""})

        assert resp.json()["description"] == "will be reset"

    def test_a_new_value_overwrites_it(self, client: TestClient) -> None:
        """Without this the two above are satisfied by never writing the column at all."""
        resp = client.post(f"/api/v1/projects/{FULL}", json={"title": "T4", "description": "y"})

        assert resp.json()["description"] == "y"


# --- 403 / 404 layering ------------------------------------------------------


class TestWhichLayerAnswersWhat:
    """Practice 26: CRUDRouter is policy-then-service, so the policy can only ever
    produce 403. Every 404 below exists **because** the policy deliberately passes the
    missing case through. A policy that refused it would turn all of these into 403 and
    the change would read as pure hardening."""

    def test_reading_a_missing_project_is_404_not_403(self, client: TestClient) -> None:
        resp = client.get("/api/v1/projects/999999")

        assert resp.status_code == 404
        assert resp.json() == {"code": 3001, "message": "This project does not exist."}

    def test_updating_a_missing_project_is_404_not_403(self, client: TestClient) -> None:
        resp = client.post("/api/v1/projects/999999", json={"title": "x"})

        assert resp.status_code == 404
        assert resp.json()["code"] == 3001

    def test_deleting_a_missing_project_is_404_not_403(self, client: TestClient) -> None:
        resp = client.delete("/api/v1/projects/999999")

        assert resp.status_code == 404
        assert resp.json()["code"] == 3001

    def test_reading_someone_elses_project_is_403_with_the_read_wording(self, app: FastAPI) -> None:
        """Paired with the 404 above: together they show the two cases stay
        distinguishable. Either one alone is satisfied by answering it everywhere."""
        resp = as_user(app, DAVE).get(f"/api/v1/projects/{P_ROOT}")

        assert resp.status_code == 403
        assert resp.json() == {
            "code": 0,
            "message": "You don't have the permission to see this",
        }

    def test_updating_someone_elses_project_is_the_pipeline_403(self, app: FastAPI) -> None:
        """Code 0, not code 1: the CRUD pipeline's Forbidden, not models.ErrGenericForbidden.
        The reparent gates use the other one, so the two codes distinguish "you may not
        touch this project" from "you may not make this particular move"."""
        resp = as_user(app, DAVE).post(f"/api/v1/projects/{P_ROOT}", json={"title": "x"})

        assert resp.status_code == 403
        assert resp.json() == {"code": 0, "message": "Forbidden"}

    def test_a_write_collaborator_may_not_delete(self, app: FastAPI) -> None:
        """Delete needs admin while update needs write, so bob can rename C2 and cannot
        delete it. Same user, same project, two different answers."""
        assert (
            as_user(app, BOB).post(f"/api/v1/projects/{C2}", json={"title": "ok"}).status_code
            == 200
        )

        resp = as_user(app, BOB).delete(f"/api/v1/projects/{C2}")

        assert resp.status_code == 403
        assert resp.json() == {"code": 0, "message": "Forbidden"}

    def test_a_non_numeric_id_is_400_not_fastapis_422(self, client: TestClient) -> None:
        """Echo fails at binding. Declaring ``project: int`` on the handler would answer
        422 with a ``detail`` body no v1 client can parse."""
        resp = client.get("/api/v1/projects/abc")

        assert resp.status_code == 400
        assert resp.json() == {"code": 2004, "message": INVALID_MODEL_MESSAGE}


class TestPseudoProjectsOnWritePaths:
    def test_updating_a_pseudo_project_is_403(self, client: TestClient) -> None:
        """Favorites has no row to write to. Upstream answers the pipeline's 403 rather
        than a 400 or a 404, so an MCP client is told "not allowed", not "malformed"."""
        resp = client.post("/api/v1/projects/-1", json={"title": "x"})

        assert resp.status_code == 403
        assert resp.json() == {"code": 0, "message": "Forbidden"}

    def test_deleting_a_pseudo_project_is_403(self, client: TestClient) -> None:
        resp = client.delete("/api/v1/projects/-1")

        assert resp.status_code == 403

    def test_reading_favorites_serves_the_synthetic_project(self, client: TestClient) -> None:
        """Was a recorded gap; now the real assertion. Grew out of the tripwire that used
        to assert 403 here — it went red the moment Favorites landed, which is what it was
        for. Do not collapse this back into a status check.

        Every value below is a constant in ``project.go:156-191``. Nothing is read from the
        ``favorites`` table — that is what ``GET /projects/-1/tasks`` needs, and the
        distinction is the whole reason this could be built before favourites exist.
        """
        resp = client.get("/api/v1/projects/-1")

        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == -1
        assert body["title"] == "Favorites"
        assert body["description"] == "This project has all tasks marked as favorites."
        # `is_favorite` is true and `position` is -1 — the two fields that make it sort and
        # render as a pinned project rather than an ordinary one.
        assert body["is_favorite"] is True
        assert body["position"] == -1
        assert body["parent_project_id"] == 0
        assert body["identifier"] == ""
        assert body["owner"]["id"] == ALICE, "every user owns their own Favorites"
        # ⚠️ Read (0), not the Admin (2) that a saved filter's pseudo project reports on
        # the same header. The two pseudo projects disagree here.
        assert resp.headers["x-max-permission"] == "0"

    def test_favorites_has_three_views_and_no_kanban(self, client: TestClient) -> None:
        """⚠️ The **absence** of the Kanban view is the assertion.

        A real project is created with four views; Favorites has three, because it has no
        buckets to hang a board on. Building this from the ordinary default-view helper is
        the obvious implementation and yields four — and every field of the three that do
        exist would still be correct, so only a count-and-kind assertion catches it. That
        is why this is a separate test with the omission in its name.
        """
        views = client.get("/api/v1/projects/-1").json()["views"]

        assert [view["view_kind"] for view in views] == ["list", "gantt", "table"]
        assert "kanban" not in [view["view_kind"] for view in views]
        assert [view["id"] for view in views] == [-1, -2, -3]
        assert [view["project_id"] for view in views] == [-1, -1, -1]
        assert [view["position"] for view in views] == [100, 200, 300]
        # Only List carries a filter, and it hides completed tasks. Gantt and Table are
        # null — not an empty filter object, which is a different JSON value.
        assert views[0]["filter"]["filter"] == "done = false"
        assert views[1]["filter"] is None
        assert views[2]["filter"] is None

    def test_favorites_timestamps_do_not_move_between_requests(self, client: TestClient) -> None:
        """⚠️ ``created``/``updated`` are the **process start time**, not the request time.

        Upstream evaluates ``time.Now()`` in a package-level ``var`` initialiser, so the
        value is fixed for the life of the process. Measured on the reference server: two
        requests 1.1 s apart came back byte-identical. A per-request ``utcnow()`` here
        would look right in any single response and would give a client polling this
        endpoint a project whose creation date advances every time it asks.
        """
        first = client.get("/api/v1/projects/-1").json()
        second = client.get("/api/v1/projects/-1").json()

        assert first["created"] == second["created"]
        assert first["updated"] == second["updated"]

    def test_favorites_is_not_listed_by_the_collection(self, client: TestClient) -> None:
        """Reachable by id, absent from ``GET /projects``. Measured: the only negative id
        in alice's project list is her saved filter's, never -1. An implementation that
        appended Favorites alongside the saved-filter pseudo projects — they are built in
        the same place and it reads as an oversight that it is missing — would add a
        project to every user's sidebar that upstream does not send."""
        listed = client.get("/api/v1/projects").json()

        assert -1 not in [entry["id"] for entry in listed]


# --- delete semantics --------------------------------------------------------


class TestDeleteIsRecursiveAndHard:
    def test_descendants_go_too(self, client: TestClient, session: Session) -> None:
        """P-root -> C1 -> C2, so this needs real recursion rather than one level."""
        resp = client.delete(f"/api/v1/projects/{P_ROOT}")

        assert resp.status_code == 200
        assert resp.json() == {"message": "Successfully deleted."}
        session.expire_all()
        assert session.get(Project, P_ROOT) is None
        assert session.get(Project, C1) is None
        assert session.get(Project, C2) is None

    def test_tasks_are_physically_gone_not_soft_deleted(
        self, client: TestClient, session: Session
    ) -> None:
        """A soft delete would leave rows behind with nothing to restore them into.
        Queried without the soft-delete filter, so a ``deleted_at`` marker still fails."""
        client.delete(f"/api/v1/projects/{P_ROOT}")

        session.expire_all()
        remaining = session.execute(
            select(Task.id)
            .where(Task.project_id.in_([P_ROOT, C1, C2]))
            .execution_options(include_deleted=True)
        ).all()
        assert remaining == []

    def test_the_views_go_too(self, client: TestClient, session: Session) -> None:
        created = client.put("/api/v1/projects", json={"title": "with views"}).json()

        client.delete(f"/api/v1/projects/{created['id']}")

        session.expire_all()
        assert (
            session.scalars(
                select(ProjectView).where(ProjectView.project_id == created["id"])
            ).all()
            == []
        )

    def test_a_child_owned_by_someone_else_is_deleted_too(
        self, client: TestClient, session: Session
    ) -> None:
        """@critical. 905 belongs to bob and alice deletes it by deleting its parent.

        The recursion deliberately does **not** re-check CanDelete. Adding that check is
        the intuitive hardening and diverges from upstream — which is why this asserts
        bob's project and bob's task are both gone, not merely that the parent is.
        """
        resp = client.delete(f"/api/v1/projects/{A_PARENT}")

        assert resp.status_code == 200
        session.expire_all()
        assert session.get(Project, B_CHILD) is None
        assert session.get(Task, 904) is None


# --- projectusers ------------------------------------------------------------


class TestProjectUsers:
    def test_a_project_with_only_an_owner_lists_exactly_that_owner(
        self, client: TestClient
    ) -> None:
        """An exact list, not a count: a rule that over-counts (every member of every
        team) or under-counts (owner omitted) hits the right length surprisingly often."""
        resp = client.get(f"/api/v1/projects/{P_ROOT}/projectusers")

        assert resp.status_code == 200
        assert [user["username"] for user in resp.json()] == ["alice"]

    def test_grants_are_included_and_ordered_by_id(self, client: TestClient) -> None:
        resp = client.get(f"/api/v1/projects/{C2}/projectusers")

        assert [user["username"] for user in resp.json()] == ["alice", "bob", "carol"]

    def test_the_403_body_carries_no_code_at_all(self, app: FastAPI) -> None:
        """⚠️ The only error body in the whole API with no ``code`` key.

        Every other 403 has one — ``code: 0``, ``code: 1``, ``code: 4005``. Serialising an
        error struct naturally produces a code, so an implementation that fills one in
        here adds a key upstream does not send, and a test asserting only the status
        never sees it. Hence the exact-body comparison.
        """
        resp = as_user(app, DAVE).get(f"/api/v1/projects/{P_ROOT}/projectusers")

        assert resp.status_code == 403
        assert resp.json() == {"message": "Forbidden"}

    def test_a_missing_project_is_404(self, client: TestClient) -> None:
        resp = client.get("/api/v1/projects/999999/projectusers")

        assert resp.status_code == 404
        assert resp.json()["code"] == 3001

    def test_no_email_is_disclosed(self, client: TestClient, session: Session) -> None:
        """A privacy property, not an omission — this route lists everyone who can reach
        a project. Measured: upstream omits the key even for a user who has an address,
        so the seeded user is given one here to make the case discriminating."""
        stored_user(session, ALICE).email = "alice@example.test"
        session.commit()

        resp = client.get(f"/api/v1/projects/{P_ROOT}/projectusers")

        assert resp.status_code == 200
        assert "email" not in resp.json()[0]


# --- boundaries --------------------------------------------------------------


class TestBoundaries:
    def test_a_duplicate_identifier_is_refused(self, client: TestClient) -> None:
        client.post(f"/api/v1/projects/{A_PARENT}", json={"title": "A-parent", "identifier": "DUP"})

        resp = client.post(f"/api/v1/projects/{FULL}", json={"title": "Full", "identifier": "DUP"})

        assert resp.status_code == 400
        assert resp.json()["code"] == 3007

    def test_identifiers_collide_case_insensitively(self, client: TestClient) -> None:
        """Stored uppercase so the answer does not depend on the database's collation:
        SQLite and Postgres compare case-sensitively, MySQL does not."""
        client.post(f"/api/v1/projects/{A_PARENT}", json={"title": "A-parent", "identifier": "DUP"})

        resp = client.post(f"/api/v1/projects/{FULL}", json={"title": "Full", "identifier": "dup"})

        assert resp.status_code == 400
        assert resp.json()["code"] == 3007

    def test_a_cyclic_move_is_refused(self, client: TestClient) -> None:
        """P-root under its own grandchild. 412/3011, not a 403 — upstream validates the
        hierarchy before applying the permission gates, so the client is told what is
        actually wrong."""
        resp = client.post(
            f"/api/v1/projects/{P_ROOT}", json={"title": "P-root", "parent_project_id": C2}
        )

        assert resp.status_code == 412
        assert resp.json()["code"] == 3011

    def test_a_project_cannot_be_its_own_parent(self, client: TestClient) -> None:
        resp = client.post(
            f"/api/v1/projects/{P_ROOT}", json={"title": "P-root", "parent_project_id": P_ROOT}
        )

        assert resp.status_code == 412
        assert resp.json()["code"] == 3010

    def test_a_pseudo_parent_is_refused(self, client: TestClient) -> None:
        resp = client.post(
            f"/api/v1/projects/{P_ROOT}", json={"title": "P-root", "parent_project_id": -1}
        )

        assert resp.status_code == 412
        assert resp.json()["code"] == 3009

    def test_a_type_mismatch_is_the_bind_error_not_the_validation_one(
        self, client: TestClient
    ) -> None:
        """400/2004, **not** 412/2002 — measured for all four of these bodies.

        Upstream separates the two exits: encoding/json refusing a value is a *bind*
        failure, while a bound-but-invalid value is validation. So an empty title is 412
        with ``invalid_fields`` and ``"position": "5"`` is a bare 400, even though both
        are "bad input". Strict mode is what puts Calton on the same side of that line;
        without it Pydantic would coerce ``"5"`` to ``5`` and answer 201, persisting a
        value the client never sent.
        """
        for body in (
            {"title": "t", "position": "5"},
            {"title": "t", "is_archived": "yes"},
            {"title": 5},
            {"title": "t", "parent_project_id": "9"},
        ):
            resp = client.put("/api/v1/projects", json=body)

            assert resp.status_code == 400, body
            assert resp.json() == {
                "code": 2004,
                "message": INVALID_MODEL_MESSAGE,
            }, body

    def test_a_duplicate_identifier_outranks_a_refused_reparent(self, app: FastAPI) -> None:
        """Measured precedence, and the reason the identifier check sits where it does.

        bob's request is refused twice over — the identifier is taken *and* he may not
        move C2 — and upstream reports the identifier. Run the checks in the other order
        and this is a 403.
        """
        as_user(app, ALICE).post(
            f"/api/v1/projects/{A_PARENT}", json={"title": "A-parent", "identifier": "DUP"}
        )

        resp = as_user(app, BOB).post(
            f"/api/v1/projects/{C2}",
            json={"title": "x", "identifier": "DUP", "parent_project_id": BOB_OWN},
        )

        assert resp.status_code == 400
        assert resp.json()["code"] == 3007

    def test_a_cycle_outranks_a_duplicate_identifier(self, client: TestClient) -> None:
        """The other half of the ordering: the hierarchy is validated before the
        identifier, so these three checks have one measured order and not merely a
        pairwise one."""
        client.post(f"/api/v1/projects/{A_PARENT}", json={"title": "A-parent", "identifier": "DUP"})

        resp = client.post(
            f"/api/v1/projects/{P_ROOT}",
            json={"title": "P-root", "identifier": "DUP", "parent_project_id": C2},
        )

        assert resp.status_code == 412
        assert resp.json()["code"] == 3011


# --- the session contract ----------------------------------------------------


class TestPolicyAndServiceShareTheRequestSession:
    """Practice 10, with the **real** ProjectPolicy.

    An earlier version of this assertion used a stand-in policy that recorded the session
    it was handed. That pins the *router*: it proves CRUDRouter passes a session down. It
    says nothing about whether the real policy uses it — a ProjectPolicy that opened its
    own session internally would keep that test green, and the failure it causes (a
    permission check that cannot see the write it guards) is exactly the kind that only
    shows up under concurrency in production.

    So this drives the real policy against a session holding **uncommitted** state, where
    "used the session it was given" and "opened its own" produce different status codes.

    ⚠️ It runs on a **file-backed** database on purpose. The rest of this file shares one
    in-memory connection through ``StaticPool``, and on a shared connection an uncommitted
    row is visible to every session — which makes the whole experiment a fixed point. The
    premise test below is what caught that: it failed on the shared-connection setup,
    proving the discriminating test could not have discriminated.
    """

    @pytest.fixture
    def file_engine(self, tmp_path: Any) -> Iterator[Engine]:
        from sqlalchemy import create_engine

        # No StaticPool: each session gets its own connection and therefore real
        # transaction isolation.
        built = create_engine(f"sqlite+pysqlite:///{tmp_path / 'isolation.db'}")
        Base.metadata.create_all(built)
        yield built
        built.dispose()

    @pytest.fixture
    def file_sessions(self, file_engine: Engine) -> sessionmaker[Session]:
        factory = session_factory(file_engine)
        with factory() as opened:
            _seed(opened)
        return factory

    @pytest.fixture
    def file_app(self, file_engine: Engine, file_sessions: sessionmaker[Session]) -> FastAPI:
        application = create_app(engine=file_engine)
        application.state.session_factory = file_sessions

        @application.middleware("http")
        async def _stub_auth(request, call_next):  # type: ignore[no-untyped-def]
            header = request.headers.get("x-test-user")
            if header:
                request.state.auth = SimpleNamespace(id=int(header))
            return await call_next(request)

        # Third copy of this override in this file (see the module-level `app` fixture).
        # Every locally-built app needs it, which is exactly why the auth wiring is
        # asserted in one dedicated place rather than relied on here.
        application.dependency_overrides[get_auth_subject] = lambda: None

        return application

    @pytest.fixture
    def uncommitted(
        self, file_app: FastAPI, file_sessions: sessionmaker[Session]
    ) -> Iterator[tuple[int, sessionmaker[Session]]]:
        """A project that exists **only** inside the session the request will use.

        Flushed so it has an id and answers queries on this session; never committed, so
        no other connection can see it.
        """
        session = file_sessions()
        project = Project(title="only-in-this-session", identifier="", owner_id=ALICE, position=9)
        session.add(project)
        session.flush()

        file_app.dependency_overrides[get_db] = lambda: session
        yield project.id, file_sessions
        file_app.dependency_overrides.clear()
        session.rollback()
        session.close()

    def test_the_premise_that_makes_the_next_test_discriminating(
        self, uncommitted: tuple[int, sessionmaker[Session]]
    ) -> None:
        """Practice 24: assert the assumption the real test rests on.

        If an uncommitted row were visible to other sessions, the test below would pass
        whichever session the policy used and would be quietly worthless. **This is not a
        hypothetical** — it is exactly what happened on the shared in-memory connection,
        and this assertion is the only reason it was noticed.
        """
        project_id, sessions = uncommitted

        with sessions() as other:
            assert other.get(Project, project_id) is None

    def test_the_real_policy_sees_a_write_made_in_the_request_session(
        self, file_app: FastAPI, uncommitted: tuple[int, sessionmaker[Session]]
    ) -> None:
        """200 only if ``ProjectPolicy.can_read`` ran its permission query on the session
        it was handed. A policy that opened its own would not find the row and this would
        be a 403."""
        project_id, _ = uncommitted

        resp = as_user(file_app, ALICE).get(f"/api/v1/projects/{project_id}")

        assert resp.status_code == 200, (
            "the policy could not see an uncommitted row from the request session, "
            "so it is not using the session it was given"
        )
        assert resp.json()["id"] == project_id

    def test_one_request_creates_exactly_one_session(self, file_app: FastAPI) -> None:
        """The same contract from the other side, and the one that catches a policy
        opening a session from the injected factory rather than from the engine.

        ``POST`` exercises policy *and* service in one request, so anything above one is
        a layer that went and got its own.
        """
        created: list[Session] = []
        real_factory = file_app.state.session_factory

        def counting_factory() -> Session:
            # Annotated because sessionmaker.__call__ is typed loosely; without it
            # strict mode flags the return as Any and the count below would be
            # counting objects mypy cannot vouch for.
            opened: Session = real_factory()
            created.append(opened)
            return opened

        file_app.state.session_factory = counting_factory

        resp = as_user(file_app, ALICE).post(f"/api/v1/projects/{C2}", json={"title": "once"})

        assert resp.status_code == 200
        assert len(created) == 1, f"{len(created)} sessions opened for one request"


class TestTheDefaultProjectForANewUser:
    """Registration has to leave the new account with a usable Inbox.

    Measured: straight after ``POST /register`` the reference server reports
    ``settings.default_project_id`` as a real project id, and that project is titled
    "Inbox" with the four default views already on it. Calton registers users with the
    column unset, so the frontend opens an empty default project.
    """

    def test_it_creates_an_inbox_with_the_four_views(self, session: Session) -> None:
        from calton.services.project_service import create_default_project_for

        user = User(id=990, username="newcomer", created=EPOCH, updated=EPOCH)
        session.add(user)
        session.flush()

        project = create_default_project_for(session, user)

        assert project.title == "Inbox"
        assert project.owner_id == user.id
        views = session.scalars(
            select(ProjectView).where(ProjectView.project_id == project.id)
        ).all()
        assert [view.title for view in views] == ["List", "Gantt", "Table", "Kanban"]

    def test_it_points_default_project_id_at_that_project(self, session: Session) -> None:
        """The half that makes it *the default* rather than merely a project.

        Asserted separately because creating the project and recording it on the user are
        two writes, and only the second is what ``GET /user`` reports.
        """
        from calton.services.project_service import create_default_project_for

        user = User(id=991, username="newcomer2", created=EPOCH, updated=EPOCH)
        session.add(user)
        session.flush()

        project = create_default_project_for(session, user)

        assert user.default_project_id == project.id
        assert user.default_project_id != 0

    def test_registration_actually_creates_the_inbox(self, app: FastAPI) -> None:
        """Registration goes through the hook — asserted over HTTP, not at the service.

        This replaces a handoff tripwire. ``create_default_project_for`` was delivered on
        one branch while ``/register`` was still on another, so nothing called it: a
        delivered module that is not connected, which is not a delivery. Rather than
        leave that as a sentence in a report, the tripwire skipped while ``/register``
        was absent and failed the moment it appeared — it went red inside the merge that
        brought registration in, which is the whole point of writing it that way.

        It asserts through ``POST /register`` rather than by calling the service, because
        the defect it guards is exactly "the service is fine and nobody calls it".
        """
        assert "/api/v1/register" in app.openapi()["paths"], (
            "registration disappeared from the app; this test guards the wiring between "
            "registration and the default project, so it cannot pass without it"
        )

        client = TestClient(app, raise_server_exceptions=False)
        # POST, not PUT: v1 creates resources with PUT, but the auth endpoints are the
        # exception — measured from the app's own OpenAPI document rather than assumed.
        registered = client.post(
            "/api/v1/register",
            json={"username": "hooked", "password": "12345678", "email": "hooked@example.test"},
        )
        assert registered.status_code == 200, registered.text

        with app.state.session_factory() as check:
            user = check.scalars(select(User).where(User.username == "hooked")).one()
            assert user.default_project_id, "registration did not point the user at a project"

            inbox = check.get(Project, user.default_project_id)
            assert inbox is not None
            assert inbox.title == "Inbox"
            assert inbox.owner_id == user.id

            views = check.scalars(
                select(ProjectView).where(ProjectView.project_id == inbox.id)
            ).all()
            assert len(views) == 4, f"expected the four default views, got {len(views)}"


class TestArchivedIsInheritedOnTheWire:
    """A project under an archived parent reports and *filters* as archived.

    The unit tests in ``test_project_service`` cover the rule; these cover the two places
    it has to reach the client, because those are separate wirings and each can be left
    out without the other noticing:

    * the serialised ``is_archived`` field, and
    * ``GET /projects``, which hides archived projects by default.

    ⚠️ Every project here is archived by **writing the column directly**, never through
    the update endpoint. Going through the endpoint also runs the write-time propagation,
    which sets the child's own flag — and then every assertion below passes with the
    inheritance deleted. The row this reproduces (seed project 21, "Test21 archived
    through parent list") is exactly a child whose own column was never written.
    """

    def _archive_the_root(self, session: Session) -> None:
        stored_project(session, P_ROOT).is_archived = True
        session.commit()
        assert stored_project(session, C1).is_archived is False, (
            "the sample stops discriminating the moment the child's own column is set"
        )

    def test_the_child_reports_archived(self, client: TestClient, session: Session) -> None:
        self._archive_the_root(session)

        response = client.get(f"/api/v1/projects/{C1}")

        assert response.status_code == 200, response.text
        assert response.json()["is_archived"] is True

    def test_the_grandchild_reports_archived(self, client: TestClient, session: Session) -> None:
        self._archive_the_root(session)

        assert client.get(f"/api/v1/projects/{C2}").json()["is_archived"] is True

    def test_it_is_hidden_from_the_default_collection(
        self, client: TestClient, session: Session
    ) -> None:
        before = {p["id"] for p in client.get("/api/v1/projects").json()}
        assert {P_ROOT, C1, C2} <= before, "all three must start visible, or this proves nothing"

        self._archive_the_root(session)

        after = {p["id"] for p in client.get("/api/v1/projects").json()}
        assert after.isdisjoint({P_ROOT, C1, C2})
        # A live project stays: "hide everything" would satisfy the line above.
        assert after, "unrelated projects must survive the exclusion"

    def test_it_comes_back_with_is_archived_true(
        self, client: TestClient, session: Session
    ) -> None:
        """``?is_archived=true`` means *include* archived, not *only* archived."""
        self._archive_the_root(session)

        listed = client.get("/api/v1/projects", params={"is_archived": "true"}).json()
        by_id = {p["id"]: p for p in listed}

        assert {P_ROOT, C1, C2} <= set(by_id)
        assert by_id[C1]["is_archived"] is True

    def test_creating_under_an_inherited_archived_project_is_412(
        self, client: TestClient, session: Session
    ) -> None:
        """★ C1's own column is 0; only the inherited reading refuses this."""
        self._archive_the_root(session)

        response = client.put("/api/v1/projects", json={"title": "child", "parent_project_id": C1})

        assert response.status_code == 412, response.text
        assert response.json()["code"] == 3008


def test_project_field_order_is_upstreams_wire_order() -> None:
    """Declaration order in ProjectRead is the wire order, so it is contractual.

    ⚠️ This exists because the harness check that would otherwise catch it —
    `key_order_diffs` in compare.py — is deliberately switched OFF until the
    read/write serialiser split lands. Without an assertion here, the field order
    is unguarded in the meantime, and the edit that breaks it is an attractive
    one: moving `is_favorite` up beside `is_archived` groups the two `is_*` flags
    and reads better. Upstream emits it after `background_blur_hash`.

    Measured against a running reference server (GET /projects), not read off the
    Go struct. `subscription` is ours alone — declared for the contract and never
    emitted (see the module docstring in schemas/project.py) — so it is dropped
    before comparing rather than added to upstream's list.
    """
    from calton.schemas.project import ProjectRead

    upstream_order = [
        "id",
        "title",
        "description",
        "identifier",
        "hex_color",
        "parent_project_id",
        "owner",
        "is_archived",
        "background_information",
        "background_blur_hash",
        "is_favorite",
        "position",
        "views",
        "max_permission",
        "created",
        "updated",
    ]
    ours = [name for name in ProjectRead.model_fields if name in set(upstream_order)]

    assert ours == upstream_order, (
        "ProjectRead's field order no longer matches upstream's wire order.\n"
        f"  upstream: {upstream_order}\n"
        f"  ours:     {ours}"
    )
