from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text

from app.config import BACKEND_DIR, SCORING_MODEL_VERSION, get_settings
from app.models import Base


def test_model_version_migration_backfills_existing_scores(tmp_path: Path, monkeypatch) -> None:
    database_path = tmp_path / "migration.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config(str(BACKEND_DIR / "alembic.ini"))

    try:
        command.upgrade(config, "20260711_0002")
        engine = create_engine(database_url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO cafes "
                    "(overture_id, source_release, source_confidence, primary_category, "
                    "name, lat, lng, website, active) VALUES "
                    "('overture:migration-test', '2026-06-17.0', 0.9, 'cafe', "
                    "'migration cafe', 37.5, 127.0, "
                    "'https://m.place.naver.com/restaurant/123/home?entry=pll', true)"
                )
            )
            cafe_id = connection.execute(
                text("SELECT id FROM cafes WHERE overture_id = 'overture:migration-test'")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO cafe_scores (cafe_id, computed_at, coverage) "
                    "VALUES (:cafe_id, '2026-07-12 00:00:00', 'uncovered')"
                ),
                {"cafe_id": cafe_id},
            )

        command.upgrade(config, "head")

        with engine.connect() as connection:
            stored_version = connection.execute(
                text("SELECT model_version FROM cafe_scores WHERE cafe_id = :cafe_id"),
                {"cafe_id": cafe_id},
            ).scalar_one()
            source_observed_at = connection.execute(
                text(
                    "SELECT source_observed_at FROM cafe_scores "
                    "WHERE cafe_id = :cafe_id"
                ),
                {"cafe_id": cafe_id},
            ).scalar_one()
            columns = {item["name"]: item for item in inspect(connection).get_columns("cafe_scores")}
            cycle_columns = {
                item["name"]: item
                for item in inspect(connection).get_columns("ingest_cycles")
            }
            cafe_columns = {
                item["name"]: item
                for item in inspect(connection).get_columns("cafes")
            }
            provider_columns = {
                item["name"]: item
                for item in inspect(connection).get_columns("cafe_provider_places")
            }
            serving_state_columns = {
                item["name"]: item
                for item in inspect(connection).get_columns(
                    "hotspot_serving_states"
                )
            }
            canonical_origin = connection.execute(
                text(
                    "SELECT origin_provider, origin_source_id FROM cafes "
                    "WHERE id = :cafe_id"
                ),
                {"cafe_id": cafe_id},
            ).one()
            provider_refs = connection.execute(
                text(
                    "SELECT provider, provider_place_id, detail_url "
                    "FROM cafe_provider_places WHERE cafe_id = :cafe_id "
                    "ORDER BY provider"
                ),
                {"cafe_id": cafe_id},
            ).all()

        assert stored_version == SCORING_MODEL_VERSION
        assert source_observed_at is None
        assert columns["model_version"]["nullable"] is False
        assert columns["source_observed_at"]["nullable"] is True
        assert set(cycle_columns) == {
            "id",
            "started_at",
            "completed_at",
            "targets",
            "saved",
            "failed",
            "status",
        }
        assert cycle_columns["completed_at"]["nullable"] is True
        assert cafe_columns["overture_id"]["nullable"] is True
        assert cafe_columns["origin_provider"]["nullable"] is False
        assert cafe_columns["origin_source_id"]["nullable"] is False
        assert canonical_origin == ("overture", "overture:migration-test")
        assert set(provider_columns) == {
            "id",
            "cafe_id",
            "provider",
            "provider_place_id",
            "detail_url",
            "active",
            "match_method",
            "match_distance_m",
            "verified_at",
            "last_seen_at",
        }
        assert set(serving_state_columns) == {
            "hotspot_id",
            "computed_at",
            "observed_at",
            "trend_12h_json",
            "forecast_1h_json",
        }
        assert provider_refs == [
            (
                "naver",
                "123",
                "https://m.place.naver.com/restaurant/123",
            ),
            ("overture", "overture:migration-test", None),
        ]
        engine.dispose()
    finally:
        get_settings.cache_clear()


def test_source_observed_migration_backfills_covered_score(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = tmp_path / "source-observed.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config(str(BACKEND_DIR / "alembic.ini"))

    try:
        command.upgrade(config, "20260713_0005")
        engine = create_engine(database_url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO hotspots "
                    "(area_cd, name, lat, lng, is_polled) VALUES "
                    "('POI-MIGRATION', 'migration hotspot', 37.5, 127.0, true)"
                )
            )
            hotspot_id = connection.execute(
                text(
                    "SELECT id FROM hotspots WHERE area_cd = 'POI-MIGRATION'"
                )
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO hotspot_snapshots "
                    "(hotspot_id, observed_at, fetched_at, congest_level, "
                    "congest_label) VALUES "
                    "(:hotspot_id, '2026-07-14 00:00:00', "
                    "'2026-07-14 00:01:00', 2, '보통')"
                ),
                {"hotspot_id": hotspot_id},
            )
            connection.execute(
                text(
                    "INSERT INTO cafes "
                    "(origin_provider, origin_source_id, source_release, "
                    "source_confidence, primary_category, name, lat, lng, active) "
                    "VALUES ('overture', 'migration:covered', 'test', 1.0, "
                    "'cafe', 'covered migration cafe', 37.5, 127.0, true)"
                )
            )
            cafe_id = connection.execute(
                text(
                    "SELECT id FROM cafes "
                    "WHERE origin_source_id = 'migration:covered'"
                )
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO cafe_scores "
                    "(cafe_id, model_version, computed_at, score, level, "
                    "confidence, confidence_tier, coverage, primary_hotspot_id, "
                    "primary_distance_m, contributors_json) VALUES "
                    "(:cafe_id, :model_version, '2026-07-14 00:01:00', 2.0, 2, "
                    "0.5, 'mid', 'covered', :hotspot_id, 100.0, '[]')"
                ),
                {
                    "cafe_id": cafe_id,
                    "hotspot_id": hotspot_id,
                    "model_version": SCORING_MODEL_VERSION,
                },
            )

        command.upgrade(config, "head")

        with engine.connect() as connection:
            source_observed_at = connection.execute(
                text(
                    "SELECT source_observed_at FROM cafe_scores "
                    "WHERE cafe_id = :cafe_id"
                ),
                {"cafe_id": cafe_id},
            ).scalar_one()

        assert str(source_observed_at).startswith("2026-07-14 00:00:00")

        migrated_score_columns = {
            item["name"]
            for item in inspect(engine).get_columns("cafe_scores")
        }
        migrated_state_columns = {
            item["name"]
            for item in inspect(engine).get_columns("hotspot_serving_states")
        }
        assert migrated_score_columns == set(
            Base.metadata.tables["cafe_scores"].columns.keys()
        )
        assert migrated_state_columns == set(
            Base.metadata.tables["hotspot_serving_states"].columns.keys()
        )

        command.downgrade(config, "20260713_0005")
        assert "source_observed_at" not in {
            item["name"]
            for item in inspect(engine).get_columns("cafe_scores")
        }
        assert not inspect(engine).has_table("hotspot_serving_states")

        command.upgrade(config, "head")
        with engine.connect() as connection:
            restored_source = connection.execute(
                text(
                    "SELECT source_observed_at FROM cafe_scores "
                    "WHERE cafe_id = :cafe_id"
                ),
                {"cafe_id": cafe_id},
            ).scalar_one()
        assert str(restored_source).startswith("2026-07-14 00:00:00")
        engine.dispose()
    finally:
        get_settings.cache_clear()


def test_provider_neutral_downgrade_refuses_provider_only_cafes(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = tmp_path / "provider-only.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config(str(BACKEND_DIR / "alembic.ini"))

    try:
        command.upgrade(config, "head")
        engine = create_engine(database_url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO cafes "
                    "(origin_provider, origin_source_id, overture_id, source_release, "
                    "source_confidence, primary_category, name, lat, lng, active) "
                    "VALUES ('seoul_refreshment_permits', 'permit:1', NULL, "
                    "'OA-16095', 1.0, 'coffee_shop', 'permit cafe', "
                    "37.5, 127.0, true)"
                )
            )

        with pytest.raises(RuntimeError, match="provider-only canonical cafes"):
            command.downgrade(config, "20260712_0004")

        assert inspect(engine).has_table("cafe_provider_places")
        engine.dispose()
    finally:
        get_settings.cache_clear()
