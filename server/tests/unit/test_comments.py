"""T30: task comments.

Expected values come from a live Go reference server (probes 1, 2, 4, 8, 10, 11 in this
task's scratchpad), not from ``pkg/models/task_comments.go``. The parity corpus
``harness/corpus/_comments.yaml`` measured 19 of them independently; the cases here that
are **not** in the corpus are called out on the test, because those are the ones with no
second opinion behind them.

The tests worth having are the ones that fail when someone tidies this resource up: the
two 403 bodies that differ between list and read-one, the comment lookup that is scoped to
its task, and — most of all — the write gate, which is an **and** of two conditions that
each look sufficient on their own.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from tests.unit.conftest import ALICE, BOB, PROJECT

from calton.models import ProjectUser, TaskComment, User

#: Write access to PROJECT, and the author of BOBS_COMMENT.
#: Alice owns the project and still cannot touch that comment.
CAROL = 902
#: **Read-only** on PROJECT, and the author of DAVES_COMMENT. This is the sample that
#: separates "author" from "author *and* may write"; without a user in this state the two
#: readings of the gate agree on every row in the fixture and neither test can fail.
DAVE = 903

TASK = 920
EMPTY_TASK = 923
FORBIDDEN_TASK = 927  # bob's, in bob's project

ALICES_COMMENT = 800
BOBS_COMMENT = 801
ALICES_SECOND = 802
DAVES_COMMENT = 803
FORBIDDEN_COMMENT = 804  # on FORBIDDEN_TASK

MISSING_TASK = 99999
MISSING_COMMENT = 99999

EPOCH = "2026-02-01T00:00:00Z"
ZERO = "0001-01-01T00:00:00Z"

INVALID_DATA = {
    "code": 2002,
    "message": "Invalid Data",
    "invalid_fields": ["comment: non zero value required"],
}


@pytest.fixture(autouse=True)
def comment_fixture(sessions: sessionmaker[Session]) -> None:
    """Four comments on TASK plus one on bob's private task.

    Authors are deliberately interleaved — alice, bob, alice — so that a listing sorted by
    author id would return the same *set* in a different order, which is the only thing an
    ordered assertion can catch.
    """
    epoch = datetime(2026, 2, 1, tzinfo=UTC)
    with sessions() as session:
        session.add_all(
            [
                User(id=CAROL, username="carol", created=epoch, updated=epoch),
                User(id=DAVE, username="dave", created=epoch, updated=epoch),
                ProjectUser(user_id=BOB, project_id=PROJECT, permission=1),
                ProjectUser(user_id=CAROL, project_id=PROJECT, permission=2),
                # Read, not write. The whole point of this row.
                ProjectUser(user_id=DAVE, project_id=PROJECT, permission=0),
            ]
        )
        session.add_all(
            [
                TaskComment(
                    id=ALICES_COMMENT,
                    task_id=TASK,
                    author_id=ALICE,
                    comment="<p>alice one</p>",
                    created=epoch,
                    updated=epoch,
                ),
                TaskComment(
                    id=BOBS_COMMENT,
                    task_id=TASK,
                    author_id=BOB,
                    comment="<p>bob</p>",
                    created=epoch,
                    updated=epoch,
                ),
                TaskComment(
                    id=ALICES_SECOND,
                    task_id=TASK,
                    author_id=ALICE,
                    comment="<p>alice two</p>",
                    created=epoch,
                    updated=epoch,
                ),
                TaskComment(
                    id=DAVES_COMMENT,
                    task_id=TASK,
                    author_id=DAVE,
                    comment="<p>dave</p>",
                    created=epoch,
                    updated=epoch,
                ),
                TaskComment(
                    id=FORBIDDEN_COMMENT,
                    task_id=FORBIDDEN_TASK,
                    author_id=BOB,
                    comment="<p>private</p>",
                    created=epoch,
                    updated=epoch,
                ),
            ]
        )
        session.commit()


def as_user(client: TestClient, user_id: int) -> dict[str, str]:
    return {"X-Test-User": str(user_id)}


class TestRead:
    def test_the_list_is_in_row_order_not_grouped_by_author(self, client: TestClient) -> None:
        """[800, 801, 802, 803] — bob's 801 stays between alice's two.

        Ordering by ``author_id`` would give [800, 802, 803, 801]: same set, same status,
        different order. Only an ordered assertion on interleaved data notices, which is
        why the fixture interleaves.
        """
        response = client.get(f"/api/v1/tasks/{TASK}/comments")

        assert response.status_code == 200
        assert [entry["id"] for entry in response.json()] == [
            ALICES_COMMENT,
            BOBS_COMMENT,
            ALICES_SECOND,
            DAVES_COMMENT,
        ]

    def test_a_comment_carries_exactly_six_keys_and_a_null_reactions(
        self, client: TestClient
    ) -> None:
        """No ``task_id``, and ``reactions`` present as null rather than absent.

        ``reactions`` has no ``omitempty`` upstream, so it is not one of the expand-only
        fields that disappear — giving it a ``{}`` default (the natural Python spelling)
        or dropping the key are both wire differences.
        """
        response = client.get(f"/api/v1/tasks/{TASK}/comments/{ALICES_COMMENT}")

        assert response.status_code == 200
        assert response.json() == {
            "id": ALICES_COMMENT,
            "comment": "<p>alice one</p>",
            "author": {
                "id": ALICE,
                "name": "",
                "username": "alice",
                "created": EPOCH,
                "updated": EPOCH,
            },
            "reactions": None,
            "created": EPOCH,
            "updated": EPOCH,
        }

    def test_read_one_sends_x_max_permission(self, client: TestClient) -> None:
        """ReadOne goes through the generic web handler, which attaches the header. The
        value is the permission on the task's *project* — comments have none of their own.
        """
        response = client.get(f"/api/v1/tasks/{TASK}/comments/{ALICES_COMMENT}")

        assert response.headers["x-max-permission"] == "2"
        assert response.headers["access-control-expose-headers"] == "x-max-permission"

    def test_a_comment_cannot_be_read_through_another_tasks_path(self, client: TestClient) -> None:
        """The comment exists; the task in the URL is not the one it belongs to → 404.

        Looking a comment up by id alone is the obvious shortcut, answers 200 here, and
        turns any comment id into a readable oracle for tasks the caller cannot open.
        """
        response = client.get(f"/api/v1/tasks/{EMPTY_TASK}/comments/{ALICES_COMMENT}")

        assert response.status_code == 404
        assert response.json() == {"code": 4015, "message": "This task comment does not exist"}

    def test_the_two_forbidden_bodies_differ_between_list_and_read_one(
        self, client: TestClient
    ) -> None:
        """Same task, same overreach, two different codes *and* two different messages.

        This is the assertion that fails the moment someone factors the two authorisation
        checks into one helper — which is the correct-looking refactor and a wire change.
        """
        listing = client.get(f"/api/v1/tasks/{FORBIDDEN_TASK}/comments")
        one = client.get(f"/api/v1/tasks/{FORBIDDEN_TASK}/comments/{FORBIDDEN_COMMENT}")

        assert listing.status_code == 403
        assert listing.json() == {"code": 1, "message": "You're not allowed to do this."}
        assert one.status_code == 403
        assert one.json() == {"code": 0, "message": "You don't have the permission to see this"}

    def test_a_missing_task_says_task_not_project(self, client: TestClient) -> None:
        """4002, not the 3001 the *assignee* endpoints answer for the same mistake."""
        response = client.get(f"/api/v1/tasks/{MISSING_TASK}/comments")

        assert response.status_code == 404
        assert response.json() == {"code": 4002, "message": "This task does not exist"}

    def test_a_task_with_no_comments_is_an_empty_array(self, client: TestClient) -> None:
        response = client.get(f"/api/v1/tasks/{EMPTY_TASK}/comments")

        assert response.status_code == 200
        assert response.json() == []
        assert response.headers["x-pagination-result-count"] == "0"
        assert response.headers["x-pagination-total-pages"] == "0"


class TestCreate:
    def test_the_created_comment_is_fully_hydrated(self, client: TestClient) -> None:
        """201 with a real author object and real timestamps — the opposite of the update
        response two classes down. Same entity, two shapes."""
        response = client.put(f"/api/v1/tasks/{TASK}/comments", json={"comment": "<p>new</p>"})

        assert response.status_code == 201
        body = response.json()
        assert body["comment"] == "<p>new</p>"
        assert body["author"]["id"] == ALICE
        assert body["reactions"] is None
        assert body["created"] != ZERO
        assert body["id"] not in (0, ALICES_COMMENT)

    def test_an_empty_comment_is_412_with_invalid_fields(self, client: TestClient) -> None:
        """Not 400, and not pydantic's 422. The status, the ``invalid_fields`` key and the
        exact ``"<field>: <constraint>"` wording are three separate things to get wrong.
        """
        response = client.put(f"/api/v1/tasks/{TASK}/comments", json={"comment": ""})

        assert response.status_code == 412
        assert response.json() == INVALID_DATA

    def test_a_missing_comment_key_gives_the_identical_412(self, client: TestClient) -> None:
        """Absent and empty are one case upstream, because Go decodes the missing key to
        the zero value and validates afterwards. In Pydantic they are two code paths, and
        the default is not validated unless ``validate_default`` says so — without it this
        body answers 201.
        """
        response = client.put(f"/api/v1/tasks/{TASK}/comments", json={})

        assert response.status_code == 412
        assert response.json() == INVALID_DATA

    def test_validation_runs_before_the_task_is_looked_up(self, client: TestClient) -> None:
        """Empty comment **and** a task that does not exist → 412, not 404.

        Neither single-error case can tell the two orders apart; only this double fault
        can. It also means an empty body never reveals whether a task id exists.
        """
        response = client.put(f"/api/v1/tasks/{MISSING_TASK}/comments", json={"comment": ""})

        assert response.status_code == 412
        assert response.json() == INVALID_DATA

    def test_a_valid_body_on_a_missing_task_is_404(self, client: TestClient) -> None:
        response = client.put(f"/api/v1/tasks/{MISSING_TASK}/comments", json={"comment": "x"})

        assert response.status_code == 404
        assert response.json() == {"code": 4002, "message": "This task does not exist"}

    def test_commenting_on_a_forbidden_task_is_403_code_0(self, client: TestClient) -> None:
        response = client.put(f"/api/v1/tasks/{FORBIDDEN_TASK}/comments", json={"comment": "x"})

        assert response.status_code == 403
        assert response.json() == {"code": 0, "message": "Forbidden"}

    def test_read_only_members_cannot_comment(self, client: TestClient) -> None:
        """Dave holds read on the project. Creating needs write."""
        response = client.put(
            f"/api/v1/tasks/{TASK}/comments",
            json={"comment": "x"},
            headers=as_user(client, DAVE),
        )

        assert response.status_code == 403

    def test_a_body_id_is_ignored_on_create(self, client: TestClient) -> None:
        """``Create`` sets ``tc.ID = 0`` before inserting — even though the same field
        *does* override the path segment on update. Not covered by the corpus."""
        response = client.put(
            f"/api/v1/tasks/{TASK}/comments", json={"id": ALICES_COMMENT, "comment": "<p>x</p>"}
        )

        assert response.status_code == 201
        assert response.json()["id"] != ALICES_COMMENT

    def test_a_non_string_comment_is_a_bind_failure_not_a_validation_one(
        self, client: TestClient
    ) -> None:
        """400/2004, not the 412 an empty string gets. Upstream splits "encoding/json
        refused it" from "it decoded and failed validation", and the two have different
        bodies."""
        response = client.put(f"/api/v1/tasks/{TASK}/comments", json={"comment": 5})

        assert response.status_code == 400
        assert response.json() == {"code": 2004, "message": "Invalid model provided: Bad Request"}

    def test_the_dbtext_bound_is_bytes_not_characters(self, client: TestClient) -> None:
        """1 048 576 **bytes** is the limit on sqlite, and Go's ``len`` counts bytes.

        Not covered by the corpus. The multibyte half is the point: 400 000 CJK characters
        is well under the limit as a character count and over it as a byte count, and a
        ``len(str)`` implementation accepts it.
        """
        at_limit = client.put(f"/api/v1/tasks/{TASK}/comments", json={"comment": "a" * 1048576})
        over = client.put(f"/api/v1/tasks/{TASK}/comments", json={"comment": "a" * 1048577})
        multibyte = client.put(f"/api/v1/tasks/{TASK}/comments", json={"comment": "中" * 400000})

        assert at_limit.status_code == 201
        assert over.status_code == 412
        assert over.json()["invalid_fields"][0].startswith("comment: aaa")
        assert over.json()["invalid_fields"][0].endswith("does not validate as dbtext")
        assert multibyte.status_code == 412


class TestTheWriteGateIsAnAnd:
    """``CanWrite(task) AND is-author``. Each half alone looks like the whole rule.

    The corpus only covers the second test here. The first — an author who may not write —
    has no corpus case and no second opinion, so it says where it was measured.
    """

    def test_an_author_who_may_not_write_the_task_is_refused(self, client: TestClient) -> None:
        """Dave wrote comment 803 and holds **read** on the project → 403 on both verbs.

        Measured on the reference server by seeding exactly this state (probe 4); there is
        no corpus case. Dropping the write half of the gate — the obvious reading of "only
        the author may edit" — turns both of these into 200s, and a collaborator demoted to
        read-only keeps editing their old comments forever.
        """
        headers = as_user(client, DAVE)
        update = client.post(
            f"/api/v1/tasks/{TASK}/comments/{DAVES_COMMENT}",
            json={"comment": "<p>edited</p>"},
            headers=headers,
        )
        delete = client.delete(f"/api/v1/tasks/{TASK}/comments/{DAVES_COMMENT}", headers=headers)

        assert update.status_code == 403
        assert update.json() == {"code": 0, "message": "Forbidden"}
        assert delete.status_code == 403

    def test_the_project_owner_cannot_touch_someone_elses_comment(self, client: TestClient) -> None:
        """Alice owns the project. Carol is an admin. Neither may edit bob's comment.

        Folding comments into the project permission model is the natural unification and
        the one product will ask for; it makes both of these 200 and lets an administrator
        rewrite another person's words with nothing in the response to show for it.
        """
        as_owner = client.post(
            f"/api/v1/tasks/{TASK}/comments/{BOBS_COMMENT}", json={"comment": "<p>hijack</p>"}
        )
        as_admin = client.delete(
            f"/api/v1/tasks/{TASK}/comments/{BOBS_COMMENT}", headers=as_user(client, CAROL)
        )

        assert as_owner.status_code == 403
        assert as_admin.status_code == 403

    def test_an_author_with_write_but_no_ownership_of_the_project_succeeds(
        self, client: TestClient
    ) -> None:
        """Bob: not the owner, holds write, wrote the comment → 200.

        This and the previous test are a pair. With only one of them, "check the project
        permission" and "check the author" both pass.
        """
        response = client.post(
            f"/api/v1/tasks/{TASK}/comments/{BOBS_COMMENT}",
            json={"comment": "<p>bob edits his own</p>"},
            headers=as_user(client, BOB),
        )

        assert response.status_code == 200
        assert response.json()["comment"] == "<p>bob edits his own</p>"


class TestUpdate:
    def test_the_response_drops_author_and_created_when_the_client_sent_none(
        self, client: TestClient
    ) -> None:
        """``author: null`` and ``created`` at the zero time, while a GET of the same
        comment returns both in full.

        Upstream serialises the struct it bound the request into instead of re-reading the
        row. Returning the complete updated entity is more useful and is a wire
        difference on two fields.
        """
        response = client.post(
            f"/api/v1/tasks/{TASK}/comments/{ALICES_COMMENT}", json={"comment": "<p>edited</p>"}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["author"] is None
        assert body["created"] == ZERO
        assert body["updated"] != ZERO
        assert set(body) == {"id", "comment", "author", "reactions", "created", "updated"}

        stored = client.get(f"/api/v1/tasks/{TASK}/comments/{ALICES_COMMENT}").json()
        assert stored["author"]["id"] == ALICE
        assert stored["created"] == EPOCH

    def test_a_read_modify_write_client_gets_its_own_values_echoed_back(
        self, client: TestClient
    ) -> None:
        """The null above is **not** a constant.

        Measured: hand the whole object back with one field changed and ``author`` and
        ``created`` come back as sent. Not covered by the corpus, which only ever sends
        ``{"comment": ...}`` — so an implementation that hard-codes the null passes every
        corpus case and disagrees with upstream for every real client (design R4/RMW).
        """
        whole = client.get(f"/api/v1/tasks/{TASK}/comments/{ALICES_COMMENT}").json()
        whole["comment"] = "<p>rmw</p>"

        response = client.post(f"/api/v1/tasks/{TASK}/comments/{ALICES_COMMENT}", json=whole)

        assert response.status_code == 200
        assert response.json()["author"] == whole["author"]
        assert response.json()["created"] == EPOCH

    def test_a_body_id_overrides_the_path_segment(self, client: TestClient) -> None:
        """Echo binds path parameters before the body, so the body wins.

        Not covered by the corpus. The second half matters more than the first: the author
        check follows the **effective** id, so aiming a body id at someone else's comment
        is 403 rather than a silent hijack.
        """
        redirected = client.post(
            f"/api/v1/tasks/{TASK}/comments/{ALICES_COMMENT}",
            json={"id": ALICES_SECOND, "comment": "<p>redirected</p>"},
        )
        at_someone_elses = client.post(
            f"/api/v1/tasks/{TASK}/comments/{ALICES_COMMENT}",
            json={"id": BOBS_COMMENT, "comment": "<p>hijack</p>"},
        )

        assert redirected.status_code == 200
        assert redirected.json()["id"] == ALICES_SECOND
        assert (
            client.get(f"/api/v1/tasks/{TASK}/comments/{ALICES_SECOND}").json()["comment"]
            == "<p>redirected</p>"
        )
        # …and the one named in the path is untouched.
        assert (
            client.get(f"/api/v1/tasks/{TASK}/comments/{ALICES_COMMENT}").json()["comment"]
            == "<p>alice one</p>"
        )
        assert at_someone_elses.status_code == 403

    def test_validation_still_applies_on_the_update_path(self, client: TestClient) -> None:
        """Validating only on create is a common omission and would let a user blank an
        existing comment."""
        response = client.post(
            f"/api/v1/tasks/{TASK}/comments/{ALICES_COMMENT}", json={"comment": ""}
        )

        assert response.status_code == 412
        assert response.json() == INVALID_DATA

    def test_validation_beats_every_other_error(self, client: TestClient) -> None:
        """Empty body against a forbidden task and a missing comment — still 412.

        Not covered by the corpus for the update verb.
        """
        forbidden = client.post(
            f"/api/v1/tasks/{FORBIDDEN_TASK}/comments/{FORBIDDEN_COMMENT}", json={"comment": ""}
        )
        missing = client.post(
            f"/api/v1/tasks/{TASK}/comments/{MISSING_COMMENT}", json={"comment": ""}
        )

        assert forbidden.status_code == 412
        assert missing.status_code == 412

    def test_updating_a_missing_comment_is_404(self, client: TestClient) -> None:
        """404/4015 for both read and write, unlike labels, where the two differ."""
        response = client.post(
            f"/api/v1/tasks/{TASK}/comments/{MISSING_COMMENT}", json={"comment": "x"}
        )

        assert response.status_code == 404
        assert response.json() == {"code": 4015, "message": "This task comment does not exist"}


class TestDelete:
    def test_deleting_own_comment_returns_a_message_and_removes_the_row(
        self, client: TestClient
    ) -> None:
        """``{"message": ...}``, not the deleted resource and not an empty 204."""
        response = client.delete(f"/api/v1/tasks/{TASK}/comments/{ALICES_SECOND}")

        assert response.status_code == 200
        assert response.json() == {"message": "Successfully deleted."}
        assert [entry["id"] for entry in client.get(f"/api/v1/tasks/{TASK}/comments").json()] == [
            ALICES_COMMENT,
            BOBS_COMMENT,
            DAVES_COMMENT,
        ]

    def test_deleting_a_missing_comment_is_404_not_an_idempotent_200(
        self, client: TestClient
    ) -> None:
        """The association family has three different answers to "remove something that is
        not there": labels 403, assignees 200, comments 404. Any unification breaks two.
        """
        response = client.delete(f"/api/v1/tasks/{TASK}/comments/{MISSING_COMMENT}")

        assert response.status_code == 404
        assert response.json() == {"code": 4015, "message": "This task comment does not exist"}

    def test_the_task_gate_runs_before_the_comment_lookup(self, client: TestClient) -> None:
        """A forbidden task with a comment id that does not exist is 403, not 404.

        Not covered by the corpus. The other order would let a caller probe which comment
        ids exist on a task they cannot see.
        """
        response = client.delete(f"/api/v1/tasks/{FORBIDDEN_TASK}/comments/{MISSING_COMMENT}")

        assert response.status_code == 403
        assert response.json() == {"code": 0, "message": "Forbidden"}

    def test_a_comment_cannot_be_deleted_through_another_tasks_path(
        self, client: TestClient
    ) -> None:
        response = client.delete(f"/api/v1/tasks/{EMPTY_TASK}/comments/{ALICES_COMMENT}")

        assert response.status_code == 404
        assert client.get(f"/api/v1/tasks/{TASK}/comments/{ALICES_COMMENT}").status_code == 200
