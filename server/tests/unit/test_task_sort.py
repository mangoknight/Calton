"""Sort parameter parsing, validation and the two appended keys.

Pure logic, so these are unit tests over ``parse_sort`` rather than HTTP round trips; the
ordering they produce is asserted end-to-end in the collection tests.
"""

from __future__ import annotations

import pytest

from calton.core.errors import CaltonError
from calton.services.task_sort import (
    SortParam,
    parse_sort,
    with_id_tiebreaker,
    with_view_position,
)


def _pairs(params: list[SortParam]) -> list[tuple[str, str]]:
    return [(param.sort_by, param.order_by) for param in params]


def test_sort_and_order_pair_by_position() -> None:
    """The second key gets asc because there is no second order_by — not desc."""
    params = parse_sort(["priority", "title"], ["desc"])

    assert _pairs(params) == [("priority", "desc"), ("title", "asc")]


def test_a_lone_order_by_does_not_apply_to_every_key() -> None:
    """Guards the "apply the last order_by to all of them" misreading."""
    params = parse_sort(["priority", "title", "done"], ["desc"])

    assert [param.order_by for param in params] == ["desc", "asc", "asc"]


def test_extra_order_by_entries_are_ignored() -> None:
    params = parse_sort(["priority"], ["desc", "asc", "desc"])

    assert _pairs(params) == [("priority", "desc")]


def test_an_unknown_sort_field_is_rejected_with_its_name() -> None:
    with pytest.raises(CaltonError) as raised:
        parse_sort(["nosuchfield"], ["asc"])

    assert raised.value.code == 4016
    assert raised.value.message == "The task field 'nosuchfield' is invalid."


def test_an_unknown_order_echoes_the_constant_not_the_user_input() -> None:
    """★ The message says 'invalid', never 'sideways'.

    Upstream parses the direction into an enum and formats the enum, so the user's text
    never reaches the message. Interpolating the input instead — the natural way to write
    it — gives the same 400 and the same code 4014 with one word different, which only a
    byte-exact body comparison catches.
    """
    with pytest.raises(CaltonError) as raised:
        parse_sort(["priority"], ["sideways"])

    assert raised.value.code == 4014
    assert raised.value.message == (
        "The task sort order 'invalid' is invalid. Allowed is either asc or desc."
    )
    assert "sideways" not in raised.value.message


def test_sorting_by_position_outside_a_view_is_rejected() -> None:
    """position lives in task_positions, keyed by view; there is no global one."""
    with pytest.raises(CaltonError) as raised:
        parse_sort(["position"], ["asc"])

    assert raised.value.code == 4026


def test_sorting_by_position_inside_a_view_carries_the_view_id() -> None:
    params = parse_sort(["position"], ["asc"], view_id=973)

    assert params == [SortParam(sort_by="position", order_by="asc", project_view_id=973)]


def test_a_saved_filters_pseudo_view_drops_the_position_key_silently() -> None:
    """A negative view id has no stored positions. Dropped, not rejected."""
    params = parse_sort(["position", "priority"], ["asc", "desc"], view_id=-2)

    assert _pairs(params) == [("priority", "desc")]


def test_relevance_is_accepted_even_though_it_is_not_a_column() -> None:
    """Sortable but not filterable, which is why it is not in SORTABLE_FIELDS."""
    assert _pairs(parse_sort(["relevance"], ["desc"])) == [("relevance", "desc")]


# --- the two appended keys ------------------------------------------------------------


def test_the_id_tiebreaker_is_appended_when_the_last_key_is_not_id() -> None:
    params = with_id_tiebreaker(parse_sort(["priority"], ["desc"]))

    assert _pairs(params) == [("priority", "desc"), ("id", "asc")]


def test_the_id_tiebreaker_is_appended_when_nothing_was_sorted_by() -> None:
    """The other disjunct of the same condition, reached by a different code path.

    Without it the empty case falls through to whatever order the database returns —
    which on SQLite is usually id ascending anyway, so this passes by luck locally and
    breaks on any other engine.
    """
    assert _pairs(with_id_tiebreaker(parse_sort([], []))) == [("id", "asc")]


def test_an_explicit_trailing_id_desc_is_left_alone() -> None:
    """★ "ends with id", not "contains id".

    Appending unconditionally would silently rewrite the user's `id desc` to `id asc`:
    still 200, still the right rows, in the opposite order — which shows up as a
    descending list that restarts from the top on every page.
    """
    params = with_id_tiebreaker(parse_sort(["priority", "id"], ["desc", "desc"]))

    assert _pairs(params) == [("priority", "desc"), ("id", "desc")]


def test_an_id_key_that_is_not_last_still_gets_the_tiebreaker() -> None:
    """ "Contains" would skip the append here and leave the sort unstable on ties."""
    params = with_id_tiebreaker(parse_sort(["id", "priority"], ["desc", "asc"]))

    assert _pairs(params) == [("id", "desc"), ("priority", "asc"), ("id", "asc")]


def test_the_view_position_key_is_appended_before_the_tiebreaker() -> None:
    """Order matters: position goes on first, so the id tiebreaker ends up last."""
    params = with_id_tiebreaker(with_view_position(parse_sort(["priority"], ["desc"]), 973))

    assert _pairs(params) == [("priority", "desc"), ("position", "asc"), ("id", "asc")]


def test_a_users_own_position_key_is_not_duplicated() -> None:
    params = with_view_position(parse_sort(["position"], ["desc"], view_id=973), 973)

    assert _pairs(params) == [("position", "desc")]
