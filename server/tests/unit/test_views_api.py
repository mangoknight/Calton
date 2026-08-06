"""The project view endpoints, driven over HTTP through ``create_app``.

Every expectation here was measured on a running Go reference server (probes in
``harness/probe_views*.py``), not read off ``pkg/models/project_view.go``. Where the two
disagreed the measurement won — the source says ``Delete`` removes the view's buckets in
the sense that a reader expects it to, and the table says the bucket rows are still there.

The world mirrors ``harness/seed/overlay/assoc.yml`` and ``perm.yml`` so a case here and a
parity case describe the same fixture:

===  =========================================================================
950  alice's project, four views 950-953 (List/Gantt/Table/Kanban) and 7 tasks.
     View 950 carries the default ``done = false`` filter; 951-953 carry none.
902  C2, alice's — **bob has write, carol has admin, dave has nothing**. The
     only place the ladder can be measured: "write is enough" and "admin is
     required" give the same answer on every project where the caller is owner.
903  Bob-Own, bob's. alice's 403 control.
===  =========================================================================

⚠️ 902 is not decoration. Without a subject holding write-but-not-admin, the create/update
/delete assertions below cannot tell the implemented rule from the wrong one — both would
pass on any project the caller owns.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# starlette's TestClient is backed by httpx2 here, not httpx — the annotation on
# TestClient.get still says `httpx.Response`, which is not importable in this venv.
from httpx2 import Response
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from calton.auth.deps import get_auth_subject
from calton.db.base import Base
from calton.db.session import session_factory
from calton.main import create_app
from calton.models import Project, Task, User
from calton.models.bucket import Bucket
from calton.models.project_view import ProjectView
from calton.models.saved_filter import SavedFilter
from calton.models.task_position import TaskBucket, TaskPosition
from calton.models.team import ProjectUser

ALICE, BOB, CAROL, DAVE = 900, 901, 902, 903
#: alice's saved filter, addressed as pseudo project -951 (``id * -1 - 1``).
ALICE_FILTER, ALICE_FILTER_PROJECT = 950, -951
FAVORITES = -1
C2, BOB_OWN = 902, 903
PROJ = 950
LIST_VIEW, GANTT_VIEW, TABLE_VIEW, KANBAN_VIEW = 950, 951, 952, 953

EPOCH = datetime(2026, 1, 1, tzinfo=UTC)

#: What upstream stores in ``project_views.filter`` — the whole marshalled TaskCollection,
#: not the expression. A view seeded with the bare string instead would make every
#: assertion about ``filter`` below pass against a broken serializer.
DONE_FALSE_FILTER = (
    '{"s":"","sort_by":null,"order_by":null,"filter":"done = false","filter_include_nulls":false}'
)


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
            Project(id=PROJ, title="Assoc", identifier="", owner_id=ALICE, position=1),
            Project(id=C2, title="C2", identifier="", owner_id=ALICE, position=2),
            Project(id=BOB_OWN, title="Bob-Own", identifier="", owner_id=BOB, position=3),
        ]
    )
    session.add_all(
        [
            ProjectUser(project_id=C2, user_id=BOB, permission=1),
            ProjectUser(project_id=C2, user_id=CAROL, permission=2),
        ]
    )
    session.add_all(
        [
            ProjectView(
                id=LIST_VIEW,
                project_id=PROJ,
                title="List",
                view_kind=0,
                position=100,
                filter=DONE_FALSE_FILTER,
                bucket_configuration_mode=0,
                created=EPOCH,
                updated=EPOCH,
            ),
            ProjectView(
                id=GANTT_VIEW,
                project_id=PROJ,
                title="Gantt",
                view_kind=1,
                position=200,
                bucket_configuration_mode=0,
                created=EPOCH,
                updated=EPOCH,
            ),
            ProjectView(
                id=TABLE_VIEW,
                project_id=PROJ,
                title="Table",
                view_kind=2,
                position=300,
                bucket_configuration_mode=0,
                created=EPOCH,
                updated=EPOCH,
            ),
            ProjectView(
                id=KANBAN_VIEW,
                project_id=PROJ,
                title="Kanban",
                view_kind=3,
                position=400,
                bucket_configuration_mode=1,
                default_bucket_id=950,
                done_bucket_id=952,
                created=EPOCH,
                updated=EPOCH,
            ),
        ]
    )
    session.add_all(
        [
            Bucket(
                id=950,
                project_view_id=KANBAN_VIEW,
                title="To-Do",
                position=100,
                created_by_id=ALICE,
                created=EPOCH,
                updated=EPOCH,
            ),
            Bucket(
                id=951,
                project_view_id=KANBAN_VIEW,
                title="Doing",
                position=200,
                created_by_id=ALICE,
                created=EPOCH,
                updated=EPOCH,
            ),
            Bucket(
                id=952,
                project_view_id=KANBAN_VIEW,
                title="Done",
                position=300,
                created_by_id=ALICE,
                created=EPOCH,
                updated=EPOCH,
            ),
        ]
    )
    session.add(
        SavedFilter(
            id=ALICE_FILTER,
            title="alice's filter",
            filters="done = false",
            owner_id=ALICE,
            created=EPOCH,
            updated=EPOCH,
        )
    )
    session.add_all(
        [
            Task(id=950 + n, project_id=PROJ, index=n + 1, title=f"t{n}", created_by_id=ALICE)
            for n in range(7)
        ]
    )
    session.add_all(
        [TaskBucket(bucket_id=950, task_id=950 + n, project_view_id=KANBAN_VIEW) for n in range(7)]
    )
    session.commit()


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    factory = session_factory(engine)
    with factory() as opened:
        _seed(opened)
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

    # See test_projects_api: overriding here means this file does not cover the auth
    # wiring. TestTheAuthChainIsWired in test_api_tokens.py does.
    application.dependency_overrides[get_auth_subject] = lambda: None
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, headers={"X-Test-User": str(ALICE)}, raise_server_exceptions=False)


def as_user(app: FastAPI, user_id: int) -> TestClient:
    return TestClient(app, headers={"X-Test-User": str(user_id)}, raise_server_exceptions=False)


def stored_view(session: Session, view_id: int) -> ProjectView:
    """The row, failing loudly if it is gone.

    ``session.get`` returns ``None`` for a missing row, and every caller below is asserting
    something *about* a row it expects to exist — so a silent ``None`` would turn "the view
    was deleted" into an AttributeError somewhere further down instead of a named failure.
    """
    view = session.get(ProjectView, view_id)
    assert view is not None, f"view {view_id} is not in the database"
    return view


def stored_filter(session: Session, view_id: int) -> str | None:
    return stored_view(session, view_id).filter


# --- wiring ------------------------------------------------------------------


class TestTheRoutesAreActuallyMounted:
    """Five routes, reachable over HTTP. A module that imports is not a module that serves."""

    def test_the_openapi_schema_lists_all_five(self, app: FastAPI) -> None:
        paths = app.openapi()["paths"]
        assert sorted(paths["/api/v1/projects/{project}/views"]) == ["get", "put"]
        assert sorted(paths["/api/v1/projects/{project}/views/{id}"]) == [
            "delete",
            "get",
            "post",
        ]

    def test_delete_declares_no_request_body(self, app: FastAPI) -> None:
        """The DELETE body override is read through a Request dependency on purpose.

        Declaring it as a route parameter would put a request body on DELETE in the
        generated contract, which upstream's swagger does not have.
        """
        delete = app.openapi()["paths"]["/api/v1/projects/{project}/views/{id}"]["delete"]
        assert "requestBody" not in delete

    def test_every_route_is_in_the_registry(self, app: FastAPI) -> None:
        """A route the registry does not know about answers 403 to every API token.

        ``app`` is a parameter so ``create_app`` has run and done the registering, even
        though the registry itself is a module-level singleton.
        """
        from calton.api.v1 import views as views_api
        from calton.core.route_registry import registry

        registered = set(registry.paths())
        for method, path in views_api.REGISTERED_ROUTES:
            assert (method, path) in registered, f"{method} {path} is not in route_registry"


# --- the wire shape ----------------------------------------------------------


class TestTheCollectionAndTheItemAgree:
    """Measured: for an ordinary project the two bodies are identical, key and value.

    ⚠️ This is the opposite of projects, where ``views`` and ``max_permission`` differ
    between the two shapes. The assertion exists because the *project* precedent makes
    "they must differ somewhere" the natural guess — it is pinned so a later change that
    introduces a difference here is caught rather than assumed correct.

    ⚠️ **"Ordinary project" is load-bearing.** On the Favorites pseudo project the two
    routes disagree outright: the collection lists nothing while the item serves three
    views. See :class:`TestFavoritesViews`.
    """

    def test_the_bodies_are_identical(self, client: TestClient) -> None:
        collection = client.get(f"/api/v1/projects/{PROJ}/views").json()
        item = client.get(f"/api/v1/projects/{PROJ}/views/{LIST_VIEW}").json()
        in_collection = next(v for v in collection if v["id"] == LIST_VIEW)
        assert in_collection == item

    def test_the_headers_do_not_agree(self, client: TestClient) -> None:
        """The difference between the two lives entirely in the headers."""
        collection = client.get(f"/api/v1/projects/{PROJ}/views")
        item = client.get(f"/api/v1/projects/{PROJ}/views/{LIST_VIEW}")

        assert collection.headers["x-pagination-result-count"] == "4"
        assert collection.headers["x-pagination-total-pages"] == "1"
        assert "x-max-permission" not in collection.headers

        assert item.headers["x-max-permission"] == "2"
        assert "x-pagination-result-count" not in item.headers


class TestTheViewBody:
    def test_the_full_shape_of_a_list_view(self, client: TestClient) -> None:
        body = client.get(f"/api/v1/projects/{PROJ}/views/{LIST_VIEW}").json()
        assert body == {
            "id": LIST_VIEW,
            "title": "List",
            "project_id": PROJ,
            "view_kind": "list",
            "filter": {
                "s": "",
                "sort_by": None,
                "order_by": None,
                "filter": "done = false",
                "filter_include_nulls": False,
            },
            "position": 100,
            "bucket_configuration_mode": "none",
            "bucket_configuration": None,
            "default_bucket_id": 0,
            "done_bucket_id": 0,
            "created": "2026-01-01T00:00:00Z",
            "updated": "2026-01-01T00:00:00Z",
        }

    def test_the_stored_filter_document_is_parsed_not_echoed(self, client: TestClient) -> None:
        """The column holds the whole marshalled TaskCollection, not the expression.

        Treating it as the expression puts the raw JSON text inside the object's own
        ``filter`` key — which is what the code did before this test existed, on every
        project response carrying a List view.
        """
        body = client.get(f"/api/v1/projects/{PROJ}/views/{LIST_VIEW}").json()
        assert body["filter"]["filter"] == "done = false"
        assert "{" not in body["filter"]["filter"]

    def test_a_view_with_no_filter_is_null_not_an_empty_object(self, client: TestClient) -> None:
        """951 and 950 differ on this field in the same project. Pure wire difference."""
        body = client.get(f"/api/v1/projects/{PROJ}/views/{GANTT_VIEW}").json()
        assert body["filter"] is None

    def test_both_enums_are_strings(self, client: TestClient) -> None:
        views = client.get(f"/api/v1/projects/{PROJ}/views").json()
        assert [(v["id"], v["view_kind"], v["bucket_configuration_mode"]) for v in views] == [
            (950, "list", "none"),
            (951, "gantt", "none"),
            (952, "table", "none"),
            # The Kanban view is manual, which is what `_view_shape.yaml` branches on.
            (953, "kanban", "manual"),
        ]

    def test_the_collection_is_ordered_by_position(self, client: TestClient) -> None:
        views = client.get(f"/api/v1/projects/{PROJ}/views").json()
        assert [v["position"] for v in views] == [100, 200, 300, 400]


class TestPaginationIsAcceptedAndIgnored:
    """``per_page`` reaches the headers and never reaches the query. Measured, not fixed."""

    def test_per_page_does_not_shorten_the_body(self, client: TestClient) -> None:
        response = client.get(f"/api/v1/projects/{PROJ}/views", params={"per_page": 2})
        assert len(response.json()) == 4
        assert response.headers["x-pagination-result-count"] == "4"
        # ...but total-pages is computed from per_page anyway, so the headers describe a
        # pagination the body did not perform.
        assert response.headers["x-pagination-total-pages"] == "2"

    def test_page_two_returns_the_same_four(self, client: TestClient) -> None:
        first = client.get(f"/api/v1/projects/{PROJ}/views", params={"per_page": 2, "page": 1})
        second = client.get(f"/api/v1/projects/{PROJ}/views", params={"per_page": 2, "page": 2})
        assert first.json() == second.json()


# --- the permission ladder ---------------------------------------------------


class TestWritingAViewNeedsAdminNotWrite:
    """bob has write on 902 and carol has admin. Only one of them may write a view.

    ⚠️ Every case here runs against 902 rather than a project alice owns, because on an
    owned project "write is enough" and "admin is required" give the same answer and the
    assertion would hold against either implementation.
    """

    @pytest.fixture
    def view_id(self, client: TestClient) -> int:
        created = client.put(f"/api/v1/projects/{C2}/views", json={"title": "seed"})
        assert created.status_code == 201
        return int(created.json()["id"])

    def test_write_may_read_both_shapes(self, app: FastAPI, view_id: int) -> None:
        bob = as_user(app, BOB)
        assert bob.get(f"/api/v1/projects/{C2}/views").status_code == 200
        assert bob.get(f"/api/v1/projects/{C2}/views/{view_id}").status_code == 200

    @staticmethod
    def _write(client: TestClient, verb: str, view_id: int) -> Response:
        """The three write verbs, issued the same way for both subjects.

        Shared so that "bob is refused" and "carol is allowed" are provably the *same*
        request differing only in who sent it. Two separately written call sites can drift
        into comparing two different requests and still look like a permission test.
        """
        if verb == "create":
            return client.put(f"/api/v1/projects/{C2}/views", json={"title": "x"})
        if verb == "update":
            return client.post(f"/api/v1/projects/{C2}/views/{view_id}", json={"title": "x"})
        return client.delete(f"/api/v1/projects/{C2}/views/{view_id}")

    @pytest.mark.parametrize("verb", ["create", "update", "delete"])
    def test_write_may_not_write(self, app: FastAPI, view_id: int, verb: str) -> None:
        response = self._write(as_user(app, BOB), verb, view_id)
        assert response.status_code == 403
        assert response.json() == {"code": 0, "message": "Forbidden"}

    @pytest.mark.parametrize(
        ("verb", "expected"), [("create", 201), ("update", 200), ("delete", 200)]
    )
    def test_admin_may_write(self, app: FastAPI, view_id: int, verb: str, expected: int) -> None:
        assert self._write(as_user(app, CAROL), verb, view_id).status_code == expected

    def test_the_max_permission_header_carries_the_project_permission(
        self, app: FastAPI, view_id: int
    ) -> None:
        assert (
            as_user(app, BOB)
            .get(f"/api/v1/projects/{C2}/views/{view_id}")
            .headers["x-max-permission"]
            == "1"
        )
        assert (
            as_user(app, CAROL)
            .get(f"/api/v1/projects/{C2}/views/{view_id}")
            .headers["x-max-permission"]
            == "2"
        )


class TestTheTwoReadRoutesRefuseDifferently:
    """read_all answers code 1, read_one answers code 0 with its own wording.

    A shared "forbidden" helper produces one of them on both routes and passes any test
    that only looks at the status.
    """

    def test_read_all_is_code_one(self, client: TestClient) -> None:
        response = client.get(f"/api/v1/projects/{BOB_OWN}/views")
        assert response.status_code == 403
        assert response.json() == {"code": 1, "message": "You're not allowed to do this."}

    def test_read_one_is_code_zero_with_the_read_wording(self, client: TestClient) -> None:
        response = client.get(f"/api/v1/projects/{BOB_OWN}/views/1")
        assert response.status_code == 403
        assert response.json() == {
            "code": 0,
            "message": "You don't have the permission to see this",
        }


# --- the order of the checks -------------------------------------------------


class TestTheOrderOfTheChecks:
    """Which code a request carrying two problems gets. Every cell measured.

    These are the assertions that separate a correct implementation from one that does all
    the same checks in a different order — and that difference is invisible to any test
    whose requests only ever have one thing wrong with them.
    """

    def test_a_bad_path_segment_beats_a_bad_body(self, client: TestClient) -> None:
        """400/2004, not the 412 the body alone would earn.

        This is why the ids are parsed in a dependency. Parsed inside the handler they run
        *after* the body, and this answers 412.
        """
        response = client.post(f"/api/v1/projects/{PROJ}/views/abc", json={})
        assert response.status_code == 400
        assert response.json() == {"code": 2004, "message": "Invalid model provided: Bad Request"}

    def test_a_bad_body_beats_a_missing_project(self, client: TestClient) -> None:
        response = client.put("/api/v1/projects/999999/views", json={})
        assert response.status_code == 412
        assert response.json()["invalid_fields"] == ["title: non zero value required"]

    def test_a_bad_body_beats_a_forbidden_project(self, client: TestClient) -> None:
        response = client.put(f"/api/v1/projects/{BOB_OWN}/views", json={})
        assert response.status_code == 412

    def test_a_bad_body_beats_a_missing_view(self, client: TestClient) -> None:
        response = client.post(f"/api/v1/projects/{PROJ}/views/999999", json={})
        assert response.status_code == 412

    def test_the_read_routes_report_a_missing_project_as_3001(self, client: TestClient) -> None:
        for path in ("/api/v1/projects/999999/views", "/api/v1/projects/999999/views/1"):
            response = client.get(path)
            assert response.status_code == 404
            assert response.json() == {"code": 3001, "message": "This project does not exist."}

    def test_the_write_item_routes_report_a_missing_project_as_3014(
        self, client: TestClient
    ) -> None:
        """☠ Update and delete never look the project up at all.

        They resolve the (view, project) pair and report 3014 when there is none — so the
        *same* missing project id answers 3001 on GET and 3014 on POST. Routing both
        through one "load the project first" helper is the obvious tidy-up and changes
        this code.
        """
        expected = {"code": 3014, "message": "This project view does not exist."}

        update = client.post("/api/v1/projects/999999/views/1", json={"title": "x"})
        assert update.status_code == 404
        assert update.json() == expected

        delete = client.delete("/api/v1/projects/999999/views/1")
        assert delete.status_code == 404
        assert delete.json() == expected

    def test_create_reports_a_missing_project_as_3001(self, client: TestClient) -> None:
        response = client.put("/api/v1/projects/999999/views", json={"title": "x"})
        assert response.status_code == 404
        assert response.json()["code"] == 3001

    def test_a_missing_view_beats_no_permission(self, app: FastAPI) -> None:
        """☠ A caller with no access can tell an existing view from a missing one.

        dave gets 403 for a view that exists and 404/3014 for one that does not, so the
        route leaks existence. Upstream's ``CanUpdate`` resolves the view before asking
        about permission; reversing them — which reads like closing a hole — turns every
        404 on this route into a 403.
        """
        alice, dave = as_user(app, ALICE), as_user(app, DAVE)
        existing = alice.put(f"/api/v1/projects/{C2}/views", json={"title": "x"}).json()["id"]

        present = dave.post(f"/api/v1/projects/{C2}/views/{existing}", json={"title": "y"})
        assert present.status_code == 403

        absent = dave.post(f"/api/v1/projects/{C2}/views/999999", json={"title": "y"})
        assert absent.status_code == 404
        assert absent.json()["code"] == 3014

    def test_a_view_of_another_project_is_404_not_403(self, client: TestClient) -> None:
        """The path project scopes the lookup, so a real view under the wrong parent is 3014."""
        response = client.get(f"/api/v1/projects/{C2}/views/{LIST_VIEW}")
        assert response.status_code == 404
        assert response.json()["code"] == 3014


class TestTheBodyOverridesThePath:
    """Echo binds the path onto the struct and then unmarshals the body over the top.

    ⚠️ Every assertion here is upstream behaviour that a safer implementation would not
    have. Making the path authoritative is the natural design and diverges on all four.
    """

    def test_update_follows_the_bodys_id(self, client: TestClient) -> None:
        a = client.put(f"/api/v1/projects/{PROJ}/views", json={"title": "A"}).json()["id"]
        b = client.put(f"/api/v1/projects/{PROJ}/views", json={"title": "B"}).json()["id"]

        client.post(f"/api/v1/projects/{PROJ}/views/{a}", json={"title": "viaBody", "id": b})

        assert client.get(f"/api/v1/projects/{PROJ}/views/{a}").json()["title"] == "A"
        assert client.get(f"/api/v1/projects/{PROJ}/views/{b}").json()["title"] == "viaBody"

    def test_create_follows_the_bodys_project_id_for_the_permission_check(
        self, client: TestClient
    ) -> None:
        """The caller owns the path project and not the body one, and is refused."""
        response = client.put(
            f"/api/v1/projects/{PROJ}/views", json={"title": "x", "project_id": BOB_OWN}
        )
        assert response.status_code == 403

    def test_create_puts_the_view_in_the_bodys_project(self, client: TestClient) -> None:
        response = client.put(
            f"/api/v1/projects/{BOB_OWN}/views", json={"title": "x", "project_id": PROJ}
        )
        assert response.status_code == 201
        assert response.json()["project_id"] == PROJ

    def test_delete_follows_the_bodys_id_too(self, client: TestClient) -> None:
        """Echo binds a body whenever one is present, DELETE included."""
        a = client.put(f"/api/v1/projects/{PROJ}/views", json={"title": "A"}).json()["id"]
        b = client.put(f"/api/v1/projects/{PROJ}/views", json={"title": "B"}).json()["id"]

        deleted = client.request("DELETE", f"/api/v1/projects/{PROJ}/views/{a}", json={"id": b})
        assert deleted.status_code == 200

        assert client.get(f"/api/v1/projects/{PROJ}/views/{a}").status_code == 200
        assert client.get(f"/api/v1/projects/{PROJ}/views/{b}").status_code == 404

    def test_an_explicit_zero_counts_as_sent(self, client: TestClient) -> None:
        """``{"project_id": 0}`` addresses project 0, which does not exist.

        Falling back on falsiness would silently use the path here and answer 200.
        """
        response = client.post(
            f"/api/v1/projects/{PROJ}/views/{LIST_VIEW}", json={"title": "x", "project_id": 0}
        )
        assert response.status_code == 404
        assert response.json()["code"] == 3014

    def test_create_ignores_the_bodys_id(self, client: TestClient) -> None:
        """``createProjectView`` zeroes it before the insert, so a forged id is not honoured."""
        response = client.put(f"/api/v1/projects/{PROJ}/views", json={"title": "x", "id": 12345})
        assert response.status_code == 201
        assert response.json()["id"] != 12345


# --- create ------------------------------------------------------------------


class TestCreate:
    def test_the_created_shape(self, client: TestClient) -> None:
        response = client.put(
            f"/api/v1/projects/{PROJ}/views", json={"title": "NewView", "view_kind": "list"}
        )
        assert response.status_code == 201
        body = response.json()
        assert body["title"] == "NewView"
        assert body["project_id"] == PROJ
        assert body["view_kind"] == "list"
        # A view created through the API carries no filter, unlike the List view a new
        # project comes with.
        assert body["filter"] is None
        assert body["bucket_configuration"] is None

    def test_an_omitted_kind_defaults_to_list(self, client: TestClient) -> None:
        response = client.put(f"/api/v1/projects/{PROJ}/views", json={"title": "NoKind"})
        assert response.status_code == 201
        assert response.json()["view_kind"] == "list"

    def test_the_two_validation_exits_are_different(self, client: TestClient) -> None:
        """☠ A missing field is 412 and an unrecognised enum is 400, on the same route.

        pydantic produces one ValidationError for both, so the natural implementation
        answers the same code twice. Whichever way they are unified, one of these fails.
        """
        empty_title = client.put(f"/api/v1/projects/{PROJ}/views", json={"title": ""})
        assert empty_title.status_code == 412
        assert empty_title.json() == {
            "code": 2002,
            "message": "Invalid Data",
            "invalid_fields": ["title: non zero value required"],
        }

        bad_kind = client.put(
            f"/api/v1/projects/{PROJ}/views", json={"title": "X", "view_kind": "nosuch"}
        )
        assert bad_kind.status_code == 400
        assert bad_kind.json() == {"code": 2004, "message": "Invalid model provided: Bad Request"}

    def test_an_integer_kind_is_a_bind_failure(self, client: TestClient) -> None:
        """The wire type is the name. The index is what the column holds, never the body."""
        response = client.put(f"/api/v1/projects/{PROJ}/views", json={"title": "X", "view_kind": 0})
        assert response.status_code == 400

    def test_a_bare_string_filter_is_a_bind_failure(self, client: TestClient) -> None:
        response = client.put(
            f"/api/v1/projects/{PROJ}/views", json={"title": "X", "filter": "done = false"}
        )
        assert response.status_code == 400

    def test_a_position_the_client_sent_is_kept(self, client: TestClient) -> None:
        response = client.put(
            f"/api/v1/projects/{PROJ}/views", json={"title": "P", "position": 12345}
        )
        assert response.json()["position"] == 12345

    def test_an_omitted_position_is_derived_from_the_id(self, client: TestClient) -> None:
        body = client.put(f"/api/v1/projects/{PROJ}/views", json={"title": "P"}).json()
        assert body["position"] == body["id"] * 65536

    def test_unknown_keys_are_ignored(self, client: TestClient) -> None:
        """Read-modify-write clients send back everything they read, ``owner`` included."""
        response = client.put(
            f"/api/v1/projects/{PROJ}/views",
            json={
                "title": "X",
                "owner": {"id": 1},
                "created": "2020-01-01T00:00:00Z",
                "max_right": 2,
                "totally_unknown": [1, 2, 3],
            },
        )
        assert response.status_code == 201

    def test_a_read_body_replayed_verbatim_is_accepted(self, client: TestClient) -> None:
        """The RMW echo: everything a GET emits must survive being sent straight back."""
        whole = client.get(f"/api/v1/projects/{PROJ}/views/{LIST_VIEW}").json()
        whole["title"] = "Renamed"
        response = client.post(f"/api/v1/projects/{PROJ}/views/{LIST_VIEW}", json=whole)
        assert response.status_code == 200, response.text
        assert response.json()["filter"]["filter"] == "done = false"


class TestCreateSideEffects:
    def test_a_manual_kanban_gets_three_buckets_and_the_two_pointers(
        self, client: TestClient, session: Session
    ) -> None:
        body = client.put(
            f"/api/v1/projects/{PROJ}/views",
            json={"title": "K", "view_kind": "kanban", "bucket_configuration_mode": "manual"},
        ).json()

        buckets = list(
            session.scalars(
                select(Bucket)
                .where(Bucket.project_view_id == body["id"])
                .order_by(Bucket.position.asc())
            )
        )
        assert [b.title for b in buckets] == ["To-Do", "Doing", "Done"]
        assert body["default_bucket_id"] == buckets[0].id
        assert body["done_bucket_id"] == buckets[-1].id

    def test_a_kanban_with_mode_none_gets_no_buckets(
        self, client: TestClient, session: Session
    ) -> None:
        """☠ The kind alone does not decide. Both conditions are required.

        A view created as ``kanban`` with the default mode ``none`` gets nothing — which is
        the same pairing ``_view_shape.yaml`` turns on when it branches on the *mode*.
        """
        body = client.put(
            f"/api/v1/projects/{PROJ}/views", json={"title": "KN", "view_kind": "kanban"}
        ).json()
        assert (
            session.scalars(select(Bucket).where(Bucket.project_view_id == body["id"])).all() == []
        )

    def test_every_create_gives_the_projects_tasks_a_position_in_the_new_view(
        self, client: TestClient, session: Session
    ) -> None:
        """``createProjectView`` ends in ``RecalculateTaskPositions``, whatever the kind.

        Measured on a plain List view as well as a Kanban one: seven tasks, seven rows.
        Without it the view exists and every task in it has no position.
        """
        body = client.put(f"/api/v1/projects/{PROJ}/views", json={"title": "L"}).json()
        positions = session.scalars(
            select(TaskPosition).where(TaskPosition.project_view_id == body["id"])
        ).all()
        assert len(positions) == 7

    def test_a_manual_kanban_also_drops_the_tasks_into_its_backlog(
        self, client: TestClient, session: Session
    ) -> None:
        body = client.put(
            f"/api/v1/projects/{PROJ}/views",
            json={"title": "K", "view_kind": "kanban", "bucket_configuration_mode": "manual"},
        ).json()
        links = session.scalars(
            select(TaskBucket).where(TaskBucket.project_view_id == body["id"])
        ).all()
        assert len(links) == 7
        assert {link.bucket_id for link in links} == {body["default_bucket_id"]}


class TestFilterValidation:
    """Upstream parses the expression before touching the database, on create and update."""

    def test_an_unknown_field_is_4016(self, client: TestClient) -> None:
        response = client.put(
            f"/api/v1/projects/{PROJ}/views",
            json={"title": "F", "filter": {"filter": "nosuchfield = 1"}},
        )
        assert response.status_code == 400
        assert response.json()["code"] == 4016

    def test_a_malformed_expression_is_4024(self, client: TestClient) -> None:
        response = client.put(
            f"/api/v1/projects/{PROJ}/views", json={"title": "F", "filter": {"filter": "((("}}
        )
        assert response.status_code == 400
        assert response.json()["code"] == 4024

    def test_an_empty_expression_is_accepted(self, client: TestClient) -> None:
        response = client.put(
            f"/api/v1/projects/{PROJ}/views", json={"title": "F", "filter": {"filter": ""}}
        )
        assert response.status_code == 201

    def test_update_validates_too(self, client: TestClient) -> None:
        response = client.post(
            f"/api/v1/projects/{PROJ}/views/{GANTT_VIEW}",
            json={"title": "F", "filter": {"filter": "nosuchfield = 1"}},
        )
        assert response.status_code == 400
        assert response.json()["code"] == 4016

    def test_a_rejected_filter_writes_nothing(self, client: TestClient, session: Session) -> None:
        """The parse runs before the write, so the view keeps its old title."""
        client.post(
            f"/api/v1/projects/{PROJ}/views/{GANTT_VIEW}",
            json={"title": "Renamed", "filter": {"filter": "nosuchfield = 1"}},
        )
        session.expire_all()
        assert stored_view(session, GANTT_VIEW).title == "Gantt"


class TestTheFilterNullability:
    """NULL and the empty document are different values. Measured on all four inputs."""

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            ({"title": "a"}, None),
            ({"title": "b", "filter": None}, None),
            ({"title": "c", "filter": {}}, "object"),
            ({"title": "d", "filter": {"filter": ""}}, "object"),
        ],
    )
    def test_only_an_absent_filter_is_null(
        self, client: TestClient, body: dict[str, object], expected: str | None
    ) -> None:
        response = client.put(f"/api/v1/projects/{PROJ}/views", json=body).json()
        if expected is None:
            assert response["filter"] is None
        else:
            assert response["filter"] == {
                "s": "",
                "sort_by": None,
                "order_by": None,
                "filter": "",
                "filter_include_nulls": False,
            }

    def test_the_search_term_round_trips(self, client: TestClient) -> None:
        """``s`` is persisted, so this is a real nested object rather than one string."""
        created = client.put(
            f"/api/v1/projects/{PROJ}/views", json={"title": "S", "filter": {"s": "abc"}}
        ).json()
        reread = client.get(f"/api/v1/projects/{PROJ}/views/{created['id']}").json()
        assert reread["filter"]["s"] == "abc"


class TestBucketConfiguration:
    def test_it_round_trips(self, client: TestClient) -> None:
        created = client.put(
            f"/api/v1/projects/{PROJ}/views",
            json={
                "title": "BC",
                "view_kind": "kanban",
                "bucket_configuration_mode": "filter",
                "bucket_configuration": [
                    {"title": "open", "filter": {"filter": "done = false"}},
                    {"title": "shut", "filter": {"filter": "done = true"}},
                ],
            },
        )
        assert created.status_code == 201, created.text
        reread = client.get(f"/api/v1/projects/{PROJ}/views/{created.json()['id']}").json()
        assert [entry["title"] for entry in reread["bucket_configuration"]] == ["open", "shut"]
        assert reread["bucket_configuration"][0]["filter"]["filter"] == "done = false"

    def test_an_empty_array_is_not_null(self, client: TestClient) -> None:
        """``[]`` and ``null`` are distinguishable on the wire, and both are reachable."""
        created = client.put(
            f"/api/v1/projects/{PROJ}/views", json={"title": "BC", "bucket_configuration": []}
        ).json()
        assert created["bucket_configuration"] == []

    def test_a_row_holding_sql_null_reads_the_same_as_the_json_literal(
        self, client: TestClient, session: Session
    ) -> None:
        """Upstream writes the literal ``null``; a seeded row has SQL NULL. Both are absent.

        Reading only one of the two makes a view's shape depend on how it was written.
        """
        seeded = client.get(f"/api/v1/projects/{PROJ}/views/{GANTT_VIEW}").json()
        assert stored_view(session, GANTT_VIEW).bucket_configuration is None
        written = client.put(f"/api/v1/projects/{PROJ}/views", json={"title": "W"}).json()
        session.expire_all()
        assert stored_view(session, written["id"]).bucket_configuration == "null"
        assert seeded["bucket_configuration"] == written["bucket_configuration"] is None


# --- update ------------------------------------------------------------------


class TestUpdateIsAWholeModelReplace:
    def test_every_omitted_field_is_reset(self, client: TestClient, session: Session) -> None:
        """☠ Renaming a Kanban view turns it into a List view with no buckets configured.

        All eight columns in ``Update()``'s ``Cols(...)`` are written unconditionally.
        There is no nil guard anywhere on this model — unlike ``Project.ParentProjectID``.
        """
        response = client.post(
            f"/api/v1/projects/{PROJ}/views/{KANBAN_VIEW}", json={"title": "Renamed"}
        )
        assert response.status_code == 200

        session.expire_all()
        stored = stored_view(session, KANBAN_VIEW)
        assert stored.title == "Renamed"
        assert stored.view_kind == 0
        assert stored.bucket_configuration_mode == 0
        assert stored.position == 0
        assert stored.default_bucket_id == 0
        assert stored.done_bucket_id == 0

    def test_the_buckets_themselves_survive_the_pointers_being_cleared(
        self, client: TestClient, session: Session
    ) -> None:
        """The view forgets which bucket is default and which is done; the buckets remain."""
        client.post(f"/api/v1/projects/{PROJ}/views/{KANBAN_VIEW}", json={"title": "Renamed"})
        session.expire_all()
        assert (
            len(session.scalars(select(Bucket).where(Bucket.project_view_id == KANBAN_VIEW)).all())
            == 3
        )

    def test_an_omitted_filter_is_cleared_to_null(
        self, client: TestClient, session: Session
    ) -> None:
        """Not an oversight to guard against — it is the specified behaviour."""
        client.post(f"/api/v1/projects/{PROJ}/views/{LIST_VIEW}", json={"title": "List"})
        session.expire_all()
        assert stored_filter(session, LIST_VIEW) is None

    def test_an_explicit_filter_is_written(self, client: TestClient) -> None:
        client.post(
            f"/api/v1/projects/{PROJ}/views/{GANTT_VIEW}",
            json={"title": "G", "filter": {"filter": "done = true"}},
        )
        body = client.get(f"/api/v1/projects/{PROJ}/views/{GANTT_VIEW}").json()
        assert body["filter"]["filter"] == "done = true"

    def test_the_position_is_really_zeroed_not_just_dropped(
        self, client: TestClient, session: Session
    ) -> None:
        """☠ Changing a view's title moves it to the front of the list.

        Users see "the view order changed" with no connection to the rename. The re-read is
        the whole point of this case: the response alone cannot tell "reset" from
        "not echoed", and ``created`` on the very same response is the other one.
        """
        response = client.post(
            f"/api/v1/projects/{PROJ}/views/{TABLE_VIEW}", json={"title": "Renamed"}
        )
        assert response.json()["position"] == 0
        session.expire_all()
        assert stored_view(session, TABLE_VIEW).position == 0

    def test_created_is_dropped_from_the_response_but_kept_in_the_row(
        self, client: TestClient, session: Session
    ) -> None:
        """☠ The other half of the pair above, and it goes the other way.

        The response carries the zero time while the row keeps 2026-01-01. Serialising the
        stored row — the natural implementation — differs on every single update.
        """
        response = client.post(
            f"/api/v1/projects/{PROJ}/views/{TABLE_VIEW}", json={"title": "Renamed"}
        )
        assert response.json()["created"] == "0001-01-01T00:00:00Z"
        session.expire_all()
        assert stored_view(session, TABLE_VIEW).created == EPOCH
        assert client.get(f"/api/v1/projects/{PROJ}/views/{TABLE_VIEW}").json()["created"] == (
            "2026-01-01T00:00:00Z"
        )

    def test_update_does_not_create_buckets(self, client: TestClient, session: Session) -> None:
        """Only the create path makes them, so a view switched to Kanban has none."""
        made = client.put(f"/api/v1/projects/{PROJ}/views", json={"title": "L"}).json()["id"]
        client.post(
            f"/api/v1/projects/{PROJ}/views/{made}",
            json={"title": "K", "view_kind": "kanban", "bucket_configuration_mode": "manual"},
        )
        session.expire_all()
        assert session.scalars(select(Bucket).where(Bucket.project_view_id == made)).all() == []

    def test_the_same_two_validation_exits_apply(self, client: TestClient) -> None:
        empty = client.post(f"/api/v1/projects/{PROJ}/views/{GANTT_VIEW}", json={"title": ""})
        assert empty.status_code == 412
        bad = client.post(
            f"/api/v1/projects/{PROJ}/views/{GANTT_VIEW}",
            json={"title": "X", "view_kind": "nosuch"},
        )
        assert bad.status_code == 400


# --- delete ------------------------------------------------------------------


class TestDelete:
    def test_the_body_is_a_message(self, client: TestClient) -> None:
        response = client.delete(f"/api/v1/projects/{PROJ}/views/{GANTT_VIEW}")
        assert response.status_code == 200
        assert response.json() == {"message": "Successfully deleted."}

    def test_the_view_is_gone_from_the_collection(self, client: TestClient) -> None:
        client.delete(f"/api/v1/projects/{PROJ}/views/{GANTT_VIEW}")
        remaining = client.get(f"/api/v1/projects/{PROJ}/views").json()
        assert [v["id"] for v in remaining] == [LIST_VIEW, TABLE_VIEW, KANBAN_VIEW]

    def test_deleting_a_missing_view_is_404_not_an_idempotent_200(self, client: TestClient) -> None:
        response = client.delete(f"/api/v1/projects/{PROJ}/views/999999")
        assert response.status_code == 404
        assert response.json()["code"] == 3014

    def test_every_view_of_a_project_may_be_deleted(self, client: TestClient) -> None:
        """☠ Views have no "you cannot remove the last one" guard, and buckets do.

        The two resources look alike — both hang off a project, both have positions, both
        can be added and removed — and the rule is opposite. Adding the guard here is a
        reasonable defence and breaks this.
        """
        for view in (LIST_VIEW, GANTT_VIEW, TABLE_VIEW, KANBAN_VIEW):
            assert client.delete(f"/api/v1/projects/{PROJ}/views/{view}").status_code == 200

        assert client.get(f"/api/v1/projects/{PROJ}/views").json() == []
        # ...and the tasks are still readable, so this is a reachable state, not a broken one.
        assert client.get(f"/api/v1/projects/{PROJ}/tasks").status_code == 200

    def test_the_task_links_go_and_the_buckets_stay(
        self, client: TestClient, session: Session
    ) -> None:
        """☠ Measured, and the opposite of what the resource model suggests.

        ``Delete`` touches ``project_views``, ``task_buckets`` and ``task_positions``. The
        bucket rows are left orphaned. Cleaning them up is tidier and diverges: bucket ids
        are AUTOINCREMENT, so which id the next bucket gets is observable.
        """
        assert (
            len(
                session.scalars(
                    select(TaskBucket).where(TaskBucket.project_view_id == KANBAN_VIEW)
                ).all()
            )
            == 7
        )

        client.delete(f"/api/v1/projects/{PROJ}/views/{KANBAN_VIEW}")
        session.expire_all()

        assert (
            session.scalars(
                select(TaskBucket).where(TaskBucket.project_view_id == KANBAN_VIEW)
            ).all()
            == []
        )
        assert (
            len(session.scalars(select(Bucket).where(Bucket.project_view_id == KANBAN_VIEW)).all())
            == 3
        )


# --- the project response's nested views -------------------------------------


class TestTheViewsNestedInAProject:
    """T16 renders these through the same serializer, so the filter bug lived there too."""

    def test_a_projects_nested_view_parses_its_filter(self, client: TestClient) -> None:
        project = client.get(f"/api/v1/projects/{PROJ}").json()
        nested = next(v for v in project["views"] if v["id"] == LIST_VIEW)
        assert nested["filter"]["filter"] == "done = false"

    def test_the_nested_shape_matches_the_view_endpoint(self, client: TestClient) -> None:
        project = client.get(f"/api/v1/projects/{PROJ}").json()
        nested = next(v for v in project["views"] if v["id"] == LIST_VIEW)
        assert nested == client.get(f"/api/v1/projects/{PROJ}/views/{LIST_VIEW}").json()


def test_the_stored_filter_helpers_are_inverse() -> None:
    """``view_filter_of`` and ``stored_filter_of`` must round-trip, or a write changes a read."""
    from calton.schemas.project_view import stored_filter_of, view_filter_of

    parsed = view_filter_of(DONE_FALSE_FILTER)
    assert parsed is not None
    round_tripped = stored_filter_of(parsed)
    assert round_tripped is not None
    assert json.loads(round_tripped) == json.loads(DONE_FALSE_FILTER)


# --- pseudo projects ---------------------------------------------------------


class TestFavoritesViews:
    """The Favorites pseudo project (-1) has views, and they are not rows.

    ⚠️ **THREE views, no Kanban.** "Every project gets four default views" is not a
    universal rule and this is the counter-example — so "how many views does this have"
    has to be measured per kind of project, never generalised from the one at hand.

    They are a compiled-in struct upstream, which is why their ids are negative and their
    timestamps are the zero time.
    """

    def test_the_collection_lists_nothing(self, client: TestClient) -> None:
        """☠ The collection and the item disagree, and both are measured.

        ``getViewsForProject`` only ever queries by ``project_id`` and Favorites owns no
        rows, so the list is empty — while the item route below reads the struct and
        answers 200. Serving the three views here instead is the tidy, consistent fix and
        changes what every favourites client reads.
        """
        response = client.get(f"/api/v1/projects/{FAVORITES}/views")
        assert response.status_code == 200
        assert response.json() == []

    def test_the_item_route_serves_three_constant_views(self, client: TestClient) -> None:
        titles = []
        for view_id in (-1, -2, -3):
            response = client.get(f"/api/v1/projects/{FAVORITES}/views/{view_id}")
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["id"] == view_id
            assert body["project_id"] == FAVORITES
            # Never rows, so never a real creation time.
            assert body["created"] == "0001-01-01T00:00:00Z"
            assert body["updated"] == "0001-01-01T00:00:00Z"
            titles.append(body["title"])
        assert titles == ["List", "Gantt", "Table"]

    def test_there_is_no_fourth_view(self, client: TestClient) -> None:
        """The Kanban a real project would have does not exist here."""
        response = client.get(f"/api/v1/projects/{FAVORITES}/views/-4")
        assert response.status_code == 404
        assert response.json()["code"] == 3014

    def test_the_list_view_carries_the_default_filter(self, client: TestClient) -> None:
        body = client.get(f"/api/v1/projects/{FAVORITES}/views/-1").json()
        assert body["filter"]["filter"] == "done = false"
        assert client.get(f"/api/v1/projects/{FAVORITES}/views/-2").json()["filter"] is None

    def test_every_authenticated_caller_sees_the_same_views(self, app: FastAPI) -> None:
        """Constants, not per-user data — so there is nothing to scope by owner."""
        alice = as_user(app, ALICE).get(f"/api/v1/projects/{FAVORITES}/views/-1")
        dave = as_user(app, DAVE).get(f"/api/v1/projects/{FAVORITES}/views/-1")
        assert alice.status_code == dave.status_code == 200
        assert alice.json() == dave.json()

    def test_the_permission_header_is_read_not_admin(self, app: FastAPI) -> None:
        """☠ ``x-max-permission: 0``.

        "It is my favourites, so I own it" is the natural reading and gives 2. Measured 0
        for two different users.
        """
        for user in (ALICE, DAVE):
            response = as_user(app, user).get(f"/api/v1/projects/{FAVORITES}/views/-1")
            assert response.headers["x-max-permission"] == "0"

    @pytest.mark.parametrize("verb", ["create", "update", "delete"])
    def test_every_write_is_refused(self, client: TestClient, verb: str) -> None:
        """There is nothing to write to — the views are compiled in."""
        response = {
            "create": lambda: client.put(
                f"/api/v1/projects/{FAVORITES}/views", json={"title": "x"}
            ),
            "update": lambda: client.post(
                f"/api/v1/projects/{FAVORITES}/views/-1", json={"title": "x"}
            ),
            "delete": lambda: client.delete(f"/api/v1/projects/{FAVORITES}/views/-1"),
        }[verb]()
        assert response.status_code == 403
        assert response.json() == {"code": 0, "message": "Forbidden"}


class TestSavedFilterViews:
    """A saved filter addressed as a project. Its views are ordinary rows.

    ⚠️ **How many views it has depends on how it was created, not on what it is.** A
    filter created through ``PUT /filters`` gets the four default views; the one seeded
    here was written straight into the table and has none — which is exactly the shape
    that makes a wrong implementation look correct against a DB-loaded fixture.
    """

    def test_a_table_loaded_filter_has_no_views(self, client: TestClient) -> None:
        response = client.get(f"/api/v1/projects/{ALICE_FILTER_PROJECT}/views")
        assert response.status_code == 200
        assert response.json() == []

    def test_a_pseudo_id_naming_no_filter_is_11001_not_3001(self, client: TestClient) -> None:
        """☠ A third distinct 404 on the same route.

        An ordinary missing project is 3001, a missing view is 3014, and a pseudo id with
        no filter behind it is **11001**. Resolving the id with a plain
        ``session.get(Project, ...)`` reports 3001 for all pseudo ids — right status,
        wrong code, and it tells the caller the wrong thing is missing.
        """
        response = client.get("/api/v1/projects/-9999/views")
        assert response.status_code == 404
        assert response.json() == {"code": 11001, "message": "This saved filter does not exist."}

    def test_a_non_owner_is_refused_with_the_read_all_code(self, app: FastAPI) -> None:
        response = as_user(app, DAVE).get(f"/api/v1/projects/{ALICE_FILTER_PROJECT}/views")
        assert response.status_code == 403
        assert response.json() == {"code": 1, "message": "You're not allowed to do this."}

    def test_a_non_owner_is_refused_with_the_read_one_code(self, app: FastAPI) -> None:
        """The same list/item code split as ordinary projects, on the pseudo path too."""
        response = as_user(app, DAVE).get(f"/api/v1/projects/{ALICE_FILTER_PROJECT}/views/1")
        assert response.status_code == 403
        assert response.json() == {
            "code": 0,
            "message": "You don't have the permission to see this",
        }

    def test_the_owner_may_create_a_view_on_it(self, client: TestClient) -> None:
        """Unlike Favorites: these are rows, so they can be written."""
        response = client.put(
            f"/api/v1/projects/{ALICE_FILTER_PROJECT}/views", json={"title": "mine"}
        )
        assert response.status_code == 201, response.text
        assert response.json()["project_id"] == ALICE_FILTER_PROJECT

        listed = client.get(f"/api/v1/projects/{ALICE_FILTER_PROJECT}/views").json()
        assert [v["title"] for v in listed] == ["mine"]

    def test_a_non_owner_may_not_create_a_view_on_it(self, app: FastAPI) -> None:
        response = as_user(app, DAVE).put(
            f"/api/v1/projects/{ALICE_FILTER_PROJECT}/views", json={"title": "x"}
        )
        assert response.status_code == 403
        assert response.json() == {"code": 0, "message": "Forbidden"}


def test_default_views_can_be_created_for_a_pseudo_project(session: Session) -> None:
    """The T29 reuse point: creating a saved filter has to make its four default views.

    ``create_default_views`` takes a bare project id and never asks whether a row backs
    it, so T29 calls it with ``-N-1`` rather than growing a second copy of the rule. This
    test exists so that stays true — a future "look the project up first" would break the
    saved-filter path with nothing else to catch it.
    """
    from calton.services.project_service import create_default_views

    made = create_default_views(session, ALICE_FILTER_PROJECT, ALICE)
    session.flush()

    assert [(v.title, v.view_kind) for v in made] == [
        ("List", 0),
        ("Gantt", 1),
        ("Table", 2),
        ("Kanban", 3),
    ]
    assert all(v.project_id == ALICE_FILTER_PROJECT for v in made)
    # The Kanban view gets its three buckets here too, exactly as on a real project.
    kanban = made[-1]
    buckets = session.scalars(select(Bucket).where(Bucket.project_view_id == kanban.id)).all()
    assert [b.title for b in buckets] == ["To-Do", "Doing", "Done"]
    assert kanban.default_bucket_id == buckets[0].id
    assert kanban.done_bucket_id == buckets[-1].id


class TestTheDefaultListFilterDependsOnWhatIsBeingCreated:
    """``createDefaultListFilter`` — a project gets ``done = false``, a saved filter does not.

    ☠ **Only the List view can tell the two implementations apart.** Gantt, Table and
    Kanban carry no filter either way, so a case written against any of them passes against
    both the right and the wrong implementation. That is why every assertion here reads the
    List view specifically.

    The consequence of getting it wrong is not cosmetic: a saved filter *is* a filter, so
    layering ``done = false`` on top of it hides the user's done tasks from the only view
    that shows them — and the filter they wrote is the one place they would look for that
    behaviour.
    """

    def test_a_project_gets_the_default_list_filter(self, session: Session) -> None:
        from calton.services.project_service import create_default_views

        views = {v.title: v for v in create_default_views(session, PROJ, ALICE)}
        session.flush()

        assert views["List"].filter is not None
        assert json.loads(views["List"].filter)["filter"] == "done = false"

    def test_a_saved_filter_does_not(self, session: Session) -> None:
        from calton.services.project_service import create_default_views

        views = {
            v.title: v
            for v in create_default_views(
                session, ALICE_FILTER_PROJECT, ALICE, with_list_filter=False
            )
        }
        session.flush()

        assert views["List"].filter is None

    def test_the_other_three_are_null_either_way(self, session: Session) -> None:
        """The control that makes the pair above meaningful.

        Without it, "the saved filter's views have no filter" is indistinguishable from
        "no view ever gets a filter" — the assertion would hold against an implementation
        that dropped the feature entirely.
        """
        from calton.services.project_service import create_default_views

        with_filter = {v.title: v for v in create_default_views(session, PROJ, ALICE)}
        without = {
            v.title: v
            for v in create_default_views(
                session, ALICE_FILTER_PROJECT, ALICE, with_list_filter=False
            )
        }
        session.flush()

        for title in ("Gantt", "Table", "Kanban"):
            assert with_filter[title].filter is None
            assert without[title].filter is None

    def test_the_saved_filter_endpoint_creates_views_without_it(
        self, client: TestClient, session: Session
    ) -> None:
        """The wiring, not just the helper: T29's create path has to pass the flag.

        A correct helper called with the default argument produces exactly the bug this
        class exists to prevent, and no test of the helper alone would notice.
        """
        made = client.put(
            "/api/v1/filters", json={"title": "wired", "filters": {"filter": "done = false"}}
        )
        assert made.status_code == 201, made.text

        pseudo = -made.json()["id"] - 1
        views = client.get(f"/api/v1/projects/{pseudo}/views").json()
        listed = next(v for v in views if v["view_kind"] == "list")
        assert listed["filter"] is None
