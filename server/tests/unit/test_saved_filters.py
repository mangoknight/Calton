"""T29 — saved filters, and the pseudo project a saved filter is addressed by.

The wire shapes are asserted by the parity corpus (`harness/corpus/_filters.yaml`), which
was measured against a running Go server by someone other than this module's author. What
is here is deliberately the complement: the things the corpus *cannot* reach.

* **the four-route surface**, including the two routes that must NOT exist. A corpus can
  only assert what it sends; "GET /filters is a 405 upstream and must not be built" is a
  statement about absence, and the only place it can be pinned is the route table.
* **side effects on other tables** — the four views a create makes and the fact that a
  delete leaves them behind. The corpus compares responses; both of these are invisible
  there.
* **registration**, whose saved filter is created by a code path with no endpoint of its
  own.
* **the zero-value rule on `filters`**, at the schema level, where the three measured
  inputs can be shown next to each other.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from calton.core.route_registry import registry as route_registry
from calton.models.project_view import ProjectView
from calton.models.saved_filter import SavedFilter
from calton.schemas.project_view import ViewFilter, stored_filter_of
from calton.schemas.saved_filter import SavedFilterFilters
from calton.services import saved_filter_service

ALICE = 900
BOB = 901


def _create(client: TestClient, **body: Any) -> dict[str, Any]:
    payload = {"title": "F", "filters": {"filter": "done = false"}}
    payload.update(body)
    response = client.put("/api/v1/filters", json=payload)
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


# --- the route surface ----------------------------------------------------------------


class TestTheSurfaceIsFourRoutesNotSix:
    """Upstream registers create/read_one/update/delete and nothing else.

    ``GET /api/v1/routes`` on the reference server lists exactly those four under
    ``filters``, and the two a CRUDRouter would have added answer 405 there. That makes
    their absence part of the contract rather than an omission, which is why it is
    asserted rather than left to whoever next reaches for CRUDRouter here.
    """

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("put", "/api/v1/filters"),
            ("get", "/api/v1/filters/{filter}"),
            ("post", "/api/v1/filters/{filter}"),
            ("delete", "/api/v1/filters/{filter}"),
        ],
    )
    def test_the_route_is_mounted(self, app: FastAPI, method: str, path: str) -> None:
        # From the OpenAPI document, not app.routes: routers merged by include_router
        # appear there as _IncludedRouter objects with no .path, so that scan silently
        # sees nothing and passes forever.
        paths = app.openapi()["paths"]
        assert path in paths, f"{path} is not mounted; have {sorted(paths)}"
        assert method in paths[path], f"{path} has no {method.upper()}"

    def test_there_is_no_collection_get_and_no_patch(self, app: FastAPI) -> None:
        """The two CRUDRouter would have added. Both are 405 upstream."""
        paths = app.openapi()["paths"]
        assert "get" not in paths.get("/api/v1/filters", {}), (
            "GET /filters is 405 on the reference server; a collection route here would "
            "answer 200 to a request Calton refuses"
        )
        assert "patch" not in paths.get("/api/v1/filters/{filter}", {}), (
            "PATCH /filters/{filter} is 405 on the reference server"
        )

    def test_the_unmounted_verbs_really_answer_405(self, client: TestClient) -> None:
        """The route-table check above is about the document; this is about the wire."""
        assert client.get("/api/v1/filters").status_code == 405
        assert client.patch("/api/v1/filters/950", json={"title": "x"}).status_code == 405

    @pytest.mark.parametrize(
        ("method", "path", "expected"),
        [
            ("PUT", "/api/v1/filters", ("filters", "create")),
            ("GET", "/api/v1/filters/{filter}", ("filters", "read_one")),
            ("POST", "/api/v1/filters/{filter}", ("filters", "update")),
            ("DELETE", "/api/v1/filters/{filter}", ("filters", "delete")),
        ],
    )
    def test_the_permission_key_is_registered(
        self, app: FastAPI, method: str, path: str, expected: tuple[str, str]
    ) -> None:
        """Mounting and registering are two actions, and only one of them is visible.

        A route absent from the registry routes fine and answers **403 to every API-token
        request** while JWT requests keep working — an asymmetry that reads like anything
        except a missing table entry. ``app`` is requested so the registration in
        ``create_app`` has run.
        """
        assert route_registry.lookup(method, path) == expected


# --- the zero-value rule on `filters` -------------------------------------------------


class TestFiltersIsRequiredByZeroValueNotByPresence:
    """govalidator's ``required`` on ``*TaskCollection`` tests the struct, not the pointer.

    All three rows are measured on the reference server, and they only make sense
    together: an object is not "provided" merely by being present.
    """

    @pytest.mark.parametrize(
        ("body", "status"),
        [
            ({"title": "F"}, 412),  # key absent
            ({"title": "F", "filters": {}}, 412),  # present but empty
            ({"title": "F", "filters": {"filter": ""}}, 412),  # present, explicit zero
            ({"title": "F", "filters": {"s": "hello"}}, 201),  # one non-zero field is enough
        ],
    )
    def test_measured_inputs(self, client: TestClient, body: dict[str, Any], status: int) -> None:
        assert client.put("/api/v1/filters", json=body).status_code == status, body

    def test_the_invalid_fields_entry_quotes_govalidator(self, client: TestClient) -> None:
        response = client.put("/api/v1/filters", json={"title": "F"})

        assert response.json() == {
            "code": 2002,
            "message": "Invalid Data",
            "invalid_fields": ["filters: non zero value required"],
        }

    def test_an_empty_body_names_both_fields(self, client: TestClient) -> None:
        """Order is deliberately not asserted — upstream builds this from a map and Go
        randomises the walk, so it comes back rotated. Compare as a set."""
        response = client.put("/api/v1/filters", json={})

        assert response.status_code == 412
        assert set(response.json()["invalid_fields"]) == {
            "filters: non zero value required",
            "title: non zero value required",
        }

    def test_the_zero_test_is_on_the_model_itself(self) -> None:
        """The schema-level statement of the same rule.

        Asserted directly because ``__bool__`` is what carries it, and a future edit that
        removes it would leave every case above green except the two 412s — which read as
        a validation problem rather than as a deleted method.
        """
        assert not SavedFilterFilters()
        assert not SavedFilterFilters(filter="")
        assert SavedFilterFilters(s="hello")
        assert SavedFilterFilters(filter="done = false")
        assert SavedFilterFilters(filter_include_nulls=True)


# --- the expression is validated at write time ----------------------------------------


class TestTheExpressionIsCheckedBeforeItIsStored:
    """``SavedFilter.Create`` parses the filter before inserting (saved_filters.go:130).

    Without this a user can save a filter that explodes the first time its pseudo project
    is opened — and the failure surfaces on a completely different endpoint.
    """

    def test_an_unparseable_expression_is_400_4024(self, client: TestClient) -> None:
        response = client.put("/api/v1/filters", json={"title": "F", "filters": {"filter": "x"}})

        assert response.status_code == 400
        assert response.json()["code"] == 4024

    def test_an_unknown_field_is_400_4016(self, client: TestClient) -> None:
        response = client.put(
            "/api/v1/filters", json={"title": "F", "filters": {"filter": "nope = 1"}}
        )

        assert response.status_code == 400
        assert response.json()["code"] == 4016

    def test_the_expression_is_reported_before_the_sort_keys(self, client: TestClient) -> None:
        """Both wrong at once answers 4024, the expression's code — measured."""
        response = client.put(
            "/api/v1/filters",
            json={"title": "F", "filters": {"filter": "x", "sort_by": ["nope"]}},
        )

        assert response.json()["code"] == 4024

    def test_a_bad_sort_order_outranks_a_bad_sort_field(self, client: TestClient) -> None:
        """4014 (the order), not 4016 (the field) — parse_sort validates order first."""
        response = client.put(
            "/api/v1/filters",
            json={
                "title": "F",
                "filters": {"filter": "done = false", "sort_by": ["nope"], "order_by": ["bogus"]},
            },
        )

        assert response.json()["code"] == 4014

    def test_an_order_by_with_no_sort_by_is_never_looked_at(self, client: TestClient) -> None:
        """⚠️ Measured **201**, and the bogus order is stored.

        Orders are consumed positionally per ``sort_by`` entry, so with no sort keys
        nothing ever reads them. Validating the list on its own is the obvious tightening
        and rejects a body upstream accepts.
        """
        created = _create(client, filters={"filter": "done = false", "order_by": ["bogus"]})

        assert created["filters"]["order_by"] == ["bogus"]

    def test_the_update_path_checks_it_too(self, client: TestClient, session: Session) -> None:
        created = _create(client)
        response = client.post(
            f"/api/v1/filters/{created['id']}",
            json={"title": "F", "filters": {"filter": "x"}},
        )

        assert response.status_code == 400
        assert response.json()["code"] == 4024


# --- side effects on other tables ------------------------------------------------------


class TestCreatingAFilterCreatesItsPseudoProjectsViews:
    """``Create`` ends in ``CreateDefaultViewsForProject`` for the *negative* id.

    Invisible in the response, so only a database assertion catches it. Without it
    ``GET /projects/-N-1`` answers ``views: []`` where upstream sends four, and the board
    views of a saved filter are unreachable.
    """

    def test_four_views_are_created_against_the_negative_id(
        self, client: TestClient, session: Session
    ) -> None:
        created = _create(client)
        pseudo_id = created["id"] * -1 - 1

        views = list(
            session.scalars(select(ProjectView).where(ProjectView.project_id == pseudo_id))
        )

        assert [view.title for view in views] == ["List", "Gantt", "Table", "Kanban"]

    def test_the_list_view_carries_no_default_filter(
        self, client: TestClient, session: Session
    ) -> None:
        """⚠️ A saved filter's List view must **not** get ``done = false``; a real
        project's must.

        The filter already defines the set, so layering ``done = false`` on top filters
        twice — a saved filter for "tasks I have completed" would come back empty through
        its own List view. Upstream expresses this as one boolean:
        ``CreateDefaultViewsForProject(..., createDefaultListFilter)``, ``true`` from
        ``project.go:1145`` and ``false`` from ``saved_filters.go:142``.

        ⚠️ **Only the List view can show this.** Gantt, Table and Kanban have no default
        filter on either kind of project, so asserting on them proves nothing — they are
        identical under both implementations. They are checked here only as the contrast
        that makes the List row meaningful, not as three more test cases.
        """
        created = _create(client)
        pseudo_id = created["id"] * -1 - 1

        views = list(
            session.scalars(select(ProjectView).where(ProjectView.project_id == pseudo_id))
        )
        by_title = {view.title: view for view in views}

        assert by_title["List"].filter is None, "the filter already defines the set"
        # The contrast: these three are null on a real project too, which is exactly why
        # they cannot discriminate.
        assert [by_title[title].filter for title in ("Gantt", "Table", "Kanban")] == [
            None,
            None,
            None,
        ]

    def test_a_real_projects_list_view_still_filters_done(
        self, client: TestClient, session: Session
    ) -> None:
        """The other half of the pair. Without it, "never set a List filter anywhere"
        passes the test above and silently changes every ordinary project."""
        response = client.put("/api/v1/projects", json={"title": "Ordinary"})
        assert response.status_code == 201, response.text

        views = list(
            session.scalars(
                select(ProjectView).where(ProjectView.project_id == response.json()["id"])
            )
        )
        list_view = next(view for view in views if view.title == "List")

        # The column holds the whole marshalled TaskCollection, not the bare expression
        # — built through the same helper the implementation uses so the two cannot drift
        # into agreeing about a shape neither server produces.
        assert list_view.filter == stored_filter_of(ViewFilter(filter="done = false"))
        assert list_view.filter is not None
        assert '"filter":"done = false"' in list_view.filter

    def test_deleting_the_filter_leaves_the_views_behind(
        self, client: TestClient, session: Session
    ) -> None:
        """⚠️ Upstream's behaviour, copied on purpose — measured: after deleting a filter
        created through the API its four view rows are still there, now pointing at a
        project id that resolves to nothing.

        Cascading them is the obvious tidy-up. It would make Calton's database diverge
        from a Go one under the same request sequence, which is what ``schema_diff`` and
        every ``assert_sql`` case compare.
        """
        created = _create(client)
        pseudo_id = created["id"] * -1 - 1

        assert client.delete(f"/api/v1/filters/{created['id']}").status_code == 200

        session.expire_all()
        assert session.get(SavedFilter, created["id"]) is None
        orphans = list(
            session.scalars(select(ProjectView).where(ProjectView.project_id == pseudo_id))
        )
        assert len(orphans) == 4, "upstream leaves these behind; do not cascade"


class TestRegistrationCreatesMyOpenTasks:
    def test_a_new_account_gets_the_filter(self, client: TestClient, session: Session) -> None:
        response = client.post(
            "/api/v1/register",
            json={"username": "eve1", "email": "eve1@example.com", "password": "12345678"},
        )
        assert response.status_code == 200, response.text

        session.expire_all()
        stored = session.scalars(
            select(SavedFilter).where(SavedFilter.owner_id == response.json()["id"])
        ).one()

        assert stored.title == "My Open Tasks"
        # ⚠️ The expression interpolates the **username**, not the id. `assignees = 904`
        # matches nothing, and matching nothing is indistinguishable from "you have no
        # open tasks" — the correct answer for a brand-new account, so the wrong version
        # looks right for as long as anyone bothers to check.
        assert (
            saved_filter_service.stored_filters(stored).filter == "done = false && assignees = eve1"
        )

    def test_it_shows_up_as_a_pseudo_project_in_the_project_list(self, client: TestClient) -> None:
        """The reason the filter matters to a client at all: registration is the only way
        most users ever acquire one."""
        response = client.post(
            "/api/v1/register",
            json={"username": "eve2", "email": "eve2@example.com", "password": "12345678"},
        )
        user_id = response.json()["id"]

        listed = client.get("/api/v1/projects", headers={"X-Test-User": str(user_id)}).json()
        pseudo = [entry for entry in listed if entry["id"] < 0]

        assert [entry["title"] for entry in pseudo] == ["My Open Tasks"]


# --- the pseudo project read path ------------------------------------------------------


class TestReadingASavedFilterAsAProject:
    """``GET /projects/{negative id}``. The corpus covers the three outcomes byte for
    byte; what is added here is the ownership boundary against a second user, which needs
    two identities in one test."""

    def test_the_owner_gets_the_synthetic_project(self, client: TestClient) -> None:
        created = _create(client, title="Mine")
        pseudo_id = created["id"] * -1 - 1

        response = client.get(f"/api/v1/projects/{pseudo_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == pseudo_id
        assert body["title"] == "Mine"
        assert body["owner"]["id"] == ALICE
        assert body["parent_project_id"] == 0
        # Always 0 in the body; the real value travels in the header. CanRead answers
        # PermissionAdmin for every saved filter, because by then the caller is the owner.
        assert body["max_permission"] == 0
        assert response.headers["x-max-permission"] == "2"

    def test_somebody_elses_filter_is_403_with_the_read_message(self, client: TestClient) -> None:
        created = _create(client, title="Mine")
        pseudo_id = created["id"] * -1 - 1

        response = client.get(f"/api/v1/projects/{pseudo_id}", headers={"X-Test-User": str(BOB)})

        assert response.status_code == 403
        assert response.json() == {
            "code": 0,
            "message": "You don't have the permission to see this",
        }

    def test_a_missing_filter_is_404_11001_not_the_projects_3001(self, client: TestClient) -> None:
        """The path says ``/projects/``; the error is the *saved filter's*.

        And it comes out of the permission check rather than the service — a policy that
        returned False for the missing case would produce 403 instead, which is the
        natural shape in a policy-then-service pipeline.
        """
        response = client.get("/api/v1/projects/-9999")

        assert response.status_code == 404
        assert response.json() == {
            "code": 11001,
            "message": "This saved filter does not exist.",
        }

    def test_a_favourited_filter_reports_it_on_both_shapes(self, client: TestClient) -> None:
        """`is_favorite` is the one per-row field of a pseudo project, so the collection
        and the item must agree about it. They are built by different call sites and would
        not agree by construction."""
        created = _create(client, title="Fav", is_favorite=True)
        pseudo_id = created["id"] * -1 - 1

        item = client.get(f"/api/v1/projects/{pseudo_id}").json()
        listed = client.get("/api/v1/projects").json()
        in_collection = next(entry for entry in listed if entry["id"] == pseudo_id)

        assert item["is_favorite"] is True
        assert in_collection["is_favorite"] is True


class TestTheWriteAndDeletePathsAreUnchangedForPseudoIds:
    """T29 builds the **read** path only. These pin the current answers so that whoever
    builds the write path sees them move rather than discovering them."""

    def test_updating_a_saved_filters_pseudo_project_is_403_here(self, client: TestClient) -> None:
        """⚠️ **A recorded difference.** Upstream answers **200** and really renames the
        underlying filter (``Project.Update`` delegates to ``SavedFilter.Update``,
        project.go:1369-1386). Measured. Calton refuses, because the write path for pseudo
        ids is not built. When it lands this goes red, and that is the signal to change it.
        """
        created = _create(client)
        pseudo_id = created["id"] * -1 - 1

        response = client.post(f"/api/v1/projects/{pseudo_id}", json={"title": "x"})

        assert response.status_code == 403, "upstream answers 200 and renames the filter"

    def test_deleting_a_saved_filters_pseudo_project_is_403_here(self, client: TestClient) -> None:
        """Upstream answers **404/3001** — the *project* code, not 11001 — measured. Also
        not built; pinned so the difference has a name."""
        created = _create(client)
        pseudo_id = created["id"] * -1 - 1

        response = client.delete(f"/api/v1/projects/{pseudo_id}")

        assert response.status_code == 403, "upstream answers 404/3001"
