"""T31: task relations, and how they surface inside a task's JSON.

Expected values come from a live Go reference server (probes 3, 5, 6, 7, 8, 9 in this
task's scratchpad), not from ``pkg/models/task_relation.go``. The parity corpus
``harness/corpus/_relations.yaml`` measured 16 of them independently; cases with no corpus
counterpart say so on the test, because those have no second opinion behind them.

Two things dominate this file:

* **Bidirectionality.** Both write paths touch two rows. Every assertion here reads back
  from the *far* end, because the near end looks correct under a one-sided implementation.
* **The nested task shape.** ``related_tasks`` embeds whole task objects that are
  deliberately *not* hydrated — empty identifier, null ``created_by``, null
  ``related_tasks`` — except for ``is_favorite``, which is. Rendering them with the
  ordinary read view breaks nothing a user can see and changes four fields on the wire.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from tests.unit.conftest import ALICE, BOB

from calton.models import Project, Task, TaskRelation

#: A project with an identifier, so a nested task's empty ``identifier`` is visibly wrong
#: rather than trivially empty. The shared fixture's project deliberately has none.
RELATION_PROJECT = 950

BASE = 9500  # has both a subtask and a related, seeded in both directions
CHILD = 9501  # BASE's subtask
PEER = 9502  # BASE's related
BARE = 9503  # no relations at all
SPARE = 9504

FORBIDDEN_TASK = 927  # bob's, in bob's project
MISSING_TASK = 99999

FORBIDDEN = {"code": 0, "message": "Forbidden"}
INVALID_KIND = {"code": 4007, "message": "The task relation is invalid."}
NO_SUCH_RELATION = {"code": 4009, "message": "The task relation does not exist."}
NO_SUCH_TASK = {"code": 4002, "message": "This task does not exist"}


@pytest.fixture(autouse=True)
def relation_fixture(sessions: sessionmaker[Session]) -> None:
    """BASE --subtask--> CHILD and BASE --related--> PEER, each written **twice**.

    Seeding both directions is not redundancy: without the inverse rows already present,
    "deleting one direction removes the other" passes vacuously, because the row it was
    supposed to clean up never existed.

    The ``subtask`` pair is written *before* the ``related`` pair so that insertion order
    (subtask, related) runs opposite to alphabetical order (related, subtask). With them in
    the same order the key-sorting assertion cannot fail.
    """
    epoch = datetime(2026, 2, 1, tzinfo=UTC)
    with sessions() as session:
        session.add(
            Project(id=RELATION_PROJECT, title="Relations", identifier="AS", owner_id=ALICE)
        )
        for index, task_id in enumerate((BASE, CHILD, PEER, BARE, SPARE), start=1):
            session.add(
                Task(
                    id=task_id,
                    project_id=RELATION_PROJECT,
                    index=index,
                    title=f"R-{task_id}",
                    created_by_id=ALICE,
                    done=False,
                    created=epoch,
                    updated=epoch,
                )
            )
        for forward, backward, kind, inverse in (
            (BASE, CHILD, "subtask", "parenttask"),
            (BASE, PEER, "related", "related"),
        ):
            session.add(
                TaskRelation(
                    task_id=forward,
                    other_task_id=backward,
                    relation_kind=kind,
                    created_by_id=ALICE,
                    created=epoch,
                )
            )
            session.add(
                TaskRelation(
                    task_id=backward,
                    other_task_id=forward,
                    relation_kind=inverse,
                    created_by_id=ALICE,
                    created=epoch,
                )
            )
        session.commit()


def related(client: TestClient, task_id: int) -> dict[str, list[int]]:
    response = client.get(f"/api/v1/tasks/{task_id}")
    assert response.status_code == 200, response.text
    return {
        kind: [task["id"] for task in tasks]
        for kind, tasks in response.json()["related_tasks"].items()
    }


class TestTheTaskJson:
    def test_related_tasks_is_populated_without_being_asked_for(self, client: TestClient) -> None:
        """No ``?expand=`` involved: ``addMoreInfoToTasks`` fills this on every read path.

        Answering ``{}`` — which is what an unimplemented relation layer does — is a
        difference on every task that has any relation at all, on every one of the four
        read endpoints.
        """
        assert related(client, BASE) == {"related": [PEER], "subtask": [CHILD]}

    def test_the_keys_are_alphabetical_not_insertion_ordered(self, client: TestClient) -> None:
        """Go's ``encoding/json`` sorts map keys; Python dicts do not.

        The fixture writes ``subtask`` first precisely so the two orders differ. Purely a
        byte-level difference — nothing functional depends on it, so only parity can see
        it, and a serialisation bug is the wrong place to go looking.
        """
        body = client.get(f"/api/v1/tasks/{BASE}").json()

        assert list(body["related_tasks"]) == ["related", "subtask"]

    def test_a_task_with_no_relations_is_an_empty_object_not_null(self, client: TestClient) -> None:
        """``related_tasks: {}`` next to ``labels: null`` and ``assignees: null`` in the
        same body. A map's zero value serialises as ``{}`` and a slice's as ``null``;
        unifying the three breaks whichever two you did not pick.
        """
        body = client.get(f"/api/v1/tasks/{BARE}").json()

        assert body["related_tasks"] == {}
        assert body["labels"] is None
        assert body["assignees"] is None

    def test_the_nested_task_is_deliberately_not_hydrated(self, client: TestClient) -> None:
        """Empty ``identifier`` although the project has one, null ``created_by``, null
        ``related_tasks`` — while the same task read directly has all three.

        ``addRelatedTasksToTasks`` says in a comment that it does not recurse. Recursing
        is the natural implementation, changes three fields at once, and risks unbounded
        recursion wherever two tasks relate to each other.
        """
        nested = client.get(f"/api/v1/tasks/{BASE}").json()["related_tasks"]["subtask"][0]
        direct = client.get(f"/api/v1/tasks/{CHILD}").json()

        assert nested["identifier"] == ""
        assert nested["created_by"] is None
        assert nested["related_tasks"] is None
        assert nested["index"] == direct["index"] == 2
        assert direct["identifier"] == "AS-2"
        assert direct["created_by"]["id"] == ALICE
        assert list(direct["related_tasks"]) == ["parenttask"]

    def test_is_favorite_is_the_one_field_that_is_hydrated(self, client: TestClient) -> None:
        """The exception to the rule above, and it is per-caller.

        Not covered by the corpus. ``addRelatedTasksToTasks`` sets ``IsFavorite`` by hand
        right before copying the task, so "don't hydrate anything" is *too* simple a rule
        and leaves a starred subtask looking unstarred in its parent's card.
        """
        assert client.post(f"/api/v1/tasks/{CHILD}", json={"is_favorite": True}).status_code == 200

        nested = client.get(f"/api/v1/tasks/{BASE}").json()["related_tasks"]["subtask"][0]

        assert nested["is_favorite"] is True

    def test_a_relation_to_an_unreadable_task_is_dropped_with_its_key(
        self, client: TestClient, sessions: sessionmaker[Session]
    ) -> None:
        """The far end is filtered by the caller's project access.

        Not covered by the corpus. Without the filter, ``related_tasks`` hands back the
        title, description and dates of any task whose id appears in a relation row —
        a read of data the caller has no access to, through a request that looks
        completely ordinary. And when filtering empties a kind, the **key goes too**:
        ``{}``, not ``{"related": []}``.
        """
        with sessions() as session:
            session.add(
                TaskRelation(
                    task_id=BARE,
                    other_task_id=FORBIDDEN_TASK,
                    relation_kind="related",
                    created_by_id=ALICE,
                )
            )
            session.commit()

        body = client.get(f"/api/v1/tasks/{BARE}").json()

        assert body["related_tasks"] == {}

    def test_within_one_kind_the_order_is_the_relation_row_id(
        self, client: TestClient, sessions: sessionmaker[Session]
    ) -> None:
        """Not the other task's id.

        Not covered by the corpus. The three rows below are inserted with ``other_task_id``
        running *opposite* to their insertion order, so "row order" and "id order" are
        different answers; with them agreeing the assertion cannot fail.
        """
        with sessions() as session:
            for other in (SPARE, CHILD, PEER):  # descending-ish, deliberately not sorted
                session.add(
                    TaskRelation(
                        task_id=BARE,
                        other_task_id=other,
                        relation_kind="blocking",
                        created_by_id=ALICE,
                    )
                )
            session.commit()

        assert related(client, BARE)["blocking"] == [SPARE, CHILD, PEER]

    def test_the_update_response_echoes_related_tasks_rather_than_computing_it(
        self, client: TestClient
    ) -> None:
        """``POST /tasks/{id}`` answers ``related_tasks: null`` for a client that did not
        send one, even on a task that has relations — the update response is the bound
        request merged over the row, not a re-read. Filling it in here would be more
        useful and is a wire difference on every task update.
        """
        response = client.post(f"/api/v1/tasks/{BASE}", json={"title": "R-9500"})

        assert response.status_code == 200
        assert response.json()["related_tasks"] is None


class TestCreate:
    def test_the_created_relation_has_exactly_five_keys(self, client: TestClient) -> None:
        """No ``id``: the row has a primary key and does not publish it, which is why the
        delete route addresses a relation by ``(task, kind, other task)``.

        ``created_by`` is a full user object and ``created`` a real timestamp — both
        differ from the assignee endpoints' equivalents, which return an empty shell and
        the zero time respectively.
        """
        response = client.put(
            f"/api/v1/tasks/{BARE}/relations",
            json={"other_task_id": SPARE, "relation_kind": "subtask"},
        )

        assert response.status_code == 201
        body = response.json()
        assert set(body) == {"task_id", "other_task_id", "relation_kind", "created_by", "created"}
        assert body["task_id"] == BARE
        assert body["created_by"]["id"] == ALICE
        assert body["created"] != "0001-01-01T00:00:00Z"

    def test_the_inverse_row_is_written_too(self, client: TestClient) -> None:
        """Read from the **far** end. From the originating task a one-sided implementation
        looks perfect, and the 201 is identical either way: the only visible symptom is a
        parent whose subtask list is empty, which users report as a display bug.
        """
        client.put(
            f"/api/v1/tasks/{BARE}/relations",
            json={"other_task_id": SPARE, "relation_kind": "subtask"},
        )

        assert related(client, SPARE) == {"parenttask": [BARE]}

    def test_the_inverse_is_appended_not_substituted(self, client: TestClient) -> None:
        """CHILD already has ``parenttask: [BASE]`` from the seed; a second parent appends.

        This is the case the parity corpus originally got wrong — it expected only the
        newly created id, having read "the harness resets" as "the task starts with no
        relations". Corrected under ``relation.create.writes_the_inverse_side``.
        """
        client.put(
            f"/api/v1/tasks/{BARE}/relations",
            json={"other_task_id": CHILD, "relation_kind": "subtask"},
        )

        assert related(client, CHILD) == {"parenttask": [BASE, BARE]}

    def test_a_duplicate_is_409_not_400(self, client: TestClient) -> None:
        """The only 409 in this API. "Already there" is 400/8001 for labels and 400/4021
        for assignees; making the three consistent breaks two of them."""
        response = client.put(
            f"/api/v1/tasks/{BASE}/relations",
            json={"other_task_id": CHILD, "relation_kind": "subtask"},
        )

        assert response.status_code == 409
        assert response.json() == {"code": 4008, "message": "The task relation already exists."}

    def test_relating_a_task_to_itself_is_400_4010(self, client: TestClient) -> None:
        """Note the message has **no** trailing full stop, where 4007/4008/4009 all do."""
        response = client.put(
            f"/api/v1/tasks/{BARE}/relations",
            json={"other_task_id": BARE, "relation_kind": "related"},
        )

        assert response.status_code == 400
        assert response.json() == {
            "code": 4010,
            "message": "You cannot relate a task with itself",
        }

    def test_an_unknown_kind_and_a_missing_kind_are_the_same_400(self, client: TestClient) -> None:
        """400/4007, not a 412 with ``invalid_fields`` — which is what declaring the enum
        on the request schema would produce, for the most likely bad input this endpoint
        receives. The message does not echo the offending value.
        """
        unknown = client.put(
            f"/api/v1/tasks/{BARE}/relations",
            json={"other_task_id": SPARE, "relation_kind": "nosuch"},
        )
        missing = client.put(f"/api/v1/tasks/{BARE}/relations", json={"other_task_id": SPARE})

        assert unknown.status_code == missing.status_code == 400
        assert unknown.json() == missing.json() == INVALID_KIND

    def test_the_kind_is_checked_before_the_tasks_are(self, client: TestClient) -> None:
        """A bad kind against a task that does not exist is 400, not 404; against a task
        the caller cannot write it is 400, not 403.

        Not covered by the corpus. ``CanCreate`` validates the kind on its first line,
        before it looks at either task.
        """
        missing = client.put(
            f"/api/v1/tasks/{MISSING_TASK}/relations",
            json={"other_task_id": SPARE, "relation_kind": "nosuch"},
        )
        forbidden = client.put(
            f"/api/v1/tasks/{FORBIDDEN_TASK}/relations",
            json={"other_task_id": SPARE, "relation_kind": "nosuch"},
        )

        assert missing.json() == INVALID_KIND
        assert forbidden.json() == INVALID_KIND

    def test_the_far_end_needs_read_permission(self, client: TestClient) -> None:
        """The base task is alice's and she may write it; the other end is bob's.

        The single easiest check to omit — authentication only ever sees the path
        parameter — and omitting it answers 201, after which ``related_tasks`` hands alice
        the whole of bob's task.
        """
        response = client.put(
            f"/api/v1/tasks/{BARE}/relations",
            json={"other_task_id": FORBIDDEN_TASK, "relation_kind": "related"},
        )

        assert response.status_code == 403
        assert response.json() == FORBIDDEN

    def test_either_end_missing_gives_the_identical_404(self, client: TestClient) -> None:
        """Indistinguishable from outside, and that is part of the contract."""
        base_missing = client.put(
            f"/api/v1/tasks/{MISSING_TASK}/relations",
            json={"other_task_id": SPARE, "relation_kind": "related"},
        )
        other_missing = client.put(
            f"/api/v1/tasks/{BARE}/relations",
            json={"other_task_id": MISSING_TASK, "relation_kind": "related"},
        )

        assert base_missing.status_code == other_missing.status_code == 404
        assert base_missing.json() == other_missing.json() == NO_SUCH_TASK

    def test_a_body_task_id_replaces_the_path_segment(self, client: TestClient) -> None:
        """Echo binds path parameters before the body, so the body wins.

        Not covered by the corpus. Permissions follow the effective id, so this is a wire
        quirk and not a hole — but an implementation that ignores the body value writes
        the relation on a different task and returns a different ``task_id``.
        """
        response = client.put(
            f"/api/v1/tasks/{BARE}/relations",
            json={"task_id": SPARE, "other_task_id": CHILD, "relation_kind": "related"},
        )

        assert response.status_code == 201
        assert response.json()["task_id"] == SPARE
        assert related(client, BARE) == {}
        assert related(client, SPARE) == {"related": [CHILD]}


class TestCycles:
    """409/4023, and only for the two hierarchical kinds. No corpus case covers any of
    this, so these were measured directly (probe 9)."""

    def test_a_two_task_subtask_loop_is_refused(self, client: TestClient) -> None:
        """BASE is already CHILD's parent; making BASE a subtask of CHILD closes the loop.

        Without it the front end's relation tree recurses forever.
        """
        response = client.put(
            f"/api/v1/tasks/{CHILD}/relations",
            json={"other_task_id": BASE, "relation_kind": "subtask"},
        )

        assert response.status_code == 409
        assert response.json() == {
            "code": 4023,
            "message": "This task relation would create a cycle.",
        }

    def test_a_longer_chain_is_walked_not_just_the_immediate_parent(
        self, client: TestClient
    ) -> None:
        """BASE → CHILD → BARE, then BARE → BASE. Checking only the direct parent admits
        this and produces exactly the same unbounded recursion one level further out."""
        assert (
            client.put(
                f"/api/v1/tasks/{CHILD}/relations",
                json={"other_task_id": BARE, "relation_kind": "subtask"},
            ).status_code
            == 201
        )

        response = client.put(
            f"/api/v1/tasks/{BARE}/relations",
            json={"other_task_id": BASE, "relation_kind": "subtask"},
        )

        assert response.status_code == 409

    def test_a_non_hierarchical_loop_is_allowed(self, client: TestClient) -> None:
        """``blocking`` both ways is two 201s. The check runs for ``subtask`` and
        ``parenttask`` only, so extending it to every kind — which reads like hardening —
        starts refusing relations upstream accepts."""
        first = client.put(
            f"/api/v1/tasks/{BARE}/relations",
            json={"other_task_id": SPARE, "relation_kind": "blocking"},
        )
        second = client.put(
            f"/api/v1/tasks/{SPARE}/relations",
            json={"other_task_id": BARE, "relation_kind": "blocking"},
        )

        assert first.status_code == 201
        assert second.status_code == 201


class TestDelete:
    def test_deleting_removes_both_directions_and_nothing_else(self, client: TestClient) -> None:
        """The far end loses its row too, and BASE keeps its *other* relation.

        The second half matters: deleting every row with a matching ``task_id`` is a real
        implementation and clears the whole task.
        """
        response = client.delete(f"/api/v1/tasks/{BASE}/relations/subtask/{CHILD}")

        assert response.status_code == 200
        assert response.json() == {"message": "Successfully deleted."}
        assert related(client, CHILD) == {}
        assert related(client, BASE) == {"related": [PEER]}

    def test_the_inverse_direction_is_an_equivalent_entry_point(self, client: TestClient) -> None:
        """Deleting ``CHILD --parenttask--> BASE`` clears the same pair.

        Matching only the row the request names happens to work forwards — that row is
        found first — and leaves the forward row behind when the request comes from the
        other side.
        """
        response = client.delete(f"/api/v1/tasks/{CHILD}/relations/parenttask/{BASE}")

        assert response.status_code == 200
        assert related(client, BASE) == {"related": [PEER]}
        assert related(client, CHILD) == {}

    def test_deleting_twice_is_404_not_an_idempotent_200(self, client: TestClient) -> None:
        client.delete(f"/api/v1/tasks/{BASE}/relations/subtask/{CHILD}")
        second = client.delete(f"/api/v1/tasks/{BASE}/relations/subtask/{CHILD}")

        assert second.status_code == 404
        assert second.json() == NO_SUCH_RELATION

    def test_an_invalid_kind_is_404_here_and_400_on_create(self, client: TestClient) -> None:
        """The same malformed input, two answers. Delete does not validate the kind at
        all — it looks a relation up by it and finds none. Sharing one kind validator
        between the two verbs, which is what anyone would do, turns this into a 400.
        """
        deleted = client.delete(f"/api/v1/tasks/{BASE}/relations/nosuch/{PEER}")
        created = client.put(
            f"/api/v1/tasks/{BARE}/relations",
            json={"other_task_id": SPARE, "relation_kind": "nosuch"},
        )

        assert deleted.status_code == 404
        assert deleted.json() == NO_SUCH_RELATION
        assert created.status_code == 400
        assert created.json() == INVALID_KIND

    def test_the_kind_is_matched_case_sensitively(self, client: TestClient) -> None:
        """Not covered by the corpus. ``SUBTASK`` matches no row → 404, and the relation
        survives."""
        response = client.delete(f"/api/v1/tasks/{BASE}/relations/SUBTASK/{CHILD}")

        assert response.status_code == 404
        assert related(client, BASE) == {"related": [PEER], "subtask": [CHILD]}

    def test_permission_is_checked_before_the_relation_is_looked_up(
        self, client: TestClient
    ) -> None:
        """Otherwise the 404/200 split reveals which relations exist on a task the caller
        cannot see."""
        response = client.delete(f"/api/v1/tasks/{FORBIDDEN_TASK}/relations/related/{BASE}")

        assert response.status_code == 403
        assert response.json() == FORBIDDEN

    def test_a_missing_base_task_is_404_4002_not_4009(self, client: TestClient) -> None:
        """Two different 404s on the same route, and they are not interchangeable: 4002
        says the task is gone, 4009 says the relation is. Not covered by the corpus."""
        missing_task = client.delete(f"/api/v1/tasks/{MISSING_TASK}/relations/related/{BASE}")
        missing_other = client.delete(f"/api/v1/tasks/{BASE}/relations/related/{MISSING_TASK}")

        assert missing_task.json() == NO_SUCH_TASK
        assert missing_other.json() == NO_SUCH_RELATION


def test_read_only_members_cannot_write_relations(
    client: TestClient, sessions: sessionmaker[Session]
) -> None:
    """``CanDelete``/``CanCreate`` both go through ``Task.CanUpdate`` — write, not read.

    Bob has nothing on RELATION_PROJECT, so both verbs are 403 from his side.
    """
    create = client.put(
        f"/api/v1/tasks/{BARE}/relations",
        json={"other_task_id": SPARE, "relation_kind": "related"},
        headers={"X-Test-User": str(BOB)},
    )
    delete = client.delete(
        f"/api/v1/tasks/{BASE}/relations/subtask/{CHILD}",
        headers={"X-Test-User": str(BOB)},
    )

    assert create.status_code == 403
    assert delete.status_code == 403


class TestTheReadModifyWriteEcho:
    """The real MCP client updates by ``GET`` → change one field → ``POST`` the whole
    object back, so **everything these endpoints emit comes back as a request body**.

    Recorded from 40 real client sessions (coder-h) and re-measured here against the
    reference server. It matters for T31 specifically because ``related_tasks`` used to be
    permanently ``{}`` — nothing to echo — and is now a populated structure of whole nested
    task objects. Anything that answers 422 breaks every update the client makes, and our
    own frontend can never find it because it only ever sends the fields it cares about.
    """

    def test_a_task_update_tolerates_its_own_populated_related_tasks(
        self, client: TestClient
    ) -> None:
        """The case T31 created. Before it, an echoed ``related_tasks`` was always ``{}``.

        The body is taken from a real ``GET`` rather than hand-written, so it carries the
        nested task objects — each of which is itself a full task with its own null
        ``related_tasks`` — plus ``labels``, ``created_by``, ``identifier`` and ``index``.
        """
        whole = client.get(f"/api/v1/tasks/{BASE}").json()
        assert whole["related_tasks"], "the fixture must have relations or this proves nothing"
        whole["title"] = "R-9500 edited"

        response = client.post(f"/api/v1/tasks/{BASE}", json=whole)

        assert response.status_code == 200, response.text
        assert response.json()["title"] == "R-9500 edited"
        # Echoed, not recomputed — the update response is the bound request over the row.
        assert list(response.json()["related_tasks"]) == ["related", "subtask"]

    def test_a_task_update_does_not_treat_related_tasks_as_a_write_instruction(
        self, client: TestClient
    ) -> None:
        """A **premise** assertion, not a behaviour one (practice #24).

        T31 relies on ``related_tasks`` being read-only on the task endpoints — relations
        are changed only through ``/relations``. Measured on the reference server: a
        fabricated ``related_tasks`` in a task update is accepted and ignored. If upstream
        ever makes it a write path, this goes red and the relation service needs revisiting
        — do not simply update the expectation.
        """
        response = client.post(
            f"/api/v1/tasks/{BARE}",
            json={"title": "R-9503", "related_tasks": {"subtask": [{"id": PEER}]}},
        )

        assert response.status_code == 200
        assert related(client, BARE) == {}

    def test_creating_a_relation_tolerates_echoed_read_only_fields(
        self, client: TestClient
    ) -> None:
        """``created_by`` and ``created`` come back in the 201, so a client that retries or
        replays a create will send them. Both must be **ignored** — the server's own values
        win — rather than rejected or, worse, honoured.
        """
        response = client.put(
            f"/api/v1/tasks/{BARE}/relations",
            json={
                "task_id": BARE,
                "other_task_id": SPARE,
                "relation_kind": "subtask",
                "created_by": {
                    "id": 1,
                    "name": "",
                    "username": "someone-else",
                    "created": "2000-01-01T00:00:00Z",
                    "updated": "2000-01-01T00:00:00Z",
                },
                "created": "2000-01-01T00:00:00Z",
            },
        )

        assert response.status_code == 201, response.text
        assert response.json()["created_by"]["id"] == ALICE
        assert response.json()["created"] != "2000-01-01T00:00:00Z"

    def test_a_comment_update_tolerates_the_whole_comment_object(self, client: TestClient) -> None:
        """Cross-checked here as well as in ``test_comments.py`` because the comment body
        carries a **nested user object** — the one shape most likely to be rejected by a
        strict write schema, and the reason ``author`` is declared on ``TaskCommentWrite``
        rather than dropped."""
        created = client.put(f"/api/v1/tasks/{BASE}/comments", json={"comment": "<p>hello</p>"})
        assert created.status_code == 201, created.text
        comment_id = created.json()["id"]

        whole = client.get(f"/api/v1/tasks/{BASE}/comments/{comment_id}").json()
        whole["comment"] = "<p>edited</p>"

        response = client.post(f"/api/v1/tasks/{BASE}/comments/{comment_id}", json=whole)

        assert response.status_code == 200, response.text
        assert response.json()["author"]["id"] == ALICE
