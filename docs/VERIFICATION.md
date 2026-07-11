# Verification Log

실측 검증 결과, 계획과 실제의 차이, Phase별 DoD 통과 여부를 누적 기록한다. 인증키와 개인정보는 기록하지 않는다.

## 상태 요약

| Phase | 상태 | 완료일 | 근거 |
|---|---|---|---|
| Phase 0 | 완료 | 2026-07-11 | 실 API/스키마/라벨/호출 제한/마스터 검증 및 fixture 커밋 |
| Phase 1 | 진행 중 (121개 범위 재검증) | - | 공식 master 검증 완료. 기존 10개 대상 검증은 legacy이며 121개 seed apply·1시간 무인 구동·full-cycle 측정 전 |
| Phase 2 | 진행 중 (catalog cache 설계/구현) | - | Overture release cache·인허가 보정·직접 상세 링크 검증 전 |
| Phase 3 | 병렬 구현 완료·gate 대기 | - | 순수 IDW/신뢰도/materialize 및 테스트 완료, Phase 2 HUMAN 품질 gate 대기 |
| Phase 4 | 병렬 구현 완료·gate 대기 | - | cache-only 4 endpoint·CORS·bbox p95 검증 완료, Phase 3 gate 대기 |
| Phase 5 | 진행 중 (MapLibre UI) | - | MapLibre/OpenFreeMap·내 위치·cached API 연결 완료, 브라우저 수동 시나리오/stale banner 대기 |
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

없음. 대표점 산출은 완료됐고, 현행 폴링 대상은 공식 master 121개 전부로 ADR-0004에서 확정했다.

## 현행 제품 범위와 legacy 기록 해석

2026-07-11에 채택한 [ADR-0003](adr/ADR-0003-maplibre-overture-catalog.md)와
[ADR-0004](adr/ADR-0004-seoulwide-polling-cache.md)가 현행 제품 경로다.

- 지도: MapLibre GL + OpenFreeMap. Kakao Maps SDK는 제품 런타임 의존성이 아니다.
- 카페: Overture Places의 versioned 서울 cache와 인허가 보정. viewport 요청에서 외부
  POI API를 호출하지 않는다.
- 혼잡도: 공식 121개 장소 전부를 10분마다 폴링한다. 목표량은 17,424콜/일이다.
- 외부 매장 링크: 검증된 provider ID/canonical direct detail URL이 있을 때만 보인다.
  이름·좌표 검색 링크나 추측 매칭은 금지한다.

아래의 `≤12곳`, `10곳`, `1,440/1,728콜/일`, Kakao 지도/CE7 seed 관련 기록은 당시
실제 호출·코드·인시던트를 보존하는 **legacy 역사 기록**이다. 삭제하거나 실측 사실을
바꾸지 않되, 현행 폴링 범위·제품 POI 경로로 해석하지 않는다.

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
- 안전 절차: seed CLI는 기본 dry-run이며 `--apply`에서만 write. 공식 121개 외 DB 코드는 자동 삭제하지 않고 전체 transaction을 rollback

## 2026-07-11 — Phase 1 / 공식 영역 대표점 및 폴링 대상 검증

- 입력: 커밋된 121장소 XLSX와 WGS84 Shapefile
- 기대 결과: 121개 metadata/geometry 일대일 결합, 유효한 서울 내부 대표점, 폴링 대상 ≤12
- 실제 결과: 121개 코드·명칭·분류 일치. `POI070` 원본 polygon에서 self-intersection 발견
- 처리: 원본 fixture는 변경하지 않고 Shapely `make_valid`로 topology 정규화 후 내부 대표점 산출
- 폴링 대상: TARGET_NEIGHBORHOODS 반경 합집합 10곳
  - `POI007` 홍대 관광특구
  - `POI015` 건대입구역
  - `POI025` 뚝섬역
  - `POI040` 신촌·이대역
  - `POI053` 합정역
  - `POI055` 홍대입구역(2호선)
  - `POI068` 성수카페거리
  - `POI073` 연남동
  - `POI101` 서울숲공원
  - `POI122` 신촌 스타광장
- 판정: PASS (코드/대표점/≤12); HUMAN 목록 검수와 DB apply는 진행 중
- 관련 결정: `docs/adr/ADR-0002-hotspot-location.md`
- 관련 커밋: `5e78cb8`

## 2026-07-11 — Phase 1 / DB nullable 규칙 명확화

- 입력: D4 uncovered 규칙과 PostgreSQL 초기 migration
- 결정: uncovered는 score/level/confidence뿐 아니라 tier, primary hotspot/distance, contributors도 모두 NULL
- 근거: 데이터가 없는데 evidence 또는 신뢰도 등급만 남는 모순 상태를 DB CHECK constraint로 차단
- 판정: PASS (모델·migration·SQLite constraint tests), PostgreSQL runtime 검증 대기
- 관련 커밋: `781e8a8`

## 2026-07-11 — Phase 1 / 인제스트 통합 검증

- 관련 커밋: DB `781e8a8`, seed `5e78cb8`, worker `fbc7086`
- 테스트: backend 59 passed, 경고 없음; Python compileall; PostgreSQL offline Alembic SQL 생성; frontend typecheck/build
- SQLite migration: `/tmp/BusyCafe-phase1-review.sqlite`에 initial upgrade 성공
- seed 기본 실행: dry-run, source 121, would insert 121, polling targets 10/12
- 안전 확인: dry-run 후 DB `hotspots=0`
- 스냅샷 보호: 응답 AREA_CD와 AREA_NM을 요청 대상과 모두 대조하고 불일치 시 저장 차단
- 파싱 실패: 재호출하지 않고 안전한 고정 오류 요약과 raw JSON을 `hotspot_parse_failures`에 append
- fetch 실패: 최초 호출 후 최대 3회 지수 backoff, 최종 실패 후 다음 대상 진행
- 호출량: 대상 10곳 × 144회 = 1,440콜/일, 확인된 호출 횟수 무제한 정책 내

### Phase 1 현재 DoD

- [x] 공식 121개 metadata/geometry 결정적 결합 및 대표점 검증
- [x] 폴링 대상 10곳(≤12) 산출
- [x] 잘못된 응답/대상 불일치/저장 실패 격리 fixture 테스트
- [x] 예상 일일 호출량 1,440회와 호출 정책 확인
- [ ] HUMAN 폴링 대상 목록 확인 후 `--apply`
- [ ] 1시간 무인 구동, 대상별 snapshot ≥5 확인
- [ ] PostgreSQL runtime migration/seed 검증(Docker 또는 PostgreSQL 필요)

### 개발 미리보기 네트워크 확인

- 로컬: `http://127.0.0.1:5188`, BusyCafe 고유 응답 확인
- Tailscale Serve: `https://jaemans-mac-studio.tail2743ae.ts.net:8443/`
- 공개 범위: tailnet only; 기존 443 Funnel(`/` → `127.0.0.1:8012`)은 변경하지 않음
- Vite allowed host: 현재 tailnet suffix `.tail2743ae.ts.net`만 허용
- Kakao SDK Referer 검증: 최초 401 `domain mismatched` 확인 후 사용자가 Web 플랫폼 도메인을 등록했고, localhost/127.0.0.1:5188 및 tailnet:8443 모두 HTTP 200 `text/javascript` 확인
- Kakao CE7 재검증: REST category search 정상, 첫 페이지 15건/total 761
- 지도 구현 검증: TypeScript typecheck, Vite production build, tailnet HTML과 SDK 인증 통과. 브라우저 상호작용 수동 확인 대상

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

## 2026-07-11 — 제품 경로 재기준선 / MapLibre·Overture·121개 polling

- 실행 환경: 제품 범위 및 구현 diff 검토. 외부 API 추가 호출 없음.
- 검증자: Codex
- 관련 결정: `ADR-0003`, `ADR-0004`, `PLAN.md` v1.4
- 입력: 사용자 요구(서울 전역 지도 이동, 부정확한 장소명·위치 개선, 서버 cache,
  실제 매장 상세만 연결), Phase 0 서울 OpenAPI 호출 정책, 기존 Kakao fixture/인시던트
- 실제 결과:
  - 지도/POI 제품 경로는 MapLibre GL + OpenFreeMap과 Overture Places versioned cache로
    변경했다. Kakao Local/Maps는 legacy fixture·도메인 활성화 기록으로만 보존한다.
  - 외부 장소 provider는 지도 요청에서 호출하지 않는다. P2의 catalog job과 P4의 cache-only
    API가 책임을 분리한다.
  - 공식 121개 master 전체가 10분 polling 대상이다. 계획상 17,424콜/일이며, 이전
    10개/1,440콜 검증은 실행 증거로 남지만 현행 범위 검증을 충족하지 않는다.
  - Naver/Kakao/Google은 provider-specific ID/canonical detail URL이 검증된 경우에만
    링크를 보인다. 검색 링크·스크레이핑·추측 ID 매칭은 제품 규칙에서 제외했다.
- 판정: PASS (범위·문서·ADR 정렬); Phase 1 full-cycle, Phase 2 catalog ingest/direct-link,
  Phase 4 API cache, Phase 5 브라우저 상호작용은 아직 별도 실행 검증이 필요하다.
- 계획과의 차이: 예전 `≤12` polling, Kakao CE7 재귀 seed, Kakao Maps SDK 의존은
  legacy로 대체됐다.
- 후속 조치:
  - [ ] 121개 seed dry-run/apply와 full cycle 10분 측정
  - [ ] Overture release/hash·서울 분류/좌표·50곳 표본 검수
  - [ ] 인허가 보정과 partial-release rollback/atomicity 검증
  - [ ] provider direct detail URL allowlist와 ID-missing UI 테스트
  - [ ] MapLibre/OpenFreeMap attribution, 내 위치 권한 허용/거부, cached API 연결 수동 확인

## 2026-07-11 — Overture cache·API·부분 실시간 materialize

- Overture release: `2026-06-17.0`
- 필터: 서울 guard bbox, `cafe/coffee_shop/bubble_tea/tea_room/coffee_roastery`, confidence ≥ 0.80
- 로컬 immutable extract: 4,933건, 420.9KB
- SHA-256: `5115e468e6ea34a4859fb9391914a5a9c82c9c2e99d7ba09c8fe8b3d7d8d184e`
- DB seed: hotspots 121/121 `is_polled=true`, cafes 4,933 active, 동일 release 멱등 테스트 PASS
- 실시간 순회: HTTP 로그 키 노출 발견으로 64/121 저장 후 즉시 중단. 추가 호출은 새 키 발급 전 금지
- 오프라인 score materialize: covered 1,803 / fringe 1,560 / uncovered 1,570
- API: `/api/health`와 bbox `/api/cafes`를 로컬 5188 proxy 및 tailnet 8443에서 HTTP 200 확인
- bbox 522건 응답 30회 로컬 측정: p50 18.5ms, p95 31.6ms, max 36.6ms
- 보안: 검색 URL과 비허용 host/path는 외부 상세 링크 응답에서 제거; provider ID가 없으면 버튼 숨김
- 테스트: backend 87 passed(당시), frontend typecheck/build PASS
- 판정: cache/API/P3 순수함수·materialize PASS. Phase 1 full cycle은 키 교체 후 재검증 필요
- 관련 인시던트: `INC-2026-005`, `INC-2026-006`
