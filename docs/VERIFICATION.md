# Verification Log

실측 검증 결과, 계획과 실제의 차이, Phase별 DoD 통과 여부를 누적 기록한다. 인증키와 개인정보는 기록하지 않는다.

## 상태 요약

| Phase | 상태 | 완료일 | 근거 |
|---|---|---|---|
| Phase 0 | 완료 | 2026-07-11 | 실 API/스키마/라벨/호출 제한/마스터 검증 및 fixture 커밋 |
| Phase 1 | 진행 중 (1시간 무인 구동 대기) | - | 121개 seed apply와 full-cycle 121/121 성공 확인. 대상별 5개 snapshot 1시간 검증 전 |
| Phase 2 | 진행 중 (catalog cache 설계/구현) | - | Overture release cache·인허가 보정·직접 상세 링크 검증 전 |
| Phase 3 | 병렬 구현 완료·gate 대기 | - | 순수 IDW/신뢰도/materialize 및 테스트 완료, Phase 2 HUMAN 품질 gate 대기 |
| Phase 4 | 병렬 구현 완료·gate 대기 | - | cache-only 4 endpoint·CORS·bbox p95 검증 완료, Phase 3 gate 대기 |
| Phase 5 | 진행 중 (MapLibre UI) | - | MapLibre/OpenFreeMap·내 위치·cached API 연결 완료, 브라우저 수동 시나리오/stale banner 대기 |
| Phase 6 | 병렬 구현 완료·HUMAN 검수/관측 대기 | - | 이중 라벨 historical evaluator와 24곳 후보 완료. 공개 승격은 Phase 5 gate 및 현장 기준선 대기 |

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

## 2026-07-11 — Phase 1 / 새 서울 API 키로 121개 full cycle 재검증

- 실행 환경: local preview SQLite cache, 단일 ingest worker
- 사전 검증: 새 키로 `광화문광장` 단일 호출 성공. `httpx`/`httpcore` WARNING 고정으로 request URL 로그 미출력 확인
- 실행: `rtk uv run python -m app.ingest.worker --once --database-url sqlite+pysqlite:///data/preview.db`
- 결과: targets=121, saved=121, failed=0. 실행은 10분 SLA보다 충분히 짧은 단일 cycle 안에 완료됐다.
- 저장 확인: 새 cycle의 distinct hotspot 121개, health `last_ingest_at=2026-07-11T10:10:25Z`, 최근 1시간 snapshot 185개(이전 부분 cycle 포함)
- materialize 결과: cafes=4,933; covered=2,317 / fringe=1,523 / uncovered=1,093
- Tailnet 확인: `/api/health`, bbox `/api/cafes` HTTP 200. 홍대 viewport 522곳 모두 score/evidence 갱신 시각을 반환했다.
- 판정: PASS (키 교체·secret-safe logging·full cycle). 1시간 무인 구동과 대상별 5개 snapshot DoD만 남음.
- 관련 인시던트: `INC-2026-006`

## 2026-07-11 — API timestamp timezone 직렬화 보정

- 증상: SQLite preview가 UTC offset을 보존하지 않아 브라우저 패널이 `09:40`을 KST로 해석, 약 30분 지연의 원본 데이터를 572분 전으로 표시
- 원본 확인: citydata `PPLTN_TIME=2026-07-11 18:40` KST, 수집 시각 `19:10` KST
- 수정: API 경계에서 timezone 없는 SQLite datetime을 UTC로 복원해 `2026-07-11T09:40:00Z` 형태로 반환. 패널은 경과 분 숫자를 표시하지 않음
- 검증: bbox API 응답의 `evidence.observed_at`가 `Z` suffix를 포함하는 회귀 테스트와 local/tailnet HTTP 확인 PASS
- 관련 인시던트: `INC-2026-007`

## 2026-07-11 — Vercel 읽기 전용 스냅샷 배포

- 배포 URL: `https://busy-cafe.vercel.app`
- production deployment: `https://busy-cafe-fdx012hxi-jaemanis-projects.vercel.app`가
  Vercel에서 `Ready` 상태임을 확인했다.
- `.vercelignore` 적용 deployment `https://busy-cafe-lpih2yfxk-jaemanis-projects.vercel.app`에서 function bundle이 19.88MB에서 13.26MB로 감소한 것을 확인했다.
- 최신 production `https://busy-cafe-cn3jj4vfe-jaemanis-projects.vercel.app`는 `Ready`이고 function bundle은 13.59MB다. 정확한 `busy-cafe.vercel.app` alias의 health는 `last_ingest_at=2026-07-11T15:55:04.362435Z`, `snapshots_last_hour=242`, `cafes_count=4933`을 반환하며, bbox API는 `model_version=v1-idw-point`를 반환한다.
- rename 검증(2026-07-12): 오타가 있던 Vercel 프로젝트 `budy-cafe`를 `busy-cafe`로
  변경하고 새 production deployment와 alias를 연결했다.
- 초기 수동 alias는 SSO로 HTTP 302를 반환했다. 공개 접근 정책을 복원한 뒤
  `https://busy-cafe.vercel.app/`과 `/api/health`가 인증 리디렉션 없이 HTTP 200임을 확인했다.
- production HTML의 canonical과 `og:url`은 모두 `https://busy-cafe.vercel.app/`이다.
  기존 `budy-cafe.vercel.app` alias는 이전 링크 호환용으로 유지한다.
- 배포 모델: `api/data/preview.db`의 카페 4,933개·저장된 혼잡도 점수를 포함한 읽기 전용
  SQLite 스냅샷이다. 요청 중 외부 API를 호출하지 않으며 서울 API 키도 배포하지 않는다.
- 검증: production에서 `/` HTTP 200, `/api/health` HTTP 200,
  홍대 인근 bbox `/api/cafes` HTTP 200(1,059,073 bytes) 확인.
- UI 배포: Vite 정적 산출물을 동일 함수에서 제공한다. 지도 attribution은 화면 최하단의
  접힌 버튼으로 시작하며, 누르면만 원문 표기를 펼친다.
- 제한: Vercel 서버리스는 지속 10분 worker와 쓰기 가능한 SQLite를 제공하지 않는다.
  따라서 이 URL은 **배포 시점 스냅샷**이며, 현행성은 데이터 출처에 명시한다. 실시간 운영은
  managed PostgreSQL과 별도 ingest worker로 전환할 때에만 약속한다.
- 판정: PASS

## 2026-07-11 — 읽기 API CDN 캐시 정책

- 대상: `GET /api/cafes`, `/api/hotspots`, `/api/health`
- 정책: `public, max-age=30, s-maxage=60, stale-while-revalidate=300`
- 근거: 지도 이동·확대/축소 중 같은 bbox가 반복 요청되는 비용을 CDN에서 흡수한다.
  공유 캐시는 60초로 제한해 10분 인제스트 SLA보다 충분히 짧게 유지한다.
- 회귀 검증: backend pytest 87 passed, frontend typecheck 및 production build PASS.
  Vercel production은 지시자를 `cache-control: public, max-age=30`으로 정규화했고,
  동일 bbox 재조회에서 `x-vercel-cache: HIT`를 반환했다.

## 2026-07-11 — 실시간 production 전환 기반

- 구현: `DATABASE_URL`이 설정된 Vercel API는 관리형 PostgreSQL을 읽고, 값이 없을 때만
  읽기 전용 SQLite 배포 스냅샷으로 fallback한다.
- scheduler: 10분 one-shot GitHub Actions worker를 추가했다. DB/API 키가 없는 동안에는
  성공 종료로 skip하며, 동시 실행은 concurrency group으로 막는다.
- 운영 이관: 항상 켜진 worker용 Dockerfile을 추가했다. GitHub Actions cron 지연은
  strict SLA에 부적합하므로 상시 worker 전환 기준과 bootstrap 절차를 ADR-0005에 기록했다.
- 검증: backend pytest 87 passed, snapshot fallback `GET /api/health` HTTP 200, workflow YAML
  parse PASS.

## 2026-07-12 — Track 1 A1 scoring model version 고정

- 구현: `SCORING_MODEL_VERSION="v1-idw-point"`를 단일 상수로 두고 `CafeScore`의
  non-null `model_version`, materialize upsert, 목록·상세 API 응답에 additive 필드로 연결했다.
- migration: `20260712_0003`이 nullable 컬럼 추가 → 기존 행 backfill → NOT NULL 전환 순서로
  실행되도록 구성하고, 기존 score가 있는 SQLite DB의 실제 upgrade 테스트를 추가했다.
- 실제 DB: local `backend/data/preview.db`와 Vercel bundle `api/data/preview.db`를 각각 백업한 뒤
  upgrade했다. 두 DB 모두 4,933개 score가 `v1-idw-point`로 backfill되고 NOT NULL임을 확인했다.
- 복구 확인: local snapshot 2,363건을 보존했고 materialize 재실행 결과 cafes 4,933,
  covered/fringe/uncovered `2,317/1,523/1,093`이었다.
- 검증: backend 전체 97 passed, compileall PASS, Vercel snapshot TestClient의 bbox API가 HTTP 200과
  `model_version=v1-idw-point`를 반환했다.
- production 검증: cache-busting query를 붙인 홍대 bbox API가 HTTP 200이며
  `model_version=v1-idw-point`를 반환했다.
- 재기동 확인: worker session 13858에서 scheduler started, API session 50211에서 local health
  `snapshots_last_hour=121`, `last_ingest_at=2026-07-11T15:42:09.249470Z`를 확인했다. 카페 응답은
  `model_version=v1-idw-point`를 반환했다.
- 자동 cycle: 2026-07-12 00:54:53 KST에 scheduler가 실행한 순회가
  `targets=121, saved=121, failed=0`으로 완료됐고, 다음 실행은 01:04:53 KST로 예약됐다.
- 판정: PASS. confidence 공식은 변경하지 않았다.
- 관련 인시던트: `INC-2026-009`

## 2026-07-12 — 국내·해외 확장을 위한 universal contracts

- 공개 타입: provider 독립 경계에 `RegionProfile`, `RawObservation`, `CrowdObservation`,
  `CoverageSnapshot`을 추가했다.
- 감사 필드: observation과 coverage에 provider, source version, license manifest, region/area,
  geometry version, observed/fetched 시각을 필수화했다.
- 원본 보존: provider 값의 타입·단위·라벨·정의·원본 필드명을 정규화 전 상태로 보존한다.
- 안전성: strict·immutable contract, IANA timezone 검증, UTC 정규화, 유효 상태 집합,
  시간 순서와 중복 locale/quality flag를 검증한다.
- 검증: source/license 필수값, 원본 의미 보존, UTC, coverage 상태, extra field 차단을 포함한
  focused tests 8건 PASS.
- 판정: PASS. provider adapter, DB persistence 및 외부 API 연동은 아직 추가하지 않았다.

## 2026-07-12 — 외부감사 대응 검증

- 감사 결과를 `docs/audits/2026-07-12-external-review-response.md`에 수용·보류·기각으로
  구분해 기록했다. ADR-0007은 구현 병렬화를 허용하되 공개·승격 게이트는 선행 DoD가
  통과할 때까지 차단하도록 규칙을 정정했다. Phase 6 기준선 전까지 신규 국내·해외 확장
  구현과 확장 문서 작성은 동결했다.
- `run_eval.py`는 관측 시각보다 나중에 계산된 점수를 사용하지 않는 historical no-leak
  조회와 관측 timestamp별 Spearman을 동일 가중치로 합산하는 macro 평가를 구현했다.
  회귀 테스트 추가 시 백엔드 전체 104개가 통과했고, 출처 API 테스트까지 포함한 최종
  결과는 105개 통과다.
- CI에 PostgreSQL 17 service를 추가하고 Alembic migration을 실제 DB에 적용한 뒤
  스키마를 확인하도록 구성했다. GitHub Actions run `29160900757`에서 backend 40초,
  frontend 18초로 통과했고 실제 `upgrade head`, head 확인과 핵심 테이블 smoke가 모두
  성공했다. Node.js 20 기반 action의 deprecation annotation은 후속 정리 대상이다.
- README의 외부 재현·Quickstart 명령에서 로컬 전용 `rtk` 의존을 제거하고 표준
  `git`, `uv`, `npm`, `curl` 명령만 사용하도록 정리했다.
- `/api/sources`와 지도 헤더에 서울특별시 OA-21285, Overture Places 및 전체 라이선스
  링크를 추가했다. MapLibre attribution의 `compact-show`와 `open` 상태를 강제로 제거하던
  코드와 강제 숨김 CSS를 삭제해 라이브러리 기본 동작을 복원했다.
- 프론트엔드 TypeScript typecheck와 production build는 통과했다. attribution의 최초 표시,
  접힘·펼침과 모바일 배치는 실제 브라우저 수동 검증이 남아 있다.
- production `busy-cafe-g2ozf7t2k-jaemanis-projects.vercel.app`은 Ready이며 정확한
  `busy-cafe.vercel.app` alias에 연결했다. 직접 deployment와 alias의 `/api/sources`가
  모두 HTTP 200으로 세 source ID, 공공누리 제1유형과 Overture release를 반환했고,
  배포 HTML에서 서울특별시·Overture·전체 라이선스 링크를 확인했다.
- 코드 라이선스 선택과 루트 `LICENSE` 추가는 저장소 소유자의 `[HUMAN]` 결정이 필요해
  미완료 상태다.

## 2026-07-12 — Phase 6 축소 기준선 계약과 현장 후보

- 관련 커밋: `5912787510db6f359336693b15eae77ae607820b`
- 입력: 현재 카페 원장, `v1-idw-point`의 materialized score, 홍대 관광특구와
  성수카페거리의 근접·중간·외곽 거리대
- 구현: `select_eval_candidates.py`가 active 카페를 POI source confidence 내림차순,
  거리 오름차순, cafe ID 오름차순으로 고정 선택한다. `run_eval.py`는 실제 관측 시각까지
  fetched된 과거 스냅샷만 재생한다.
- 라벨 계약: `observed_area_level`은 주변 보행 혼잡의 주 정답이고,
  `observed_venue_level`은 선택적인 매장 효용 라벨이다. 두 지표를 합산하지 않는다.
  동일 순위 비교군은 필수 `slot`으로 묶고 Spearman은 슬롯별 계산 후 macro average한다.
- 발견·수정한 문제: 이전 evaluator는 정확한 `observed_at`별로 순위를 계산했다. 현장
  순차 관측에서는 timestamp당 표본이 1개가 되어 Spearman이 대부분 `N/A`가 되므로,
  실제 관측 시각은 historical replay에만 사용하고 순위 비교는 사전 정의한 slot으로
  분리했다.
- 후보 결과: 총 24곳, 홍대·성수 각 12곳, 각 동네의 near/mid/fringe가 각각 4곳이다.
  cafe ID는 24개 모두 고유하다. 후보 CSV SHA-256은
  `38f5bed085646b691c6857e88dc18c420d0b5af5591b845e7b208c2d2036aff3`이다.
- 실행 명령: `cd backend && uv run pytest` 및
  `uv run python -m compileall -q app scripts tests`
- 실제 결과: backend 112 passed, compileall PASS, `git diff --check` PASS.
- 원격 검증: GitHub Actions CI run `29161361101`에서 frontend typecheck/build와 backend
  pytest/compileall, PostgreSQL 17의 실제 Alembic upgrade 및 schema smoke가 모두 PASS했다.
- 판정: 도구·후보 생성 PASS. 후보의 `poi_valid`와 `exclusion_reason` 검수 및 현장 관측은
  `[HUMAN]` 대기다. 실측 전이므로 정확도와 제품 가설에 대한 성능 주장은 하지 않는다.
- 계획과의 차이: PLAN v1.6의 좌석 점유 단일 라벨과 16곳×6슬롯 계획을 폐기했다.
  PLAN v1.7은 Track 1의 제품 경계에 맞춘 이중 라벨과 24곳×동네별 3슬롯 축소 기준선을
  canonical protocol로 둔다.

### 현재 Phase 6 DoD

- [x] 결정적 후보 선택기와 거리대별 24곳 후보 생성
- [x] 미래 fetched 데이터 누출을 막는 historical evaluator
- [x] 지역 혼잡 주 라벨과 매장 효용 보조 라벨 분리
- [ ] 후보 POI 유효성 및 관측 가능성 HUMAN 검수
- [ ] 동네별 3개 슬롯 현장 관측
- [ ] `docs/EVAL_REPORT.md`와 `v1-idw-point` 기준 지표 기록

## 2026-07-12 — complete-cycle 운영 상태와 preview.1 배포

- 관련 커밋: Phase 6 현장 입력 `a164e17`, 운영 상태·monitor `b0712e6`
- 문제 정의: 기존 `/api/health.last_ingest_at`은 121곳 중 한 곳의 snapshot만 갱신돼도
  fresh하게 보일 수 있었다. 또한 Vercel 프론트는 build-time 상수로 항상 snapshot 문구를
  표시해 향후 managed DB 전환 뒤에도 잘못된 모드를 보일 구조였다.
- 구현: `ingest_cycles`가 cycle 시작, 완료 시각, targets/saved/failed와
  running/complete/partial/failed 상태를 저장한다. 전체 대상 저장과 score materialize가
  모두 성공해야 complete가 된다. one-shot worker는 complete가 아니면 nonzero로 종료한다.
- health/monitor: API는 `data_mode`, 마지막 complete cycle과 latest cycle 상태를 반환한다.
  freshness probe는 complete cycle age만 사용하고 partial/failed, 25분 초과, 2분을 넘는 미래
  시각, HTTP URL과 잘못된 JSON을 실패 처리한다. cache-busting query로 CDN stale 응답도 피한다.
- workflow gate: `PRODUCTION_ENABLED=true` 전에는 poll과 monitor가 명시적으로 skip한다.
  활성화 뒤 URL 또는 secret 누락은 실패한다. 비활성 수동 검증은 poll run `29164391760`,
  monitor run `29164392552`에서 PASS했다.
- GitHub repository variable `PRODUCTION_HEALTH_URL`은
  `https://busy-cafe.vercel.app/api/health`로 등록했다. `PRODUCTION_ENABLED`와 production
  활성화 변수는 아직 없으므로 monitor와 poll은 계속 비활성이다.
- Supabase 설정 점검에서 GitHub `Production` environment에 `DATABASE_URL`과
  `SEOUL_API_KEY` 이름이 존재함을 확인했다. poll job은 이 environment를 명시하도록 수정했다.
  사용자가 넣은 기존 `DATABASE_URL` 값은 Project URL이어서 PostgreSQL 연결값으로 사용할 수
  없으며 교체 대기다. Vercel의 direct URL 제거 중 CLI scope 오판으로 Production 값도 함께
  삭제됐으나 공개 snapshot health는 HTTP 200과 카페 4,933개를 유지했다. `INC-2026-011`에
  기록했고 올바른 pooled Production URL 재등록 전에는 redeploy와 live 승격을 하지 않는다.
- 연결 전 코드 감사에서 Supabase 표준 `postgresql://` URL이 SQLAlchemy 기본 psycopg 2
  dialect로 해석되지만 Vercel requirements에는 PostgreSQL driver가 전혀 없음을 발견했다.
  `postgresql://`와 `postgres://`를 credential/query 손상 없이 `postgresql+psycopg://`로
  내부 정규화하고, Vercel bundle에 psycopg 3 binary를 추가했다. transaction pooler 호환을
  위해 `prepare_threshold=None`을 사용하며 외부 연결 없는 URL·engine 회귀 테스트를 추가했다.

## 2026-07-12 — Supabase production bootstrap dry-run 연결 검증

- 대상: GitHub `Production` environment secrets와 Supabase production DB. seed apply와
  `PRODUCTION_ENABLED`는 사용하지 않았다.
- 준비: 수동 `bootstrap-production.yml`을 추가했다. 기본 `apply=false`는 migration과
  hotspot/cafe dry-run만 수행하고, 명시적 HUMAN 승인 전에는 seed와 서울 API 수집을 하지 않는다.
- 첫 dispatch: job-level env에서 `runner.temp` context를 사용해 GitHub parser가 HTTP 422로
  실행 전 거부했다. `/tmp` runner 경로로 수정한 커밋은 `f13887b`이다. 외부 상태 변경 없음.
- run `29165824349`: secret preflight와 dependency 설치는 통과했지만 Alembic의 raw
  `postgresql://` 경로가 psycopg2를 요구해 migration 전에 실패했다. runtime과 migration의
  URL 정규화를 단일화한 `682f151`로 수정하고 166 tests 및 raw-URL offline render를 통과했다.
- run `29165859811`: psycopg 3 driver와 URL 정규화는 통과했다. Supabase direct connection이
  IPv6 주소로 해석됐으나 GitHub hosted runner에 IPv6 route가 없어 실제 연결 전에
  `Network is unreachable`로 종료됐다. migration과 seed 변경은 0건이다.
- 판정: 코드 경로 PASS, direct connection from GitHub BLOCKED. GitHub `DATABASE_URL`을
  Supabase Session pooler connection string으로 교체한 뒤 dry-run을 재실행한다. Vercel은
  serverless용 Transaction pooler를 유지하고 `PRODUCTION_ENABLED`는 계속 미설정 상태다.

### Session pooler 재검증

- run: GitHub Actions `29166166360`, `bootstrap-production.yml`, `apply=false`
- 연결: GitHub `Production` environment의 Session pooler로 remote PostgreSQL 연결 PASS.
  secret 값과 connection URL은 로그에 출력하지 않았다.
- migration: production DB에 Alembic `20260712_0004` head 적용 및 `--check-heads` PASS.
- hotspot dry-run: source 121, would insert/update 121/0, unchanged 0,
  polling targets 121/121.
- Overture download: release `2026-06-17.0`, 4,933건. cache SHA-256
  `5115e468e6ea34a4859fb9391914a5a9c82c9c2e99d7ba09c8fe8b3d7d8d184e`로
  기존 로컬 검증 cache와 완전히 일치했다.
- cafe dry-run: source/active 4,933/4,933,
  inserted/updated/unchanged/deactivated 4,933/0/0/0.
- dry-run 후 production DB summary: hotspots 0, polled 0, active cafes 0,
  ingest cycle 없음. schema 외 seed·서울 API write가 없음을 확인했다.
- 판정: migration과 두 seed dry-run PASS. `apply=true`는 HUMAN 검토·승인 대기다.
- 발견한 workflow 오류: 이전 revision의 poll run `29162731378`은 checkout 전에 기본
  `backend/` working directory를 사용해 skip 분기 자체가 실패했다. 현행 preflight는 root에서
  실행하며 `INC-2026-010`에 원인과 회귀 검증을 기록했다.
- migration: `20260712_0004`를 local과 Vercel bundle의 `preview.db`에 실제 적용했다.
  두 DB 모두 Alembic head를 확인했다. 적용 전 백업은 repository에서 제외된 로컬 파일로
  보존했다.
  - local before/after SHA-256:
    `738e78170aacc1f94f43ff314f4cd2cd18cb76cf7d31518eb0c1b1f5bc5c5895` /
    `bdce186700bfde078ac26bc0b0a83a6abe4d30cecbf1ca99b6ccc5ccc26cd8eb`
  - Vercel bundle before/after SHA-256:
    `762744cc67e120a111bb433d0e428a2911e62a1036b5246d0907cf4303b4122b` /
    `141848e5f1304826557097d609389b8ac06638578dc480a3ff37e75cdd01332c`
- 로컬 검증: backend 158 passed, compileall, PostgreSQL migration SQL render, frontend
  typecheck/build와 세 workflow YAML parse PASS. 기존 Starlette/httpx deprecation과 Vite
  500kB chunk warning은 남아 있다.
- 원격 검증: CI run `29164383390`에서 PostgreSQL 17 실제 migration, schema smoke, backend와
  frontend가 모두 PASS했다.
- 배포: `busy-cafe-k78p8h65o-jaemanis-projects.vercel.app`가 Ready이며
  `busy-cafe.vercel.app` exact alias에 연결했다. direct URL과 alias의 `/api/health`가 모두
  HTTP 200, `data_mode=snapshot`, `cafes_count=4933`을 반환한다. preview에는 complete cycle
  기록이 없으므로 해당 필드는 NULL이며, 이를 live freshness로 해석하지 않는다.
- 판정: complete-cycle 계측, 비활성 monitor와 snapshot 재배포 PASS. managed PostgreSQL,
  `PRODUCTION_ENABLED=true`, 1시간 6-cycle 검증과 실제 복구 훈련은 `[HUMAN]`/운영 대기다.

## 2026-07-12 — Supabase production bootstrap 적용

- 승인·실행: GitHub Actions `bootstrap-production.yml`, run `29180703862`, `apply=true`.
- migration: production PostgreSQL이 Alembic head `20260712_0004`임을 확인했다.
- hotspot seed: 121곳을 적재했고 `is_polled=1`은 121/121이다.
- cafe seed: Overture release `2026-06-17.0`에서 active 카페 4,933곳을 적재했다.
  cache SHA-256은
  `5115e468e6ea34a4859fb9391914a5a9c82c9c2e99d7ba09c8fe8b3d7d8d184e`로
  dry-run 및 로컬 검증 입력과 일치한다.
- 첫 ingest: `targets=121, saved=121, failed=0`, latest cycle 상태 `complete`.
  전체 순회 소요는 약 4분 6초로 10분 폴링 주기 안에 완료됐다.
- 판정: production seed와 최초 complete cycle PASS. 자동 poll과 monitor는 아직 비활성이고,
  공개 `busy-cafe.vercel.app`은 이 기록 시점에 snapshot 배포다. live direct deployment 검증과
  exact alias 승격 전에는 `PRODUCTION_ENABLED`를 켜지 않는다.

## 2026-07-12 — freshness UI, Apache-2.0, 이중 관측자 평가 계약

- health: `/api/health`가 canonical config의 `stale_warn_min`을 반환한다.
- UI: live 상태에서 latest cycle이 `partial`/`failed`/없음이거나 complete 시각이 유효하지
  않거나 임계값보다 오래되면 `데이터 갱신 지연 중`을 표시한다. health 조회 실패는 지도를
  중단하지 않고 데이터 모드 확인 불가 상태로 격리한다. snapshot 표시는 유지한다.
- 라이선스: 저장소 코드를 Apache License 2.0으로 확정하고 루트 `LICENSE`를 추가했다.
  외부 데이터와 지도 라이선스는 코드 라이선스와 별개임을 README와 attribution audit에 명시했다.
- Phase 6: worksheet는 정확히 두 관측자를 요구하고 세션별 4개 카페를 독립 중복 관측한다.
  엔진 지표는 primary 행만 사용하며 reliability 행으로 quadratic weighted Cohen's kappa를 계산한다.
  중복·주 관측 누락·다중 reliability 입력은 fail-closed 처리한다.
- 검증: backend `184 passed`, compileall PASS, frontend typecheck/build PASS,
  `git diff --check` PASS.
- 판정: 구현·자동 검증 PASS. 실제 브라우저 stale 시나리오와 Phase 6 현장 관측은 대기다.

## 2026-07-12 — Supabase live read 승격과 수집 worker 운영 실패

- 코드 기준: freshness·license `9801383`, outage circuit `2895f27`, read-only canary
  `0f2b9c3`, poll v2 `aabea03`, gate 분리 `12b29a2`.
- Vercel deployment: `dpl_H97BiHsYg18yHezTB1S2NcQnWuv1`, direct host
  `busy-cafe-muwrcw8tl-jaemanis-projects.vercel.app`, exact alias
  `busy-cafe.vercel.app`.
- 승격 직후 direct/alias health: `data_mode=live`, cafes 4,933,
  latest complete `targets/saved/failed=121/121/0`, `stale_warn_min=25`.
  bbox, cafe detail, forecast와 `/api/sources`도 HTTP 200으로 확인했다.
- CI: run `29180965164`, `29181347156`, `29181421631`, `29181975649`에서 backend,
  frontend와 PostgreSQL 17 실제 migration smoke가 모두 PASS했다.

### 장애와 완화 실측

- run `29181020574`: 첫 target부터 서울 API 요청이 반복 실패했다. serial retry가 8분 job
  limit에 도달해 취소됐고, 기존 worker는 latest cycle을 `running`으로 남겼다.
- 로컬 동일 key의 `광화문·덕수궁` 1회 호출은 같은 날 즉시 성공했다. GitHub runner에서
  DB 접근 없이 실행한 fixed 1-target canary run `29181444784`도 12초에 PASS했다.
- run `29181460312`: serial full poll은 121개 snapshot을 처리했지만 worker 8분 deadline에
  도달했다. SIGINT cleanup 수정으로 latest cycle은 `failed`로 마감됐고 `running` 고착은
  재발하지 않았다.
- poll v2: httpx connection pool과 fetch concurrency를 4로 제한하고, 결과 검증 순서를
  target 순서로 고정했으며, 121개 snapshot 정상 저장을 1 transaction으로 바꿨다.
  synthetic 121-target 20ms fixture는 concurrency 1의 3.343초에서 concurrency 4의
  0.923초로 약 3.6배 단축됐다.
- score materialize: 로컬 실제 4,933개 DB에서 변경 전 1.15초, 변경 후 0.98~1.00초였다.
  coverage `2,317/1,523/1,093`과 score/level/evidence diff는 0건이었다.
- run `29182006480`: poll v2는 65.536초에 fail-closed 종료했다. 첫 5개 GitHub runner 서울
  요청이 모두 실패해 circuit이 열렸고 `targets/saved/failed=121/0/121`이었다. phase는
  `poll=52.387s`, `fetch_sum=132.696s`, `persist_sum=0`, `materialize=7.022s`,
  `finalize=1.131s`였다.
- 최종 자동 검증: backend 215 passed, compileall PASS, frontend typecheck/build PASS,
  `git diff --check` PASS. Vite 500kB chunk warning과 Starlette/httpx deprecation warning은
  기존과 동일하다.

### scheduler 판정

- GitHub scheduled poll event는 `18:02, 19:21, 20:22, 21:14, 22:09, 23:09,
  00:09, 03:55 UTC`에 생성됐다. 10분 cron과 달리 대체로 약 1시간 간격이고 최대
  3시간 46분 공백이 있었다.
- `PRODUCTION_POLL_ENABLED=false`, `PRODUCTION_MONITOR_ENABLED=true`로 분리했다.
  monitor run `29182058524`는 latest failed/stale 상태를 exit 1로 정확히 탐지했다.
- 판정: Supabase live read와 stale UI는 PASS. GitHub hosted runner poll과 cron은
  10분/25분 freshness 운영 경로로 FAIL. ADR-0008에 따라 전용 상시 Docker worker 배포와
  1시간 6 complete-cycle 검증이 `[HUMAN]` 블로커다. Phase 6와 확장 track 승격은 계속
  차단한다.
- 최종 코드 배포: CI run `29182162571` PASS 뒤 deployment
  `dpl_HbFmuwuBjdVgR8an4Xb7aLTh7Ywb`
  (`busy-cafe-hlp4q9ntg-jaemanis-projects.vercel.app`)의 health와 cafe detail을 direct로
  검증하고 exact `busy-cafe.vercel.app` alias를 이 deployment로 이동했다.

## 2026-07-12 — Track 1 WP-1 / v3-density-shadow 밀도 채점기

- 실행 환경: backend/, uv, pytest
- 검증자: Claude (Fable 판단·리뷰, opus 구현)
- 관련 커밋: `9bef9e2`
- 입력/fixture: 합성 polygon fixture(서울 위도 37.55 인근 ~300m 정사각형, 포함·겹침·
  경계 케이스)와 합성 SQLite snapshot(ppltn_min/max). 실 API/네트워크 미사용
- 실행 명령: `rtk proxy uv run python -m pytest tests` ·
  `rtk proxy uv run python -m compileall -q app scripts`
- 기대 결과: 전체 스위트 통과(신규 밀도 테스트 포함), compileall 성공, polygon_shadow
  동작 불변
- 실제 결과: 에이전트 전체 실행 278 passed(동시 작업 트리, 기준선 250 + 병렬 에이전트
  12 + 신규 16). 오케스트레이터 재검증: scoring 범위 39 passed, compileall PASS.
  면적 근사는 haversine 변길이 곱 대비 0.22% 이내(허용 2%). polygon_shadow 기존
  테스트 9건 무변경 통과
- 판정: PASS. 전체 스위트 최종 확인은 WP-3 병합 후 재실행으로 기록한다
- 계획과의 차이: confidence tier/CONF 상수는 레벨 매핑 부재로 의도적 미도입.
  `run_density_snapshot.py` 읽기 전용 구조 리포트는 stretch로 포함
- 후속 조치: 실 ppltn 분포로 밀도→레벨 cut point 보정(Track 1 gate) 전에는 레벨을
  emit하지 않는다. 생활인구 250m 격자 확보 후 백테스트 설계와 연결

## 2026-07-12 — WP-3 Overture confidence 임계값 연구 스캐폴딩

- 실행 환경: backend/, uv, pytest
- 검증자: Claude (Fable 판단·리뷰, sonnet 구현)
- 관련 커밋: `33df01c` (연구 노트: `docs/research/2026-07-12-catalog-recall.md`)
- 입력/fixture: 합성 `OvertureCafeRecord`와 DuckDB로 직접 생성한 로컬 parquet
  extract(네트워크 미사용). 실 Overture S3 접근 없음
- 실행 명령: `rtk proxy uv run python -m pytest tests`
- 기대 결과: 전체 스위트 통과, `--confidence-report`가 DB 세션·`cache_seoul_extract`를
  호출하지 않음(monkeypatch로 확인)
- 실제 결과: 278 passed, compileall PASS. `--min-confidence` 플래그는 기존
  커밋(`298af5a`)에 이미 존재해 신규 추가 불필요를 확인. scoring, 기존 config 값,
  DB 스키마, 기본 ingest 동작 무변경
- 판정: PASS(자동 테스트 기준). 층화 표본 정밀도 측정은 실제 저임계값 재다운로드가
  필요한 `[HUMAN]` 작업으로 남는다
- 계획과의 차이: 로컬 cache에 다운로드 시점 임계값이 저장되지 않아 "cache filtered"
  판정은 관측된 최소 confidence를 근사 floor로 사용한다(모듈 주석에 한계 기록).
  병렬 작업 중 orchestrator가 `config.py` 파일 전체를 스테이징해 본 작업의
  `OVERTURE_CONFIDENCE_REPORT_*` 상수 3개가 밀도 채점기 커밋 `9bef9e2`에 섞여
  들어갔다. 기능 영향 없음, history rewrite는 하지 않고 기록으로 남긴다. 재발 방지:
  병렬 에이전트가 같은 파일을 수정 중일 때 orchestrator는 파일 단위 `git add`를
  하지 않는다
