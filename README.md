# cafe-crowd

서울 실시간 도시데이터의 지역 혼잡도를 카페 위치에 공간 매핑해, 지금 주변에서 상대적으로 한산할 가능성이 높은 카페를 근거와 함께 보여주는 지도 서비스입니다.

현재는 Phase 0 준비 단계입니다. 외부 API의 엔드포인트, 응답 필드, 장소명, 쿼터 등은 아직 확정하지 않았으며 실제 응답을 저장한 fixture를 기준으로 검증할 예정입니다.

## 문서

- [`docs/PLAN.md`](docs/PLAN.md): 제품 및 구현 계획의 source of truth
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

Phase 0의 키 없이 가능한 스캐폴딩과 검증 도구까지 준비됐습니다. 실제 fixture,
쿼터 확인, 121개 장소 마스터 확보는 API 키 발급 후 진행합니다. Phase 0 DoD가
끝나기 전에는 Phase 1 인제스트 구현으로 넘어가지 않습니다.

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
```

로컬 PostgreSQL은 Docker가 설치된 환경에서 `rtk docker compose up -d postgres`로
시작할 수 있습니다. 현재 Phase 0 검증 도구 자체는 데이터베이스를 요구하지 않습니다.

## API 키 준비 후

발급받은 `SEOUL_API_KEY`, `KAKAO_REST_KEY`는 `backend/.env`에만 넣고 채팅,
문서, 이슈, 커밋에는 붙여 넣지 마세요. 카카오 JavaScript 키는 백엔드에 필요
없으며 `frontend/.env`의 `VITE_KAKAO_JS_KEY`에만 넣습니다.

```bash
cd backend
rtk uv run python scripts/verify_apis.py --service all
rtk uv run python scripts/download_hotspot_master.py --file all
```

스크립트는 원본 JSON을 provisional 스키마 검증보다 먼저 저장하고 기존 결과를
덮어쓰지 않습니다. 스키마가 다르면 원본은 유지한 채 `.validation_error.txt`를
생성하므로, 그 증거를 바탕으로 계획과 모델을 함께 갱신합니다.

인증키 값은 문서나 이슈에 붙여 넣지 마세요.
