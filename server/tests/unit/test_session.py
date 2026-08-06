"""SQLite has to be configured the same way the Go server configures it
(``pkg/db/db.go:305``: ``?_busy_timeout=5000&_journal_mode=WAL``), plus foreign keys,
which SQLite leaves off by default.
"""

from pathlib import Path

from sqlalchemy import text

from calton.config import DatabaseSettings, Settings
from calton.db.session import build_engine, session_factory


def _settings(tmp_path: Path) -> Settings:
    return Settings(database=DatabaseSettings(path=str(tmp_path / "test.db")))


def test_pragmas_match_upstream(tmp_path: Path) -> None:
    engine = build_engine(_settings(tmp_path))

    with engine.connect() as connection:
        journal_mode = connection.scalar(text("PRAGMA journal_mode"))
        foreign_keys = connection.scalar(text("PRAGMA foreign_keys"))
        busy_timeout = connection.scalar(text("PRAGMA busy_timeout"))

    assert journal_mode == "wal"
    assert foreign_keys == 1
    assert busy_timeout == 5000


def test_pragmas_apply_to_every_connection(tmp_path: Path) -> None:
    engine = build_engine(_settings(tmp_path))

    for _ in range(3):
        with engine.connect() as connection:
            assert connection.scalar(text("PRAGMA foreign_keys")) == 1


def test_in_memory_database_is_supported(tmp_path: Path) -> None:
    engine = build_engine(Settings(database=DatabaseSettings(path=":memory:")))

    with engine.connect() as connection:
        assert connection.scalar(text("SELECT 1")) == 1


def test_session_factory_yields_usable_sessions(tmp_path: Path) -> None:
    factory = session_factory(build_engine(_settings(tmp_path)))

    with factory() as session:
        assert session.scalar(text("SELECT 1")) == 1
