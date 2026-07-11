"""Database engine configuration tests without external connections."""

from unittest.mock import patch

import pytest

from app.database import create_db_engine


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
    )


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
