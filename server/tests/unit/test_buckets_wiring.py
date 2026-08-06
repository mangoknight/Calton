"""T28 — proof that the bucket endpoints are actually reachable on the real app.

Separate from the parity cases on purpose. Every behaviour test passes against a router
that was built and never mounted, and against a route mounted without a permission key —
two failures this project has shipped several times between them. "The module is
finished" and "you can call it" are different claims.

The permission keys below are **not** derived from the paths by eye. They were read off
``GET /api/v1/routes`` on the running Go reference server, which is the authoritative
table, and the harness README records why guessing is a bad idea here: the names are
irregular. ``GET .../views/{view}/tasks`` is its own top-level group
(``projects_views_tasks.read_all``) while everything under ``.../buckets`` hangs off
``projects`` with hand-written action names — ``views_buckets``, ``views_buckets_put``,
``views_buckets_post``, ``views_buckets_delete``, ``views_buckets_tasks``. They are not
the usual create/read_all/update/delete quartet, and the two that look most alike
(``projects.views_buckets_tasks``, the POST that drops a task in a bucket, versus
``projects_views_tasks``, the board read) belong to different groups entirely. Getting one
wrong takes down exactly one route for API-token callers, leaves JWT callers untouched,
and reads like anything except a registration problem.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from calton.api.v1 import buckets as buckets_api
from calton.core.route_registry import RouteRegistry
from calton.core.route_registry import registry as route_registry

#: Every route T28 owns, as (method, path template).
EXPECTED_ROUTES = [
    ("get", "/api/v1/projects/{project}/views/{view}/buckets"),
    ("put", "/api/v1/projects/{project}/views/{view}/buckets"),
    ("post", "/api/v1/projects/{project}/views/{view}/buckets/{bucket}"),
    ("delete", "/api/v1/projects/{project}/views/{view}/buckets/{bucket}"),
    ("post", "/api/v1/projects/{project}/views/{view}/buckets/{bucket}/tasks"),
]

#: Measured on the reference server, not inferred. See the module docstring.
EXPECTED_KEYS = [
    ("GET", "/api/v1/projects/{project}/views/{view}/buckets", ("projects", "views_buckets")),
    ("PUT", "/api/v1/projects/{project}/views/{view}/buckets", ("projects", "views_buckets_put")),
    (
        "POST",
        "/api/v1/projects/{project}/views/{view}/buckets/{bucket}",
        ("projects", "views_buckets_post"),
    ),
    (
        "DELETE",
        "/api/v1/projects/{project}/views/{view}/buckets/{bucket}",
        ("projects", "views_buckets_delete"),
    ),
    (
        "POST",
        "/api/v1/projects/{project}/views/{view}/buckets/{bucket}/tasks",
        ("projects", "views_buckets_tasks"),
    ),
]


@pytest.mark.parametrize(("method", "path"), EXPECTED_ROUTES)
def test_the_route_is_registered_on_the_real_app(app: FastAPI, method: str, path: str) -> None:
    """Read from the OpenAPI document — the contract layer's own view of what exists.

    ⛔ Not ``[r.path for r in app.routes]``: routers merged by ``include_router`` appear
    there as ``_IncludedRouter`` objects with no ``.path``, so that scan finds nothing and
    passes forever. This project shipped exactly that check once.
    """
    paths = app.openapi()["paths"]
    assert path in paths, f"{path} is not mounted; sorted paths: {sorted(paths)}"
    assert method in paths[path], f"{path} has no {method.upper()}; has {sorted(paths[path])}"


def test_a_bare_app_lacks_these_routes() -> None:
    """Canary for the check above (practice #21).

    Without it, a change that made ``openapi()["paths"]`` unexpectedly permissive would
    leave the parametrised test green and nothing would say so.
    """
    paths = FastAPI().openapi()["paths"]
    assert not any(path in paths for _method, path in EXPECTED_ROUTES)


@pytest.mark.parametrize(("method", "path", "expected"), EXPECTED_KEYS)
def test_each_route_resolves_to_the_permission_key_upstream_uses(
    method: str, path: str, expected: tuple[str, str]
) -> None:
    """Mounting a route and granting a token access to it are two separate actions.

    Skipping the second breaks nothing a JWT-authenticated test would notice: every
    API-token request 403s while everything else keeps working.

    ``lookup`` takes the **route template**. A concrete URL such as
    ``/api/v1/projects/950/views/953/buckets`` yields the group ``projects_950`` and
    resolves to None — fails closed, with a symptom pointing nowhere near the cause.
    """
    assert route_registry.lookup(method, path) == expected


def test_the_app_registered_the_bucket_actions(app: FastAPI) -> None:
    """The registry the app actually built, not a freshly derived answer.

    ``lookup`` above only proves the naming rules produce the right strings; it answers
    identically on an app that registered nothing.
    """
    assert app.state.settings is not None
    actions = route_registry.routes["projects"].keys()
    assert actions >= {
        "views_buckets",
        "views_buckets_put",
        "views_buckets_post",
        "views_buckets_delete",
        "views_buckets_tasks",
    }


def test_a_registry_that_never_saw_the_routes_does_not_have_them() -> None:
    """Canary for the assertion above.

    ``route_registry`` is a module-level singleton, so an assertion against it starts
    passing as soon as *any* app in the session has been built — including one built by
    another test module. This proves the entries come from registration.
    """
    fresh = RouteRegistry()
    assert "views_buckets" not in fresh.routes.get("projects", {})


def test_the_declared_route_list_matches_what_is_mounted(app: FastAPI) -> None:
    """``REGISTERED_ROUTES`` is what ``main.py`` feeds the registry, so a route added to
    the module and forgotten there would be reachable but unauthorised for API tokens."""
    paths = app.openapi()["paths"]
    for method, path in buckets_api.REGISTERED_ROUTES:
        assert method.lower() in paths.get(path, {}), f"{method} {path} declared but not mounted"
    assert len(buckets_api.REGISTERED_ROUTES) == len(EXPECTED_ROUTES)


@pytest.mark.parametrize(("method", "path"), EXPECTED_ROUTES)
def test_an_unauthenticated_request_is_401(method: str, path: str) -> None:
    """Anonymous callers get 401 on every one of these — measured to match Go.

    This catches a router mounted without ``dependencies=[Depends(get_auth_subject)]``:
    the handlers read the subject off ``request.state.auth``, so without it they either
    500 or answer the request. The shape that failure took the last time it happened in
    this project was a **412** on the body-carrying routes — validation replying before
    authentication, which also leaks "your title is invalid" to callers who are not
    logged in. Reference server, anonymous, empty body: 401 on all five, and on
    ``PUT /projects`` too.

    ⚠️ Deliberately **not** using the ``app`` fixture, and this is not a style choice.
    That fixture does ``dependency_overrides[get_auth_subject] = lambda: None`` so the
    other tests can act as a user via a header — which switches off the very thing this
    test exists to check. Written against the fixture, it reported 412 on the two routes
    that take a body and looked exactly like the auth-dependency bug described above. The
    implementation was correct the whole time; the scaffold had removed the subject.
    (Practice #20's third face: a stand-in that does not reproduce the system under test
    produces a *false red*, and a false red gets "fixed" by breaking working code.)
    """
    from fastapi.testclient import TestClient

    from calton.main import create_app

    concrete = path.replace("{project}", "950").replace("{view}", "953").replace("{bucket}", "950")
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        response = client.request(method.upper(), concrete, json={})

    assert response.status_code == 401, (
        f"{method.upper()} {concrete} answered {response.status_code}, not 401 — "
        "the auth dependency is probably missing from the mount"
    )
    assert response.json()["code"] == 11
