"""Compile a parsed filter into a SQLAlchemy condition.

The input is what :func:`calton.filters.parser.parse_task_filter` produced — validated
field names and comparators, with values still as raw text. This module casts those
values to their column types and builds the WHERE clause
(``task_search.go:186-308``, ``tasks.go:268-305``).

Most of it is unremarkable. The sub-table fields are not:

**Labels, assignees and reminders live in their own tables, and each condition becomes
its own EXISTS.** That is what makes ``labels = 4 && labels = 5`` mean "has both labels"
rather than the impossible "one row equal to two values".

**Except that consecutive range comparisons over the same table are merged into a single
EXISTS**, because those are conditions one row can satisfy at once —
``reminders > X && reminders < Y`` should mean one reminder inside the window, not two
reminders that between them straddle it. Four things must hold to merge, and any one of
them failing ends the group:

1. the comparator is one of ``> >= < <=``;
2. the join to the next condition is AND;
3. the next condition targets the same sub-table;
4. the two are **adjacent in the expression**.

Condition 4 is the one to be careful about. The natural implementation groups by field
name — collect every ``reminders`` condition, then merge — and that is wrong:
``reminders > X && labels = 1 && reminders < Y`` must produce three separate EXISTS
because the ``labels`` condition sits between them. Grouping by field would merge the two
``reminders`` bounds into "one reminder both after X and before Y", which quietly returns
a smaller result set. Nothing errors; the rows are just wrong.

``assignees like ...`` is dropped on the floor — not an error, and not a filter either.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from typing import Any, cast

from sqlalchemy import ColumnElement, String, and_, exists, literal, or_, select
from sqlalchemy.orm import InstrumentedAttribute

from calton.core.errors import CaltonError
from calton.db.types import as_utc
from calton.filters.datemath import resolve_filter_time
from calton.filters.parser import FilterCondition, FilterGroup, Join
from calton.models.label import LabelTask
from calton.models.task import Task
from calton.models.task_assignee import TaskAssignee
from calton.models.task_position import TaskBucket
from calton.models.task_reminder import TaskReminder
from calton.models.user import User

#: Comparators whose conditions a single sub-table row can satisfy simultaneously, and so
#: the only ones that may share an EXISTS (``task_search.go:87-94``).
RANGE_COMPARATORS = frozenset({">", ">=", "<", "<="})

#: Comparators rewritten to ``in`` for the *inner* sub-table condition. Negation is
#: carried by wrapping the subquery in NOT EXISTS instead, which is what keeps a task
#: with no rows at all in the result (``task_search.go:233-238``).
_STRICT_COMPARATORS = frozenset({"in", "not in", "=", "!="})

_NEGATING_COMPARATORS = frozenset({"!=", "not in"})


@dataclass(frozen=True)
class SubTableSpec:
    """How a sub-table field is filtered: which table, and on which column."""

    name: str
    column: InstrumentedAttribute[Any]
    #: Correlates the subquery to the outer task row.
    task_id_column: InstrumentedAttribute[Any]
    #: Whether ``filter_include_nulls`` also matches tasks with no rows at all.
    allow_null_check: bool = True


#: ⚠️ The field lists in this module are **hand-copies of upstream's enumerations**, so
#: they cannot be made wrong by anything we change here — only by bumping the vendored
#: Go source. No local edit touches them, which is why no local test guards them. The
#: trigger and the six affected lists are registered in `harness/README.md`, under
#: "Lists that expire when upstream moves".

#: ``task_search.go:36-77``. ``assignees`` filters on the *username*, not the user id,
#: which is why its subquery has to reach into the users table.
SUB_TABLE_FIELDS = {
    "labels": SubTableSpec("labels", LabelTask.label_id, LabelTask.task_id),
    "reminders": SubTableSpec("reminders", TaskReminder.reminder, TaskReminder.task_id),
    "assignees": SubTableSpec("assignees", User.username, TaskAssignee.task_id),
}

#: Fields whose value is a timestamp, so date maths applies.
_DATE_FIELDS = frozenset({"due_date", "start_date", "end_date", "done_at", "created", "updated"})
#: Numeric columns, for casting and for the ``filter_include_nulls`` "0 counts as unset"
#: rule.
#:
#: ⚠️ ``position`` and ``bucket_id`` are listed here for value *casting*, but neither is a
#: column of ``tasks`` — they live on ``task_positions`` and ``task_buckets``. The two are
#: structurally identical and are handled **differently**, on purpose (T28):
#:
#: * ``bucket_id`` **is compiled**, by :func:`_bucket_id_condition`, as a subquery. No view
#:   context is required, because a bucket belongs to exactly one view — see that
#:   function for the measurements.
#: * ``position`` **still raises 4016**, and must keep doing so. Upstream answers **500**
#:   to ``position > 1`` (re-measured 2026-08-04, unchanged), and a controlled 400 is a
#:   better answer than a reproduced crash. This is a registered deviation, not an
#:   oversight, and ``test_position_is_rejected_here_and_500s_upstream`` guards it.
#:
#: This comment previously said *both* were uncompilable and cited ``bucket_id = 3``
#: returning "200 with an empty list" as evidence that the field did nothing upstream.
#: Bucket 3 is simply empty — the probe could not tell "works" from "no-op". Re-probing
#: with a bucket that holds tasks showed it filtering correctly all along.
_NUMERIC_FIELDS = frozenset(
    {
        "id",
        "project_id",
        "priority",
        "percent_done",
        "repeat_after",
        "created_by_id",
        "position",
        "bucket_id",
        "index",
        "uid",
    }
)
_BOOLEAN_FIELDS = frozenset({"done"})

#: Go's ``strconv.ParseBool``.
_TRUE_LITERALS = frozenset({"1", "t", "T", "TRUE", "true", "True"})
_FALSE_LITERALS = frozenset({"0", "f", "F", "FALSE", "false", "False"})


def _invalid_value(field: str, value: str) -> CaltonError:
    return CaltonError.from_name("models.ErrInvalidTaskFilterValue", field=field, value=value)


def _cast_scalar(field: str, value: str, location: tzinfo) -> Any:
    """Cast one raw value to the type its column holds."""
    value = value.strip()

    if field == "assignees":
        # Compared against users.username, so it stays text.
        return value

    if field == "labels":
        try:
            return int(value)
        except ValueError as error:
            raise _invalid_value(field, value) from error

    if field == "reminders" or field in _DATE_FIELDS:
        try:
            return resolve_filter_time(value, location)
        except ValueError as error:
            raise _invalid_value(field, value) from error

    if field in _BOOLEAN_FIELDS:
        if value in _TRUE_LITERALS:
            return True
        if value in _FALSE_LITERALS:
            return False
        raise _invalid_value(field, value)

    if field == "percent_done":
        try:
            return float(value)
        except ValueError as error:
            raise _invalid_value(field, value) from error

    if field in _NUMERIC_FIELDS:
        try:
            return int(value)
        except ValueError as error:
            raise _invalid_value(field, value) from error

    return value


def cast_value(condition: FilterCondition, location: tzinfo) -> Any:
    """Cast a condition's value, splitting on commas for ``in`` and ``not in``.

    ``assignees`` always splits, whatever the comparator: its value is a username list
    (``getNativeValueForTaskField``).
    """
    if condition.field == "assignees":
        return [part.strip() for part in condition.value.split(",")]

    if condition.comparator in ("in", "not in"):
        return [
            _cast_scalar(condition.field, part, location) for part in condition.value.split(",")
        ]

    return _cast_scalar(condition.field, condition.value, location)


def _go_bound(value: Any) -> Any:
    """Render a datetime the way upstream binds it into a filter's SQL.

    ⚠️ **Text, with the UTC offset, not a datetime.** SQLite stores these columns as TEXT
    and compares them as TEXT, so a filter only ever matches rows whose stored spelling is
    identical. Every datetime in the seed is written ``2026-05-01 00:00:00+00:00``, and
    upstream's comparisons carry the same suffix — while ``CaltonDateTime`` binds a
    Python datetime as ``2026-05-01 00:00:00``. Those two never compare equal, so **every
    equality against a datetime column returned an empty set**: measured,
    ``created = '2026-01-01'`` answers 50 tasks upstream and 0 here, and the same held for
    ``due_date`` and ``reminders``. Ordering comparisons were unaffected, which is why one
    corpus case out of the whole date surface is what caught it.

    ⚠️ This deliberately reproduces an upstream inconsistency, so do not "correct" it to
    compare instants. Upstream **writes** the bare spelling and **queries** with the
    offset, so a reminder created through its own API can never be found by an equality
    filter on it. Measured directly: two rows for the same instant, one written by
    upstream (``2026-05-01 00:00:00``) and one carrying the offset, and
    ``reminders = '2026-05-01'`` returns only the second — see
    ``harness/probe_coder_e_reminder_eq.py``. Normalising both sides here would make
    Calton match rows upstream does not.
    """
    if isinstance(value, list):
        return [_go_bound(item) for item in value]
    if isinstance(value, datetime):
        # ⚠️ An explicitly ``String``-typed literal, not a bare ``str``. SQLAlchemy types a
        # bind parameter from the column it is compared against, so a plain string here is
        # bound as ``CaltonDateTime`` and handed back to ``process_bind_param``, which
        # renders it as the bare spelling again — undoing this function while looking like
        # it worked. (It does not fail silently: it raises, and the endpoint 500s.)
        return literal(as_utc(value).isoformat(sep=" "), String())
    return value


def _comparison(
    column: Any, comparator: str, value: Any, field: str, raw: str | None = None
) -> ColumnElement[bool]:
    """Apply one comparator to one column.

    ``raw`` is the value as the client typed it, used only for the 4019 message. Without
    it the message renders the *cast* value, so ``done like true`` reported
    ``value 'True'`` — Python's ``repr`` of a bool, echoed back at a user who wrote
    ``true``. The point of not reproducing upstream's message here is that ours is the
    clean one; leaking an implementation language into it gives that up.

    ``column`` is typed loosely because the operators come from SQLAlchemy's
    ``ColumnOperators`` mixin, whose overloads return ``Any`` for the ordering
    comparisons rather than a boolean expression.
    """
    # Every path below funnels through here, so the datetime spelling is fixed once
    # rather than at each of the leaf, sub-table and bucket call sites.
    value = _go_bound(value)
    if comparator == "=":
        return cast("ColumnElement[bool]", column == value)
    if comparator == "!=":
        return cast("ColumnElement[bool]", column != value)
    if comparator == ">":
        return cast("ColumnElement[bool]", column > value)
    if comparator == ">=":
        return cast("ColumnElement[bool]", column >= value)
    if comparator == "<":
        return cast("ColumnElement[bool]", column < value)
    if comparator == "<=":
        return cast("ColumnElement[bool]", column <= value)
    if comparator == "like":
        if not isinstance(value, str):
            raise _invalid_value(field, raw if raw is not None else str(value))
        # Both sides, so it is a "contains" rather than a prefix match.
        return cast("ColumnElement[bool]", column.like(f"%{value}%"))
    if comparator == "in":
        return cast(
            "ColumnElement[bool]", column.in_(value if isinstance(value, list) else [value])
        )
    return cast("ColumnElement[bool]", column.not_in(value if isinstance(value, list) else [value]))


#: ``bucket_id`` is the one whitelisted field that is not a column of ``tasks``. It lives
#: on ``task_buckets``, so it compiles to a membership test rather than a comparison.
BUCKET_ID_FIELD = "bucket_id"


def _bucket_id_condition(condition: FilterCondition, location: tzinfo) -> ColumnElement[bool]:
    """``bucket_id <op> N`` as a subquery against ``task_buckets``.

    ⚠️ **No view scoping, and that is measured rather than assumed.** The obvious reading
    is that this needs a project view in scope — a task sits in a different bucket in each
    view, so "which bucket" looks meaningless without one, and this module has no view
    context. It is not needed: **a bucket belongs to exactly one view**, so naming a bucket
    id already pins the view. Confirmed against the reference server — the same filter
    returns the same three tasks through a Kanban view, through a List view, and through
    two endpoints with no view at all::

        GET /projects/970/views/973/tasks?filter=bucket_id = 970   -> [9700, 9701, 9702]
        GET /projects/970/views/974/tasks?filter=bucket_id = 970   -> [9700, 9701, 9702]
        GET /tasks?filter=bucket_id = 970                          -> [9700, 9701, 9702]
        GET /projects/970/tasks?filter=bucket_id = 970             -> [9700, 9701, 9702]

    ⚠️ This module's header previously recorded ``bucket_id = 3`` as answering *200 with
    an empty list* upstream and concluded the field was effectively unsupported. The
    status was right and the conclusion was wrong: **bucket 3 simply has no tasks.**
    Probing with a bucket that does have tasks shows it filtering properly. A measurement
    taken on an input that cannot distinguish "works" from "does nothing" reads exactly
    like the feature being absent.

    ``position`` is the sibling field and is **deliberately still rejected** with 4016:
    upstream answers **500** for ``position > 1`` (re-measured just now, still true), and
    reproducing a crash is worse than diverging from one.
    """
    value = cast_value(condition, location)
    values = value if isinstance(value, list) else [value]
    members = select(TaskBucket.task_id).where(TaskBucket.bucket_id.in_(values))

    if condition.comparator in ("!=", "not in"):
        return cast("ColumnElement[bool]", Task.id.not_in(members))
    return cast("ColumnElement[bool]", Task.id.in_(members))


def _leaf_condition(
    condition: FilterCondition, location: tzinfo, include_nulls: bool
) -> ColumnElement[bool]:
    """A condition on a column of ``tasks`` itself."""
    if condition.field == BUCKET_ID_FIELD:
        return _bucket_id_condition(condition, location)

    column = getattr(Task, condition.field, None)
    if column is None:
        raise CaltonError.from_name("models.ErrInvalidTaskField", task_field=condition.field)

    value = cast_value(condition, location)
    compiled = _comparison(column, condition.comparator, value, condition.field, condition.value)

    if include_nulls:
        compiled = or_(compiled, column.is_(None))
        # Numeric columns also treat 0 as "unset", because that is what an int column
        # holds when nothing was ever written to it.
        if condition.field in _NUMERIC_FIELDS:
            compiled = or_(compiled, column.is_(None), column == 0)

    return compiled


def _base_subquery(spec: SubTableSpec) -> Any:
    """``SELECT 1 FROM <table> WHERE tasks.id = task_id``, before any value condition."""
    query = select(1).where(spec.task_id_column == Task.id)
    if spec.name == "assignees":
        # username lives on users, so the assignee row has to be joined to it.
        query = query.where(User.id == TaskAssignee.user_id)
    return query


def _sub_table_condition(
    group: list[FilterCondition], location: tzinfo, include_nulls: bool
) -> ColumnElement[bool]:
    """Build one EXISTS covering every condition in ``group``.

    The group holds one condition, or several range conditions that were merged.
    """
    spec = SUB_TABLE_FIELDS[group[0].field]

    inner: list[ColumnElement[bool]] = []
    for condition in group:
        comparator = condition.comparator
        value = cast_value(condition, location)
        # Equality and negation both become IN on the inside; see _STRICT_COMPARATORS.
        if comparator in _STRICT_COMPARATORS:
            comparator = "in"
            if not isinstance(value, list):
                value = [value]
        inner.append(_comparison(spec.column, comparator, value, condition.field, condition.value))

    subquery = _base_subquery(spec).where(and_(*inner))

    # The *first* condition decides whether the whole group is negated, matching upstream
    # -- a merged group is all range comparators, so none of them can be negating.
    if group[0].comparator in _NEGATING_COMPARATORS:
        compiled: ColumnElement[bool] = ~exists(subquery)
    else:
        compiled = exists(subquery)

    if include_nulls and spec.allow_null_check:
        compiled = or_(compiled, ~exists(_base_subquery(spec)))

    return compiled


def _is_mergeable_successor(
    current: FilterCondition, candidate: FilterCondition | FilterGroup
) -> bool:
    """Whether ``candidate`` may join ``current``'s EXISTS.

    Adjacency is enforced by the caller, which only ever offers the immediately following
    element — that is the whole point, and why this takes one candidate rather than a list.
    """
    if not isinstance(candidate, FilterCondition):
        return False
    if candidate.field not in SUB_TABLE_FIELDS:
        return False
    if SUB_TABLE_FIELDS[candidate.field].name != SUB_TABLE_FIELDS[current.field].name:
        return False
    if candidate.join is not Join.AND:
        return False
    return candidate.comparator in RANGE_COMPARATORS


def compile_filter(
    nodes: list[FilterCondition | FilterGroup],
    *,
    include_nulls: bool = False,
    location: tzinfo = UTC,
) -> ColumnElement[bool] | None:
    """Compile parsed filter nodes into a WHERE clause, or ``None`` if there are none."""
    compiled: list[ColumnElement[bool]] = []
    joins: list[Join] = []

    index = 0
    while index < len(nodes):
        node = nodes[index]

        if isinstance(node, FilterGroup):
            nested = compile_filter(
                list(node.conditions), include_nulls=include_nulls, location=location
            )
            if nested is not None:
                compiled.append(nested)
                joins.append(node.join)
            index += 1
            continue

        if node.field in SUB_TABLE_FIELDS:
            if node.field == "assignees" and node.comparator == "like":
                # Silently dropped: not an error, and not a filter (task_search.go:207).
                index += 1
                continue

            group = [node]
            if node.comparator in RANGE_COMPARATORS:
                while index + 1 < len(nodes) and _is_mergeable_successor(node, nodes[index + 1]):
                    successor = nodes[index + 1]
                    assert isinstance(successor, FilterCondition)
                    group.append(successor)
                    index += 1

            compiled.append(_sub_table_condition(group, location, include_nulls))
            # The group attaches to what precedes it using its *first* condition's join.
            joins.append(node.join)
            index += 1
            continue

        compiled.append(_leaf_condition(node, location, include_nulls))
        joins.append(node.join)
        index += 1

    if not compiled:
        return None

    # A strict left fold: upstream applies each join to everything accumulated so far, so
    # `a || b && c` is `(a || b) && c`. There is no AND-over-OR precedence.
    result = compiled[0]
    for position in range(1, len(compiled)):
        if joins[position] is Join.OR:
            result = or_(result, compiled[position])
        else:
            result = and_(result, compiled[position])

    return result


def compile_filter_string(
    filter_string: str, *, include_nulls: bool = False, location: tzinfo = UTC
) -> ColumnElement[bool] | None:
    """Parse and compile in one step."""
    from calton.filters.parser import parse_task_filter

    return compile_filter(
        parse_task_filter(filter_string), include_nulls=include_nulls, location=location
    )


__all__ = [
    "RANGE_COMPARATORS",
    "SUB_TABLE_FIELDS",
    "SubTableSpec",
    "cast_value",
    "compile_filter",
    "compile_filter_string",
]
