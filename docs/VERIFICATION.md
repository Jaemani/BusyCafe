# Verification Log

실측 검증 결과, 계획과 실제의 차이, Phase별 DoD 통과 여부를 누적 기록한다. 인증키와 개인정보는 기록하지 않는다.

## 상태 요약

| Phase | 상태 | 완료일 | 근거 |
|---|---|---|---|
| Phase 0 | 완료 | 2026-07-11 | 실 API/스키마/라벨/호출 제한/마스터 검증 및 fixture 커밋 |
| Phase 1 | 진행 중 | - | Phase 0 DoD 통과 |
| Phase 2 | 대기 | - | Phase 1 미완료 |
| Phase 3 | 대기 | - | Phase 2 미완료 |
| Phase 4 | 대기 | - | Phase 3 미완료 |
| Phase 5 | 대기 | - | Phase 4 미완료 |
| Phase 6 | 대기 | - | Phase 5 미완료 |

## 기록 템플릿

아래 블록을 복사해 Phase 또는 개별 검증 단위로 추가한다.

```md
## YYYY-MM-DD — Phase N / 검증 제목

- 실행 환경:
- 검증자:
- 관련 커밋:
- 입력/fixture:
- 실행 명령:
- 기대 결과:
- 실제 결과:
- 판정: PASS / FAIL / BLOCKED
- 계획과의 차이:
- 후속 조치:
- 관련 결정/인시던트:

### DoD

- [ ] 항목
```

## Phase 0 미확정 항목

없음. Phase 1에서 대표점 산출 후 실제 폴링 대상 목록을 확정한다.

## 2026-07-11 — Phase 0 / 첫 실 API 호출

- 실행 환경: 로컬 `.env`, 키 값은 출력·기록하지 않음
- 검증자: Codex
- 관련 커밋: `c641095`
- 입력/fixture: 서울 `광화문광장`, 카카오 광화문 인근 CE7 반경 1,000m
- 실행 명령: `rtk uv run python scripts/verify_apis.py --service all`
- 기대 결과: 서울·카카오 원본 fixture 각각 1개 저장
- 실제 결과: 첫 호출에서 서울 HTTP 정상 및 `citydata_sample.json` 저장. 카카오는 Map/Local 활성화 전 403을 반환했으나, 사용자 활성화 후 재호출하여 `kakao_ce7_sample.json`과 summary 저장
- 판정: PASS (두 API 원본 확보); Phase 0의 쿼터 확인은 계속 진행 중
- 계획과의 차이: 서울 응답은 예상한 `LIVE_PPLTN_STTS` 중첩이 아니라 `SeoulRtd.citydata_ppltn[]`의 평면 레코드. root 성공 결과도 `RESULT.CODE`/`RESULT.MESSAGE` 형태의 키 사용
- 후속 조치: 두 실측 모델의 fixture 기반 회귀 테스트 유지. 포털 일일 쿼터 확인
- 관련 결정/인시던트: `docs/INCIDENTS.md`의 INC-2026-001
- 회귀 검증: 실측 fixture 기반 backend 13 tests passed, Python compileall, TypeScript typecheck, Vite production build 통과

### 확인된 서울 값

- endpoint/service: `citydata_ppltn`, AREA_NM 경로 호출
- area: `광화문광장` / `POI088`
- 현재 및 12개 forecast에서 관측한 라벨: `여유`, `보통`
- forecast item 키: `FCST_TIME`, `FCST_CONGEST_LVL`, `FCST_PPLTN_MIN`, `FCST_PPLTN_MAX`
- 호출 정책: 1회 최대 1,000건, 호출 횟수 제한 없음. 장소당 1콜인 본 API에는 행 제한 영향 없음
- 확정 폴링 정책: 대상 ≤12곳, 10분 주기, 최대 1,728콜/일
- 공식 OA-21285 설명에서 장소당 1콜·일괄 호출 불가와 장소명/코드 호출을 확인

### 확인된 카카오 값

- Map/Local 제품 활성화 전 REST Local API는 `OPEN_MAP_AND_LOCAL` 403 반환
- CE7 반경 1,000m: `total_count=761`, `pageable_count=45`, page 1 문서 15개
- size 15의 page 3: 문서 15개, `is_end=true`
- 응답 root와 document 필드는 `docs/PLAN.md` §2.2에 반영

### MVP 중심 핫스팟 제어 호출

쿼터 미확정 상태이므로 세 후보를 각 1회만 호출했으며, 응답은 실측 서울 parser로
검증했다. 광범위한 장소 탐색은 수행하지 않았다.

- `성수카페거리` → `POI068`, `약간 붐빔`, `2026-07-11 16:30`
- `홍대 관광특구` → `POI007`, `붐빔`, `2026-07-11 16:30`
- `연남동` → `POI073`, `약간 붐빔`, `2026-07-11 16:30`
- 광화문 fixture의 현재/forecast `보통`, `여유`와 합쳐 라벨 4종을 모두 실측 확인

### 현재 Phase 0 DoD

- [x] 서울 원본 fixture 저장 및 실측 parser 회귀 테스트
- [x] 카카오 CE7 원본 fixture 저장 및 실측 parser 회귀 테스트
- [x] 혼잡도 라벨 4종 실 API 확인
- [x] 호출 제한과 폴링 주기 확정
- [x] 121개 장소 목록/영역 원본 확보, 컬럼·포맷·좌표계 확인

### 호출 제한 확인

- 확인자: 사용자(HUMAN), 서울 열린데이터광장 안내 문구
- 안내: OpenAPI 1회 호출 최대 1,000건, 1,000건 초과 시 분할 호출, 호출 횟수 제한 없음
- 해석: 장소 1곳당 1콜인 `citydata_ppltn`에는 행 제한 영향 없음
- 결정: `POLL_INTERVAL_MIN=10`, 폴링 대상 최대 12곳, 예상 최대 1,728콜/일
- 판정: PASS — Phase 0 DoD 완료

### 공식 OA-21285 첨부 확인

- 관련 커밋: `b860b3e`
- 페이지: `https://data.seoul.go.kr/dataList/OA-21285/A/1/datasetView.do`
- 목록: `서울시 주요 121장소 목록.xlsx`, seq 23, 2026-04-02
- 영역: `서울시 주요 121장소 영역.zip`, seq 24, 2026-04-02
- POST 다운로드 endpoint의 HTTP 200, Content-Disposition, Content-Length를 확인
- 재현 가능한 `scripts/download_hotspot_master.py`로 두 원본 다운로드 완료
- XLSX: 121 records, `CATEGORY/NO/AREA_CD/AREA_NM/ENG_NM`, 5 categories
- 영역 ZIP: 121-record Shapefile, DBF `AREA_CD/CATEGORY/AREA_NM`, UTF-8, WGS84
- XLSX와 Shapefile DBF의 `AREA_CD` 집합은 각각 121개이며 누락·추가 없이 완전히 일치
- SHA-256 XLSX: `60aedf332efef1535623e22c14af2acd6b3ccfa35e60423fbbea8cc8188f1ff7`
- SHA-256 ZIP: `fda69cd2ee3812103931cfd0ef1a0146336f06a23b6e1c2e4f9e0653620262ac`
- 계획과의 차이: 목록에 중심 좌표가 없고 별도 폴리곤으로 제공됨. Phase 1은 공식 폴리곤의 내부 대표점을 사용하도록 변경
- 관련 결정: `docs/adr/ADR-0002-hotspot-location.md`

## 2026-07-11 — Phase 0 / 기본 저장소 설계 변경

- 실행 환경: 설계 검토(실 API 호출 없음)
- 검증자: Codex
- 관련 커밋: `f1be918`
- 입력/fixture: `docs/PLAN.md`의 관계, 시계열, bbox, IDW 요구사항
- 기대 결과: 사용자 증가와 공간 질의를 감당하면서 데이터 정합성을 유지할 저장소 선택
- 실제 결과: PostgreSQL을 운영·통합테스트 기준으로 채택하고 PostGIS를 선택적으로 활성화하기로 결정
- 판정: PASS (설계 결정), API `[VERIFY]` 항목에는 영향 없음
- 계획과의 차이: 기존 SQLite 운영 가정을 PostgreSQL로 변경. 프로세스 내 스케줄러는 개발 단일 인스턴스에만 허용하고 운영에서는 ingest worker로 분리
- 후속 조치: SQLAlchemy/Alembic 기반 스키마, PostgreSQL CI, 운영 전 백업·복구 확인
- 관련 결정/인시던트: `docs/adr/ADR-0001-primary-database.md`

## 2026-07-11 — Phase 0 / DDL 내부 불일치 정정

- 실행 환경: 계획서 정적 검토(실 API 호출 없음)
- 검증자: Codex
- 관련 커밋: `f1be918`
- 기대 결과: D4와 §4.5의 uncovered NULL 규칙이 DDL과 일치
- 실제 결과: 초기 DDL은 `score`, `level`, `confidence`를 `NOT NULL`로 선언해 규칙과 충돌
- 판정: PASS (문서 정정)
- 계획과의 차이: uncovered인 경우 `score`, `level`, `confidence`, `confidence_tier`를 NULL로 명시. PostgreSQL 채택에 맞춰 시각 컬럼을 timezone-aware `TIMESTAMPTZ`로 변경
- 후속 조치: SQLAlchemy 모델과 API 응답 스키마에서 nullable 규칙을 동일하게 유지

## 2026-07-11 — Phase 0 / 키 없이 가능한 스캐폴딩 검증

- 실행 환경: macOS, Python 3.14.6(프로젝트 기준 3.12+), Node.js 22.23.0
- 검증자: Codex
- 관련 커밋: `e2c044d`
- 입력/fixture: 실측 fixture 없음, in-memory HTTP transport만 사용
- 실행 명령: `rtk uv run pytest`, `rtk uv run python -m compileall -q app scripts tests`, `rtk npm run typecheck`, `rtk npm run build`
- 기대 결과: 외부 실호출 없이 검증 도구 안전장치와 양쪽 스캐폴딩이 통과
- 실제 결과: backend 9 tests passed, Python compileall 통과, TypeScript typecheck 및 Vite production build 통과
- 판정: PASS (부분); Phase 0 전체는 `[HUMAN]` 키와 실측이 없어 BLOCKED
- 계획과의 차이: upstream 스키마 성공 테스트는 합성 응답으로 고정하지 않고 실측 fixture 확보 뒤 추가. provisional 모델은 raw 저장 이후에만 실행
- 후속 조치: 키 준비 후 `rtk uv run python scripts/verify_apis.py --service all`, 121개 장소 마스터와 포털 쿼터 수동 확인
- 관련 결정/인시던트: `docs/INCIDENTS.md`의 INC-2026-001

### 추가 환경 확인

- 키 미설정 상태에서 검증 스크립트는 필요한 환경 변수 이름만 출력하고 exit 2로 안전 종료했다.
- 현재 머신에는 Docker CLI가 없어 `compose.yaml` 런타임 검증은 실행하지 못했다. PostgreSQL은 Phase 0 API 실측 도구의 선행 조건이 아니다.
