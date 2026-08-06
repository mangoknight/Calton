"""Engine and session management.

Synchronous throughout — every path operation is a plain ``def`` so FastAPI runs it in a
threadpool. Do not introduce ``async def`` handlers or an async engine (design §2.1).

SQLite is configured the way the Go server configures it (``pkg/db/db.go:305`` opens the
database with ``?_busy_timeout=5000&_journal_mode=WAL``).
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from fastapi import Request
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from calton.config import Settings, get_settings

PRAGMAS = {
    "journal_mode": "WAL",
    "busy_timeout": 5000,
    # No table in the upstream schema declares a foreign key, so this enforces nothing
    # today; it is set so that any constraint we do add later actually takes effect.
    "foreign_keys": "ON",
}


def build_engine(settings: Settings | None = None, **kwargs: Any) -> Engine:
    settings = settings or get_settings()
    if settings.database.type != "sqlite":
        raise ValueError(f"unsupported database type: {settings.database.type}")

    if settings.database.path == ":memory:":
        # Every connection to :memory: is its own empty database, so tests that create a
        # schema on one connection and query on another need them pinned to one.
        kwargs.setdefault("poolclass", StaticPool)
        kwargs.setdefault("connect_args", {"check_same_thread": False})

    engine = create_engine(f"sqlite+pysqlite:///{settings.database.path}", **kwargs)

    @event.listens_for(engine, "connect")
    def _apply_pragmas(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            for pragma, value in PRAGMAS.items():
                cursor.execute(f"PRAGMA {pragma} = {value}")
        finally:
            cursor.close()

    return engine


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db(request: Request) -> Generator[Session]:
    """FastAPI dependency yielding a request-scoped session.

    The factory lives on ``app.state`` rather than in a module global so tests can build
    an app against their own database without patching anything.
    """
    factory: sessionmaker[Session] = request.app.state.session_factory
    with factory() as session:
        yield session
