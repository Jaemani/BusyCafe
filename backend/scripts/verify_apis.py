#!/usr/bin/env python3
"""Make one controlled call per selected API and create Phase 0 fixtures.

Existing fixtures are never overwritten. Run from ``backend`` after placing
keys in ``backend/.env`` (or exporting them), for example::

    python scripts/verify_apis.py --service all

Raw JSON is persisted before provisional schema validation. If validation
fails, the raw fixture remains intact and a sibling ``.validation_error.txt``
file records why, so a new upstream schema can be inspected without data loss.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from pydantic import ValidationError

# Permit direct execution without installing the package first.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.clients.kakao_local import KakaoLocalClient, parse_category  # noqa: E402
from app.clients.seoul_citydata import (  # noqa: E402
    SeoulCityDataClient,
    parse_population,
)
from app.config import (  # noqa: E402
    FIXTURES_DIR,
    KAKAO_VERIFY_LAT,
    KAKAO_VERIFY_LNG,
    KAKAO_VERIFY_RADIUS_M,
    SEOUL_VERIFY_AREA_NAME,
    get_settings,
)
from app.schemas import VerificationSummary  # noqa: E402


FIXTURE_FILES = {
    "seoul": FIXTURES_DIR / "citydata_sample.json",
    "kakao": FIXTURES_DIR / "kakao_ce7_sample.json",
}
SUMMARY_FILES = {
    "seoul": FIXTURES_DIR / "citydata_sample.summary.json",
    "kakao": FIXTURES_DIR / "kakao_ce7_sample.summary.json",
}
VALIDATION_ERROR_FILES = {
    service: fixture.with_suffix(".validation_error.txt")
    for service, fixture in FIXTURE_FILES.items()
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--service",
        choices=("all", "seoul", "kakao"),
        default="all",
        help="API to verify (default: all)",
    )
    return parser.parse_args()


def _selected_services(choice: str) -> list[str]:
    return ["seoul", "kakao"] if choice == "all" else [choice]


def _preflight(services: list[str]) -> dict[str, str]:
    settings = get_settings()
    secrets = {
        "seoul": settings.seoul_api_key,
        "kakao": settings.kakao_rest_key,
    }
    missing = [
        service
        for service in services
        if secrets[service] is None
        or not secrets[service].get_secret_value().strip()
    ]
    if missing:
        env_names = {"seoul": "SEOUL_API_KEY", "kakao": "KAKAO_REST_KEY"}
        required = ", ".join(env_names[name] for name in missing)
        raise RuntimeError(f"missing required environment variable(s): {required}")

    output_paths = {
        path
        for name in services
        for path in (
            FIXTURE_FILES[name],
            SUMMARY_FILES[name],
            VALIDATION_ERROR_FILES[name],
        )
    }
    collisions = [str(path) for path in output_paths if path.exists()]
    if collisions:
        joined = "\n  - ".join(collisions)
        raise RuntimeError(
            "refusing to overwrite existing verification output:\n"
            f"  - {joined}\nMove or review the existing files before retrying."
        )
    return {
        name: secrets[name].get_secret_value()  # type: ignore[union-attr]
        for name in services
    }


def _atomic_create_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # The exclusive final create is the overwrite guard; the temp file avoids a
    # partial fixture if serialization or disk writes fail.
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as temporary:
        json.dump(payload, temporary, ensure_ascii=False, indent=2)
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    try:
        with temporary_path.open("rb") as source, path.open("xb") as target:
            target.write(source.read())
    finally:
        temporary_path.unlink(missing_ok=True)


def _atomic_create_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as destination:
        destination.write(content.rstrip())
        destination.write("\n")


def _fetch_service(service: str, key: str) -> dict[str, Any]:
    if service == "seoul":
        return SeoulCityDataClient(key).fetch_population_raw(
            SEOUL_VERIFY_AREA_NAME
        )
    if service == "kakao":
        with KakaoLocalClient(key) as client:
            return client.search_category_raw(
                longitude=KAKAO_VERIFY_LNG,
                latitude=KAKAO_VERIFY_LAT,
                radius_m=KAKAO_VERIFY_RADIUS_M,
            )
    raise ValueError(f"unsupported service: {service}")


def _summary(payloads: dict[str, dict[str, Any]]) -> VerificationSummary:
    summary = VerificationSummary(
        generated_at=datetime.now(UTC), services=sorted(payloads)
    )
    if seoul_payload := payloads.get("seoul"):
        area = parse_population(seoul_payload)
        summary.seoul_area_name = area.area_name
        summary.seoul_area_code = area.area_code
        summary.observed_seoul_labels = sorted(
            {area.congestion_level}
            | {forecast.congestion_level for forecast in area.forecast}
        )
    if kakao_payload := payloads.get("kakao"):
        summary.kakao_result_count = len(parse_category(kakao_payload).documents)
    return summary


def main() -> int:
    args = _parse_args()
    services = _selected_services(args.service)
    try:
        keys = _preflight(services)
        validation_failures: list[tuple[str, Exception]] = []
        for service in services:
            payload = _fetch_service(service, keys[service])
            # The raw response is the Phase 0 source of truth. Save it before
            # attempting any provisional Pydantic interpretation.
            _atomic_create_json(FIXTURE_FILES[service], payload)
            try:
                summary = _summary({service: payload})
                _atomic_create_json(
                    SUMMARY_FILES[service], summary.model_dump(mode="json")
                )
            except Exception as exc:
                _atomic_create_text(VALIDATION_ERROR_FILES[service], str(exc))
                validation_failures.append((service, exc))
    except (RuntimeError, ValidationError, ValueError) as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # network/httpx errors need a concise CLI failure
        print(f"verification failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 1

    if validation_failures:
        for service, exc in validation_failures:
            print(
                f"{service}: raw fixture saved, but provisional schema validation "
                f"failed ({type(exc).__name__}). See "
                f"{VALIDATION_ERROR_FILES[service]}",
                file=sys.stderr,
            )
        return 3

    print("verification succeeded; created:")
    for service in services:
        print(f"  - {FIXTURE_FILES[service]}")
        print(f"  - {SUMMARY_FILES[service]}")
    if "seoul" in services:
        print(
            "Observed labels are only evidence from this sample; confirm all four "
            "labels before removing [VERIFY]."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
