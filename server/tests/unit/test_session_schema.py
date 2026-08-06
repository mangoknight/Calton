"""``sessions`` must match the table the Go binary builds.

The main schema fixture (``go_schema.json``) covers exactly the 24 Phase 1
tables, and ``sessions`` is not one of them — see ``calton.models.session`` for
why Calton has it regardless. Rather than widen that shared fixture, this checks
against ``go_sessions_schema.json``, written by
``scripts/dump_go_sessions_schema.py`` from the same reference database.

Parity matters here for the same reason it does elsewhere: the harness seeds both
servers from one snapshot, so a column Calton spells differently makes a logged-in
session unreadable on the other side.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, inspect

import calton.models  # noqa: F401  -- registers every model on Base.metadata
from calton.db.base import Base

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "go_sessions_schema.json"
TABLE = "sessions"


@pytest.fixture(scope="module")
def go() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(FIXTURE.read_text())["tables"][TABLE]
    return loaded


@pytest.fixture(scope="module")
def engine() -> Engine:
    built = create_engine("sqlite+pysqlite://")
    Base.metadata.create_all(built)
    return built


def _columns(engine: Engine) -> list[dict[str, Any]]:
    return [
        {"name": column["name"], "notnull": not column["nullable"]}
        for column in inspect(engine).get_columns(TABLE)
    ]


def test_column_names_and_order_match_go(engine: Engine, go: dict[str, Any]) -> None:
    assert [column["name"] for column in _columns(engine)] == [
        column["name"] for column in go["columns"]
    ]


def test_nullability_matches_go(engine: Engine, go: dict[str, Any]) -> None:
    assert [column["notnull"] for column in _columns(engine)] == [
        column["notnull"] for column in go["columns"]
    ]


def test_index_names_and_columns_match_go(engine: Engine, go: dict[str, Any]) -> None:
    produced = sorted(
        (
            {
                "name": index["name"],
                "unique": bool(index["unique"]),
                "columns": list(index["column_names"]),
            }
            for index in inspect(engine).get_indexes(TABLE)
        ),
        key=lambda index: index["name"] or "",
    )

    assert produced == go["indexes"]


def test_the_primary_key_is_the_text_session_id(engine: Engine, go: dict[str, Any]) -> None:
    """A UUID string, not an autoincrementing integer.

    The value goes into the JWT's ``sid`` claim; making it an integer would issue
    tokens the Go side cannot match to a row.
    """
    assert inspect(engine).get_pk_constraint(TABLE)["constrained_columns"] == ["id"]
    assert next(column for column in go["columns"] if column["name"] == "id")["type"] == "TEXT"


def test_token_hash_is_unique(engine: Engine, go: dict[str, Any]) -> None:
    """Two sessions sharing a hash would make redemption ambiguous."""
    unique_indexes = {
        index["name"] for index in inspect(engine).get_indexes(TABLE) if index["unique"]
    }

    reference = {index["name"] for index in go["indexes"] if index["unique"]}

    assert "UQE_sessions_token_hash" in unique_indexes
    assert "UQE_sessions_token_hash" in reference


def test_the_table_is_not_in_the_phase_one_parity_list(engine: Engine) -> None:
    """Guards the arrangement, not the schema.

    ``sessions`` is deliberately outside ``test_schema_parity.IMPLEMENTED_TABLES``
    so the shared ``go_schema.json`` fixture — which other lines diff against —
    does not have to be regenerated. If someone adds it there, the fixture must be
    regenerated in the same change or that suite fails with a confusing KeyError.
    """
    from tests.unit.test_schema_parity import IMPLEMENTED_TABLES

    assert TABLE not in IMPLEMENTED_TABLES
