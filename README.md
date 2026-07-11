# cafe-crowd

서울 실시간 도시데이터의 지역 혼잡도를 카페 위치에 공간 매핑해, 지금 주변에서 상대적으로 한산할 가능성이 높은 카페를 근거와 함께 보여주는 지도 서비스입니다.

Phase 0 실측 검증을 완료했고 Phase 1 인제스트 파이프라인을 구현 중입니다.

## 문서

- [`docs/PLAN.md`](docs/PLAN.md): 제품 및 구현 계획의 source of truth
- [`docs/ROADMAP.md`](docs/ROADMAP.md): 고도화 트랙의 우선순위와 공통 확장 로드맵
- [`Track 1 — 정확도`](docs/tracks/TRACK-1-ACCURACY.md): 엔진 신뢰도와 정확도 개선
- [`Track 2 — 국내 확장`](docs/tracks/TRACK-2-KOREA.md): 서울에서 국내 도시로 확장
- [`Track 3 — 해외 확장`](docs/tracks/TRACK-3-GLOBAL.md): 유니버설 구조와 해외 도시 확장
- [`docs/VERIFICATION.md`](docs/VERIFICATION.md): Phase별 실측 결과와 DoD 기록
- [`docs/DECISIONS.md`](docs/DECISIONS.md): 주요 기술·제품 의사결정 기록(ADR)
- [`docs/CHANGELOG.md`](docs/CHANGELOG.md): 사용자 관점의 앱 변경 내역
- [`docs/INCIDENTS.md`](docs/INCIDENTS.md): 중요한 장애와 실수, 재발 방지 조치

## 개발 원칙

- 추정치를 실제 매장 점유율처럼 표현하지 않습니다.
- 계획과 실측 결과가 충돌하면 먼저 `docs/PLAN.md`를 고치고 `docs/VERIFICATION.md`에 근거를 남깁니다.
- 비밀값은 `.env`에만 두고 Git에 커밋하지 않습니다.
- 외부 API를 사용하는 테스트는 금지하고, 검증 과정에서 저장한 fixture로 테스트합니다.
- 앱 업데이트, 주요 의사결정, 중요한 실수와 후속 조치를 문서화하고 Git으로 버전 관리합니다.

## 현재 상태

MapLibre/OpenFreeMap 지도, Overture 고신뢰 서울 카페 4,933건 cache, 공식 121개
핫스팟 seed, IDW score materialize와 FastAPI bbox API가 연결됐습니다. 잘못된 OSM
타일 POI와 Kakao runtime 의존은 제거했습니다. 공개 URL은 읽기 전용 스냅샷이며,
실시간 production 전환 경로와 운영 제약은
[`ADR-0005`](docs/adr/ADR-0005-live-production-runtime.md)에 기록합니다.

## 로컬 준비

백엔드는 Python 3.12+와 `uv`, 프론트엔드는 Node.js 22+를 기준으로 합니다.

```bash
cd backend
cp .env.example .env
rtk uv sync --extra dev
rtk uv run pytest
```

```bash
cd frontend
cp .env.example .env
rtk npm install
rtk npm run build
rtk npm run dev
```

프론트 개발 서버는 다른 로컬 서비스와 충돌하지 않도록
`http://127.0.0.1:5188`을 고정 사용하며, 포트가 이미 사용 중이면 즉시 실패합니다.

로컬 PostgreSQL은 Docker가 설치된 환경에서 `rtk docker compose up -d postgres`로
시작할 수 있습니다.

## API 키

제품 실행에 필요한 외부 비밀값은 `backend/.env`의 `SEOUL_API_KEY`입니다. Kakao
REST/JavaScript 키는 현 제품 지도·POI 경로에서 사용하지 않습니다. 키는 채팅,
문서, 이슈, 커밋과 HTTP 요청 로그에 남기지 마세요.

실측 fixture와 공식 마스터 원본은 이미 커밋되어 있습니다. 갱신할 때는 기존 원본을
검토·이동한 뒤 검증/다운로드 스크립트를 명시적으로 실행해야 하며 자동 덮어쓰기는
허용되지 않습니다.

## 데이터베이스와 인제스트

```bash
cd backend
rtk uv run alembic -c alembic.ini upgrade head
rtk uv run python scripts/seed_hotspots.py
```

`seed_hotspots.py`의 기본 동작은 dry-run입니다. 출력된 대상 목록을 사람이 확인한
후에만 다음 명령으로 적용합니다.

```bash
rtk uv run python scripts/seed_hotspots.py --apply
rtk uv run python scripts/seed_cafes.py --download --download-only
rtk uv run python scripts/seed_cafes.py --apply
rtk uv run python scripts/materialize_scores.py
rtk uv run python -m app.ingest.worker --once
rtk uv run python -m app.ingest.worker
```

마지막 명령은 API 서버와 분리된 단일 worker를 실행하며 10분마다 폴링합니다.
응답의 장소 코드·이름이 요청 대상과 다르거나 스키마 파싱이 실패하면 snapshot을
만들지 않고 원본을 `hotspot_parse_failures`에 보존합니다.

인증키 값은 문서나 이슈에 붙여 넣지 마세요.

개발 미리보기 API는 `8190`, Vite는 `5188`을 사용하며 Vite가 same-origin `/api`를
프록시합니다. 기존 로컬 서비스나 Tailscale 443 Funnel 설정은 변경하지 않습니다.

```bash
rtk proxy env DATABASE_URL=sqlite+pysqlite:///data/preview.db uv run uvicorn app.main:app --host 127.0.0.1 --port 8190
```
