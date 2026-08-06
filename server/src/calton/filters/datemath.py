"""Elasticsearch-style date maths, ported from ``github.com/jszwedko/go-datemath``.

Upstream evaluates every date-typed filter value through this grammar
(``task_collection_filter.go:387-397``): ``now``, ``now-30d``, ``now/d``, ``now+1M/d``,
``2021-01-01||+1M/d``. Rounding happens in the caller's timezone and the result is then
converted to UTC.

Three behaviours here are easy to get wrong in Python and are pinned by fixtures rather
than argued from the source:

* **Calendar arithmetic overflows, it does not clamp.** Go's ``AddDate`` builds a date
  from out-of-range parts and lets them roll over, so ``2021-01-31 + 1M`` is 3 March, not
  28 February. ``dateutil.relativedelta`` would clamp and silently shift every
  end-of-month filter boundary by a few days.
* **``y M w d`` are wall-clock, ``h m s`` are absolute.** Adding a day across a DST
  boundary keeps the wall time; adding an hour does not.
* **``/d /w /M /y`` truncate the wall clock, but ``/h /m /s`` truncate absolute time.**
  The latter is Go's ``Time.Truncate``, which counts from the zero time in UTC, so in a
  zone offset by half an hour ``now/h`` does not land on a round local hour.

Error text is deliberately *not* reproduced. Upstream discards it: a datemath failure
falls through to :func:`parse_time_from_user_input`, and if that fails too the value is
reported as ``ErrInvalidTaskFilterValue`` (4019), which interpolates only the field and
the value. Nothing the parser says reaches the client.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from enum import StrEnum

#: Go's ``time.Unix(0, 0)``, used as the date part of a time-only expression.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

#: Go's weekday numbering (Sunday is 0), which is not Python's (Monday is 0).
SUNDAY = 0
MONDAY = 1


class DateMathError(ValueError):
    """The expression is not valid date maths.

    Also raised where upstream's generated lexer *panics* instead of erroring — the
    two-character input ``"no"`` matches no lexer rule and falls through to
    ``panic("scanner internal error")``. Calton wraps the call in a ``recover`` and
    treats it as an ordinary failure, so raising here reproduces its behaviour exactly.
    """


class Unit(StrEnum):
    YEAR = "y"
    FISCAL_YEAR = "fy"
    QUARTER = "Q"
    FISCAL_QUARTER = "fQ"
    MONTH = "M"
    WEEK = "w"
    DAY = "d"
    BUSINESS_DAY = "b"
    HOUR = "h"
    MINUTE = "m"
    SECOND = "s"


#: Single-character units. ``H`` is an accepted spelling of ``h``.
_SINGLE_CHAR_UNITS = {
    "y": Unit.YEAR,
    "Q": Unit.QUARTER,
    "M": Unit.MONTH,
    "w": Unit.WEEK,
    "b": Unit.BUSINESS_DAY,
    "d": Unit.DAY,
    "h": Unit.HOUR,
    "H": Unit.HOUR,
    "m": Unit.MINUTE,
    "s": Unit.SECOND,
}


def _is_not_weekend(moment: datetime) -> bool:
    return moment.weekday() < 5


@dataclass(frozen=True)
class Options:
    """Evaluation settings. Calton only ever sets :attr:`location`."""

    now: datetime | None = None
    location: tzinfo = UTC
    start_of_week: int = MONDAY
    round_up: bool = False
    #: ``None`` means the fiscal year starts on 1 January, matching Go's zero time.
    start_of_fiscal_year: tuple[int, int, int, int, int, int] | None = None
    business_day_func: Callable[[datetime], bool] | None = None

    def resolved_now(self) -> datetime:
        return self.now if self.now is not None else datetime.now(UTC)


# ---------------------------------------------------------------------------
# Go time helpers
# ---------------------------------------------------------------------------


def _go_date(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    second: int,
    microsecond: int,
    location: tzinfo,
) -> datetime:
    """Build a datetime the way Go's ``time.Date`` does — normalising out-of-range parts.

    ``time.Date(2021, 2, 31, ...)`` is 3 March in Go rather than an error, and filters
    depend on it: it is what makes ``2021-01-31||+1M`` land in March.
    """
    month_index = month - 1
    year += month_index // 12
    month = month_index % 12 + 1

    normalised = date(year, month, 1) + timedelta(days=day - 1)

    carry, second = divmod(second, 60)
    minute += carry
    carry, minute = divmod(minute, 60)
    hour += carry
    carry, hour = divmod(hour, 24)
    normalised += timedelta(days=carry)

    return datetime(
        normalised.year,
        normalised.month,
        normalised.day,
        hour,
        minute,
        second,
        microsecond,
        tzinfo=location,
    )


def _add_date(moment: datetime, years: int = 0, months: int = 0, days: int = 0) -> datetime:
    """Go's ``Time.AddDate``: calendar arithmetic on the wall clock, with overflow."""
    return _go_date(
        moment.year + years,
        moment.month + months,
        moment.day + days,
        moment.hour,
        moment.minute,
        moment.second,
        moment.microsecond,
        moment.tzinfo or UTC,
    )


def _add_duration(moment: datetime, delta: timedelta) -> datetime:
    """Go's ``Time.Add``: absolute, so a DST transition shortens or lengthens the day."""
    zone = moment.tzinfo or UTC
    return (moment.astimezone(UTC) + delta).astimezone(zone)


def _truncate_absolute(moment: datetime, unit: timedelta) -> datetime:
    """Go's ``Time.Truncate``: floor the instant, not the wall clock.

    Go counts from its zero time, which sits a whole number of days from the Unix epoch,
    so flooring against the epoch gives the same answer for hour, minute and second.
    """
    zone = moment.tzinfo or UTC
    utc_moment = moment.astimezone(UTC)
    elapsed = utc_moment - _EPOCH
    floored = elapsed - (elapsed % unit)
    return (_EPOCH + floored).astimezone(zone)


def _go_weekday(moment: datetime) -> int:
    """Go numbers weekdays from Sunday; Python numbers them from Monday."""
    return (moment.weekday() + 1) % 7


def _first_day_of_fiscal_year(moment: datetime, options: Options) -> datetime:
    start = options.start_of_fiscal_year or (1, 1, 0, 0, 0, 0)
    month, day, hour, minute, second, microsecond = start
    zone = moment.tzinfo or UTC

    candidate = _go_date(moment.year, month, day, hour, minute, second, microsecond, zone)
    if candidate > moment:
        candidate = _go_date(moment.year - 1, month, day, hour, minute, second, microsecond, zone)
    return candidate


# ---------------------------------------------------------------------------
# Adjustments
# ---------------------------------------------------------------------------


def _add_units(factor: int, unit: Unit) -> Callable[[datetime, Options], datetime]:
    def adjust(moment: datetime, options: Options) -> datetime:
        if unit in (Unit.YEAR, Unit.FISCAL_YEAR):
            return _add_date(moment, years=factor)
        if unit in (Unit.QUARTER, Unit.FISCAL_QUARTER):
            return _add_date(moment, months=3 * factor)
        if unit is Unit.MONTH:
            return _add_date(moment, months=factor)
        if unit is Unit.WEEK:
            return _add_date(moment, days=7 * factor)
        if unit is Unit.DAY:
            return _add_date(moment, days=factor)
        if unit is Unit.BUSINESS_DAY:
            is_business_day = options.business_day_func or _is_not_weekend
            step = 1 if factor >= 0 else -1
            remaining = factor
            while remaining != 0:
                moment = _add_date(moment, days=step)
                while not is_business_day(moment):
                    moment = _add_date(moment, days=step)
                remaining -= step
            return moment
        if unit is Unit.HOUR:
            return _add_duration(moment, timedelta(hours=factor))
        if unit is Unit.MINUTE:
            return _add_duration(moment, timedelta(minutes=factor))
        return _add_duration(moment, timedelta(seconds=factor))

    return adjust


def _truncate_units(unit: Unit) -> Callable[[datetime, Options], datetime]:
    def round_down(moment: datetime, options: Options) -> datetime:
        zone = moment.tzinfo or UTC

        if unit is Unit.YEAR:
            return _go_date(moment.year, 1, 1, 0, 0, 0, 0, zone)
        if unit is Unit.FISCAL_YEAR:
            return _first_day_of_fiscal_year(moment, options)
        if unit is Unit.QUARTER:
            first_month = (moment.month - 1) // 3 * 3 + 1
            return _go_date(moment.year, first_month, 1, 0, 0, 0, 0, zone)
        if unit is Unit.FISCAL_QUARTER:
            first_day = _first_day_of_fiscal_year(moment, options)
            if moment.month >= first_day.month:
                month_delta = moment.month - first_day.month
            else:
                month_delta = moment.month + 12 - first_day.month
            return _add_date(first_day, months=month_delta // 3 * 3)
        if unit is Unit.MONTH:
            return _go_date(moment.year, moment.month, 1, 0, 0, 0, 0, zone)
        if unit is Unit.WEEK:
            difference = _go_weekday(moment) - options.start_of_week
            today = _go_date(moment.year, moment.month, moment.day, 0, 0, 0, 0, zone)
            if difference < 0:
                today = _add_date(today, days=-7)
            return _add_date(today, days=-difference)
        if unit is Unit.DAY:
            return _go_date(moment.year, moment.month, moment.day, 0, 0, 0, 0, zone)
        if unit is Unit.HOUR:
            return _truncate_absolute(moment, timedelta(hours=1))
        if unit is Unit.MINUTE:
            return _truncate_absolute(moment, timedelta(minutes=1))
        return _truncate_absolute(moment, timedelta(seconds=1))

    def adjust(moment: datetime, options: Options) -> datetime:
        rounded = round_down(moment, options)
        if options.round_up:
            return _add_duration(_add_units(1, unit)(rounded, options), timedelta(milliseconds=-1))
        return rounded

    return adjust


# ---------------------------------------------------------------------------
# Lexer and parser
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Anchor:
    """An anchor date. ``now`` and a literal timestamp are the only two forms."""

    is_now: bool = False
    year: int = 0
    month: int = 0
    day: int = 0
    hour: int = 0
    minute: int = 0
    second: int = 0
    microsecond: int = 0
    #: ``None`` means the expression named no zone, so the caller's location applies.
    zone: tzinfo | None = None
    #: Set instead of the fields above when the expression was an epoch timestamp.
    epoch_millis: int | None = None

    def resolve(self, options: Options) -> datetime:
        if self.is_now:
            return options.resolved_now().astimezone(options.location)
        if self.epoch_millis is not None:
            # An absolute instant: upstream reads it through the machine's local zone and
            # rebuilds the same instant, so the location never affects the result.
            return (_EPOCH + timedelta(milliseconds=self.epoch_millis)).astimezone(options.location)
        return _go_date(
            self.year,
            self.month,
            self.day,
            self.hour,
            self.minute,
            self.second,
            self.microsecond,
            self.zone or options.location,
        )


@dataclass(frozen=True)
class Expression:
    """A parsed expression, reusable across evaluations."""

    text: str
    anchor: _Anchor
    adjustments: Sequence[Callable[[datetime, Options], datetime]] = field(default_factory=tuple)

    def time(self, options: Options | None = None) -> datetime:
        """Evaluate to an aware datetime in ``options.location``."""
        resolved = options or Options()
        moment = self.anchor.resolve(resolved)
        for adjustment in self.adjustments:
            moment = adjustment(moment, resolved)
        return moment

    def __str__(self) -> str:
        return self.text


_DIGITS = re.compile(r"[0-9]+")


class _Parser:
    """Recursive descent over ``datemath.y``'s grammar.

    The upstream parser is generated (golex plus goyacc); this accepts the same language
    by hand. Only acceptance and the resulting instant are reproduced — see the module
    docstring on why the error text is not.
    """

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0

    # -- primitives ------------------------------------------------------

    def _at_end(self) -> bool:
        return self.pos >= len(self.text)

    def _peek(self) -> str:
        return "" if self._at_end() else self.text[self.pos]

    def _digit_run(self) -> str:
        match = _DIGITS.match(self.text, self.pos)
        return match.group() if match else ""

    def _take_digits(self, count: int) -> int:
        run = self.text[self.pos : self.pos + count]
        if len(run) != count or not run.isdigit():
            raise DateMathError(f"expected {count} digits in {self.text!r}")
        self.pos += count
        return int(run)

    def _expect(self, character: str) -> None:
        if self._peek() != character:
            raise DateMathError(f"expected {character!r} in {self.text!r}")
        self.pos += 1

    # -- grammar ---------------------------------------------------------

    def parse(self) -> Expression:
        if self.text.startswith("now", self.pos):
            self.pos += 3
            anchor = _Anchor(is_now=True)
            adjustments = self._date_math_expressions(required=False)
        else:
            anchor = self._absolute_date_expression()
            adjustments = ()
            if self.text.startswith("||", self.pos):
                self.pos += 2
                adjustments = self._date_math_expressions(required=False)

        if not self._at_end():
            raise DateMathError(f"trailing input in {self.text!r}")

        return Expression(text=self.text, anchor=anchor, adjustments=adjustments)

    def _absolute_date_expression(self) -> _Anchor:
        run = self._digit_run()

        # Five digits or more is a millisecond timestamp; four is a year; two is an hour.
        if len(run) >= 5:
            self.pos += len(run)
            return _Anchor(epoch_millis=int(run))

        if len(run) == 4:
            year, month, day = self._date()
            if self._peek() != "T":
                return _Anchor(year=year, month=month, day=day)
            self.pos += 1
            hour, minute, second, microsecond = self._time()
            return _Anchor(
                year=year,
                month=month,
                day=day,
                hour=hour,
                minute=minute,
                second=second,
                microsecond=microsecond,
                zone=self._timezone(),
            )

        if len(run) == 2:
            hour, minute, second, microsecond = self._time()
            # A bare time hangs off the epoch date, and takes no timezone: the grammar
            # only allows one after a full date-and-time.
            return _Anchor(
                year=_EPOCH.year,
                month=_EPOCH.month,
                day=_EPOCH.day,
                hour=hour,
                minute=minute,
                second=second,
                microsecond=microsecond,
            )

        raise DateMathError(f"not an anchor date: {self.text!r}")

    def _date(self) -> tuple[int, int, int]:
        year = self._take_digits(4)
        if self._peek() != "-":
            return year, 1, 1
        self.pos += 1

        month = self._take_digits(2)
        if month > 12:
            raise DateMathError(f"month out of bounds {month}")
        if self._peek() != "-":
            return year, month, 1
        self.pos += 1

        day = self._take_digits(2)
        if day > _days_in(month, year):
            raise DateMathError(f"day {day} out of bounds for month {month}")
        return year, month, day

    def _time(self) -> tuple[int, int, int, int]:
        hour = self._take_digits(2)
        if hour > 23:
            raise DateMathError(f"hours out of bounds {hour}")
        if self._peek() != ":":
            return hour, 0, 0, 0
        self.pos += 1

        minute = self._take_digits(2)
        if minute > 59:
            raise DateMathError(f"minutes out of bounds {minute}")
        if self._peek() != ":":
            return hour, minute, 0, 0
        self.pos += 1

        second = self._take_digits(2)
        if second > 59:
            raise DateMathError(f"seconds out of bounds {second}")
        if self._peek() != ".":
            return hour, minute, second, 0
        self.pos += 1

        # One to three fractional digits, each place worth ten times less.
        fraction = self._digit_run()
        if not 1 <= len(fraction) <= 3:
            raise DateMathError(f"invalid fractional seconds in {self.text!r}")
        self.pos += len(fraction)
        nanoseconds = int(fraction) * 10 ** (9 - len(fraction))
        return hour, minute, second, nanoseconds // 1000

    def _timezone(self) -> tzinfo | None:
        character = self._peek()

        if character == "Z":
            self.pos += 1
            return UTC

        if character in ("+", "-"):
            sign = 1 if character == "+" else -1
            self.pos += 1
            hours = self._take_digits(2)
            self._expect(":")
            minutes = self._take_digits(2)
            return timezone(sign * timedelta(hours=hours, minutes=minutes))

        return None

    def _date_math_expressions(
        self, *, required: bool
    ) -> tuple[Callable[[datetime, Options], datetime], ...]:
        adjustments: list[Callable[[datetime, Options], datetime]] = []
        while not self._at_end():
            adjustments.append(self._date_math_expression())
        if required and not adjustments:
            raise DateMathError(f"expected a date math expression in {self.text!r}")
        return tuple(adjustments)

    def _date_math_expression(self) -> Callable[[datetime, Options], datetime]:
        character = self._peek()

        if character == "/":
            self.pos += 1
            return _truncate_units(self._unit())

        if character in ("+", "-"):
            sign = 1 if character == "+" else -1
            self.pos += 1
            run = self._digit_run()
            # An omitted factor means one, so "+M" is "+1M".
            factor = 1
            if run:
                self.pos += len(run)
                factor = int(run)
            return _add_units(sign * factor, self._unit())

        raise DateMathError(f"expected + - or / in {self.text!r}")

    def _unit(self) -> Unit:
        if self.text.startswith("fy", self.pos):
            self.pos += 2
            return Unit.FISCAL_YEAR
        if self.text.startswith("fQ", self.pos):
            self.pos += 2
            return Unit.FISCAL_QUARTER

        unit = _SINGLE_CHAR_UNITS.get(self._peek())
        if unit is None:
            raise DateMathError(f"unknown time unit in {self.text!r}")
        self.pos += 1
        return unit


def _days_in(month: int, year: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - timedelta(days=1)).day


def parse(text: str) -> Expression:
    """Parse a date math expression, raising :class:`DateMathError` if it is not one."""
    return _Parser(text).parse()


def parse_and_evaluate(text: str, options: Options | None = None) -> datetime:
    return parse(text).time(options)


# ---------------------------------------------------------------------------
# Calton's fallback chain
# ---------------------------------------------------------------------------

#: ``time.RFC3339``. Go requires the offset, so a bare ``2021-01-02T15:04:05`` fails here
#: and drops through to the layouts below.
_RFC3339 = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})(\.\d+)?"
    r"([Zz]|[+-]\d{2}:\d{2})$"
)
#: What Safari hands back for a datetime-local input, and then for a date input.
_SAFARI_DATE_AND_TIME = re.compile(r"^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})$")
_SAFARI_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def parse_time_from_user_input(value: str, location: tzinfo) -> datetime:
    """Upstream's four-step fallback for values date maths could not read.

    RFC3339, then ``2006-01-02 15:04``, then ``2006-01-02``, then a hand-rolled split on
    ``-``. The last step is not redundant: Go's layouts are fixed-width, so ``2021-1-1``
    reaches it — and because it feeds the parts straight to ``time.Date``, which
    normalises, ``2021-11-31`` comes back as 1 December rather than an error.
    """
    match = _RFC3339.match(value)
    if match:
        year, month, day, hour, minute, second = (int(g) for g in match.groups()[:6])
        fraction = match.group(7)
        microsecond = round(float(fraction) * 1_000_000) if fraction else 0
        offset = match.group(8)
        zone = (
            UTC
            if offset in ("Z", "z")
            else timezone(
                (1 if offset[0] == "+" else -1)
                * timedelta(hours=int(offset[1:3]), minutes=int(offset[4:6]))
            )
        )
        return _go_date(year, month, day, hour, minute, second, microsecond, zone)

    match = _SAFARI_DATE_AND_TIME.match(value)
    if match:
        year, month, day, hour, minute = (int(g) for g in match.groups())
        return _go_date(year, month, day, hour, minute, 0, 0, location)

    match = _SAFARI_DATE.match(value)
    if match:
        year, month, day = (int(g) for g in match.groups())
        return _go_date(year, month, day, 0, 0, 0, 0, location)

    parts = value.split("-")
    if len(parts) < 3:
        raise ValueError(f"cannot parse {value!r} as a time")
    try:
        year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError as error:
        raise ValueError(f"cannot parse {value!r} as a time") from error

    return _go_date(year, month, day, 0, 0, 0, 0, location)


def resolve_filter_time(value: str, location: tzinfo) -> datetime:
    """Read a filter value as a UTC instant, date maths first and layouts after.

    Raises :class:`ValueError` when nothing can read it; the caller turns that into
    ``ErrInvalidTaskFilterValue`` (4019).
    """
    try:
        return parse_and_evaluate(value, Options(location=location)).astimezone(UTC)
    except DateMathError:
        return parse_time_from_user_input(value, location).astimezone(UTC)
