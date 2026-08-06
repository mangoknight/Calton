"""``?expand=`` — the opt-in parts of a task response.

The parameter repeats, and every value is validated before anything is read, so one bad
value rejects the whole request rather than being skipped.

**``expand=subtasks`` deliberately returns more rows than ``per_page``.** Pagination is
applied to *root* tasks — those whose parent is not itself in the result set — and then
every descendant of those roots is appended. Measured: five tasks with two roots and
``per_page=2`` returns all five, and the ``x-pagination-result-count`` header says 5.

That looks like a paging bug and is not one. Capping the total at ``per_page`` would drop
subtasks arbitrarily, so a client walking pages would see a parent on one page and lose
half its children with nothing to indicate it — which is exactly why the header reports
what was actually sent rather than the root count.
"""

from __future__ import annotations

from enum import StrEnum

from calton.core.errors import ValidationError

#: The exact wording upstream returns, which is the *message* of the 412 rather than the
#: usual "Invalid Data". Measured; the list order is upstream's too.
INVALID_EXPAND_MESSAGE = (
    "Expand must be one of the following values: subtasks, buckets, reactions, "
    "comments, comment_count, time_entries_count, is_unread"
)


class Expandable(StrEnum):
    """``TaskCollectionExpandable``. The strings are a wire contract."""

    SUBTASKS = "subtasks"
    BUCKETS = "buckets"
    REACTIONS = "reactions"
    COMMENTS = "comments"
    COMMENT_COUNT = "comment_count"
    TIME_ENTRIES_COUNT = "time_entries_count"
    IS_UNREAD = "is_unread"


#: Values Calton accepts but cannot populate, because the tables they read are not part of
#: Phase 1's schema (``task_unread_statuses``, ``time_entries``) or belong to endpoints not
#: yet built (subscriptions). Accepting them is the faithful behaviour rather than a
#: shortcut: upstream also answers 200 with the key absent when there is no row to report,
#: so the two agree on every request that can be made today. Rejecting them instead would
#: break clients that ask for the expansion speculatively — which the web frontend does.
UNPOPULATED: frozenset[Expandable] = frozenset(
    {Expandable.IS_UNREAD, Expandable.TIME_ENTRIES_COUNT, Expandable.REACTIONS}
)

#: How many comments ``?expand=comments`` embeds. Measured: a task with 55 comments
#: returns 50 of them while ``comment_count`` still reports 55, so the two numbers are
#: independent and truncating the count as well would lose that.
EMBEDDED_COMMENT_LIMIT = 50


def parse_expand(values: list[str]) -> list[Expandable]:
    """Validate every requested expansion, preserving order and dropping duplicates.

    An unknown value — including the empty string, which is *not* treated as "unset" —
    fails the whole request with 412/2002 and ``invalid_fields: ["expand"]``. Measured on
    both. Skipping unknown values instead would silently return a response missing the
    fields the client asked for.
    """
    seen: dict[Expandable, None] = {}
    for raw in values:
        try:
            expandable = Expandable(raw)
        except ValueError:
            raise ValidationError(["expand"], message=INVALID_EXPAND_MESSAGE) from None
        seen.setdefault(expandable, None)
    return list(seen)


__all__ = [
    "EMBEDDED_COMMENT_LIMIT",
    "INVALID_EXPAND_MESSAGE",
    "UNPOPULATED",
    "Expandable",
    "parse_expand",
]
