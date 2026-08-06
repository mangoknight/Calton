"""Filter preprocessing.

Before the filter string reaches a parser, upstream rewrites it twice
(``task_collection_filter.go:209-266``):

1. The human operators ``not in`` / ``in`` / ``like`` become the fexpr sigils ``?!=`` /
   ``?=`` / ``~``. Longest first, so ``not in`` is not eaten by ``in``, and **quoted runs
   are copied verbatim** so ``title like 'stuff in progress'`` keeps its text.
2. Bare values are quoted, so ``done = false`` becomes ``done = 'false'``.

Getting step 1 wrong does not raise anything — it produces a filter that parses and
matches the wrong rows, which is why these tests are exhaustive about quoting rather than
sampling it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from calton.filters.lexer import preprocess_filter, replace_filter_operators


class TestOperatorReplacement:
    """Step 1 in isolation, before any value quoting."""

    @pytest.mark.parametrize(
        ("filter_string", "expected"),
        [
            ("priority in 3,4,5", "priority ?= 3,4,5"),
            ("priority not in 3,4", "priority ?!= 3,4"),
            ("title like foo", "title ~ foo"),
        ],
    )
    def test_each_operator_becomes_its_sigil(self, filter_string: str, expected: str) -> None:
        assert replace_filter_operators(filter_string) == expected

    def test_not_in_wins_over_in(self) -> None:
        """Order matters: matching ``in`` first would leave a stray ``not``."""
        assert replace_filter_operators("a not in 1") == "a ?!= 1"
        assert "not" not in replace_filter_operators("a not in 1")

    def test_several_operators_in_one_expression(self) -> None:
        result = replace_filter_operators("a in 1 && b like x && c not in 2")

        assert result == "a ?= 1 && b ~ x && c ?!= 2"


class TestQuotedRunsAreUntouched:
    """The part that fails silently when wrong."""

    def test_in_inside_single_quotes_survives(self) -> None:
        assert replace_filter_operators("title like 'stuff in progress'") == (
            "title ~ 'stuff in progress'"
        )

    def test_in_inside_double_quotes_survives(self) -> None:
        assert replace_filter_operators('title like "stuff in progress"') == (
            'title ~ "stuff in progress"'
        )

    def test_like_inside_quotes_survives(self) -> None:
        assert replace_filter_operators("title = 'I like cake'") == "title = 'I like cake'"

    def test_not_in_inside_quotes_survives(self) -> None:
        assert replace_filter_operators("title = 'this is not in scope'") == (
            "title = 'this is not in scope'"
        )

    def test_an_operator_outside_quotes_still_applies(self) -> None:
        result = replace_filter_operators("title like 'in progress' && priority in 1,2")

        assert result == "title ~ 'in progress' && priority ?= 1,2"

    def test_an_escaped_quote_does_not_end_the_run(self) -> None:
        r"""``\`` escapes the next character, so the run continues past ``\'``."""
        assert replace_filter_operators(r"title = 'it\'s in here'") == r"title = 'it\'s in here'"

    def test_an_unclosed_quote_is_an_ordinary_character(self) -> None:
        """A bare value may legitimately contain an apostrophe, so an unterminated quote
        must not swallow the rest of the expression."""
        result = replace_filter_operators("title = it's cool && done = false")

        assert result == "title = it's cool && done = false"

    def test_an_unclosed_quote_does_not_hide_a_later_operator(self) -> None:
        """The bug this guards: treating the apostrophe as an opening quote would make
        everything after it literal, so ``in`` would never be rewritten."""
        result = replace_filter_operators("title = it's cool && priority in 1,2")

        assert result.endswith("priority ?= 1,2")


class TestValueQuoting:
    """Step 2: bare values get quoted so the parser accepts them."""

    @pytest.mark.parametrize(
        ("filter_string", "expected"),
        [
            ("done = false", "done = 'false'"),
            ("priority > 3", "priority > '3'"),
            ("percent_done <= 50", "percent_done <= '50'"),
            ("title != foo", "title != 'foo'"),
        ],
    )
    def test_bare_values_are_quoted(self, filter_string: str, expected: str) -> None:
        assert preprocess_filter(filter_string) == expected

    @pytest.mark.parametrize(
        "filter_string",
        ["done = 'false'", 'done = "false"', "title = 'foo bar'"],
    )
    def test_already_quoted_values_are_left_alone(self, filter_string: str) -> None:
        assert preprocess_filter(filter_string) == filter_string

    def test_an_apostrophe_inside_a_bare_value_is_escaped(self) -> None:
        assert preprocess_filter("title = it's cool") == r"title = 'it\'s cool'"

    def test_both_halves_of_a_conjunction_are_quoted(self) -> None:
        """Note the missing space before ``&&``.

        The value pattern consumes the trailing whitespace and the rebuilt string does
        not put it back, so upstream really does emit ``'false'&&``. I asserted the
        tidier form here first and the Go output corrected me.
        """
        assert preprocess_filter("done = false && priority > 3") == (
            "done = 'false'&& priority > '3'"
        )

    def test_parentheses_bound_the_value(self) -> None:
        """The value pattern excludes ``&|()``, so a group does not get swallowed."""
        assert preprocess_filter("(done = false)") == "(done = 'false')"


class TestEndToEnd:
    """Both steps together, on the shapes the design doc calls out."""

    def test_the_apostrophe_case_from_the_design_doc(self) -> None:
        assert preprocess_filter("title = it's cool && done = false") == (
            r"title = 'it\'s cool'&& done = 'false'"
        )

    def test_in_becomes_a_comma_list_not_a_bracket_list(self) -> None:
        """``priority in 3,4,5`` is a bare comma list upstream — no brackets anywhere."""
        result = preprocess_filter("priority in 3,4,5")

        assert result == "priority ?= '3,4,5'"
        assert "[" not in result

    def test_like_keeps_its_quoted_argument(self) -> None:
        assert preprocess_filter("title like 'in progress'") == "title ~ 'in progress'"

    def test_a_date_math_value_is_quoted_whole(self) -> None:
        assert preprocess_filter("due_date > now/d") == "due_date > 'now/d'"

    @pytest.mark.parametrize("filter_string", ["", "   "])
    def test_empty_input_is_returned_unchanged(self, filter_string: str) -> None:
        assert preprocess_filter(filter_string) == filter_string

    def test_a_string_with_no_comparison_is_untouched(self) -> None:
        assert preprocess_filter("nonsense") == "nonsense"


class TestAgainstGoOutput:
    """Every case checked against output captured from the real function.

    ``preprocessFilterString`` is unexported and has no upstream test, so the fixture was
    produced by a temporary Go test calling it directly — the same approach as the
    permission cross-check, and for the same reason: this is the part of T20 where a
    wrong answer is silent. Regenerate as described in the fixture's ``_meta.how``.
    """

    FIXTURE = json.loads(
        (Path(__file__).resolve().parent.parent / "fixtures" / "go_preprocess.json").read_text()
    )

    @pytest.mark.parametrize(
        ("source", "expected"), sorted(FIXTURE["cases"].items()), ids=lambda v: v[:40]
    )
    def test_matches_go(self, source: str, expected: str) -> None:
        assert preprocess_filter(source) == expected

    def test_the_fixture_records_its_provenance(self) -> None:
        assert len(self.FIXTURE["_meta"]["commit"]) == 40
        assert self.FIXTURE["_meta"]["source"].startswith("task_collection_filter.go")

    def test_the_fixture_covers_the_dangerous_shapes(self) -> None:
        """A fixture that only held easy cases would pass without proving anything."""
        cases = self.FIXTURE["cases"]

        assert any(" not in " in case for case in cases)
        assert any("'stuff in progress'" in case for case in cases)
        assert any("it's" in case for case in cases)
