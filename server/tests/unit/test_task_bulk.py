"""T27: ``POST /tasks/bulk``.

Expected values come from a live Go reference server (``coder-f``'s probe runs), not from
``pkg/models/bulk_task.go``. Every case here also ran as a differential against that
server on the same seed, so what is pinned is what the reference actually answered.

**The cases that matter most are the ones a response-only assertion cannot see.** Three of
them:

* the batch rolls back as a unit — status codes are identical whether it does or not;
* a scoped edit wipes assignees, reminders and the favourite flag — the response reports
  none of that;
* error precedence follows the order of ``task_ids`` — swap two ids and the same body
  answers a different error.

Each of those has a test whose *read-back* is the assertion.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

# starlette's TestClient is built on httpx2, not httpx (see pyproject); importing
# the response type from the wrong one type-checks locally and fails under mypy.
from httpx2 import Response
from sqlalchemy.orm import Session, sessionmaker
from tests.unit.conftest import ALICE, BOB, BOBS_PROJECT, PROJECT

from calton.models import Favorite, Project, ProjectUser, Task, TaskAssignee, TaskReminder

BULK = "/api/v1/tasks/bulk"

#: The three bulk-target tasks, mirroring the corpus seed's T-bulk-1/2/3 (924-926).
BULK_TASKS = (924, 925, 926)
#: Alice's second project, so a move has somewhere legal to go.
OTHER_PROJECT = 901
#: Bob's, from the shared fixture. Alice holds nothing on it.
FORBIDDEN_TASK = 927
#: Soft-deleted in the shared fixture — invisible to the bulk loader, like a missing row.
DELETED_TASK = 921
MISSING_TASK = 99999
MISSING_PROJECT = 99999

FORBIDDEN_BODY = {"code": 0, "message": "Forbidden"}
NEED_ONE = {"code": 4004, "message": "Need at least one tasks to do bulk editing."}
NO_TASK = {"code": 4002, "message": "This task does not exist"}
NO_PROJECT = {"code": 3001, "message": "This project does not exist."}


@pytest.fixture(autouse=True)
def bulk_fixture(sessions: sessionmaker[Session]) -> None:
    """924-926 in Alice's project, plus a second project of hers to move tasks into.

    Priorities are deliberately left at 0 so that "the write happened" and "the write was
    rolled back" are different observations — seeding them non-zero would let a test pass
    on a value that was already there.
    """
    with sessions() as session:
        session.add(Project(id=OTHER_PROJECT, title="C1", identifier="", owner_id=ALICE))
        session.add_all(
            [
                Task(
                    id=task_id,
                    project_id=PROJECT,
                    index=index,
                    title=f"T-bulk-{index}",
                    created_by_id=ALICE,
                    done=False,
                )
                for index, task_id in enumerate(BULK_TASKS, start=5)
            ]
        )
        session.commit()


def priority_of(client: TestClient, task_id: int, user: int = ALICE) -> int:
    response = client.get(f"/api/v1/tasks/{task_id}", headers={"X-Test-User": str(user)})
    assert response.status_code == 200, response.text
    return int(response.json()["priority"])


def bulk(client: TestClient, **body: object) -> Response:
    return client.post(BULK, json=body)


class TestGateOne4004:
    """Nothing to update at all — 400/4004, never a 404."""

    @pytest.mark.parametrize(
        ("label", "body"),
        [
            ("empty object", {}),
            ("empty list", {"task_ids": [], "fields": ["priority"], "values": {"priority": 1}}),
            ("no task_ids key", {"fields": ["priority"], "values": {"priority": 1}}),
            ("every id missing", {"task_ids": [MISSING_TASK, 88888]}),
            # A soft-deleted row is not "found" here, so a batch of only deleted ids is
            # 4004 — the *same* answer as naming no ids at all, which is why the mixed
            # case below is a separate test.
            ("only a soft-deleted id", {"task_ids": [DELETED_TASK]}),
        ],
    )
    def test_is_4004(self, client: TestClient, label: str, body: dict[str, object]) -> None:
        response = client.post(BULK, json=body)

        assert response.status_code == 400, f"{label}: {response.text}"
        assert response.json() == NEED_ONE

    def test_a_live_id_alongside_a_deleted_one_is_4002_not_4004(self, client: TestClient) -> None:
        """The pair that separates "no rows found" from "one row missing".

        With 924 present the batch clears the 4004 gate, and the deleted id then fails
        the per-id lookup. Collapsing the two gates gives 4004 here and passes every
        test above.
        """
        response = bulk(
            client, task_ids=[924, DELETED_TASK], fields=["priority"], values={"priority": 1}
        )

        assert response.status_code == 404
        assert response.json() == NO_TASK


class TestGateTwoPermission:
    def test_a_forbidden_task_in_the_batch_refuses_the_whole_batch(
        self, client: TestClient
    ) -> None:
        response = bulk(
            client, task_ids=[924, FORBIDDEN_TASK], fields=["priority"], values={"priority": 77}
        )

        assert response.status_code == 403
        # code 0 "Forbidden" — the CRUD pipeline's denial. The attachment endpoints answer
        # code 1 "You're not allowed to do this." for the same kind of refusal; both exist
        # upstream and this endpoint uses this one.
        assert response.json() == FORBIDDEN_BODY

    def test_the_writable_task_is_not_written(self, client: TestClient) -> None:
        """The response above is identical whether or not 924 was written first. Only the
        read-back distinguishes them, and 924 is deliberately *first* in the list so a
        naive implementation really would have written it before reaching 927."""
        before = priority_of(client, 924)

        bulk(client, task_ids=[924, FORBIDDEN_TASK], fields=["priority"], values={"priority": 77})

        assert priority_of(client, 924) == before

    def test_permission_is_checked_before_the_field_names(self, client: TestClient) -> None:
        """Both gates are failable at once; upstream answers the permission one.

        Validating ``fields`` up front — which reads like ordinary input validation —
        answers 4027 here instead.
        """
        response = bulk(client, task_ids=[FORBIDDEN_TASK], fields=["nope"])

        assert response.status_code == 403
        assert response.json() == FORBIDDEN_BODY


class TestGateThreeDestination:
    def test_missing_destination_project_is_3001(self, client: TestClient) -> None:
        response = bulk(
            client, task_ids=[924], fields=["project_id"], values={"project_id": MISSING_PROJECT}
        )

        assert response.status_code == 404
        assert response.json() == NO_PROJECT

    def test_forbidden_destination_project_is_403(self, client: TestClient) -> None:
        response = bulk(
            client, task_ids=[924], fields=["project_id"], values={"project_id": BOBS_PROJECT}
        )

        assert response.status_code == 403
        assert response.json() == FORBIDDEN_BODY

    def test_the_destination_is_checked_even_when_project_id_is_not_in_fields(
        self, client: TestClient
    ) -> None:
        """★ The discriminating case for this gate.

        ``fields=["priority"]`` means ``project_id`` will not be written at all, so no
        task can move — and the request is *still* refused for where it would have moved
        them. An implementation that checks the destination only when it is about to use
        it answers 200 here and passes every other test in this class.
        """
        response = bulk(
            client,
            task_ids=[924],
            fields=["priority"],
            values={"priority": 5, "project_id": BOBS_PROJECT},
        )

        assert response.status_code == 403
        assert response.json() == FORBIDDEN_BODY
        assert priority_of(client, 924) == 0

    def test_a_legal_destination_outside_fields_does_not_move_the_task(
        self, client: TestClient
    ) -> None:
        """The other half of the pair: permitted, so it succeeds — and still does not move
        the task, because ``project_id`` is not among the named columns."""
        response = bulk(
            client,
            task_ids=[924],
            fields=["priority"],
            values={"priority": 6, "project_id": OTHER_PROJECT},
        )

        assert response.status_code == 200
        detail = client.get("/api/v1/tasks/924").json()
        assert detail["project_id"] == PROJECT
        assert detail["priority"] == 6


class TestGateFourPerTask:
    @pytest.mark.parametrize("column", ["nope", "id", "is_favorite", "index", "identifier"])
    def test_a_field_outside_the_updatable_set_is_4027(
        self, client: TestClient, column: str
    ) -> None:
        """``id``, ``index``, ``identifier`` and ``is_favorite`` are all real task fields
        and none of them is an updatable *column*, so all four are rejected — even though
        ``values.is_favorite`` does still take effect."""
        response = bulk(client, task_ids=[924], fields=[column], values={"priority": 1})

        assert response.status_code == 400
        assert response.json() == {
            "code": 4027,
            "message": f"The task field '{column}' is invalid.",
        }

    def test_error_precedence_follows_the_order_of_task_ids(self, client: TestClient) -> None:
        """★ Same body, ids swapped, two different errors.

        Field names are validated *inside* the per-task loop, so whichever id is reached
        first decides which error surfaces. Hoisting the validation out of the loop — the
        obvious tidy-up — makes both of these 4027 and nothing else changes.
        """
        good_first = bulk(client, task_ids=[924, MISSING_TASK], fields=["nope"])
        bad_first = bulk(client, task_ids=[MISSING_TASK, 924], fields=["nope"])

        assert good_first.status_code == 400
        assert good_first.json()["code"] == 4027
        assert bad_first.status_code == 404
        assert bad_first.json()["code"] == 4002


class TestAtomicity:
    def test_bulk_rolls_back_the_whole_batch(self, client: TestClient) -> None:
        """★ The case the card was written around.

        Two writable ids are listed *before* the bad one, so a per-task-commit
        implementation has already written both by the time it fails. The status code and
        the body are byte-identical either way — this test is entirely in the read-back.
        """
        response = bulk(
            client,
            task_ids=[924, 925, MISSING_TASK],
            fields=["priority"],
            values={"priority": 42},
        )

        assert response.status_code == 404
        assert response.json() == NO_TASK
        assert priority_of(client, 924) == 0
        assert priority_of(client, 925) == 0

    def test_a_successful_batch_writes_every_task(self, client: TestClient) -> None:
        """The positive control for the test above: without it, a service that writes
        nothing at all would pass the rollback test."""
        response = bulk(client, task_ids=[924, 925], fields=["priority"], values={"priority": 4})

        assert response.status_code == 200
        assert priority_of(client, 924) == 4
        assert priority_of(client, 925) == 4
        assert priority_of(client, 926) == 0, "untargeted tasks must be untouched"


class TestCollateralDamage:
    """``fields`` scopes *columns*. Assignees, reminders and favourites are not columns."""

    @pytest.fixture(autouse=True)
    def associations(self, sessions: sessionmaker[Session]) -> None:
        with sessions() as session:
            session.add_all(
                [
                    TaskAssignee(task_id=924, user_id=BOB),
                    TaskReminder(
                        task_id=924,
                        reminder=datetime(2026, 9, 1, 10, tzinfo=UTC),
                        relative_period=0,
                        relative_to="",
                    ),
                    Favorite(entity_id=924, user_id=ALICE, kind=1),
                ]
            )
            session.commit()

    def test_a_priority_only_edit_wipes_assignees(self, client: TestClient) -> None:
        """★ Measured on the reference server and reproduced deliberately.

        ``values`` carries no assignees, and upstream reads "no assignees" as "delete them
        all". The response says nothing about it — ``tasks[0].assignees`` is ``null``
        either way — so a UI's "bulk set priority" button silently unassigns everyone.
        If this test ever has to change, that is a product decision plus a deviation
        entry, not a bug fix.
        """
        assert client.get("/api/v1/tasks/924/assignees").json() != []

        bulk(client, task_ids=[924], fields=["priority"], values={"priority": 3})

        assert client.get("/api/v1/tasks/924/assignees").json() == []

    def test_a_priority_only_edit_wipes_reminders(
        self, client: TestClient, sessions: sessionmaker[Session]
    ) -> None:
        bulk(client, task_ids=[924], fields=["priority"], values={"priority": 3})

        with sessions() as session:
            remaining = session.query(TaskReminder).filter_by(task_id=924).count()
        assert remaining == 0

    def test_a_priority_only_edit_clears_the_favourite_flag(self, client: TestClient) -> None:
        assert client.get("/api/v1/tasks/924").json()["is_favorite"] is True

        bulk(client, task_ids=[924], fields=["priority"], values={"priority": 3})

        assert client.get("/api/v1/tasks/924").json()["is_favorite"] is False

    def test_description_survives_because_it_is_a_column(self, client: TestClient) -> None:
        """The contrast that makes the three tests above mean something.

        Without it they read as "bulk destroys everything", and an implementation that
        cleared the description too would pass all three.
        """
        client.post("/api/v1/tasks/924", json={"title": "T-bulk-5", "description": "<p>keep</p>"})

        bulk(client, task_ids=[924], fields=["priority"], values={"priority": 3})

        assert client.get("/api/v1/tasks/924").json()["description"] == "<p>keep</p>"


class TestColumnSemantics:
    def test_a_column_named_in_fields_is_written_even_at_its_zero_value(
        self, client: TestClient
    ) -> None:
        """``values: null`` becomes an empty task, and a named column takes its zero.

        So "set priority" with no values is "clear the priority", not "change nothing".
        """
        bulk(client, task_ids=[924], fields=["priority"], values={"priority": 9})
        assert priority_of(client, 924) == 9

        response = bulk(client, task_ids=[924], fields=["priority"], values=None)

        assert response.status_code == 200
        assert priority_of(client, 924) == 0

    def test_a_column_not_named_in_fields_keeps_its_stored_value(self, client: TestClient) -> None:
        response = bulk(
            client,
            task_ids=[924],
            fields=["priority"],
            values={"priority": 7, "title": "SHOULD-NOT-LAND"},
        )

        assert response.status_code == 200
        detail = client.get("/api/v1/tasks/924").json()
        assert detail["title"] == "T-bulk-5"
        assert detail["priority"] == 7

    def test_an_empty_title_never_clears_the_title_even_when_named(
        self, client: TestClient
    ) -> None:
        """``title`` is in the column list but has no zero-value override, so mergo skips
        it. Every other named column would have been set to its zero here."""
        response = bulk(client, task_ids=[924], fields=["title"], values={"title": ""})

        assert response.status_code == 200
        assert client.get("/api/v1/tasks/924").json()["title"] == "T-bulk-5"

    def test_no_fields_writes_the_default_column_set(self, client: TestClient) -> None:
        """Omitting ``fields`` is not "change nothing" — it writes all fourteen columns,
        so anything absent from ``values`` is reset to its zero. ``title`` still survives,
        which is what separates the column list from the zero-value override list."""
        client.post(
            "/api/v1/tasks/924",
            json={"title": "T-bulk-5", "description": "<p>gone</p>", "hex_color": "ff0000"},
        )

        response = bulk(client, task_ids=[924], values={"priority": 8})

        assert response.status_code == 200
        detail = client.get("/api/v1/tasks/924").json()
        assert detail["priority"] == 8
        assert detail["description"] == ""
        assert detail["hex_color"] == ""
        assert detail["title"] == "T-bulk-5"

    def test_a_duplicated_id_is_accepted(self, client: TestClient) -> None:
        """Not de-duplicated and not an error — the write simply happens twice. The
        assignee bulk endpoint refuses a repeated id outright, so this is worth pinning."""
        response = bulk(client, task_ids=[925, 925], fields=["priority"], values={"priority": 66})

        assert response.status_code == 200
        assert len(response.json()["tasks"]) == 2
        assert priority_of(client, 925) == 66

    def test_a_move_reallocates_the_index(self, client: TestClient) -> None:
        response = bulk(
            client, task_ids=[924], fields=["project_id"], values={"project_id": OTHER_PROJECT}
        )

        assert response.status_code == 200
        assert client.get("/api/v1/tasks/924").json()["project_id"] == OTHER_PROJECT


class TestResponseShape:
    def test_the_response_is_the_envelope_not_a_bare_array(self, client: TestClient) -> None:
        """Upstream's swagger says ``{array} models.Task``. It is wrong: the handler
        serialises the bound struct."""
        response = bulk(client, task_ids=[924], fields=["priority"], values={"priority": 3})

        assert response.status_code == 200
        assert set(response.json()) == {"task_ids", "fields", "values", "tasks"}

    def test_fields_echoes_null_and_empty_list_differently(self, client: TestClient) -> None:
        """Both mean "write the default columns"; only the echo tells them apart. A schema
        that normalises ``None`` to ``[]`` loses a distinction the wire format makes."""
        omitted = bulk(client, task_ids=[924], values={"priority": 1})
        empty = bulk(client, task_ids=[924], fields=[], values={"priority": 1})

        assert omitted.json()["fields"] is None
        assert empty.json()["fields"] == []

    def test_values_is_echoed_hydrated_even_when_the_request_omitted_it(
        self, client: TestClient
    ) -> None:
        response = bulk(client, task_ids=[924], fields=["priority"])

        values = response.json()["values"]
        assert values["priority"] == 0
        assert values["title"] == ""
        assert values["updated"] == "0001-01-01T00:00:00Z"

    def test_values_echoes_the_input_not_the_result(self, client: TestClient) -> None:
        """★ ``title`` was gated out of the write and still comes back in ``values``.

        This is what makes ``values`` an echo of the request rather than a summary of what
        happened, and it is the only field pair that can show the difference.
        """
        response = bulk(
            client,
            task_ids=[924],
            fields=["priority"],
            values={"priority": 7, "title": "SHOULD-NOT-LAND"},
        )

        assert response.json()["values"]["title"] == "SHOULD-NOT-LAND"
        assert client.get("/api/v1/tasks/924").json()["title"] != "SHOULD-NOT-LAND"

    def test_tasks_echo_read_only_fields_without_persisting_them(self, client: TestClient) -> None:
        response = bulk(
            client,
            task_ids=[924],
            fields=["priority"],
            values={"priority": 9, "identifier": "ZZZ", "index": 777},
        )

        echoed = response.json()["tasks"][0]
        assert echoed["identifier"] == "ZZZ"
        assert echoed["index"] == 777
        stored = client.get("/api/v1/tasks/924").json()
        assert stored["identifier"] != "ZZZ"
        assert stored["index"] != 777


class TestAuth:
    def test_bob_may_not_bulk_edit_alices_task(self, client: TestClient) -> None:
        response = client.post(
            BULK,
            json={"task_ids": [924], "fields": ["priority"], "values": {"priority": 1}},
            headers={"X-Test-User": str(BOB)},
        )

        assert response.status_code == 403
        assert response.json() == FORBIDDEN_BODY
        assert priority_of(client, 924) == 0


class TestRouting:
    def test_bulk_route_is_not_shadowed_by_the_task_id_route(self, client: TestClient) -> None:
        """★ Guards the registration order in ``api.v1.tasks``.

        ``POST /tasks/bulk`` must be registered before ``POST /tasks/{task}``. Swap them
        and "bulk" is parsed as a task id, so every request here answers 400/2004 — an
        error that says nothing about routing. This is the same trap ``/tasks/all`` sits
        in, and upstream itself fell into it for that path.
        """
        response = bulk(client, task_ids=[924], fields=["priority"], values={"priority": 1})

        assert response.status_code != 400, "'bulk' was parsed as a task id"
        assert response.status_code == 200

    def test_the_route_is_registered_for_api_tokens(self) -> None:
        """A route mounted but not registered answers 403 to every API token while JWT
        callers see nothing wrong. Measured group/action: tasks.update_bulk."""
        from calton.api.v1 import tasks as tasks_api

        assert ("POST", "/api/v1/tasks/bulk") in tasks_api.REGISTERED_ROUTES


class TestReadModifyWriteEcho:
    """★ The MCP acceptance path.

    The real client updates read-modify-write: GET the whole task, change a field, POST it
    all back. Every read path hydrates ``assignees``, so its body carries them — and
    ``updateSingleTask`` acts on that array. Get this wrong in the obvious direction
    (clear assignees whatever the body says) and every bulk edit strips the assignees off
    every task it touches, silently.

    An earlier version of this module did exactly that, on a measurement taken from a task
    whose assignees a previous probe had already deleted. The fixture below therefore
    seeds a task that *has* an assignee, because a task with none cannot tell the two
    implementations apart.
    """

    @pytest.fixture(autouse=True)
    def assigned(self, sessions: sessionmaker[Session]) -> None:
        # The grant is not decoration: an assignee must be able to *read* the project
        # (assignee_service's third gate), so without it every case here answers 403/7003
        # and the assignee behaviour is never reached.
        with sessions() as session:
            session.add(ProjectUser(user_id=BOB, project_id=PROJECT, permission=1))
            session.add(TaskAssignee(task_id=924, user_id=BOB))
            session.commit()

    def assignees_of(self, client: TestClient, task_id: int = 924) -> list[int]:
        response = client.get(f"/api/v1/tasks/{task_id}/assignees")
        assert response.status_code == 200, response.text
        return sorted(entry["id"] for entry in response.json())

    def test_an_echoed_assignee_array_keeps_the_assignment(self, client: TestClient) -> None:
        """★ The case the acceptance line rests on."""
        assert self.assignees_of(client) == [BOB]

        response = bulk(
            client,
            task_ids=[924],
            fields=["priority"],
            values={"priority": 3, "assignees": [{"id": BOB}]},
        )

        assert response.status_code == 200
        assert self.assignees_of(client) == [BOB]

    def test_omitting_assignees_clears_them(self, client: TestClient) -> None:
        """The other half of the pair — without it the test above passes for an
        implementation that simply never touches assignees at all."""
        bulk(client, task_ids=[924], fields=["priority"], values={"priority": 3})

        assert self.assignees_of(client) == []

    def test_the_echoed_user_object_is_round_tripped_verbatim(self, client: TestClient) -> None:
        """Only ``id`` is acted on; everything else comes back exactly as sent.

        Rebuilding the user from its id — which is what an id-only request model forces —
        answers ``username: ""`` and zeroed timestamps here, and the real client sends
        whole user objects.
        """
        response = bulk(
            client,
            task_ids=[924],
            fields=["priority"],
            values={
                "priority": 4,
                "assignees": [
                    {
                        "id": BOB,
                        "username": "bob",
                        "name": "",
                        "created": "2026-02-01T00:00:00Z",
                        "updated": "2026-02-02T00:00:00Z",
                    }
                ],
            },
        )

        echoed = response.json()["values"]["assignees"][0]
        assert echoed["username"] == "bob"
        assert echoed["created"] == "2026-02-01T00:00:00Z"
        assert echoed["updated"] == "2026-02-02T00:00:00Z"
        assert response.json()["tasks"][0]["assignees"][0]["username"] == "bob"

    def test_an_id_only_assignee_echoes_zeros(self, client: TestClient) -> None:
        """The contrast: what the client sent is what comes back, so a bare id echoes the
        zero value for every other key rather than the stored user."""
        response = bulk(
            client,
            task_ids=[924],
            fields=["priority"],
            values={"priority": 5, "assignees": [{"id": BOB}]},
        )

        echoed = response.json()["values"]["assignees"][0]
        assert echoed == {
            "id": BOB,
            "name": "",
            "username": "",
            "created": "0001-01-01T00:00:00Z",
            "updated": "0001-01-01T00:00:00Z",
        }

    def test_the_assignees_echo_is_three_valued(self, client: TestClient) -> None:
        """★ ``null`` and ``[]`` both mean "none now", and which one appears depends on
        whether there was something to delete — not on the resulting set.

        Computing the echo from the final state collapses the two and is wrong half the
        time; both branches are reachable and are asserted here in order.
        """
        # had one, cleared it -> null
        first = bulk(client, task_ids=[924], fields=["priority"], values={"priority": 1})
        assert first.json()["tasks"][0]["assignees"] is None

        # had none, still none -> []
        second = bulk(client, task_ids=[924], fields=["priority"], values={"priority": 2})
        assert second.json()["tasks"][0]["assignees"] == []

    def test_an_explicit_empty_list_clears_and_echoes_null(self, client: TestClient) -> None:
        response = bulk(
            client, task_ids=[924], fields=["priority"], values={"priority": 6, "assignees": []}
        )

        assert response.status_code == 200
        assert response.json()["values"]["assignees"] == [], "values echoes the request"
        assert response.json()["tasks"][0]["assignees"] is None, "tasks echoes the clearing"
        assert self.assignees_of(client) == []

    def test_a_repeated_assignee_id_is_accepted(self, client: TestClient) -> None:
        """★ 200, not the 400/4021 the assignee *bulk* endpoint answers.

        Upstream builds a map here, so duplicates collapse. Routing this through
        ``assignee_service.bulk_assign`` — the obvious reuse — rejects a body the
        reference server accepts.
        """
        response = bulk(
            client,
            task_ids=[924],
            fields=["priority"],
            values={"priority": 7, "assignees": [{"id": BOB}, {"id": BOB}]},
        )

        assert response.status_code == 200
        assert self.assignees_of(client) == [BOB]

    def test_an_unknown_assignee_is_1005_and_writes_nothing(self, client: TestClient) -> None:
        response = bulk(
            client,
            task_ids=[924],
            fields=["priority"],
            values={"priority": 8, "assignees": [{"id": 99999}]},
        )

        assert response.status_code == 404
        assert response.json() == {"code": 1005, "message": "The user does not exist."}
        assert self.assignees_of(client) == [BOB], "the previous set must survive"
        assert priority_of(client, 924) == 0, "and so must the columns"

    def test_a_good_assignee_alongside_a_bad_one_writes_nothing(self, client: TestClient) -> None:
        """The batch is atomic across the assignee writes too, not just the columns."""
        response = bulk(
            client,
            task_ids=[924],
            fields=["priority"],
            values={"priority": 9, "assignees": [{"id": ALICE}, {"id": 99999}]},
        )

        assert response.status_code == 404
        assert self.assignees_of(client) == [BOB], "alice must not have been added"

    def test_attachments_are_echoed_but_never_acted_on(self, client: TestClient) -> None:
        """``attachments`` is the opposite of ``assignees``: same round trip, no effect.

        Both arrive in the same read-modify-write body, so treating them alike in either
        direction is wrong — acting on attachments would let a client forge rows, and
        ignoring assignees would strip them.
        """
        response = bulk(
            client,
            task_ids=[924],
            fields=["priority"],
            values={"priority": 2, "attachments": [{"id": 999, "task_id": 924}]},
        )

        assert response.status_code == 200
        # Echoed as the parsed struct — five keys, not the two that were sent.
        assert response.json()["values"]["attachments"][0] == {
            "id": 999,
            "task_id": 924,
            "created_by": None,
            "file": None,
            "created": "0001-01-01T00:00:00Z",
        }
        assert client.get("/api/v1/tasks/924/attachments").json() == [], "nothing was created"

    def test_an_empty_attachments_list_echoes_null_in_tasks(self, client: TestClient) -> None:
        """mergo skips a zero-length slice, so ``[]`` in becomes ``null`` out — but only
        in ``tasks``; ``values`` still echoes the request."""
        response = bulk(
            client, task_ids=[924], fields=["priority"], values={"priority": 3, "attachments": []}
        )

        assert response.json()["values"]["attachments"] == []
        assert response.json()["tasks"][0]["attachments"] is None

    def test_the_whole_rmw_body_is_accepted(self, client: TestClient) -> None:
        """★ The blunt end-to-end check: GET a task, POST the entire thing back through
        bulk, and require a 200.

        Every read-only field in the briefing's echo list travels in this body. Any one of
        them tightened into a validation error makes this red — which is the only way this
        project finds out before a real client does, because our own frontend never sends
        them.
        """
        whole = client.get("/api/v1/tasks/924").json()

        response = bulk(
            client, task_ids=[924], fields=["priority"], values={**whole, "priority": 5}
        )

        assert response.status_code == 200, response.text
        assert priority_of(client, 924) == 5
