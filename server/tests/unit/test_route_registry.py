"""T08 — route_registry and GET /routes.

Group names are what API tokens are granted against, so a wrong one means every
MCP call for that resource returns 403 with nothing to explain it. The cases
below are taken from the paths Phase 1 actually registers.
"""

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from calton.core.route_registry import (
    CRUD_RESOURCES,
    RouteRegistry,
    ends_with_param,
    group_name_of,
    is_standard_crud_route,
)


@pytest.fixture
def empty() -> RouteRegistry:
    return RouteRegistry(seed=False)


# --- group names -------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/api/v1/labels", "labels"),
        ("/api/v1/labels/:label", "labels"),
        ("/api/v1/projects", "projects"),
        # All non-parameter segments join, so this is NOT "projects".
        ("/api/v1/projects/:project/views", "projects_views"),
        ("/api/v1/projects/:project/views/:view/buckets", "projects_views_buckets"),
        ("/api/v1/tasks/:task/comments", "tasks_comments"),
        ("/api/v1/tasks/:task/attachments/:attachment", "tasks_attachments"),
        ("/api/v1/user/settings/email", "user_settings_email"),
    ],
)
def test_group_names(path: str, expected: str) -> None:
    assert group_name_of(path)[0] == expected


def test_a_first_segment_rule_would_be_wrong() -> None:
    """Guards against "simplifying" the join back to the first segment."""
    group, parts = group_name_of("/api/v1/projects/:project/views")
    assert group == "projects_views"
    assert group != parts[0]


@pytest.mark.parametrize(
    ("path", "expected_group", "expected_parts"),
    [
        ("/api/v1/projects/:project/tasks", "tasks", ["tasks"]),
        ("/api/v1/tasks/all", "tasks", ["tasks"]),
        ("/api/v1/projects/:project/tasks/bulk", "tasks_bulk", ["tasks_bulk"]),
    ],
)
def test_the_three_special_cased_group_names(
    path: str, expected_group: str, expected_parts: list[str]
) -> None:
    assert group_name_of(path) == (expected_group, expected_parts)


def test_tasks_all_already_groups_as_tasks_upstream() -> None:
    """So the /tasks/all alias (T34) needs no extra registry rule."""
    assert group_name_of("/api/v1/tasks/all") == group_name_of("/api/v1/tasks")


def test_hyphens_become_underscores() -> None:
    assert group_name_of("/api/v2/time-entries")[0] == "time_entries"


def test_braces_are_treated_as_parameters_like_colons() -> None:
    """FastAPI writes {param}; Echo writes :param. Both must drop out."""
    assert group_name_of("/api/v1/labels/{label}") == group_name_of("/api/v1/labels/:label")
    assert group_name_of("/api/v1/projects/{project}/views")[0] == "projects_views"


def test_the_api_version_prefix_is_stripped() -> None:
    assert group_name_of("/api/v1/labels")[0] == group_name_of("/api/v2/labels")[0]


# --- actions -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/api/v1/labels", ("labels", "read_all")),
        ("GET", "/api/v1/labels/:label", ("labels", "read_one")),
        ("PUT", "/api/v1/labels", ("labels", "create")),
        ("POST", "/api/v1/labels/:label", ("labels", "update")),
        ("DELETE", "/api/v1/labels/:label", ("labels", "delete")),
    ],
)
def test_lookup_maps_requests_to_group_and_action(
    method: str, path: str, expected: tuple[str, str]
) -> None:
    assert RouteRegistry().lookup(method, path) == expected


def test_put_on_the_collection_is_create_not_update() -> None:
    """The v1 inversion again. Reversed here means every create is refused."""
    assert RouteRegistry().lookup("PUT", "/api/v1/labels") == ("labels", "create")


def test_ends_with_param_decides_read_one_versus_read_all() -> None:
    assert ends_with_param("/api/v1/labels/:label")
    assert ends_with_param("/api/v1/labels/{label}")
    assert not ends_with_param("/api/v1/labels")


# --- registration ------------------------------------------------------------


def test_a_crud_route_files_under_its_own_group(empty: RouteRegistry) -> None:
    empty.register("GET", "/api/v1/labels")
    empty.register("PUT", "/api/v1/labels")
    assert set(empty.routes["labels"]) == {"read_all", "create"}
    assert empty.routes["labels"]["create"].method == "PUT"


def test_a_non_crud_route_files_under_its_parent_with_a_detail_subkey(empty: RouteRegistry) -> None:
    empty.register("POST", "/api/v1/projects/:project/background")
    assert "background" in empty.routes["projects"]


def test_a_single_segment_non_crud_route_files_under_other(empty: RouteRegistry) -> None:
    empty.register("GET", "/api/v1/info")
    assert "info" in empty.routes["other"]


def test_a_colliding_subkey_gets_the_method_appended(empty: RouteRegistry) -> None:
    empty.register("GET", "/api/v1/projects/:project/background")
    empty.register("DELETE", "/api/v1/projects/:project/background")
    assert set(empty.routes["projects"]) == {"background", "background_delete"}


def test_bulk_routes_file_under_the_parent_with_a_bulk_suffix(empty: RouteRegistry) -> None:
    empty.register("POST", "/api/v1/tasks/:task/labels/bulk")
    assert "update_bulk" in empty.routes["tasks_labels"]


def test_notifications_post_files_as_update_not_mark_all_as_read(empty: RouteRegistry) -> None:
    """api_routes.go:292-296 has a `mark_all_as_read` branch for POST
    /notifications, but it sits inside the `!isCRUD` arm and "notifications" is
    in crudResources — so it never runs. POST files as the ordinary `update`,
    same as the dead `page < 0` branch in read_all.go. Copy the reachable
    behaviour, and pin it so nobody "restores" the name from reading the source.
    """
    empty.register("POST", "/api/v1/notifications")
    assert set(empty.routes["notifications"]) == {"update"}
    assert "mark_all_as_read" not in empty.routes["notifications"]


def test_attachments_get_create_and_read_one_despite_custom_handlers(empty: RouteRegistry) -> None:
    empty.register("PUT", "/api/v1/tasks/:task/attachments")
    empty.register("GET", "/api/v1/tasks/:task/attachments/:attachment")
    assert "create" in empty.routes["tasks_attachments"]
    assert "read_one" in empty.routes["tasks_attachments"]


# --- exclusions --------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/tokens",
        "/api/v1/token/test",
        "/api/v1/subscriptions/:entity/:id",
        "/api/v1/user/settings/token/caldav",
    ],
)
def test_excluded_groups_never_enter_the_registry(empty: RouteRegistry, path: str) -> None:
    """T15 refuses any route missing from the registry, which is what keeps a
    leaked read-only token from minting more tokens or enumerating users."""
    empty.register("GET", path)
    empty.register("PUT", path)
    assert empty.routes == {}


def test_a_concrete_url_does_not_resolve() -> None:
    """lookup() takes route templates, not URLs — the load-bearing wiring
    assumption for T15, previously untested.

    A concrete URL makes the group "labels_5", which resolves to None and refuses
    the call. Fail-closed is right, but the symptom (every request 403s) points
    nowhere near the cause, and the obvious "fix" of loosening the match is how
    GHSA-v479 happened. T15 must pass request.scope["route"].path_format.
    """
    assert RouteRegistry().lookup("GET", "/api/v1/labels/5") is None
    assert group_name_of("/api/v1/labels/5")[0] == "labels_5"
    assert RouteRegistry().lookup("GET", "/api/v1/labels/{label}") == ("labels", "read_one")


def test_a_numeric_segment_is_never_silently_treated_as_a_parameter(empty: RouteRegistry) -> None:
    """Guards the tempting loosening: if lookup() started stripping numeric
    segments, the prefix-matching hole would be back."""
    empty.register("GET", "/api/v1/labels/{label}")
    assert empty.lookup("GET", "/api/v1/labels/5") is None


def test_tasks_and_tasks_all_share_one_permission_key() -> None:
    """Intentionally wider than Go, which authorises only the stored path. A
    token granted tasks.read_all must reach both, or calton-mcp@1.0.4 breaks."""
    registry = RouteRegistry()
    assert registry.lookup("GET", "/api/v1/tasks") == ("tasks", "read_all")
    assert registry.lookup("GET", "/api/v1/tasks/all") == ("tasks", "read_all")


def test_excluded_routes_do_not_resolve(empty: RouteRegistry) -> None:
    assert RouteRegistry().lookup("GET", "/api/v1/tokens") is None
    assert RouteRegistry().lookup("GET", "/api/v1/user/settings/token/caldav") is None


def test_routes_not_requiring_jwt_are_skipped(empty: RouteRegistry) -> None:
    empty.register("POST", "/api/v1/login", requires_jwt=False)
    assert empty.routes == {}


def test_an_unregistered_route_is_refused_by_default(empty: RouteRegistry) -> None:
    assert not empty.can("labels", "read_all")
    empty.register("GET", "/api/v1/labels")
    assert empty.can("labels", "read_all")
    assert not empty.can("labels", "delete")


# --- seeds and output --------------------------------------------------------


def test_caldav_and_feeds_are_seeded() -> None:
    routes = RouteRegistry().to_json()
    assert routes["caldav"]["access"] == {"path": "/dav/*", "method": "ANY"}
    assert routes["feeds"]["access"] == {"path": "/feeds/*", "method": "GET"}


def test_json_shape_is_group_action_path_method(empty: RouteRegistry) -> None:
    empty.register("GET", "/api/v1/labels")
    assert empty.to_json() == {"labels": {"read_all": {"path": "/api/v1/labels", "method": "GET"}}}


def test_json_is_sorted_so_the_byte_diff_against_calton_is_stable(empty: RouteRegistry) -> None:
    empty.register("PUT", "/api/v1/labels")
    empty.register("GET", "/api/v1/labels")
    empty.register("GET", "/api/v1/projects")
    output = empty.to_json()
    assert list(output) == sorted(output)
    assert list(output["labels"]) == ["create", "read_all"]


# --- the endpoint ------------------------------------------------------------


def test_routes_endpoint_serves_the_registry() -> None:
    """The body, with authentication stubbed out.

    ``/routes`` requires a caller since T15 — see
    ``test_routes_endpoint_refuses_anonymous_callers`` below — so this overrides
    the dependency rather than dropping it, which would let the endpoint go back
    to being anonymous without any test noticing.
    """
    from calton.api.v1.routes import build_router
    from calton.auth.deps import AuthSubject, get_auth_subject
    from calton.models.user import User

    registry = RouteRegistry(seed=False)
    registry.register("GET", "/api/v1/labels")

    app = FastAPI()
    app.include_router(build_router(registry), prefix="/api/v1")
    app.dependency_overrides[get_auth_subject] = lambda: AuthSubject(
        user=User(id=1, username="alice")
    )

    body = TestClient(app).get("/api/v1/routes").json()
    assert body == {"labels": {"read_all": {"path": "/api/v1/labels", "method": "GET"}}}


def test_routes_endpoint_refuses_anonymous_callers() -> None:
    """Serving this anonymously publishes the whole API-token permission table.

    It names every group and action a token can hold, which is both a map of the
    API surface and the exact vocabulary for phishing a grant. The reference
    server answers 401; Calton did not until this was wired.
    """
    from sqlalchemy import create_engine

    from calton.api.v1.routes import build_router
    from calton.core.errors import register_exception_handlers
    from calton.db.base import Base
    from calton.db.session import session_factory

    engine = create_engine("sqlite+pysqlite://")
    Base.metadata.create_all(engine)

    app = FastAPI()
    register_exception_handlers(app)
    # The auth dependency opens a session before it looks at the credential, so
    # the app needs a real factory for the refusal to be a 401 rather than an
    # AttributeError dressed up as a 500.
    app.state.session_factory = session_factory(engine)
    app.include_router(build_router(RouteRegistry(seed=False)), prefix="/api/v1")

    response = TestClient(app, raise_server_exceptions=False).get("/api/v1/routes")

    assert response.status_code == 401
    assert response.json()["code"] == 11


# --- consistency with the CRUDRouter ----------------------------------------


def test_a_crud_router_registers_all_five_actions(empty: RouteRegistry) -> None:
    from pydantic import BaseModel, ConfigDict

    from calton.core.crud_router import CRUDRouter
    from calton.core.policy import AllowAll

    class Schema(BaseModel):
        model_config = ConfigDict(strict=True)

        title: str = ""

    class Service:
        def create(self, session: Any, data: Any, auth: Any, **kw: Any) -> Any: ...
        def read_one(self, session: Any, auth: Any, **kw: Any) -> Any: ...
        def read_all(
            self, session: Any, auth: Any, search: str, page: int, per_page: int, **kw: Any
        ) -> Any: ...
        def update(self, session: Any, data: Any, auth: Any, **kw: Any) -> Any: ...
        def delete(self, session: Any, auth: Any, **kw: Any) -> None: ...

    crud: CRUDRouter[Any, Schema, Schema] = CRUDRouter(
        prefix="/labels",
        item_param="label",
        service=Service(),
        policy=AllowAll(),
        read_schema=Schema,
        write_schema=Schema,
    )
    empty.register_crud_router(crud)
    assert set(empty.routes["labels"]) == {"create", "read_one", "read_all", "update", "delete"}


def test_every_phase1_crud_group_is_a_known_crud_resource() -> None:
    """A group missing from CRUD_RESOURCES files under "other" and becomes
    ungrantable, so every resource Phase 1 serves must be listed."""
    for group in ("labels", "projects", "tasks", "filters", "tasks_comments", "projects_views"):
        assert group in CRUD_RESOURCES
        assert is_standard_crud_route(group, group.split("_"))


# --- collisions and the /routes serialisation --------------------------------
#
# `GET /routes` publishes one path per (group, action). Several routes can land
# on one key, and until this section existed the last one registered silently
# won — so the published path was a function of the order routers happen to be
# included in `create_app`. `tasks.read_all` was reporting
# `/api/v1/projects/:project/tasks` where upstream reports `/api/v1/tasks`
# for exactly that reason, and nothing failed.


def test_an_undeclared_collision_is_refused(empty: RouteRegistry) -> None:
    """Two routes on one key must be declared, not resolved by luck.

    This is the assertion the whole `_publish` indirection exists for: without
    it, the second registration overwrites the first and the only evidence is a
    path string in a response body nobody diffs by hand.
    """
    # Both land on ("tasks", "read_one"): the second's group `projects_tasks` is
    # renamed to `tasks` by GROUP_RENAMES, and both end in a parameter. This is a
    # plausible addition — a project-scoped task detail route — and exactly the
    # shape that used to overwrite the first entry without a word.
    empty.register("GET", "/api/v1/tasks/{task}")
    with pytest.raises(RuntimeError, match="two routes claim"):
        empty.register("GET", "/api/v1/projects/{project}/tasks/{task}")


def test_registering_the_same_route_twice_is_not_a_collision(empty: RouteRegistry) -> None:
    """Building two apps in one process must stay a no-op, not raise."""
    empty.register("GET", "/api/v1/labels")
    empty.register("GET", "/api/v1/labels")
    assert empty.routes["labels"]["read_all"].path == "/api/v1/labels"


def test_a_declared_collision_publishes_the_declared_path() -> None:
    """The three /tasks read paths collapse onto one key; upstream publishes the
    bare one. Measured against the reference server."""
    from calton.core.route_registry import registry
    from calton.main import create_app

    create_app()
    assert registry.to_json()["tasks"]["read_all"]["path"] == "/api/v1/tasks"


def test_every_declared_collision_still_collides() -> None:
    """A declaration that no longer describes reality has to go.

    Same rule as `known_differences.yaml` and `parity_baseline.txt`: an entry that
    can outlive its cause turns the register into a description of a past state of
    the code.
    """
    import collections

    from calton.core.route_registry import COLLISIONS, registry
    from calton.main import create_app

    create_app()
    claimed: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
    for (method, path), key in registry._registered.items():
        claimed[key].add(f"{method} {path}")

    for key, declared in COLLISIONS.items():
        assert len(claimed[key]) > 1, (
            f"{key} is declared as a collision ({declared.owner}) but only "
            f"{claimed[key]} claims it now. Delete the declaration."
        )


def test_every_echo_path_override_still_names_a_registered_route() -> None:
    """An override for a path nothing registers renames nothing, silently."""
    from calton.core.route_registry import ECHO_PATH_OVERRIDES, registry
    from calton.main import create_app

    create_app()
    registered = {path for _method, path in registry._registered}
    stale = sorted(set(ECHO_PATH_OVERRIDES) - registered)
    assert not stale, f"ECHO_PATH_OVERRIDES entries match no registered route: {stale}"


def test_the_echo_serialisation_uses_upstreams_parameter_names() -> None:
    """`/routes` and swagger disagree about parameter names, and both are upstream.

    The route templates follow swagger (`/tasks/{id}`, `/projects/{p}/views/{id}`)
    because the contract diff compares against it; `/routes` publishes Echo's
    (`:projecttask`, `:view`). Renaming the templates would only move the red
    from `routes.ok` to the contract suite.
    """
    from calton.core.route_registry import registry
    from calton.main import create_app

    create_app()
    published = registry.to_json()
    assert published["tasks"]["read_one"]["path"] == "/api/v1/tasks/:projecttask"
    assert published["tasks_assignees"]["delete"]["path"] == (
        "/api/v1/tasks/:projecttask/assignees/:user"
    )
    assert published["projects_views"]["read_one"]["path"] == (
        "/api/v1/projects/:project/views/:view"
    )
