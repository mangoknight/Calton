"""T07 — CRUDRouter, Policy and the v1 verb inversion.

The two assertions that matter most here fail *silently* in production if the
implementation is wrong:

* verbs reversed -> 404, with nothing in any log saying why;
* partial update instead of full replace -> no error, just wrong data later.

Both get explicit tests below.
"""

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from calton.core.crud_router import (
    Action,
    CRUDRouter,
    action_for,
    deleted_response,
    read_one_response,
)
from calton.core.errors import INVALID_MODEL_MESSAGE, register_exception_handlers
from calton.core.policy import (
    FORBIDDEN_MESSAGE,
    FORBIDDEN_READ_MESSAGE,
    AllowAll,
)
from calton.db.session import get_db

ZERO_VALUES = {"title": "", "description": "", "priority": 0, "done": False, "hex_color": ""}


class WidgetWrite(BaseModel):
    # extra="ignore" because clients read the whole object and post it back;
    # strict=True because lax coercion would accept writes Go refuses (⑱).
    model_config = ConfigDict(extra="ignore", strict=True)

    title: str = ""
    description: str = ""
    priority: int = 0
    done: bool = False
    hex_color: str = ""


class WidgetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = 0
    title: str = ""
    description: str = ""
    priority: int = 0
    done: bool = False
    hex_color: str = ""
    owner: str = "someone"


class Widget:
    title: str
    description: str
    priority: int
    done: bool
    hex_color: str

    def __init__(self, id: int, **fields: Any) -> None:
        self.id = id
        for name, default in ZERO_VALUES.items():
            setattr(self, name, fields.get(name, default))
        self.owner = "someone"


class WidgetService:
    """An in-memory service. Update replaces, as the real ones must."""

    def __init__(self) -> None:
        self.widgets: dict[int, Widget] = {}
        self.calls: list[str] = []
        self.next_id = 1

    def create(self, session: Any, data: BaseModel, auth: Any, **kwargs: Any) -> Widget:
        self.calls.append("create")
        widget = Widget(self.next_id, **data.model_dump())
        self.widgets[widget.id] = widget
        self.next_id += 1
        return widget

    def read_one(self, session: Any, auth: Any, **kwargs: Any) -> Widget:
        self.calls.append("read_one")
        return self.widgets[int(kwargs["widget"])]

    def read_all(
        self, session: Any, auth: Any, search: str, page: int, per_page: int, **kwargs: Any
    ) -> tuple[list[Widget], int, int]:
        self.calls.append("read_all")
        items = [w for w in self.widgets.values() if search in w.title]
        window = items[(page - 1) * per_page : (page - 1) * per_page + per_page]
        return window, len(window), len(items)

    def update(self, session: Any, data: BaseModel, auth: Any, **kwargs: Any) -> Widget:
        self.calls.append("update")
        widget = Widget(int(kwargs["widget"]), **data.model_dump())
        self.widgets[widget.id] = widget
        return widget

    def delete(self, session: Any, auth: Any, **kwargs: Any) -> None:
        self.calls.append("delete")
        del self.widgets[int(kwargs["widget"])]


class DenyAll:
    def can_read(self, session: Any, auth: Any, **kwargs: Any) -> tuple[bool, int]:
        return False, -1

    def can_create(self, session: Any, auth: Any, **kwargs: Any) -> bool:
        return False

    def can_update(self, session: Any, auth: Any, **kwargs: Any) -> bool:
        return False

    def can_delete(self, session: Any, auth: Any, **kwargs: Any) -> bool:
        return False


class SessionStub:
    """Stands in for the request session, and counts commits.

    It used to be a bare ``None``, on the grounds that these cases exercise routing rather
    than persistence and the service doubles ignore the session. That stopped being true
    when the router took over committing: ``get_db`` closes its session **without**
    committing, so a service that only flushes loses its write after the response has
    already been built — 201 with the right body, and no row. A ``None`` here cannot tell
    the difference between "the router commits" and "the router does not", which is the
    third face of practice #20: a double that lacks the behaviour under test.
    """

    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


def build(
    policy: Any = None,
) -> tuple[TestClient, WidgetService, CRUDRouter[Widget, WidgetRead, WidgetWrite]]:
    app = FastAPI()
    register_exception_handlers(app)
    service = WidgetService()
    crud = CRUDRouter(
        prefix="/widgets",
        item_param="widget",
        service=service,
        policy=policy or AllowAll(),
        read_schema=WidgetRead,
        write_schema=WidgetWrite,
    )
    # The doubles above ignore the session's contents, so this is a stub rather than a
    # database — but it is a stub that records commits, because the router is responsible
    # for them. The override also proves the dependency is actually wired: without it
    # every route 500s looking for app.state.session_factory.
    session = SessionStub()
    app.state.session_stub = session
    app.dependency_overrides[get_db] = lambda: session
    app.include_router(crud.router)
    return TestClient(app, raise_server_exceptions=False), service, crud


CrudClient = tuple[TestClient, WidgetService, "CRUDRouter[Widget, WidgetRead, WidgetWrite]"]


@pytest.fixture
def crud_client() -> CrudClient:
    return build()


# --- the verb inversion ------------------------------------------------------


def test_put_on_the_collection_creates(crud_client: CrudClient) -> None:
    client, service, _ = crud_client
    resp = client.put("/widgets", json={"title": "first"})
    assert resp.status_code == 201
    assert resp.json()["title"] == "first"
    assert service.calls == ["create"]


def test_post_on_the_item_updates(crud_client: CrudClient) -> None:
    client, service, _ = crud_client
    client.put("/widgets", json={"title": "first"})
    resp = client.post("/widgets/1", json={"title": "changed"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "changed"
    assert service.calls == ["create", "update"]


def test_the_verbs_are_not_the_rest_convention(crud_client: CrudClient) -> None:
    """If someone "fixes" the router to POST-creates/PUT-updates, these 404."""
    client, _, _ = crud_client
    assert client.post("/widgets", json={"title": "x"}).status_code == 405
    assert client.put("/widgets/1", json={"title": "x"}).status_code == 405


@pytest.mark.parametrize(
    ("method", "has_param", "expected"),
    [
        ("PUT", False, Action.CREATE),
        ("POST", True, Action.UPDATE),
        ("PATCH", True, Action.UPDATE),
        ("DELETE", True, Action.DELETE),
        ("GET", True, Action.READ_ONE),
        ("GET", False, Action.READ_ALL),
    ],
)
def test_action_mapping(method: str, has_param: bool, expected: str) -> None:
    assert action_for(method, has_path_param=has_param) == expected


def test_v2_flips_put_and_post_back() -> None:
    """api_routes.go branches on v2; we only serve v1, but the flag documents why."""
    assert action_for("PUT", has_path_param=False, v2=True) == Action.UPDATE
    assert action_for("POST", has_path_param=True, v2=True) == Action.CREATE


def _mounted(crud: "CRUDRouter[Widget, WidgetRead, WidgetWrite]") -> set[tuple[str, str]]:
    """(method, path) actually on the router, read from the router itself."""
    # `routes` is typed as BaseRoute, which declares neither `path` nor
    # `methods`; every route an APIRouter actually holds is an APIRoute and has
    # both. Read defensively rather than casting, so a future route type that
    # genuinely lacks them is skipped instead of crashing the test.
    mounted: set[tuple[str, str]] = set()
    for route in crud.router.routes:
        path = getattr(route, "path", None)
        if path is None:
            continue
        for method in getattr(route, "methods", set()):
            if method not in {"HEAD", "OPTIONS"}:
                mounted.add((method, path))
    return mounted


def test_registered_actions_lists_exactly_what_was_mounted(
    crud_client: CrudClient,
) -> None:
    """`registered_actions()` is a hand-written literal; `_register()` mounts the
    real routes. Nothing but this test makes the two agree.

    It matters because `route_registry.py:215` turns that list into the API token
    permission table. The two failure directions are not symmetric:

    * **Mounted but unlisted** is the dangerous one — a reachable endpoint with no
      entry in the permission table.
    * **Listed but unmounted** grants a permission for a route that does not
      exist, which is merely wrong.

    The previous version of this test asserted four tuples were *present in* the
    list and never looked at the router at all, so it could not catch either
    direction despite its name. Adding a verb to `_register()` and forgetting the
    literal would have gone through silently.
    """
    _, _, crud = crud_client

    listed = {(method, path) for method, path, _ in crud.registered_actions()}

    assert listed == _mounted(crud), (
        f"registered_actions() and the mounted routes disagree; "
        f"mounted but unlisted={sorted(_mounted(crud) - listed)} "
        f"(reachable with no API-token permission entry), "
        f"listed but unmounted={sorted(listed - _mounted(crud))}"
    )


def test_each_mounted_route_is_classified_by_the_same_rule_as_action_for(
    crud_client: CrudClient,
) -> None:
    """The action attached to each route must be the one `action_for` derives from
    the method and whether the path takes a parameter.

    `action_for` is what the v1 verb inversion lives in (PUT creates, POST
    updates). If `registered_actions()` ever disagrees with it — say PUT on the
    collection gets labelled UPDATE — the token check would enforce a permission
    the route does not actually perform, and every test above would still pass.
    """
    _, _, crud = crud_client

    for method, path, action in crud.registered_actions():
        expected = action_for(method, has_path_param="{" in path)
        assert action is expected, f"{method} {path} is {action}, but action_for says {expected}"


# --- full replacement --------------------------------------------------------


def test_update_resets_omitted_fields_to_their_zero_values(crud_client: CrudClient) -> None:
    """Five fields in, two supplied on update, the other three must be cleared.

    This is the rule for scalars. Certain pointer fields opt out — decided by the
    model's Update() Cols/nil guard, not by "is it a pointer" — and those need the
    four-cell matrix (omitted / null / explicit 0 / explicit value) instead. In
    Phase 1: Project.ParentProjectID in T16, SavedFilter.Filters in T29. See the
    module docstring of core/crud_router.py and design §2.3.1.
    """
    client, _, _ = crud_client
    client.put(
        "/widgets",
        json={
            "title": "full",
            "description": "d",
            "priority": 5,
            "done": True,
            "hex_color": "ff0000",
        },
    )

    body = client.post("/widgets/1", json={"title": "partial", "priority": 3}).json()
    assert body["title"] == "partial"
    assert body["priority"] == 3
    assert body["description"] == ""
    assert body["done"] is False
    assert body["hex_color"] == ""


def test_posting_back_a_whole_read_object_is_accepted(crud_client: CrudClient) -> None:
    """Read-modify-write, the way eargollo does it. Read-only and unknown fields
    must be ignored, never 422."""
    client, _, _ = crud_client
    client.put("/widgets", json={"title": "original"})
    read_back = client.get("/widgets/1").json()

    read_back["title"] = "edited"
    read_back["max_right"] = 2
    read_back["created"] = "2026-01-01T00:00:00Z"
    read_back["tasks"] = [{"id": 1}]

    resp = client.post("/widgets/1", json=read_back)
    assert resp.status_code == 200, resp.text
    assert resp.json()["title"] == "edited"


def test_read_only_fields_in_the_body_are_not_written(crud_client: CrudClient) -> None:
    client, _, _ = crud_client
    client.put("/widgets", json={"title": "x"})
    resp = client.post("/widgets/1", json={"title": "y", "owner": "attacker", "id": 999})
    assert resp.status_code == 200
    assert resp.json()["owner"] == "someone"
    assert resp.json()["id"] == 1


# --- statuses, bodies and headers -------------------------------------------


def test_create_returns_201_and_the_object(crud_client: CrudClient) -> None:
    client, _, _ = crud_client
    resp = client.put("/widgets", json={"title": "x"})
    assert resp.status_code == 201
    assert resp.json()["id"] == 1


def test_read_one_carries_the_max_permission_header(crud_client: CrudClient) -> None:
    client, _, _ = crud_client
    client.put("/widgets", json={"title": "x"})
    resp = client.get("/widgets/1")
    assert resp.status_code == 200
    assert resp.headers["x-max-permission"] == "2"
    assert resp.headers["access-control-expose-headers"] == "x-max-permission"


def test_read_all_carries_the_pagination_headers(crud_client: CrudClient) -> None:
    client, _, _ = crud_client
    for n in range(3):
        client.put("/widgets", json={"title": f"w{n}"})
    resp = client.get("/widgets")
    assert resp.headers["x-pagination-result-count"] == "3"
    assert resp.headers["x-pagination-total-pages"] == "1"
    assert "x-pagination-total-pages" in resp.headers["access-control-expose-headers"]


def test_read_all_is_an_empty_list_when_there_is_nothing(crud_client: CrudClient) -> None:
    client, _, _ = crud_client
    resp = client.get("/widgets")
    assert resp.json() == []
    assert resp.headers["x-pagination-total-pages"] == "0"


def test_read_all_honours_the_search_parameter(crud_client: CrudClient) -> None:
    client, _, _ = crud_client
    client.put("/widgets", json={"title": "apple"})
    client.put("/widgets", json={"title": "banana"})
    assert [w["title"] for w in client.get("/widgets?s=app").json()] == ["apple"]


def test_read_all_rejects_a_negative_page(crud_client: CrudClient) -> None:
    """The pagination guards apply inside the router, not just where hand-written."""
    client, _, _ = crud_client
    resp = client.get("/widgets?page=-1")
    assert resp.status_code == 400
    assert resp.json() == {"message": "Page number cannot be negative."}


def test_delete_returns_a_message_object(crud_client: CrudClient) -> None:
    client, _, _ = crud_client
    client.put("/widgets", json={"title": "x"})
    resp = client.delete("/widgets/1")
    assert resp.status_code == 200
    assert resp.json() == {"message": "Successfully deleted."}


def test_patch_is_not_registered_and_the_service_never_runs(crud_client: CrudClient) -> None:
    """★ The reverse of what this used to assert.

    It read ``test_patch_also_updates`` and checked that PATCH reached ``update``.
    Upstream does not serve the verb: measured on the reference service, ``PATCH`` on the
    task, project and label item paths all answer **405** with
    ``Allow: OPTIONS, DELETE, GET, POST``.

    ``service.calls`` is the load-bearing half. A 405 assertion alone would still pass if
    the route were registered and something else refused it; requiring that the service
    was never entered is what says the router declined to route at all.
    """
    client, service, _ = crud_client
    client.put("/widgets", json={"title": "x"})

    resp = client.patch("/widgets/1", json={"title": "patched"})

    assert resp.status_code == 405
    assert resp.json() == {"message": "Method Not Allowed"}
    assert service.calls == ["create"]


def test_a_405_carries_the_allow_header(crud_client: CrudClient) -> None:
    """Upstream sends one, and it is taken off the exception Starlette already built
    rather than recomputed — see core.errors."""
    client, _, _ = crud_client
    client.put("/widgets", json={"title": "x"})

    allow = client.patch("/widgets/1", json={"title": "x"}).headers.get("allow")

    assert allow is not None
    assert set(allow.replace(" ", "").split(",")) >= {"GET", "POST", "DELETE"}


# --- policy ------------------------------------------------------------------


def test_a_denied_write_is_403_with_code_zero() -> None:
    """handler/error.go leaves Code unset, so it is 0 — not models' code 1."""
    client, service, _ = build(policy=DenyAll())
    resp = client.put("/widgets", json={"title": "x"})
    assert resp.status_code == 403
    assert resp.json() == {"code": 0, "message": FORBIDDEN_MESSAGE}
    assert service.calls == []


def test_a_denied_read_one_uses_its_own_wording() -> None:
    """DoReadOne overrides the message (handler/core.go:89); the others do not."""
    client, _, _ = build(policy=DenyAll())
    resp = client.get("/widgets/1")
    assert resp.status_code == 403
    assert resp.json() == {"code": 0, "message": FORBIDDEN_READ_MESSAGE}
    assert FORBIDDEN_READ_MESSAGE != FORBIDDEN_MESSAGE


@pytest.mark.parametrize(
    ("method", "path"),
    [("post", "/widgets/1"), ("delete", "/widgets/1")],
)
def test_every_write_verb_is_gated(method: str, path: str) -> None:
    client, service, _ = build(policy=DenyAll())
    resp = (
        getattr(client, method)(path, json={"title": "x"})
        if method == "post"
        else client.delete(path)
    )
    assert resp.status_code == 403
    assert service.calls == []


def test_the_policy_runs_before_the_service() -> None:
    """A denial must not reach the service at all, or a 403 could still mutate."""
    client, service, _ = build(policy=DenyAll())
    client.put("/widgets", json={"title": "x"})
    client.post("/widgets/1", json={"title": "x"})
    client.delete("/widgets/1")
    client.get("/widgets/1")
    assert service.calls == []


def test_read_all_has_no_collection_level_permission_gate() -> None:
    """DoReadAll (handler/core.go:111-130) runs no permission check whatsoever.

    Even a policy refusing everything must not turn a list into a 403: upstream
    returns 200 with whatever the service scoped to this caller, which for a new
    user is an empty list. Gating here made the most ordinary case in the product
    — "you have no labels yet" — a 403, across all 59 generated endpoints at once.
    """
    client, service, _ = build(policy=DenyAll())
    resp = client.get("/widgets")
    assert resp.status_code == 200
    assert resp.json() == []
    assert service.calls == ["read_all"]


def test_read_all_reports_zero_results_rather_than_refusing() -> None:
    client, _, _ = build(policy=DenyAll())
    assert client.get("/widgets").headers["x-pagination-result-count"] == "0"


# --- the response helpers on their own --------------------------------------


def test_read_one_response_helper() -> None:
    resp = read_one_response({"id": 1}, max_permission=0)
    assert resp.headers["x-max-permission"] == "0"
    assert resp.headers["Access-Control-Expose-Headers"] == "x-max-permission"


def test_deleted_response_helper() -> None:
    assert deleted_response().body == b'{"message":"Successfully deleted."}'


# --- write schemas must be strict (item 18) ---------------------------------


def test_a_lax_write_schema_is_refused_at_construction() -> None:
    """The base for ~59 endpoints, so this cannot be left to reviewer vigilance."""

    class LaxWrite(BaseModel):
        model_config = ConfigDict(extra="ignore")

        title: str = ""

    with pytest.raises(ValueError, match="strict=True"):
        CRUDRouter(
            prefix="/widgets",
            item_param="widget",
            service=WidgetService(),
            policy=AllowAll(),
            read_schema=WidgetRead,
            write_schema=LaxWrite,
        )


def test_a_string_is_not_silently_coerced_into_a_bool(crud_client: CrudClient) -> None:
    """The data-correctness case behind item 18.

    Pydantic's lax mode reads {"done": "yes"} as True. encoding/json refuses it,
    so upstream answers 400 and stores nothing — while we would have answered 200
    and persisted a value the user never sent. LLM clients emit loosely typed
    JSON constantly, so this is a live risk, not a theoretical one.
    """
    client, _, _ = crud_client
    client.put("/widgets", json={"title": "w"})

    resp = client.post("/widgets/1", json={"title": "w", "done": "yes"})
    assert resp.status_code == 400
    assert resp.json() == {"code": 2004, "message": INVALID_MODEL_MESSAGE}

    assert client.get("/widgets/1").json()["done"] is False


def test_a_numeric_string_is_not_silently_coerced_into_an_int(crud_client: CrudClient) -> None:
    client, _, _ = crud_client
    client.put("/widgets", json={"title": "w"})
    resp = client.post("/widgets/1", json={"title": "w", "priority": "3"})
    assert resp.status_code == 400


def test_real_bools_and_ints_still_work(crud_client: CrudClient) -> None:
    """Strict rejects coercion, not correct types."""
    client, _, _ = crud_client
    resp = client.put("/widgets", json={"title": "w", "done": True, "priority": 3})
    assert resp.status_code == 201
    assert resp.json()["done"] is True
    assert resp.json()["priority"] == 3


def test_a_strict_exemption_requires_a_justification_citing_go_source() -> None:
    """The escape hatch is deliberately awkward. encoding/json is strict
    everywhere, so an exemption should only ever come from the parity harness
    proving Go is lax somewhere — and then the proof travels with the code."""

    class LaxWrite(BaseModel):
        model_config = ConfigDict(extra="ignore")

        title: str = ""

    def build_with(exempt: str | None) -> CRUDRouter[Widget, WidgetRead, LaxWrite]:
        return CRUDRouter(
            prefix="/widgets",
            item_param="widget",
            service=WidgetService(),
            policy=AllowAll(),
            read_schema=WidgetRead,
            write_schema=LaxWrite,
            strict_exempt=exempt,
        )

    for useless in ("", "   ", "legacy client needs it", "tests were failing"):
        with pytest.raises(ValueError, match="must cite Go source"):
            build_with(useless)


def test_a_justified_exemption_is_accepted_and_recorded_for_audit() -> None:
    from calton.core.crud_router import STRICT_EXEMPTIONS

    class LegacyWrite(BaseModel):
        model_config = ConfigDict(extra="ignore")

        title: str = ""

    justification = "pkg/models/example.go:42 decodes this field with a custom lax UnmarshalJSON"
    CRUDRouter(
        prefix="/widgets",
        item_param="widget",
        service=WidgetService(),
        policy=AllowAll(),
        read_schema=WidgetRead,
        write_schema=LegacyWrite,
        strict_exempt=justification,
    )
    assert STRICT_EXEMPTIONS["LegacyWrite"] == justification


def test_no_exemptions_are_granted_in_phase_1() -> None:
    """Expected steady state. If this goes red, an exemption was added — read its
    justification and check the parity harness really backs it up."""
    from calton.core.crud_router import STRICT_EXEMPTIONS

    assert {k: v for k, v in STRICT_EXEMPTIONS.items() if not k.startswith("Legacy")} == {}


# --- step 0c: path parameters become ids in one place ------------------------


@pytest.mark.parametrize("raw", ["abc", "1.5", "9999999999999999999999", "1e5"])
def test_a_non_numeric_path_parameter_is_400_not_422(crud_client: CrudClient, raw: str) -> None:
    """Measured: the reference server answers 400 {"code":2004} for these.

    Echo binds the path parameter onto an int64 before any handler runs, so the failure
    is a binding error. FastAPI's instinct is 422, which is a different status *and* a
    different body shape.
    """
    client, _, _ = crud_client

    resp = client.get(f"/widgets/{raw}")

    assert resp.status_code == 400
    assert resp.json()["code"] == 2004


def test_an_empty_segment_never_reaches_id_conversion(crud_client: CrudClient) -> None:
    """``/widgets/`` is a routing question, not an id one, so it does not 400 here.

    Known divergence, recorded here only — it has **not** been entered in the parity
    corpus, so this comment is the whole of the record:
    the reference server answers 404 for ``GET /api/v1/projects/`` while FastAPI treats
    the trailing slash as the collection and answers 200. Nothing about id conversion
    can change that — it is decided before any handler runs.
    """
    client, _, _ = crud_client

    assert client.get("/widgets/").status_code == 200


def test_a_negative_id_is_accepted(crud_client: CrudClient) -> None:
    """``-1`` is the Favorites pseudo project upstream, so rejecting negatives breaks it.

    Measured: GET /api/v1/projects/-1 answers 200 on the reference server.
    """
    client, service, _ = crud_client
    service.widgets[-1] = Widget(-1, title="pseudo")

    resp = client.get("/widgets/-1")

    assert resp.status_code == 200
    assert resp.json()["title"] == "pseudo"


def test_the_id_reaches_the_service_as_an_int(crud_client: CrudClient) -> None:
    """Converted once in the router, so no service has to parse strings itself."""
    seen: list[object] = []

    class Recorder(WidgetService):
        def read_one(self, session: Any, auth: Any, **kwargs: Any) -> Widget:
            seen.append(kwargs["widget"])
            return Widget(1, title="w")

    app = FastAPI()
    register_exception_handlers(app)
    crud = CRUDRouter(
        prefix="/widgets",
        item_param="widget",
        service=Recorder(),
        policy=AllowAll(),
        read_schema=WidgetRead,
        write_schema=WidgetWrite,
    )
    app.dependency_overrides[get_db] = lambda: None
    app.include_router(crud.router)

    TestClient(app, raise_server_exceptions=False).get("/widgets/42")

    assert seen == [42]
    assert isinstance(seen[0], int)


# --- step 0b: one session per request, shared by both layers -----------------


def test_the_policy_and_the_service_get_the_same_session_object() -> None:
    """Not merely "both got a session" — the *same* one.

    Two sessions means the permission check runs in a different transaction from the
    write it guards, so it cannot see uncommitted work. Each layer passes its own tests
    in that state; only a comparison like this one fails.
    """
    seen: dict[str, object] = {}
    sentinel = object()

    class RecordingPolicy:
        def can_read(self, session: Any, auth: Any, **kwargs: Any) -> tuple[bool, int]:
            seen["policy"] = session
            return True, 2

        def can_create(self, session: Any, auth: Any, **kwargs: Any) -> bool:
            return True

        def can_update(self, session: Any, auth: Any, **kwargs: Any) -> bool:
            return True

        def can_delete(self, session: Any, auth: Any, **kwargs: Any) -> bool:
            return True

    class RecordingService(WidgetService):
        def read_one(self, session: Any, auth: Any, **kwargs: Any) -> Widget:
            seen["service"] = session
            return Widget(1, title="w")

    app = FastAPI()
    register_exception_handlers(app)
    crud = CRUDRouter(
        prefix="/widgets",
        item_param="widget",
        service=RecordingService(),
        policy=RecordingPolicy(),
        read_schema=WidgetRead,
        write_schema=WidgetWrite,
    )
    app.dependency_overrides[get_db] = lambda: sentinel
    app.include_router(crud.router)

    TestClient(app, raise_server_exceptions=False).get("/widgets/1")

    assert seen["policy"] is sentinel
    assert seen["service"] is sentinel


# --- the router owns the transaction -----------------------------------------


def test_each_write_route_commits_exactly_once(crud_client: CrudClient) -> None:
    """``get_db`` yields a session and closes it without committing.

    So a service that only flushes has its work rolled back once the request ends — after
    the response body was built from the in-memory object, which makes the response
    completely correct and the database empty. Nothing about that failure points at the
    transaction; it looks like the write "sometimes doesn't take". Labels were the first
    resource mounted here and lost every write to it.

    Counted rather than merely observed as non-zero, because committing twice is its own
    bug (the second one would swallow work the first did not intend to publish).
    """
    client, _service, _crud = crud_client
    session = client.app.state.session_stub  # type: ignore[attr-defined]

    assert client.put("/widgets", json={"title": "x"}).status_code == 201
    assert session.commits == 1

    assert client.post("/widgets/1", json={"title": "y"}).status_code == 200
    assert session.commits == 2

    assert client.delete("/widgets/1").status_code == 200
    assert session.commits == 3


def test_reads_do_not_commit(crud_client: CrudClient) -> None:
    """The canary for the test above.

    If ``commits`` rose on every request — a stub that counted something else, or a commit
    installed in a dependency rather than in the write routes — the counts above would be
    satisfied by an implementation that never committed a write at all.
    """
    client, _service, _crud = crud_client
    session = client.app.state.session_stub  # type: ignore[attr-defined]

    client.get("/widgets")
    client.get("/widgets/1")
    assert session.commits == 0


def test_a_refused_write_does_not_commit() -> None:
    """A policy refusal must not publish a transaction. The service never ran, so there is
    nothing to commit, and committing anyway would persist whatever an earlier failed
    request left in the session."""
    client, _service, _crud = build(policy=DenyAll())
    session = client.app.state.session_stub  # type: ignore[attr-defined]

    assert client.put("/widgets", json={"title": "x"}).status_code == 403
    assert client.delete("/widgets/1").status_code == 403
    assert session.commits == 0


# --- the read/write serialiser split -----------------------------------------


def _split_client() -> TestClient:
    """A router whose two serialisers answer differently, so the routes can be told apart."""
    app = FastAPI()
    register_exception_handlers(app)
    crud = CRUDRouter(
        prefix="/widgets",
        item_param="widget",
        service=WidgetService(),
        policy=AllowAll(),
        read_schema=WidgetRead,
        write_schema=WidgetWrite,
        serialize=lambda model, session, in_collection: {"id": model.id, "who": "read"},
        serialize_write=lambda model, session, in_collection: {"id": model.id, "who": "write"},
    )
    session = SessionStub()
    app.state.session_stub = session
    app.dependency_overrides[get_db] = lambda: session
    app.include_router(crud.router)
    return TestClient(app, raise_server_exceptions=False)


def test_the_write_routes_use_the_write_serialiser() -> None:
    """★ PUT and POST render through ``serialize_write``; GET renders through ``serialize``.

    Both hooks are handed the *same* function by the two resources mounted on this router
    today, because upstream's project and label updates are the only two of the seven that
    overwrite their receiver from storage before answering — so their write response really
    does equal their read response. That makes every existing assertion here pass under a
    single shared serialiser as well, which is why this test supplies two that disagree:
    without it, folding the split back into one hook is invisible.
    """
    client = _split_client()

    assert client.put("/widgets", json={"title": "x"}).json()["who"] == "write"
    assert client.post("/widgets/1", json={"title": "y"}).json()["who"] == "write"
    assert client.get("/widgets/1").json()["who"] == "read"
    assert client.get("/widgets").json()[0]["who"] == "read"


def test_the_write_serialiser_defaults_to_the_read_one() -> None:
    """Omitting ``serialize_write`` keeps the two identical — the state both resources are
    in, and the reason the split changed no observable behaviour when it landed."""
    client, _, _ = build()

    created = client.put("/widgets", json={"title": "x"}).json()

    assert created["owner"] == "someone"  # the read schema's default, i.e. the read shape
    assert client.get("/widgets/1").json()["owner"] == "someone"


# --- body id shadows the path ------------------------------------------------


class WidgetWriteWithId(WidgetWrite):
    id: int = 0


class IdAwareWidgetService(WidgetService):
    """``WidgetService`` for a write schema that declares ``id``.

    The base one splats the whole body into ``Widget(id, **fields)``, which now collides
    with the model's own ``id``. Dropping it here keeps the collision out of the thing
    under test — the router's choice of key, not the double's constructor.
    """

    def create(self, session: Any, data: BaseModel, auth: Any, **kwargs: Any) -> Widget:
        fields = {k: v for k, v in data.model_dump().items() if k != "id"}
        self.calls.append("create")
        widget = Widget(self.next_id, **fields)
        self.widgets[widget.id] = widget
        self.next_id += 1
        return widget

    def update(self, session: Any, data: BaseModel, auth: Any, **kwargs: Any) -> Widget:
        fields = {k: v for k, v in data.model_dump().items() if k != "id"}
        self.calls.append("update")
        widget = Widget(int(kwargs["widget"]), **fields)
        self.widgets[widget.id] = widget
        return widget


def _id_client() -> tuple[TestClient, WidgetService]:
    app = FastAPI()
    register_exception_handlers(app)
    service = IdAwareWidgetService()
    crud = CRUDRouter(
        prefix="/widgets",
        item_param="widget",
        service=service,
        policy=AllowAll(),
        read_schema=WidgetRead,
        write_schema=WidgetWriteWithId,
    )
    session = SessionStub()
    app.dependency_overrides[get_db] = lambda: session
    app.include_router(crud.router)
    return TestClient(app, raise_server_exceptions=False), service


def test_a_body_id_on_update_wins_over_the_path() -> None:
    """★ Echo binds the path parameter before the body, so a body ``id`` shadows it.

    Measured upstream: ``POST /labels/950 {"id": 951}`` renames **951** and leaves 950
    alone, and the same holds for tasks and projects.

    The assertion is on *which object the service was asked for*, not on the status: both
    readings answer 200, so a status assertion is satisfied by either. Honouring the path
    is the safer-looking choice and silently writes to a different row than upstream.
    """
    client, service = _id_client()
    client.put("/widgets", json={"title": "a"})  # id 1
    client.put("/widgets", json={"title": "b"})  # id 2

    body = client.post("/widgets/1", json={"title": "changed", "id": 2}).json()

    assert body["id"] == 2
    assert service.widgets[2].title == "changed"
    assert service.widgets[1].title == "a", "the path's object must be untouched"


def test_a_zero_body_id_falls_back_to_the_path() -> None:
    """The fallback half. A client that omits ``id`` — which is most of them — must still
    address the object named in the URL; ``0`` is the Go zero value, not a real id."""
    client, service = _id_client()
    client.put("/widgets", json={"title": "a"})

    body = client.post("/widgets/1", json={"title": "changed"}).json()

    assert body["id"] == 1
    assert service.widgets[1].title == "changed"
