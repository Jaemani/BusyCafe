"""Add a shared aggregate user-contribution rate limit.

Revision ID: 20260716_0011
Revises: 20260716_0010
Create Date: 2026-07-16
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260716_0011"
down_revision: str | None = "20260716_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CLIENT_ROLES = ("anon", "authenticated")
TABLE_NAME = "user_contribution_rate_limits"


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _lock_down_postgresql_table() -> None:
    if not _is_postgresql():
        return

    op.execute(
        sa.text(
            f'ALTER TABLE public."{TABLE_NAME}" ENABLE ROW LEVEL SECURITY'
        )
    )
    role_names = ", ".join(f"'{name}'" for name in CLIENT_ROLES)
    op.execute(
        sa.text(
            f"""
            DO $busy_cafe_rate_limit_security$
            DECLARE
                target_role text;
            BEGIN
                FOREACH target_role IN ARRAY ARRAY[{role_names}]
                LOOP
                    IF EXISTS (
                        SELECT 1 FROM pg_roles WHERE rolname = target_role
                    ) THEN
                        EXECUTE format(
                            'REVOKE ALL PRIVILEGES ON TABLE public.%I FROM %I',
                            '{TABLE_NAME}',
                            target_role
                        );
                    END IF;
                END LOOP;
            END
            $busy_cafe_rate_limit_security$;
            """
        )
    )


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("bucket_epoch", sa.BigInteger(), nullable=False),
        sa.Column("submission_count", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('feedback', 'place_report')",
            name=op.f("ck_user_contribution_rate_limits_kind_values"),
        ),
        sa.CheckConstraint(
            "bucket_epoch >= 0",
            name=op.f(
                "ck_user_contribution_rate_limits_bucket_epoch_nonnegative"
            ),
        ),
        sa.CheckConstraint(
            "submission_count > 0",
            name=op.f(
                "ck_user_contribution_rate_limits_submission_count_positive"
            ),
        ),
        sa.PrimaryKeyConstraint(
            "kind", name=op.f("pk_user_contribution_rate_limits")
        ),
    )
    _lock_down_postgresql_table()


def downgrade() -> None:
    op.drop_table(TABLE_NAME)
