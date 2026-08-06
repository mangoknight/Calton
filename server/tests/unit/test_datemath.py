"""Date maths, checked against the upstream library and against Go's stdlib.

Two fixtures back this, both recorded rather than reasoned about:

* ``go_datemath_expressions.json`` — ``scripts/dump_go_datemath.go`` copies the case
  table out of ``go-datemath``'s own ``datemath_test.go`` **verbatim** (it is not
  retyped), appends Calton-shaped cases, and evaluates every one with the real library.
* ``go_datemath_fallback.json`` — ``scripts/dump_go_datemath_fallback.go`` runs Calton's
  four-step fallback against Go's stdlib, which is the only way to settle whether its
  fixed-width layouts accept things like ``2021-1-1``.

Error *text* is not asserted, unlike the fexpr parser. It is unobservable: upstream
discards the datemath error, falls through to the layout chain, and reports only
``ErrInvalidTaskFilterValue`` (4019), whose message carries just the field and value.
What must match is whether a given input parses, and what instant it produces.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from calton.filters.datemath import (
    MONDAY,
    SUNDAY,
    DateMathError,
    Options,
    parse,
    parse_and_evaluate,
    parse_time_from_user_input,
    resolve_filter_time,
)

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_EXPRESSION_CASES = json.loads((_FIXTURES / "go_datemath_expressions.json").read_text())["cases"]
_FALLBACK_CASES = json.loads((_FIXTURES / "go_datemath_fallback.json").read_text())["cases"]

SHANGHAI = ZoneInfo("Asia/Shanghai")
KOLKATA = ZoneInfo("Asia/Kolkata")
NEW_YORK = ZoneInfo("America/New_York")


_FISCAL_YEAR = re.compile(r"^\d{4}-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})")


def _parse_instant(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _location(name: str) -> tzinfo:
    if name == "UTC":
        return UTC
    if name.startswith("fixed:"):
        return timezone(timedelta(seconds=int(name.split(":", 1)[1])))
    return ZoneInfo(name)


def _options_for(case: dict[str, Any]) -> Options:
    fiscal = case.get("fiscal_year")
    start_of_fiscal_year = None
    if fiscal:
        # Recorded as Go's zero year (0000), which datetime cannot hold; only the
        # month, day and time of day carry meaning here.
        match = _FISCAL_YEAR.match(fiscal)
        assert match, fiscal
        month, day, hour, minute, second = (int(g) for g in match.groups())
        start_of_fiscal_year = (month, day, hour, minute, second, 0)

    return Options(
        now=_parse_instant(case["now"]),
        location=_location(case["location"]),
        start_of_week=case.get("start_of_week", MONDAY),
        round_up=case.get("round_up", False),
        start_of_fiscal_year=start_of_fiscal_year,
    )


#: Cases whose expected answer depends on a custom business-day predicate. Calton never
#: sets one, and the closure cannot be carried through JSON, so they are not replayed.
_REPLAYABLE = [c for c in _EXPRESSION_CASES if not c.get("custom_business_day")]


class TestAgainstTheLibrary:
    @pytest.mark.parametrize(
        "case",
        _REPLAYABLE,
        ids=[f"{i}:{c['in']!r}@{c['location']}" for i, c in enumerate(_REPLAYABLE)],
    )
    def test_matches_go(self, case: dict[str, Any]) -> None:
        options = _options_for(case)

        if case.get("err"):
            with pytest.raises(DateMathError):
                parse_and_evaluate(case["in"], options)
            return

        result = parse_and_evaluate(case["in"], options)
        # Normalised to UTC because PEP 495 has inter-zone comparison ignore `fold`,
        # which would make an ambiguous local time compare equal to the wrong instant.
        assert result.astimezone(UTC) == _parse_instant(case["got"])

    def test_the_fixture_still_holds_the_whole_upstream_table(self) -> None:
        """The upstream table contributes these; a trimmed regeneration would show up.

        73 distinct rows, not 74: ``2014-05-35T20:21:21Z`` declares *both* an expected
        value and an error, so adding the two counts double-counts it.
        """
        assert len(_EXPRESSION_CASES) == 120

        declaring_a_value = [c for c in _EXPRESSION_CASES if c.get("upstream_expected")]
        declaring_an_error = [c for c in _EXPRESSION_CASES if c.get("upstream_err")]
        declaring_either = [
            c for c in _EXPRESSION_CASES if c.get("upstream_expected") or c.get("upstream_err")
        ]

        assert len(declaring_a_value) == 70
        assert len(declaring_an_error) == 4
        assert len(declaring_either) == 73

    def test_every_upstream_declared_error_still_errors(self) -> None:
        declared = [c for c in _EXPRESSION_CASES if c.get("upstream_err")]
        assert len(declared) == 4
        assert all(c.get("err") for c in declared)


class TestTheFormsTheDesignDocNames:
    """T21's target list, stated directly rather than only as fixture rows."""

    NOW = datetime(2014, 11, 18, 14, 27, 32, tzinfo=UTC)

    def _evaluate(self, expression: str, location: tzinfo = UTC) -> datetime:
        return parse_and_evaluate(expression, Options(now=self.NOW, location=location))

    def test_now(self) -> None:
        assert self._evaluate("now") == self.NOW

    @pytest.mark.parametrize(
        ("expression", "expected"),
        [
            ("now+1y", "2015-11-18T14:27:32Z"),
            ("now-1y", "2013-11-18T14:27:32Z"),
            ("now+1M", "2014-12-18T14:27:32Z"),
            ("now+1w", "2014-11-25T14:27:32Z"),
            ("now+30d", "2014-12-18T14:27:32Z"),
            ("now-30d", "2014-10-19T14:27:32Z"),
            ("now+1h", "2014-11-18T15:27:32Z"),
            ("now+1m", "2014-11-18T14:28:32Z"),
            ("now+1s", "2014-11-18T14:27:33Z"),
        ],
    )
    def test_every_offset_unit(self, expression: str, expected: str) -> None:
        assert self._evaluate(expression) == _parse_instant(expected)

    @pytest.mark.parametrize(
        ("expression", "expected"),
        [
            ("now/d", "2014-11-18T00:00:00Z"),
            ("now/w", "2014-11-17T00:00:00Z"),
            ("now/M", "2014-11-01T00:00:00Z"),
            ("now/y", "2014-01-01T00:00:00Z"),
        ],
    )
    def test_every_truncation_unit(self, expression: str, expected: str) -> None:
        assert self._evaluate(expression) == _parse_instant(expected)

    def test_offset_then_truncate_applies_in_order(self) -> None:
        """T21 ②. ``now+1M/d`` offsets first and only then rounds."""
        assert self._evaluate("now+1M/d") == _parse_instant("2014-12-18T00:00:00Z")

    def test_anchored_expression(self) -> None:
        """T21 ③."""
        assert self._evaluate("2021-01-01||+1M/d") == _parse_instant("2021-02-01T00:00:00Z")

    def test_week_starts_on_monday(self) -> None:
        """Calton never overrides the start of the week, and the library defaults to it."""
        # 2021-01-03 is a Sunday, so the Monday-based week began on 2020-12-28.
        assert self._evaluate("2021-01-03||/w") == _parse_instant("2020-12-28T00:00:00Z")
        assert self._evaluate("2021-01-04||/w") == _parse_instant("2021-01-04T00:00:00Z")

    def test_start_of_week_is_configurable(self) -> None:
        options = Options(now=self.NOW, start_of_week=SUNDAY)
        assert parse_and_evaluate("2021-01-03||/w", options) == _parse_instant(
            "2021-01-03T00:00:00Z"
        )


class TestTimezoneHandling:
    """T21 ①: the rounding timezone changes the answer, and the result is UTC."""

    NOW = datetime(2014, 11, 18, 14, 27, 32, tzinfo=UTC)

    def test_truncation_differs_between_zones(self) -> None:
        utc = parse_and_evaluate("now/d", Options(now=self.NOW, location=UTC))
        shanghai = parse_and_evaluate("now/d", Options(now=self.NOW, location=SHANGHAI))

        assert utc == _parse_instant("2014-11-18T00:00:00Z")
        # Midnight in Shanghai is 16:00 UTC the day before, so the two disagree.
        assert shanghai == _parse_instant("2014-11-17T16:00:00Z")
        assert utc != shanghai

    def test_wall_clock_truncation_uses_the_given_zone(self) -> None:
        result = parse_and_evaluate("now/d", Options(now=self.NOW, location=SHANGHAI))
        assert result.astimezone(SHANGHAI).hour == 0

    def test_hour_truncation_is_absolute_not_wall_clock(self) -> None:
        """``/h`` is Go's Truncate, which counts from the zero time in UTC.

        Kolkata is offset by 5h30m, so flooring the instant to an hour lands on half past
        the local hour. Truncating the wall clock instead would give a different instant,
        and nothing would report the difference.
        """
        result = parse_and_evaluate("now/h", Options(now=self.NOW, location=KOLKATA))

        assert result == _parse_instant("2014-11-18T14:00:00Z")
        assert result.astimezone(KOLKATA).minute == 30

    def test_day_truncation_stays_wall_clock_in_a_half_hour_zone(self) -> None:
        result = parse_and_evaluate("now/d", Options(now=self.NOW, location=KOLKATA))
        assert result == _parse_instant("2014-11-17T18:30:00Z")

    def test_the_result_is_returned_as_utc_by_resolve(self) -> None:
        result = resolve_filter_time("now/d", SHANGHAI)
        assert result.utcoffset() == timedelta(0)


class TestCalendarArithmetic:
    """T21 ⑥. Overflow rolls forward; it does not clamp to the end of the month."""

    @pytest.mark.parametrize(
        ("expression", "expected"),
        [
            # 31 January + 1 month is "31 February", which normalises into March.
            ("2021-01-31||+1M", "2021-03-03T00:00:00Z"),
            ("2020-01-31||+1M", "2020-03-02T00:00:00Z"),
            ("2021-12-31||+2M", "2022-03-03T00:00:00Z"),
            ("2020-02-29||+1y", "2021-03-01T00:00:00Z"),
            # Going the other way needs no normalisation.
            ("2021-08-31||-1M", "2021-07-31T00:00:00Z"),
        ],
    )
    def test_month_overflow_rolls_forward(self, expression: str, expected: str) -> None:
        assert parse_and_evaluate(expression) == _parse_instant(expected)

    def test_truncating_after_an_overflow_keeps_the_rolled_date(self) -> None:
        assert parse_and_evaluate("2021-01-31||+1M/d") == _parse_instant("2021-03-03T00:00:00Z")


class TestDaylightSaving:
    """T21 ⑥. Calendar units keep the wall clock; durations keep the instant."""

    # 2021-03-14T07:00Z is when New York springs forward.
    BEFORE_SPRING_FORWARD = datetime(2021, 3, 14, 6, 30, tzinfo=UTC)

    def test_adding_a_day_keeps_the_wall_clock(self) -> None:
        result = parse_and_evaluate(
            "now+1d", Options(now=self.BEFORE_SPRING_FORWARD, location=NEW_YORK)
        )
        # 01:30 EST becomes 01:30 EDT, so only 23 hours of real time pass.
        assert result == _parse_instant("2021-03-15T05:30:00Z")
        assert result.astimezone(NEW_YORK).hour == 1

    def test_adding_an_hour_keeps_the_instant(self) -> None:
        result = parse_and_evaluate(
            "now+1h", Options(now=self.BEFORE_SPRING_FORWARD, location=NEW_YORK)
        )
        assert result == _parse_instant("2021-03-14T07:30:00Z")
        # The wall clock jumps two hours because the zone skipped one.
        assert result.astimezone(NEW_YORK).hour == 3

    def test_adding_an_hour_across_the_autumn_repeat(self) -> None:
        result = parse_and_evaluate(
            "now+1h",
            Options(now=datetime(2021, 11, 7, 5, 30, tzinfo=UTC), location=NEW_YORK),
        )
        assert result.astimezone(UTC) == _parse_instant("2021-11-07T06:30:00Z")


class TestAnchorForms:
    def test_bare_year(self) -> None:
        assert parse_and_evaluate("2014") == _parse_instant("2014-01-01T00:00:00Z")

    def test_truncated_dates_and_times(self) -> None:
        assert parse_and_evaluate("2014-05") == _parse_instant("2014-05-01T00:00:00Z")
        assert parse_and_evaluate("2014-05-30T20:21") == _parse_instant("2014-05-30T20:21:00Z")

    def test_a_bare_time_hangs_off_the_epoch(self) -> None:
        assert parse_and_evaluate("04:52:20") == _parse_instant("1970-01-01T04:52:20Z")

    def test_five_or_more_digits_is_an_epoch_timestamp(self) -> None:
        assert parse_and_evaluate("1418248078000") == _parse_instant("2014-12-10T21:47:58Z")

    def test_a_timestamp_ignores_the_location(self) -> None:
        """It names an instant outright, so rounding zone cannot move it."""
        assert parse_and_evaluate(
            "1418248078000", Options(location=SHANGHAI)
        ) == parse_and_evaluate("1418248078000", Options(location=UTC))

    def test_an_explicit_offset_wins_over_the_location(self) -> None:
        assert parse_and_evaluate(
            "2014-05-30T20:21+03:00", Options(location=SHANGHAI)
        ) == _parse_instant("2014-05-30T17:21:00Z")

    def test_a_lone_anchor_with_pipes_is_allowed(self) -> None:
        assert parse_and_evaluate("2014-05-30T20:21||") == _parse_instant("2014-05-30T20:21:00Z")

    def test_an_omitted_factor_means_one(self) -> None:
        assert parse_and_evaluate("2014-11-18||+y") == _parse_instant("2015-11-18T00:00:00Z")

    def test_expression_keeps_its_source_text(self) -> None:
        assert str(parse("now+1M/d")) == "now+1M/d"


class TestRejections:
    """T21 ⑤: malformed input must be a refusal, never a crash."""

    @pytest.mark.parametrize(
        "expression",
        [
            "nope",
            "",
            "2021-1-1",
            "not a date at all",
            "2021-13-01",
            "2021-02-30",
            "now-nope",
            "definitely-nope",
            "2014-05-35T20:21:21Z",
            "now+",
            "now/",
            "now/x",
        ],
    )
    def test_malformed_input_raises_datematherror(self, expression: str) -> None:
        with pytest.raises(DateMathError):
            parse_and_evaluate(expression)

    def test_the_input_that_makes_the_go_lexer_panic(self) -> None:
        """``"no"`` matches no rule upstream and hits ``panic("scanner internal error")``.

        Calton recovers it, so the observable behaviour is an ordinary refusal. It is
        called out here because it is the specific input that would otherwise be a 500.
        """
        with pytest.raises(DateMathError):
            parse_and_evaluate("no")

    def test_datematherror_is_a_valueerror(self) -> None:
        """So a caller catching ValueError for the whole chain cannot miss it."""
        assert issubclass(DateMathError, ValueError)


class TestFallbackChain:
    """The layouts Calton tries once date maths has refused the value."""

    @pytest.mark.parametrize(
        "case",
        _FALLBACK_CASES,
        ids=[f"{i}:{c['in']!r}@{c['location']}" for i, c in enumerate(_FALLBACK_CASES)],
    )
    def test_matches_go(self, case: dict[str, Any]) -> None:
        location = _location(case["location"])

        if case.get("err"):
            with pytest.raises(ValueError):
                parse_time_from_user_input(case["in"], location)
            return

        result = parse_time_from_user_input(case["in"], location)
        assert result.astimezone(UTC) == _parse_instant(case["got"])

    def test_each_step_of_the_chain_is_reachable(self) -> None:
        """T21 ④. All four, including the hand-rolled one, decide some real input."""
        assert parse_time_from_user_input("2021-01-02T15:04:05Z", UTC) == _parse_instant(
            "2021-01-02T15:04:05Z"
        )
        assert parse_time_from_user_input("2021-01-02 15:04", UTC) == _parse_instant(
            "2021-01-02T15:04:00Z"
        )
        assert parse_time_from_user_input("2021-01-02", UTC) == _parse_instant(
            "2021-01-02T00:00:00Z"
        )
        # Fixed-width layouts reject a one-digit month, so this reaches the split.
        assert parse_time_from_user_input("2021-1-1", UTC) == _parse_instant("2021-01-01T00:00:00Z")

    def test_the_manual_step_normalises_rather_than_validating(self) -> None:
        """It hands the parts to Go's date constructor, which rolls them over."""
        assert parse_time_from_user_input("2021-11-31", UTC) == _parse_instant(
            "2021-12-01T00:00:00Z"
        )

    def test_the_safari_layouts_are_read_in_the_given_zone(self) -> None:
        result = parse_time_from_user_input("2021-01-02", SHANGHAI)
        assert result.astimezone(UTC) == _parse_instant("2021-01-01T16:00:00Z")


class TestResolveFilterTime:
    """The two halves in the order upstream tries them."""

    def test_date_maths_wins_when_it_can_read_the_value(self) -> None:
        now = datetime(2014, 11, 18, 14, 27, 32, tzinfo=UTC)
        assert resolve_filter_time("2021-01-02", UTC) == _parse_instant("2021-01-02T00:00:00Z")
        assert parse_and_evaluate("now/d", Options(now=now)) == _parse_instant(
            "2014-11-18T00:00:00Z"
        )

    def test_the_chain_catches_what_date_maths_refuses(self) -> None:
        assert resolve_filter_time("2021-1-1", UTC) == _parse_instant("2021-01-01T00:00:00Z")
        assert resolve_filter_time("2021-01-02 15:04", UTC) == _parse_instant(
            "2021-01-02T15:04:00Z"
        )

    @pytest.mark.parametrize("value", ["no", "nope", "", "not a date at all", "12345x"])
    def test_a_value_nothing_can_read_raises_valueerror(self, value: str) -> None:
        """The caller turns this into 4019 — a 400, never a 500."""
        with pytest.raises(ValueError):
            resolve_filter_time(value, UTC)

    def test_the_result_is_always_utc(self) -> None:
        for value in ["now", "2021-01-02", "2021-1-1", "2021-01-02 15:04"]:
            assert resolve_filter_time(value, SHANGHAI).utcoffset() == timedelta(0)
