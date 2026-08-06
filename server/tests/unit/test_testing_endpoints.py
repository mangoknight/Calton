"""T10 — the seed/reset endpoints the parity harness drives.

These routes rewrite tables with no authentication beyond a shared token, so the
tests that matter most are the ones proving they are *absent* unless explicitly
enabled.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from calton.config import Settings
from calton.main import create_app

TOKEN = "parity-testing-token"


def settings_with(token: str) -> Settings:
    settings = Settings()
    settings.service.testingtoken = token
    settings.database.path = ":memory:"
    return settings


@pytest.fixture
def client() -> TestClient:
    app = create_app(settings_with(TOKEN))
    from calton.db.base import Base

    Base.metadata.create_all(app.state.engine)
    return TestClient(app, raise_server_exceptions=False)


def auth(token: str = TOKEN) -> dict[str, str]:
    # Plain token, no "Bearer " prefix — testing.go compares the raw header.
    return {"Authorization": token}


# --- the gate ----------------------------------------------------------------


def test_the_routes_do_not_exist_without_a_configured_token() -> None:
    """The single most important assertion in this file.

    An accidental deployment with these routes mounted is a total compromise:
    anyone who guesses the token can rewrite every table. Upstream gates them the
    same way (routes.go:523-527).
    """
    app = create_app(settings_with(""))
    paths = app.openapi()["paths"]
    # Matched on the /test/ *prefix*, not on the substring "/test": the auth line
    # serves a legitimate GET /api/v1/token/test, which a substring match flags
    # as a leaked testing route.
    assert not [p for p in paths if p.startswith("/api/v1/test/")]

    unguarded = TestClient(app, raise_server_exceptions=False)
    assert unguarded.delete("/api/v1/test/all").status_code == 404
    assert unguarded.patch("/api/v1/test/users", json=[]).status_code == 404


def test_a_wrong_token_is_refused(client: TestClient) -> None:
    assert client.delete("/api/v1/test/all", headers=auth("nope")).status_code == 403
    assert client.patch("/api/v1/test/users", json=[], headers=auth("nope")).status_code == 403


def test_a_missing_token_is_refused(client: TestClient) -> None:
    assert client.delete("/api/v1/test/all").status_code == 403


def test_a_bearer_prefix_is_not_accepted(client: TestClient) -> None:
    """Upstream compares the raw header value, so "Bearer <token>" must fail."""
    assert (
        client.delete("/api/v1/test/all", headers={"Authorization": f"Bearer {TOKEN}"}).status_code
        == 403
    )


def test_the_forbidden_body_matches_echos(client: TestClient) -> None:
    assert client.delete("/api/v1/test/all", headers=auth("nope")).json() == {
        "message": "Forbidden"
    }


# --- behaviour ---------------------------------------------------------------


def test_truncate_all_returns_ok(client: TestClient) -> None:
    resp = client.delete("/api/v1/test/all", headers=auth())
    assert resp.status_code == 200
    assert resp.json() == {"message": "ok"}


def test_patch_replaces_a_table_and_returns_201(client: TestClient) -> None:
    rows: list[dict[str, Any]] = [
        {"id": 1, "username": "user1", "password": "x", "email": "u1@example.com"},
        {"id": 2, "username": "user2", "password": "x", "email": "u2@example.com"},
    ]
    resp = client.patch("/api/v1/test/users?truncate=true", json=rows, headers=auth())
    assert resp.status_code == 201
    assert resp.json() == rows


def test_a_missing_truncate_parameter_means_true(client: TestClient) -> None:
    """testing.go: `truncate == "true" || truncate == ""`. Absent means truncate,
    which is the opposite of what a reader expects from a flag."""
    first = [{"id": 1, "username": "first", "password": "x", "email": "a@example.com"}]
    second = [{"id": 2, "username": "second", "password": "x", "email": "b@example.com"}]

    client.patch("/api/v1/test/users", json=first, headers=auth())
    client.patch("/api/v1/test/users", json=second, headers=auth())

    remaining = client.patch("/api/v1/test/users?truncate=false", json=[], headers=auth())
    assert remaining.status_code == 201


def test_truncate_false_appends_instead_of_replacing(client: TestClient) -> None:
    client.patch(
        "/api/v1/test/users",
        json=[{"id": 1, "username": "a", "password": "x", "email": "a@example.com"}],
        headers=auth(),
    )
    resp = client.patch(
        "/api/v1/test/users?truncate=false",
        json=[{"id": 2, "username": "b", "password": "x", "email": "b@example.com"}],
        headers=auth(),
    )
    assert resp.status_code == 201


def test_unknown_columns_in_a_row_are_dropped(client: TestClient) -> None:
    """Upstream fixtures carry columns Phase 1 has not modelled yet; the loader
    must not die on them."""
    resp = client.patch(
        "/api/v1/test/users",
        json=[
            {"id": 1, "username": "u", "password": "x", "email": "e@example.com", "not_a_column": 1}
        ],
        headers=auth(),
    )
    assert resp.status_code == 201


def test_an_unknown_table_is_reported(client: TestClient) -> None:
    resp = client.patch("/api/v1/test/no_such_table", json=[], headers=auth())
    assert resp.status_code == 500
    assert resp.json()["error"] is True


def test_an_empty_row_list_just_truncates(client: TestClient) -> None:
    assert client.patch("/api/v1/test/users", json=[], headers=auth()).status_code == 201


def test_a_timestamp_with_microseconds_loads(client: TestClient) -> None:
    """☠ One unparseable timestamp 500s the **whole table**, not just its row.

    ``harness/seed_load.py`` resolves overlay tokens like ``{now+30m}`` with
    ``datetime.isoformat()``, which emits microseconds. That shape matched none of
    ``_STAMP_FORMATS``, so the string reached ``CaltonDateTime.process_bind_param`` and
    died on ``'str' object has no attribute 'tzinfo'`` — and because the loader sends a
    table per request, Calton ended up with **zero** tasks while Go had all 285. Every
    task-dependent parity case then compared an empty result against a full one, which
    reads exactly like a broken query rather than a failed reset.

    The pair matters: the second row is the format that already worked, so this stays a
    test about the new format rather than about timestamps in general.
    """
    resp = client.patch(
        "/api/v1/test/tasks",
        json=[
            {
                "id": 1,
                "project_id": 1,
                "index": 1,
                "title": "microseconds",
                "created_by_id": 1,
                "done": False,
                "due_date": "2026-08-04T04:06:41.827094Z",
            },
            {
                "id": 2,
                "project_id": 1,
                "index": 2,
                "title": "whole seconds",
                "created_by_id": 1,
                "done": False,
                "due_date": "2026-08-04T04:06:41Z",
            },
        ],
        headers=auth(),
    )
    assert resp.status_code == 201, resp.text


def test_a_loaded_timestamp_is_stored_with_the_utc_offset(client: TestClient) -> None:
    """⚠️ The stored **spelling** is the contract here, not the instant.

    These columns are TEXT and filters compare them as TEXT, so a row written
    ``2026-05-01 00:00:00`` and a row written ``2026-05-01 00:00:00+00:00`` are two
    different values to ``reminders = '2026-05-01'``. Upstream's loader normalises every
    fixture spelling to the offset form; this one used to strip it, so after a reset the
    two servers held different bytes for the same instant while both reported a
    successful load, and equality filters matched on the Go side alone.

    The two input spellings below are the two the fixtures actually contain, and they
    have to come out identical — that is the property, and asserting on a parsed datetime
    instead of the raw text would be satisfied by the behaviour this replaced.
    """
    from sqlalchemy import text as sql_text

    rows: list[dict[str, Any]] = [
        {"id": 1, "task_id": 1, "reminder": "2026-05-01T00:00:00Z"},
        {"id": 2, "task_id": 1, "reminder": "2026-08-01 00:00:00"},
    ]
    resp = client.patch("/api/v1/test/task_reminders?truncate=true", json=rows, headers=auth())
    assert resp.status_code == 201, resp.text

    engine = client.app.state.engine  # type: ignore[attr-defined]
    with engine.connect() as connection:
        stored = [
            row[0]
            for row in connection.execute(
                sql_text("select reminder from task_reminders order by id")
            )
        ]

    assert stored == ["2026-05-01 00:00:00+00:00", "2026-08-01 00:00:00+00:00"]


def test_an_unparseable_timestamp_still_fails_loudly(client: TestClient) -> None:
    """The widened format list must not turn into "accept anything".

    A fixture value that quietly becomes a *different* datetime is the one corruption the
    harness cannot detect, because both servers would then be seeded differently and every
    diff after that is noise. Unknown shapes are passed through so the database refuses
    them.
    """
    resp = client.patch(
        "/api/v1/test/tasks",
        json=[
            {
                "id": 1,
                "project_id": 1,
                "index": 1,
                "title": "nonsense",
                "created_by_id": 1,
                "done": False,
                "due_date": "not-a-timestamp",
            }
        ],
        headers=auth(),
    )
    assert resp.status_code == 500
