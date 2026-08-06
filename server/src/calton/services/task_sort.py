"""Sort parameters for task collections (``pkg/models/task_collection_sort.go``).

Three rules here are each worth a test of their own, because each has a plausible wrong
version that behaves identically on ordinary input:

**``sort_by`` and ``order_by`` pair by position, not by name.** ``sort_by=priority&
sort_by=title&order_by=desc`` means *priority desc, title asc* — the second key falls back
to ascending because there is no second ``order_by``. Zipping them or applying the last
``order_by`` to everything both give "title desc" here.

**The ``id`` tiebreaker is appended unless it is already last.** Not "unless it appears
anywhere": ``sort_by=id&sort_by=priority`` still gets ``id asc`` appended at the end. And
when the user's own last key *is* ``id``, theirs stands — so an explicit ``id desc``
survives instead of being overwritten by an appended ``id asc``. Appending
unconditionally answers 200 with the data merely in the opposite order, which surfaces as
"my descending list restarts from the top every page".

**An invalid ``order_by`` echoes the constant ``'invalid'``, not what the user sent.**
Upstream parses the direction into an enum first and formats the *enum* into the message,
so ``order_by=sideways`` reports ``'invalid'``. Any implementation that interpolates the
user's text — the obvious way to write it — produces the same status and the same code
with one word different.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from calton.core.errors import CaltonError

#: ``validateTaskFieldForSorting``. Note this doubles as the *filterable* field list
#: upstream, which is why ``relevance`` is handled separately rather than added here.
SORTABLE_FIELDS = frozenset(
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

#: Sortable but not filterable: a search-relevance score rather than a column. Accepted
#: and then silently ignored when the database cannot score the query, which is always
#: for us — SQLite has no equivalent of the upstream ParadeDB ranking.
RELEVANCE = "relevance"

ASCENDING = "asc"
DESCENDING = "desc"

#: What ``getSortOrderFromString`` returns for anything that is not asc/desc. It reaches
#: the error message verbatim, which is why the message never contains the user's text.
INVALID_ORDER = "invalid"


@dataclass(frozen=True)
class SortParam:
    sort_by: str
    order_by: str
    #: Only meaningful for ``position``, which lives in ``task_positions`` per view.
    project_view_id: int = 0

    @property
    def descending(self) -> bool:
        return self.order_by == DESCENDING


def _order_from_string(raw: str) -> str:
    if raw == ASCENDING:
        return ASCENDING
    if raw == DESCENDING:
        return DESCENDING
    return INVALID_ORDER


def _validate(param: SortParam) -> None:
    if param.order_by not in (ASCENDING, DESCENDING):
        raise CaltonError.from_name("models.ErrInvalidSortOrder", order_by=param.order_by)

    if param.sort_by == "position" and param.project_view_id == 0:
        raise CaltonError.from_name("models.ErrMustHaveProjectViewToSortByPosition")

    if param.sort_by == RELEVANCE:
        return

    if param.sort_by not in SORTABLE_FIELDS:
        raise CaltonError.from_name("models.ErrInvalidTaskField", task_field=param.sort_by)


def parse_sort(
    sort_by: Sequence[str],
    order_by: Sequence[str],
    *,
    view_id: int | None = None,
) -> list[SortParam]:
    """Pair the two query parameters up and validate each result.

    ``view_id`` is the view being read through, or None outside a view. A **negative**
    view id is a saved filter's pseudo view, whose tasks have no stored positions, so a
    ``position`` key is dropped rather than rejected — silently, as upstream does.
    """
    params: list[SortParam] = []

    for index, field in enumerate(sort_by):
        # Positional pairing: the i-th order_by belongs to the i-th sort_by, and a
        # missing one defaults to ascending rather than inheriting its neighbour.
        raw_order = order_by[index] if index < len(order_by) else ASCENDING

        if field == "position" and view_id is not None and view_id < 0:
            continue

        param = SortParam(
            sort_by=field,
            order_by=_order_from_string(raw_order),
            project_view_id=view_id if (field == "position" and view_id is not None) else 0,
        )
        _validate(param)
        params.append(param)

    return params


def with_view_position(params: list[SortParam], view_id: int) -> list[SortParam]:
    """Append the view's ``position`` key unless the caller already sorted by it.

    Reading through a view means the stored per-view order is what the board shows, so it
    is added even when the user asked for something else — their keys stay in front of it.
    """
    if any(param.sort_by == "position" for param in params):
        return params
    return [*params, SortParam(sort_by="position", order_by=ASCENDING, project_view_id=view_id)]


def with_id_tiebreaker(params: list[SortParam]) -> list[SortParam]:
    """Append ``id asc`` unless the list already *ends* with an ``id`` key.

    Without a tiebreaker, rows tied on every sort key come back in whatever order the
    database happens to produce, so pages overlap and drop rows. "Ends with" rather than
    "contains" is the upstream condition, and it is what lets an explicit ``id desc``
    stand as the user wrote it.
    """
    if params and params[-1].sort_by == "id":
        return params
    return [*params, SortParam(sort_by="id", order_by=ASCENDING)]
