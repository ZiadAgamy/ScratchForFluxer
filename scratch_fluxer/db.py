"""Engine / session handling (synchronous SQLAlchemy, friendly to serverless).

Endpoints are ``async`` for the HTTP calls they make, so database work goes through
:func:`db_run`, which executes a plain function in a worker thread with its own session.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, TypeVar

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.concurrency import run_in_threadpool

from .config import DATA_DIR, get_settings
from .models import Base

T = TypeVar("T")

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_lock = threading.Lock()
_init_lock = threading.Lock()
_sqlite_lock = threading.RLock()
_tables_ready = False


def _build_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        kwargs: dict[str, Any] = {"connect_args": {"check_same_thread": False}}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool
        engine = create_engine(url, **kwargs)

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return engine

    # Postgres (Neon / Vercel Postgres). prepare_threshold=None keeps PgBouncer's
    # transaction pooling happy; pool_pre_ping survives serverless freezes.
    return create_engine(
        url,
        pool_size=3,
        max_overflow=3,
        pool_pre_ping=True,
        pool_recycle=240,
        connect_args={"prepare_threshold": None},
    )


def get_engine() -> Engine:
    global _engine, _session_factory
    if _engine is None:
        with _lock:
            if _engine is None:
                _engine = _build_engine(get_settings().effective_database_url)
                _session_factory = sessionmaker(_engine, expire_on_commit=False, autoflush=False)
    return _engine


def init_db() -> None:
    """Create tables if they do not exist yet (idempotent, once per process)."""
    global _tables_ready
    if _tables_ready:
        return
    with _init_lock:
        if _tables_ready:
            return
        engine = get_engine()
        try:
            Base.metadata.create_all(engine)
        except Exception:
            # Two cold starts can race on the very first deploy; the loser just retries.
            time.sleep(0.4)
            Base.metadata.create_all(engine)
        _tables_ready = True


def reset_engine() -> None:
    """Dispose the engine (tests switch databases between cases)."""
    global _engine, _session_factory, _tables_ready
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _session_factory = None
        _tables_ready = False


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
    assert _session_factory is not None
    session = _session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _call_with_session(fn: Callable[..., T], args: tuple, kwargs: dict) -> T:
    init_db()
    if get_engine().dialect.name == "sqlite":
        # Postgres locks the rows that a limit is counted from (``with_for_update``); SQLite has no row locks, so two
        # requests at once could both count 9 bots and both add a 10th. SQLite is only for working on your own
        # computer, so it simply does one piece of database work at a time.
        with _sqlite_lock, session_scope() as db:
            return fn(db, *args, **kwargs)
    with session_scope() as db:
        return fn(db, *args, **kwargs)


async def db_run(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run ``fn(db, *args, **kwargs)`` in a worker thread inside a transaction."""
    return await run_in_threadpool(_call_with_session, fn, args, kwargs)
