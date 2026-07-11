"""Database engine and transactional session boundaries."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def _normalize_database_url(database_url: str) -> str:
    """Select psycopg 3 without altering the URL's credentials or query string."""

    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+psycopg://", 1)
    return database_url


def create_db_engine(database_url: str | None = None) -> Engine:
    """Create an engine suitable for production or isolated SQLite tests."""

    url = _normalize_database_url(database_url or get_settings().database_url)
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
    elif url.startswith("postgresql+psycopg://"):
        connect_args = {"prepare_threshold": None}
    else:
        connect_args = {}
    return create_engine(url, pool_pre_ping=True, connect_args=connect_args)


engine = create_db_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def get_db() -> Generator[Session, None, None]:
    """Yield one session per request and always release its connection."""

    with SessionLocal() as session:
        yield session
