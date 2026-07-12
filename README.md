# BusyCafe

서울시의 지역 혼잡도를 카페 위치에 매핑해, 지금 주변에서 상대적으로 한산할 가능성이
높은 카페를 근거와 함께 보여주는 지도 서비스입니다. 표시되는 값은 매장 좌석 점유율이
아니라 **카페 주변 지역의 혼잡도 추정치**입니다.

**공개 프리뷰:** https://busy-cafe.vercel.app

> 현재 공개 프리뷰는 배포 시점의 읽기 전용 SQLite 스냅샷입니다. 로컬 수집기의 10분
> 갱신이 공개 URL에 자동 반영되지는 않습니다. 관리형 PostgreSQL과 운영 worker 전환이
> 끝나기 전에는 실시간 서비스로 간주하지 않습니다.

## 현재 제공하는 기능

- MapLibre/OpenFreeMap 기반 서울 지도와 카페 클러스터
- Overture Places에서 선별한 서울 카페 원장
- 서울시 주요 장소 121개의 혼잡도 수집 파이프라인
- 거리, 데이터 신선도와 기여 핫스팟 수를 반영한 결정적 IDW 점수
- 카페별 주변 혼잡도, coverage, 신뢰도와 근거 핫스팟 표시
- 지도 영역 조회 API와 상태 확인 API

현재 신뢰도는 실제 적중 확률이 아니라 **입력 근거의 충분도**입니다. 지역 보행 혼잡과
카페 좌석 여유의 상관은 아직 현장 관측으로 검증되지 않았습니다.

## 로컬 Quickstart

Python 3.12+, [uv](https://docs.astral.sh/uv/), Node.js 22+와 Docker가 필요합니다.
저장소를 clone한 뒤 루트에서 다음 세 명령을 각각 실행합니다. 두 번째와 세 번째 명령은
서버가 계속 실행되므로 별도 터미널을 사용합니다.

```bash
docker compose up -d postgres
```

```bash
cd backend && cp .env.example .env && uv sync --extra dev && uv run alembic -c alembic.ini upgrade head && uv run uvicorn app.main:app --host 127.0.0.1 --port 8190
```

```bash
cd frontend && cp .env.example .env && npm ci && npm run dev
```

브라우저에서 `http://127.0.0.1:5188`을 엽니다. 새 로컬 DB는 비어 있으므로 지도와 API
동작만 확인할 수 있습니다. 실제 데이터를 수집하려면 `backend/.env`에 발급받은
`SEOUL_API_KEY`를 넣고 아래 데이터 적재 절차를 따르세요. 비밀값은 채팅, 이슈, 문서,
커밋 또는 HTTP 로그에 남기지 않습니다.

기본 검증 명령은 다음과 같습니다. 테스트는 저장된 fixture만 사용하며 외부 API를
호출하지 않습니다.

```bash
cd backend
uv run pytest
uv run python -m compileall -q app scripts tests

cd ../frontend
npm run typecheck
npm run build
```

## 아키텍처

```text
서울 실시간 도시데이터 ──> 분리 ingest worker ──> snapshot 저장소
Overture 카페 원장 ──────────────────────────────> PostgreSQL/SQLite
                                                   │
브라우저(MapLibre) <── FastAPI cache-only API <────┘
```

요청 경로에서는 서울시나 POI 제공자를 직접 호출하지 않습니다. 수집과 제공을 분리하고,
스냅샷과 모델 버전을 저장해 같은 입력에는 같은 결과가 나오도록 합니다. 공개 Vercel
프리뷰는 이 구조의 읽기 전용 스냅샷 경로만 사용합니다.

## 데이터 적재와 실행

핫스팟 seed는 기본적으로 dry-run이며, 출력된 대상을 사람이 확인한 뒤에만 `--apply`를
사용합니다.

```bash
cd backend
uv run python scripts/seed_hotspots.py
uv run python scripts/seed_hotspots.py --apply
uv run python scripts/seed_cafes.py --download --download-only
uv run python scripts/seed_cafes.py --apply
uv run python scripts/materialize_scores.py
uv run python -m app.ingest.worker --once
```

지속 수집 worker는 API 서버와 별도 프로세스로 실행합니다.

```bash
cd backend
uv run python -m app.ingest.worker
```

## 주요 문서

- [제품·구현 계획](docs/PLAN.md)
- [실측과 DoD 기록](docs/VERIFICATION.md)
- [변경 이력](docs/CHANGELOG.md)
- [중요 사고와 재발 방지](docs/INCIDENTS.md)
- [설계 결정](docs/DECISIONS.md)
- [데이터 라이선스·출처표시 감사](docs/LICENSE_ATTRIBUTION_AUDIT.md)
- [Production 운영·백업·복구 Runbook](docs/OPERATIONS.md)
- [고도화 로드맵](docs/ROADMAP.md)
- [기여 가이드](CONTRIBUTING.md)

계획과 실측이 충돌하면 실측을 근거로 계획과 검증 기록을 함께 갱신합니다. 사용자 영향은
변경 이력에, 중요한 기술 결정은 ADR에, 실수와 후속 조치는 인시던트 문서에 남깁니다.

## 라이선스

BusyCafe 자체 코드는 [Apache License 2.0](LICENSE)에 따라 배포됩니다. 지도와 카페 원장,
서울시 혼잡도처럼 서비스가 이용하는 외부 데이터에는 각 제공자의 별도 라이선스와
출처표시 조건이 적용됩니다. 자세한 구분은
[데이터 라이선스·출처표시 감사](docs/LICENSE_ATTRIBUTION_AUDIT.md)를 참고하세요.
