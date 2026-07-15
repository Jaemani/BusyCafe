"""External API schemas backed by dated, stored response fixtures."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config import CONGESTION_LEVELS


# Two labels were observed in the first Seoul fixture. Confirm the remaining
# two against additional observed data or official material during Phase 0.
CongestionLabel = Literal["여유", "보통", "약간 붐빔", "붐빔"]


class ExternalModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class SeoulForecastPopulation(ExternalModel):
    forecast_time: str = Field(alias="FCST_TIME")
    congestion_level: CongestionLabel = Field(alias="FCST_CONGEST_LVL")
    population_min: int = Field(alias="FCST_PPLTN_MIN", ge=0)
    population_max: int = Field(alias="FCST_PPLTN_MAX", ge=0)

    @field_validator("population_max")
    @classmethod
    def population_range_is_valid(cls, value: int, info: Any) -> int:
        minimum = info.data.get("population_min")
        if minimum is not None and value < minimum:
            raise ValueError("FCST_PPLTN_MAX must be >= FCST_PPLTN_MIN")
        return value


class SeoulAreaPopulation(ExternalModel):
    """One flat population record returned by ``citydata_ppltn``."""

    area_name: str = Field(alias="AREA_NM", min_length=1)
    area_code: str = Field(alias="AREA_CD", min_length=1)
    congestion_level: CongestionLabel = Field(alias="AREA_CONGEST_LVL")
    population_min: int = Field(alias="AREA_PPLTN_MIN", ge=0)
    population_max: int = Field(alias="AREA_PPLTN_MAX", ge=0)
    observed_at: str = Field(alias="PPLTN_TIME", min_length=1)
    forecast_available: str | None = Field(default=None, alias="FCST_YN")
    forecast: list[SeoulForecastPopulation] = Field(
        default_factory=list, alias="FCST_PPLTN"
    )

    @field_validator("population_max")
    @classmethod
    def population_range_is_valid(cls, value: int, info: Any) -> int:
        minimum = info.data.get("population_min")
        if minimum is not None and value < minimum:
            raise ValueError("AREA_PPLTN_MAX must be >= AREA_PPLTN_MIN")
        return value

    @property
    def numeric_level(self) -> int:
        return CONGESTION_LEVELS[self.congestion_level]


class KakaoMeta(ExternalModel):
    total_count: int = Field(ge=0)
    pageable_count: int = Field(ge=0)
    is_end: bool
    same_name: dict[str, Any] | None = None


class KakaoPlace(ExternalModel):
    place_id: str = Field(alias="id", min_length=1)
    place_name: str = Field(min_length=1)
    category_name: str = ""
    category_group_code: str = ""
    category_group_name: str = ""
    phone: str = ""
    address_name: str = ""
    road_address_name: str = ""
    longitude: float = Field(alias="x", ge=-180, le=180)
    latitude: float = Field(alias="y", ge=-90, le=90)
    place_url: str = ""
    distance: str = ""


class KakaoCategoryResponse(ExternalModel):
    meta: KakaoMeta
    documents: list[KakaoPlace]


class NaverLocalItem(ExternalModel):
    """One official Naver local-search result.

    ``link`` is not assumed to be a Naver Place URL.  Provider-ID extraction
    remains a separate fail-closed step.
    """

    title: str = ""
    link: str = ""
    category: str = ""
    description: str = ""
    telephone: str = ""
    address: str = ""
    road_address: str = Field(default="", alias="roadAddress")
    map_x: str = Field(default="", alias="mapx")
    map_y: str = Field(default="", alias="mapy")

    @field_validator("map_x", "map_y", mode="before")
    @classmethod
    def coordinate_as_text(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value)


class NaverLocalResponse(ExternalModel):
    last_build_date: str = Field(default="", alias="lastBuildDate")
    total: int = Field(ge=0)
    start: int = Field(ge=1)
    display: int = Field(ge=0)
    items: list[NaverLocalItem]


_PERMIT_RAW_DECIMAL_PATTERN = re.compile(r"[+-]?[0-9]+(?:\.[0-9]*)?\Z", re.ASCII)


def _permit_raw_decimal(value: Any) -> tuple[str | None, Decimal | None]:
    if value is None:
        return None, None
    raw = str(value).strip()
    if not raw:
        return None, None
    if _PERMIT_RAW_DECIMAL_PATTERN.fullmatch(raw) is None:
        return raw, None
    parsed = Decimal(raw)
    return raw, parsed if parsed.is_finite() else None


class SeoulRefreshmentPermit(ExternalModel):
    """One OA-16095 permit row, preserving source status and category."""

    municipality_code: str = Field(alias="OPNSFTEAMCODE", min_length=1)
    management_number: str | None = Field(default=None, alias="MGTNO")
    permit_date: str | None = Field(default=None, alias="APVPERMYMD")
    trade_status_code: str = Field(alias="TRDSTATEGBN", min_length=1)
    trade_status_name: str = Field(alias="TRDSTATENM", min_length=1)
    detail_status_code: str = Field(alias="DTLSTATEGBN", min_length=1)
    detail_status_name: str = Field(alias="DTLSTATENM", min_length=1)
    closure_date: str | None = Field(default=None, alias="DCBYMD")
    phone: str | None = Field(default=None, alias="SITETEL")
    lot_address: str | None = Field(default=None, alias="SITEWHLADDR")
    road_address: str | None = Field(default=None, alias="RDNWHLADDR")
    business_name: str = Field(alias="BPLCNM", min_length=1)
    last_modified_at: str | None = Field(default=None, alias="LASTMODTS")
    source_updated_at: str | None = Field(default=None, alias="UPDATEDT")
    business_type: str = Field(alias="UPTAENM", min_length=1)
    projected_x_m: float | None = Field(default=None, alias="X")
    projected_y_m: float | None = Field(default=None, alias="Y")
    hygiene_type: str | None = Field(default=None, alias="SNTUPTAENM")
    facility_total_scope_raw: str | None = Field(default=None, alias="FACILTOTSCP")
    facility_total_scope_decimal: Decimal | None = None
    site_area_raw: str | None = Field(default=None, alias="SITEAREA")
    site_area_decimal: Decimal | None = None

    @model_validator(mode="before")
    @classmethod
    def preserve_and_parse_raw_area_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        parsed = dict(value)
        for alias, decimal_field in (
            ("FACILTOTSCP", "facility_total_scope_decimal"),
            ("SITEAREA", "site_area_decimal"),
        ):
            raw, decimal = _permit_raw_decimal(parsed.get(alias))
            parsed[alias] = raw
            parsed[decimal_field] = decimal
        return parsed

    @field_validator(
        "municipality_code",
        "management_number",
        "permit_date",
        "trade_status_code",
        "trade_status_name",
        "detail_status_code",
        "detail_status_name",
        "closure_date",
        "phone",
        "lot_address",
        "road_address",
        "business_name",
        "last_modified_at",
        "source_updated_at",
        "business_type",
        "hygiene_type",
        mode="before",
    )
    @classmethod
    def strip_source_text(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        return stripped or None

    @field_validator("projected_x_m", "projected_y_m", mode="before")
    @classmethod
    def blank_coordinate_is_missing(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def is_reported_open(self) -> bool:
        """Conservative source-state predicate; not a cafe classifier."""

        return (
            self.trade_status_code == "01"
            and self.trade_status_name == "영업/정상"
            and self.detail_status_code == "01"
            and self.detail_status_name == "영업"
            and self.closure_date is None
        )

    @property
    def has_projected_coordinates(self) -> bool:
        return self.projected_x_m is not None and self.projected_y_m is not None


class SeoulRefreshmentPermitPage(BaseModel):
    total_count: int = Field(ge=0)
    result_code: str
    result_message: str
    rows: list[SeoulRefreshmentPermit]


class VerificationSummary(BaseModel):
    generated_at: datetime
    services: list[str]
    observed_seoul_labels: list[str] = Field(default_factory=list)
    seoul_area_name: str | None = None
    seoul_area_code: str | None = None
    kakao_result_count: int | None = None


class EvidenceResponse(BaseModel):
    hotspot_name: str | None = None
    distance_m: float | None = None
    observed_at: datetime | None = None
    age_minutes: int | None = Field(default=None, ge=0)


class ExternalLinksResponse(BaseModel):
    """Verified detail URLs plus an explicitly non-identity search fallback."""

    naver: str | None = None
    naver_search: str | None = None
    kakao: str | None = None
    google: str | None = None


class LicenseLink(BaseModel):
    name: str
    url: str


class DataSourceManifestItem(BaseModel):
    id: str
    role: str
    name: str
    attribution: str
    source_url: str
    release: str | None = None
    licenses: list[LicenseLink]


class SourceManifestResponse(BaseModel):
    sources: list[DataSourceManifestItem]


class CafeMapResponse(BaseModel):
    """Compact viewport payload; place metadata belongs to detail reads."""

    id: int
    name: str
    lat: float
    lng: float
    level: int | None = None
    confidence: float | None = None
    freshness: Literal["fresh", "delayed", "stale", "n/a"]
    coverage: Literal["covered", "fringe", "uncovered"]
    evidence: EvidenceResponse


class CafeMapSummaryResponse(BaseModel):
    """Viewport-only fields; full evidence remains available from detail reads."""

    id: int
    name: str
    lat: float
    lng: float
    level: int | None = None
    confidence: float | None = None
    freshness: Literal["fresh", "delayed", "stale", "n/a"]
    coverage: Literal["covered", "fringe", "uncovered"]
    age_minutes: int | None = Field(default=None, ge=0)


class CafeSearchResponse(CafeMapResponse):
    """Bounded catalog-search result with enough context to disambiguate names."""

    road_address: str | None = None


class ContributorResponse(BaseModel):
    hotspot_id: int
    distance_m: float
    level: int
    weight: float


class TrendPointResponse(BaseModel):
    observed_at: datetime
    level: int


class CafeDetailResponse(CafeMapResponse):
    road_address: str | None = None
    phone: str | None = None
    website: str | None = None
    source_label: str
    license_manifest_url: str = "/api/sources"
    model_version: str | None = None
    score: float | None = None
    confidence_tier: str | None = None
    external_links: ExternalLinksResponse
    primary_hotspot_id: int | None = None
    contributors: list[ContributorResponse] = Field(default_factory=list)
    trend_12h: list[TrendPointResponse] = Field(default_factory=list)
    forecast_1h: dict[str, Any] | None = None


class HotspotStatusResponse(BaseModel):
    id: int
    area_cd: str
    name: str
    lat: float
    lng: float
    observed_at: datetime | None = None
    level: int | None = None
    freshness: Literal["fresh", "delayed", "stale", "n/a"]


class HealthResponse(BaseModel):
    data_mode: Literal["snapshot", "live"]
    stale_warn_min: int = Field(ge=1)
    current_display_max_age_min: int = Field(ge=1)
    last_ingest_at: datetime | None = None
    last_complete_cycle_at: datetime | None = None
    last_cycle_status: (
        Literal["running", "complete", "partial", "failed"] | None
    ) = None
    last_cycle_targets: int | None = None
    last_cycle_saved: int | None = None
    last_cycle_failed: int | None = None
    snapshots_last_hour: int
    cafes_count: int
