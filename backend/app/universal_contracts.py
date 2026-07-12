"""Experimental seams for provider-independent regional observations.

ADR-0010 freezes these models as a seam inventory, not a proven runtime
contract.  No persistence, API, or provider integration imports them today.
Only fields confirmed by a second provider fixture may be promoted into the
runtime boundary; do not expand this module from documentation alone.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


RegionState = Literal["catalog_only", "pilot", "live", "suspended"]
CoverageStatus = Literal["live", "delayed", "stale", "unsupported"]
RawScalar = str | int | float | bool | None


def _as_utc(value: datetime) -> datetime:
    """Require a timezone-aware value and normalize it to UTC."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return value.astimezone(timezone.utc)


UtcDatetime = Annotated[datetime, AfterValidator(_as_utc)]


class UniversalContract(BaseModel):
    """Strict, immutable base for values crossing provider boundaries."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )


class RegionProfile(UniversalContract):
    """Configuration that makes a region explicit and provider-independent."""

    region_id: str = Field(min_length=1)
    country_code: str = Field(min_length=2, max_length=2, pattern=r"^[A-Z]{2}$")
    city_code: str = Field(min_length=1)
    timezone: str = Field(min_length=1)
    default_locale: str = Field(min_length=2)
    supported_locales: tuple[str, ...] = Field(min_length=1)
    state: RegionState

    @field_validator("timezone")
    @classmethod
    def timezone_is_available_in_iana_database(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value

    @field_validator("supported_locales")
    @classmethod
    def supported_locales_are_unique(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        if any(not locale.strip() for locale in value):
            raise ValueError("supported locales must not be blank")
        if len(set(value)) != len(value):
            raise ValueError("supported locales must be unique")
        return value

    @model_validator(mode="after")
    def default_locale_is_supported(self) -> RegionProfile:
        if self.default_locale not in self.supported_locales:
            raise ValueError("default locale must be included in supported locales")
        return self


class RawObservation(UniversalContract):
    """The provider's value and its meaning before any normalization."""

    observation_type: str = Field(min_length=1)
    value: RawScalar
    unit: str = Field(min_length=1)
    label: str | None = Field(default=None, min_length=1)
    definition: str = Field(min_length=1)
    source_field: str = Field(min_length=1)

    @model_validator(mode="after")
    def value_or_label_is_present(self) -> RawObservation:
        if self.value is None and self.label is None:
            raise ValueError("a raw observation requires a value or label")
        return self


class CrowdObservation(UniversalContract):
    """One immutable crowd observation emitted by a provider adapter."""

    provider_id: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    license_manifest_id: str = Field(min_length=1)
    provider_observation_id: str | None = Field(default=None, min_length=1)
    region_id: str = Field(min_length=1)
    area_id: str = Field(min_length=1)
    geometry_version: str = Field(min_length=1)
    observed_at: UtcDatetime
    fetched_at: UtcDatetime
    raw: RawObservation
    quality_flags: tuple[str, ...] = ()

    @field_validator("quality_flags")
    @classmethod
    def quality_flags_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not flag.strip() for flag in value):
            raise ValueError("quality flags must not be blank")
        if len(set(value)) != len(value):
            raise ValueError("quality flags must be unique")
        return value

    @model_validator(mode="after")
    def fetch_does_not_precede_observation(self) -> CrowdObservation:
        if self.fetched_at < self.observed_at:
            raise ValueError("fetched_at must be at or after observed_at")
        return self


class CoverageSnapshot(UniversalContract):
    """Coverage state for one provider area at a specific observation time."""

    provider_id: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    license_manifest_id: str = Field(min_length=1)
    region_id: str = Field(min_length=1)
    area_id: str = Field(min_length=1)
    geometry_version: str = Field(min_length=1)
    status: CoverageStatus
    observed_at: UtcDatetime
    fetched_at: UtcDatetime
    reason: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def fetch_does_not_precede_observation(self) -> CoverageSnapshot:
        if self.fetched_at < self.observed_at:
            raise ValueError("fetched_at must be at or after observed_at")
        return self
