"""The fexpr expression parser and Calton's validation layer on top of it.

Two layers live here, and keeping them apart matters:

* :func:`parse` is a port of ``github.com/ganigeorgiev/fexpr`` v0.6.0 ``parser.go`` — a
  130-line state machine over the scanner in :mod:`calton.filters.lexer`. It knows
  nothing about tasks: any identifier and any sign operator the scanner accepts parses.
* :func:`parse_task_filter` is Calton's ``parseFilterFromExpression``
  (``task_collection_filter.go:113-168``), which preprocesses the string, parses it, and
  then rejects fields outside the whitelist (4016) and comparators outside the supported
  set (4017).

That split is why ``title ?~ 'x'`` parses cleanly but is still a 400: fexpr has a whole
family of ``?``-prefixed "any" operators, and Calton supports only two of them.

Values are left as their raw literal text. Casting them to native types needs date maths,
so it belongs to the compiler (T21/T22), not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from calton.core.errors import CaltonError
from calton.filters.lexer import (
    ERR_EMPTY,
    ERR_INCOMPLETE,
    FilterExpressionError,
    Scanner,
    Token,
    TokenType,
    go_quote,
    preprocess_filter,
)


class Join(StrEnum):
    """How an expression joins to the one before it."""

    AND = "&&"
    OR = "||"


@dataclass(frozen=True)
class Expr:
    """A single ``left op right`` comparison."""

    left: Token
    op: str
    right: Token


@dataclass(frozen=True)
class ExprGroup:
    """An expression plus the join that precedes it.

    ``item`` is either a single :class:`Expr` or, for a parenthesised sub-expression, a
    tuple of nested groups.
    """

    join: Join
    item: Expr | tuple[ExprGroup, ...]


#: The token types that may stand as an operand on either side of a comparison.
_OPERAND_TYPES = (
    TokenType.IDENTIFIER,
    TokenType.TEXT,
    TokenType.NUMBER,
    TokenType.FUNCTION,
)

# Parser state machine steps.
_STEP_BEFORE_SIGN = 0
_STEP_SIGN = 1
_STEP_AFTER_SIGN = 2
_STEP_JOIN = 3


def parse(text: str) -> list[ExprGroup]:
    """Parse ``text`` into a flat list of groups. Whitespace and comments are ignored.

    Nesting is handled by recursion: the scanner hands back a parenthesised run as one
    group token holding its inner text, which is parsed again from scratch.
    """
    result: list[ExprGroup] = []
    scanner = Scanner(text)
    step = _STEP_BEFORE_SIGN
    join = Join.AND

    # None until a left operand is read; upstream tracks the same thing as a zero-valued
    # Expr, which is what tells ErrEmpty apart from ErrIncomplete at the end.
    left: Token | None = None
    op = ""

    while True:
        token = scanner.scan()

        if token.type == TokenType.EOF:
            break

        if token.type in (TokenType.WHITESPACE, TokenType.COMMENT):
            continue

        if token.type == TokenType.GROUP:
            nested = parse(token.literal)
            # An empty group contributes nothing rather than an empty node.
            if nested:
                result.append(ExprGroup(join=join, item=tuple(nested)))
            step = _STEP_JOIN
            continue

        if step == _STEP_BEFORE_SIGN:
            if token.type not in _OPERAND_TYPES:
                raise FilterExpressionError(
                    f"expected left operand (identifier, function, text or number), "
                    f"got {_describe(token)}"
                )
            left = token
            step = _STEP_SIGN

        elif step == _STEP_SIGN:
            if token.type != TokenType.SIGN:
                raise FilterExpressionError(f"expected a sign operator, got {_describe(token)}")
            op = token.literal
            step = _STEP_AFTER_SIGN

        elif step == _STEP_AFTER_SIGN:
            if token.type not in _OPERAND_TYPES:
                raise FilterExpressionError(
                    f"expected right operand (identifier, function text or number), "
                    f"got {_describe(token)}"
                )
            assert left is not None  # the state machine cannot reach here without one
            result.append(ExprGroup(join=join, item=Expr(left=left, op=op, right=token)))
            step = _STEP_JOIN

        else:  # _STEP_JOIN
            if token.type != TokenType.JOIN:
                raise FilterExpressionError(f"expected && or ||, got {_describe(token)}")
            join = Join.OR if token.literal == "||" else Join.AND
            step = _STEP_BEFORE_SIGN

    if step != _STEP_JOIN:
        # Nothing was consumed at all, versus a comparison that was cut off partway.
        if not result and left is None and op == "":
            raise FilterExpressionError(ERR_EMPTY)
        raise FilterExpressionError(ERR_INCOMPLETE)

    return result


def _describe(token: Token) -> str:
    """Format a token the way Go's ``%q (%s)`` pair does in the parser's messages."""
    return f"{go_quote(token.literal)} ({token.type})"


# ---------------------------------------------------------------------------
# Calton's validation layer
# ---------------------------------------------------------------------------
#: ⚠️ The field lists in this module are **hand-copies of upstream's enumerations**, so
#: they cannot be made wrong by anything we change here — only by bumping the vendored
#: Go source. No local edit touches them, which is why no local test guards them. The
#: trigger and the six affected lists are registered in `harness/README.md`, under
#: "Lists that expire when upstream moves".

#: Fields that may be sorted by, from ``validateTaskFieldForSorting``
#: (``task_collection_sort.go:95-121``). Every one of them is also filterable.
#:
#: ⚠️ ``position`` and ``bucket_id`` pass validation here but cannot be *compiled* yet:
#: neither is a column of ``tasks``, and the join upstream uses needs a project view.
#: See the note on ``_NUMERIC_FIELDS`` in :mod:`calton.filters.compiler` and T28.
SORTABLE_TASK_FIELDS = frozenset(
    {
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
    }
)

#: Filterable but not sortable: each lives in its own table rather than on ``tasks``
#: (``task_collection.go:103-111``).
SUBTABLE_TASK_FIELDS = frozenset({"assignees", "labels", "reminders"})

FILTERABLE_TASK_FIELDS = SORTABLE_TASK_FIELDS | SUBTABLE_TASK_FIELDS

#: Sortable but *not* filterable: not a column at all, but a search-relevance score. Its
#: exemption is applied by the sort layer, deliberately not by the shared whitelist —
#: which is exactly why filtering on it is an error (``task_collection_sort.go:86-90``).
RELEVANCE_FIELD = "relevance"

#: fexpr sign operator to Calton comparator (``getFilterComparatorFromOp``). The
#: ``"in"``/``"not in"`` keys are unreachable from the scanner but upstream lists them,
#: so they are kept: a caller constructing an expression by hand hits the same mapping.
_COMPARATOR_FOR_OP = {
    "=": "=",
    ">": ">",
    ">=": ">=",
    "<": "<",
    "<=": "<=",
    "!=": "!=",
    "~": "like",
    "?=": "in",
    "in": "in",
    "?!=": "not in",
    "not in": "not in",
}

TASK_FILTER_COMPARATORS = frozenset(_COMPARATOR_FOR_OP.values())


@dataclass(frozen=True)
class FilterCondition:
    """One validated comparison. ``value`` is still the raw literal from the filter."""

    field: str
    comparator: str
    value: str
    join: Join


@dataclass(frozen=True)
class FilterGroup:
    """A parenthesised run of conditions, joined to its neighbour by ``join``."""

    conditions: tuple[FilterCondition | FilterGroup, ...]
    join: Join


def validate_task_field_for_sorting(field: str) -> None:
    """Raise 4016 unless ``field`` may be sorted by."""
    if field not in SORTABLE_TASK_FIELDS:
        raise CaltonError.from_name("models.ErrInvalidTaskField", task_field=field)


def validate_task_field(field: str) -> None:
    """Raise 4016 unless ``field`` may be filtered on."""
    if field not in FILTERABLE_TASK_FIELDS:
        raise CaltonError.from_name("models.ErrInvalidTaskField", task_field=field)


def comparator_for_op(op: str) -> str:
    """Map a sign operator to its comparator, raising 4017 for the unsupported ones."""
    comparator = _COMPARATOR_FOR_OP.get(op)
    if comparator is None:
        raise CaltonError.from_name("models.ErrInvalidTaskFilterComparator", comparator=op)
    return comparator


def parse_task_filter(filter_string: str) -> list[FilterCondition | FilterGroup]:
    """Preprocess, parse and validate a user-written filter.

    An empty filter is not an error — upstream returns before reaching the parser
    (``task_collection_filter.go:270-272``), so it yields no conditions at all. A filter
    of only whitespace is *not* short-circuited and does reach the parser, where it is
    rejected as an empty expression.
    """
    if filter_string == "":
        return []

    preprocessed = preprocess_filter(filter_string)

    try:
        groups = parse(preprocessed)
    except FilterExpressionError as error:
        # The message quotes the rewritten expression, not what the user typed.
        raise CaltonError.from_name(
            "models.ErrInvalidFilterExpression",
            expression=preprocessed,
            expression_error=str(error),
        ) from error

    return [_condition_from_group(group) for group in groups]


def _condition_from_group(group: ExprGroup) -> FilterCondition | FilterGroup:
    if isinstance(group.item, tuple):
        # Upstream returns here without validating anything on the group itself; the
        # checks below apply per leaf comparison, which recursion reaches.
        return FilterGroup(
            conditions=tuple(_condition_from_group(inner) for inner in group.item),
            join=group.join,
        )

    expr = group.item

    # Comparator before field, as upstream: a filter that is wrong in both ways is 4017.
    comparator = comparator_for_op(expr.op)

    field = expr.left.literal
    # `project` is an alias rather than a filterable field of its own, so it is renamed
    # before the whitelist sees it.
    if field == "project":
        field = "project_id"

    validate_task_field(field)

    return FilterCondition(
        field=field, comparator=comparator, value=expr.right.literal, join=group.join
    )
