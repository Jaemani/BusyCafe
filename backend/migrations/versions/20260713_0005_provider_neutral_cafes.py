"""Add provider-neutral cafe origins and verified provider identities.

Revision ID: 20260713_0005
Revises: 20260712_0004
Create Date: 2026-07-13
"""

from collections.abc import Sequence
from datetime import UTC, datetime
import re
from urllib.parse import urlparse

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260713_0005"
down_revision: str | None = "20260712_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAVER_MAP_PATH = re.compile(r"^/p/entry/place/([0-9]+)/?$")
_NAVER_MOBILE_PATH = re.compile(
    r"^/(place|restaurant)/([0-9]+)(?:/(?:home|menu|review|photo))?/?$"
)


def _naver_detail_reference(website: object) -> tuple[str, str] | None:
    """Return a normalized Naver place ID/link for strict detail URLs only."""

    if not isinstance(website, str):
        return None
    parsed = urlparse(website.strip())
    if parsed.scheme not in {"http", "https"}:
        return None
    host = parsed.hostname
    if host in {"map.naver.com", "m.map.naver.com"}:
        matched = _NAVER_MAP_PATH.fullmatch(parsed.path)
        if matched:
            place_id = matched.group(1)
            return place_id, f"https://map.naver.com/p/entry/place/{place_id}"
        return None
    if host == "m.place.naver.com":
        matched = _NAVER_MOBILE_PATH.fullmatch(parsed.path)
        if matched:
            place_type, place_id = matched.groups()
            return place_id, f"https://m.place.naver.com/{place_type}/{place_id}"
    return None


def upgrade() -> None:
    with op.batch_alter_table("cafes") as batch:
        batch.add_column(
            sa.Column("origin_provider", sa.String(length=32), nullable=True)
        )
        batch.add_column(
            sa.Column("origin_source_id", sa.String(length=255), nullable=True)
        )

    op.execute(
        "UPDATE cafes SET origin_provider = 'overture', "
        "origin_source_id = overture_id"
    )

    with op.batch_alter_table("cafes") as batch:
        batch.alter_column(
            "origin_provider",
            existing_type=sa.String(length=32),
            nullable=False,
            server_default="overture",
        )
        batch.alter_column(
            "overture_id", existing_type=sa.String(length=64), nullable=True
        )
        batch.alter_column(
            "origin_source_id",
            existing_type=sa.String(length=255),
            nullable=False,
        )
        batch.create_check_constraint(
            "origin_provider_nonempty", "length(origin_provider) > 0"
        )
        batch.create_check_constraint(
            "origin_source_id_nonempty", "length(origin_source_id) > 0"
        )
        batch.create_unique_constraint(
            "uq_cafes_origin_provider_source_id",
            ["origin_provider", "origin_source_id"],
        )

    op.create_table(
        "cafe_provider_places",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cafe_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_place_id", sa.String(length=255), nullable=False),
        sa.Column("detail_url", sa.String(length=1000), nullable=True),
        sa.Column(
            "active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("match_method", sa.String(length=64), nullable=False),
        sa.Column("match_distance_m", sa.Float(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(provider) > 0",
            name=op.f("ck_cafe_provider_places_provider_nonempty"),
        ),
        sa.CheckConstraint(
            "length(provider_place_id) > 0",
            name=op.f("ck_cafe_provider_places_provider_place_id_nonempty"),
        ),
        sa.CheckConstraint(
            "length(match_method) > 0",
            name=op.f("ck_cafe_provider_places_match_method_nonempty"),
        ),
        sa.CheckConstraint(
            "match_distance_m IS NULL OR match_distance_m >= 0",
            name=op.f("ck_cafe_provider_places_match_distance_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["cafe_id"],
            ["cafes.id"],
            name=op.f("fk_cafe_provider_places_cafe_id_cafes"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cafe_provider_places")),
        sa.UniqueConstraint(
            "provider",
            "provider_place_id",
            name="uq_cafe_provider_places_provider_place_id",
        ),
        sa.UniqueConstraint(
            "cafe_id",
            "provider",
            name="uq_cafe_provider_places_cafe_provider",
        ),
    )

    op.execute(
        "INSERT INTO cafe_provider_places "
        "(cafe_id, provider, provider_place_id, detail_url, active, "
        "match_method, match_distance_m, verified_at, last_seen_at) "
        "SELECT id, 'overture', overture_id, NULL, active, "
        "'source_primary', NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
        "FROM cafes WHERE overture_id IS NOT NULL"
    )
    if context.is_offline_mode():
        return

    connection = op.get_bind()
    now = datetime.now(UTC)
    cafes = connection.execute(
        sa.text(
            "SELECT id, website, active FROM cafes "
            "WHERE overture_id IS NOT NULL ORDER BY id"
        )
    ).mappings()
    seen_naver_ids: dict[str, int] = {}
    rows: list[dict[str, object]] = []
    for cafe in cafes:
        cafe_id = int(cafe["id"])
        naver = _naver_detail_reference(cafe["website"])
        if naver is None:
            continue
        place_id, detail_url = naver
        previous_cafe_id = seen_naver_ids.setdefault(place_id, cafe_id)
        if previous_cafe_id != cafe_id:
            raise RuntimeError(
                "provider-neutral migration found one Naver place ID on multiple cafes"
            )
        rows.append(
            {
                "cafe_id": cafe_id,
                "provider": "naver",
                "provider_place_id": place_id,
                "detail_url": detail_url,
                "active": bool(cafe["active"]),
                "match_method": "source_direct_url",
                "verified_at": now,
                "last_seen_at": now,
            }
        )
    if rows:
        provider_places = sa.table(
            "cafe_provider_places",
            sa.column("cafe_id", sa.Integer()),
            sa.column("provider", sa.String()),
            sa.column("provider_place_id", sa.String()),
            sa.column("detail_url", sa.String()),
            sa.column("active", sa.Boolean()),
            sa.column("match_method", sa.String()),
            sa.column("verified_at", sa.DateTime(timezone=True)),
            sa.column("last_seen_at", sa.DateTime(timezone=True)),
        )
        connection.execute(provider_places.insert(), rows)


def downgrade() -> None:
    connection = op.get_bind()
    provider_only_count = connection.execute(
        sa.text(
            "SELECT count(*) FROM cafes "
            "WHERE overture_id IS NULL OR origin_provider != 'overture'"
        )
    ).scalar_one()
    if provider_only_count:
        raise RuntimeError(
            "refusing downgrade: provider-only canonical cafes would lose identity"
        )

    op.drop_table("cafe_provider_places")
    with op.batch_alter_table("cafes") as batch:
        batch.drop_constraint(
            "uq_cafes_origin_provider_source_id", type_="unique"
        )
        batch.drop_constraint("origin_source_id_nonempty", type_="check")
        batch.drop_constraint("origin_provider_nonempty", type_="check")
        batch.alter_column(
            "overture_id", existing_type=sa.String(length=64), nullable=False
        )
        batch.drop_column("origin_source_id")
        batch.drop_column("origin_provider")
