"""Lock Supabase public tables behind server-only database access.

Revision ID: 20260715_0008
Revises: 20260714_0007
Create Date: 2026-07-15
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0008"
down_revision: str | None = "20260714_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Keep this explicit: the migration must not take ownership of unrelated
# objects that happen to share Supabase's public schema.
PUBLIC_TABLES = (
    "alembic_version",
    "ingest_cycles",
    "hotspots",
    "hotspot_snapshots",
    "hotspot_serving_states",
    "hotspot_parse_failures",
    "cafes",
    "cafe_provider_places",
    "cafe_scores",
)

CLIENT_ROLES = ("anon", "authenticated")


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgresql():
        # SQLite has neither roles nor row-level security. Keeping the
        # revision as a no-op preserves local migration parity.
        return

    for table_name in PUBLIC_TABLES:
        op.execute(
            sa.text(
                f'ALTER TABLE public."{table_name}" ENABLE ROW LEVEL SECURITY'
            )
        )

    table_names = ", ".join(f"'{name}'" for name in PUBLIC_TABLES)
    role_names = ", ".join(f"'{name}'" for name in CLIENT_ROLES)
    op.execute(
        sa.text(
            f"""
            DO $busy_cafe_security$
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
                              ON sequence_namespace.oid = sequence_relation.relnamespace
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
                              AND owning_table.relname IN ({table_names})
                        LOOP
                            EXECUTE format(
                                'REVOKE ALL PRIVILEGES ON SEQUENCE %I.%I FROM %I',
                                target_sequence.schema_name,
                                target_sequence.sequence_name,
                                target_role
                            );
                        END LOOP;

                        EXECUTE format(
                            'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
                            'REVOKE ALL PRIVILEGES ON TABLES FROM %I',
                            target_role
                        );
                        EXECUTE format(
                            'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
                            'REVOKE ALL PRIVILEGES ON SEQUENCES FROM %I',
                            target_role
                        );
                    END IF;
                END LOOP;
            END
            $busy_cafe_security$;
            """
        )
    )


def downgrade() -> None:
    if not _is_postgresql():
        return

    # Never recreate client grants automatically: the migration cannot know
    # which pre-existing grants were intentional. A schema downgrade disables
    # RLS while retained revocations keep direct client access closed.
    for table_name in reversed(PUBLIC_TABLES):
        op.execute(
            sa.text(
                f'ALTER TABLE public."{table_name}" DISABLE ROW LEVEL SECURITY'
            )
        )
