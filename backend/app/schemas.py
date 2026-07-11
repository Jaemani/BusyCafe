"""Provisional external API and verification schemas.

The Seoul envelope is deliberately normalized by the client because Open Data
responses may include header records in the same list as the data record. The
field models below encode the plan's ``[VERIFY]`` assumptions only; they are not
an authoritative contract yet. Unknown fields are preserved until actual raw
fixtures are reviewed and the fixture-backed schema is finalized.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import CONGESTION_LEVELS


# Provisional labels from PLAN.md. Confirm all four against observed API data
# before treating this Literal as the finalized upstream contract.
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


class SeoulLivePopulationStatus(ExternalModel):
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


class SeoulAreaPopulation(ExternalModel):
    area_name: str = Field(alias="AREA_NM", min_length=1)
    area_code: str = Field(alias="AREA_CD", min_length=1)
    live_status: list[SeoulLivePopulationStatus] = Field(alias="LIVE_PPLTN_STTS")

    @field_validator("live_status")
    @classmethod
    def live_status_must_not_be_empty(
        cls, value: list[SeoulLivePopulationStatus]
    ) -> list[SeoulLivePopulationStatus]:
        if not value:
            raise ValueError("LIVE_PPLTN_STTS must contain at least one item")
        return value


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
