"""T06 — the contract diff, and the tests that prove the diff can fail.

A contract test that cannot go red is worse than no contract test, so most of
this file checks the framework itself: an empty app must fail all 87 operations,
and a deliberately broken schema must be caught.

The live diff runs over whatever Calton currently implements and grows on its own
as endpoints land. Set ``CONTRACT_STRICT=1`` to additionally demand that all 87
are implemented — that is AC-2, and the CI contract job flips it at the end of
Phase 1.
"""

import importlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

from calton.contract.diff import diff_operation, generated_operations
from calton.contract.golden import (
    CORRECTED_PATH,
    GOLDEN_PATH,
    OperationKey,
    load_aliases,
    load_golden,
    load_phase1_whitelist,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVER_ROOT = REPO_ROOT / "server"
WHITELIST = load_phase1_whitelist()
GOLDEN = load_golden()
STRICT = os.environ.get("CONTRACT_STRICT") == "1"


def calton_main_is_absent() -> bool:
    """True only when calton.main genuinely does not exist on this branch.

    The previous `try: ... except ImportError: return FastAPI()` made "T01 is not
    merged here yet" indistinguishable from "the app is broken": a circular
    import, a missing dependency or a typo inside main.py all raise ImportError
    too, and every one of them would have skipped all 87 operations and reported
    the contract job green.

    So only a ModuleNotFoundError naming calton.main itself counts as absent.
    Anything else propagates and fails the run, loudly.

    TODO: delete this and its uses once coder-a's T01 is merged into this branch;
    calton/main.py is theirs, not ours to create.
    """
    try:
        importlib.import_module("calton.main")
    except ModuleNotFoundError as exc:
        if exc.name == "calton.main":
            return True
        raise
    return False


needs_app = pytest.mark.skipif(
    calton_main_is_absent(),
    reason="calton.main lands with coder-a's T01; not merged into this branch yet",
)


def calton_app() -> FastAPI:
    """The app under test. Raises if calton.main is broken rather than absent."""
    from calton.main import create_app

    return create_app()


# --- the whitelist itself ----------------------------------------------------


def test_the_whitelist_promises_exactly_90_operations() -> None:
    # 68 for most of this project's life; POST /token/test was added once measured
    # (418 with credentials, 401 without — auth precedes the handler), making 69. The
    # eight team operations were Phase 2's first group; the four project-webhook ones
    # are the second, plus GET /webhooks/events — approved separately, because doing
    # four of a resource's five routes is worse than doing none: the fifth reads as
    # possibly-deliberate and costs the next person time to rule out. Notifications and
    # the sharing PUTs are still deferred, so nothing was added for them.
    #
    # ⚠️ This total has TWO more copies — harness/test_coverage.PHASE1_ENDPOINT_COUNT
    # and harness/corpus/_endpoints.yaml — in a different suite. Changing this one and
    # running only this suite is how the webhook four reached mainline with two reds.
    #
    # ⚠️ The webhook four were nearly cut on a wrong conclusion of mine: the `webhooks`
    # permission group holds only `events`, and I read that as "no API token can reach
    # these". They file under `projects_webhooks`, and a token granted that reaches all
    # four. Routes do not map one-to-one onto group names.
    #
    # ⚠️ Eight, not the seven the card asked for. DELETE /teams/{id} is a real upstream
    # route (routes.go:825) and appears in the reference server's own GET /routes as
    # teams.delete; it was missing from the card, not from upstream.
    #
    # The count is spelled out rather than derived so that adding a line to
    # phase1-endpoints.yaml is a deliberate act with a visible diff, not something that
    # happens quietly.
    assert len(WHITELIST) == 90


def test_the_whitelist_has_no_duplicates() -> None:
    assert len(set(WHITELIST)) == len(WHITELIST)


def test_every_whitelisted_operation_exists_in_the_corrected_contract() -> None:
    """Except the Calton-only aliases, which upstream has never had."""
    alias_keys = {(a["method"].upper(), a["path"]) for a in load_aliases()}
    for key in WHITELIST:
        if key in alias_keys:
            continue
        assert key in GOLDEN, f"{key[0]} {key[1]} is whitelisted but not in the contract"


def test_the_only_calton_only_operation_is_the_tasks_all_alias() -> None:
    alias_keys = {(a["method"].upper(), a["path"]) for a in load_aliases()}
    assert alias_keys == {("GET", "/tasks/all")}
    assert set(WHITELIST) - set(GOLDEN) == alias_keys


def test_the_corrected_contract_has_the_added_operations() -> None:
    """169 documented operations plus the real-but-undocumented routes that
    swagger-corrections.yaml adds. Path parameters are normalised before keying,
    and this asserts no two operations collapsed together when they did.

    Three additions, not two: GET and POST /token/test are separate registrations
    with different handlers (routes.go:545-546), and POST was measured later.
    """
    assert len(load_golden(apply_corrections=False)) == 169
    assert len(GOLDEN) == 172


# --- the corrections are still needed ----------------------------------------


def test_corrections_are_applied() -> None:
    assert ("GET", "/token/test") in GOLDEN
    assert ("GET", "/projects/{}/tasks") in GOLDEN
    assert ("POST", "/labels/{}") in GOLDEN
    assert ("PUT", "/labels/{}") not in GOLDEN
    assert GOLDEN[("DELETE", "/labels/{}")].response_fields == frozenset({"message"})


def test_the_golden_file_declares_a_concrete_version() -> None:
    """Go's swag leaves info.version null, which puts openapi-typescript into
    patch mode for coder-c. We fill it with the upstream file's sha prefix."""
    spec = json.loads(GOLDEN_PATH.read_text())
    version = spec["info"]["version"]
    assert version, "info.version must not be null; coder-c's generator depends on it"
    assert version == "v1-frozen-feb3f68b"


def test_the_golden_file_is_the_frozen_upstream_swagger() -> None:
    spec = json.loads(GOLDEN_PATH.read_text())
    assert spec["swagger"] == "2.0"
    assert spec["basePath"] == "/api/v1"
    assert len(spec["paths"]) == 126
    assert len(spec["definitions"]) == 98


# --- the framework can go red ------------------------------------------------


def test_an_empty_app_fails_every_whitelisted_operation() -> None:
    """Card acceptance ①: against an empty app all 90 must report red."""
    generated = generated_operations(FastAPI())
    diffs = [diff_operation(key, GOLDEN, generated) for key in WHITELIST]
    assert len(diffs) == 90
    assert all(d.missing_operation for d in diffs)
    assert not any(d.ok for d in diffs)


def test_a_misspelled_response_field_is_caught() -> None:
    """Card acceptance ②: hand-build a schema with a typo; the diff must catch it."""
    key = ("GET", "/projects/{}")
    golden_fields = GOLDEN[key].response_fields
    assert "title" in golden_fields, "fixture assumes Project has a title"

    app = FastAPI()

    @app.get("/api/v1/projects/{id}")
    def read_project(id: int) -> dict[str, Any]:
        return {}

    # Splice in a response schema that misspells one real field.
    spec = app.openapi()
    spec["paths"]["/api/v1/projects/{id}"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"] = {
        "type": "object",
        "properties": {name: {} for name in golden_fields if name != "title"} | {"titel": {}},
    }
    app.openapi_schema = spec

    diff = diff_operation(key, GOLDEN, generated_operations(app))
    assert not diff.ok
    assert diff.missing_response_fields == frozenset({"title"})
    assert "title" in diff.describe()


def test_extra_fields_are_allowed_but_missing_ones_are_not() -> None:
    """Superset rule: right/max_right double-writing must not trip the diff."""
    key = ("GET", "/projects/{}")
    golden_fields = GOLDEN[key].response_fields

    app = FastAPI()

    @app.get("/api/v1/projects/{id}")
    def read_project(id: int) -> dict[str, Any]:
        return {}

    spec = app.openapi()
    spec["paths"]["/api/v1/projects/{id}"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"] = {
        "type": "object",
        "properties": {name: {} for name in golden_fields} | {"max_right": {}, "right": {}},
    }
    app.openapi_schema = spec

    assert diff_operation(key, GOLDEN, generated_operations(app)).ok


def test_a_newly_required_parameter_is_caught() -> None:
    key = ("GET", "/projects/{}")

    app = FastAPI()

    @app.get("/api/v1/projects/{id}")
    def read_project(id: int, mandatory: str) -> dict[str, Any]:
        return {}

    spec = app.openapi()
    spec["paths"]["/api/v1/projects/{id}"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"] = {
        "type": "object",
        "properties": {name: {} for name in GOLDEN[key].response_fields},
    }
    app.openapi_schema = spec

    diff = diff_operation(key, GOLDEN, generated_operations(app))
    assert not diff.ok
    assert diff.extra_required_params == frozenset({"mandatory"})


def test_operations_outside_the_whitelist_are_not_diffed() -> None:
    """Card acceptance ④: upstream has 169 operations; we only owe 77."""
    generated = generated_operations(FastAPI())
    assert ("GET", "/admin/overview") in GOLDEN
    assert ("GET", "/admin/overview") not in WHITELIST
    diffs = [diff_operation(key, GOLDEN, generated) for key in WHITELIST]
    assert all(d.key in WHITELIST for d in diffs)


#: Endpoints wired into create_app so far. The tripwire below fires whenever this
#: is out of date, forcing a deliberate look rather than letting the contract job
#: drift into testing nothing. When it covers all 87, delete the tripwire and set
#: CONTRACT_STRICT=1 in CI.
WIRED_ENDPOINTS = {
    "/api/v1/admin/overview",
    "/api/v1/admin/projects",
    "/api/v1/admin/projects/{id}/owner",
    "/api/v1/admin/users",
    "/api/v1/admin/users/{id}",
    "/api/v1/admin/users/{id}/admin",
    "/api/v1/admin/users/{id}/status",
    "/api/v1/auth/openid/{provider}/callback",
    "/api/v1/backgrounds/unsplash/image/{image}",
    "/api/v1/backgrounds/unsplash/image/{image}/thumb",
    "/api/v1/backgrounds/unsplash/search",
    "/api/v1/filters",
    "/api/v1/filters/{filter}",
    "/api/v1/info",
    "/api/v1/labels",
    "/api/v1/labels/{label}",
    "/api/v1/login",
    "/api/v1/migration/calton-file/migrate",
    "/api/v1/migration/calton-file/status",
    "/api/v1/migration/csv/detect",
    "/api/v1/migration/csv/migrate",
    "/api/v1/migration/csv/preview",
    "/api/v1/migration/csv/status",
    "/api/v1/migration/microsoft-todo/auth",
    "/api/v1/migration/microsoft-todo/migrate",
    "/api/v1/migration/microsoft-todo/status",
    "/api/v1/migration/ticktick/migrate",
    "/api/v1/migration/ticktick/status",
    "/api/v1/migration/todoist/auth",
    "/api/v1/migration/todoist/migrate",
    "/api/v1/migration/todoist/status",
    "/api/v1/migration/trello/auth",
    "/api/v1/migration/trello/migrate",
    "/api/v1/migration/trello/status",
    "/api/v1/migration/wekan/migrate",
    "/api/v1/migration/wekan/status",
    "/api/v1/notifications",
    "/api/v1/notifications/{notification_id}",
    "/api/v1/projects",
    "/api/v1/projects/{id}/background",
    "/api/v1/projects/{id}/backgrounds/unsplash",
    "/api/v1/projects/{project}",
    "/api/v1/projects/{project}/projectusers",
    "/api/v1/projects/{project}/shares",
    "/api/v1/projects/{project}/tasks",
    "/api/v1/projects/{project}/tasks/by-index/{index}",
    "/api/v1/projects/{project}/teams",
    "/api/v1/projects/{project}/users",
    "/api/v1/projects/{project}/views",
    "/api/v1/projects/{project}/views/{id}",
    "/api/v1/projects/{project}/views/{view}/buckets",
    "/api/v1/projects/{project}/views/{view}/buckets/{bucket}",
    "/api/v1/projects/{project}/views/{view}/buckets/{bucket}/tasks",
    "/api/v1/projects/{project}/views/{view}/tasks",
    "/api/v1/projects/{project}/webhooks",
    "/api/v1/projects/{project}/webhooks/{webhook}",
    "/api/v1/register",
    "/api/v1/routes",
    "/api/v1/tasks",
    "/api/v1/tasks/all",
    "/api/v1/tasks/bulk",
    "/api/v1/tasks/{task}",
    "/api/v1/tasks/{task}/assignees",
    "/api/v1/tasks/{task}/assignees/bulk",
    "/api/v1/tasks/{task}/assignees/{userID}",
    "/api/v1/tasks/{task}/attachments",
    "/api/v1/tasks/{task}/attachments/{attachment}",
    "/api/v1/tasks/{task}/comments",
    "/api/v1/tasks/{task}/comments/{commentid}",
    "/api/v1/tasks/{task}/labels",
    "/api/v1/tasks/{task}/labels/bulk",
    "/api/v1/tasks/{task}/labels/{label}",
    "/api/v1/tasks/{task}/relations",
    "/api/v1/tasks/{task}/relations/{relationKind}/{otherTask}",
    "/api/v1/teams",
    "/api/v1/teams/{id}",
    "/api/v1/teams/{id}/members",
    "/api/v1/teams/{id}/members/{username}",
    "/api/v1/teams/{id}/members/{username}/admin",
    "/api/v1/token/test",
    "/api/v1/tokens",
    "/api/v1/tokens/{tokenID}",
    "/api/v1/user",
    "/api/v1/user/confirm",
    "/api/v1/user/deletion/cancel",
    "/api/v1/user/deletion/confirm",
    "/api/v1/user/deletion/request",
    "/api/v1/user/export",
    "/api/v1/user/export/download",
    "/api/v1/user/export/request",
    "/api/v1/user/logout",
    "/api/v1/user/password",
    "/api/v1/user/password/reset",
    "/api/v1/user/password/token",
    "/api/v1/user/settings/avatar",
    "/api/v1/user/settings/avatar/upload",
    "/api/v1/user/settings/email",
    "/api/v1/user/settings/general",
    "/api/v1/user/settings/token/caldav",
    "/api/v1/user/settings/token/caldav/{id}",
    "/api/v1/user/settings/totp",
    "/api/v1/user/settings/totp/disable",
    "/api/v1/user/settings/totp/enable",
    "/api/v1/user/settings/totp/enroll",
    "/api/v1/user/settings/totp/qrcode",
    "/api/v1/user/settings/webhooks",
    "/api/v1/user/settings/webhooks/events",
    "/api/v1/user/settings/webhooks/{id}",
    "/api/v1/user/timezones",
    "/api/v1/user/token",
    "/api/v1/user/token/refresh",
    "/api/v1/users",
    "/api/v1/webhooks/events",
    "/api/v1/{username}/avatar",
}


@needs_app
def test_tripwire_records_exactly_which_endpoints_are_wired() -> None:
    """Fires whenever the wired set changes, in either direction.

    Originally this asserted "zero /api/v1 routes", counted via
    `getattr(route, "path", "")` over app.routes — which this FastAPI version
    wraps in _IncludedRouter objects that have no .path. So it saw zero routes
    even after /info and /test/* were mounted: the tripwire was blind by
    construction, the exact failure it exists to prevent. Counting comes from the
    generated OpenAPI now, which is also what the diff itself reads.
    """
    # Counted from the generated OpenAPI, not from app.routes: this FastAPI
    # version wraps included routers in _IncludedRouter objects with no .path, so
    # the obvious `getattr(r, "path", "")` scan silently sees nothing and the
    # tripwire would never fire — the exact failure it exists to prevent.
    paths = {p for p in calton_app().openapi()["paths"] if p.startswith("/api/v1")}
    # /test/* is harness plumbing gated behind service.testingtoken, never part of
    # the 68 and never present in a deployment.
    business = {p for p in paths if not p.startswith("/api/v1/test")}

    assert business == WIRED_ENDPOINTS, (
        f"wired endpoints changed: now {sorted(business)}, recorded {sorted(WIRED_ENDPOINTS)}. "
        "Update WIRED_ENDPOINTS, and once all 87 are wired delete this tripwire "
        "and set CONTRACT_STRICT=1 in the CI contract job."
    )


# --- corrections carry real evidence (item 13) -------------------------------


@needs_app
@pytest.mark.parametrize("key", WHITELIST, ids=lambda k: f"{k[0]} {k[1]}")
def test_implemented_operations_match_the_contract(key: OperationKey) -> None:
    generated = generated_operations(calton_app())
    diff = diff_operation(key, GOLDEN, generated)
    if diff.missing_operation and not STRICT:
        pytest.skip("not implemented yet; set CONTRACT_STRICT=1 to require it")

    # No register to subtract any more: the read and write serialisers are separate
    # models, so every response field carries its real element type and a widened one is
    # a plain failure. `labels`, `attachments` and `reminders` were the three entries.
    assert not diff.describe_if_broken(), diff.describe()


@needs_app
def test_no_response_field_is_widened_anywhere() -> None:
    """★ The replacement for the loose-field register, which no longer exists.

    Until the read and write serialisers were split, ``TaskRead`` served both and had to
    declare ``labels``/``attachments``/``reminders`` as untyped arrays — the write path
    assigns the client's own values onto the response model, and a concrete element type
    made pydantic raise *after the write had committed*. Three fields were registered as
    knowingly widened, and the cost was paid on the read side: the generated TypeScript
    got ``any[]`` for them on every task response.

    ``TaskWriteResponse`` now carries the loose collections and ``TaskRead`` the concrete
    ones, so there is nothing left to register. This asserts the register cannot come back
    by the front door — a new widened field is a failure, not an entry.
    """
    generated = generated_operations(calton_app())
    widened = {
        (key, entry)
        for key in WHITELIST
        for entry in diff_operation(key, GOLDEN, generated).widened_field_types
    }
    assert not widened, (
        f"{sorted(widened)} declare a weaker element type than upstream. Do not add a "
        f"register back — give the field its real type, and if the write path is what "
        f"forces the widening, put the loose declaration on the write response model."
    )


# --- the corrected contract exists on disk (item 14) -------------------------


def test_the_corrected_contract_is_checked_in() -> None:
    """The frontend's generator reads a file, not our Python.

    Applying corrections only in memory meant gen-api-types.mjs fell back to the
    raw swagger and inherited all three defects into the TypeScript types —
    including label update typed as PUT when the server serves POST. Both
    languages now read this one artefact.
    """
    paths = json.loads(CORRECTED_PATH.read_text())["paths"]
    assert "post" in paths["/labels/{label}"]
    assert "put" not in paths.get("/labels/{id}", {})
    assert "get" in paths["/token/test"]
    assert "get" in paths["/projects/{project}/tasks"]


def test_the_corrected_contract_is_marked_as_derived() -> None:
    assert json.loads(CORRECTED_PATH.read_text())["info"]["version"].endswith("-corrected")


def test_the_corrected_contract_records_why_each_change_was_made() -> None:
    """Someone reading only the JSON should find the routes.go citation."""
    corrected = json.loads(CORRECTED_PATH.read_text())
    for path, method in (
        ("/labels/{label}", "post"),
        ("/token/test", "get"),
        ("/projects/{project}/tasks", "get"),
    ):
        description = corrected["paths"][path][method].get("description", "")
        assert "[calton]" in description
        assert "routes.go:" in description


def test_the_in_memory_corrections_agree_with_the_file() -> None:
    """The two routes to the same fact must not diverge."""
    from calton.contract.golden import _operations_of

    corrected = json.loads(CORRECTED_PATH.read_text())
    assert set(_operations_of(corrected)) == set(GOLDEN)


def _splice_response_schema(app: FastAPI, path: str, schema: dict[str, Any]) -> FastAPI:
    spec = app.openapi()
    spec["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"] = schema
    app.openapi_schema = spec
    return app


class TestPolymorphicResponses:
    """`GET /projects/{id}/views/{view}/tasks` does not always return `Task[]`.

    When the view has a bucket configuration and the filter does not mention
    `bucket_id`, Go returns `Bucket[]` instead — each bucket carrying its own
    tasks (``task_collection.go:173-184``). Measured against the Go reference:
    the List view of project 1 returns 20 Tasks, the Kanban view returns 3
    Buckets, from the same data.

    Calton has to express that as a Union response model to satisfy C-1 (every
    handler declares a concrete response model; no `-> dict`), and FastAPI
    renders a Union as a top-level `anyOf`. These tests pin what the diff engine
    makes of that, because the answer used to be "nothing at all".
    """

    key = ("GET", "/projects/{}/views/{}/tasks")

    def test_upstream_declares_only_the_flat_shape(self) -> None:
        """The premise. Upstream's swagger never describes the bucket branch, so
        the golden contract can only ever speak for `Task[]` — which is why the
        union rule below cannot be tightened into an intersection."""
        assert "title" in GOLDEN[self.key].response_fields
        assert "done" in GOLDEN[self.key].response_fields

    def test_an_anyof_response_is_not_read_as_having_no_fields(self) -> None:
        """The regression this fix exists for.

        `anyOf` matched no branch of `_field_names`, so the operation reported
        zero fields and the diff claimed every upstream field was missing. A
        wrong red is nearly as expensive as a wrong green: it is indistinguishable
        from a real regression right up until someone spends an hour on it.
        """
        golden_fields = GOLDEN[self.key].response_fields
        app = FastAPI()

        @app.get("/api/v1/projects/{id}/views/{view}/tasks")
        def view_tasks(id: int, view: int) -> dict[str, Any]:
            return {}

        _splice_response_schema(
            app,
            "/api/v1/projects/{id}/views/{view}/tasks",
            {
                "anyOf": [
                    {
                        "type": "array",
                        "items": {"properties": {name: {} for name in golden_fields}},
                    },
                    {
                        "type": "array",
                        "items": {"properties": {"id": {}, "title": {}, "tasks": {}}},
                    },
                ]
            },
        )

        assert diff_operation(self.key, GOLDEN, generated_operations(app)).ok

    def test_a_field_dropped_from_every_branch_is_still_caught(self) -> None:
        """What the union rule still protects. Widening to a union weakens the
        check but must not switch it off: a field that no branch provides is a
        real regression and stays red."""
        golden_fields = GOLDEN[self.key].response_fields
        app = FastAPI()

        @app.get("/api/v1/projects/{id}/views/{view}/tasks")
        def view_tasks(id: int, view: int) -> dict[str, Any]:
            return {}

        without_done: dict[str, Any] = {name: {} for name in golden_fields if name != "done"}
        _splice_response_schema(
            app,
            "/api/v1/projects/{id}/views/{view}/tasks",
            {
                "anyOf": [
                    {"type": "array", "items": {"properties": without_done}},
                    {"type": "array", "items": {"properties": {"id": {}, "tasks": {}}}},
                ]
            },
        )

        diff = diff_operation(self.key, GOLDEN, generated_operations(app))
        assert not diff.ok
        assert "done" in diff.missing_response_fields

    def test_a_field_in_only_one_branch_is_the_known_blind_spot(self) -> None:
        """Documented limitation, asserted so it is a decision rather than a bug.

        `done` living only in the Task branch satisfies the diff. It has to:
        that is precisely the shape of a polymorphic response, and upstream
        publishes no schema for the second branch to check against. The parity
        harness is what covers this instead — it compares real responses, so it
        sees whichever branch Go actually returned.
        """
        golden_fields = GOLDEN[self.key].response_fields
        app = FastAPI()

        @app.get("/api/v1/projects/{id}/views/{view}/tasks")
        def view_tasks(id: int, view: int) -> dict[str, Any]:
            return {}

        _splice_response_schema(
            app,
            "/api/v1/projects/{id}/views/{view}/tasks",
            {
                "anyOf": [
                    {
                        "type": "array",
                        "items": {"properties": {name: {} for name in golden_fields}},
                    },
                    {"type": "array", "items": {"properties": {"id": {}, "tasks": {}}}},
                ]
            },
        )

        assert diff_operation(self.key, GOLDEN, generated_operations(app)).ok

    def test_oneof_is_handled_the_same_as_anyof(self) -> None:
        """Pydantic emits `anyOf`; a hand-written or regenerated spec may say
        `oneOf` for the same intent. Treating only one of them would make the
        check depend on which tool produced the document."""
        golden_fields = GOLDEN[self.key].response_fields
        app = FastAPI()

        @app.get("/api/v1/projects/{id}/views/{view}/tasks")
        def view_tasks(id: int, view: int) -> dict[str, Any]:
            return {}

        _splice_response_schema(
            app,
            "/api/v1/projects/{id}/views/{view}/tasks",
            {
                "oneOf": [
                    {
                        "type": "array",
                        "items": {"properties": {name: {} for name in golden_fields}},
                    },
                    {"type": "array", "items": {"properties": {"id": {}, "tasks": {}}}},
                ]
            },
        )

        assert diff_operation(self.key, GOLDEN, generated_operations(app)).ok


# --- the type dimension ----------------------------------------------------
#
# Field *names* were the only thing compared until a widened type shipped
# unnoticed: a `number` field started advertising `anyOf: [integer, number]` and
# nothing in the suite said a word. A wrong value makes a client throw; a wrong
# type makes a generated client quietly accept something the API never sends.


def test_a_widened_field_type_is_caught() -> None:
    """The case that motivated the dimension, encoded so it cannot regress.

    Upstream declares `number`; offering `integer | number` admits values the
    real API never sends. This is the shape `WithJsonSchema` in `db/types.py`
    exists to prevent — delete that line and this test is what should notice.
    """
    from calton.contract.golden import Operation

    theirs = Operation(
        method="GET",
        path="/x",
        response_fields=frozenset({"position"}),
        field_types=frozenset({("position", frozenset({"number"}))}),
    )
    ours = Operation(
        method="GET",
        path="/x",
        response_fields=frozenset({"position"}),
        field_types=frozenset({("position", frozenset({"integer", "number"}))}),
    )
    result = diff_operation(("GET", "/x"), {("GET", "/x"): theirs}, {("GET", "/x"): ours})
    assert not result.ok
    assert result.widened_field_types == frozenset({("position", "number", "integer|number")})
    assert "widens" in result.describe()


def test_narrowing_a_type_is_not_reported() -> None:
    """Producing fewer types than upstream declares is not an incompatibility.

    Every client written against upstream can still read it. Reporting it would
    push us to declare types we never emit, which is the opposite of the goal.
    """
    from calton.contract.golden import Operation

    theirs = Operation(
        method="GET",
        path="/x",
        response_fields=frozenset({"f"}),
        field_types=frozenset({("f", frozenset({"string", "null"}))}),
    )
    ours = Operation(
        method="GET",
        path="/x",
        response_fields=frozenset({"f"}),
        field_types=frozenset({("f", frozenset({"string"}))}),
    )
    assert diff_operation(("GET", "/x"), {("GET", "/x"): theirs}, {("GET", "/x"): ours}).ok


def test_nullability_is_not_compared_because_upstream_cannot_express_it() -> None:
    """Guards the *reason*, not just the behaviour.

    swaggo emits no `x-nullable`/`nullable` anywhere, so our `X | None` would be
    compared against silence rather than against a claim. On the first run that
    produced 24 of 26 differences, and satisfying them would have meant deleting
    `| None` from fields where **null is the measured upstream behaviour**
    (`created_by` on a partial update, `reminders`, a project's `views`).

    If someone later teaches upstream's spec to express nullability, this test is
    where the decision gets revisited — the assertion below will start failing and
    say why.
    """
    golden_text = GOLDEN_PATH.read_text()
    assert '"x-nullable"' not in golden_text
    assert '"nullable"' not in golden_text

    from calton.contract.golden import _canonical_types

    assert _canonical_types({"type": ["string", "null"]}, {}) == frozenset({"string"})
    assert _canonical_types({"type": "string", "x-nullable": True}, {}) == frozenset({"string"})


def test_generator_dialect_is_not_reported_as_a_difference() -> None:
    """`format`, `title` and `default` differ between swaggo and FastAPI harmlessly.

    Comparing them would report a difference on nearly every field and bury the
    handful that matter — the same "noise drowns signal" failure that makes a
    desynchronised diff worthless.
    """
    from calton.contract.golden import _canonical_types

    swaggo = {"type": "integer", "format": "int64"}
    fastapi = {"type": "integer", "title": "Id", "default": 0}
    assert _canonical_types(swaggo, {}) == _canonical_types(fastapi, {}) == frozenset({"integer"})


def test_an_array_element_type_is_part_of_the_contract() -> None:
    """`string[]` and `object[]` are different contracts, so the element type is kept."""
    from calton.contract.golden import _canonical_types

    assert _canonical_types({"type": "array", "items": {"type": "string"}}, {}) == frozenset(
        {"array<string>"}
    )
    assert _canonical_types({"type": "array", "items": {"type": "object"}}, {}) != _canonical_types(
        {"type": "array", "items": {"type": "string"}}, {}
    )
