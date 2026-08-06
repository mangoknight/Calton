"""Integration: the error handlers are actually attached to the real app.

Every other T04 test builds its own FastAPI() and registers the handlers by hand,
so the whole error layer passed its tests while being unreachable in production —
nothing called register_exception_handlers() from create_app(). These assertions
go through calton.main.create_app so that can never be true again.
"""

from typing import Annotated, Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from calton.core.errors import INVALID_MODEL_MESSAGE, CaltonError
from calton.db.types import GoValid
from calton.main import create_app


@pytest.fixture
def app() -> FastAPI:
    application = create_app()

    class Body(BaseModel):
        # Tagged like the Go struct it stands in for, so the probe exercises the wording
        # that actually goes on the wire.
        title: Annotated[str, GoValid("required,runelength(1|250)")] = Field(
            default="", validate_default=True
        )

    class UntaggedBody(BaseModel):
        """A write schema whose owner has not added its Go tag yet."""

        title: str

    @application.post("/api/v1/_probe")
    def probe(body: Body) -> dict[str, Any]:
        return {"title": body.title}

    @application.post("/api/v1/_probe_untagged")
    def probe_untagged(body: UntaggedBody) -> dict[str, Any]:
        return {"title": body.title}

    @application.get("/api/v1/_missing-project")
    def missing_project() -> dict[str, Any]:
        raise CaltonError.from_name("models.ErrProjectDoesNotExist")

    @application.get("/api/v1/_boom")
    def boom() -> dict[str, Any]:
        raise RuntimeError("unhandled")

    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_malformed_json_gets_the_v1_bind_error_not_fastapis_detail(client: TestClient) -> None:
    """The assertion that proves the wiring: 400 + code 2004, not 422 + detail."""
    resp = client.post(
        "/api/v1/_probe",
        content=b"{not json",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400
    assert resp.json() == {"code": 2004, "message": INVALID_MODEL_MESSAGE}
    assert "detail" not in resp.json()


def test_a_validation_failure_gets_the_412_shape(client: TestClient) -> None:
    """``invalid_fields`` carries govalidator's wording, not just the field name.

    Upstream sends ``"title: non zero value required"`` — the field, a colon and the rule
    that failed — and the frontend draws its field-level error from it. Calton used to
    send the bare name for every resource, which is why the ``body_exact`` parity cases
    were red across the board.
    """
    resp = client.post("/api/v1/_probe", json={})
    assert resp.status_code == 412
    assert resp.json() == {
        "code": 2002,
        "message": "Invalid Data",
        "invalid_fields": ["title: non zero value required"],
    }


def test_an_untagged_field_still_reports_only_its_name(client: TestClient) -> None:
    """⚠️ **A recorded shortfall, not a design.**

    The wording comes from the field's ``GoValid`` tag. A write schema whose owner has
    not added one yet degrades to the old bare-name form rather than inventing a message
    — inventing one would put text on the wire that upstream never sends, which is worse
    than an incomplete one. This is pinned so the remaining schemas are visible as work
    rather than as a mystery when their parity cases stay red.
    """
    resp = client.post("/api/v1/_probe_untagged", json={})

    assert resp.status_code == 412
    assert resp.json()["invalid_fields"] == ["title"]


def test_a_domain_error_renders_code_and_message(client: TestClient) -> None:
    resp = client.get("/api/v1/_missing-project")
    assert resp.status_code == 404
    assert resp.json() == {"code": 3001, "message": "This project does not exist."}


def test_an_unhandled_exception_is_json_not_plain_text(client: TestClient) -> None:
    resp = client.get("/api/v1/_boom")
    assert resp.status_code == 500
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json() == {"message": "Internal Server Error"}


def test_an_unknown_api_path_is_a_v1_404_not_fastapis_detail(client: TestClient) -> None:
    """The SPA fallback must not swallow API 404s, and the 404 must be v1-shaped."""
    resp = client.get("/api/v1/_nope")
    assert resp.status_code == 404
    assert "detail" not in resp.json()


def test_health_still_works(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"


# --- endpoints must be reachable through the real app, not just their router ---


def test_info_is_reachable_through_create_app(client: TestClient) -> None:
    """A router that exists but is never included is a 404 in production while its
    own unit tests stay green — the same failure as register_exception_handlers.
    Delivery is not wiring."""
    resp = client.get("/api/v1/info")
    assert resp.status_code == 200
    assert resp.json()["concurrent_writes"] is False


def test_routes_is_reachable_through_create_app(client: TestClient) -> None:
    """Reachable, but no longer anonymous.

    ``/routes`` requires a caller since T15: serving it openly publishes the whole
    API-token permission vocabulary, and the reference server answers 401. The
    assertion here is that the route is *mounted* — a 404 would mean it was never
    wired, which is what this test exists to catch — so 401 is the pass condition
    and 404 is the failure. See test_api_tokens.py::TestWiring for the auth side.
    """
    resp = client.get("/api/v1/routes")
    assert resp.status_code == 401
    assert resp.status_code != 404
    # A 401 body is the v1 error shape, not the routes table — the table's seeded
    # contents are asserted at the registry in test_route_registry.py, and the auth
    # side in test_api_tokens.py::TestWiring.
    assert resp.json()["code"] == 11


def test_a_cyclic_hierarchy_becomes_a_plain_500(client: TestClient, app: FastAPI) -> None:
    """Corrupt data, not a business error: standard fallback body, no error code.

    Not a 403 — a denial here would be indistinguishable from a legitimate one,
    so data corruption would be diagnosed as a permissions problem. The project id
    and depth go to the log only; putting them in the body would leak hierarchy
    structure to a caller who may not be entitled to see it.
    """
    from calton.permissions.project import CyclicHierarchyError

    @app.get("/api/v1/_cyclic")
    def cyclic() -> dict[str, Any]:
        raise CyclicHierarchyError(project_id=42, depth=1000)

    resp = client.get("/api/v1/_cyclic")
    assert resp.status_code == 500
    assert resp.json() == {"message": "Internal Server Error"}
    assert "code" not in resp.json()
    assert "42" not in resp.text


def test_the_cyclic_error_is_logged_with_its_diagnosis(
    client: TestClient, app: FastAPI, caplog: Any
) -> None:
    import logging

    from calton.permissions.project import CyclicHierarchyError

    @app.get("/api/v1/_cyclic_logged")
    def cyclic() -> dict[str, Any]:
        raise CyclicHierarchyError(project_id=99, depth=1000)

    with caplog.at_level(logging.ERROR, logger="calton.errors"):
        client.get("/api/v1/_cyclic_logged")

    assert any("99" in record.getMessage() for record in caplog.records)


def test_cache_control_is_absent_on_an_unrouted_path(client: TestClient) -> None:
    """Go sets no-store on routed /api/v1 responses but not on an unrouted 404 —
    verified against the running server. The middleware keys off whether a route
    matched, so this stays header-free."""
    resp = client.get("/api/v1/definitely-not-a-route")
    assert resp.status_code == 404
    assert "cache-control" not in resp.headers


def test_cache_control_is_present_on_a_routed_error(client: TestClient) -> None:
    """It is group middleware, so it applies to error responses too — Go sends it
    on /api/v1/tasks' 401."""
    resp = client.post("/api/v1/_probe", json={})
    assert resp.status_code == 412
    assert resp.headers["cache-control"] == "no-store"


def test_cache_control_is_absent_on_a_405(client: TestClient) -> None:
    """Go omits it when the path matched but the method did not — measured on
    HEAD /api/v1/info, which Echo answers 405 without the header. Starlette still
    fills scope["route"] there, so "a route matched" is not a sufficient test."""
    resp = client.request("HEAD", "/api/v1/info")
    assert resp.status_code == 405
    assert "cache-control" not in resp.headers


def test_a_trailing_slash_is_404_not_a_redirect(client: TestClient) -> None:
    """Echo 404s on a trailing slash; Starlette would 307 to the canonical path.

    Measured on the reference server: GET /api/v1/info/ -> 404. Left as a
    redirect, a client that does not follow redirects sees a different outcome
    entirely, and one that does turns a POST into a second request.
    """
    for path in ("/api/v1/info/", "/api/v1/routes/"):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code == 404, path
