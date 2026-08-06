"""T26: task assignees.

The value of this file is almost entirely in the cases that look wrong. The assignee
endpoints mirror the label endpoints in shape and contradict them in five behaviours, so
the tests worth having are the ones that fail if someone "harmonises" the two families —
which is the single most likely edit this code will ever receive.

Expected values come from a live Go reference server (``probe_assignees``,
``probe_assignees2``, ``probe_assignees3``), not from ``pkg/models/task_assignees.go``,
and the parity corpus ``harness/corpus/_assignees.yaml`` measured the same 22 of them
independently.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from tests.unit.conftest import ALICE, BOB, PROJECT

from calton.models import ProjectUser, TaskAssignee, User

#: A collaborator with write access to PROJECT — assignable.
CAROL = 902
#: Seeded with no grant on any project at all. Every "the assignee may not be assigned"
#: case rests on this: without a user in this state the check cannot be tested, because
#: every other user in the fixture can reach the project.
DAVE = 903

#: The task the assignments hang off, and one with none.
TASK = 920
EMPTY_TASK = 923
#: Bob's, in Bob's project. Alice cannot touch it.
FORBIDDEN_TASK = 927

MISSING_TASK = 99999
MISSING_USER = 99999

ZERO = "0001-01-01T00:00:00Z"


@pytest.fixture(autouse=True)
def assignee_fixture(sessions: sessionmaker[Session]) -> None:
    """Carol and Dave, plus Bob already assigned to TASK.

    Bob is assigned rather than Carol so that the ordering case can assign Carol
    afterwards; ids ascend in insertion order there, so the ordering case uses Alice
    instead — see ``test_the_list_is_in_insertion_order_not_id_order``.
    """
    epoch = datetime(2026, 2, 1, tzinfo=UTC)
    with sessions() as session:
        session.add_all(
            [
                User(id=CAROL, username="carol", created=epoch, updated=epoch),
                User(id=DAVE, username="dave", created=epoch, updated=epoch),
                # Bob and Carol can reach the host project; Dave deliberately cannot.
                ProjectUser(user_id=BOB, project_id=PROJECT, permission=1),
                ProjectUser(user_id=CAROL, project_id=PROJECT, permission=2),
                TaskAssignee(task_id=TASK, user_id=BOB),
            ]
        )
        session.commit()


def ids_on(client: TestClient, task: int = TASK) -> list[int]:
    response = client.get(f"/api/v1/tasks/{task}/assignees")
    assert response.status_code == 200, response.text
    return [entry["id"] for entry in response.json()]


class TestRead:
    def test_returns_whole_user_objects_without_email(self, client: TestClient) -> None:
        """The list is visible to every collaborator on the project, so an email leaking
        in here is a real disclosure. Asserted as an exact key set: a *missing* field is
        the kind of assertion that quietly disappears when a schema is rewritten."""
        response = client.get(f"/api/v1/tasks/{TASK}/assignees")

        assert response.status_code == 200
        assert response.json() == [
            {
                "id": BOB,
                "name": "",
                "username": "bob",
                "created": "2026-02-01T00:00:00Z",
                "updated": "2026-02-01T00:00:00Z",
            }
        ]

    def test_an_unassigned_task_is_an_empty_array(self, client: TestClient) -> None:
        """``[]``, never ``null`` — and note ``GET /tasks/{id}`` renders the same
        underlying emptiness as ``assignees: null``. The two must not be unified by a
        shared serialiser."""
        response = client.get(f"/api/v1/tasks/{EMPTY_TASK}/assignees")

        assert response.status_code == 200
        assert response.json() == []

    def test_the_empty_page_still_carries_both_pagination_headers(self, client: TestClient) -> None:
        """Measured: result-count 0 and total-pages 0, with the CORS exposure. MCP
        clients loop until they have seen total-pages, so a missing header silently
        truncates every list."""
        response = client.get(f"/api/v1/tasks/{EMPTY_TASK}/assignees")

        assert response.headers["x-pagination-result-count"] == "0"
        assert response.headers["x-pagination-total-pages"] == "0"
        assert "x-pagination-total-pages" in response.headers["access-control-expose-headers"]

    def test_the_list_is_in_insertion_order_not_id_order(self, client: TestClient) -> None:
        """Assigning Alice (900) to a task already holding Bob (901) gives [901, 900].

        Alice is used precisely because her id is *lower* than Bob's: with Carol (902)
        the insertion order and the id order agree and the case proves nothing. An
        ``ORDER BY user_id`` — the natural way to make a listing deterministic — returns
        the same set with the same status and fails only this assertion.
        """
        assert client.put(f"/api/v1/tasks/{TASK}/assignees", json={"user_id": ALICE}).status_code

        assert ids_on(client) == [BOB, ALICE]

    def test_a_task_the_caller_cannot_see_is_403_code_1(self, client: TestClient) -> None:
        """Code **1**, "You're not allowed to do this." — the third distinct forbidden
        body in this family (the label list gives 4005, the label bulk gives 0). A
        unified error mapping is the obvious cleanup and breaks clients branching on it.
        """
        response = client.get(f"/api/v1/tasks/{FORBIDDEN_TASK}/assignees")

        assert response.status_code == 403
        assert response.json() == {"code": 1, "message": "You're not allowed to do this."}

    def test_a_missing_task_reports_the_project_missing(self, client: TestClient) -> None:
        """★ 404 / 3001 "This project does not exist." — naming the *project* for a
        request that only ever mentioned a task, because permissions resolve task →
        project and fail at the project step. The intuitive 4002 "task does not exist" is
        what a reviewer would expect and is wrong."""
        response = client.get(f"/api/v1/tasks/{MISSING_TASK}/assignees")

        assert response.status_code == 404
        assert response.json() == {"code": 3001, "message": "This project does not exist."}


class TestAssign:
    def test_created_is_echoed_as_the_zero_time(self, client: TestClient) -> None:
        """★ Not the row's real timestamp: the response is the bound request struct and
        upstream never fills the field. The label endpoint returns a real one, so this
        cannot become a shared helper. A ``sentinel_time``-style loose assertion would
        accept both and test nothing."""
        response = client.put(f"/api/v1/tasks/{TASK}/assignees", json={"user_id": CAROL})

        assert response.status_code == 201
        assert response.json() == {"user_id": CAROL, "created": ZERO}

    def test_assigning_someone_without_project_access_is_403_7003(self, client: TestClient) -> None:
        """★ The only gate in this file that judges the *assignee* rather than the caller.

        Alice is the project owner and entirely entitled to make the call; it fails
        because of Dave. An implementation that checks only the caller — the natural
        shape, since auth only ever hands you the caller — answers 201 and puts Dave on a
        task he cannot open, with no error anywhere.
        """
        response = client.put(f"/api/v1/tasks/{TASK}/assignees", json={"user_id": DAVE})

        assert response.status_code == 403
        assert response.json() == {
            "code": 7003,
            "message": "This user does not have access to the project.",
        }
        assert ids_on(client) == [BOB]

    def test_assigning_the_same_user_twice_is_400_4021(self, client: TestClient) -> None:
        """4021, not the labels' 8001. Also not 4016, which is the sort validator — the
        corpus already mixed those two up once."""
        response = client.put(f"/api/v1/tasks/{TASK}/assignees", json={"user_id": BOB})

        assert response.status_code == 400
        assert response.json() == {
            "code": 4021,
            "message": "This user is already assigned to that task.",
        }

    @pytest.mark.parametrize(
        ("label", "body"),
        [("a user that does not exist", {"user_id": MISSING_USER}), ("no user_id at all", {})],
    )
    def test_both_kinds_of_bad_user_id_are_404_1005(
        self, client: TestClient, label: str, body: dict[str, int]
    ) -> None:
        """★ A missing ``user_id`` and a non-existent one take the *same* exit.

        The label endpoints split them — absent is 403, non-existent is 404/8002 — so the
        two families cannot share a validator. Note also what this rules out on the
        Python side: declaring ``user_id`` required would answer 422 here, failing both
        rows at once.
        """
        response = client.put(f"/api/v1/tasks/{TASK}/assignees", json=body)

        assert response.status_code == 404, label
        assert response.json() == {"code": 1005, "message": "The user does not exist."}

    def test_a_non_integer_user_id_is_400_2004(self, client: TestClient) -> None:
        """Echo fails to bind the body and never reaches a handler. FastAPI's own answer
        would be 422, which no upstream endpoint ever returns."""
        response = client.put(f"/api/v1/tasks/{TASK}/assignees", json={"user_id": "901"})

        assert response.status_code == 400
        assert response.json()["code"] == 2004

    def test_a_forbidden_task_is_403_code_0(self, client: TestClient) -> None:
        """Code **0** / "Forbidden" — the write path, against code 1 on the read path for
        the very same task and the very same overreach."""
        response = client.put(f"/api/v1/tasks/{FORBIDDEN_TASK}/assignees", json={"user_id": BOB})

        assert response.status_code == 403
        assert response.json() == {"code": 0, "message": "Forbidden"}


class TestUnassign:
    def test_removing_an_assignee_succeeds_and_takes_effect(self, client: TestClient) -> None:
        response = client.delete(f"/api/v1/tasks/{TASK}/assignees/{BOB}")

        assert response.status_code == 200
        assert response.json() == {"message": "Successfully deleted."}
        assert ids_on(client) == []

    @pytest.mark.parametrize(
        ("label", "user"),
        [("a user who was never assigned", DAVE), ("a user who does not exist", MISSING_USER)],
    )
    def test_removing_a_non_assignment_is_an_idempotent_200(
        self, client: TestClient, label: str, user: int
    ) -> None:
        """★ Exactly opposite to labels, where detaching a label that is not attached is
        403. Delete does not validate the target at all — note the contrast with assign
        directly above, where the *same* non-existent id is a 404. Adding the symmetric
        check is a tidy-up that turns two measured 200s into 404s.
        """
        response = client.delete(f"/api/v1/tasks/{TASK}/assignees/{user}")

        assert response.status_code == 200, label
        assert response.json() == {"message": "Successfully deleted."}
        assert ids_on(client) == [BOB]

    def test_the_idempotent_200_is_still_gated_on_the_caller(self, client: TestClient) -> None:
        """Without this, "DELETE always returns 200" passes both rows above."""
        response = client.delete(f"/api/v1/tasks/{FORBIDDEN_TASK}/assignees/{BOB}")

        assert response.status_code == 403
        assert response.json() == {"code": 0, "message": "Forbidden"}

    def test_a_missing_task_reports_the_project_missing(self, client: TestClient) -> None:
        response = client.delete(f"/api/v1/tasks/{MISSING_TASK}/assignees/{BOB}")

        assert response.status_code == 404
        assert response.json() == {"code": 3001, "message": "This project does not exist."}

    def test_a_non_numeric_user_id_is_400_2004(self, client: TestClient) -> None:
        response = client.delete(f"/api/v1/tasks/{TASK}/assignees/abc")

        assert response.status_code == 400
        assert response.json()["code"] == 2004


class TestBulk:
    def test_bulk_replaces_the_set_rather_than_appending(self, client: TestClient) -> None:
        """Bob is not in the submitted list and must be gone afterwards. An append
        implementation leaves {901, 902} and returns the same 201."""
        response = client.post(
            f"/api/v1/tasks/{TASK}/assignees/bulk", json={"assignees": [{"id": CAROL}]}
        )

        assert response.status_code == 201
        assert ids_on(client) == [CAROL]

    def test_the_response_echoes_the_request_unhydrated(self, client: TestClient) -> None:
        """★ ids survive; name, username and both timestamps come back empty even though
        the users exist and a GET a moment later returns them fully populated. Hydrating
        the echo is more useful, more consistent, and exactly what parity forbids."""
        response = client.post(
            f"/api/v1/tasks/{TASK}/assignees/bulk", json={"assignees": [{"id": CAROL}]}
        )

        assert response.json() == {
            "assignees": [
                {"id": CAROL, "name": "", "username": "", "created": ZERO, "updated": ZERO}
            ]
        }

    def test_bulk_keeps_the_rows_it_is_told_to_keep(self, client: TestClient) -> None:
        """★ Submitting [carol, bob] while Bob is already assigned lists [bob, carol]:
        Bob's existing row survives and Carol is appended, so the result does *not*
        follow the request order. Delete-everything-then-reinsert — the simplest way to
        write a replace — produces [carol, bob]: identical set, identical response,
        different order."""
        response = client.post(
            f"/api/v1/tasks/{TASK}/assignees/bulk",
            json={"assignees": [{"id": CAROL}, {"id": BOB}]},
        )

        assert response.status_code == 201
        assert ids_on(client) == [BOB, CAROL]

    def test_an_empty_list_clears_the_set(self, client: TestClient) -> None:
        """The response is correct either way, so only the follow-up read distinguishes a
        real clear from an implementation that short-circuits on "nothing to do"."""
        response = client.post(f"/api/v1/tasks/{TASK}/assignees/bulk", json={"assignees": []})

        assert response.status_code == 201
        assert response.json() == {"assignees": []}
        assert ids_on(client) == []

    def test_an_absent_assignees_key_clears_the_set_and_echoes_null(
        self, client: TestClient
    ) -> None:
        """★ ``{}`` and ``{"assignees": []}`` both clear, and are *not* interchangeable on
        the wire: this one echoes ``null``. Defaulting null to ``[]`` on the way in — the
        reflexive defensive move — changes a response clients can observe."""
        response = client.post(f"/api/v1/tasks/{TASK}/assignees/bulk", json={})

        assert response.status_code == 201
        assert response.json() == {"assignees": None}
        assert ids_on(client) == []

    @pytest.mark.parametrize(
        ("label", "assignees", "status", "expected"),
        [
            (
                "a user with no project access",
                [{"id": DAVE}],
                403,
                {"code": 7003, "message": "This user does not have access to the project."},
            ),
            (
                "a user that does not exist",
                [{"id": MISSING_USER}],
                404,
                {"code": 1005, "message": "The user does not exist."},
            ),
            (
                "the same user twice in one request",
                [{"id": CAROL}, {"id": CAROL}],
                400,
                {"code": 4021, "message": "This user is already assigned to that task."},
            ),
        ],
    )
    def test_a_rejected_batch_writes_nothing(
        self,
        client: TestClient,
        label: str,
        assignees: list[dict[str, int]],
        status: int,
        expected: dict[str, object],
    ) -> None:
        """★ All-or-nothing: the previous set survives every rejection.

        Validation therefore has to run over the whole list before the first write. The
        obvious loop — validate-and-write each entry in turn — passes the status
        assertions and leaves the task half-updated, which only the follow-up read here
        catches. The duplicate row is the subtlest: Carol is not assigned when the
        request arrives, so the error is about the request contradicting itself.
        """
        response = client.post(
            f"/api/v1/tasks/{TASK}/assignees/bulk", json={"assignees": assignees}
        )

        assert response.status_code == status, label
        assert response.json() == expected
        assert ids_on(client) == [BOB], label

    def test_a_forbidden_task_is_403_code_0(self, client: TestClient) -> None:
        response = client.post(
            f"/api/v1/tasks/{FORBIDDEN_TASK}/assignees/bulk", json={"assignees": []}
        )

        assert response.status_code == 403
        assert response.json() == {"code": 0, "message": "Forbidden"}

    def test_a_missing_task_reports_the_project_missing(self, client: TestClient) -> None:
        response = client.post(
            f"/api/v1/tasks/{MISSING_TASK}/assignees/bulk", json={"assignees": []}
        )

        assert response.status_code == 404
        assert response.json() == {"code": 3001, "message": "This project does not exist."}


class TestGateOrder:
    """Which gate answers when two of them would both refuse.

    Each case below is one where a plausible implementation orders the checks the other
    way round and returns a different, equally reasonable-looking error.
    """

    def test_a_missing_task_outranks_a_missing_assignee(self, client: TestClient) -> None:
        """Both are wrong; the answer is about the task. Validating the body first — which
        is what a schema-driven handler does by default — reports 1005 instead."""
        response = client.put(
            f"/api/v1/tasks/{MISSING_TASK}/assignees", json={"user_id": MISSING_USER}
        )

        assert response.status_code == 404
        assert response.json()["code"] == 3001

    def test_the_callers_permission_outranks_a_missing_assignee(self, client: TestClient) -> None:
        """A forbidden task with a bogus assignee is 403, not 404: the caller is refused
        before the body is examined, so nothing leaks about which user ids exist."""
        response = client.put(
            f"/api/v1/tasks/{FORBIDDEN_TASK}/assignees", json={"user_id": MISSING_USER}
        )

        assert response.status_code == 403
        assert response.json() == {"code": 0, "message": "Forbidden"}

    def test_the_callers_permission_outranks_everything_on_delete(self, client: TestClient) -> None:
        response = client.delete(f"/api/v1/tasks/{FORBIDDEN_TASK}/assignees/{MISSING_USER}")

        assert response.status_code == 403
        assert response.json() == {"code": 0, "message": "Forbidden"}


class TestWiring:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", "/api/v1/tasks/{task}/assignees"),
            ("put", "/api/v1/tasks/{task}/assignees"),
            ("post", "/api/v1/tasks/{task}/assignees/bulk"),
            ("delete", "/api/v1/tasks/{task}/assignees/{userID}"),
        ],
    )
    def test_the_route_reaches_the_generated_contract(
        self, app: object, method: str, path: str
    ) -> None:
        assert method in app.openapi()["paths"].get(path, {}), path  # type: ignore[attr-defined]

    def test_every_route_is_registered_for_api_tokens(self) -> None:
        """Mounted but unregistered is refused for every API token while JWT callers see
        nothing wrong — a failure that only ever surfaces under a token, which is the
        harder half of the matrix to remember to test."""
        from calton.api.v1.assignees import REGISTERED_ROUTES
        from calton.core.route_registry import registry

        for method, path in REGISTERED_ROUTES:
            assert registry.lookup(method, path) is not None, f"{method} {path}"


class TestAgainstTheGoRecording:
    """Replay ``tests/fixtures/go_assignees.json`` — bodies the Go server produced.

    Everything above compares this implementation against expectations written by the
    same person who wrote the implementation, which only ever proves the two agree. This
    class compares it against a recording, so a self-consistent mistake — deciding the
    zero timestamp is wrong and "fixing" it in both the code and the test — still fails.

    Statuses and key *sets* are replayed rather than whole bodies: the recording ran
    against the parity seed (task 950, users 900-903) and these tests run against the
    unit fixture (task 920), so ids and timestamps legitimately differ. The shapes do not.
    """

    RECORDING = json.loads(
        (Path(__file__).resolve().parent.parent / "fixtures" / "go_assignees.json").read_text()
    )

    def scenario(self, name: str) -> dict[str, Any]:
        entry: dict[str, Any] = self.RECORDING[name]
        return entry

    @pytest.mark.parametrize(
        ("name", "call"),
        [
            ("read_all", lambda c: c.get(f"/api/v1/tasks/{TASK}/assignees")),
            ("read_all_empty", lambda c: c.get(f"/api/v1/tasks/{EMPTY_TASK}/assignees")),
            ("read_all_forbidden", lambda c: c.get(f"/api/v1/tasks/{FORBIDDEN_TASK}/assignees")),
            ("read_all_missing_task", lambda c: c.get(f"/api/v1/tasks/{MISSING_TASK}/assignees")),
            (
                "add",
                lambda c: c.put(f"/api/v1/tasks/{TASK}/assignees", json={"user_id": CAROL}),
            ),
            (
                "add_no_access",
                lambda c: c.put(f"/api/v1/tasks/{TASK}/assignees", json={"user_id": DAVE}),
            ),
            (
                "add_duplicate",
                lambda c: c.put(f"/api/v1/tasks/{TASK}/assignees", json={"user_id": BOB}),
            ),
            (
                "add_missing_user",
                lambda c: c.put(f"/api/v1/tasks/{TASK}/assignees", json={"user_id": MISSING_USER}),
            ),
            ("add_no_user_id", lambda c: c.put(f"/api/v1/tasks/{TASK}/assignees", json={})),
            (
                "add_forbidden_task",
                lambda c: c.put(f"/api/v1/tasks/{FORBIDDEN_TASK}/assignees", json={"user_id": BOB}),
            ),
            ("delete", lambda c: c.delete(f"/api/v1/tasks/{TASK}/assignees/{BOB}")),
            (
                "delete_never_assigned",
                lambda c: c.delete(f"/api/v1/tasks/{TASK}/assignees/{DAVE}"),
            ),
            (
                "delete_missing_user",
                lambda c: c.delete(f"/api/v1/tasks/{TASK}/assignees/{MISSING_USER}"),
            ),
            (
                "delete_forbidden_task",
                lambda c: c.delete(f"/api/v1/tasks/{FORBIDDEN_TASK}/assignees/{BOB}"),
            ),
            (
                "delete_missing_task",
                lambda c: c.delete(f"/api/v1/tasks/{MISSING_TASK}/assignees/{BOB}"),
            ),
            (
                "bulk",
                lambda c: c.post(
                    f"/api/v1/tasks/{TASK}/assignees/bulk", json={"assignees": [{"id": CAROL}]}
                ),
            ),
            (
                "bulk_empty",
                lambda c: c.post(f"/api/v1/tasks/{TASK}/assignees/bulk", json={"assignees": []}),
            ),
            ("bulk_absent_key", lambda c: c.post(f"/api/v1/tasks/{TASK}/assignees/bulk", json={})),
            (
                "bulk_no_access",
                lambda c: c.post(
                    f"/api/v1/tasks/{TASK}/assignees/bulk", json={"assignees": [{"id": DAVE}]}
                ),
            ),
            (
                "bulk_missing_user",
                lambda c: c.post(
                    f"/api/v1/tasks/{TASK}/assignees/bulk",
                    json={"assignees": [{"id": MISSING_USER}]},
                ),
            ),
            (
                "bulk_duplicate_in_request",
                lambda c: c.post(
                    f"/api/v1/tasks/{TASK}/assignees/bulk",
                    json={"assignees": [{"id": CAROL}, {"id": CAROL}]},
                ),
            ),
        ],
    )
    def test_status_and_body_shape_match_the_recording(
        self, client: TestClient, name: str, call: Any
    ) -> None:
        recorded = self.scenario(name)
        response = call(client)

        assert response.status_code == recorded["status"], name
        for key in ("item_keys", "body_keys"):
            if key in recorded:
                body = response.json()
                actual = sorted(body[0]) if key == "item_keys" else sorted(body)
                assert actual == recorded[key], f"{name}: {key}"

    def test_the_absent_key_and_empty_list_bulk_bodies_differ_exactly_as_recorded(
        self, client: TestClient
    ) -> None:
        """The one place where two requests that do the same thing must answer
        differently. Recorded: ``{}`` gives ``assignees: null``, ``{"assignees": []}``
        gives ``assignees: []``."""
        assert self.scenario("bulk_absent_key")["body"] == {"assignees": None}
        assert self.scenario("bulk_empty")["body"] == {"assignees": []}

        assert client.post(f"/api/v1/tasks/{TASK}/assignees/bulk", json={}).json() == {
            "assignees": None
        }
        assert client.post(
            f"/api/v1/tasks/{TASK}/assignees/bulk", json={"assignees": []}
        ).json() == {"assignees": []}

    def test_bulk_keeps_existing_rows_exactly_as_recorded(self, client: TestClient) -> None:
        """The recording submitted [carol, bob] against a task holding bob and read the
        list back as [bob, carol] — request order not preserved, existing row kept."""
        assert self.RECORDING["bulk_keeps_existing_rows_then_list"] == [901, 902]

        client.post(
            f"/api/v1/tasks/{TASK}/assignees/bulk",
            json={"assignees": [{"id": CAROL}, {"id": BOB}]},
        )
        assert ids_on(client) == [BOB, CAROL]
