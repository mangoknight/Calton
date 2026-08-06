"""The fexpr expression parser, checked against the upstream library's own case table.

Upstream does not hand-roll a parser; it delegates to ``github.com/ganigeorgiev/fexpr``
(``parser.go`` plus ``scanner.go``). So the acceptance table here is not invented — it is
that library's ``parser_test.go``, extracted with ``go/ast`` and run through the real
``fexpr.Parse`` by ``scripts/dump_go_fexpr.go``. ``tests/fixtures/go_fexpr_parse.json`` is
its output: 103 upstream cases plus 28 Calton-shaped ones.

That fixture pins the grammar but says nothing about what Calton does with the result,
so a second one — ``go_filter_validation.json``, recorded from a running reference server
by ``scripts/dump_go_filter_validation.py`` — pins the field whitelist, the comparator
mapping and the error code behind each refusal.

Error *messages* are asserted verbatim, not just "something raised". Two reasons:

* Several cases differ only in which branch fires — ``a - 1`` is an invalid number while
  ``a ! 1`` is an invalid sign operator, and both are simply "an error" if you squint.
* The message is wire-visible. A parse failure surfaces as code 4024, whose message
  template interpolates the raw parser error (``error.go:1306``), so a paraphrase would
  change the response body.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from calton.core.errors import CaltonError
from calton.filters.lexer import FilterExpressionError, Token
from calton.filters.parser import (
    Expr,
    ExprGroup,
    FilterCondition,
    FilterGroup,
    Join,
    parse,
    parse_task_filter,
)

_FIXTURE = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "go_fexpr_parse.json").read_text()
)
_CASES = _FIXTURE["cases"]

_VALIDATION_CASES = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "go_filter_validation.json").read_text()
)["cases"]

#: The refusals this layer is responsible for. Any other code the reference server
#: returned is decided further down the pipeline, when the value is cast (T21/T22).
PARSE_LAYER_CODES = frozenset({4016, 4017, 4024})


def _token_to_json(token: Token) -> dict[str, Any]:
    out: dict[str, Any] = {"type": str(token.type), "literal": token.literal}
    if token.args is not None:
        out["args"] = [_token_to_json(arg) for arg in token.args]
    return out


def _groups_to_json(groups: list[ExprGroup]) -> list[dict[str, Any]]:
    """Render an AST in the same shape the Go probe emits, so the two can be compared."""
    out = []
    for group in groups:
        rendered: dict[str, Any] = {"join": str(group.join)}
        if isinstance(group.item, Expr):
            rendered["expr"] = {
                "left": _token_to_json(group.item.left),
                "op": str(group.item.op),
                "right": _token_to_json(group.item.right),
            }
        else:
            rendered["groups"] = _groups_to_json(list(group.item))
        out.append(rendered)
    return out


def _conditions(nodes: Sequence[FilterCondition | FilterGroup]) -> list[FilterCondition]:
    """Assert every node is a leaf comparison, and hand them back narrowed."""
    for node in nodes:
        assert isinstance(node, FilterCondition), f"expected a comparison, got {node!r}"
    return [node for node in nodes if isinstance(node, FilterCondition)]


def _only(nodes: Sequence[FilterCondition | FilterGroup]) -> FilterCondition:
    conditions = _conditions(nodes)
    assert len(conditions) == 1
    return conditions[0]


class TestAgainstUpstreamTable:
    """Every case from fexpr's own parser_test.go, plus the shapes Calton produces."""

    @pytest.mark.parametrize(
        "case", _CASES, ids=[f"{i}:{c['input']!r}" for i, c in enumerate(_CASES)]
    )
    def test_matches_go(self, case: dict[str, Any]) -> None:
        expected_error = case.get("error")

        # The library's own table declares which inputs are errors. Asserting it here
        # keeps the fixture honest: if a regeneration ever recorded a result that
        # disagreed with upstream's expectation, replaying it would enshrine the
        # disagreement instead of failing.
        if "upstream_expects_error" in case:
            assert bool(expected_error) == case["upstream_expects_error"]

        if expected_error:
            with pytest.raises(FilterExpressionError) as excinfo:
                parse(case["input"])
            assert str(excinfo.value) == expected_error
        else:
            assert _groups_to_json(parse(case["input"])) == case["groups"]

    def test_the_fixture_is_the_upstream_table(self) -> None:
        """Guards against the fixture being regenerated from a trimmed case list."""
        assert _FIXTURE["_meta"]["upstream_case_count"] == 103
        assert len([c for c in _CASES if c.get("error")]) == 51


class TestAgainstTheGoServer:
    """End-to-end answers from the reference server, replayed against this layer.

    ``go_fexpr_parse.json`` pins the grammar; this fixture pins what Calton does with
    the result — which field names it accepts, which comparators it maps, and which
    error code each refusal carries. It is recorded by
    ``scripts/dump_go_filter_validation.py`` from ``GET /api/v1/tasks?filter=...``.
    """

    @pytest.mark.parametrize(
        "case",
        _VALIDATION_CASES,
        ids=[f"{i}:{c['filter']!r}" for i, c in enumerate(_VALIDATION_CASES)],
    )
    def test_matches_the_go_server(self, case: dict[str, Any]) -> None:
        code = case.get("code")

        if code in PARSE_LAYER_CODES:
            with pytest.raises(CaltonError) as excinfo:
                parse_task_filter(case["filter"])
            assert excinfo.value.code == code
            assert excinfo.value.message == case["message"]
            assert excinfo.value.http_status == case["http_status"]
        else:
            # Either the server accepted it, or it got past this layer and failed later.
            assert case["http_status"] == 200 or case["decided_after_parsing"]
            # `decided_after_parsing` is maintained by hand in the dump script. Pinning
            # the code stops a mislabelled 4016 from quietly turning this branch into a
            # requirement that the parse layer *accept* something it must reject.
            if case.get("decided_after_parsing"):
                assert case["code"] == 4019
            parse_task_filter(case["filter"])

    def test_the_fixture_covers_all_three_refusal_codes(self) -> None:
        codes = {c.get("code") for c in _VALIDATION_CASES}
        assert codes >= PARSE_LAYER_CODES


class TestGrammar:
    """The structural properties T20 calls out, stated directly rather than as fixtures."""

    def test_unclosed_quote_in_a_bare_value_still_parses(self) -> None:
        """T20 ①. The apostrophe survives preprocessing, so the parser sees valid text."""
        conditions = parse_task_filter("title = it's cool && done = false")

        assert conditions == [
            FilterCondition(field="title", comparator="=", value="it's cool", join=Join.AND),
            FilterCondition(field="done", comparator="=", value="false", join=Join.AND),
        ]

    def test_quoted_operator_is_not_rewritten(self) -> None:
        """T20 ②. ``in`` inside a value must survive into the parsed value."""
        conditions = parse_task_filter("title like 'stuff in progress'")

        assert conditions == [
            FilterCondition(
                field="title", comparator="like", value="stuff in progress", join=Join.AND
            )
        ]

    def test_in_takes_a_bare_comma_list(self) -> None:
        """T20 ③. The value is ``3,4,5`` — no brackets, and it stays one token."""
        conditions = parse_task_filter("priority in 3,4,5")

        assert conditions == [
            FilterCondition(field="priority", comparator="in", value="3,4,5", join=Join.AND)
        ]

    def test_not_in_takes_a_bare_comma_list(self) -> None:
        conditions = parse_task_filter("priority not in 3,4")

        assert conditions == [
            FilterCondition(field="priority", comparator="not in", value="3,4", join=Join.AND)
        ]

    def test_parentheses_nest(self) -> None:
        """T20 ④."""
        conditions = parse_task_filter("(done = false || priority > 3) && title = x")

        assert len(conditions) == 2
        inner = conditions[0]
        assert isinstance(inner, FilterGroup)
        assert inner.join == Join.AND
        assert [c.field for c in _conditions(inner.conditions)] == ["done", "priority"]
        assert [c.join for c in _conditions(inner.conditions)] == [Join.AND, Join.OR]
        assert conditions[1] == FilterCondition(
            field="title", comparator="=", value="x", join=Join.AND
        )

    def test_join_belongs_to_the_expression_it_precedes(self) -> None:
        """The join on a group is the operator written *before* it, and the first is AND.

        Getting this off by one silently swaps AND and OR across a whole filter.
        """
        conditions = parse_task_filter("done = false || priority = 2 && title = x")

        assert [c.field for c in _conditions(conditions)] == ["done", "priority", "title"]
        assert [c.join for c in _conditions(conditions)] == [Join.AND, Join.OR, Join.AND]

    def test_groups_nest_to_two_levels(self) -> None:
        conditions = parse_task_filter("((done = false || done = true) && (priority = 1))")

        assert len(conditions) == 1
        outer = conditions[0]
        assert isinstance(outer, FilterGroup)
        assert len(outer.conditions) == 2
        assert all(isinstance(c, FilterGroup) for c in outer.conditions)


class TestFieldValidation:
    """T20 ⑤ and ⑦."""

    def test_unknown_field_is_4016(self) -> None:
        with pytest.raises(CaltonError) as excinfo:
            parse_task_filter("nonexistent = 1")

        assert excinfo.value.code == 4016
        assert excinfo.value.http_status == 400
        assert excinfo.value.message == "The task field 'nonexistent' is invalid."

    def test_relevance_cannot_be_filtered(self) -> None:
        """T20 ⑦. ``relevance`` is a sort-only pseudo field (task_collection_sort.go:87-90)."""
        with pytest.raises(CaltonError) as excinfo:
            parse_task_filter("relevance = 1")

        assert excinfo.value.code == 4016

    def test_project_view_id_cannot_be_filtered(self) -> None:
        with pytest.raises(CaltonError) as excinfo:
            parse_task_filter("project_view_id = 1")

        assert excinfo.value.code == 4016

    def test_project_is_rewritten_to_project_id(self) -> None:
        """Bare ``project`` is not itself filterable; it is renamed before validation."""
        assert parse_task_filter("project = 1") == [
            FilterCondition(field="project_id", comparator="=", value="1", join=Join.AND)
        ]

    @pytest.mark.parametrize("field", ["assignees", "labels", "reminders"])
    def test_subtable_fields_are_filterable(self, field: str) -> None:
        """These three are filterable but *not* sortable — the two lists are not equal."""
        assert _only(parse_task_filter(f"{field} = 1")).field == field

    @pytest.mark.parametrize(
        "field",
        [
            "id",
            "title",
            "description",
            "done",
            "done_at",
            "due_date",
            "created_by_id",
            "project_id",
            "repeat_after",
            "priority",
            "start_date",
            "end_date",
            "hex_color",
            "percent_done",
            "uid",
            "created",
            "updated",
            "position",
            "bucket_id",
            "index",
        ],
    )
    def test_every_sortable_field_is_filterable(self, field: str) -> None:
        assert _only(parse_task_filter(f"{field} = 1")).field == field

    def test_a_bad_field_inside_a_group_still_raises(self) -> None:
        with pytest.raises(CaltonError) as excinfo:
            parse_task_filter("(done = false && bogus = 1)")

        assert excinfo.value.code == 4016


class TestComparatorValidation:
    """T20 ⑥."""

    @pytest.mark.parametrize(
        ("written", "expected"),
        [
            ("done = 1", "="),
            ("done != 1", "!="),
            ("priority > 1", ">"),
            ("priority >= 1", ">="),
            ("priority < 1", "<"),
            ("priority <= 1", "<="),
            ("title like x", "like"),
            ("priority in 1,2", "in"),
            ("priority not in 1,2", "not in"),
        ],
    )
    def test_every_supported_comparator(self, written: str, expected: str) -> None:
        assert _only(parse_task_filter(written)).comparator == expected

    @pytest.mark.parametrize(
        "written",
        [
            "title !~ 'x'",
            "title ?~ 'x'",
            "title ?!~ 'x'",
            "priority ?> '1'",
            "priority ?>= '1'",
            "priority ?< '1'",
            "priority ?<= '1'",
        ],
    )
    def test_sigils_fexpr_accepts_but_calton_rejects_are_4017(self, written: str) -> None:
        """These scan and parse cleanly; only the comparator mapping rejects them.

        They are unreachable through preprocessing but reachable by writing the sigil
        directly, which the filter string permits.
        """
        with pytest.raises(CaltonError) as excinfo:
            parse_task_filter(written)

        assert excinfo.value.code == 4017
        assert excinfo.value.http_status == 400

    def test_comparator_is_checked_before_the_field(self) -> None:
        """Upstream validates the comparator first, so a doubly-invalid filter is 4017."""
        with pytest.raises(CaltonError) as excinfo:
            parse_task_filter("nonexistent !~ 'x'")

        assert excinfo.value.code == 4017


class TestEdges:
    """T20 ⑦ boundaries."""

    def test_empty_filter_yields_no_conditions(self) -> None:
        """Short-circuited before the parser, so it is not an error (filter.go:270-272)."""
        assert parse_task_filter("") == []

    def test_whitespace_only_filter_is_an_error(self) -> None:
        """Unlike the empty string: it is not short-circuited, so it reaches the parser."""
        with pytest.raises(CaltonError) as excinfo:
            parse_task_filter("   ")

        assert excinfo.value.code == 4024

    def test_a_parse_failure_becomes_4024_carrying_the_parser_message(self) -> None:
        with pytest.raises(CaltonError) as excinfo:
            parse_task_filter("done = false &&")

        assert excinfo.value.code == 4024
        assert excinfo.value.http_status == 400
        assert "invalid or incomplete filter expression" in excinfo.value.message

    def test_a_parse_failure_reports_the_preprocessed_expression(self) -> None:
        """The 4024 message quotes the rewritten filter, not what the user typed."""
        with pytest.raises(CaltonError) as excinfo:
            parse_task_filter("done = false && ")

        assert "done = 'false'" in excinfo.value.message

    def test_unbalanced_parenthesis(self) -> None:
        with pytest.raises(CaltonError) as excinfo:
            parse_task_filter("(done = false")

        assert excinfo.value.code == 4024
