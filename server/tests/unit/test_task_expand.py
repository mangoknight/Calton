"""``?expand=`` behaviour, measured on the Go reference server.

The seed mirrors the fixture those measurements were taken against: a parent with two
children and a grandchild, a second root with no relations, 55 comments on the parent, and
one bucket.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient


def _ids(body: list[dict[str, Any]]) -> list[int]:
    return [entry["id"] for entry in body]


def _parent(body: list[dict[str, Any]]) -> dict[str, Any]:
    return next(entry for entry in body if entry["id"] == 9800)


# --------------------------------------------------------------------------------------
# subtasks: the response is allowed to be longer than per_page
# --------------------------------------------------------------------------------------


def test_expanding_subtasks_returns_more_rows_than_per_page(
    client: TestClient, expand_seed: None
) -> None:
    """★ The most counter-intuitive behaviour in this card, so it is asserted head-on.

    Pagination applies to *roots*: the parent and the lonely task, both of which fit in
    ``per_page=2``. Every descendant is then appended — two children and a grandchild —
    so five tasks come back from a two-per-page request.

    Capping the total at per_page instead looks like the obvious fix and silently drops
    subtasks, so a client paging through would see a parent whose children are simply
    missing, with nothing in the response saying so.
    """
    response = client.get(
        "/api/v1/projects/980/tasks", params={"expand": "subtasks", "per_page": 2}
    )
    body = response.json()

    assert len(body) == 5
    assert set(_ids(body)) == {9800, 9801, 9802, 9803, 9804}
    assert response.headers["x-pagination-result-count"] == "5"


def test_without_expanding_the_same_request_honours_per_page(
    client: TestClient, expand_seed: None
) -> None:
    """Control: the over-run is caused by the expansion, not by broken pagination."""
    response = client.get("/api/v1/projects/980/tasks", params={"per_page": 2})

    assert len(response.json()) == 2
    assert response.headers["x-pagination-result-count"] == "2"


def test_the_result_count_header_reports_what_was_actually_sent(
    client: TestClient, expand_seed: None
) -> None:
    """Not the root count. A client that trusted the header over the body would be wrong
    in the other direction."""
    response = client.get(
        "/api/v1/projects/980/tasks", params={"expand": "subtasks", "per_page": 2}
    )

    assert response.headers["x-pagination-result-count"] == str(len(response.json()))


def test_a_grandchild_is_included_so_the_walk_recurses(
    client: TestClient, expand_seed: None
) -> None:
    """★ One level of children would satisfy every other assertion here.

    The grandchild hangs off childA, not off the paginated root, so a non-recursive
    descendant query returns four tasks instead of five and renders an incomplete tree
    with no error.
    """
    body = client.get(
        "/api/v1/projects/980/tasks", params={"expand": "subtasks", "per_page": 2}
    ).json()

    assert 9803 in _ids(body)


def test_children_are_not_counted_as_roots(client: TestClient, expand_seed: None) -> None:
    """★ Paging proves it; a single page does not.

    Page 1 with ``per_page=1`` admits one root (the parent) and brings its three
    descendants — but that same set comes back even if children *were* roots, since the
    parent still sorts first and still drags its subtasks along. The discriminating half
    is page 2: with the root condition it holds the lonely task, and without it page 2
    would be childA (the second row of a five-root result) instead.
    """
    first = client.get(
        "/api/v1/projects/980/tasks", params={"expand": "subtasks", "per_page": 1}
    ).json()
    second = client.get(
        "/api/v1/projects/980/tasks", params={"expand": "subtasks", "per_page": 1, "page": 2}
    ).json()

    assert set(_ids(first)) == {9800, 9801, 9802, 9803}
    assert _ids(second) == [9804], "page 2 must be the other root, not a child"


def test_a_soft_deleted_subtask_is_not_resurrected(
    client: TestClient, expand_seed: None, session: Any
) -> None:
    """The descendant walk needs the same deleted_at filter as the root query.

    It is a separate query, so the filter has to be repeated — and a deleted child
    reappearing under a live parent is exactly the leak the soft-delete discipline exists
    to prevent.
    """
    from calton.db.base import utcnow
    from calton.models import Task

    child = session.get(Task, 9802)
    child.deleted_at = utcnow()
    session.commit()

    body = client.get(
        "/api/v1/projects/980/tasks", params={"expand": "subtasks", "per_page": 2}
    ).json()

    assert 9802 not in _ids(body)
    assert 9801 in _ids(body)


# --------------------------------------------------------------------------------------
# buckets, comments, comment_count
# --------------------------------------------------------------------------------------


def test_expanding_buckets_fills_the_field(client: TestClient, expand_seed: None) -> None:
    body = client.get("/api/v1/projects/980/tasks", params={"expand": "buckets"}).json()

    assert _parent(body)["buckets"][0]["id"] == 980


def test_an_embedded_bucket_has_no_tasks_key(client: TestClient, expand_seed: None) -> None:
    """Measured: the bucket inside a task carries no ``tasks``. Nesting the tasks back
    inside would recurse — each task listing its buckets listing their tasks."""
    body = client.get("/api/v1/projects/980/tasks", params={"expand": "buckets"}).json()

    assert "tasks" not in _parent(body)["buckets"][0]


def test_expanding_comments_embeds_at_most_fifty(client: TestClient, expand_seed: None) -> None:
    """★ 55 comments in, 50 out — and the cap is per task, not per request."""
    body = client.get("/api/v1/projects/980/tasks", params={"expand": "comments"}).json()

    assert len(_parent(body)["comments"]) == 50
    assert _parent(body)["comments"][0]["comment"] == "comment-000"
    assert _parent(body)["comments"][-1]["comment"] == "comment-049"


def test_the_comment_count_is_not_truncated_to_the_embedded_limit(
    client: TestClient, expand_seed: None
) -> None:
    """★ The two numbers are independent: 50 embedded, 55 counted.

    Deriving the count from the embedded list is the obvious simplification and makes the
    UI say "55 comments" only until someone passes 50, at which point it silently plateaus.
    """
    body = client.get(
        "/api/v1/projects/980/tasks", params={"expand": ["comments", "comment_count"]}
    ).json()

    assert len(_parent(body)["comments"]) == 50
    assert _parent(body)["comment_count"] == 55


def test_an_embedded_comment_carries_exactly_the_upstream_keys(
    client: TestClient, expand_seed: None
) -> None:
    body = client.get("/api/v1/projects/980/tasks", params={"expand": "comments"}).json()

    assert set(_parent(body)["comments"][0]) == {
        "id",
        "comment",
        "author",
        "reactions",
        "created",
        "updated",
    }


def test_a_task_without_comments_has_no_comments_key(client: TestClient, expand_seed: None) -> None:
    """Absent rather than ``[]``: the field is omitempty, and only the parent has any."""
    body = client.get("/api/v1/projects/980/tasks", params={"expand": "comments"}).json()
    lonely = next(entry for entry in body if entry["id"] == 9804)

    assert "comments" not in lonely


# --------------------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------------------


def test_an_unknown_expand_value_is_a_412_naming_the_parameter(client: TestClient) -> None:
    """412 with the long allowed-values message, not the usual "Invalid Data"."""
    response = client.get("/api/v1/tasks", params={"expand": "bogus"})

    assert response.status_code == 412
    assert response.json() == {
        "code": 2002,
        "message": (
            "Expand must be one of the following values: subtasks, buckets, reactions, "
            "comments, comment_count, time_entries_count, is_unread"
        ),
        "invalid_fields": ["expand"],
    }


def test_an_empty_expand_value_is_rejected_rather_than_ignored(client: TestClient) -> None:
    """★ ``?expand=`` is invalid, not "unset".

    Treating the empty string as absent is the natural reading and makes a client that
    builds its query string carelessly get a silently unexpanded response instead of the
    error upstream returns.
    """
    response = client.get("/api/v1/tasks", params={"expand": ""})

    assert response.status_code == 412
    assert response.json()["invalid_fields"] == ["expand"]


def test_one_bad_value_rejects_the_whole_request(client: TestClient, expand_seed: None) -> None:
    """Every value is validated before anything is read, so a good value alongside a bad
    one does not get partially applied."""
    response = client.get("/api/v1/projects/980/tasks", params={"expand": ["subtasks", "bogus"]})

    assert response.status_code == 412


def test_repeating_the_same_expand_value_is_accepted(client: TestClient, expand_seed: None) -> None:
    """Measured: duplicates are deduplicated, not rejected and not applied twice."""
    once = client.get("/api/v1/projects/980/tasks", params={"expand": "subtasks"})
    twice = client.get("/api/v1/projects/980/tasks", params={"expand": ["subtasks", "subtasks"]})

    assert twice.status_code == 200
    assert twice.json() == once.json()


@pytest.mark.parametrize(
    "value",
    [
        "subtasks",
        "buckets",
        "reactions",
        "comments",
        "comment_count",
        "time_entries_count",
        "is_unread",
    ],
)
def test_every_upstream_expand_value_is_accepted(
    client: TestClient, expand_seed: None, value: str
) -> None:
    """Including the three Calton cannot populate.

    ``is_unread``, ``time_entries_count`` and ``reactions`` read tables outside Phase 1's
    schema, so the field stays absent — which is also what upstream does when there is no
    row, so the two agree on every request that can be made today. Rejecting them would
    break clients that ask speculatively, and the web frontend does.
    """
    assert client.get("/api/v1/projects/980/tasks", params={"expand": value}).status_code == 200


# --------------------------------------------------------------------------------------
# The omitempty precondition T18 left behind
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["buckets", "comments", "comment_count", "is_unread", "time_entries_count", "subscription"],
)
def test_an_unexpanded_response_still_omits_every_declared_field(
    client: TestClient, expand_seed: None, field: str
) -> None:
    """★ The precondition T18 recorded and asked T24 to re-check before filling these in.

    All six are declared on TaskRead so the contract diff sees them; all six must stay
    *absent* from a response that did not ask for them. Now that four are populated on
    demand, the risk is the opposite of T18's: a default of ``[]`` or ``0`` would put the
    key back on every response and break the byte-comparison for every un-expanded call.
    """
    body = client.get("/api/v1/projects/980/tasks").json()

    for task in body:
        assert field not in task, f"{field} leaked into an un-expanded response"


def test_reactions_is_present_and_null_rather_than_omitted(
    client: TestClient, expand_seed: None
) -> None:
    """★ The exception in that list, and it goes the other way.

    ``reactions`` is not omitempty upstream: it is always present and null when empty,
    unlike the six above. Measured on an un-expanded response. Grouping it with them —
    the obvious tidy-up, since it is equally unpopulated — would drop a key clients read.
    """
    body = client.get("/api/v1/projects/980/tasks").json()

    assert all("reactions" in task and task["reactions"] is None for task in body)
