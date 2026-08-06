"""T25 — proof that the label endpoints are actually reachable on the real app.

Separate from the behaviour tests on purpose. Every one of those passes against a router
that was built and never mounted, and against a route that was mounted but never given a
permission key — which are two failures this project has already shipped three times
between them. "The module is finished" and "you can call it" are different claims and
need different tests.

Two rules are load-bearing here and are stated where they are used:

* route membership is read from ``app.openapi()["paths"]``, never from ``app.routes``
* the permission key is checked through ``registry.lookup``, on the **route template**
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event

from calton.api.v1 import labels as labels_api
from calton.core.route_registry import RouteRegistry
from calton.core.route_registry import registry as route_registry

#: Every route T25 is responsible for, as (method, path template).
EXPECTED_ROUTES = [
    ("get", "/api/v1/labels"),
    ("put", "/api/v1/labels"),
    ("get", "/api/v1/labels/{label}"),
    ("post", "/api/v1/labels/{label}"),
    # No PATCH: upstream answers 405 here (measured). test_tasks_api pins that.
    ("delete", "/api/v1/labels/{label}"),
    ("get", "/api/v1/tasks/{task}/labels"),
    ("put", "/api/v1/tasks/{task}/labels"),
    ("delete", "/api/v1/tasks/{task}/labels/{label}"),
    ("post", "/api/v1/tasks/{task}/labels/bulk"),
]


@pytest.mark.parametrize(("method", "path"), EXPECTED_ROUTES)
def test_the_route_is_registered_on_the_real_app(app: FastAPI, method: str, path: str) -> None:
    """Read from the OpenAPI document, which is the contract layer's own view.

    ⛔ Not ``[r.path for r in app.routes]``: routers merged by ``include_router`` appear
    there as ``_IncludedRouter`` objects with no ``.path`` attribute, so that scan reports
    nothing and passes forever. A route-registration check that cannot fail is the exact
    thing this file exists to prevent, and this project shipped one.
    """
    paths = app.openapi()["paths"]
    assert path in paths, f"{path} is not mounted; sorted paths: {sorted(paths)}"
    assert method in paths[path], f"{path} has no {method.upper()}; has {sorted(paths[path])}"


def test_removing_the_mount_makes_this_file_fail(app: FastAPI) -> None:
    """The canary for the check above — practice #21.

    An app built without the label routers must be missing them. Without this, a change
    that made ``app.openapi()["paths"]`` return something unexpectedly permissive would
    leave the parametrised test green and nobody would know the difference.
    """
    bare = FastAPI()
    paths = bare.openapi()["paths"]
    assert not any(path in paths for _method, path in EXPECTED_ROUTES)


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("PUT", "/api/v1/labels", ("labels", "create")),
        ("POST", "/api/v1/labels/{label}", ("labels", "update")),
        ("PATCH", "/api/v1/labels/{label}", ("labels", "update")),
        ("DELETE", "/api/v1/labels/{label}", ("labels", "delete")),
        ("GET", "/api/v1/labels/{label}", ("labels", "read_one")),
        ("GET", "/api/v1/labels", ("labels", "read_all")),
        ("GET", "/api/v1/tasks/{task}/labels", ("tasks_labels", "read_all")),
        ("PUT", "/api/v1/tasks/{task}/labels", ("tasks_labels", "create")),
        ("DELETE", "/api/v1/tasks/{task}/labels/{label}", ("tasks_labels", "delete")),
        ("POST", "/api/v1/tasks/{task}/labels/bulk", ("tasks_labels", "update_bulk")),
    ],
)
def test_each_route_resolves_to_a_permission_key(
    method: str, path: str, expected: tuple[str, str]
) -> None:
    """Mounting a route and granting a token access to it are two separate actions.

    Skipping the second breaks nothing that a JWT-authenticated test would notice: every
    API-token request to the route 403s while everything else keeps working, which reads
    like a permissions bug in the resource rather than a missing registration. That has
    already cost this project one debugging session.

    Note ``lookup`` takes the **route template**. Handing it a concrete URL like
    ``/api/v1/labels/5`` yields the group ``labels_5``, resolves to None, and fails closed
    — correct, but with a symptom that points nowhere near the cause.
    """
    assert route_registry.lookup(method, path) == expected


def test_the_app_registers_every_label_route_in_the_registry(app: FastAPI) -> None:
    """The registry the app actually built, not a fresh one.

    ``lookup`` above only proves the naming rules are right; it would answer the same on
    an app that registered nothing. This asserts the entries are present.
    """
    entries = app.state.settings and route_registry.routes
    assert entries["labels"].keys() >= {"create", "update", "delete", "read_one", "read_all"}
    assert entries["tasks_labels"].keys() >= {"create", "delete", "read_all", "update_bulk"}


def test_a_registry_that_never_saw_the_routes_does_not_have_them() -> None:
    """Canary for the assertion above: a fresh registry must lack these groups.

    ``route_registry`` is a module-level singleton, so an assertion against it passes as
    soon as *any* app in the test session has been built — including one built by another
    module. This proves the entries come from registration rather than from the object
    existing.
    """
    fresh = RouteRegistry()
    assert "labels" not in fresh.routes
    assert "tasks_labels" not in fresh.routes


def test_the_declared_route_list_matches_what_is_mounted(app: FastAPI) -> None:
    """``REGISTERED_ROUTES`` is what main.py feeds the registry, so a route added to the
    module and forgotten there would be reachable but unauthorised for API tokens."""
    paths = app.openapi()["paths"]
    for method, path in labels_api.REGISTERED_ROUTES:
        assert method.lower() in paths.get(path, {}), f"{method} {path} declared but not mounted"


def test_an_unauthenticated_request_is_401_rather_than_500(app: FastAPI) -> None:
    """Every route here refuses an anonymous caller with the middleware's 401.

    The CRUDRouter pipeline runs its policy before anything else and has no authentication
    step, so ``request.state.auth`` is None until T14/T15 land. The natural spelling of
    "get the user id" then raises ``TypeError`` and the endpoint answers 500 — which is
    what schemathesis found on all six ``/labels`` routes. 401 is also the answer that
    stays correct once the middleware exists.
    """
    anonymous = TestClient(app, raise_server_exceptions=False)
    for method, path in EXPECTED_ROUTES:
        url = path.replace("{label}", "950").replace("{task}", "950") if "{" in path else path
        response = anonymous.request(method.upper(), url, json={})
        assert response.status_code == 401, f"{method.upper()} {url} -> {response.status_code}"
        assert response.json() == {
            "code": 11,
            "message": "missing, malformed, expired or otherwise invalid token provided",
        }


def test_an_anonymous_request_never_reaches_the_database(app: FastAPI, engine: Any) -> None:
    """The subject is resolved before any lookup, not after.

    Ordering that the status code alone cannot see: with the checks the other way round
    the request still ends in 401, because the id resolution fails either way — so
    ``test_an_unauthenticated_request_is_401_rather_than_500`` above passes on both
    orderings and says nothing about this. What differs is that the wrong order issues a
    ``SELECT`` on behalf of nobody, and on a database where that query itself fails (an
    unmigrated one, which is what schemathesis fuzzes) the endpoint answers 500 instead.

    Counting statements is therefore the only assertion that distinguishes the two.
    """
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _record(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        statements.append(statement)

    try:
        anonymous = TestClient(app, raise_server_exceptions=False)
        for method, path in EXPECTED_ROUTES:
            url = path.replace("{label}", "950").replace("{task}", "950") if "{" in path else path
            assert anonymous.request(method.upper(), url, json={}).status_code == 401
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    assert statements == [], (
        f"anonymous requests issued {len(statements)} queries: {statements[:3]}"
    )
