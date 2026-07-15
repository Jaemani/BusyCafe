"""Add PostgreSQL trigram indexes for bounded cafe catalog search.

Revision ID: 20260715_0009
Revises: 20260715_0008
Create Date: 2026-07-15
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0009"
down_revision: str | None = "20260715_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgresql():
        # SQLite test/dev catalogs are small and lack pg_trgm. The API query
        # remains dialect-portable; production PostgreSQL gets search indexes.
        return

    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    op.execute(
        sa.text(
            "CREATE INDEX ix_cafes_active_name_trgm ON public.cafes "
            "USING gin (lower(name) gin_trgm_ops) WHERE active"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX ix_cafes_active_road_address_trgm ON public.cafes "
            "USING gin (lower(road_address) gin_trgm_ops) "
            "WHERE active AND road_address IS NOT NULL"
        )
    )


def downgrade() -> None:
    if not _is_postgresql():
        return

    op.execute(
        sa.text(
            "DROP INDEX IF EXISTS public.ix_cafes_active_road_address_trgm"
        )
    )
    op.execute(sa.text("DROP INDEX IF EXISTS public.ix_cafes_active_name_trgm"))
    # pg_trgm may be shared by unrelated objects; never remove the extension.
