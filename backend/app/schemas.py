"""External API schemas.

The Seoul and Kakao models are backed by raw responses measured on 2026-07-11.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class ExternalLinksResponse(BaseModel):
    """Only provider-verified, direct place-detail URLs are exposed."""

    naver: str | None = None
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
    id: int
    name: str
    lat: float
    lng: float
    road_address: str | None = None
    phone: str | None = None
    website: str | None = None
    source_label: str
    license_manifest_url: str = "/api/sources"
    model_version: str | None = None
    level: int | None = None
    score: float | None = None
    confidence: float | None = None
    confidence_tier: str | None = None
    freshness: Literal["fresh", "delayed", "stale", "n/a"]
    coverage: Literal["covered", "fringe", "uncovered"]
    evidence: EvidenceResponse
    external_links: ExternalLinksResponse


class ContributorResponse(BaseModel):
    hotspot_id: int
    distance_m: float
    level: int
    weight: float


class TrendPointResponse(BaseModel):
    observed_at: datetime
    level: int


class CafeDetailResponse(CafeMapResponse):
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
