"""Application configuration and all tunable constants.

Secrets are loaded from environment variables.  Values marked as provisional in
the plan remain configurable until Phase 0 verification is recorded.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Final

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR: Final = Path(__file__).resolve().parents[1]
FIXTURES_DIR: Final = BACKEND_DIR / "fixtures"

# Seoul endpoint and schema were measured in Phase 0.
SEOUL_API_BASE_URL: Final = "http://openapi.seoul.go.kr:8088"
SEOUL_CITYDATA_SERVICE: Final = "citydata_ppltn"
SEOUL_RESPONSE_FORMAT: Final = "json"
SEOUL_RESPONSE_START_INDEX: Final = 1
SEOUL_RESPONSE_END_INDEX: Final = 5
SEOUL_VERIFY_AREA_NAME: Final = "광화문광장"

# Official Seoul refreshment-food business permits (dataset OA-16095).
# The dataset page and live Open API response were both checked on 2026-07-13.
# Coordinates are projected metres, not latitude/longitude; the portal names
# Korean 1985 / Modified Central Belt (EPSG:5174) and a three-day source lag.
SEOUL_REFRESHMENT_PERMIT_DATASET_ID: Final = "OA-16095"
SEOUL_REFRESHMENT_PERMIT_SERVICE: Final = "LOCALDATA_072405"
SEOUL_REFRESHMENT_PERMIT_CRS: Final = "EPSG:5174"
SEOUL_REFRESHMENT_PERMIT_WGS84_CRS: Final = "EPSG:4326"
SEOUL_REFRESHMENT_PERMIT_MAX_PAGE_SIZE: Final = 1_000
SEOUL_REFRESHMENT_PERMIT_SOURCE_LAG_DAYS: Final = 3
SEOUL_REFRESHMENT_PERMIT_PROFILE_PATH: Final = (
    BACKEND_DIR / "data" / "seoul_refreshment_permit_profile.json"
)
# Report-only candidates. No row is promoted to the cafe catalog until each
# exact source category passes the full profile and a manual precision sample.
SEOUL_REFRESHMENT_PROVISIONAL_CAFE_TYPES: Final = (
    "커피숍",
    "다방",
    "전통찻집",
    "떡카페",
)
# Read-only cross-source reconciliation gates. These create review evidence;
# they do not authorize automatic catalog publication.
PERMIT_RECONCILE_EXACT_NAME_MAX_M: Final = 50.0
PERMIT_RECONCILE_EXACT_PHONE_MAX_M: Final = 150.0
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

# Living population bulk files (OA-22784)
# `infId`/`infSeq`/the SEOUL_DATAFILE_DOWNLOAD_URL endpoint above were read
# from the dataset page's `frmFile` hidden fields
# (https://data.seoul.go.kr/dataList/OA-22784/F/1/datasetView.do) and then
# confirmed live on 2026-07-12 by POSTing this exact payload for a daily file
# (250_LOCAL_RESD_20260708.zip, 15,037,162 bytes). The portal page exposed the
# monthly target metadata (250_LOCAL_RESD_202606.zip, 448,638,322 bytes), but
# the monthly body was not downloaded in full. The per-file `seq` value itself
# is NOT a stored constant here -- it is derived per request; see notes on
# `build_download_target()` in app/clients/seoul_living_population_files.py.
SEOUL_LIVING_POPULATION_INF_ID: Final = "OA-22784"
SEOUL_LIVING_POPULATION_INF_SEQ: Final = 1
LIVING_POPULATION_DATA_DIR: Final = BACKEND_DIR / "data" / "living_population"
LIVING_POPULATION_HISTORY_START_MONTH: Final = "202301"
LIVING_POPULATION_BACKFILL_MANIFEST_FILENAME: Final = "backfill_manifest.json"
LIVING_POPULATION_BACKFILL_MANIFEST_SCHEMA_VERSION: Final = 1
LIVING_POPULATION_HASH_CHUNK_BYTES: Final = 1024 * 1024
# Offline-only compact extract used by the living-population baseline research.
# These versions are persisted in every manifest so a change in validation,
# normalization, or physical output is never mistaken for the same dataset.
LIVING_POPULATION_COMPACT_SCHEMA_VERSION: Final = 1
LIVING_POPULATION_COMPACT_QUERY_VERSION: Final = "oa-22784-cp949-cells-v1"
LIVING_POPULATION_COMPACT_MANIFEST_SUFFIX: Final = ".manifest.json"
LIVING_POPULATION_COMPACT_PART_SUFFIX: Final = ".part"
LIVING_POPULATION_COMPACT_PARQUET_COMPRESSION: Final = "zstd"
LIVING_POPULATION_COMPACT_PARQUET_ROW_GROUP_SIZE: Final = 100_000
LIVING_POPULATION_COMPACT_MISSING_CELL_AUDIT_LIMIT: Final = 20

# Kakao Local is an offline catalog-enrichment source. It is never called from
# the map request path. Sweep tuning lives here so completeness/cost changes
# are explicit and testable.
KAKAO_LOCAL_BASE_URL: Final = "https://dapi.kakao.com"
KAKAO_CATEGORY_PATH: Final = "/v2/local/search/category.json"
KAKAO_CAFE_CATEGORY_CODE: Final = "CE7"
KAKAO_PAGE_SIZE: Final = 15
KAKAO_MAX_PAGES: Final = 3
KAKAO_MAX_RESULTS_PER_QUERY: Final = KAKAO_PAGE_SIZE * KAKAO_MAX_PAGES
KAKAO_MAX_RADIUS_M: Final = 20_000
KAKAO_SWEEP_MAX_DEPTH: Final = 14
KAKAO_SWEEP_MIN_CELL_SPAN_DEG: Final = 0.00005
KAKAO_SWEEP_MAX_CALLS: Final = 80_000
KAKAO_RETRYABLE_STATUS_CODES: Final = frozenset({429, 500, 502, 503, 504})
KAKAO_RETRY_AFTER_MAX_SECONDS: Final = 30.0
KAKAO_CACHE_DIR: Final = BACKEND_DIR / "data" / "kakao"
KAKAO_CACHE_FILENAME: Final = "kakao-ce7-seoul.jsonl"
# Hard stop for the manually confirmed Kakao-owned canonical catalog apply.
# The operator supplies a lower expected-candidate bound for every write run.
KAKAO_CATALOG_APPLY_ABSOLUTE_MAX_CANDIDATES: Final = 40_000
KAKAO_VERIFY_LNG: Final = 126.9769
KAKAO_VERIFY_LAT: Final = 37.5759
KAKAO_VERIFY_RADIUS_M: Final = 1_000

# Naver Search Local API is an offline provider-link enrichment source.  Its
# official contract returns at most five results per query and has a 25,000
# request daily quota.  A result is useful only when ``link`` itself contains
# a canonical numeric Naver Place ID; non-identity search fallbacks use the
# separate public map-search contract below and never enter provider identity.
NAVER_SEARCH_BASE_URL: Final = "https://openapi.naver.com"
NAVER_MAP_SEARCH_BASE_URL: Final = "https://map.naver.com/p/search"
NAVER_LOCAL_SEARCH_PATH: Final = "/v1/search/local.json"
NAVER_LOCAL_MAX_DISPLAY: Final = 5
NAVER_LOCAL_DAILY_CALL_LIMIT: Final = 25_000
# Naver calls its relevance ordering "random"; this is the official API token,
# not stochastic sampling.  Keeping it centralized prevents accidental switch
# to review-count ordering ("comment").
NAVER_LOCAL_SORT: Final = "random"
NAVER_LOCAL_RETRYABLE_STATUS_CODES: Final = frozenset({429, 500, 502, 503, 504})
NAVER_LOCAL_RETRY_AFTER_MAX_SECONDS: Final = 30.0
NAVER_VERIFY_QUERY: Final = "스타벅스 더종로R점 서울 종로구 종로 51"

HTTP_TIMEOUT_SECONDS: Final = 10.0
HTTP_CONNECT_TIMEOUT_SECONDS: Final = 5.0
HTTP_MAX_RETRIES: Final = 3
HTTP_RETRY_BASE_DELAY_SECONDS: Final = 0.5
HTTP_USER_AGENT: Final = "cafe-crowd/0.1"
HTTP_MAX_CONNECTIONS: Final = 4
HTTP_MAX_KEEPALIVE_CONNECTIONS: Final = 4

# Polling interval confirmed after the portal reported no call-count limit.
# Scoring defaults remain subject to Phase 6 calibration.
POLL_INTERVAL_MIN: Final = 5
# Production pg_cron starts away from the top of the hour, then dispatches the
# health monitor after the normal one-cycle worker runtime.  Scheduler code
# derives its full minute lists from these centralized values.
PRODUCTION_POLL_MINUTE_OFFSET: Final = 2
PRODUCTION_MONITOR_DELAY_MIN: Final = 2
POLL_MAX_CONSECUTIVE_FAILURES: Final = 5
# Read-only operational report defaults. A cadence gap is reported only after
# two expected poll intervals so normal worker runtime jitter is not an outage.
INGEST_SLO_DEFAULT_WINDOW_HOURS: Final = 24.0
INGEST_SLO_CADENCE_GAP_FACTOR: Final = 2.0
# Bounds upstream pressure while reducing the 121-place cycle latency.
# Seoul endpoint accepted the same five targets sequentially while the
# four-way production batch timed out on every target. Keep ingestion serial
# until provider-side parallel request tolerance is measured independently.
POLL_FETCH_CONCURRENCY: Final = 1
SCORING_MODEL_VERSION: Final = "v1-idw-point"
R_MAX_M: Final = 1_500
COVERED_M: Final = 600
K_NEIGHBORS: Final = 3
D_FLOOR_M: Final = 50
TAU_MIN: Final = 15
CONF_HIGH: Final = 0.55
CONF_MID: Final = 0.30
# Offline-only geometry challenger. An independent namespace keeps shadow
# runs reproducible if public v1 defaults are calibrated later.
POLYGON_SHADOW_MODEL_VERSION: Final = "v2-polygon-shadow"
POLYGON_SHADOW_GEOMETRY_VERSION: Final = "oa-21285-2026-04-02-make-valid-v1"
POLYGON_SHADOW_R_MAX_M: Final = R_MAX_M
POLYGON_SHADOW_COVERED_M: Final = COVERED_M
POLYGON_SHADOW_K_NEIGHBORS: Final = K_NEIGHBORS
POLYGON_SHADOW_D_FLOOR_M: Final = D_FLOOR_M
POLYGON_SHADOW_TAU_MIN: Final = TAU_MIN
POLYGON_SHADOW_CONF_HIGH: Final = CONF_HIGH
POLYGON_SHADOW_CONF_MID: Final = CONF_MID
# Metres per degree of latitude used by the v3 density challenger's local
# equirectangular polygon-area approximation. A geodesy constant (WGS84 mean),
# not a tuning parameter; longitude metres per degree are derived as this value
# times cos(latitude), matching polygon_shadow._boundary_distance_m.
M_PER_DEG_LAT: Final = 111_320.0
# Offline-only density challenger. Population density (people/m^2) replaces the
# 1-4 congestion label as the interpolated signal. A separate namespace mirrors
# the v2 polygon defaults so calibrating either model never perturbs the other;
# confidence tiers are intentionally absent because there is no level mapping.
DENSITY_SHADOW_MODEL_VERSION: Final = "v3-density-shadow"
DENSITY_SHADOW_GEOMETRY_VERSION: Final = POLYGON_SHADOW_GEOMETRY_VERSION
DENSITY_SHADOW_R_MAX_M: Final = POLYGON_SHADOW_R_MAX_M
DENSITY_SHADOW_COVERED_M: Final = POLYGON_SHADOW_COVERED_M
DENSITY_SHADOW_K_NEIGHBORS: Final = POLYGON_SHADOW_K_NEIGHBORS
DENSITY_SHADOW_D_FLOOR_M: Final = POLYGON_SHADOW_D_FLOOR_M
DENSITY_SHADOW_TAU_MIN: Final = POLYGON_SHADOW_TAU_MIN
# People/m^2 floor added before taking the logarithm so a genuinely empty
# hotspot (ppltn == 0) maps to a finite log-density instead of -inf. Chosen well
# below one person spread over the largest supported polygon (1 / 1e8 = 1e-8),
# so it never dominates a real non-zero density.
DENSITY_SHADOW_LOG_EPSILON: Final = 1e-9
# Sanity bounds (m^2) for official Seoul hotspot polygons. Areas outside this
# range signal a projection or ingest error rather than a real place; official
# OA-21285 polygons fall well inside it.
DENSITY_SHADOW_AREA_MIN_M2: Final = 1e2
DENSITY_SHADOW_AREA_MAX_M2: Final = 1e8
# Offline-only temporal baseline challenger.  These provisional defaults are
# isolated from public v1 and must not be promoted before the pre-registered
# living-population correlation and Phase 6 gates pass.
TEMPORAL_BASELINE_SHADOW_MODEL_VERSION: Final = "v1-temporal-baseline-shadow"
TEMPORAL_BASELINE_SHADOW_WINDOW_DAYS: Final = 84
TEMPORAL_BASELINE_SHADOW_RECENCY_HALF_LIFE_DAYS: Final = 28.0
# Public/long-holiday observations are intrinsically sparse.  Shadow runs use
# a longer history and slower decay for those targets only; both remain
# caller-overridable and require empirical calibration before promotion.
TEMPORAL_BASELINE_SHADOW_SPECIAL_WINDOW_DAYS: Final = 1_095
TEMPORAL_BASELINE_SHADOW_SPECIAL_RECENCY_HALF_LIFE_DAYS: Final = 365.0
TEMPORAL_BASELINE_SHADOW_MIN_BUCKET_RAW_N: Final = 3
TEMPORAL_BASELINE_SHADOW_SHRINKAGE_PRIOR_EFFECTIVE_N: Final = 4.0
# The verified OA-22784 source masks low-population totals with ``*``.  The
# pre-registered primary analysis substitutes 2.0 people and repeats with 0.0
# and 3.0 as sensitivity bounds; callers can override this shadow-only value.
TEMPORAL_BASELINE_SHADOW_MASKED_IMPUTATION: Final = 2.0
# Offline-only city-activity contract.  It intentionally exposes a source-local
# anomaly rather than an uncalibrated 0..100 index.  Standardized anomalies are
# clipped only to keep pathological low-dispersion inputs from dominating
# shadow comparisons; promotion requires an empirical calibration gate.
ACTIVITY_SHADOW_MODEL_VERSION: Final = "v1-city-activity-shadow"
ACTIVITY_SHADOW_MIN_LOG_DISPERSION: Final = 1e-6
ACTIVITY_SHADOW_STANDARDIZED_ANOMALY_CAP: Final = 5.0
SHADOW_DIVERGENCE_AUDIT_LIMIT: Final = 20
# Offline-only observation-lag nowcast challenger.  It interpolates the saved
# Seoul forecast curve to the original fetch time, then backtests that estimate
# against an actual snapshot which arrived later.  None of these values affect
# public v1 materialization or API responses.
NOWCAST_SHADOW_MODEL_VERSION: Final = "v1-forecast-lag-shadow"
NOWCAST_HYBRID_SHADOW_MODEL_VERSION: Final = (
    "v1-forecast-population-observed-level-shadow"
)
# Population-error deltas within this absolute tolerance are deterministic
# ties for per-hotspot/day win-rate reporting.
NOWCAST_HYBRID_TIE_ABS_EPSILON: Final = 1e-9
NOWCAST_SHADOW_TIME_FORMAT: Final = "%Y-%m-%d %H:%M"
NOWCAST_SHADOW_MAX_HORIZON_MIN: Final = 12 * 60
NOWCAST_SHADOW_MAX_INTERPOLATION_GAP_MIN: Final = 90
NOWCAST_SHADOW_NEAREST_TOLERANCE_MIN: Final = 20
NOWCAST_SHADOW_ACTUAL_TOLERANCE_MIN: Final = 10
NOWCAST_SHADOW_LAG_BUCKET_EDGES_MIN: Final = (15.0, 30.0, 60.0, 120.0)
# Bound production evaluation cost as append-only snapshots grow.  Fourteen
# days preserve twice the minimum gate span while preventing an unbounded JSON
# forecast transfer from PostgreSQL on every manual backtest.
NOWCAST_SHADOW_BACKTEST_WINDOW_DAYS: Final = 14
# Promotion requires both broad historical support and no regression against
# the uncorrected delayed observation.  Thresholds are deliberately strict;
# this hotspot-forecast gate cannot replace Phase 6 cafe ground truth.
NOWCAST_SHADOW_MIN_SAMPLES: Final = 1_000
NOWCAST_SHADOW_MIN_HOTSPOTS: Final = 80
NOWCAST_SHADOW_MIN_SPAN_DAYS: Final = 7.0
NOWCAST_SHADOW_MAX_POPULATION_WAPE: Final = 0.20
NOWCAST_SHADOW_MIN_LEVEL_EXACT_ACCURACY: Final = 0.70
NOWCAST_SHADOW_MIN_LEVEL_ADJACENT_ACCURACY: Final = 0.95
# Confidence V2 remains a shadow input-quality score until empirical
# calibration passes Track 1 Gate D. These weights intentionally exclude the
# validation-sufficiency placeholder: sample quantity is not runtime input
# quality and must not be presented as an accuracy probability.
CONF_V2_SPATIAL_WEIGHT: Final = 0.30
CONF_V2_FRESHNESS_WEIGHT: Final = 0.30
CONF_V2_AGREEMENT_WEIGHT: Final = 0.25
CONF_V2_SOURCE_HEALTH_WEIGHT: Final = 0.15
CONF_V2_SINGLE_NEIGHBOR_AGREEMENT: Final = 0.50
CONF_V2_ALIGNMENT_TAU_MIN: Final = 10.0
CONF_V2_PARTIAL_CYCLE_FACTOR: Final = 0.50
CONF_V2_VALIDATION_TARGET_SAMPLES: Final = 120
STALE_WARN_MIN: Final = 25
# Current area level remains usable as explicitly delayed evidence for at most
# two hours. It is never relabeled fresh after STALE_WARN_MIN.
CURRENT_DISPLAY_MAX_AGE_MIN: Final = 120
FRESHNESS_MAX_FUTURE_SKEW_MIN: Final = 2
MAX_CAFES_PER_VIEWPORT: Final = 5_000
# Public read API cache policy. Vercel keys shared-cache entries by the full
# path and query string, so the frontend must request canonical tile bboxes to
# reuse these entries. Map/detail data may lag one ingest cycle already; keep
# edge freshness far below POLL_INTERVAL_MIN while allowing stale delivery
# briefly during background revalidation or an origin failure.
API_MAP_BROWSER_MAX_AGE_SEC: Final = 30
API_MAP_EDGE_MAX_AGE_SEC: Final = 60
API_MAP_STALE_WHILE_REVALIDATE_SEC: Final = 120
API_MAP_STALE_IF_ERROR_SEC: Final = 600
# Health drives the stale banner and therefore gets a much shorter edge TTL.
API_HEALTH_BROWSER_MAX_AGE_SEC: Final = 0
API_HEALTH_EDGE_MAX_AGE_SEC: Final = 10
API_HEALTH_STALE_WHILE_REVALIDATE_SEC: Final = 10
API_HEALTH_STALE_IF_ERROR_SEC: Final = 60
# Attribution/source metadata changes only with a deployment.
API_STATIC_BROWSER_MAX_AGE_SEC: Final = 3_600
API_STATIC_EDGE_MAX_AGE_SEC: Final = 86_400
API_STATIC_STALE_WHILE_REVALIDATE_SEC: Final = 604_800
API_STATIC_STALE_IF_ERROR_SEC: Final = 604_800
EVAL_DEFAULT_HOTSPOT_NAMES: Final = ("홍대 관광특구", "성수카페거리")
EVAL_CANDIDATES_PER_BAND: Final = 4
EVAL_NEAR_MAX_M: Final = 300
EVAL_PILOT_SLOTS: Final = 3
EVAL_OBSERVATION_RADIUS_M: Final = 50
EVAL_OBSERVATION_DURATION_MIN: Final = 3
EVAL_AREA_PEDESTRIANS_PER_MIN_THRESHOLDS: Final = (5, 15, 30)
EVAL_MIN_SPEARMAN: Final = 0.50
EVAL_MIN_ADJACENT_ACCURACY: Final = 0.80
# Gate B requires no measured segment regression before public promotion.
SHADOW_MAX_SEGMENT_REGRESSION: Final = 0.0
FRONTEND_CORS_ORIGINS: Final = (
    "http://localhost:5188",
    "http://127.0.0.1:5188",
)
TAILNET_CORS_ORIGIN_REGEX: Final = r"https://[a-z0-9-]+\.tail2743ae\.ts\.net(?::8443)?"
OFFICIAL_HOTSPOT_COUNT: Final = 121
MAX_POLLED_HOTSPOTS: Final = OFFICIAL_HOTSPOT_COUNT

# Coarse Seoul guard used only to limit bulk POI ingest and reject accidental
# worldwide queries. Precise administrative-boundary filtering is a Phase 2
# verification item.
SEOUL_BBOX: Final = (126.76, 37.41, 127.20, 37.72)

# Overture is fetched only by an operator-run monthly ingest. User requests
# never touch this source: the result is materialized in PostgreSQL first.
OVERTURE_RELEASE: Final = "2026-06-17.0"
OVERTURE_S3_URI_TEMPLATE: Final = (
    "s3://overturemaps-us-west-2/release/{release}/theme=places/type=place/*"
)
OVERTURE_MIN_CONFIDENCE: Final = 0.80
# Ignore storage float round-trip jitter only.  The coordinate tolerance is
# about 0.0000001 m in Seoul; direct comparison across 4,933 cached rows found
# a maximum round-trip coordinate delta of 0.0000000443 m.
OVERTURE_COORDINATE_ABS_TOL_DEG: Final = 1e-12
OVERTURE_CONFIDENCE_ABS_TOL: Final = 1e-12
OVERTURE_CAFE_CATEGORIES: Final = (
    "cafe",
    "coffee_shop",
    "bubble_tea",
    "tea_room",
    "coffee_roastery",
)

# Permit-origin rows are admitted only after strict one-to-one confirmation by
# a CE7 provider place. This catalog-quality value is not forecast confidence.
PROVIDER_CAFE_RELEASE: Final = "OA-16095+kakao-local"
PROVIDER_VERIFIED_CAFE_CONFIDENCE: Final = 1.0
PROVIDER_LAST_SEEN_UPDATE_BATCH_SIZE: Final = 500

# `--confidence-report` (read-only, no network) bucket range for the Overture
# confidence-threshold study. Does not affect ingest filtering.
OVERTURE_CONFIDENCE_REPORT_MIN: Final = 0.50
OVERTURE_CONFIDENCE_REPORT_MAX: Final = 1.00
OVERTURE_CONFIDENCE_REPORT_STEP: Final = 0.05

CONGESTION_LEVELS: Final = {
    "여유": 1,
    "보통": 2,
    "약간 붐빔": 3,
    "붐빔": 4,
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
    naver_client_id: SecretStr | None = None
    naver_client_secret: SecretStr | None = None
    database_url: str = (
        "postgresql+psycopg://cafe_crowd:cafe_crowd_dev@localhost:5432/cafe_crowd"
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
