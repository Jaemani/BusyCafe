"""Add append-only place reports and unverified crowd feedback.

Revision ID: 20260716_0010
Revises: 20260715_0009
Create Date: 2026-07-16
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260716_0010"
down_revision: str | None = "20260715_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


NEW_PUBLIC_TABLES = (
    "cafe_place_reports",
    "cafe_crowd_feedback",
)
CLIENT_ROLES = ("anon", "authenticated")


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _lock_down_new_postgresql_tables() -> None:
    if not _is_postgresql():
        return

    for table_name in NEW_PUBLIC_TABLES:
        op.execute(
            sa.text(
                f'ALTER TABLE public."{table_name}" ENABLE ROW LEVEL SECURITY'
            )
        )

    table_names = ", ".join(f"'{name}'" for name in NEW_PUBLIC_TABLES)
    role_names = ", ".join(f"'{name}'" for name in CLIENT_ROLES)
    op.execute(
        sa.text(
            f"""
            DO $busy_cafe_submission_security$
            DECLARE
                target_role text;
                target_table text;
                target_sequence record;
            BEGIN
                FOREACH target_role IN ARRAY ARRAY[{role_names}]
                LOOP
                    IF EXISTS (
                        SELECT 1 FROM pg_roles WHERE rolname = target_role
                    ) THEN
                        FOREACH target_table IN ARRAY ARRAY[{table_names}]
                        LOOP
                            EXECUTE format(
                                'REVOKE ALL PRIVILEGES ON TABLE public.%I FROM %I',
                                target_table,
                                target_role
                            );
                        END LOOP;

                        FOR target_sequence IN
                            SELECT sequence_namespace.nspname AS schema_name,
                                   sequence_relation.relname AS sequence_name
                            FROM pg_class AS sequence_relation
                            JOIN pg_namespace AS sequence_namespace
                              ON sequence_namespace.oid =
                                 sequence_relation.relnamespace
                            JOIN pg_depend AS dependency
                              ON dependency.objid = sequence_relation.oid
                             AND dependency.classid = 'pg_class'::regclass
                             AND dependency.refclassid = 'pg_class'::regclass
                             AND dependency.deptype IN ('a', 'i')
                            JOIN pg_class AS owning_table
                              ON owning_table.oid = dependency.refobjid
                            JOIN pg_namespace AS table_namespace
                              ON table_namespace.oid = owning_table.relnamespace
                            WHERE sequence_relation.relkind = 'S'
                              AND sequence_namespace.nspname = 'public'
                              AND table_namespace.nspname = 'public'
                              AND owning_table.relkind = 'r'
                              AND owning_table.relname IN ({table_names})
                        LOOP
                            EXECUTE format(
                                'REVOKE ALL PRIVILEGES ON SEQUENCE %I.%I FROM %I',
                                target_sequence.schema_name,
                                target_sequence.sequence_name,
                                target_role
                            );
                        END LOOP;
                    END IF;
                END LOOP;
            END
            $busy_cafe_submission_security$;
            """
        )
    )


def upgrade() -> None:
    op.create_table(
        "cafe_place_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cafe_id", sa.Integer(), nullable=True),
        sa.Column("report_type", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("reported_name", sa.String(length=80), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "report_type IN ('missing', 'wrong_details', 'closed')",
            name=op.f("ck_cafe_place_reports_report_type_values"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'verified', 'rejected')",
            name=op.f("ck_cafe_place_reports_status_values"),
        ),
        sa.CheckConstraint(
            "(cafe_id IS NULL AND report_type = 'missing' "
            "AND reported_name IS NOT NULL "
            "AND length(reported_name) BETWEEN 2 AND 80) OR "
            "(cafe_id IS NOT NULL "
            "AND report_type IN ('missing', 'wrong_details', 'closed') "
            "AND reported_name IS NULL)",
            name=op.f("ck_cafe_place_reports_cafe_matches_report_type"),
        ),
        sa.ForeignKeyConstraint(
            ["cafe_id"],
            ["cafes.id"],
            name=op.f("fk_cafe_place_reports_cafe_id_cafes"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cafe_place_reports")),
    )
    op.create_index(
        "ix_cafe_place_reports_status_created",
        "cafe_place_reports",
        ["status", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_cafe_place_reports_cafe_created",
        "cafe_place_reports",
        ["cafe_id", sa.text("created_at DESC")],
    )

    op.create_table(
        "cafe_crowd_feedback",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cafe_id", sa.Integer(), nullable=False),
        sa.Column("street_feedback", sa.String(length=16), nullable=False),
        sa.Column("seat_feedback", sa.String(length=16), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="unverified",
            nullable=False,
        ),
        sa.Column("model_version", sa.String(length=64), nullable=True),
        sa.Column("predicted_level", sa.Integer(), nullable=True),
        sa.Column("coverage", sa.String(length=16), nullable=True),
        sa.Column(
            "source_observed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "street_feedback IN ('busier', 'similar', 'quieter')",
            name=op.f(
                "ck_cafe_crowd_feedback_street_feedback_values"
            ),
        ),
        sa.CheckConstraint(
            "seat_feedback IN ('available', 'limited', 'full', 'not_entered')",
            name=op.f(
                "ck_cafe_crowd_feedback_seat_feedback_values"
            ),
        ),
        sa.CheckConstraint(
            "status IN ('unverified', 'verified', 'rejected')",
            name=op.f("ck_cafe_crowd_feedback_status_values"),
        ),
        sa.CheckConstraint(
            "predicted_level IS NULL OR predicted_level BETWEEN 1 AND 4",
            name=op.f(
                "ck_cafe_crowd_feedback_predicted_level_range"
            ),
        ),
        sa.CheckConstraint(
            "coverage IS NULL OR "
            "coverage IN ('covered', 'fringe', 'uncovered')",
            name=op.f("ck_cafe_crowd_feedback_coverage_values"),
        ),
        sa.CheckConstraint(
            "(model_version IS NULL AND predicted_level IS NULL "
            "AND coverage IS NULL AND source_observed_at IS NULL) OR "
            "(model_version IS NOT NULL AND coverage = 'uncovered' "
            "AND predicted_level IS NULL AND source_observed_at IS NULL) OR "
            "(model_version IS NOT NULL AND coverage IN ('covered', 'fringe') "
            "AND predicted_level BETWEEN 1 AND 4 "
            "AND source_observed_at IS NOT NULL)",
            name=op.f(
                "ck_cafe_crowd_feedback_prediction_snapshot_consistent"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["cafe_id"],
            ["cafes.id"],
            name=op.f("fk_cafe_crowd_feedback_cafe_id_cafes"),
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_cafe_crowd_feedback")
        ),
    )
    op.create_index(
        "ix_cafe_crowd_feedback_cafe_created",
        "cafe_crowd_feedback",
        ["cafe_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_cafe_crowd_feedback_model_created",
        "cafe_crowd_feedback",
        ["model_version", sa.text("created_at DESC")],
    )

    _lock_down_new_postgresql_tables()


def downgrade() -> None:
    op.drop_index(
        "ix_cafe_crowd_feedback_model_created",
        table_name="cafe_crowd_feedback",
    )
    op.drop_index(
        "ix_cafe_crowd_feedback_cafe_created",
        table_name="cafe_crowd_feedback",
    )
    op.drop_table("cafe_crowd_feedback")
    op.drop_index(
        "ix_cafe_place_reports_cafe_created",
        table_name="cafe_place_reports",
    )
    op.drop_index(
        "ix_cafe_place_reports_status_created",
        table_name="cafe_place_reports",
    )
    op.drop_table("cafe_place_reports")
