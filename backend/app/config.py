"""Application configuration and all tunable constants.

Secrets are loaded from environment variables.  Values marked as provisional in
the plan remain configurable until Phase 0 verification is recorded.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR: Final = Path(__file__).resolve().parents[1]
FIXTURES_DIR: Final = BACKEND_DIR / "fixtures"

# External API configuration. These endpoints are provisional until Phase 0.
SEOUL_API_BASE_URL: Final = "http://openapi.seoul.go.kr:8088"
SEOUL_CITYDATA_SERVICE: Final = "citydata_ppltn"
SEOUL_RESPONSE_FORMAT: Final = "json"
SEOUL_RESPONSE_START_INDEX: Final = 1
SEOUL_RESPONSE_END_INDEX: Final = 5
SEOUL_VERIFY_AREA_NAME: Final = "광화문광장"

# Official Seoul major-place master attachments (dataset OA-21285).
SEOUL_DATAFILE_DOWNLOAD_URL: Final = (
    "https://datafile.seoul.go.kr/bigfile/iot/inf/nio_download.do"
)
SEOUL_HOTSPOT_MASTER_INF_ID: Final = "OA-21285"
SEOUL_HOTSPOT_MASTER_INF_SEQ: Final = 2
SEOUL_HOTSPOT_LIST_SEQ: Final = 23
SEOUL_HOTSPOT_AREAS_SEQ: Final = 24
SEOUL_HOTSPOT_LIST_PATH: Final = FIXTURES_DIR / "seoul_hotspots_master.xlsx"
SEOUL_HOTSPOT_AREAS_PATH: Final = FIXTURES_DIR / "seoul_hotspot_areas.zip"

KAKAO_LOCAL_BASE_URL: Final = "https://dapi.kakao.com"
KAKAO_CATEGORY_PATH: Final = "/v2/local/search/category.json"
KAKAO_CAFE_CATEGORY_CODE: Final = "CE7"
KAKAO_PAGE_SIZE: Final = 15
KAKAO_MAX_PAGES: Final = 3
KAKAO_MAX_RESULTS_PER_QUERY: Final = KAKAO_PAGE_SIZE * KAKAO_MAX_PAGES
KAKAO_MAX_RADIUS_M: Final = 20_000
KAKAO_VERIFY_LNG: Final = 126.9769
KAKAO_VERIFY_LAT: Final = 37.5759
KAKAO_VERIFY_RADIUS_M: Final = 1_000

HTTP_TIMEOUT_SECONDS: Final = 10.0
HTTP_CONNECT_TIMEOUT_SECONDS: Final = 5.0
HTTP_MAX_RETRIES: Final = 3
HTTP_RETRY_BASE_DELAY_SECONDS: Final = 0.5
HTTP_USER_AGENT: Final = "cafe-crowd/0.1"

# Scoring and polling defaults. Phase 6 may tune these values.
POLL_INTERVAL_MIN: Final = 10
R_MAX_M: Final = 1_500
COVERED_M: Final = 600
K_NEIGHBORS: Final = 3
D_FLOOR_M: Final = 50
TAU_MIN: Final = 15
CONF_HIGH: Final = 0.55
CONF_MID: Final = 0.30
STALE_WARN_MIN: Final = 25
MAX_POLLED_HOTSPOTS: Final = 12

CONGESTION_LEVELS: Final = {
    "여유": 1,
    "보통": 2,
    "약간 붐빔": 3,
    "붐빔": 4,
}


@dataclass(frozen=True, slots=True)
class Neighborhood:
    lat: float
    lng: float
    radius_m: int


# Provisional centers/radii; confirm against the hotspot master in Phase 0/1.
TARGET_NEIGHBORHOODS: Final = {
    "seongsu": Neighborhood(lat=37.5446, lng=127.0557, radius_m=1_500),
    "hongdae": Neighborhood(lat=37.5563, lng=126.9236, radius_m=1_500),
    "yeonnam": Neighborhood(lat=37.5621, lng=126.9254, radius_m=1_500),
}


class Settings(BaseSettings):
    """Environment-backed secrets only; non-secret tuning stays above."""

    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    seoul_api_key: SecretStr | None = None
    kakao_rest_key: SecretStr | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
