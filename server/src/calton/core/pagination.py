"""Pagination parameters and response headers, mirroring pkg/web/handler/read_all.go.

Two things here are load-bearing beyond the obvious:

``x-pagination-total-pages`` is a hard dependency of the MCP clients — eargollo's
``paginatedResult()`` loops until it has seen that many pages. If the header is
missing, every list tool quietly returns only the first page and the model
believes that is all the data there is. No error surfaces anywhere.

``Access-Control-Expose-Headers`` must accompany them, or browsers refuse to hand
the values to JavaScript and the frontend computes NaN page counts.

Routes must not build list responses by hand; go through ``Paginator.response()``
so the headers cannot be forgotten.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from typing import Any, TypeVar

from fastapi import Query
from fastapi.responses import JSONResponse

from calton.core.errors import EchoStringError

T = TypeVar("T")

# config.ServiceMaxItemsPerPage, pkg/config/config.go:359. Default and ceiling
# are the same number upstream.
MAX_ITEMS_PER_PAGE = 50

PAGINATION_EXPOSE_HEADERS = "x-pagination-total-pages, x-pagination-result-count"

# Upstream messages, reproduced verbatim. These come out of echo.NewHTTPError as
# bare strings, so they render as {"message": ...} with no code.
BAD_PAGE = "Bad page requested."
NEGATIVE_PAGE = "Page number cannot be negative."
BAD_PER_PAGE = "Bad per page amount requested."
NEGATIVE_PER_PAGE = "Per page amount cannot be negative."


# Go's strconv.Atoi accepts an optional sign then ASCII digits, nothing else, and
# errors on anything exceeding int64. Python's int() is far more permissive: it
# takes underscores ("1_0"), surrounding whitespace, full-width digits (U+FF11),
# Arabic-Indic digits (U+0661) and integers of unbounded size. Every one of those
# would be silently accepted where upstream returns 400 — and an oversized page
# also produces an astronomical OFFSET that goes on to hit SQL.
_GO_ATOI = re.compile(r"^[+-]?[0-9]+$")

INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1


def _parse_int(raw: str, error_message: str) -> int:
    """Parse exactly what Go's strconv.Atoi parses, and nothing more."""
    if not _GO_ATOI.match(raw):
        raise EchoStringError(400, error_message)
    value = int(raw)
    if not INT64_MIN <= value <= INT64_MAX:
        raise EchoStringError(400, error_message)
    return value


class Paginator:
    """FastAPI dependency parsing ``page`` and ``per_page``.

    The parameters are taken as strings and parsed by hand so that a bad value
    produces upstream's exact message instead of FastAPI's validation body, and so
    that the checks run in upstream's order (page first, then per_page).
    """

    def __init__(
        self,
        page: str = Query(default="1"),
        per_page: str = Query(default=""),
    ) -> None:
        self.page = _parse_int(page or "1", BAD_PAGE)
        if self.page < 0:
            raise EchoStringError(400, NEGATIVE_PAGE)

        # A missing per_page parses as 0, which read_all.go then replaces with the
        # default — so an explicit per_page=0 behaves identically to omitting it.
        per_page_number = _parse_int(per_page, BAD_PER_PAGE) if per_page else 0
        if per_page_number == 0:
            per_page_number = MAX_ITEMS_PER_PAGE
        if per_page_number < 1:
            raise EchoStringError(400, NEGATIVE_PER_PAGE)
        self.per_page = min(per_page_number, MAX_ITEMS_PER_PAGE)

    @property
    def unlimited(self) -> bool:
        """Whether this request asked for everything.

        ``getLimitFromPageIndex`` (pkg/models/models.go:89-94) returns ``limit=0``
        for ``page < 1``, and every caller guards its LIMIT clause with
        ``if limit > 0`` (e.g. link_sharing.go:270) — so limit 0 means no LIMIT is
        emitted at all. ``page=0`` is therefore the one way to ask this API for an
        unpaginated result. Negative pages never reach here; they 400 first.
        """
        return self.page < 1

    @property
    def offset(self) -> int:
        """Rows to skip. Zero when unlimited, as models.go:92 returns start=0."""
        if self.unlimited:
            return 0
        return (self.page - 1) * self.per_page

    @property
    def limit(self) -> int:
        """Rows to take, or 0 meaning "no limit" — the same sentinel Go uses."""
        return 0 if self.unlimited else self.per_page

    def slice(self, items: Iterable[T]) -> list[T]:
        """Apply the window in memory. Real endpoints push offset/limit into SQL,
        and must guard with ``if limit > 0`` exactly as the Go callers do."""
        materialised = list(items)
        if self.unlimited:
            return materialised
        return materialised[self.offset : self.offset + self.per_page]

    def response(
        self,
        items: Sequence[Any],
        total_items: int,
        result_count: int | None = None,
    ) -> JSONResponse:
        return paginated_response(
            items,
            total_items=total_items,
            per_page=self.per_page,
            result_count=result_count,
        )


def total_pages(total_items: int, per_page: int, result_count: int) -> int:
    """Page count as read_all.go:100-111 computes it.

    Rounds up, but an empty page reports 0 pages regardless of the total — that
    override is what clients see when they walk past the end.
    """
    if result_count == 0:
        return 0
    return math.ceil(total_items / per_page)


def paginated_response(
    items: Sequence[Any],
    total_items: int,
    per_page: int = MAX_ITEMS_PER_PAGE,
    result_count: int | None = None,
) -> JSONResponse:
    """A list response carrying both pagination headers and the CORS exposure.

    ``result_count`` is this page's length; ``total_items`` is the unpaginated
    total. Services return both because the page count needs the latter and the
    header needs the former.
    """
    count = len(items) if result_count is None else result_count
    return JSONResponse(
        # An empty page is [], never null (read_all.go:117-122).
        content=list(items),
        headers={
            "x-pagination-total-pages": str(total_pages(total_items, per_page, count)),
            "x-pagination-result-count": str(count),
            "Access-Control-Expose-Headers": PAGINATION_EXPOSE_HEADERS,
        },
    )
