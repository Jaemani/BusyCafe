from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.universal_contracts import (
    CoverageSnapshot,
    CrowdObservation,
    RawObservation,
    RegionProfile,
)


def _raw_observation() -> RawObservation:
    return RawObservation(
        observation_type="provider_congestion_level",
        value=3,
        unit="provider_level",
        label="약간 붐빔",
        definition="서울시가 공개한 네 단계 지역 혼잡도",
        source_field="AREA_CONGEST_LVL",
    )


def test_region_profile_validates_state_timezone_and_locales() -> None:
    profile = RegionProfile(
        region_id="kr-seoul",
        country_code="KR",
        city_code="seoul",
        timezone="Asia/Seoul",
        default_locale="ko-KR",
        supported_locales=("ko-KR", "en-US"),
        state="live",
    )

    assert profile.timezone == "Asia/Seoul"
    assert profile.state == "live"

    with pytest.raises(ValidationError, match="valid IANA timezone"):
        RegionProfile(
            region_id="invalid",
            country_code="KR",
            city_code="invalid",
            timezone="Asia/Not-A-City",
            default_locale="ko-KR",
            supported_locales=("ko-KR",),
            state="pilot",
        )

    with pytest.raises(ValidationError, match="default locale"):
        RegionProfile(
            region_id="kr-seoul",
            country_code="KR",
            city_code="seoul",
            timezone="Asia/Seoul",
            default_locale="ko-KR",
            supported_locales=("en-US",),
            state="catalog_only",
        )

    with pytest.raises(ValidationError):
        RegionProfile(
            region_id="kr-seoul",
            country_code="KR",
            city_code="seoul",
            timezone="Asia/Seoul",
            default_locale="ko-KR",
            supported_locales=("ko-KR",),
            state="beta",
        )


def test_observation_normalizes_aware_timestamps_to_utc() -> None:
    kst = timezone(timedelta(hours=9))
    observation = CrowdObservation(
        provider_id="seoul-citydata",
        source_version="2026-07-12",
        license_manifest_id="seoul-open-data-v1",
        region_id="kr-seoul",
        area_id="POI001",
        geometry_version="2026-04-02",
        observed_at=datetime(2026, 7, 12, 9, 0, tzinfo=kst),
        fetched_at=datetime(2026, 7, 12, 9, 5, tzinfo=kst),
        raw=_raw_observation(),
    )

    assert observation.observed_at == datetime(
        2026, 7, 12, 0, 0, tzinfo=timezone.utc
    )
    assert observation.fetched_at == datetime(
        2026, 7, 12, 0, 5, tzinfo=timezone.utc
    )


def test_observation_rejects_naive_or_reversed_timestamps() -> None:
    fields = {
        "provider_id": "provider",
        "source_version": "v1",
        "license_manifest_id": "license-v1",
        "region_id": "region",
        "area_id": "area",
        "geometry_version": "v1",
        "raw": _raw_observation(),
    }

    with pytest.raises(ValidationError, match="UTC offset"):
        CrowdObservation(
            **fields,
            observed_at=datetime(2026, 7, 12, 0, 0),
            fetched_at=datetime(2026, 7, 12, 0, 1, tzinfo=timezone.utc),
        )

    with pytest.raises(ValidationError, match="at or after"):
        CrowdObservation(
            **fields,
            observed_at=datetime(2026, 7, 12, 0, 1, tzinfo=timezone.utc),
            fetched_at=datetime(2026, 7, 12, 0, 0, tzinfo=timezone.utc),
        )


def test_raw_observation_preserves_provider_meaning() -> None:
    raw = _raw_observation()

    assert raw.model_dump() == {
        "observation_type": "provider_congestion_level",
        "value": 3,
        "unit": "provider_level",
        "label": "약간 붐빔",
        "definition": "서울시가 공개한 네 단계 지역 혼잡도",
        "source_field": "AREA_CONGEST_LVL",
    }

    with pytest.raises(ValidationError, match="value or label"):
        RawObservation(
            observation_type="provider_congestion_level",
            value=None,
            unit="provider_level",
            definition="공급자 원본 레벨",
            source_field="level",
        )


def test_coverage_has_bounded_status_and_utc_timestamps() -> None:
    snapshot = CoverageSnapshot(
        provider_id="provider",
        source_version="v1",
        license_manifest_id="license-v1",
        region_id="region",
        area_id="area",
        geometry_version="v1",
        status="stale",
        observed_at="2026-07-12T00:00:00+09:00",
        fetched_at="2026-07-12T00:01:00+09:00",
        reason="provider freshness SLA exceeded",
    )

    assert snapshot.observed_at.isoformat() == "2026-07-11T15:00:00+00:00"
    assert snapshot.fetched_at.isoformat() == "2026-07-11T15:01:00+00:00"

    with pytest.raises(ValidationError):
        CoverageSnapshot(
            provider_id="provider",
            source_version="v1",
            license_manifest_id="license-v1",
            region_id="region",
            area_id="area",
            geometry_version="v1",
            status="covered",
            observed_at="2026-07-12T00:00:00Z",
            fetched_at="2026-07-12T00:01:00Z",
        )


@pytest.mark.parametrize("field", ["source_version", "license_manifest_id"])
def test_audit_references_are_required_and_non_empty(field: str) -> None:
    observation_values = {
        "provider_id": "provider",
        "source_version": "v1",
        "license_manifest_id": "license-v1",
        "region_id": "region",
        "area_id": "area",
        "geometry_version": "geometry-v1",
        "observed_at": "2026-07-12T00:00:00Z",
        "fetched_at": "2026-07-12T00:01:00Z",
        "raw": _raw_observation(),
    }
    coverage_values = {
        key: value
        for key, value in observation_values.items()
        if key != "raw"
    }
    coverage_values["status"] = "live"
    observation_values[field] = ""
    coverage_values[field] = ""

    with pytest.raises(ValidationError):
        CrowdObservation(**observation_values)
    with pytest.raises(ValidationError):
        CoverageSnapshot(**coverage_values)


def test_contracts_forbid_unknown_fields_and_are_immutable() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        RegionProfile(
            region_id="kr-seoul",
            country_code="KR",
            city_code="seoul",
            timezone="Asia/Seoul",
            default_locale="ko-KR",
            supported_locales=("ko-KR",),
            state="live",
            provider_secret="must-not-enter-contract",
        )

    raw = _raw_observation()
    with pytest.raises(ValidationError, match="frozen"):
        raw.unit = "changed"  # type: ignore[misc]
