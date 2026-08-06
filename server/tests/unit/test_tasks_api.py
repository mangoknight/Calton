"""Wire-level behaviour of the task endpoints.

Assertions here are quoted from responses measured on the Go reference server, and the
ones that contradict the design documents say so at the point of assertion rather than in
a summary somewhere else.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from calton.db.types import ZERO_TIME
from calton.models.project import Project
from calton.models.task import Task, base_task_query

ZERO = "0001-01-01T00:00:00Z"

#: Present in a task response only when the client asked for them via ?expand=, which T24
#: implements. Absent means the key is gone, not that it is null.
EXPAND_ONLY_KEYS = ("is_unread", "subscription", "comment_count", "time_entries_count")


def _create(client: TestClient, **body: Any) -> dict[str, Any]:
    body.setdefault("title", "probe")
    response = client.put("/api/v1/projects/920/tasks", json=body)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


# --------------------------------------------------------------------------------------
# The empty-collection split. Six fields, six assertions per direction, deliberately not
# collapsed into a loop or a dict comparison: merging them means one wrong field is
# reported as "the collections are wrong" and the other five hide behind it.
# --------------------------------------------------------------------------------------


def test_create_response_returns_assignees_as_an_empty_array(client: TestClient) -> None:
    """createTasks calls updateTaskAssignees, which assigns an empty slice."""
    assert _create(client)["assignees"] == []


def test_create_response_returns_labels_as_null(client: TestClient) -> None:
    assert _create(client)["labels"] is None


def test_create_response_returns_related_tasks_as_null(client: TestClient) -> None:
    """Nothing on the create path touches RelatedTasks, so it stays nil."""
    assert _create(client)["related_tasks"] is None


def test_create_response_returns_attachments_as_null(client: TestClient) -> None:
    assert _create(client)["attachments"] is None


def test_create_response_returns_reminders_as_null(client: TestClient) -> None:
    assert _create(client)["reminders"] is None


def test_create_response_returns_reactions_as_null(client: TestClient) -> None:
    assert _create(client)["reactions"] is None


def test_read_response_returns_assignees_as_null_not_an_empty_array(client: TestClient) -> None:
    """The flip. addMoreInfoToTasks only *appends* assignees, so nil survives a read.

    Together with the create case above, this is what rules out the natural
    ``assignees: list = Field(default_factory=list)``: that spelling satisfies create and
    breaks every read, and nothing outside the parity harness would notice.
    """
    assert client.get("/api/v1/tasks/920").json()["assignees"] is None


def test_read_response_returns_related_tasks_as_an_empty_object(client: TestClient) -> None:
    """The other half of the flip: tasks.go:807 assigns make(RelatedTaskMap) always."""
    assert client.get("/api/v1/tasks/920").json()["related_tasks"] == {}


def test_read_response_returns_labels_as_null(client: TestClient) -> None:
    assert client.get("/api/v1/tasks/920").json()["labels"] is None


def test_read_response_returns_attachments_as_null(client: TestClient) -> None:
    assert client.get("/api/v1/tasks/920").json()["attachments"] is None


def test_read_response_returns_reminders_as_null(client: TestClient) -> None:
    assert client.get("/api/v1/tasks/920").json()["reminders"] is None


def test_read_response_returns_reactions_as_null(client: TestClient) -> None:
    assert client.get("/api/v1/tasks/920").json()["reactions"] is None


# --------------------------------------------------------------------------------------
# Zero values and omitted keys
# --------------------------------------------------------------------------------------


def test_unset_times_serialise_as_the_zero_time_not_null(client: TestClient) -> None:
    """Go has no omitzero on these four, and a NULL column reads back as the zero time."""
    body = client.get("/api/v1/tasks/920").json()
    assert body["done_at"] == ZERO
    assert body["due_date"] == ZERO
    assert body["start_date"] == ZERO
    assert body["end_date"] == ZERO


def test_deleted_at_is_absent_from_a_live_task(client: TestClient) -> None:
    assert "deleted_at" not in client.get("/api/v1/tasks/920").json()


def test_expand_fields_are_absent_not_null(client: TestClient) -> None:
    """`omitempty` drops the key. A client testing `"is_unread" in task` must see False."""
    body = client.get("/api/v1/tasks/920").json()
    for key in EXPAND_ONLY_KEYS:
        assert key not in body, f"{key} must be omitted until ?expand= asks for it"


def test_the_omission_mechanism_can_actually_fire(client: TestClient) -> None:
    """Mutation check for the assertion above.

    Absence is trivially true for a field that is never populated, so this shows the
    omission is doing the work: give the model a value and the key appears. Without this,
    ``test_expand_fields_are_absent_not_null`` would still pass if the fields were deleted
    from the schema entirely, and T24 would then be building on nothing.
    """
    from calton.db.types import ZERO_TIME
    from calton.schemas.task import TaskRead

    populated = TaskRead(
        id=1,
        project_id=1,
        done_at=ZERO_TIME,
        due_date=ZERO_TIME,
        start_date=ZERO_TIME,
        end_date=ZERO_TIME,
        created=ZERO_TIME,
        updated=ZERO_TIME,
        is_unread=True,
        comment_count=3,
    )
    dumped = populated.model_dump(mode="json")
    assert dumped["is_unread"] is True
    assert dumped["comment_count"] == 3
    assert "time_entries_count" not in dumped


# --------------------------------------------------------------------------------------
# index / identifier
# --------------------------------------------------------------------------------------


def test_index_counts_up_within_one_project(client: TestClient) -> None:
    """Seeded indexes run to 4, so the next three are 5, 6, 7."""
    assert [_create(client)["index"] for _ in range(3)] == [5, 6, 7]


def test_identifier_degrades_to_a_hash_when_the_project_has_none(client: TestClient) -> None:
    assert client.get("/api/v1/tasks/922").json()["identifier"] == "#3"


def test_identifier_uses_the_project_identifier_when_it_has_one(
    client: TestClient, session: Session
) -> None:
    from calton.models import Project

    project = session.get(Project, 920)
    assert project is not None
    project.identifier = "TFX"
    session.commit()

    assert client.get("/api/v1/tasks/922").json()["identifier"] == "TFX-3"


def test_by_index_accepts_a_project_identifier_string(client: TestClient, session: Session) -> None:
    """Unique to this endpoint (ResolveProjectIdentifier, routes.go:676). Do not generalise."""
    from calton.models import Project

    project = session.get(Project, 920)
    assert project is not None
    project.identifier = "TFX"
    session.commit()

    body = client.get("/api/v1/projects/TFX/tasks/by-index/3").json()
    assert body["id"] == 922
    assert body["identifier"] == "TFX-3"


def test_an_unknown_project_identifier_is_a_bare_message_with_no_code(client: TestClient) -> None:
    """Thrown by the route middleware, not the business layer, so there is no code field."""
    response = client.get("/api/v1/projects/NOPE/tasks/by-index/1")
    assert response.status_code == 404
    assert response.json() == {"message": "Project not found"}


def test_by_index_does_not_find_a_soft_deleted_task(client: TestClient) -> None:
    """Task 921 still holds index 2. Looking it up by (project, index) alone would find it."""
    response = client.get("/api/v1/projects/920/tasks/by-index/2")
    assert response.status_code == 404
    assert response.json() == {"code": 4002, "message": "This task does not exist"}


# --------------------------------------------------------------------------------------
# AC-6: full replacement, and the two fields that are exempt from it
# --------------------------------------------------------------------------------------


def test_update_resets_every_omitted_scalar_to_its_zero_value(client: TestClient) -> None:
    """AC-6 proper. Task 922 has all of these set; the request carries only the title."""
    body = client.post("/api/v1/tasks/922", json={"id": 922, "title": "only title"}).json()

    assert body["title"] == "only title"
    assert body["description"] == ""
    assert body["due_date"] == ZERO
    assert body["start_date"] == ZERO
    assert body["end_date"] == ZERO
    assert body["priority"] == 0
    assert body["percent_done"] == 0
    assert body["hex_color"] == ""
    assert body["repeat_after"] == 0


def test_an_empty_title_does_not_clear_the_title(client: TestClient) -> None:
    """★ Contradicts the design doc and the T18 card, both of which say Task has no AC-6
    exception.

    The column list they reasoned from is real, but ``Update`` ends with
    ``mergo.Merge(&ot, t, WithOverride)``, and mergo skips zero values in the source. Only
    the fields re-zeroed by the explicit block at tasks.go:1543-1589 actually reset, and
    ``title`` is not one of them. Measured on the Go reference server; the corpus records
    it as ``task.update.empty_title_is_ignored``.

    Without this, the first read-modify-write that drops the title silently blanks it —
    and answers 200 while doing so.
    """
    response = client.post("/api/v1/tasks/922", json={"id": 922, "title": ""})

    assert response.status_code == 200
    assert response.json()["title"] == "T-full"
    assert client.get("/api/v1/tasks/922").json()["title"] == "T-full"


def test_an_empty_body_does_not_clear_the_title_either(client: TestClient) -> None:
    assert client.post("/api/v1/tasks/922", json={}).json()["title"] == "T-full"


def test_an_omitted_project_id_leaves_the_task_where_it_is(client: TestClient) -> None:
    """The second AC-6 exception: guarded at tasks.go:1264 before the column list is built.

    Treating project_id as a plain replaced column would move every task updated without
    one to project 0 — a project that does not exist — losing it entirely.
    """
    client.post("/api/v1/tasks/922", json={"id": 922, "title": "T-full"})

    assert client.get("/api/v1/tasks/922").json()["project_id"] == 920


def test_an_explicit_project_id_moves_the_task_and_reallocates_its_index(
    client: TestClient, session: Session
) -> None:
    from calton.models import Project

    session.add(Project(id=930, title="Elsewhere", identifier="", owner_id=900))
    session.commit()

    client.post("/api/v1/tasks/922", json={"id": 922, "title": "T-full", "project_id": 930})

    moved = client.get("/api/v1/tasks/922").json()
    assert moved["project_id"] == 930
    # Index is unique per project, so the old number cannot simply travel with the task.
    assert moved["index"] == 1


def test_explicit_nulls_are_treated_as_zero_values_not_as_errors(client: TestClient) -> None:
    """encoding/json leaves a non-pointer field at its zero value for a JSON null.

    Under Pydantic strict mode a null would otherwise be a 400 for a body upstream
    accepts, and read-modify-write clients send nulls constantly.
    """
    response = client.post(
        "/api/v1/tasks/922",
        json={"id": 922, "title": "t", "description": None, "priority": None},
    )

    assert response.status_code == 200
    assert response.json()["description"] == ""
    assert response.json()["priority"] == 0


def test_echoing_a_whole_read_object_back_is_accepted(client: TestClient) -> None:
    """AC-6's "read-modify-write must not 422". The object carries owner-ish and computed
    fields the write schema does not model at all."""
    original = client.get("/api/v1/tasks/922").json()
    original["title"] = "renamed"

    response = client.post("/api/v1/tasks/922", json=original)

    assert response.status_code == 200
    assert response.json()["title"] == "renamed"


def test_update_response_echoes_readonly_fields_without_persisting_them(
    client: TestClient,
) -> None:
    """★ The update response is the request merged over the row, not a re-read.

    The obvious implementation — write, re-select, serialise — returns index 1 here and
    fails parity. The discriminating half is the follow-up GET: asserting only the
    response body passes under both implementations.
    """
    response = client.post("/api/v1/tasks/920", json={"id": 920, "title": "rmw", "index": 99})

    assert response.json()["index"] == 99
    assert client.get("/api/v1/tasks/920").json()["index"] == 1


def test_the_update_response_identifier_is_empty_while_a_read_computes_one(
    client: TestClient,
) -> None:
    """identifier is computed on the read path only; update echoes what was sent."""
    assert (
        client.post("/api/v1/tasks/922", json={"id": 922, "title": "x"}).json()["identifier"] == ""
    )
    assert client.get("/api/v1/tasks/922").json()["identifier"] == "#3"


def test_the_update_response_created_is_echoed_and_does_not_fall_back_to_the_row(
    client: TestClient,
) -> None:
    """★ Omitting ``created`` on an update answers the **zero time**, not the stored one.

    This is the field where mergo's "zero values fall through" rule does *not* hold, and
    the difference is only visible on a task whose stored ``created`` is non-zero — hence
    the first assertion, which is a premise and not decoration. An implementation writing
    ``data.created if data.created != ZERO_TIME else task.created`` (which is what this
    used to be) passes every test that omits the field on a fixture created at the zero
    time, because there the two rules agree.
    """
    stored = client.get("/api/v1/tasks/920").json()["created"]
    assert stored != ZERO, "premise: the row must have a real created or this cannot discriminate"

    echoed = client.post("/api/v1/tasks/920", json={"id": 920, "title": "no-created"})

    assert echoed.json()["created"] == ZERO
    # ...and the row keeps its own, so the echo really was an echo.
    assert client.get("/api/v1/tasks/920").json()["created"] == stored


def test_the_update_response_created_echoes_a_forged_value_without_storing_it(
    client: TestClient,
) -> None:
    """The other half: a non-zero ``created`` comes straight back, and is not persisted."""
    forged = "1999-03-04T05:06:07Z"
    stored_before = client.get("/api/v1/tasks/920").json()["created"]

    response = client.post(
        "/api/v1/tasks/920", json={"id": 920, "title": "forged", "created": forged}
    )

    assert response.json()["created"] == forged
    assert client.get("/api/v1/tasks/920").json()["created"] == stored_before


def test_the_create_response_created_is_the_servers_own_not_the_clients(
    client: TestClient,
) -> None:
    """Create is the exception: it reports the real timestamp even when one was sent.

    Measured on the reference service — ``PUT`` carrying ``created: 1999-03-04`` answers
    with the server's clock, while ``POST`` of the same body echoes 1999. So this cannot
    be one rule shared by both paths, which is what an echo-everywhere implementation
    would make it.
    """
    forged = "1999-03-04T05:06:07Z"

    created = _create(client, title="server-stamps-me", created=forged)

    assert created["created"] != forged
    assert created["created"] != ZERO
    assert client.get(f"/api/v1/tasks/{created['id']}").json()["created"] == created["created"]


def test_the_update_response_created_by_is_echoed_while_a_read_resolves_it(
    client: TestClient,
) -> None:
    """``created_by`` is ``null`` on an update that omits it, though the row has a creator."""
    assert client.get("/api/v1/tasks/920").json()["created_by"]["id"] == 900

    assert (
        client.post("/api/v1/tasks/920", json={"id": 920, "title": "x"}).json()["created_by"]
        is None
    )


# --------------------------------------------------------------------------------------
# assignees on a write: the rule turns on the task's PRIOR state, not on create vs update.
# The two tests below are the discriminating pair — an implementation branching on
# create/update answers `null` for both, and the second one is the only place that shows.
# --------------------------------------------------------------------------------------


def test_updating_a_task_that_had_no_assignees_answers_an_empty_array(
    client: TestClient,
) -> None:
    """No assignees before, none sent: ``updateTaskAssignees`` returns early, leaving the
    empty slice it had just built. So ``[]``, not ``null``."""
    assert client.get("/api/v1/tasks/920").json()["assignees"] is None  # i.e. none stored

    updated = client.post("/api/v1/tasks/920", json={"id": 920, "title": "x"})

    assert updated.json()["assignees"] == []


def test_updating_a_task_that_had_assignees_answers_null_and_clears_them(
    client: TestClient,
) -> None:
    """Assignees before, none sent: they are deleted and ``setTaskAssignees(nil)`` runs, so
    the response is ``null``. Without the prior assignee this arm is blind — "cleared" and
    "ignored" produce the same body — which is why it is asserted first."""
    task = _create(client, title="has-assignee", assignees=[{"id": 900}])
    assert [u["id"] for u in task["assignees"]] == [900]

    updated = client.post(f"/api/v1/tasks/{task['id']}", json={"id": task["id"], "title": "x"})

    assert updated.json()["assignees"] is None
    assert client.get(f"/api/v1/tasks/{task['id']}").json()["assignees"] is None


# --------------------------------------------------------------------------------------
# done / repeating
# --------------------------------------------------------------------------------------


def test_marking_a_task_done_stamps_done_at(client: TestClient) -> None:
    body = client.post("/api/v1/tasks/920", json={"id": 920, "title": "T-empty", "done": True})

    assert body.json()["done"] is True
    assert body.json()["done_at"] != ZERO


def test_unmarking_a_task_clears_done_at(client: TestClient) -> None:
    body = client.post("/api/v1/tasks/923", json={"id": 923, "title": "T-done", "done": False})

    assert body.json()["done"] is False
    assert body.json()["done_at"] == ZERO


def test_completing_a_repeating_task_reopens_it_on_the_next_occurrence(
    client: TestClient,
) -> None:
    """★ done comes back False and the dates move forward.

    Skipping this still answers 200 — it just marks the task genuinely done, and the
    user's recurring task vanishes from their lists. Asserting the status code alone has
    no power here, so the assertion is on `done` being exactly False and the due date
    having moved.
    """
    response = client.post(
        "/api/v1/tasks/922",
        json={
            "id": 922,
            "title": "T-full",
            "done": True,
            "repeat_after": 86400,
            "due_date": "2026-03-01T12:00:00Z",
            "start_date": "2026-02-25T08:00:00Z",
            "end_date": "2026-03-02T08:00:00Z",
        },
    )

    body = response.json()
    assert body["done"] is False
    assert body["repeat_after"] == 86400
    # Rescheduled relative to now, keeping the time of day.
    assert body["due_date"] != "2026-03-01T12:00:00Z"
    assert body["due_date"].endswith("T12:00:00Z")
    assert body["start_date"].endswith("T08:00:00Z")


# --------------------------------------------------------------------------------------
# Soft delete
# --------------------------------------------------------------------------------------


def test_delete_answers_200_with_a_message_body(client: TestClient) -> None:
    response = client.delete("/api/v1/tasks/922")

    assert response.status_code == 200
    assert response.json() == {"message": "Successfully deleted."}


def test_a_deleted_task_is_gone_from_reads_but_still_in_the_table(
    client: TestClient, session: Session
) -> None:
    """The point of soft deletion: the row survives, every read path must still miss it."""
    client.delete("/api/v1/tasks/922")

    assert client.get("/api/v1/tasks/922").status_code == 404
    assert client.get("/api/v1/projects/920/tasks/by-index/3").status_code == 404
    assert session.get(Task, 922) is not None
    assert session.scalars(base_task_query().where(Task.id == 922)).one_or_none() is None


def test_reading_a_soft_deleted_task_is_indistinguishable_from_a_missing_one(
    client: TestClient,
) -> None:
    deleted = client.get("/api/v1/tasks/921")
    missing = client.get("/api/v1/tasks/999999")

    assert deleted.status_code == missing.status_code == 404
    assert deleted.json() == missing.json()


# --------------------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------------------


def test_a_missing_task_message_has_no_full_stop(client: TestClient) -> None:
    """Unlike the project message. Do not "fix" the punctuation — bodies are compared."""
    assert client.get("/api/v1/tasks/999999").json() == {
        "code": 4002,
        "message": "This task does not exist",
    }


def test_a_non_numeric_task_id_is_a_bind_error(client: TestClient) -> None:
    response = client.get("/api/v1/tasks/abc")

    assert response.status_code == 400
    assert response.json() == {"code": 2004, "message": "Invalid model provided: Bad Request"}


def test_a_negative_task_id_is_a_404_not_a_bind_error(client: TestClient) -> None:
    """It parses fine; it just matches nothing. The two cases are one rule apart."""
    response = client.get("/api/v1/tasks/-1")

    assert response.status_code == 404
    assert response.json() == {"code": 4002, "message": "This task does not exist"}


def test_creating_without_a_title_is_a_task_error(client: TestClient) -> None:
    response = client.put("/api/v1/projects/920/tasks", json={"title": ""})

    assert response.status_code == 400
    assert response.json() == {"code": 4001, "message": "You must provide at least a task title."}


def test_creating_in_a_missing_project_reports_the_project(client: TestClient) -> None:
    response = client.put("/api/v1/projects/999999/tasks", json={"title": "x"})

    assert response.status_code == 404
    assert response.json() == {"code": 3001, "message": "This project does not exist."}


def test_creating_in_a_pseudo_project_is_forbidden_not_malformed(client: TestClient) -> None:
    """-1 is Favorites, which has no rows to put a task in."""
    response = client.put("/api/v1/projects/-1/tasks", json={"title": "x"})

    assert response.status_code == 403
    assert response.json() == {"code": 0, "message": "Forbidden"}


def test_a_repeat_interval_over_ten_years_is_rejected(client: TestClient) -> None:
    response = client.put("/api/v1/projects/920/tasks", json={"title": "x", "repeat_after": 10**12})

    assert response.status_code == 400
    assert response.json()["code"] == 4029


def test_a_string_where_a_bool_belongs_is_refused_rather_than_coerced(
    client: TestClient,
) -> None:
    """Pydantic's lax mode would store True for "yes"; Go refuses the body outright."""
    response = client.put("/api/v1/projects/920/tasks", json={"title": "x", "done": "yes"})

    assert response.status_code == 400
    assert response.json()["code"] == 2004


# --------------------------------------------------------------------------------------
# Permissions. Existence is leaked deliberately: 403 and 404 are distinguishable upstream.
# --------------------------------------------------------------------------------------


def test_reading_someone_elses_task_is_403_with_the_read_wording(client: TestClient) -> None:
    response = client.get("/api/v1/tasks/927")

    assert response.status_code == 403
    assert response.json() == {"code": 0, "message": "You don't have the permission to see this"}


def test_writing_someone_elses_task_is_403_with_the_shorter_wording(client: TestClient) -> None:
    """A different string from the read denial. Both are code 0; only the message differs."""
    response = client.post("/api/v1/tasks/927", json={"title": "x"})

    assert response.status_code == 403
    assert response.json() == {"code": 0, "message": "Forbidden"}


def test_a_forbidden_delete_leaves_the_row_untouched(client: TestClient, session: Session) -> None:
    response = client.delete("/api/v1/tasks/927")

    assert response.status_code == 403
    task = session.get(Task, 927)
    # NULL reads back as the zero time, by design (CaltonDateTime): "not deleted" is the
    # zero time in Python and NULL in the column, and those are the same value.
    assert task is not None and task.deleted_at == ZERO_TIME


def test_an_unauthenticated_request_is_the_middleware_401(app) -> None:  # type: ignore[no-untyped-def]
    """No X-Test-User header at all: there is no subject, so no default user is assumed."""
    anonymous = TestClient(app, raise_server_exceptions=False)
    response = anonymous.get("/api/v1/tasks/920")

    assert response.status_code == 401
    assert response.json()["code"] == 11


# --------------------------------------------------------------------------------------
# Headers and wiring
# --------------------------------------------------------------------------------------


def test_read_one_sends_the_permission_header_and_exposes_it(client: TestClient) -> None:
    """Exposing it matters as much as sending it: without the CORS header the browser
    cannot read the value and the frontend sees undefined."""
    response = client.get("/api/v1/tasks/920")

    assert response.headers["x-max-permission"] == "2"
    assert response.headers["access-control-expose-headers"] == "x-max-permission"


def test_by_index_sends_the_permission_header_too(client: TestClient) -> None:
    assert client.get("/api/v1/projects/920/tasks/by-index/3").headers["x-max-permission"] == "2"


def test_every_task_route_is_mounted_on_the_real_app(app) -> None:  # type: ignore[no-untyped-def]
    """Read from app.openapi(), not app.routes.

    A handler can be defined, imported and covered by unit tests while never being
    reachable — that has happened three times on this project. The OpenAPI document is
    what a client actually sees, so it is the only honest source for "is it wired".
    """
    paths = app.openapi()["paths"]

    assert "put" in paths["/api/v1/projects/{project}/tasks"]
    assert set(paths["/api/v1/tasks/{task}"]) >= {"get", "post", "delete"}
    assert "get" in paths["/api/v1/projects/{project}/tasks/by-index/{index}"]


def test_patch_is_not_served_on_the_item_paths(app) -> None:  # type: ignore[no-untyped-def]
    """The reverse assertion for a verb we deliberately do **not** register.

    This used to assert PATCH *was* mounted. Upstream does not serve it: it registers
    PATCH on exactly four paths (`/test/:table` and three `/admin` routes), and measured
    on the reference service `PATCH /tasks/950`, `/projects/906` and `/labels/950` all
    answer **405** with `Allow: OPTIONS, DELETE, GET, POST`.

    Registering it reads as free — same handler as POST, and every other REST API does
    it — so without an assertion pointing the other way the next person adds it back and
    nothing complains. Phase 1's whitelist does not cover PATCH, so the parity run would
    not catch it either.
    """
    paths = app.openapi()["paths"]

    # Every CRUD item path, not just tasks: coder-f measured all four against the
    # reference service with a POST control proving each route exists.
    for path in (
        "/api/v1/tasks/{task}",
        "/api/v1/projects/{project}",
        "/api/v1/labels/{label}",
        "/api/v1/filters/{filter}",
    ):
        assert "patch" not in paths[path], f"{path} must not serve PATCH; upstream 405s"


def test_no_task_handler_declares_a_bare_dict_response(app) -> None:  # type: ignore[no-untyped-def]
    """Convention C-1: a `-> dict` handler generates an empty response schema, which makes
    the contract diff and the generated TS types vacuous."""
    paths = app.openapi()["paths"]

    for path in (
        "/api/v1/projects/{project}/tasks",
        "/api/v1/tasks/{task}",
        "/api/v1/projects/{project}/tasks/by-index/{index}",
    ):
        for method, operation in paths[path].items():
            if method == "delete":
                continue
            schema = operation["responses"][str(next(iter(operation["responses"])))]
            content = schema.get("content", {})
            assert content.get("application/json", {}).get("schema"), (
                f"{method.upper()} {path} declares no response schema"
            )


def test_the_task_routes_land_on_the_permission_keys_go_uses(app) -> None:  # type: ignore[no-untyped-def]
    """Measured against `GET /routes` on the reference server.

    A wrong group name is not subtle: users cannot grant the permission, so every MCP
    call against tasks answers 403.
    """
    from calton.core.route_registry import registry

    assert registry.can("tasks", "create")
    assert registry.can("tasks", "read_one")
    assert registry.can("tasks", "update")
    assert registry.can("tasks", "delete")
    # by-index is not a CRUD action; upstream files it under the projects group.
    assert "tasks_by_index" in registry.routes["projects"]


def test_by_index_on_a_missing_project_reports_the_task_not_the_project(
    client: TestClient,
) -> None:
    """★ Measured: 404/4002, not 3001 and not 403.

    Upstream resolves (project, index) to a task id and runs the ordinary ReadOne
    pipeline, so the project is never a resource in its own right on this route. An
    implementation that checks the project first — the obvious way to write it — answers
    403 here, because "no permission on a project that does not exist" is how the
    permission query reports a missing project.
    """
    response = client.get("/api/v1/projects/999999/tasks/by-index/1")

    assert response.status_code == 404
    assert response.json() == {"code": 4002, "message": "This task does not exist"}


def test_by_index_on_someone_elses_real_task_is_403(client: TestClient) -> None:
    """The other half: once the task really exists, the denial is the read 403."""
    response = client.get("/api/v1/projects/903/tasks/by-index/1")

    assert response.status_code == 403
    assert response.json() == {"code": 0, "message": "You don't have the permission to see this"}


def test_creating_in_someone_elses_project_is_403_not_404(client: TestClient) -> None:
    """Existence is confirmed to a user who cannot write, matching upstream."""
    response = client.put("/api/v1/projects/903/tasks", json={"title": "x"})

    assert response.status_code == 403
    assert response.json() == {"code": 0, "message": "Forbidden"}


def test_a_non_numeric_project_on_create_is_a_bind_error(client: TestClient) -> None:
    """Only by-index resolves a project identifier string; create does not."""
    response = client.put("/api/v1/projects/abc/tasks", json={"title": "x"})

    assert response.status_code == 400
    assert response.json() == {"code": 2004, "message": "Invalid model provided: Bad Request"}


# --------------------------------------------------------------------------------------
# AC-6, one assertion per column. The lumped test above says "the scalars reset"; this
# says which ones, and fails naming the single column that regressed.
# --------------------------------------------------------------------------------------

#: A non-zero value for every column that full replacement really does reset, so each can
#: be set and then watched disappear. Keys are exactly FULLY_REPLACED_COLUMNS.
NON_ZERO_VALUES: dict[str, Any] = {
    "description": "<p>text</p>",
    "done": True,
    "due_date": "2026-03-01T12:00:00Z",
    "repeat_after": 3600,
    "priority": 4,
    "start_date": "2026-02-25T08:00:00Z",
    "end_date": "2026-03-02T08:00:00Z",
    "hex_color": "aabbcc",
    "percent_done": 0.5,
    "repeat_mode": 2,
    "cover_image_attachment_id": 0,
}

ZERO_VALUES: dict[str, Any] = {
    "description": "",
    "done": False,
    "due_date": ZERO,
    "repeat_after": 0,
    "priority": 0,
    "start_date": ZERO,
    "end_date": ZERO,
    "hex_color": "",
    "percent_done": 0,
    "repeat_mode": 0,
    "cover_image_attachment_id": 0,
}


def test_the_replaced_column_list_covers_every_column_upstream_replaces() -> None:
    """Guards the tables above against drifting out of sync with the service.

    ⚠️ This assertion alone is **self-referential** and was, for a while, the only one
    here: ``NON_ZERO_VALUES`` and ``FULLY_REPLACED_COLUMNS`` are both hand-maintained, so
    it checks that two things I control agree with each other. Adding a column to the
    ``Task`` model touches neither, so the new column would quietly escape full
    replacement with this test green. It is kept — it does catch the table below drifting
    — but the assertion that makes it mean anything is the next test, which anchors both
    lists to the model.
    """
    from calton.services.task_service import FULLY_REPLACED_COLUMNS

    assert set(NON_ZERO_VALUES) == set(FULLY_REPLACED_COLUMNS)
    assert set(ZERO_VALUES) == set(FULLY_REPLACED_COLUMNS)


def test_every_task_column_is_classified_as_replaced_or_not() -> None:
    """The external anchor: the two lists together must account for the whole model.

    This is the assertion the one above cannot make. ``Task.__table__.columns`` is not
    maintained by either list — it comes from the model — so a column added there fails
    here until someone decides which half it belongs to, which is the decision that was
    previously being skipped silently.

    Failing in both directions matters. A column in neither list is one whose update
    behaviour nobody chose. A name in *either* list that is not a column at all is a
    leftover from a rename, and a leftover in ``FULLY_REPLACED_COLUMNS`` is worse than
    untidy: the update path would keep writing zero to a column that no longer exists.
    """
    from calton.models.task import Task
    from calton.services.task_service import FULLY_REPLACED_COLUMNS, NOT_REPLACED_COLUMNS

    columns = {column.key for column in Task.__table__.columns}
    replaced = set(FULLY_REPLACED_COLUMNS)
    preserved = set(NOT_REPLACED_COLUMNS)

    assert not replaced & preserved, (
        f"these columns claim to be both replaced and preserved: {sorted(replaced & preserved)}"
    )

    unclassified = sorted(columns - replaced - preserved)
    assert not unclassified, (
        f"Task gained {unclassified} and neither list mentions them. Decide whether an "
        "omitted value resets the column (FULLY_REPLACED_COLUMNS) or leaves it alone "
        "(NOT_REPLACED_COLUMNS, with the reason), against the reference server rather "
        "than by reading tasks.go."
    )

    phantom = sorted((replaced | preserved) - columns)
    assert not phantom, (
        f"{phantom} are listed but are not columns of Task — renamed or removed, and the "
        "entry outlived them."
    )


def test_every_preserved_column_says_why_it_is_preserved() -> None:
    """An entry without a reason is indistinguishable from one added to silence this test.

    ``NOT_REPLACED_COLUMNS`` is the half a person reaches for when the reconciliation
    above fails, and adding a bare name is the fastest way to make it pass — which would
    turn the new check straight back into the bookkeeping it replaced.
    """
    from calton.services.task_service import NOT_REPLACED_COLUMNS

    for column, reason in NOT_REPLACED_COLUMNS.items():
        assert len(reason.strip()) > 30, f"{column} has no usable reason: {reason!r}"


@pytest.mark.parametrize("column", sorted(NON_ZERO_VALUES))
def test_omitting_a_replaced_column_resets_it(client: TestClient, column: str) -> None:
    """Set one column, then update without it, and it must be back to its zero value."""
    task = _create(client, title="ac6", **{column: NON_ZERO_VALUES[column]})

    updated = client.post(f"/api/v1/tasks/{task['id']}", json={"id": task["id"], "title": "ac6"})

    assert updated.json()[column] == ZERO_VALUES[column], (
        f"{column} survived an update that omitted it; it is not an AC-6 exception"
    )


@pytest.mark.parametrize("column", ["title", "project_id"])
def test_omitting_an_exempt_column_preserves_it(client: TestClient, column: str) -> None:
    """The mirror image, and the reason the two lists have to be spelled out separately.

    A change that made either of these a plain replaced column would pass every test
    above: title would silently blank on read-modify-write, and project_id would move the
    task to project 0.
    """
    before = client.get("/api/v1/tasks/922").json()

    client.post("/api/v1/tasks/922", json={"id": 922})

    assert client.get("/api/v1/tasks/922").json()[column] == before[column]


def test_an_id_beyond_int64_is_refused_rather_than_accepted(client: TestClient) -> None:
    """★ A language difference, not a transcription slip.

    Go binds path parameters onto an int64, so an id past that range fails at binding and
    answers 400/2004. Python's ints are unbounded, so the obvious `int(raw)` accepts a
    value upstream rejects and the request proceeds — silently passing where the reference
    server refuses. Shared with the CRUD routes via path_param_as_id, so all of them are
    bounded by one rule rather than five copies of it.
    """
    response = client.get("/api/v1/tasks/9999999999999999999999")

    assert response.status_code == 400
    assert response.json() == {"code": 2004, "message": "Invalid model provided: Bad Request"}


def test_the_int64_bound_applies_to_the_project_on_create_too(client: TestClient) -> None:
    response = client.put("/api/v1/projects/9999999999999999999999/tasks", json={"title": "x"})

    assert response.status_code == 400
    assert response.json()["code"] == 2004


def test_a_whitespace_only_title_is_accepted(client: TestClient) -> None:
    """Measured on the reference server: 201, not 400.

    Go's `required`/minLength check tests for a non-zero value, not for non-blank, so a
    title of spaces is a real title upstream. Adding a `.strip()` before the empty check
    would reject input the reference server stores — the kind of "improvement" that makes
    a client's request fail against us and succeed against Calton.
    """
    response = client.put("/api/v1/projects/920/tasks", json={"title": "   "})

    assert response.status_code == 201
    assert response.json()["title"] == "   "


# --------------------------------------------------------------------------------------
# Corpus cases that had no local equivalent until now (task.update.not_found,
# task.delete.not_found, task.by_index.out_of_range). Found by auditing tasks.yaml
# case-by-case against this file rather than by assuming the error paths were shared.
# --------------------------------------------------------------------------------------


def test_updating_a_missing_task_is_the_task_404(client: TestClient) -> None:
    response = client.post("/api/v1/tasks/999999", json={"title": "x"})

    assert response.status_code == 404
    assert response.json() == {"code": 4002, "message": "This task does not exist"}


def test_deleting_a_missing_task_is_the_task_404(client: TestClient) -> None:
    response = client.delete("/api/v1/tasks/999999")

    assert response.status_code == 404
    assert response.json() == {"code": 4002, "message": "This task does not exist"}


def test_by_index_past_the_last_index_is_a_404(client: TestClient) -> None:
    """Plain out-of-range, as distinct from the soft-deleted index above.

    Both answer 404/4002, but only one of them exercises the deleted_at filter, so
    covering only the soft-deleted case would leave the ordinary miss untested.
    """
    response = client.get("/api/v1/projects/920/tasks/by-index/9999")

    assert response.status_code == 404
    assert response.json() == {"code": 4002, "message": "This task does not exist"}


def test_the_policy_and_the_service_are_handed_the_same_session(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ Identity, not equality — the only comparison that can catch this.

    One session per request, shared by the permission check and the service. If either
    layer opened its own, a check running after a write would sit in a different
    transaction and not see it: "the object I just created says I have no permission",
    intermittent and hard to reproduce.

    What makes it worth a test is that nothing else catches it. Both layers pass their own
    unit tests either way, and both answer correctly in any test that exercises one layer
    at a time. Only asking "is it literally the same object" fails.
    """
    from calton.permissions import task as task_permissions
    from calton.services import task_service

    seen: dict[str, object] = {}
    real_can_read = task_permissions.can_read
    real_read_view = task_service.read_view

    def spy_can_read(session: object, user_id: int, task_id: int) -> tuple[bool, int]:
        seen["policy"] = session
        return real_can_read(session, user_id, task_id)  # type: ignore[arg-type]

    def spy_read_view(session: object, task: object, user_id: int) -> object:
        seen["service"] = session
        return real_read_view(session, task, user_id)  # type: ignore[arg-type]

    monkeypatch.setattr(task_permissions, "can_read", spy_can_read)
    monkeypatch.setattr(task_service, "read_view", spy_read_view)

    assert client.get("/api/v1/tasks/920").status_code == 200

    assert seen["policy"] is seen["service"]


#: The host project these tests create tasks in — same id the rest of the file uses.
HOST_PROJECT = 920


class TestTheArchivedProjectGate:
    """412/3008 on create, update **and** delete — and it outranks permission.

    ⚠️ Three things here are measured and none is the intuitive choice
    (``harness/probe_coder_e_archived_task_gate.py``):

    * **The gate is wider than its own message.** It says "Editing or creating new tasks
      is not possible", and it also blocks *deleting*. The wording is not the contract.
    * **It outranks the permission check.** A caller holding nothing on an archived
      project gets 412, where the same caller on a live project gets 403. Placing the
      gate after the permission check — the natural reading — gets that cell wrong.
    * **It outranks the title check.** An archived project with an empty title reports
      the archive, not the title.

    ``is_archived`` is the *inherited* value throughout: a project under an archived
    parent is archived for this purpose even though its own column is 0.
    """

    def _archive(self, session: Session, project_id: int) -> None:
        stored = session.get(Project, project_id)
        assert stored is not None
        stored.is_archived = True
        session.commit()

    def test_creating_a_task_is_refused(self, client: TestClient, session: Session) -> None:
        self._archive(session, HOST_PROJECT)

        response = client.put(f"/api/v1/projects/{HOST_PROJECT}/tasks", json={"title": "t"})

        assert response.status_code == 412, response.text
        assert response.json()["code"] == 3008

    def test_it_outranks_the_empty_title_check(self, client: TestClient, session: Session) -> None:
        """★ On a live project this same request is 400/4001 — that contrast is the test."""
        live = client.put(f"/api/v1/projects/{HOST_PROJECT}/tasks", json={"title": ""})
        assert live.status_code == 400, "the control must report the title while unarchived"
        assert live.json()["code"] == 4001

        self._archive(session, HOST_PROJECT)

        response = client.put(f"/api/v1/projects/{HOST_PROJECT}/tasks", json={"title": ""})

        assert response.status_code == 412
        assert response.json()["code"] == 3008

    def test_updating_a_task_is_refused(self, client: TestClient, session: Session) -> None:
        created = client.put(f"/api/v1/projects/{HOST_PROJECT}/tasks", json={"title": "t"})
        assert created.status_code == 201
        task_id = created.json()["id"]
        self._archive(session, HOST_PROJECT)

        response = client.post(f"/api/v1/tasks/{task_id}", json={"title": "renamed"})

        assert response.status_code == 412, response.text
        assert response.json()["code"] == 3008

    def test_deleting_a_task_is_refused(self, client: TestClient, session: Session) -> None:
        """The gate's own message says "editing or creating"; delete is blocked too."""
        created = client.put(f"/api/v1/projects/{HOST_PROJECT}/tasks", json={"title": "t"})
        task_id = created.json()["id"]
        self._archive(session, HOST_PROJECT)

        response = client.delete(f"/api/v1/tasks/{task_id}")

        assert response.status_code == 412, response.text
        assert response.json()["code"] == 3008

    def test_an_inherited_archive_counts(self, client: TestClient, session: Session) -> None:
        """★ The project's own column stays 0; only its parent is archived.

        Testing the stored column instead of the inherited value passes every other case
        in this class.
        """
        parent = client.put("/api/v1/projects", json={"title": "parent"}).json()
        child = client.put(
            "/api/v1/projects", json={"title": "child", "parent_project_id": parent["id"]}
        ).json()
        self._archive(session, parent["id"])
        stored_child = session.get(Project, child["id"])
        assert stored_child is not None
        assert stored_child.is_archived is False, "sample stops discriminating otherwise"

        response = client.put(f"/api/v1/projects/{child['id']}/tasks", json={"title": "t"})

        assert response.status_code == 412, response.text
        assert response.json()["code"] == 3008

    def test_a_live_project_still_accepts_tasks(self, client: TestClient, session: Session) -> None:
        """Without this, "refuse everything" passes every test above."""
        assert (
            client.put(f"/api/v1/projects/{HOST_PROJECT}/tasks", json={"title": "t"}).status_code
            == 201
        )
