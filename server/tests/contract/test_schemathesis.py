"""T06 — schemathesis, the fifth line of defence.

The contract diff checks that the shapes agree; this fuzzes the implemented
endpoints against Calton's own schema looking for the failures a hand-written
test will not think of. The bar for Phase 1 is narrow and firm: **no 5xx**. A
malformed filter, a negative page or a bogus timezone must come back as a 4xx
carrying a v1 error body, never as an unhandled exception.

The fuzz run covers whatever Calton currently implements, so it is empty until
T07 starts registering routers and grows by itself after that. Because "empty and
passing" and "broken and passing" look identical from the outside,
``test_the_harness_catches_a_500`` fuzzes a deliberately broken app on every run
to prove the wiring still works.
"""

from typing import Any

import pytest
import schemathesis
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from hypothesis import HealthCheck, settings

from calton.core.errors import register_exception_handlers

# ⚠️ ``filter_too_much`` is suppressed, and the reason is narrow enough to state exactly.
#
# It fires when hypothesis discards many draws before reaching ``max_examples``. For these
# operations that is not a sign of an over-constrained schema — it is the opposite: several
# have a **tiny input space** (``PUT /tasks/{task}/assignees`` takes one optional integer),
# so hypothesis exhausts the distinct values and starts discarding repeats. The check is
# asking "is this input space big enough to draw 20 different examples", which is not the
# property under test. The property is "no 5xx", and it is still checked on every example
# that *is* drawn.
#
# It only started firing when T29 declared the path parameters that 21 operations were
# missing: until then schemathesis refused those operations outright with
# ``InvalidSchema: Path parameter 'x' is not defined`` and never generated for them at all.
# So this is the cost of the gate covering them, not a regression — but left unsuppressed
# it fails a random one of them about one run in six, which is worse than useless in a gate.
#
# What is given up: if someone later writes a schema that genuinely cannot be satisfied,
# this check would have said so and now will not. ``test_the_harness_catches_a_500`` still
# proves the fuzz wiring runs, and an unsatisfiable schema would show up there as an
# operation that never exercises its endpoint.
FUZZ_SETTINGS = settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.filter_too_much],
)


def calton_app() -> FastAPI:
    try:
        from calton.main import create_app
    except ImportError:
        return FastAPI()

    # An in-memory database with the schema created, not the default on-disk one.
    # The default engine points at a file that carries no tables, so every
    # database-backed endpoint answered 500 "no such table" — a real 5xx, but one
    # that says nothing about the endpoint under fuzz. It only became visible once
    # the auth line landed and put a DB-touching route (POST /login) in the fuzz
    # set, and it surfaced intermittently because whether a generated example
    # reaches the query at all depends on the hypothesis seed.
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from calton.db.base import Base

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return create_app(engine=engine)


app = calton_app()
# From the OpenAPI document, not app.routes: included routers are wrapped and
# carry no .path attribute, so scanning app.routes reports zero forever.
has_routes = any(path.startswith("/api/v1") for path in app.openapi()["paths"])

if has_routes:
    schema = schemathesis.openapi.from_asgi("/openapi.json", app)

    @schema.parametrize()
    @FUZZ_SETTINGS
    def test_no_endpoint_returns_5xx(case: Any) -> None:
        response = case.call()
        assert response.status_code < 500, (
            f"{case.method} {case.path} returned {response.status_code}: {response.text[:400]}"
        )
else:

    @pytest.mark.skip(reason="no /api/v1 routes registered yet; schemathesis has nothing to fuzz")
    def test_no_endpoint_returns_5xx() -> None:
        pass


# The self-test goes through @schema.parametrize() — the same decorator the real
# fuzz run uses — rather than driving a Case by hand, so it exercises the whole
# chain: schema load, strategy generation, pytest collection and ASGI transport.
# xfail(strict=True) inverts it: this fails the suite if the broken app somehow
# *passes*, which is exactly the "harness silently does nothing" failure mode
# that an empty run would otherwise hide.
broken_app = FastAPI()
register_exception_handlers(broken_app)


@broken_app.get("/api/v1/boom")
def _boom(n: int = 0) -> JSONResponse:
    return JSONResponse(status_code=500, content={"message": "kaboom"})


broken_schema = schemathesis.openapi.from_asgi("/openapi.json", broken_app)


@broken_schema.parametrize()
@settings(max_examples=1, deadline=None)
@pytest.mark.xfail(strict=True, reason="proves the no-5xx assertion actually fires")
def test_the_harness_catches_a_500(case: Any) -> None:
    response = case.call()
    assert response.status_code < 500
