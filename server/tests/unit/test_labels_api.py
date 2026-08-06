"""T25 — the label endpoints over HTTP.

**These go through the real app**, not through the service layer. That is the point: the
existing label unit tests assert against ``LabelService`` directly, and a test shaped that
way cannot see anything that happens between the router and the serialiser — a route
bound to the wrong verb, a response model that drops a field, a path parameter parsed as
the wrong type, a policy whose refusal never reaches an HTTP status. Every one of those is
a class of bug the corpus exists to catch, so the assertions here are stated at the same
layer the corpus states them.

The seed mirrors ``harness/seed/overlay/assoc.yml`` — same ids, same owners, same
attachments — so a case here and the corpus case it is named after describe one world.
The five labels are not interchangeable and the reason each exists is on its line.

Expected values are the reference server's, taken either from ``corpus/_labels.yaml``
(measured by someone who was not implementing this) or from a probe run against the same
binary the harness uses, noted per assertion. None of them was derived from this
implementation, and none was derived by reading the Go source.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from calton.models import Label, LabelTask, Project, ProjectUser, Task

ALICE, BOB = 900, 901

#: Alice's project, and bob's (seeded by the shared conftest as ``BOBS_PROJECT``).
ASSOC_PROJECT = 950
BOBS_PROJECT = 903

#: Alice's tasks. 950 carries labels, 951 carries none — the pair that separates
#: "returns the attached set" from "returns everything".
TASK_WITH_LABELS = 950
TASK_WITHOUT_LABELS = 951
#: Bob's private task. Alice holds nothing on its project.
BOBS_TASK = 927

ZERO = "0001-01-01T00:00:00Z"
EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def labels_seed(sessions: sessionmaker[Session]) -> None:
    """assoc.yml's label world.

    Each label is here to break a specific wrong implementation:

    * **950, 951** alice's, attached to her task 950. 951 has *both* optional fields set,
      which is what lets the full-replacement case observe two independent resets rather
      than one.
    * **952** alice's and attached to nothing — the floating label. Only "labels you
      created" finds it; an implementation that scopes purely by attachment loses it.
    * **953** bob's, attached only to bob's private task. Alice can neither read it nor
      use it. This is the sample that makes the visibility rule testable at all — without
      an object alice does not own, the readable set and the writable set coincide and
      every permission assertion here becomes a tautology.
    * **954** bob's, attached to *alice's* task 950. Readable and usable by alice, not
      editable by her. This single row is what splits "read/use" from "edit/delete"; drop
      it and an implementation that gates everything on ownership passes the whole file.
    """
    with sessions() as session:
        session.add(Project(id=ASSOC_PROJECT, title="AssocHost", identifier="AS", owner_id=ALICE))
        # Bob holds write on alice's project, exactly as assoc.yml grants it. This is what
        # makes him able to *see* alice's labels 950/951 without having created them, and
        # therefore what makes the two-user visibility pair below decide anything: with no
        # share, both users see only their own labels and the union rule is untested.
        session.add(ProjectUser(id=950, project_id=ASSOC_PROJECT, user_id=BOB, permission=1))
        session.add_all(
            [
                Task(
                    id=TASK_WITH_LABELS,
                    project_id=ASSOC_PROJECT,
                    index=1,
                    title="A-with-everything",
                    created_by_id=ALICE,
                    done=False,
                    created=EPOCH,
                    updated=EPOCH,
                ),
                Task(
                    id=TASK_WITHOUT_LABELS,
                    project_id=ASSOC_PROJECT,
                    index=2,
                    title="B-bare",
                    created_by_id=ALICE,
                    done=False,
                    created=EPOCH,
                    updated=EPOCH,
                ),
            ]
        )
        session.add_all(
            [
                Label(
                    id=950,
                    title="X-alpha",
                    created_by_id=ALICE,
                    hex_color="e8e8e8",
                    created=EPOCH,
                    updated=EPOCH,
                ),
                Label(
                    id=951,
                    title="X-beta",
                    created_by_id=ALICE,
                    hex_color="ff0000",
                    description="X-beta description",
                    created=EPOCH,
                    updated=EPOCH,
                ),
                # description and hex_color unset: proves they serialise as "" and not null.
                Label(id=952, title="X-gamma", created_by_id=ALICE, created=EPOCH, updated=EPOCH),
                Label(id=953, title="X-bobs", created_by_id=BOB, created=EPOCH, updated=EPOCH),
                Label(
                    id=954, title="X-bobs-shared", created_by_id=BOB, created=EPOCH, updated=EPOCH
                ),
            ]
        )
        session.add_all(
            [
                LabelTask(id=950, task_id=TASK_WITH_LABELS, label_id=950, created=EPOCH),
                LabelTask(id=951, task_id=TASK_WITH_LABELS, label_id=951, created=EPOCH),
                LabelTask(id=952, task_id=BOBS_TASK, label_id=953, created=EPOCH),
                LabelTask(id=953, task_id=TASK_WITH_LABELS, label_id=954, created=EPOCH),
            ]
        )
        session.commit()


@pytest.fixture
def alice(client: TestClient, labels_seed: None) -> TestClient:
    return client


@pytest.fixture
def bob(alice: TestClient) -> Iterator[TestClient]:
    alice.headers["X-Test-User"] = str(BOB)
    yield alice
    alice.headers["X-Test-User"] = str(ALICE)


def attached_ids(sessions: sessionmaker[Session], task_id: int) -> list[int]:
    """What the database holds, read independently of any response body.

    Several cases here cannot be decided from the response: a bulk call that does nothing
    still answers with a body that looks correct. Those assertions have to come from here.
    """
    with sessions() as session:
        rows = session.scalars(
            select(LabelTask.label_id)
            .where(LabelTask.task_id == task_id)
            .order_by(LabelTask.label_id)
        )
        return list(rows)


def body(response: Any) -> Any:
    assert response.status_code < 500, response.text
    return response.json()


# ======================================================================================
# GET /tasks/{task}/labels
# ======================================================================================


def test_it_lists_every_label_on_the_task_whoever_attached_it(alice: TestClient) -> None:
    """Corpus ``tasklabel.read_all.ok`` — [950, 951, 954].

    954 is **bob's** label on alice's task, and its presence is the whole assertion: an
    implementation that filters this list by ``created_by`` (a natural reading of "labels
    I can edit") returns [950, 951] and makes a collaborator's labels vanish from a shared
    task with no error anywhere.
    """
    response = alice.get(f"/api/v1/tasks/{TASK_WITH_LABELS}/labels")
    assert response.status_code == 200, response.text
    assert [label["id"] for label in response.json()] == [950, 951, 954]


def test_an_unlabelled_task_answers_an_empty_array_not_null(alice: TestClient) -> None:
    """Corpus ``tasklabel.read_all.empty_is_array_not_null``.

    ⚠️ The *same* emptiness inside ``GET /tasks/{id}`` serialises as ``labels: null`` —
    measured, and asserted in ``test_tasks_api``. One "empty", two shapes. The natural
    Python spelling (one ``labels: list[Label] = []`` model reused in both places)
    silently unifies them and kills the frontend's ``task.labels === null`` branch.
    """
    response = alice.get(f"/api/v1/tasks/{TASK_WITHOUT_LABELS}/labels")
    assert response.status_code == 200, response.text
    assert response.json() == []


def test_the_empty_list_still_carries_pagination_headers(alice: TestClient) -> None:
    """Probe-measured: result-count 0 and total-pages **0**, not 1.

    ``compare.py`` diffs headers byte for byte, so a route that answers the right body
    with no headers fails parity for a reason that has nothing to do with labels.
    """
    response = alice.get(f"/api/v1/tasks/{TASK_WITHOUT_LABELS}/labels")
    assert response.headers["x-pagination-result-count"] == "0"
    assert response.headers["x-pagination-total-pages"] == "0"


def test_listing_labels_on_a_task_you_cannot_see_is_the_tasks_own_403(alice: TestClient) -> None:
    """Corpus ``tasklabel.read_all.forbidden_task`` — 403 **4005**, not 8002 and not 0.

    The refusal comes from the outer task check; the label layer never runs. Its sibling
    below (bulk on the same task) answers 403 **0** instead, so these two codes must not be
    unified even though the user, the task and the missing permission are identical.
    """
    assert body(alice.get(f"/api/v1/tasks/{BOBS_TASK}/labels")) == {
        "code": 4005,
        "message": "You don't have the permission to see this task.",
    }


def test_listing_labels_on_a_missing_task_is_404(alice: TestClient) -> None:
    """Corpus ``tasklabel.read_all.missing_task`` — 404/4002.

    Paired with the case above: the *task* layer does disclose existence, while the label
    read path deliberately does not. Opposite rules, one request.
    """
    response = alice.get("/api/v1/tasks/99999/labels")
    assert response.status_code == 404
    assert response.json() == {"code": 4002, "message": "This task does not exist"}


# ======================================================================================
# PUT /tasks/{task}/labels
# ======================================================================================


def test_attaching_answers_exactly_two_keys(alice: TestClient) -> None:
    """Corpus ``tasklabel.add.ok`` — ``{label_id, created}`` and nothing else.

    ``body_keys_exactly`` is the assertion that matters. Hydrating the label here would be
    more useful, would break no client feature, and is wrong; only an exact key comparison
    catches it, which is why this does not merely check the status.
    """
    response = alice.put(f"/api/v1/tasks/{TASK_WITH_LABELS}/labels", json={"label_id": 952})
    assert response.status_code == 201, response.text
    payload = response.json()
    assert set(payload) == {"label_id", "created"}
    assert payload["label_id"] == 952
    assert payload["created"] != ZERO


def test_a_label_you_can_see_but_did_not_create_can_be_attached(alice: TestClient) -> None:
    """Corpus ``tasklabel.add.readable_others_label_ok`` — 201.

    Visible is usable; being the creator is not required. This is the *only* case that
    fails when an implementation narrows "use" to ownership —
    ``test_updating_someone_elses_label_is_403`` below stays green either way, because
    that one is supposed to be refused. Without this case, tightening the rule is an
    invisible change that breaks shared labels in every collaborative project.

    Task 951 rather than 950: 954 is already on 950, so that combination would answer the
    duplicate 8001 and test something else entirely.
    """
    response = alice.put(f"/api/v1/tasks/{TASK_WITHOUT_LABELS}/labels", json={"label_id": 954})
    assert response.status_code == 201, response.text
    assert set(response.json()) == {"label_id", "created"}


def test_attaching_a_label_you_cannot_see_is_403(alice: TestClient) -> None:
    """Corpus ``tasklabel.add.invisible_label_403``.

    The other half of "visible is usable": 954 is visible and attaches, 953 is not and is
    refused. Either case alone is satisfied by an implementation that allows everything or
    one that allows nothing.
    """
    assert body(alice.put(f"/api/v1/tasks/{TASK_WITH_LABELS}/labels", json={"label_id": 953})) == {
        "code": 0,
        "message": "Forbidden",
    }


def test_attaching_the_same_label_twice_is_400_with_its_own_code(alice: TestClient) -> None:
    """Corpus ``tasklabel.add.duplicate_400`` — 400/8001, not a silently idempotent 201."""
    response = alice.put(f"/api/v1/tasks/{TASK_WITH_LABELS}/labels", json={"label_id": 950})
    assert response.status_code == 400
    assert response.json() == {"code": 8001, "message": "This label already exists on the task."}


def test_attaching_a_label_that_does_not_exist_is_404(alice: TestClient) -> None:
    """Corpus ``tasklabel.add.missing_label_404`` — 404/8002."""
    response = alice.put(f"/api/v1/tasks/{TASK_WITH_LABELS}/labels", json={"label_id": 99999})
    assert response.status_code == 404
    assert response.json() == {"code": 8002, "message": "This label does not exist."}


def test_an_empty_body_is_403_where_a_missing_id_is_404(alice: TestClient) -> None:
    """Corpus ``tasklabel.add.label_id_zero_is_403_not_404``.

    Both requests name a label that is not there, and they answer differently. Asserted as
    a **pair in one test** on purpose: separately, an implementation that unified them
    would fail one case and could be "fixed" by editing that case. Together, the pair is
    the claim.

    Two natural Python spellings break this: marking ``label_id`` required (FastAPI then
    answers 412 before any of this runs) and normalising a missing field to the ordinary
    missing-id path (404).
    """
    zero = alice.put(f"/api/v1/tasks/{TASK_WITH_LABELS}/labels", json={})
    missing = alice.put(f"/api/v1/tasks/{TASK_WITH_LABELS}/labels", json={"label_id": 99999})

    assert (zero.status_code, zero.json()) == (403, {"code": 0, "message": "Forbidden"})
    assert (missing.status_code, missing.json()) == (
        404,
        {"code": 8002, "message": "This label does not exist."},
    )


def test_a_negative_label_id_takes_the_ordinary_missing_path(alice: TestClient) -> None:
    """Probe-measured: ``label_id: -5`` is 404/8002, unlike 0's 403.

    Only zero is special — it is the value upstream's ORM turns into "no condition at
    all". A guard written as ``label_id < 1`` would swallow negatives too and turn this
    404 into a 403.
    """
    response = alice.put(f"/api/v1/tasks/{TASK_WITH_LABELS}/labels", json={"label_id": -5})
    assert response.status_code == 404
    assert response.json() == {"code": 8002, "message": "This label does not exist."}


def test_the_label_is_checked_before_the_task(alice: TestClient) -> None:
    """Probe-measured, and the pair is the point.

    Against a task alice cannot see: a **valid** label answers the task's 403, while a
    **missing** label answers 8002 — the label check ran first and disclosed that the
    label is absent even though the caller has no access to the task. Reordering to
    "task first" looks like a security improvement and changes the second status.
    """
    valid_label = alice.put(f"/api/v1/tasks/{BOBS_TASK}/labels", json={"label_id": 950})
    missing_label = alice.put(f"/api/v1/tasks/{BOBS_TASK}/labels", json={"label_id": 99999})

    assert (valid_label.status_code, valid_label.json()) == (
        403,
        {"code": 0, "message": "Forbidden"},
    )
    assert missing_label.status_code == 404
    assert missing_label.json() == {"code": 8002, "message": "This label does not exist."}


def test_attaching_to_a_missing_task_is_the_tasks_404(alice: TestClient) -> None:
    """Probe-measured: 404/4002, reached after the label passed its own checks."""
    response = alice.put("/api/v1/tasks/99999/labels", json={"label_id": 950})
    assert response.status_code == 404
    assert response.json() == {"code": 4002, "message": "This task does not exist"}


def test_a_non_integer_label_id_is_400_not_412(alice: TestClient) -> None:
    """``{"label_id": "952"}`` is a decode failure upstream, so 400/2004.

    Pydantic's default lax mode coerces the string and attaches the label — a write the
    caller's server would have refused. ``strict=True`` on the schema is what makes this a
    400; without it this test is the only thing that would notice.
    """
    response = alice.put(f"/api/v1/tasks/{TASK_WITH_LABELS}/labels", json={"label_id": "952"})
    assert response.status_code == 400
    assert response.json()["code"] == 2004


# ======================================================================================
# DELETE /tasks/{task}/labels/{label}
# ======================================================================================


def test_detaching_answers_the_generic_delete_message(alice: TestClient) -> None:
    """Corpus ``tasklabel.remove.ok``."""
    response = alice.delete(f"/api/v1/tasks/{TASK_WITH_LABELS}/labels/950")
    assert response.status_code == 200, response.text
    assert response.json() == {"message": "Successfully deleted."}


def test_detaching_actually_removes_the_row(
    alice: TestClient, sessions: sessionmaker[Session]
) -> None:
    """The response above is a fixed string and would be identical if nothing happened."""
    before = attached_ids(sessions, TASK_WITH_LABELS)
    alice.delete(f"/api/v1/tasks/{TASK_WITH_LABELS}/labels/950")
    assert before == [950, 951, 954]
    assert attached_ids(sessions, TASK_WITH_LABELS) == [951, 954]


def test_detaching_twice_is_403_not_404_and_not_idempotent(alice: TestClient) -> None:
    """Corpus ``tasklabel.remove.twice_is_403``.

    The three implementations a reasonable person would write — idempotent 200, 404, 400 —
    are all wrong. Upstream tests "does this attachment exist" inside the permission
    callback, so "there is no such attachment" leaves as "you are not allowed". Nobody
    writing this endpoint thinks to delete twice, which is why the corpus says so out loud.
    """
    first = alice.delete(f"/api/v1/tasks/{TASK_WITH_LABELS}/labels/950")
    second = alice.delete(f"/api/v1/tasks/{TASK_WITH_LABELS}/labels/950")

    assert first.status_code == 200
    assert (second.status_code, second.json()) == (403, {"code": 0, "message": "Forbidden"})


def test_detaching_a_label_that_was_never_attached_is_403(alice: TestClient) -> None:
    """Corpus ``tasklabel.remove.not_attached_403``.

    952 exists and alice may use it; it simply is not on this task. Distinct from the case
    above, which detaches something that *was* there: together they say the test is the
    current attachment and not any record of history. An implementation that remembered
    deletions would pass one of them and not both.
    """
    assert body(alice.delete(f"/api/v1/tasks/{TASK_WITH_LABELS}/labels/952")) == {
        "code": 0,
        "message": "Forbidden",
    }


def test_detaching_never_asks_whether_the_label_exists(alice: TestClient) -> None:
    """A label id that exists nowhere answers the same 403 as one that merely is not
    attached — the existence of the label is not a question this endpoint asks."""
    assert body(alice.delete(f"/api/v1/tasks/{TASK_WITH_LABELS}/labels/99999")) == {
        "code": 0,
        "message": "Forbidden",
    }


def test_detaching_from_a_task_you_cannot_write_is_403(alice: TestClient) -> None:
    """Probe-measured: 403/0 — the generic code, not the read path's 4005."""
    assert body(alice.delete(f"/api/v1/tasks/{BOBS_TASK}/labels/953")) == {
        "code": 0,
        "message": "Forbidden",
    }


def test_detaching_from_a_missing_task_is_404(alice: TestClient) -> None:
    """Probe-measured: 404/4002."""
    response = alice.delete("/api/v1/tasks/99999/labels/950")
    assert response.status_code == 404
    assert response.json() == {"code": 4002, "message": "This task does not exist"}


# ======================================================================================
# POST /tasks/{task}/labels/bulk
# ======================================================================================


def test_bulk_replaces_the_whole_set_rather_than_appending(
    alice: TestClient, sessions: sessionmaker[Session]
) -> None:
    """Corpus ``tasklabel.bulk.replaces_whole_set``.

    950 starts with {950, 951, 954}; submitting [952, 950] must leave exactly {950, 952}.
    The starting set **must contain labels absent from the submission** — 951 and 954 are
    there for that and nothing else. Without them, "append" and "replace" produce the same
    final set and this case decides nothing.

    Read back from the database, not from the 201 body: that body is an echo of the
    request and would look right regardless.
    """
    response = alice.post(
        f"/api/v1/tasks/{TASK_WITH_LABELS}/labels/bulk",
        json={"labels": [{"id": 952}, {"id": 950}]},
    )
    assert response.status_code == 201, response.text
    assert attached_ids(sessions, TASK_WITH_LABELS) == [950, 952]


def test_bulk_echoes_the_request_unhydrated_and_in_request_order(alice: TestClient) -> None:
    """Corpus ``tasklabel.bulk.response_echoes_input_unhydrated``.

    Every field here is the *request's* value, not the database's: empty titles, null
    ``created_by``, zero timestamps, and [952, 950] in submitted order rather than the
    ascending order ``GET`` uses. Hydrating this is the change a conscientious implementer
    makes, produces a strictly more useful response, and is a byte-level regression.
    """
    response = alice.post(
        f"/api/v1/tasks/{TASK_WITH_LABELS}/labels/bulk",
        json={"labels": [{"id": 952}, {"id": 950}]},
    )
    assert response.json() == {
        "labels": [
            {
                "id": 952,
                "title": "",
                "description": "",
                "hex_color": "",
                "created_by": None,
                "created": ZERO,
                "updated": ZERO,
            },
            {
                "id": 950,
                "title": "",
                "description": "",
                "hex_color": "",
                "created_by": None,
                "created": ZERO,
                "updated": ZERO,
            },
        ]
    }


def test_bulk_with_an_empty_list_clears_every_label(
    alice: TestClient, sessions: sessionmaker[Session]
) -> None:
    """Corpus ``tasklabel.bulk.empty_clears_all``.

    The most dangerous case in the group. "Nothing to add, return early" is a natural
    optimisation, and it leaves the response body — ``{"labels": []}`` — exactly correct
    while silently doing nothing. Only the database side can tell the two apart, so the
    body assertion below is *not* what this test rests on.
    """
    assert attached_ids(sessions, TASK_WITH_LABELS) == [950, 951, 954]

    response = alice.post(f"/api/v1/tasks/{TASK_WITH_LABELS}/labels/bulk", json={"labels": []})

    assert response.status_code == 201, response.text
    assert response.json() == {"labels": []}
    assert attached_ids(sessions, TASK_WITH_LABELS) == []


def test_bulk_on_a_task_you_cannot_write_is_the_generic_403(alice: TestClient) -> None:
    """Corpus ``tasklabel.bulk.forbidden_task_403`` — 403 **0**.

    ⚠️ The read path on this same task answers 403 **4005**. Same task, same user, same
    missing permission, two codes. Asserted next to each other in this file so the
    difference is visible rather than looking like an inconsistency to tidy up.
    """
    assert body(alice.post(f"/api/v1/tasks/{BOBS_TASK}/labels/bulk", json={"labels": []})) == {
        "code": 0,
        "message": "Forbidden",
    }


def test_bulk_reports_an_unusable_label_with_its_own_code(alice: TestClient) -> None:
    """Probe-measured: 403 / **8003**, where ``PUT`` answers 403 / 0 for the same thing.

    Same user, same label, same reason — a different code depending on which endpoint
    asked. Nothing about either implementation makes this discoverable; it only exists
    because both were measured.
    """
    response = alice.post(
        f"/api/v1/tasks/{TASK_WITH_LABELS}/labels/bulk", json={"labels": [{"id": 953}]}
    )
    assert response.status_code == 403
    assert response.json() == {"code": 8003, "message": "You don't have access to this label."}


def test_bulk_naming_a_missing_label_is_404(alice: TestClient) -> None:
    """Probe-measured: 404/8002."""
    response = alice.post(
        f"/api/v1/tasks/{TASK_WITH_LABELS}/labels/bulk", json={"labels": [{"id": 99999}]}
    )
    assert response.status_code == 404
    assert response.json() == {"code": 8002, "message": "This label does not exist."}


def test_bulk_refusal_leaves_the_existing_set_untouched(
    alice: TestClient, sessions: sessionmaker[Session]
) -> None:
    """A rejected submission must not have deleted the labels it was going to replace.

    Upstream deletes first and validates while inserting, relying on the transaction to
    roll back. We validate first. Both must end here — and a half-applied replacement
    would be a silent data loss that no status code reports.
    """
    alice.post(f"/api/v1/tasks/{TASK_WITH_LABELS}/labels/bulk", json={"labels": [{"id": 953}]})
    assert attached_ids(sessions, TASK_WITH_LABELS) == [950, 951, 954]


def test_bulk_keeps_a_label_the_task_already_has_without_rechecking_it(
    alice: TestClient, sessions: sessionmaker[Session]
) -> None:
    """Probe-measured: resubmitting only 954 leaves {954} and answers 201.

    954 is bob's. Alice can see it *because* it is on this task, so re-checking access on
    an already-attached label would be circular — and upstream skips the check entirely
    for labels that are already there. An implementation that validates every submitted id
    unconditionally would still pass here (954 is visible to alice), so the load-bearing
    half of this assertion is the resulting set, not the status.
    """
    response = alice.post(
        f"/api/v1/tasks/{TASK_WITH_LABELS}/labels/bulk", json={"labels": [{"id": 954}]}
    )
    assert response.status_code == 201, response.text
    assert attached_ids(sessions, TASK_WITH_LABELS) == [954]


def test_bulk_on_a_missing_task_is_404(alice: TestClient) -> None:
    """Probe-measured: 404/4002."""
    response = alice.post("/api/v1/tasks/99999/labels/bulk", json={"labels": []})
    assert response.status_code == 404
    assert response.json() == {"code": 4002, "message": "This task does not exist"}


# ======================================================================================
# /labels — the CRUDRouter half, over HTTP rather than through the service
# ======================================================================================


def test_the_visible_set_is_own_labels_union_labels_on_visible_tasks(alice: TestClient) -> None:
    """Corpus ``label.read_all.ok``, restricted to this seed's ids.

    Three boundaries, one per wrong implementation:

    * **954** present — bob's, but on a task alice can see. "Only my own" loses it.
    * **952** present — alice's, attached to nothing. "Only what is on a visible task"
      loses it.
    * **953** absent — bob's, only on bob's private task.

    Remove any one and the case degrades into "returns some labels".
    """
    response = alice.get("/api/v1/labels")
    assert response.status_code == 200, response.text
    assert sorted(label["id"] for label in response.json()) == [950, 951, 952, 954]


def test_the_same_seed_gives_a_different_user_a_different_set(bob: TestClient) -> None:
    """Corpus ``label.read_all.other_user_sees_via_shared_task``.

    Bob sees 950 and 951 — **alice's** labels — purely because they sit on task 950, which
    he can read. Scoping the query to ``created_by = me`` returns {953, 954} here and
    still passes the alice case above, so this second user is what makes that error
    visible at all.
    """
    response = bob.get("/api/v1/labels")
    assert response.status_code == 200, response.text
    assert sorted(label["id"] for label in response.json()) == [950, 951, 953, 954]


def test_reading_one_label_embeds_the_whole_user_object(alice: TestClient) -> None:
    """Corpus ``label.read_one.ok``, asserted as a whole body.

    Three things a field-by-field test would let through: ``description`` is ``""`` and not
    null; ``created_by`` is the embedded user rather than an id; that user carries ``name``
    and both timestamps but **no email** — labels are readable by collaborators, so an
    email here is a real disclosure and not a formatting slip.
    """
    response = alice.get("/api/v1/labels/950")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "id": 950,
        "title": "X-alpha",
        "description": "",
        "hex_color": "e8e8e8",
        "created_by": {
            "id": ALICE,
            "name": "",
            "username": "alice",
            "created": "2026-02-01T00:00:00Z",
            "updated": "2026-02-01T00:00:00Z",
        },
        "created": "2026-01-01T00:00:00Z",
        "updated": "2026-01-01T00:00:00Z",
    }


def test_an_invisible_label_and_a_missing_one_are_indistinguishable(alice: TestClient) -> None:
    """Corpus ``label.read_one.invisible_is_403`` and ``…missing_is_403_not_404``.

    Asserted as one comparison because indistinguishability *is* the property: a caller
    must not be able to learn which label ids exist by reading the status. Splitting this
    into two tests would let an implementation satisfy each separately with different
    bodies and still leak.

    Note ``code: 0`` — the generic middleware code, not the label-specific 8002. Inventing
    a label-flavoured code here would be tidier and the frontend branches on this value.
    """
    invisible = alice.get("/api/v1/labels/953")
    missing = alice.get("/api/v1/labels/9999")

    assert invisible.status_code == missing.status_code == 403
    assert invisible.json() == missing.json()
    assert missing.json() == {
        "code": 0,
        "message": "You don't have the permission to see this",
    }


def test_the_read_and_write_paths_disagree_about_a_missing_label(alice: TestClient) -> None:
    """Corpus ``label.read_one.missing_is_403_not_404`` against ``label.update.missing_is_404``.

    The same id, 9999: ``GET`` is 403 and ``POST``/``DELETE`` are 404/8002. This is the
    group's core finding, and it only bites when the two are asserted together — either
    one alone is satisfied by any implementation that picks a single convention.
    """
    read = alice.get("/api/v1/labels/9999")
    update = alice.post("/api/v1/labels/9999", json={"title": "X"})
    delete = alice.delete("/api/v1/labels/9999")

    assert read.status_code == 403
    assert (update.status_code, update.json()) == (
        404,
        {"code": 8002, "message": "This label does not exist."},
    )
    assert (delete.status_code, delete.json()) == (
        404,
        {"code": 8002, "message": "This label does not exist."},
    )


def test_creating_a_label_hydrates_the_response(alice: TestClient) -> None:
    """Corpus ``label.create.ok`` — the full object, in contrast to bulk's bare echo."""
    response = alice.put(
        "/api/v1/labels", json={"title": "NewLabel", "hex_color": "00ff00", "description": "desc"}
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["title"] == "NewLabel"
    assert created["hex_color"] == "00ff00"
    assert created["description"] == "desc"
    assert created["created_by"]["username"] == "alice"
    assert isinstance(created["id"], int)


@pytest.mark.parametrize(
    ("payload", "why"),
    [
        ({"title": ""}, "corpus label.create.empty_title_is_accepted"),
        ({}, "corpus label.create.empty_body_is_accepted"),
        ({"title": "   "}, "upstream's 'required' means non-zero, not non-blank"),
    ],
)
def test_labels_have_no_title_validation_at_all(
    alice: TestClient, payload: dict[str, Any], why: str
) -> None:
    """★ The label resource validates **nothing**, and that is the requirement.

    Projects, comments, buckets and saved filters all answer 412/2002 with an
    ``invalid_fields`` array for an empty title. Labels answer 201. That tidy pattern is
    precisely what makes this dangerous: an implementer generalising from four
    same-shaped resources to the fifth adds validation here, and the change reads as a
    *bug fix* in review.

    The whitespace case is the third row for a separate reason — a ``strip()`` before the
    emptiness test would be invisible in the first two.
    """
    response = alice.put("/api/v1/labels", json=payload)
    assert response.status_code == 201, f"{why}: {response.text}"
    assert response.json()["title"] == payload.get("title", "")


def test_updating_replaces_the_whole_model(alice: TestClient) -> None:
    """Corpus ``label.update.is_full_replacement``.

    951 is the seed row with **both** optional fields populated, so submitting only a
    title has two independent resets to observe. Asserting the reset fields explicitly is
    the whole test: PATCH semantics (update only what was sent) answers 200 with a body
    that looks entirely reasonable, and only ``description`` and ``hex_color`` show it.
    """
    response = alice.post("/api/v1/labels/951", json={"title": "Renamed"})
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["title"] == "Renamed"
    assert updated["description"] == ""
    assert updated["hex_color"] == ""


def test_updating_someone_elses_label_is_403(alice: TestClient) -> None:
    """Corpus ``label.update.other_owner_403`` — the ownership gate, isolated.

    954 rather than 953 is deliberate. Alice *can* see 954, so visibility is not in play
    and the 403 can only come from ownership; using 953 would trip both gates at once and
    an implementation with only one of them would pass.

    The message is ``Forbidden``, not the read path's ``You don't have the permission to
    see this``. Two different 403 strings; not one constant.
    """
    assert body(alice.post("/api/v1/labels/954", json={"title": "hj"})) == {
        "code": 0,
        "message": "Forbidden",
    }


def test_deleting_your_own_label_answers_the_message_body(
    alice: TestClient, sessions: sessionmaker[Session]
) -> None:
    """Corpus ``label.delete.ok``. 952 is the floating label, so no cascade is involved."""
    response = alice.delete("/api/v1/labels/952")
    assert response.status_code == 200, response.text
    assert response.json() == {"message": "Successfully deleted."}

    with sessions() as session:
        assert session.get(Label, 952) is None


def test_deleting_someone_elses_label_is_403(alice: TestClient) -> None:
    """Corpus ``label.delete.other_owner_403`` — the same gate as update."""
    assert body(alice.delete("/api/v1/labels/954")) == {"code": 0, "message": "Forbidden"}


def test_the_collection_carries_pagination_headers(alice: TestClient) -> None:
    """``compare.py`` diffs headers byte for byte; a missing one fails parity everywhere."""
    response = alice.get("/api/v1/labels")
    assert response.headers["x-pagination-result-count"] == "4"
    assert response.headers["x-pagination-total-pages"] == "1"
