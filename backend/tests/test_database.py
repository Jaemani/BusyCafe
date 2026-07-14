"""Database engine configuration tests without external connections."""

from unittest.mock import patch

import pytest

from app.config import (
    DB_MAX_OVERFLOW,
    DB_POOL_RECYCLE_SEC,
    DB_POOL_SIZE,
    DB_POOL_TIMEOUT_SEC,
)
from app.database import create_db_engine, normalize_database_url


@pytest.mark.parametrize(
    ("database_url", "expected_url"),
    [
        (
            "postgresql://user:p%40ss@db.example.com:5432/app?sslmode=require",
            "postgresql+psycopg://user:p%40ss@db.example.com:5432/app?sslmode=require",
        ),
        (
            "postgres://user:p%40ss@db.example.com:5432/app?sslmode=require",
            "postgresql+psycopg://user:p%40ss@db.example.com:5432/app?sslmode=require",
        ),
        (
            "postgresql+psycopg://user:p%40ss@db.example.com:5432/app?sslmode=require",
            "postgresql+psycopg://user:p%40ss@db.example.com:5432/app?sslmode=require",
        ),
    ],
)
def test_create_db_engine_normalizes_postgres_urls(
    database_url: str,
    expected_url: str,
) -> None:
    sentinel = object()

    with patch("app.database.create_engine", return_value=sentinel) as factory:
        result = create_db_engine(database_url)

    assert result is sentinel
    factory.assert_called_once_with(
        expected_url,
        pool_pre_ping=True,
        connect_args={"prepare_threshold": None},
        pool_size=DB_POOL_SIZE,
        max_overflow=DB_MAX_OVERFLOW,
        pool_timeout=DB_POOL_TIMEOUT_SEC,
        pool_recycle=DB_POOL_RECYCLE_SEC,
    )


@pytest.mark.parametrize(
    ("database_url", "expected_url"),
    [
        ("postgresql://user@host/db", "postgresql+psycopg://user@host/db"),
        ("postgres://user@host/db", "postgresql+psycopg://user@host/db"),
        (
            "postgresql+psycopg://user@host/db",
            "postgresql+psycopg://user@host/db",
        ),
        ("sqlite:///local.db", "sqlite:///local.db"),
    ],
)
def test_normalize_database_url_is_shared_with_migrations(
    database_url: str, expected_url: str
) -> None:
    assert normalize_database_url(database_url) == expected_url


def test_create_db_engine_preserves_sqlite_configuration() -> None:
    database_url = "sqlite:///./busy_cafe.db"
    sentinel = object()

    with patch("app.database.create_engine", return_value=sentinel) as factory:
        result = create_db_engine(database_url)

    assert result is sentinel
    factory.assert_called_once_with(
        database_url,
        pool_pre_ping=True,
        connect_args={"check_same_thread": False},
    )
