"""Seed/reset endpoints, replicating pkg/routes/api/v1/testing.go.

The parity harness resets both servers between mutating cases. Doing that by
restarting containers would make 293 cases unrunnable, so upstream's own testing
endpoints are the mechanism — and Calton has to answer them identically.

⚠️ SECURITY. These routes bypass authentication and rewrite tables wholesale.
They are registered **only** when ``service.testingtoken`` is non-empty, exactly
as upstream gates them (routes.go:523-527). Never set CALTON_SERVICE_TESTINGTOKEN
in a deployment: it is a total-compromise switch, not a debug flag.

The auth check is a plain header equality against the configured token — no
``Bearer`` prefix, because that is what upstream compares
(testing.go: ``c.Request().Header.Get("Authorization") != config.ServiceTestingtoken``).
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Column, Date, DateTime, MetaData, String, Table, delete, insert, text
from sqlalchemy.types import TypeDecorator, TypeEngine

import calton.models  # noqa: F401  — registers every table on Base.metadata
from calton.db.base import Base

#: Order matters: rows reference each other by id, and although the upstream
#: schema declares no FOREIGN KEY constraints, loading parents first keeps the
#: data coherent for anything that reads it.
TABLE_LOAD_ORDER = (
    "users",
    "user_tokens",
    "teams",
    "team_members",
    "projects",
    "project_views",
    "buckets",
    "tasks",
    "task_positions",
    "task_buckets",
    "labels",
    "label_tasks",
    "task_assignees",
    "task_relations",
    "task_reminders",
    "task_comments",
    "files",
    "task_attachments",
    "team_projects",
    "users_projects",
    "saved_filters",
    "favorites",
    "subscriptions",
    "api_tokens",
)


def _forbidden() -> JSONResponse:
    # echo.ErrForbidden carries no domain code, so the central handler renders it
    # through the bare-string path: {"message": "Forbidden"}.
    return JSONResponse(status_code=403, content={"message": "Forbidden"})


#: The formats upstream's fixtures use. `PATCH /test/:table` receives whatever the
#: YAML held, and Go parses these strings itself; SQLAlchemy will not, so without
#: this the whole parity reset fails with "'str' object has no attribute 'tzinfo'"
#: and every case downstream reads as an implementation bug.
#:
#: ⚠️ The two **fractional-second** entries are not decoration. Overlay rows written as
#: `{now+30m}` / `{tzday:Asia/Shanghai-60m}` are resolved by `harness/seed_load.py` with
#: `datetime.isoformat()`, which emits microseconds — `2026-08-04T04:06:41.827094Z`. That
#: matched none of the original four formats, so `_as_datetime` returned None, the string
#: was passed through, and `CaltonDateTime.process_bind_param` died on it. Because the
#: loader sends a table in one request, **one such row 500s the entire `tasks` table**:
#: Calton ends up with zero tasks while Go has all of them, and every task-dependent case
#: then reports an empty result that looks exactly like a broken query. Measured against
#: the reference server while checking T17's sibling group `_view_shape.yaml`, which is
#: unrunnable without this.
_STAMP_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
)


def _as_datetime(value: str) -> datetime | None:
    for fmt in _STAMP_FORMATS:
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _coerce(target: Table, row: dict[str, Any]) -> dict[str, Any]:
    """Drop unknown columns; parse fixture timestamps and re-render them **Go's way**.

    ⚠️ These columns are TEXT in SQLite and filters compare them as TEXT, so the
    *spelling* a loader writes is part of the data, not an encoding detail.

    The fixtures are not written consistently — ``task_reminders`` carries
    ``2026-06-15T00:00:00Z`` while ``tasks`` carries ``2026-03-01 12:00:00`` — and
    upstream's loader **normalises both** to ``2026-06-15 00:00:00+00:00``. This one
    parsed them and then let ``CaltonDateTime`` render them *without* the offset, so
    after a reset the two databases held different bytes for the same instant while
    both claimed to be "reset from the same fixtures", and equality filters matched on
    the Go side only.

    Measured before and after a reset, on both servers, in
    ``harness/probe_coder_e_reset_fmt.py``. The visible consequence:
    ``filter.exists.equality_then_range_not_merged`` passes when run alone and fails in
    a full run, because the suite calls ``reset()`` in between and nothing else differs.

    ⚠️ Do not "simplify" this to storing the fixture string unchanged. That was the
    first attempt and it is wrong for the same reason the old code was: it makes the
    stored spelling depend on which fixture file a row came from, and only one of the
    two spellings matches what upstream writes.
    """
    coerced: dict[str, Any] = {}
    for name, value in row.items():
        column = target.columns.get(name)
        if column is None:
            continue
        kind = _temporal_kind(column.type)
        if isinstance(value, str) and kind is not None:
            parsed = _as_datetime(value)
            if parsed is None:
                # ⚠️ Explicit, because it used to be accidental. An unparseable string
                # reached `CaltonDateTime.process_bind_param`, which raised on it, and
                # the loud failure `test_an_unparseable_timestamp_still_fails_loudly`
                # pins was a side effect of that type. The insert now goes through a
                # text-typed mirror, which would store "not-a-timestamp" quite happily —
                # a fixture typo would become data, on one server only, and every case
                # reading that column would report a difference that is not one.
                raise ValueError(
                    f"{target.name}.{name}: {value!r} matches none of the fixture "
                    f"timestamp formats, so the value that would be stored is not the "
                    f"value the fixture means"
                )
            # Text, not a datetime: the insert goes through `_text_mirror`, so this
            # is the exact byte sequence that lands in the column.
            value = parsed.date() if kind is Date else parsed.isoformat(sep=" ")
        coerced[name] = value
    return coerced


#: Insert targets whose temporal columns are typed as plain text, built once per table.
_TEXT_MIRRORS: dict[str, Table] = {}


def _text_mirror(target: Table) -> Table:
    """``target`` with every ``DateTime`` column retyped as ``String``.

    The fixture rows now carry timestamps as strings (see :func:`_coerce`), and a
    string bound against a ``CaltonDateTime`` column reaches that type's
    ``process_bind_param``, which calls ``as_utc()`` on it and raises. Retyping the
    column for the *insert statement only* is what lets the text through untouched;
    the stored column is TEXT either way, so nothing about the database changes.

    ⚠️ Defaults are copied across. Without them a fixture row that omits ``created``
    would get NULL here and ``utcnow()`` through the real table — the divergence would
    be in a column nothing asserts on, and it would be seeded differently on the two
    servers, which is the one class of fault this harness cannot see.
    """
    cached = _TEXT_MIRRORS.get(target.name)
    if cached is not None:
        return cached

    columns = []
    for column in target.columns:
        if _temporal_kind(column.type) is DateTime:
            columns.append(
                Column(
                    column.name,
                    String(),
                    # ⚠️ The default has to be retyped along with the column. `created`
                    # defaults to `utcnow()`, which returns a *datetime*, and a datetime
                    # bound into a String column raises — so a fixture row that simply
                    # omits `created` (upstream's `users` rows do) 500s the whole table.
                    default=_text_default(column),
                    server_default=column.server_default,
                    primary_key=column.primary_key,
                    autoincrement=column.autoincrement,
                )
            )
        else:
            columns.append(
                Column(
                    column.name,
                    column.type,
                    default=column.default,
                    server_default=column.server_default,
                    primary_key=column.primary_key,
                    autoincrement=column.autoincrement,
                )
            )

    mirror = Table(target.name, MetaData(), *columns)
    _TEXT_MIRRORS[target.name] = mirror
    return mirror


def _text_default(column: Column[Any]) -> Any:
    """``column``'s Python-side default, rendered as the same text the fixtures use —
    but **only when the column would otherwise refuse a NULL**.

    ⚠️ The parity harness reloads test fixtures via ``x.Table(t).Insert(map)`` on the
    Go side (pkg/db/dump.go:139) — a raw map insert, which **bypasses xorm's
    `created`/`updated` hooks**, so a fixture row that *omits* ``created`` lands as
    SQL NULL on the Go side and serialises as Go's zero time on read. If this mirror
    copied the Python ``default=utcnow`` over unchanged, the same row would land as a
    real timestamp on the Calton side, and the team list / read-one / update cases
    would diff on every team with a fixture that omits ``created`` --
    the HANDOFF §3 "team timestamps" red is precisely that.

    Carrying the default only when the column is **NOT NULL** keeps the cases where
    inserting NULL would 500, preserves Go's NULL-on-omit behaviour for the
    nullable team timestamps, and matches both halves of the team contract:
    ``created``/``updated`` zero on read (Go) and a real timestamp on actual
    creates (the model's own ``default=utcnow``, which fires on real-ORM inserts,
    not on these testing resets).
    """
    default = column.default
    if default is None:
        return None
    if column.nullable:
        # Nullable — Go's reload leaves it NULL when the fixture omits it, and so
        # do we.
        return None

    argument = getattr(default, "arg", None)
    if argument is None:
        return None

    if callable(argument):
        return lambda context: _stamp(argument(context))
    return _stamp(argument)


def _stamp(value: Any) -> Any:
    """A datetime as ``2026-05-01 00:00:00+00:00``; anything else untouched."""
    if isinstance(value, datetime):
        return (
            value.astimezone(UTC).isoformat(sep=" ") if value.tzinfo else value.isoformat(sep=" ")
        )
    return value


def _temporal_kind(type_: TypeEngine[Any]) -> type[Date] | type[DateTime] | None:
    """`Date`, `DateTime`, or None — looking *through* any TypeDecorator.

    Every timestamp column in this schema is `CaltonDateTime`, a TypeDecorator
    over DateTime, so a plain `isinstance(column.type, DateTime | Date)` is False
    for all of them and no fixture string was ever parsed. The decorator's own
    `process_bind_param` then called `as_utc()` on a `str` and the whole request
    died with `'str' object has no attribute 'tzinfo'` — a 500 on
    `PATCH /test/<table>` that failed the per-case reset and made 154 parity cases
    report a partial database instead of a result.
    """
    while isinstance(type_, TypeDecorator):
        type_ = type_.impl_instance
    # Date must be tested first: DateTime is not its subclass, but checking in the
    # other order would still be a trap worth ruling out by construction.
    if isinstance(type_, Date):
        return Date
    if isinstance(type_, DateTime):
        return DateTime
    return None


def _by_column_set(target: Table, rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Consecutive runs of rows that name the same columns, in input order.

    `executemany` compiles one INSERT from the *first* mapping and then demands
    every later mapping carry exactly those keys — upstream's fixtures are
    heterogeneous (`tasks` row 1 has a description, row 2 does not), so a single
    call dies with "A value is required for bind parameter 'description'".

    Grouping rather than filling the gaps is the point: an absent column must
    take its schema default, which is what Go's row-at-a-time loader does. Filling
    with None would write NULL over columns whose default is a zero value, and the
    two servers would then be seeded differently — the one divergence this harness
    is structurally unable to notice, since it compares them against each other.
    """
    groups: list[list[dict[str, Any]]] = []
    signature: tuple[str, ...] | None = None
    for row in rows:
        coerced = _coerce(target, row)
        keys = tuple(coerced)
        if keys != signature:
            groups.append([])
            signature = keys
        groups[-1].append(coerced)
    return groups


def _authorised(request: Request, token: str) -> bool:
    supplied = request.headers.get("Authorization", "")
    # Constant-time compare: the value is a shared secret, and a timing oracle on
    # a route that can rewrite every table is not a trade worth making.
    return bool(token) and secrets.compare_digest(supplied, token)


def build_router(testing_token: str) -> APIRouter:
    """Routes for resetting the database. Caller must only mount when enabled."""
    router = APIRouter()

    @router.delete("/test/all")
    def truncate_all(request: Request) -> JSONResponse:
        if not _authorised(request, testing_token):
            return _forbidden()

        engine = request.app.state.engine
        with engine.begin() as connection:
            for table in reversed(TABLE_LOAD_ORDER):
                if table in Base.metadata.tables:
                    connection.execute(delete(Base.metadata.tables[table]))
            # DELETE does not reset AUTOINCREMENT watermarks, and upstream's
            # truncate does not either. Leaving sqlite_sequence alone is what
            # keeps ids comparable across a reset instead of diverging.
        return JSONResponse(status_code=200, content={"message": "ok"})

    @router.patch("/test/{table}")
    def replace_table(request: Request, table: str, rows: list[dict[str, Any]]) -> JSONResponse:
        if not _authorised(request, testing_token):
            return _forbidden()

        if table not in Base.metadata.tables:
            return JSONResponse(
                status_code=500,
                content={"error": True, "message": f"unknown table {table}"},
            )

        # testing.go treats a missing truncate parameter as true, not false:
        #   truncate == "true" || truncate == ""
        raw = request.query_params.get("truncate", "")
        truncate = raw in ("true", "")

        target = Base.metadata.tables[table]
        with request.app.state.engine.begin() as connection:
            if truncate:
                connection.execute(delete(target))
            for group in _by_column_set(target, rows):
                # Retyped target: fixture timestamps are inserted as the exact text
                # given, the way upstream's loader does. See _text_mirror.
                connection.execute(insert(_text_mirror(target)), group)

        return JSONResponse(status_code=201, content=rows)

    @router.get("/test/_tables")
    def list_tables(request: Request) -> JSONResponse:
        """Not upstream. Lets build_seed.py check load order against the schema
        rather than hard-coding a list that silently rots."""
        if not _authorised(request, testing_token):
            return _forbidden()
        with request.app.state.engine.connect() as connection:
            names = [
                row[0]
                for row in connection.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
                )
            ]
        return JSONResponse(status_code=200, content={"tables": names})

    return router
