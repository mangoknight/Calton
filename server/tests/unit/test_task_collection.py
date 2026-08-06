"""The three collection entry points and the polymorphic view response.

The bucket-branch assertions come from measurements recorded in
``harness/corpus-incoming/corpus/_view_shape.yaml``; the seed here mirrors that fixture so
a case written in either place describes the same world.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from tests.unit.conftest import ALICE


def _ids(body: list[dict[str, Any]]) -> list[int]:
    return [entry["id"] for entry in body]


# --------------------------------------------------------------------------------------
# Polymorphism
# --------------------------------------------------------------------------------------


def test_a_kanban_view_returns_buckets_not_tasks(client: TestClient, collection_seed: None) -> None:
    body = client.get("/api/v1/projects/970/views/973/tasks").json()

    assert _ids(body) == [970, 971, 972]
    assert _ids(body[0]["tasks"]) == [9700, 9701, 9702]


def test_a_plain_view_returns_flat_tasks(client: TestClient, collection_seed: None) -> None:
    """Same URL shape, same project, different top-level type."""
    body = client.get("/api/v1/projects/970/views/970/tasks", params={"per_page": 3}).json()

    assert _ids(body) == [9700, 9701, 9702]
    assert "tasks" not in body[0]


def test_the_bucket_shape_is_decided_by_mode_not_by_view_kind(
    client: TestClient, collection_seed: None
) -> None:
    """★ The single most important case in this file.

    View 974 is ``view_kind=list`` with ``bucket_configuration_mode=manual``. Branching on
    ``view_kind`` — everyone's first instinct, since "only boards have columns" — gives the
    right answer on every view the server creates for itself, because there kind and mode
    always agree. It is wrong only on a view a user reconfigured, and the failure is a
    response type the frontend does not parse: a blank board, not an error.
    """
    body = client.get("/api/v1/projects/970/views/974/tasks").json()

    assert _ids(body) == [974]
    assert _ids(body[0]["tasks"]) == [9700, 9701, 9702]


def test_the_same_view_kind_with_mode_none_stays_flat(
    client: TestClient, collection_seed: None
) -> None:
    """Control for the case above: 970 and 974 are both view_kind=list.

    Without it, 974 returning buckets could be explained by "it happens to have bucket
    rows" rather than by the mode. One variable differs; the shape follows it.
    """
    body = client.get("/api/v1/projects/970/views/970/tasks", params={"per_page": 3}).json()

    assert "tasks" not in body[0]


# Un-marked by T28: `bucket_id` now compiles to a subquery against `task_buckets`
# (filters/compiler.py::_bucket_id_condition), so the rows arrive and not just the shape.
# The strict xfail did its job — it failed the moment the gap closed, which is how this
# came to be noticed rather than left as a stale skip.
def test_a_bucket_id_filter_returns_that_buckets_tasks(
    client: TestClient, collection_seed: None
) -> None:
    body = client.get(
        "/api/v1/projects/970/views/973/tasks", params={"filter": "bucket_id = 970"}
    ).json()

    assert _ids(body) == [9700, 9701, 9702]


def test_a_filter_mentioning_bucket_id_falls_back_to_flat_tasks(
    client: TestClient, collection_seed: None
) -> None:
    """★ The fallback triggers on a **substring**, not on a parsed filter field.

    Here ``bucket_id`` is a search term inside a title comparison — no bucket filtering is
    requested at all — and upstream still abandons the bucket shape, because the test is
    ``strings.Contains(opts.filter, "bucket_id")``. Tightening it to "the parsed filter
    has a bucket_id condition" is more correct by every ordinary standard, behaves
    identically on every sane filter, and diverges here on the response *type*, which is
    the one difference a client cannot absorb.

    This is also the shape half of the xfail above: it needs no bucket_id filtering to
    work, so it pins the branch decision on its own.
    """
    response = client.get(
        "/api/v1/projects/970/views/973/tasks",
        params={"filter": "title like 'bucket_id'"},
    )
    body = response.json()

    assert _ids(body) == [9799]
    assert "tasks" not in body[0]
    # The bucket branch would have said 3 here, whatever the rows turned out to be.
    assert response.headers["x-pagination-result-count"] == "1"


# --------------------------------------------------------------------------------------
# Pagination in the bucket branch: the headers change dimension
# --------------------------------------------------------------------------------------


def test_the_result_count_header_counts_buckets_not_tasks(
    client: TestClient, collection_seed: None
) -> None:
    """3 buckets, 53 tasks in the body, and the header says 3."""
    response = client.get("/api/v1/projects/970/views/973/tasks")

    assert response.headers["x-pagination-result-count"] == "3"
    assert response.headers["x-pagination-total-pages"] == "1"
    body = response.json()
    assert sum(len(bucket.get("tasks", [])) for bucket in body) == 53


def test_per_page_limits_tasks_inside_each_bucket_not_the_number_of_buckets(
    client: TestClient, collection_seed: None
) -> None:
    """★ One parameter doing two unrelated jobs.

    ``per_page=2`` gives 2 tasks *per bucket* — 3 buckets, 6 tasks — while the headers
    keep counting buckets, so total-pages becomes ceil(3/2)=2. There is no arithmetic
    connecting the header to the body here, which is exactly why a client must not derive
    one from the other.
    """
    response = client.get("/api/v1/projects/970/views/973/tasks", params={"per_page": 2})
    body = response.json()

    assert response.headers["x-pagination-result-count"] == "3"
    assert response.headers["x-pagination-total-pages"] == "2"
    assert _ids(body[0]["tasks"]) == [9700, 9701]
    assert len(body[1]["tasks"]) == 2


def test_a_buckets_count_is_the_total_not_the_page(
    client: TestClient, collection_seed: None
) -> None:
    """``count`` stays 60 while ``tasks`` holds 2 — the board shows "2 of 60"."""
    body = client.get("/api/v1/projects/970/views/973/tasks", params={"per_page": 2}).json()
    doing = next(bucket for bucket in body if bucket["id"] == 971)

    assert doing["count"] == 60
    assert len(doing["tasks"]) == 2


def test_the_default_per_page_truncates_buckets_too(
    client: TestClient, collection_seed: None
) -> None:
    """★ Guards the `if per_page > 0` shortcut.

    With no ``per_page`` the tasks in a bucket are still capped at 50. An implementation
    that only truncates when the caller passed the parameter returns all 60 — and since
    ``count`` is also 60, the response looks entirely self-consistent. Only this number
    disagrees with upstream.
    """
    body = client.get("/api/v1/projects/970/views/973/tasks").json()
    doing = next(bucket for bucket in body if bucket["id"] == 971)

    assert doing["count"] == 60
    assert len(doing["tasks"]) == 50


# --------------------------------------------------------------------------------------
# Sorting
# --------------------------------------------------------------------------------------


def test_sort_by_is_silently_discarded_in_the_bucket_branch(
    client: TestClient, collection_seed: None
) -> None:
    """★ The caller's sort is replaced outright, with no error.

    Judgeable only because the seed puts priority (1/5/3) opposite to position (10/20/30):
    position asc gives [9700, 9701, 9702], priority desc gives [9701, 9702, 9700] and
    priority asc gives [9700, 9702, 9701]. Make those agree and this test proves nothing.
    """
    body = client.get(
        "/api/v1/projects/970/views/973/tasks",
        params={"sort_by": "priority", "order_by": "desc"},
    ).json()

    assert _ids(body[0]["tasks"]) == [9700, 9701, 9702]


def test_the_same_sort_does_work_on_the_flat_branch(
    client: TestClient, collection_seed: None
) -> None:
    """Control. Without it, "the order did not change" also fits "the parameter was never
    recognised", and the test above would prove nothing about overriding."""
    body = client.get(
        "/api/v1/projects/970/views/970/tasks",
        params={"sort_by": "priority", "order_by": "desc", "per_page": 3},
    ).json()

    assert _ids(body) == [9701, 9702, 9700]


def test_an_unknown_sort_field_is_rejected(client: TestClient) -> None:
    response = client.get("/api/v1/projects/920/tasks", params={"sort_by": "nosuchfield"})

    assert response.status_code == 400
    assert response.json() == {"code": 4016, "message": "The task field 'nosuchfield' is invalid."}


def test_an_unknown_order_reports_the_constant_not_the_input(client: TestClient) -> None:
    response = client.get(
        "/api/v1/projects/920/tasks", params={"sort_by": "priority", "order_by": "sideways"}
    )

    assert response.status_code == 400
    assert response.json() == {
        "code": 4014,
        "message": "The task sort order 'invalid' is invalid. Allowed is either asc or desc.",
    }


# --------------------------------------------------------------------------------------
# Empty buckets
# --------------------------------------------------------------------------------------


def test_an_empty_bucket_is_returned_but_has_no_tasks_key(
    client: TestClient, collection_seed: None
) -> None:
    """★ Both halves matter and they pull in opposite directions.

    The bucket must still appear — filtering out empty ones makes empty board columns
    vanish and the user cannot drag anything into them. And the key must be *absent*, not
    ``null`` and not ``[]``, because the frontend tests ``=== undefined``.
    """
    body = client.get("/api/v1/projects/970/views/973/tasks").json()
    empty = next(bucket for bucket in body if bucket["id"] == 972)

    assert empty["count"] == 0
    assert "tasks" not in empty
    assert "tasks" in body[0]


def test_a_bucket_carries_exactly_the_upstream_keys(
    client: TestClient, collection_seed: None
) -> None:
    body = client.get("/api/v1/projects/970/views/973/tasks").json()
    empty = next(bucket for bucket in body if bucket["id"] == 972)

    assert set(empty) == {
        "id",
        "title",
        "project_view_id",
        "limit",
        "count",
        "position",
        "created",
        "updated",
        "created_by",
    }
    assert set(body[0]) == set(empty) | {"tasks"}


# --------------------------------------------------------------------------------------
# The three entry points, and the alias
# --------------------------------------------------------------------------------------


def test_the_flat_entry_points_agree(client: TestClient) -> None:
    """With no bucket configuration in play, project-scoped and view-scoped must match."""
    project_scoped = client.get("/api/v1/projects/920/tasks").json()
    cross_project = client.get("/api/v1/tasks").json()

    assert _ids(project_scoped) == [920, 922, 923]
    # The cross-project entry point sees the same tasks plus nothing else alice can read.
    assert set(_ids(project_scoped)) <= set(_ids(cross_project))


def test_the_collection_excludes_soft_deleted_tasks(client: TestClient) -> None:
    """Task 921 is soft-deleted and holds index 2."""
    assert 921 not in _ids(client.get("/api/v1/tasks").json())


def test_the_collection_excludes_tasks_the_user_cannot_read(client: TestClient) -> None:
    """Task 927 lives in bob's project."""
    assert 927 not in _ids(client.get("/api/v1/tasks").json())


def test_listing_a_project_without_access_is_the_project_403(client: TestClient) -> None:
    """★ code 7003, which is a *fourth* distinct 403 body in this API.

    Not the CRUD pipeline's code 0, and not the read denial's wording — it is thrown
    somewhere else entirely. Normalising the four into one is the obvious tidy-up and
    breaks clients that branch on the code.
    """
    response = client.get("/api/v1/projects/903/tasks")

    assert response.status_code == 403
    assert response.json() == {
        "code": 7003,
        "message": "This user does not have access to the project.",
    }


def test_the_alias_returns_exactly_what_the_canonical_path_returns(
    client: TestClient,
) -> None:
    """A single status assertion would pass for an alias returning an empty list, and
    calton-mcp@1.0.4 gets all its data through this path."""
    canonical = client.get("/api/v1/tasks", params={"per_page": 50})
    alias = client.get("/api/v1/tasks/all", params={"per_page": 50})

    assert alias.status_code == 200
    assert alias.json() == canonical.json()
    assert (
        alias.headers["x-pagination-result-count"]
        == (canonical.headers["x-pagination-result-count"])
    )
    assert (
        alias.headers["x-pagination-total-pages"] == (canonical.headers["x-pagination-total-pages"])
    )


def test_the_alias_filters_like_the_canonical_path(client: TestClient) -> None:
    body = client.get("/api/v1/tasks/all", params={"filter": "project_id = 920"}).json()

    assert _ids(body) == [920, 922, 923]


def test_the_alias_is_registered_before_the_parameterised_route() -> None:
    """The ordering itself, read off the router the app mounts."""
    from fastapi.routing import APIRoute

    from calton.api.v1 import tasks as tasks_api

    paths = [route.path for route in tasks_api.build_router().routes if isinstance(route, APIRoute)]

    assert paths.index("/tasks/all") < paths.index("/tasks/{task}")


def test_swapping_that_order_reproduces_the_upstream_400(engine: Any, sessions: Any) -> None:
    """★ The half that makes the assertion above load-bearing.

    "/tasks/all answers 200" is satisfied by any correctly ordered app and proves nothing
    about why. This mounts the same handlers with the two routes swapped and shows the
    upstream failure comes straight back: "all" is parsed as a task id, fails, and the
    request 400s — which is exactly what real Calton does, because it never registered
    the alias at all. If this ever stopped reproducing, the ordering comment on the route
    would be describing a constraint that no longer exists.
    """
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.routing import APIRoute

    from calton.api.v1 import tasks as tasks_api
    from calton.core.errors import register_exception_handlers

    router = tasks_api.build_router()
    api_routes = [route for route in router.routes if isinstance(route, APIRoute)]
    alias = next(route for route in api_routes if route.path == "/tasks/all")
    item = next(
        route
        for route in api_routes
        if route.path == "/tasks/{task}" and "GET" in (route.methods or set())
    )
    router.routes.remove(alias)
    router.routes.insert(router.routes.index(item) + 1, alias)

    app = FastAPI()
    register_exception_handlers(app)
    app.state.session_factory = sessions

    @app.middleware("http")
    async def _stub_auth(request, call_next):  # type: ignore[no-untyped-def]
        request.state.auth = SimpleNamespace(id=900)
        return await call_next(request)

    app.include_router(router, prefix="/api/v1")

    response = TestClient(app, raise_server_exceptions=False).get("/api/v1/tasks/all")

    assert response.status_code == 400
    assert response.json() == {"code": 2004, "message": "Invalid model provided: Bad Request"}


def test_both_paths_map_to_the_same_permission_key(app: FastAPI) -> None:
    """A token granted tasks.read_all must reach the alias too — that is the whole point
    of folding tasks_all onto tasks in the registry."""
    from calton.core.route_registry import registry

    assert registry.lookup("GET", "/api/v1/tasks") == ("tasks", "read_all")
    assert registry.lookup("GET", "/api/v1/tasks/all") == ("tasks", "read_all")


def test_the_task_branch_alone_satisfies_the_upstream_contract() -> None:
    """★ Closes the gap the oneOf union leaves in the contract diff.

    The diff unions the branches of a polymorphic response, so a field missing from
    ``TaskRead`` could be masked by ``BucketRead`` happening to declare the same name —
    and six names do overlap (id, title, position, created, updated, created_by). Upstream
    declares this operation as ``Task[]`` and nothing else, so the Task branch on its own
    has to cover what upstream promises. Asserted here rather than in the shared diff
    because it is specific to this endpoint's polymorphism.
    """
    from calton.contract.golden import load_golden, normalise_path
    from calton.main import create_app
    from calton.schemas.task import TaskRead

    golden = load_golden()[("GET", normalise_path("/projects/{id}/views/{view}/tasks"))]
    declared = set(create_app().openapi()["components"]["schemas"]["TaskRead"]["properties"])

    assert golden.response_fields - declared == set(), (
        "TaskRead no longer covers the fields upstream promises for this operation"
    )
    assert declared == set(TaskRead.model_fields)


def test_page_zero_lifts_the_per_bucket_limit(client: TestClient, collection_seed: None) -> None:
    """★ page=0 means "everything" inside each bucket, as it does on the flat branch.

    Measured on the reference server: with five tasks in a bucket, ``per_page=2`` returns
    two of them but ``page=0&per_page=2`` returns all five — while total-pages keeps
    dividing by per_page. So the truncation has to be guarded on the effective *limit*
    (zero when unpaginated), not on per_page, which is always 50 by the time it gets here.
    Capping on per_page instead silently truncates a request upstream leaves whole, and
    every other bucket case still passes.
    """
    response = client.get("/api/v1/projects/970/views/973/tasks", params={"page": 0, "per_page": 2})
    doing = next(bucket for bucket in response.json() if bucket["id"] == 971)

    assert doing["count"] == 60
    assert len(doing["tasks"]) == 60
    # The header still divides the bucket count by per_page.
    assert response.headers["x-pagination-total-pages"] == "2"


# --------------------------------------------------------------------------------------
# Pseudo project ids. Neither has a project row, and reaching the permission query with
# one raises by design — so both need routing before the scope is resolved.
# --------------------------------------------------------------------------------------


def test_the_favorites_pseudo_project_lists_favourited_tasks(
    client: TestClient, session: Session
) -> None:
    """★ ``-1`` is Favorites, and it must not take the saved-filter branch.

    ``-1`` maps to saved filter 0 under the id arithmetic, so testing ``project_id <= -1``
    for "is this a saved filter" sends favourites to a filter that does not exist and
    404s them. That is why the test is ``< -1``, one character apart from the bug.
    """
    from calton.models import Favorite

    session.add(Favorite(entity_id=922, user_id=900, kind=1))
    session.commit()

    body = client.get("/api/v1/projects/-1/tasks").json()

    assert _ids(body) == [922]


def test_the_favorites_project_is_empty_rather_than_broken_without_favourites(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/projects/-1/tasks")

    assert response.status_code == 200
    assert response.json() == []


def test_a_saved_filter_pseudo_id_applies_its_stored_expression(
    client: TestClient, session: Session
) -> None:
    """Filter 1 is addressed as project -2, and its expression narrows the result."""
    from calton.models import SavedFilter

    session.add(
        SavedFilter(id=1, title="Done ones", owner_id=900, filters='{"filter": "done = true"}')
    )
    session.commit()

    body = client.get("/api/v1/projects/-2/tasks").json()

    # 923 is the only done task alice can read.
    assert _ids(body) == [923]


def test_a_missing_saved_filter_is_a_404_not_a_crash(client: TestClient) -> None:
    response = client.get("/api/v1/projects/-9/tasks")

    assert response.status_code == 404


def test_another_users_saved_filter_is_refused_before_it_is_read(
    client: TestClient, session: Session
) -> None:
    """Refused on ownership, not by returning zero rows — otherwise the filter's contents
    could be inferred from which tasks come back."""
    from calton.models import SavedFilter

    session.add(SavedFilter(id=1, title="Bobs", owner_id=901, filters='{"filter": "done = true"}'))
    session.commit()

    assert client.get("/api/v1/projects/-2/tasks").status_code == 403


# --------------------------------------------------------------------------------------
# Search and filter parameters
# --------------------------------------------------------------------------------------


def test_a_hash_number_in_the_search_also_matches_that_index(client: TestClient) -> None:
    """``s=#3`` finds the task at index 3 — as a *union* with the text match, not instead
    of it, so a task whose title literally contains "#3" still matches too."""
    body = client.get("/api/v1/tasks", params={"s": "#3"}).json()

    assert 922 in _ids(body)


def test_a_plain_search_matches_title_text(client: TestClient) -> None:
    body = client.get("/api/v1/tasks", params={"s": "T-full"}).json()

    assert _ids(body) == [922]


def test_an_invalid_filter_timezone_is_reported_with_its_value(client: TestClient) -> None:
    """One of the few errors carrying i18n_params, so the client can localise it."""
    response = client.get("/api/v1/tasks", params={"filter_timezone": "Foo/Bar"})

    assert response.status_code == 400
    assert response.json() == {
        "code": 2003,
        "message": "The timezone 'Foo/Bar' is invalid",
        "i18n_params": {"timezone": "Foo/Bar"},
    }


def test_sort_keys_pair_with_orders_by_position_end_to_end(client: TestClient) -> None:
    """The unit tests cover the pairing; this checks it survives the query string, where
    a plain `params.get` would keep only the last value and silently drop a key."""
    body = client.get(
        "/api/v1/projects/920/tasks",
        params=[("sort_by", "done"), ("sort_by", "id"), ("order_by", "desc")],
    ).json()

    # done desc puts the done task first; id asc orders the rest.
    assert _ids(body) == [923, 920, 922]


def test_the_alias_declaration_matches_what_is_actually_served(client: TestClient) -> None:
    """★ Check the contract file against reality, not against itself.

    ``contract/aliases.yaml`` makes three claims about ``/tasks/all``: that it is served,
    that it runs the same handler as ``GET /tasks``, and that it resolves to the registry
    key ``tasks.read_all`` so one token grant reaches both. A declaration nobody verifies
    is how a path ends up documented as supported after it stopped working — and the
    client that needs this one (calton-mcp@1.0.4) is unmaintained, so nobody would report
    it.
    """
    from calton.contract.golden import load_aliases
    from calton.core.route_registry import registry

    declared = next(entry for entry in load_aliases() if entry["path"] == "/tasks/all")
    canonical = declared["same_handler_as"]

    assert client.get("/api/v1/tasks/all").status_code == 200
    assert (
        client.get("/api/v1/tasks/all").json() == client.get(f"/api/v1{canonical['path']}").json()
    )
    assert (
        list(registry.lookup(declared["method"], f"/api/v1{declared['path']}") or ())
        == (declared["route_registry_key"])
    )


# --------------------------------------------------------------------------------------
# filter_timezone. No corpus case covers this yet, so these are the only guard.
# --------------------------------------------------------------------------------------


def test_filter_timezone_shifts_the_day_boundary_used_by_datemath(
    client: TestClient, session: Session
) -> None:
    """★ ``filter_timezone`` must reach datemath's ``location``, or "today" silently moves.

    ``/d`` truncates to a **wall-clock** day in the caller's zone, and ``Options.location``
    defaults to UTC. Drop the parameter on the floor and "due before today" is computed
    against the UTC midnight: at +08:00 that window runs from 08:00 local to 08:00 the
    next morning, so overdue lists quietly include yesterday's tasks and omit tonight's.
    The request is a 200 either way and nothing anywhere reports a problem.

    The seeded task sits between the two midnights — after Shanghai's start of today,
    before UTC's — so the same query has to return it in one zone and not the other. A
    test using a task far from the boundary passes without the parameter being read at all.
    """
    from datetime import timedelta

    from calton.models import Task

    now = datetime.now(UTC)
    utc_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # 4 hours before UTC midnight is 20:00 UTC yesterday, which is already 04:00 *today*
    # in Asia/Shanghai — i.e. after that zone's start of day but before UTC's.
    between = utc_midnight - timedelta(hours=4)

    session.add(
        Task(
            id=930,
            project_id=920,
            index=30,
            title="straddles midnight",
            created_by_id=ALICE,
            done=False,
            due_date=between,
        )
    )
    session.commit()

    without = client.get("/api/v1/tasks", params={"filter": "due_date < now/d"})
    with_zone = client.get(
        "/api/v1/tasks",
        params={"filter": "due_date < now/d", "filter_timezone": "Asia/Shanghai"},
    )

    assert without.status_code == 200
    assert with_zone.status_code == 200
    assert 930 in _ids(without.json()), "UTC's start of today is after the task's due date"
    assert 930 not in _ids(with_zone.json()), (
        "filter_timezone was ignored: Shanghai's start of today is before the due date"
    )


def test_filter_timezone_reaches_every_collection_entry_point(
    client: TestClient, session: Session
) -> None:
    """The parameter is read in the shared service, so all three entry points get it —
    asserted rather than assumed, because a per-route copy is how one of them loses it."""
    from datetime import timedelta

    from calton.models import Task

    now = datetime.now(UTC)
    between = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=4)
    session.add(
        Task(
            id=931,
            project_id=920,
            index=31,
            title="straddles midnight",
            created_by_id=ALICE,
            done=False,
            due_date=between,
        )
    )
    session.commit()

    params = {"filter": "due_date < now/d", "filter_timezone": "Asia/Shanghai"}
    for path in ("/api/v1/tasks", "/api/v1/tasks/all", "/api/v1/projects/920/tasks"):
        assert 931 not in _ids(client.get(path, params=params).json()), path
