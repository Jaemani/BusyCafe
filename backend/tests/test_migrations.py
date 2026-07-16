from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text

from app.config import BACKEND_DIR, SCORING_MODEL_VERSION, get_settings
from app.database import normalize_database_url
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
            place_report_columns = {
                item["name"]: item
                for item in inspect(connection).get_columns(
                    "cafe_place_reports"
                )
            }
            feedback_columns = {
                item["name"]: item
                for item in inspect(connection).get_columns(
                    "cafe_crowd_feedback"
                )
            }
            rate_limit_columns = {
                item["name"]: item
                for item in inspect(connection).get_columns(
                    "user_contribution_rate_limits"
                )
            }
            snapshot_indexes = {
                item["name"]
                for item in inspect(connection).get_indexes(
                    "hotspot_snapshots"
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
        assert "ix_snap_fetched_at" in snapshot_indexes
        assert set(serving_state_columns) == {
            "hotspot_id",
            "computed_at",
            "observed_at",
            "trend_12h_json",
            "forecast_1h_json",
        }
        assert set(place_report_columns) == {
            "id",
            "cafe_id",
            "report_type",
            "status",
            "reported_name",
            "created_at",
        }
        assert place_report_columns["cafe_id"]["nullable"] is True
        assert set(feedback_columns) == {
            "id",
            "cafe_id",
            "street_feedback",
            "seat_feedback",
            "status",
            "model_version",
            "predicted_level",
            "coverage",
            "source_observed_at",
            "created_at",
        }
        assert feedback_columns["cafe_id"]["nullable"] is False
        assert set(rate_limit_columns) == {
            "kind",
            "bucket_epoch",
            "submission_count",
        }
        assert rate_limit_columns["kind"]["nullable"] is False
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


def test_public_table_lockdown_is_sqlite_compatible(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = tmp_path / "rls-noop.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config(str(BACKEND_DIR / "alembic.ini"))

    try:
        command.upgrade(config, "head")
        engine = create_engine(database_url)
        assert set(Base.metadata.tables).issubset(inspect(engine).get_table_names())

        command.downgrade(config, "20260714_0007")
        command.upgrade(config, "head")
        assert set(Base.metadata.tables).issubset(inspect(engine).get_table_names())
        engine.dispose()
    finally:
        get_settings.cache_clear()


def test_postgresql_search_migration_installs_trigram_indexes() -> None:
    raw_database_url = os.environ.get("DATABASE_URL", "")
    if not raw_database_url:
        pytest.skip("PostgreSQL CI database is not configured")
    database_url = normalize_database_url(raw_database_url)
    if not database_url.startswith("postgresql+psycopg://"):
        pytest.skip("PostgreSQL-only search-index assertion")

    engine = create_engine(database_url)
    if engine.url.database != "cafe_crowd_test":
        engine.dispose()
        pytest.skip("refusing to mutate a non-test PostgreSQL database")

    get_settings.cache_clear()
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    try:
        command.upgrade(config, "head")
        with engine.connect() as connection:
            extension_installed = connection.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_extension "
                    "WHERE extname = 'pg_trgm')"
                )
            ).scalar_one()
            index_definitions = dict(
                connection.execute(
                    text(
                        "SELECT indexname, indexdef FROM pg_indexes "
                        "WHERE schemaname = 'public' AND tablename = 'cafes' "
                        "AND indexname IN ("
                        "'ix_cafes_active_name_trgm', "
                        "'ix_cafes_active_road_address_trgm')"
                    )
                ).all()
            )

        assert extension_installed
        assert set(index_definitions) == {
            "ix_cafes_active_name_trgm",
            "ix_cafes_active_road_address_trgm",
        }
        assert all(
            "USING gin" in definition and "gin_trgm_ops" in definition
            for definition in index_definitions.values()
        )
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_public_table_lockdown_revokes_supabase_client_access_on_postgresql() -> None:
    raw_database_url = os.environ.get("DATABASE_URL", "")
    if not raw_database_url:
        pytest.skip("PostgreSQL CI database is not configured")
    database_url = normalize_database_url(raw_database_url)
    if not database_url.startswith("postgresql+psycopg://"):
        pytest.skip("PostgreSQL-only security assertion")

    engine = create_engine(database_url)
    if engine.url.database != "cafe_crowd_test":
        engine.dispose()
        pytest.skip("refusing to mutate a non-test PostgreSQL database")
    with engine.connect() as connection:
        is_superuser = connection.execute(
            text(
                "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
            )
        ).scalar_one()
    if not is_superuser:
        engine.dispose()
        pytest.skip("test role cannot create Supabase compatibility roles")

    get_settings.cache_clear()
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    expected_tables = {"alembic_version", *Base.metadata.tables.keys()}

    try:
        # Make reruns deterministic, then recreate the exposure this migration
        # must remove. The CI PostgreSQL service is isolated and ephemeral.
        command.upgrade(config, "head")
        command.downgrade(config, "20260714_0007")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DO $roles$ BEGIN "
                    "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') "
                    "THEN CREATE ROLE anon NOLOGIN; END IF; "
                    "IF NOT EXISTS (SELECT 1 FROM pg_roles "
                    "WHERE rolname = 'authenticated') "
                    "THEN CREATE ROLE authenticated NOLOGIN; END IF; "
                    "END $roles$"
                )
            )
            connection.execute(
                text(
                    "GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public "
                    "TO anon, authenticated"
                )
            )
            connection.execute(
                text(
                    "GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public "
                    "TO anon, authenticated"
                )
            )
            connection.execute(
                text(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                    "GRANT ALL PRIVILEGES ON TABLES TO anon, authenticated"
                )
            )
            connection.execute(
                text(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                    "GRANT ALL PRIVILEGES ON SEQUENCES TO anon, authenticated"
                )
            )

        command.upgrade(config, "head")

        with engine.begin() as connection:
            rls_states = dict(
                connection.execute(
                    text(
                        "SELECT relation.relname, relation.relrowsecurity "
                        "FROM pg_class AS relation "
                        "JOIN pg_namespace AS namespace "
                        "ON namespace.oid = relation.relnamespace "
                        "WHERE namespace.nspname = 'public' "
                        "AND relation.relkind = 'r'"
                    )
                ).all()
            )
            assert expected_tables <= rls_states.keys()
            assert all(rls_states[name] for name in expected_tables)

            sequence_rows = connection.execute(
                text(
                    "SELECT format('%I.%I', namespace.nspname, relation.relname), "
                    "owning_table.relname "
                    "FROM pg_class AS relation "
                    "JOIN pg_namespace AS namespace "
                    "ON namespace.oid = relation.relnamespace "
                    "JOIN pg_depend AS dependency "
                    "ON dependency.objid = relation.oid "
                    "AND dependency.classid = 'pg_class'::regclass "
                    "AND dependency.refclassid = 'pg_class'::regclass "
                    "AND dependency.deptype IN ('a', 'i') "
                    "JOIN pg_class AS owning_table "
                    "ON owning_table.oid = dependency.refobjid "
                    "WHERE relation.relkind = 'S' "
                    "AND namespace.nspname = 'public' "
                    "AND owning_table.relkind = 'r'"
                )
            ).all()
            sequence_names = [
                sequence_name
                for sequence_name, owning_table in sequence_rows
                if owning_table in expected_tables
            ]
            assert sequence_names

            for role_name in ("anon", "authenticated"):
                for table_name in expected_tables:
                    assert not connection.execute(
                        text(
                            "SELECT has_table_privilege("
                            ":role_name, :table_name, 'SELECT')"
                        ),
                        {
                            "role_name": role_name,
                            "table_name": f"public.{table_name}",
                        },
                    ).scalar_one()
                for sequence_name in sequence_names:
                    assert not connection.execute(
                        text(
                            "SELECT has_sequence_privilege("
                            ":role_name, :sequence_name, 'USAGE')"
                        ),
                        {
                            "role_name": role_name,
                            "sequence_name": sequence_name,
                        },
                    ).scalar_one()

            # Revoked default privileges must also protect future app objects.
            connection.execute(
                text(
                    "CREATE TABLE public.busy_cafe_security_probe "
                    "(id serial PRIMARY KEY)"
                )
            )
            for role_name in ("anon", "authenticated"):
                assert not connection.execute(
                    text(
                        "SELECT has_table_privilege("
                        ":role_name, 'public.busy_cafe_security_probe', 'SELECT')"
                    ),
                    {"role_name": role_name},
                ).scalar_one()
                assert not connection.execute(
                    text(
                        "SELECT has_sequence_privilege("
                        ":role_name, 'public.busy_cafe_security_probe_id_seq', "
                        "'USAGE')"
                    ),
                    {"role_name": role_name},
                ).scalar_one()
            connection.execute(
                text("DROP TABLE public.busy_cafe_security_probe")
            )

            # The backend's postgres owner path still bypasses non-forced RLS.
            inserted_id = connection.execute(
                text(
                    "INSERT INTO hotspots (area_cd, name, lat, lng, is_polled) "
                    "VALUES ('RLS-CI-PROBE', 'RLS CI probe', 37.5, 127.0, false) "
                    "ON CONFLICT (area_cd) DO UPDATE SET name = EXCLUDED.name "
                    "RETURNING id"
                )
            ).scalar_one()
            assert connection.execute(
                text("SELECT name FROM hotspots WHERE id = :id"),
                {"id": inserted_id},
            ).scalar_one() == "RLS CI probe"
            connection.execute(
                text("DELETE FROM hotspots WHERE id = :id"),
                {"id": inserted_id},
            )
    finally:
        engine.dispose()
        get_settings.cache_clear()
