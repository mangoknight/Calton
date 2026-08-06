"""Calton's generated schema must match the one the Go binary builds, table by table.

The reference is ``tests/fixtures/go_schema.json``, produced by
``scripts/dump_go_schema.py`` from a database created with ``calton migrate``. CI cannot
build Go, so the fixture is committed; the script's docstring says how to refresh it.

This is the prerequisite for AC-1: the parity harness (T10) seeds both servers from one
snapshot, which only works if both understand the same schema.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, text

import calton.models  # noqa: F401  -- registers every model on Base.metadata
from calton.config import get_settings
from calton.db.base import Base

# The Go schema source used to live in this repo and ``scripts/dump_go_schema.py``
# hashed it so the fixture could be checked for staleness. That source has moved
# out of this repository (the Go binary is no longer co-located), so the staleness
# guard and its two meta-tests have been removed. The fixture
# ``tests/fixtures/go_schema.json`` remains as a static reference: the
# column-by-column comparison below still catches schema drift in Calton's
# SQLAlchemy models. When the fixture itself needs updating, regenerate it from
# a running Go reference and replace the file.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "go_schema.json"


#: All 24 Phase 1 tables (design §1.3): T03 built the first twelve, T09 the rest.
IMPLEMENTED_TABLES = [
    "users",
    "user_tokens",
    "projects",
    "project_views",
    "tasks",
    "task_positions",
    "buckets",
    "task_buckets",
    "labels",
    "label_tasks",
    "task_assignees",
    "files",
    "task_relations",
    "task_reminders",
    "task_comments",
    "task_attachments",
    "teams",
    "team_members",
    "team_projects",
    "users_projects",
    "saved_filters",
    "favorites",
    "subscriptions",
    "api_tokens",
]


@pytest.fixture(scope="module")
def fixture_file() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(FIXTURE.read_text())
    return loaded


@pytest.fixture(scope="module")
def go_schema(fixture_file: dict[str, Any]) -> dict[str, Any]:
    tables: dict[str, Any] = fixture_file["tables"]
    return tables


@pytest.fixture(scope="module")
def calton_engine(tmp_path_factory: pytest.TempPathFactory) -> Engine:
    """A database built by running the migrations, not by ``create_all``.

    The migration is what ships, so it is what gets compared. Building the schema
    straight from the metadata would hide a baseline that has drifted from the models.
    """
    database = tmp_path_factory.mktemp("schema") / "calton.db"

    config = Config(str(Path(__file__).resolve().parent.parent.parent / "alembic.ini"))
    with mock.patch.dict(os.environ, {"CALTON_DATABASE_PATH": str(database)}):
        get_settings.cache_clear()
        try:
            command.upgrade(config, "head")
        finally:
            get_settings.cache_clear()

    return create_engine(f"sqlite+pysqlite:///{database}")


def test_migration_and_models_agree(calton_engine: Engine) -> None:
    """The baseline must not drift from the models it was generated from."""
    from_models = create_engine("sqlite+pysqlite://")
    Base.metadata.create_all(from_models)

    migrated = inspect(calton_engine)
    modelled = inspect(from_models)

    for table in IMPLEMENTED_TABLES:
        assert _columns(calton_engine, table) == _columns(from_models, table), table
        assert migrated.get_indexes(table) == modelled.get_indexes(table), table


def _columns(engine: Engine, table: str) -> list[dict[str, Any]]:
    return [
        {
            "name": column["name"],
            "type": str(column["type"]),
            "notnull": not column["nullable"],
            "default": column["default"],
        }
        for column in inspect(engine).get_columns(table)
    ]


def _indexes(engine: Engine, table: str) -> list[dict[str, Any]]:
    inspector = inspect(engine)
    indexes = [
        {
            "name": index["name"],
            "unique": bool(index["unique"]),
            "columns": list(index["column_names"]),
        }
        for index in inspector.get_indexes(table)
    ]
    for constraint in [inspector.get_unique_constraints(table)]:
        indexes.extend(
            {"name": item["name"], "unique": True, "columns": list(item["column_names"])}
            for item in constraint
        )
    return sorted(indexes, key=lambda index: index["name"] or "")


def test_every_implemented_table_exists(calton_engine: Engine) -> None:
    created = set(inspect(calton_engine).get_table_names())

    assert set(IMPLEMENTED_TABLES) <= created


@pytest.mark.parametrize("table", IMPLEMENTED_TABLES)
def test_column_names_match_go(
    calton_engine: Engine, go_schema: dict[str, Any], table: str
) -> None:
    expected = [column["name"] for column in go_schema[table]["columns"]]

    assert [column["name"] for column in _columns(calton_engine, table)] == expected


@pytest.mark.parametrize("table", IMPLEMENTED_TABLES)
def test_column_types_and_nullability_match_go(
    calton_engine: Engine, go_schema: dict[str, Any], table: str
) -> None:
    expected = {
        column["name"]: (column["type"], column["notnull"])
        for column in go_schema[table]["columns"]
    }
    actual = {
        column["name"]: (column["type"], column["notnull"])
        for column in _columns(calton_engine, table)
    }

    assert actual == expected


@pytest.mark.parametrize("table", IMPLEMENTED_TABLES)
def test_column_defaults_match_go(
    calton_engine: Engine, go_schema: dict[str, Any], table: str
) -> None:
    expected = {column["name"]: column["default"] for column in go_schema[table]["columns"]}
    actual = {column["name"]: column["default"] for column in _columns(calton_engine, table)}

    assert actual == expected


@pytest.mark.parametrize("table", IMPLEMENTED_TABLES)
def test_index_names_and_columns_match_go(
    calton_engine: Engine, go_schema: dict[str, Any], table: str
) -> None:
    expected = go_schema[table]["indexes"]

    assert _indexes(calton_engine, table) == expected


class TestKnownTraps:
    """Details that are easy to get wrong and would not fail loudly elsewhere."""

    def test_tasks_project_index_is_unique(self, go_schema: dict[str, Any]) -> None:
        """Task.index is per-project, enforced by this index; T18 relies on the conflict."""
        index = next(
            item
            for item in go_schema["tasks"]["indexes"]
            if item["name"] == "UQE_tasks_tasks_project_index"
        )

        assert index["unique"]
        assert index["columns"] == ["project_id", "index"]

    def test_parent_project_id_is_nullable(self, calton_engine: Engine) -> None:
        """Three-state handling (design R9) needs NULL and 0 to both be storable."""
        column = next(
            item
            for item in _columns(calton_engine, "projects")
            if item["name"] == "parent_project_id"
        )

        assert not column["notnull"]

    def test_autoincrement_matches_go_exactly(
        self, calton_engine: Engine, go_schema: dict[str, Any]
    ) -> None:
        """AUTOINCREMENT must be present on exactly the tables Go puts it on.

        Where Go has it, omitting it lets SQLite reuse the ids of deleted rows, so id
        allocation diverges after the first delete. Where Go does not have it (the two
        keyless position tables and favorites), adding it would create a
        ``sqlite_sequence`` row Go has no counterpart for. Neither direction is visible
        to column introspection, so both are asserted against the DDL text.
        """
        with calton_engine.connect() as connection:
            for table in IMPLEMENTED_TABLES:
                expected = "AUTOINCREMENT" in go_schema[table]["ddl"]

                ddl = connection.scalar(
                    text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:name"),
                    {"name": table},
                )
                assert ddl is not None
                assert ("AUTOINCREMENT" in ddl) is expected, (
                    f"{table}: Calton "
                    f"{'omits' if expected else 'adds'} AUTOINCREMENT where Go "
                    f"{'has' if expected else 'has none'}"
                )

    def test_go_uses_autoincrement_selectively(self, go_schema: dict[str, Any]) -> None:
        """Pins the split, so the test above cannot pass by comparing nothing to nothing."""
        without = {
            table for table in IMPLEMENTED_TABLES if "AUTOINCREMENT" not in go_schema[table]["ddl"]
        }

        assert without == {"task_positions", "task_buckets", "favorites"}

    def test_position_tables_have_no_primary_key(self, calton_engine: Engine) -> None:
        """task_positions and task_buckets are keyed only by a unique index upstream."""
        inspector = inspect(calton_engine)

        for table in ("task_positions", "task_buckets"):
            assert inspector.get_pk_constraint(table)["constrained_columns"] == []


class TestT09Traps:
    """Details specific to the twelve tables added in T09."""

    def test_api_token_lookup_indexes_exist(self, calton_engine: Engine) -> None:
        """T15 finds candidates by the last eight characters, then re-hashes each one."""
        indexes = {index["name"]: index for index in _indexes(calton_engine, "api_tokens")}

        assert indexes["UQE_api_tokens_token_hash"]["unique"]
        assert not indexes["IDX_api_tokens_token_last_eight"]["unique"]

    def test_favorites_has_a_composite_primary_key(self, calton_engine: Engine) -> None:
        """Unlike task_positions and task_buckets, this one really does declare a key."""
        key = inspect(calton_engine).get_pk_constraint("favorites")

        assert key["constrained_columns"] == ["entity_id", "user_id", "kind"]

    def test_no_table_declares_foreign_key_constraints(self, calton_engine: Engine) -> None:
        """xorm emits none, so neither do we.

        T09's task card asks for foreign key constraints, but the Go schema has zero of
        them across all 38 tables — it only indexes the columns. Adding them would make
        the schema diff above fail, and that diff is what AC-1 rests on. Relationships
        are expressed in the ORM instead. Raised with architect-1; this test pins the
        decision so it cannot be reintroduced by accident.
        """
        inspector = inspect(calton_engine)

        for table in IMPLEMENTED_TABLES:
            assert inspector.get_foreign_keys(table) == [], table

    @pytest.mark.parametrize(
        ("table", "column"),
        [
            ("users_projects", "user_id"),
            ("users_projects", "project_id"),
            ("team_projects", "team_id"),
            ("team_projects", "project_id"),
            ("team_members", "team_id"),
            ("team_members", "user_id"),
            ("task_comments", "task_id"),
            ("task_reminders", "task_id"),
            ("saved_filters", "owner_id"),
            ("subscriptions", "user_id"),
        ],
    )
    def test_relationship_columns_are_indexed(
        self, calton_engine: Engine, table: str, column: str
    ) -> None:
        """What upstream does instead of foreign keys, and what the joins actually need."""
        indexed = {tuple(index["columns"]) for index in _indexes(calton_engine, table)}

        assert any(columns[0] == column for columns in indexed), f"{table}.{column}"


def test_fixture_records_its_provenance(fixture_file: dict[str, Any]) -> None:
    """When this file's diffs go red, the first question is which upstream build it came
    from. ``scripts/build_go_reference.sh`` records that beside the database and
    ``dump_go_schema.py`` copies it in here.
    """
    meta = fixture_file["_meta"]

    assert meta["commit"], "no upstream commit recorded"
    assert len(meta["commit"]) == 40
    assert meta["version"]
    assert meta["generated_at"].endswith("Z")


def test_fixture_excludes_bookkeeping_tables(fixture_file: dict[str, Any]) -> None:
    """Upstream's 38 tables include xormigrate's own bookkeeping and sqlite_sequence.

    Calton tracks migrations with Alembic's alembic_version, so those can never match and
    must stay out of the comparison (architect-1's ruling).
    """
    excluded = {"migration", "migration_status", "sqlite_sequence", "alembic_version"}

    assert excluded.isdisjoint(fixture_file["tables"])
