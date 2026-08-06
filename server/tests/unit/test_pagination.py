"""T05 — pagination parameters and response headers.

Mirrors pkg/web/handler/read_all.go. The header names, the header *values* and
the argument-validation order are all part of the v1 contract:

* eargollo's MCP client loops on ``x-pagination-total-pages``. Omit the header and
  every list tool silently degrades to page one, with no error anywhere.
* Browsers cannot read either header without ``Access-Control-Expose-Headers``,
  which is how the frontend ends up with NaN page counts.
"""

from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from calton.core.errors import register_exception_handlers
from calton.core.pagination import (
    MAX_ITEMS_PER_PAGE,
    PAGINATION_EXPOSE_HEADERS,
    Paginator,
    paginated_response,
)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/items")
    def items(paginator: Annotated[Paginator, Depends()], total: int = 0) -> JSONResponse:
        page = [{"id": n} for n in paginator.slice(range(total))]
        return paginator.response(page, total_items=total)

    @app.get("/echo-params")
    def echo_params(paginator: Annotated[Paginator, Depends()]) -> dict[str, Any]:
        return {
            "page": paginator.page,
            "per_page": paginator.per_page,
            "offset": paginator.offset,
            "limit": paginator.limit,
            "unlimited": paginator.unlimited,
        }

    return TestClient(app, raise_server_exceptions=False)


# --- parameter defaults and validation (read_all.go:55-90) -------------------


def test_defaults_are_page_one_and_fifty_per_page(client: TestClient) -> None:
    body = client.get("/echo-params").json()
    assert body == {"page": 1, "per_page": 50, "offset": 0, "limit": 50, "unlimited": False}


def test_per_page_zero_falls_back_to_the_default(client: TestClient) -> None:
    """read_all.go treats an explicit 0 exactly like a missing parameter."""
    assert client.get("/echo-params?per_page=0").json()["per_page"] == 50


def test_per_page_above_the_maximum_is_truncated_silently(client: TestClient) -> None:
    resp = client.get("/echo-params?per_page=5000")
    assert resp.status_code == 200
    assert resp.json()["per_page"] == MAX_ITEMS_PER_PAGE == 50


def test_negative_per_page_is_rejected(client: TestClient) -> None:
    resp = client.get("/echo-params?per_page=-1")
    assert resp.status_code == 400
    assert resp.json() == {"message": "Per page amount cannot be negative."}


def test_non_numeric_per_page_is_rejected(client: TestClient) -> None:
    resp = client.get("/echo-params?per_page=lots")
    assert resp.status_code == 400
    assert resp.json() == {"message": "Bad per page amount requested."}


def test_negative_page_is_rejected(client: TestClient) -> None:
    """read_all.go:105 has a `pageNumber < 0 -> one page` branch, but the 400 at
    line 65 makes it unreachable. Copy the 400, not the dead code."""
    resp = client.get("/echo-params?page=-1")
    assert resp.status_code == 400
    assert resp.json() == {"message": "Page number cannot be negative."}


def test_page_zero_returns_everything_unpaginated(client: TestClient) -> None:
    """models.go:89-94 returns limit=0 for page<1, and callers guard their LIMIT
    with `if limit > 0` (link_sharing.go:270), so no LIMIT is emitted. page=0 is
    this API's only way to ask for an unpaginated result — asserting merely that
    it is 200 would pass for the wrong behaviour (treating it as page 1)."""
    resp = client.get("/items?total=120&per_page=50&page=0")
    assert resp.status_code == 200
    assert len(resp.json()) == 120
    assert resp.headers["x-pagination-result-count"] == "120"
    # total_pages still divides by per_page, not by the effective limit.
    assert resp.headers["x-pagination-total-pages"] == "3"


def test_page_zero_reports_no_limit_and_zero_offset(client: TestClient) -> None:
    body = client.get("/echo-params?page=0").json()
    assert body["offset"] == 0
    assert body["limit"] == 0
    assert body["unlimited"] is True


def test_page_one_is_paginated_unlike_page_zero(client: TestClient) -> None:
    """The pair that fails if page=0 is mistakenly treated as page=1."""
    assert len(client.get("/items?total=120&per_page=50&page=1").json()) == 50
    assert len(client.get("/items?total=120&per_page=50&page=0").json()) == 120


def test_non_numeric_page_is_rejected(client: TestClient) -> None:
    resp = client.get("/echo-params?page=one")
    assert resp.status_code == 400
    assert resp.json() == {"message": "Bad page requested."}


def test_page_is_validated_before_per_page(client: TestClient) -> None:
    """Both are bad; upstream checks page first, so that message wins."""
    resp = client.get("/echo-params?page=-1&per_page=-1")
    assert resp.json() == {"message": "Page number cannot be negative."}


def test_offset_follows_from_page_and_per_page(client: TestClient) -> None:
    assert client.get("/echo-params?page=3&per_page=10").json()["offset"] == 20


# --- strict integer parsing, matching strconv.Atoi ---------------------------


@pytest.mark.parametrize(
    "value",
    [
        "1_0",  # Python accepts underscores; Go does not
        " 1",  # leading whitespace
        "1 ",  # trailing whitespace
        "\uff11",  # full-width digit
        "\u0661",  # Arabic-Indic digit
        "1.0",
        "0x10",
        "+",
        "-",
    ],
)
def test_page_values_go_would_reject_are_rejected(client: TestClient, value: str) -> None:
    """int() is much laxer than strconv.Atoi. Each of these was silently accepted
    before, where upstream returns 400."""
    resp = client.get("/echo-params", params={"page": value})
    assert resp.status_code == 400
    assert resp.json() == {"message": "Bad page requested."}


def test_an_empty_page_parameter_still_defaults_to_one(client: TestClient) -> None:
    """read_all.go:57-59 substitutes "1" before parsing, so blank is not an error."""
    assert client.get("/echo-params?page=").json()["page"] == 1


def test_explicit_plus_is_accepted(client: TestClient) -> None:
    """Atoi accepts a leading sign, so we must too. Passed via params so httpx
    percent-encodes it — a literal "+" in a query string decodes to a space."""
    assert client.get("/echo-params", params={"page": "+2"}).json()["page"] == 2


def test_a_page_beyond_int64_is_rejected_not_turned_into_a_huge_offset(client: TestClient) -> None:
    """Python ints are unbounded; an oversized page would otherwise compute an
    astronomical OFFSET and carry it into SQL."""
    resp = client.get(f"/echo-params?page={2**63}")
    assert resp.status_code == 400
    assert resp.json() == {"message": "Bad page requested."}


def test_the_largest_valid_int64_page_is_accepted(client: TestClient) -> None:
    assert client.get(f"/echo-params?page={2**63 - 1}").status_code == 200


def test_oversized_per_page_beyond_int64_is_rejected(client: TestClient) -> None:
    resp = client.get(f"/echo-params?per_page={2**63}")
    assert resp.status_code == 400
    assert resp.json() == {"message": "Bad per page amount requested."}


# --- response headers (read_all.go:113-115) ---------------------------------


def test_headers_are_present_and_expose_headers_is_set(client: TestClient) -> None:
    resp = client.get("/items?total=120&per_page=50")
    assert resp.headers["x-pagination-result-count"] == "50"
    assert resp.headers["x-pagination-total-pages"] == "3"
    assert resp.headers["access-control-expose-headers"] == PAGINATION_EXPOSE_HEADERS


def test_expose_headers_value_is_byte_exact() -> None:
    assert PAGINATION_EXPOSE_HEADERS == "x-pagination-total-pages, x-pagination-result-count"


def test_total_pages_rounds_up(client: TestClient) -> None:
    resp = client.get("/items?total=101&per_page=50")
    assert resp.headers["x-pagination-total-pages"] == "3"


def test_total_pages_is_exact_when_evenly_divisible(client: TestClient) -> None:
    resp = client.get("/items?total=100&per_page=50")
    assert resp.headers["x-pagination-total-pages"] == "2"


def test_total_pages_is_forced_to_zero_when_the_page_is_empty(client: TestClient) -> None:
    """Even with 120 total items, an empty page reports 0 pages (read_all.go:109)."""
    resp = client.get("/items?total=120&per_page=50&page=99")
    assert resp.headers["x-pagination-result-count"] == "0"
    assert resp.headers["x-pagination-total-pages"] == "0"


def test_no_results_at_all_reports_zero_pages(client: TestClient) -> None:
    resp = client.get("/items?total=0")
    assert resp.headers["x-pagination-total-pages"] == "0"
    assert resp.headers["x-pagination-result-count"] == "0"


def test_empty_result_body_is_a_list_not_null(client: TestClient) -> None:
    resp = client.get("/items?total=0")
    assert resp.json() == []
    assert resp.content == b"[]"


def test_total_pages_is_an_integer_string_not_a_float(client: TestClient) -> None:
    """Go formats it with FormatFloat(..., 'f', 0, 64) — "3", never "3.0"."""
    resp = client.get("/items?total=120&per_page=50")
    assert "." not in resp.headers["x-pagination-total-pages"]


# --- the helper on its own ---------------------------------------------------


def test_paginated_response_accepts_an_explicit_result_count() -> None:
    resp = paginated_response([{"id": 1}], total_items=10, result_count=7)
    assert resp.headers["x-pagination-result-count"] == "7"
    assert resp.headers["x-pagination-total-pages"] == "1"


def test_paginated_response_defaults_result_count_to_the_page_length() -> None:
    resp = paginated_response([{"id": 1}, {"id": 2}], total_items=10)
    assert resp.headers["x-pagination-result-count"] == "2"


def test_paginated_response_uses_per_page_for_the_page_count() -> None:
    resp = paginated_response([{"id": 1}], total_items=100, per_page=10)
    assert resp.headers["x-pagination-total-pages"] == "10"
