"""Database engine and transactional session boundaries."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import (
    DB_MAX_OVERFLOW,
    DB_POOL_RECYCLE_SEC,
    DB_POOL_SIZE,
    DB_POOL_TIMEOUT_SEC,
    get_settings,
)


def normalize_database_url(database_url: str) -> str:
    """Select psycopg 3 without altering the URL's credentials or query string."""

    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+psycopg://", 1)
    return database_url


def create_db_engine(database_url: str | None = None) -> Engine:
    """Create an engine suitable for production or isolated SQLite tests."""

    url = normalize_database_url(database_url or get_settings().database_url)
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
    elif url.startswith("postgresql+psycopg://"):
        connect_args = {"prepare_threshold": None}
    else:
        connect_args = {}
    engine_options: dict[str, object] = {
        "pool_pre_ping": True,
        "connect_args": connect_args,
    }
    if url.startswith("postgresql+psycopg://"):
        engine_options.update(
            pool_size=DB_POOL_SIZE,
            max_overflow=DB_MAX_OVERFLOW,
            pool_timeout=DB_POOL_TIMEOUT_SEC,
            pool_recycle=DB_POOL_RECYCLE_SEC,
        )
    return create_engine(url, **engine_options)


engine = create_db_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def get_db() -> Generator[Session, None, None]:
    """Yield one session per request and always release its connection."""

    with SessionLocal() as session:
        yield session
