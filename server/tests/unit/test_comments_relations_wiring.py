"""T30 / T31 — proof that the comment and relation endpoints are reachable on the real app.

Separate from the behaviour tests deliberately. Every case in ``test_comments.py`` and
``test_relations.py`` passes against a router that was built and never mounted, or mounted
and never given a permission key — the project has shipped both, five times between them,
each with a green unit suite. "The module is finished" and "you can call it" are different
claims and need different tests.

Two rules are load-bearing and are stated where they are used:

* route membership is read from ``app.openapi()["paths"]``, never from ``app.routes``
* the permission key is checked through ``registry.lookup``, on the **route template**
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from calton.api.v1 import comments as comments_api
from calton.api.v1 import relations as relations_api
from calton.core.route_registry import RouteRegistry
from calton.core.route_registry import registry as route_registry
from calton.main import create_app

#: Every route T30 and T31 are responsible for, as (method, path template).
EXPECTED_ROUTES = [
    ("get", "/api/v1/tasks/{task}/comments"),
    ("put", "/api/v1/tasks/{task}/comments"),
    ("get", "/api/v1/tasks/{task}/comments/{commentid}"),
    ("post", "/api/v1/tasks/{task}/comments/{commentid}"),
    ("delete", "/api/v1/tasks/{task}/comments/{commentid}"),
    ("put", "/api/v1/tasks/{task}/relations"),
    ("delete", "/api/v1/tasks/{task}/relations/{relationKind}/{otherTask}"),
]

UNAUTHORIZED = {
    "code": 11,
    "message": "missing, malformed, expired or otherwise invalid token provided",
}


def _concrete(path: str) -> str:
    return (
        path.replace("{task}", "950")
        .replace("{commentid}", "950")
        .replace("{relationKind}", "subtask")
        .replace("{otherTask}", "951")
    )


@pytest.mark.parametrize(("method", "path"), EXPECTED_ROUTES)
def test_the_route_is_mounted_on_the_real_app(app: FastAPI, method: str, path: str) -> None:
    """Read from the OpenAPI document, which is the contract layer's own view.

    ⛔ Not ``[r.path for r in app.routes]``: routers merged by ``include_router`` appear
    there as ``_IncludedRouter`` objects with no ``.path``, so that scan reports nothing
    and passes forever.
    """
    paths = app.openapi()["paths"]
    assert path in paths, f"{path} is not mounted; sorted paths: {sorted(paths)}"
    assert method in paths[path], f"{path} has no {method.upper()}; has {sorted(paths[path])}"


def test_a_bare_app_has_none_of_them() -> None:
    """Canary for the check above (practice #21): an app that mounted nothing must be
    missing all seven, or the assertion is measuring the wrong thing."""
    paths = FastAPI().openapi()["paths"]
    assert not any(path in paths for _method, path in EXPECTED_ROUTES)


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/api/v1/tasks/{task}/comments", ("tasks_comments", "read_all")),
        ("PUT", "/api/v1/tasks/{task}/comments", ("tasks_comments", "create")),
        ("GET", "/api/v1/tasks/{task}/comments/{commentid}", ("tasks_comments", "read_one")),
        ("POST", "/api/v1/tasks/{task}/comments/{commentid}", ("tasks_comments", "update")),
        ("DELETE", "/api/v1/tasks/{task}/comments/{commentid}", ("tasks_comments", "delete")),
        ("PUT", "/api/v1/tasks/{task}/relations", ("tasks_relations", "create")),
        (
            "DELETE",
            "/api/v1/tasks/{task}/relations/{relationKind}/{otherTask}",
            ("tasks_relations", "delete"),
        ),
    ],
)
def test_each_route_resolves_to_the_permission_key_upstream_publishes(
    method: str, path: str, expected: tuple[str, str]
) -> None:
    """The group names are not ours to choose — they come from ``GET /api/v1/routes``,
    which is diffed against the reference server and which the frontend reads to build the
    API-token permission picker. Verified against a running Go server: ``tasks_comments``
    with five actions and ``tasks_relations`` with two.

    Mounting a route and granting a token access to it are separate actions, and skipping
    the second is invisible to any JWT-authenticated test: every API-token request 403s
    while everything else works, which reads like a permissions bug in the resource.
    """
    assert route_registry.lookup(method, path) == expected


def test_the_app_put_both_groups_in_the_registry(app: FastAPI) -> None:
    """The registry the app actually built. ``lookup`` above only proves the naming rules;
    it answers the same on an app that registered nothing."""
    assert app.state.settings is not None
    assert route_registry.routes["tasks_comments"].keys() == {
        "read_all",
        "read_one",
        "create",
        "update",
        "delete",
    }
    assert route_registry.routes["tasks_relations"].keys() == {"create", "delete"}


def test_a_registry_that_never_saw_the_routes_does_not_have_them() -> None:
    """Canary for the assertion above. ``route_registry`` is a module-level singleton, so
    an assertion against it passes as soon as *any* app in the session has been built —
    including one built by another test module."""
    fresh = RouteRegistry()
    assert "tasks_comments" not in fresh.routes
    assert "tasks_relations" not in fresh.routes


def test_the_declared_route_list_matches_what_is_mounted(app: FastAPI) -> None:
    """``REGISTERED_ROUTES`` is what ``main.py`` feeds the registry, so a route added to
    the module and forgotten there is reachable but unauthorised for every API token."""
    paths = app.openapi()["paths"]
    for method, path in (*comments_api.REGISTERED_ROUTES, *relations_api.REGISTERED_ROUTES):
        assert method.lower() in paths.get(path, {}), f"{method} {path} declared but not mounted"


@pytest.fixture
def production_app(engine: Engine, sessions: sessionmaker[Session]) -> FastAPI:
    """``create_app`` with **no** dependency override and no stub middleware.

    ⚠️ The shared ``app`` fixture replaces ``get_auth_subject`` with ``lambda: None`` so the
    behaviour tests can act as a user through a header. That override is exactly what this
    test must not have: with it, an anonymous request never meets the auth dependency at
    all, FastAPI validates the body first, and ``PUT .../comments`` with ``{}`` answers
    **412** — a failure that looks like the auth ordering being wrong when in fact only
    the scaffold's was. (Measured on the reference server: every route below is 401 to an
    anonymous caller, ahead of body validation *and* ahead of path-parameter parsing.)

    The label wiring file gets away with the shared fixture only because ``LabelWrite`` has
    no required field, so its empty body happens to validate. That is luck, not design.
    """
    application = create_app(engine=engine)
    application.state.session_factory = sessions
    return application


def test_every_route_refuses_an_anonymous_caller(production_app: FastAPI) -> None:
    """The one assertion that catches a router mounted without
    ``dependencies=[Depends(get_auth_subject)]``.

    ⚠️ **Each route is asked twice, with a body and without**, and the second call is the
    one that carries the assertion. Without the mount's dependency nothing populates
    ``request.state.auth``, and ``auth_user_id`` then raises the *same* 401 — so a request
    whose body parses reaches the identical terminal state either way and cannot tell the
    two apart (practice #20: another path produces the same result). A **missing** body is
    a validation error, which FastAPI answers *before* the handler runs but *after* a
    router dependency, so it is 401 with the dependency and 412 without it. That is the
    only input on these seven routes that distinguishes them; verified by removing the
    dependency and watching this go red.
    """
    anonymous = TestClient(production_app, raise_server_exceptions=False)

    for method, path in EXPECTED_ROUTES:
        url = _concrete(path)

        with_body = anonymous.request(method.upper(), url, json={})
        assert with_body.status_code == 401, f"{method.upper()} {url} -> {with_body.status_code}"
        assert with_body.json() == UNAUTHORIZED

        # The discriminating call. See the note above before "simplifying" it away.
        without_body = anonymous.request(method.upper(), url)
        assert without_body.status_code == 401, (
            f"{method.upper()} {url} with no body -> {without_body.status_code}; "
            "a validation error answering ahead of authentication means this router was "
            "mounted without dependencies=[Depends(get_auth_subject)]"
        )
        assert without_body.json() == UNAUTHORIZED


def test_authentication_answers_before_body_validation_and_path_parsing(
    production_app: FastAPI,
) -> None:
    """401 outranks both the 412 an invalid body earns and the 400 a bad id earns.

    Measured on the reference server; no corpus case covers it. The ordering matters
    because the alternative leaks: a 412 tells an anonymous caller their body was at least
    *parsed*, and a 400 on ``/tasks/abc/comments`` confirms the route exists. Both are
    what FastAPI does by default when the auth dependency is missing from the mount, which
    is why this is pinned rather than assumed.
    """
    anonymous = TestClient(production_app, raise_server_exceptions=False)

    invalid_body = anonymous.put("/api/v1/tasks/950/comments", json={"comment": ""})
    bad_path_param = anonymous.get("/api/v1/tasks/abc/comments")

    assert invalid_body.status_code == 401
    assert invalid_body.json() == UNAUTHORIZED
    assert bad_path_param.status_code == 401
    assert bad_path_param.json() == UNAUTHORIZED
