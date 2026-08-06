"""Datetime handling, matching the Go server byte for byte.

Verified against a database produced by the Go binary (`calton migrate`, SQLite):

* Columns are declared ``DATETIME`` and hold TEXT of the form ``YYYY-MM-DD HH:MM:SS``,
  in UTC and truncated to whole seconds — the engine runs with ``SetTZDatabase(GMT)``.
* An unset time is stored as SQL NULL, read back as Go's zero ``time.Time``, and
  serialized as ``"0001-01-01T00:00:00Z"``.

That last point makes the mapping uniform: **NULL in the database is the zero time in
JSON**. It is never null and never a missing key, except on the fields Go tags
``omitzero``/``omitempty``, where the key disappears entirely.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, Any

from pydantic import AfterValidator, BeforeValidator, PlainSerializer, WithJsonSchema
from sqlalchemy import DateTime, Dialect, Integer
from sqlalchemy.dialects import sqlite
from sqlalchemy.types import TypeDecorator

#: Go's zero ``time.Time``.
ZERO_TIME = datetime(1, 1, 1, 0, 0, 0, tzinfo=UTC)

#: How xorm writes a time into SQLite.
_SQLITE_DATETIME = sqlite.DATETIME(  # type: ignore[no-untyped-call]
    storage_format=("%(year)04d-%(month)02d-%(day)02d %(hour)02d:%(minute)02d:%(second)02d"),
    regexp=r"(\d+)-(\d+)-(\d+) (\d+):(\d+):(\d+)",
)


def as_utc(value: datetime) -> datetime:
    """Normalize to an aware UTC datetime, reading naive input as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def format_rfc3339(value: datetime) -> str:
    """Render the way Go's ``time.Time.MarshalJSON`` does (RFC3339Nano).

    Go trims trailing zeros from the fractional part and omits it entirely when zero,
    so ``.120000`` is written ``.12`` and ``.000000`` is written not at all.
    """
    value = as_utc(value)
    rendered = value.strftime("%Y-%m-%dT%H:%M:%S")
    if value.microsecond:
        rendered += "." + f"{value.microsecond:06d}".rstrip("0")
    return rendered + "Z"


def _render(value: datetime | None) -> str | None:
    return None if value is None else format_rfc3339(value)


def parse_rfc3339(value: object) -> object:
    """Accept an RFC3339 string where a datetime is expected.

    Needed because write schemas run under Pydantic ``strict=True`` (required by
    ``CRUDRouter``), and FastAPI validates request bodies in *Python* mode, where strict
    refuses ``str -> datetime`` outright. Without this every write carrying a date would
    422 — including a client echoing back the ``"0001-01-01T00:00:00Z"`` it just read,
    which is exactly what the MCP clients do on read-modify-write.

    Go accepts RFC3339 here (``time.Time.UnmarshalJSON``), so accepting it is the
    faithful behaviour, not a relaxation. Running before the strict check keeps strictness
    everywhere else: only this conversion is permitted, and anything unparseable is handed
    on untouched for Pydantic to reject.
    """
    if not isinstance(value, str):
        return value

    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"

    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return value


def parse_str_enum[EnumT: Enum](enum_type: type[EnumT]) -> Callable[[object], object]:
    """Build a validator accepting the string form of an enum member.

    Strict mode refuses ``str -> Enum`` outright, so a write schema containing a
    ``StrEnum`` rejects exactly what every real client sends: ``view_kind`` arrives as
    ``"list"``, not as the member object. Go decodes the string (the field's Go type is a
    string), so accepting it is faithful rather than a relaxation.

    Anything unrecognised is handed back untouched for Pydantic to reject, which keeps
    strictness everywhere else.
    """

    def parse(value: object) -> object:
        if isinstance(value, enum_type):
            return value
        if isinstance(value, str):
            try:
                return enum_type(value)
            except ValueError:
                return value
        return value

    return parse


def StrEnumValue[EnumT: Enum](enum_type: type[EnumT]) -> Any:  # noqa: N802 - names a type
    """Annotated alias for an enum field that also accepts its string form.

    The rule this satisfies: **any write schema, or any nested object a write schema
    accepts, that contains a StrEnum must convert before strict validation runs.**
    """
    return Annotated[enum_type, BeforeValidator(parse_str_enum(enum_type))]


#: Upstream validates with govalidator, driven by ``valid:"..."`` struct tags, and puts
#: **its own wording** into ``invalid_fields``. Measured, two shapes and only two:
#:
#:   required failing   ->  "title: non zero value required"
#:   anything else      ->  "identifier: AAAAAAAAAAA does not validate as runelength(0|10)"
#:
#: The second quotes the *offending value* and the *tag text verbatim*, parentheses and
#: all. The frontend draws field-level errors from this array, so the field name alone —
#: which is what Calton used to send — loses the reason.
_GO_REQUIRED_MESSAGE = "non zero value required"


def _rune_length(value: object) -> int:
    """Character count, not byte count.

    govalidator's ``runelength`` counts runes, so a 250-character title of non-ASCII text
    is accepted where a byte count would reject it at 84.
    """
    return len(str(value))


#: ``dbtext`` is not a govalidator built-in — Calton registers it in
#: ``pkg/routes/validation.go:35`` and its bound **depends on the database**: 65000 bytes
#: for MySQL, 1048576 for sqlite and postgres. Phase 1 is sqlite only, so the larger bound
#: is the one on the wire here. It is a **byte** count (Go's ``len`` on a string), not a
#: rune count, which is the opposite of ``runelength`` two rules above — measured: 400 000
#: CJK characters is 1.2 MB of UTF-8 and is rejected, while 1 048 576 ASCII bytes is
#: accepted and 1 048 577 is not.
DBTEXT_MAX_BYTES = 1048576


def _byte_length(value: object) -> int:
    """UTF-8 byte count, which is what Go's ``len(str)`` returns."""
    return len(str(value).encode("utf-8"))


def _check_rule(rule: str, value: object) -> bool:
    """Whether ``value`` satisfies one govalidator rule. True means it passed."""
    name, _, params = rule.partition("(")
    bounds = params.rstrip(")").split("|") if params else []

    if name == "required":
        # Go's "required" is a **zero-value** test, which is why a missing key and an
        # empty string are the same case and why a title of spaces passes.
        #
        # ⚠️ A ``datetime`` needs its own arm: every datetime object is truthy in
        # Python, so the generic ``bool(value)`` below calls Go's zero time
        # *present*. ``APIToken.ExpiresAt`` is the first ``required`` time.Time in
        # the codebase and it is the field that makes "you may not mint a token
        # that never expires" enforceable — measured, upstream answers 412
        # ``expires_at: non zero value required`` both when the key is omitted and
        # when it is sent as an explicit ``"0001-01-01T00:00:00Z"``. Without this
        # arm the explicit form is accepted and the policy is silently gone.
        if isinstance(value, datetime):
            return as_utc(value) != ZERO_TIME
        return bool(value) if not isinstance(value, int | float) else value != 0
    if name in ("runelength", "length"):
        low, high = int(bounds[0]), int(bounds[1])
        return low <= _rune_length(value) <= high
    if name == "minstringlength":
        return _rune_length(value) >= int(bounds[0])
    if name == "range":
        low, high = int(bounds[0]), int(bounds[1])
        return isinstance(value, int | float) and low <= value <= high
    if name == "dbtext":
        return _byte_length(value) <= DBTEXT_MAX_BYTES

    raise ValueError(
        f"unsupported govalidator rule {rule!r}. Add it here with a measured example "
        f"rather than guessing its wording — the text goes on the wire."
    )


def GoValid(tag: str) -> Any:  # noqa: N802 - used in an Annotated position, names a type
    """Carry upstream's ``valid:"..."`` tag so the error layer can quote its wording.

    Used as ``Annotated[str, GoValid("required,runelength(1|250)")]``, copying the tag
    text **verbatim** from the Go struct — it is reproduced in the response, so a
    paraphrase is a wire difference.

    Rules are applied in tag order and the **first** failure is reported, which is what
    produces "non zero value required" for an empty title and the runelength message for
    an over-long one, from the same two-rule tag.

    ⚠️ A field carrying ``required`` must also carry a **default** in the schema. Go
    decodes a missing key to the zero value and *then* validates, so "absent" and "empty"
    are indistinguishable upstream; leaving the field required in Pydantic instead makes a
    missing key a ``missing`` error, which reports a different message than the measured
    one.
    """
    rules = [rule.strip() for rule in tag.split(",") if rule.strip()]
    for rule in rules:
        # Fail at import rather than at request time on an unknown rule.
        _check_rule(rule, "probe")

    requires_value = "required" in rules

    def validate(value: object) -> object:
        # ⚠️ govalidator **skips every other rule on a zero value** unless the tag also
        # says ``required``. That is not a detail: ``label.go:34`` tags the title
        # ``runelength(1|250)`` with no ``required``, and an empty label title is measured
        # **201** while a 251-character one is 412. Applying the rules unconditionally
        # rejects the empty one and breaks the single most distinctive thing about the
        # label resource. The lower bound of a runelength is therefore only reachable on a
        # field that is *also* required — where ``required`` reports first anyway.
        if not requires_value and not value:
            return value

        for rule in rules:
            if _check_rule(rule, value):
                continue
            if rule == "required":
                raise ValueError(_GO_REQUIRED_MESSAGE)
            raise ValueError(f"{value} does not validate as {rule}")
        return value

    return AfterValidator(validate)


class OmitZero:
    """Marks a field Go tags ``omitzero``: the key disappears when the value is zero."""


class OmitEmptyPtr:
    """``omitempty`` on a Go pointer: the key disappears when the value is ``None``.

    Go's ``omitempty`` omits a nil pointer. The Python side of that is ``None`` and
    nothing else — a pointer to a zero value is *not* omitted upstream, so this must not
    grow into "omit anything falsy". See :class:`OmitEmptyCollection` for why the two are
    separate markers rather than one.
    """


class OmitEmptyCollection:
    """``omitempty`` on a Go slice or map: the key disappears when it is nil **or empty**.

    This is the half a ``None``-only implementation misses. Go's ``omitempty`` treats an
    empty slice and an empty map as empty, so ``[]`` and ``{}`` vanish from the JSON
    exactly as nil does — while Pydantic's ``exclude_none`` and any ``is None`` check keep
    them. A response that answers ``"buckets": []`` where upstream answers no key at all
    is a difference every parity case on that endpoint reports.

    ⚠️ Deliberately **not** merged with :class:`OmitEmptyPtr` into one "falsy is omitted"
    marker. Go omits an empty slice but keeps ``0``, ``false`` and ``""`` on fields not
    tagged ``omitempty``; a falsy test would silently delete all three. The two markers are
    applied to disjoint field sets and each says which Go tag it stands for.
    """


def _render_go_float(value: float) -> int | float:
    """Render a float the way Go's ``encoding/json`` renders a ``float64``.

    Go writes an integral float64 with no decimal point (``"position":0``); Python's
    ``json`` writes ``0.0``. The values parse equal, so **the parity harness cannot
    see this** — ``diff_paths`` compares parsed numbers and ``0 == 0.0`` — which is
    why it survived 363 corpus cases and only turned up when a T36 recording was
    diffed at the byte level.

    Returning an ``int`` for integral values is what makes ``json.dumps`` emit the
    Go spelling; non-integral values stay floats and render identically either way.

    Applied by field rather than globally, to the fields whose Go counterpart is a
    ``float64`` — ``position`` on task/project/view/bucket/task_position, and
    ``percent_done`` on task. A field whose Go type is ``int`` must not use this:
    it is already spelled correctly and this would only add a second way to be
    wrong.
    """
    # bool is a subclass of int/float in Python but never reaches here: no Go
    # float64 field is boolean, and Pydantic has already coerced to float.
    if value != value or value in (float("inf"), float("-inf")):  # NaN / ±Inf
        return value
    return int(value) if value.is_integer() else value


#: A Go ``float64`` on the wire: integral values render without a decimal point.
#:
#: ``WithJsonSchema`` is load-bearing. Without it the ``int | float`` return type
#: makes Pydantic advertise ``anyOf: [integer, number]``, which is a *contract*
#: change — upstream declares a plain ``number``, and the generated TS type would
#: widen for what is only a spelling difference in the encoder. The declared type
#: stays ``number``; how an integral value is spelled is a rendering detail.
#:
#: Nothing currently in the suite would have caught that: the contract diff
#: compares which fields exist, not their JSON-schema types, so this went green.
GoFloat = Annotated[
    float,
    PlainSerializer(_render_go_float, return_type=int | float, when_used="json"),
    WithJsonSchema({"type": "number"}, mode="serialization"),
]

#: Always present; a zero value renders as ``"0001-01-01T00:00:00Z"``.
Timestamp = Annotated[
    datetime,
    BeforeValidator(parse_rfc3339),
    AfterValidator(as_utc),
    PlainSerializer(_render, return_type=str, when_used="json"),
]

#: Present only when non-zero (Go ``json:"...,omitzero"``, e.g. ``Task.deleted_at``).
OmitZeroTimestamp = Annotated[
    datetime,
    BeforeValidator(parse_rfc3339),
    AfterValidator(as_utc),
    PlainSerializer(_render, return_type=str, when_used="json"),
    OmitZero(),
]

#: Present only when not None.
#:
#: Unlike the two above, this has **no counterpart upstream today** — there is no
#: ``*time.Time`` tagged ``omitempty`` anywhere in ``pkg/models`` or ``pkg/user``, and the
#: fields that do carry ``omitempty`` are all non-temporal. Kept for the Phase 2/3
#: endpoints that are expected to need it; nothing in Phase 1 should reach for it.
OptionalTimestamp = Annotated[
    datetime | None,
    BeforeValidator(parse_rfc3339),
    AfterValidator(lambda v: None if v is None else as_utc(v)),
    PlainSerializer(_render, return_type=str, when_used="json"),
    OmitEmptyPtr(),
]


class CaltonBoolean(TypeDecorator[bool]):
    """A boolean stored the way xorm stores one: an ``INTEGER`` holding 0 or 1.

    SQLAlchemy's ``Boolean`` renders ``BOOLEAN`` in DDL, which would show up as a
    difference against the Go schema.
    """

    impl = Integer
    cache_ok = True

    def process_bind_param(self, value: bool | None, dialect: Dialect) -> int | None:
        return None if value is None else int(value)

    def process_result_value(self, value: int | None, dialect: Dialect) -> bool | None:
        return None if value is None else bool(value)


class CaltonDateTime(TypeDecorator[datetime]):
    """A ``DATETIME`` column that treats SQL NULL and Go's zero time as the same value."""

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(_SQLITE_DATETIME)
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None or value == ZERO_TIME:
            return None
        # The storage format is naive; the value is already normalized to UTC.
        return as_utc(value).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime:
        if value is None:
            return ZERO_TIME
        return as_utc(value)
