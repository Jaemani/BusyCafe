# Verification Log

실측 검증 결과, 계획과 실제의 차이, Phase별 DoD 통과 여부를 누적 기록한다. 인증키와 개인정보는 기록하지 않는다.

## 상태 요약

| Phase | 상태 | 완료일 | 근거 |
|---|---|---|---|
| Phase 0 | 완료 | 2026-07-11 | 실 API/스키마/라벨/호출 제한/마스터 검증 및 fixture 커밋 |
| Phase 1 | 인제스트 구현 완료·운영 SLO 미달 | - | 121개 coverage는 100%이나 24시간 complete rate 94.46%, 최근 6시간 97.22%로 99% 목표 미달 |
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
- 혼잡도: 공식 121개 장소 전부를 Supabase-dispatched worker가 5분마다 폴링한다.
  목표량은 34,848콜/일이며 current scheduler 결정은 ADR-0012가 소유한다.
- 외부 매장 링크: 검증된 provider ID가 있으면 canonical direct detail을 보인다. Naver ID가
  없으면 주소+이름 fallback을 `네이버맵 검색`으로 구분하며 좌표·추측 ID는 사용하지 않는다.

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
  기존 `budy-cafe.vercel.app` alias는 당시 이전 링크 호환용으로 유지했다. 2026-07-15
  정식 URL 검증 뒤 제거한 현재 상태는 최신 검증 기록을 따른다.
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

## 2026-07-12 — 생활인구 250m 격자 Open API 실측과 원본 확보 (ADR-0009)

- 실행 환경: 로컬 macOS, curl. 명시적 검증 실호출(테스트 아님)
- 검증자: Claude (Fable)
- 관련 커밋: ADR-0009, fixture 커밋 참조
- 실행 명령: `openapi.seoul.go.kr:8088/{KEY}/{TYPE}/Se250MSpopLocalResd/1/5/{YMD}`
  (키는 `backend/.env`에서만 읽고 URL·응답 로그에 출력하지 않음, INC-2026-006 절차)
- 기대 결과: 기존 `SEOUL_API_KEY`로 250m 격자 서비스 호출 성공, 스키마 실측
- 실제 결과:
  - 서비스명 `Se250MSpopLocalResd`는 포털 API 뷰(`openApiView.do`)에서 확인.
    상세 페이지 탭은 동적 렌더링이라 정적 fetch로는 보이지 않았다.
  - 인자 없는 호출과 `json` 타입 호출은 HTTP 200 + `ERROR-500`. 동일 요청을
    `xml` + 기준일 `20260708`로 바꾸면 `INFO-000` 정상 — **JSON 타입은 포털 측
    결함으로 판단, ingest 기본 포맷은 XML**
  - 대조군: 같은 키의 `citydata_ppltn` 호출은 `INFO-000` → 키 유효성 분리 확인
  - `list_total_count=253,751`(하루, ≈10,573셀 × 24시간), `TT`(시각) 필드로
    시간대별 제공 확정
  - `CELL_ID`는 국가지점번호 형식(`다사52505325`) — geometry 산술 파생 `[VERIFY]`
  - 성·연령 구간 3명 이하 `*` 마스킹, `H_DNG_CD` 후행 공백 확인(파서 strip 필수)
  - 응답 본문에 인증키 미포함을 확인한 뒤 원본 5행을
    `backend/fixtures/se250m_spop_local_resd_sample.xml`로 보존
    (SHA-256 `efa7423dafa4017376d9b2b7a5feaeae4fd1dbcf656c216b609d8a892ec401fd`)
- 판정: PASS — 기존 키로 250m 격자 접근 가능, 단위 결정(ADR-0009) 실측 근거 확보
- 후속 조치: 월별 ZIP 파일 원본 확보 후 스키마·파서 확정(INC-2026-001 순서),
  CELL_ID → geometry 변환 검증, citydata 혼잡 라벨과의 상관 실험 설계

## 2026-07-12 — 국가 격자 CELL_ID 디코더 표본 검증

- 실행 환경: backend/, uv, pytest + 일회성 검증 스크립트(scratchpad)
- 검증자: Claude (Fable 판단·검증, opus 부분 구현 후 세션 한도로 중단 → Fable이 완료)
- 관련 커밋: `7183a78`. 상세 수치는
  `docs/research/2026-07-12-cell-id-decode.md`
- 입력: `Se250MSpopLocalResd` 20260708 실응답 1000행(고유 셀 817, 행정동 48).
  검증용 실호출 원본은 scratchpad에만 두고 5행 fixture는 기존 커밋 유지
- 실행 명령: `rtk proxy uv run python -m pytest tests` ·
  `rtk proxy uv run python -m compileall -q app scripts` · 검증 스크립트 1회
- 기대 결과: 디코드된 셀이 서울 bbox 안, 행정동 군집 정합, 250m 간격, 회귀 테스트 통과
- 실제 결과: 294 passed(+16), compileall PASS. bbox 817/817, 동 군집 최대 쌍거리
  중앙값 1,344m·최악 3,603m, 종로구 389셀 centroid (37.5899, 126.9806) 정합,
  왕복 투영 오차 ≤ 0.000011m, 인접 셀 간격 250.56m
- 판정: PASS(표본 기반). 공식 250m 격자 경계 파일과의 전수 대조는 `[VERIFY]` —
  geometry가 점수 계산에 들어가기 전 필수
- 계획과의 차이: 구현 에이전트가 세션 한도로 2회 중단되어 테스트·검증·문서는
  오케스트레이터가 직접 완료했다. 표본이 종로·용산 구간에 한정(정렬 첫 1000행)

## 2026-07-12 — 생활인구 대량 파일 다운로더와 파일 스키마 실측

- 실행 환경: backend/, uv, pytest + 명시적 검증 실다운로드 1회
- 검증자: Claude (Fable — sonnet 에이전트가 세션 한도로 중단, config 상수와 URL
  실검증 기록을 이어받아 완료)
- 관련 커밋: 구현 `6fdf450`, 실측 기록 `22f41e8`
- 실행 명령: `nio_download.do`에 `infId=OA-22784, infSeq=1, seq=260708` POST ·
  `rtk proxy uv run python -m pytest tests` · dry-run
  `scripts/download_living_population.py --date 20260708`
- 기대 결과: 페이지 JS(`downloadFile('260708')`)에서 읽은 seq 파생 규칙이 실제 파일을
  반환, 다운로더는 dry-run 기본·덮어쓰기 거부·부분 파일 미노출
- 실제 결과:
  - 일별 ZIP 실다운로드 15,037,162 bytes — 선행 에이전트가 기록한 실측치와 정확히
    일치(이중 확인). SHA-256
    `8ce3412e59c6c5dd0c11af1ec0c1932c4fc099f9446325e36961241ca96ff315`.
    Content-Disposition 파일명 일치, ZIP 매직 정상
  - 내부 CSV 실측: cp949 인코딩, 헤더
    `일자, 시간, 행정동코드, 250M격자, 생활인구합계, 남/여 연령구간…`,
    **`생활인구합계`도 `*` 마스킹 가능**(심야 저인구 셀), 행정동코드 후행 공백 —
    상관 실험 설계의 마스킹 규칙을 관측 전 수정·확정했다
  - 테스트: 315 passed(+21: target 파생 9, client 6, script 6), compileall PASS,
    dry-run 출력 정상. 대량 파일은 gitignore된 `backend/data/living_population/`에만
    저장된다
- 판정: PASS. 월별 파일(448MB)의 실다운로드는 상관 실험 직전에 명시적으로 실행한다
- 후속 조치: CELL_ID 디코더와 이 다운로더로 상관 실험 입력이 준비됨 — 남은 선행
  조건은 worker 연속 수집(`[HUMAN]`)뿐이다

## 2026-07-13 — 생활인구 CSV 파서와 250m 공간가중 shadow

- 실행 환경: macOS, backend/, Python 3.14, uv/pytest
- 관련 커밋: 파서 `8a0529f`, 공간가중 shadow `88781d6`
- 입력:
  - 실측 일별 ZIP에서 파생한 2행 `cp949` fixture
    (`backend/fixtures/living_population_minimal_cp949.csv`)
  - 고정 national-grid 셀과 synthetic hotspot polygon fixture
- 실행 명령:
  - `cd backend && uv run pytest`
  - `cd backend && uv run python -m compileall -q app scripts`
  - `cd backend && mypy app`
- 실제 결과: **342 passed**, compileall PASS, mypy PASS. 기존
  Starlette/httpx deprecation warning 1건 외 신규 경고 없음
- 구현 계약:
  - CSV는 스트리밍 파싱하며 실제 달력 날짜, `00~23`, strip 후 8자리 행정동 코드,
    250m lattice `CELL_ID`, 음이 아닌 plain decimal 또는 `*`만 허용한다
  - `*`는 `None + raw token + masked flag`로 보존하고 2.0/0/3 대치는 파서 밖의
    상관 실험 계산층에서만 수행한다
  - 공간 가중치는 `area(cell ∩ hotspot) / area(cell)`이며, 출력은
    `(area_cd, cell_id)` 순으로 결정적이다. I/O·DB write·공개 점수 변경은 없다
- 판정: PASS(shadow 기반). 공개 `v1-idw-point`는 변경하지 않았다
- 승격 차단 조건: 공식 250m 경계 파일 전수 대조 `[VERIFY]`, 7일 연속 snapshot,
  Phase 6 현장 관측이 남아 있다
- 감사 중 정정: 월별 448MB 파일은 포털 메타데이터만 확인했는데 전체 다운로드까지
  검증한 것으로 config/client/CHANGELOG에 잘못 기록돼 있었다. 일별 15MB 파일만 full
  download 검증된 사실로 수정했다. 런타임 동작에는 영향이 없었다

## 2026-07-13 — production 신선도 재확인

- 실행 명령: `curl -fsS https://busy-cafe.vercel.app/api/health`
- 실제 결과: `data_mode=live`, 마지막 ingest `2026-07-12T05:42:03Z`, 마지막 complete
  cycle `2026-07-12T05:12:32Z`, 최신 cycle `failed`(`saved=0`, `failed=121`),
  `snapshots_last_hour=0`
- 판정: FAIL — live PostgreSQL read는 동작하지만 현재 데이터는 준실시간이 아니다.
  ADR-0008의 전용 상시 worker 배포·1시간 6회 complete 검증 전까지 실시간 승격 금지

## 2026-07-13 — 과거 데이터·선행사례 조사와 temporal baseline shadow

- 관련 커밋: 구현 `e6e26d7`, 희소 공휴일 창 보완 `7e7cecb`; 조사 근거는
  `docs/research/2026-07-13-historical-baseline-and-prior-art.md`
- 공식 웹 실측:
  - OA-22784 파일 페이지에서 월별 `250_LOCAL_RESD_202301.zip`부터
    `250_LOCAL_RESD_202606.zip`까지 **42개**를 확인
  - OA-21285 FAQ에서 분단위 대용량 실시간 데이터는 과거를 별도 적재하지 않아 제공이
    불가능하다는 답변을 확인. 원천부서 별도 문의 가능성만 `[VERIFY][HUMAN]`으로 유지
  - Google Business 공식 도움말에서 최근 수개월의 요일·시간별 평균 popularity와
    평소 대비 live visit overlay 구조를 확인. 공식 Places API에 없는 Popular Times를
    스크레이핑하지 않는 기존 정책 유지
- 구현:
  - `download_living_population_history.py`: inclusive 월 범위, dry-run 기본, 순차 apply,
    `.part` 원자 게시, 무덮어쓰기, range·entry·기존 size/SHA 검증 resume, 원자 manifest
  - `temporal_baseline_shadow.py`: ISO 요일과 일반/토/일/공휴일/연휴를 분리하고
    `exact → day type → nominal weekday → hour` 계층 fallback·shrinkage 수행
  - 일반일은 84일/28일 반감기, 희소 공휴일·연휴는 1,095일/365일 반감기를 provisional
    기본값으로 분리하고 실제 적용값을 provenance에 기록
  - 예측 target의 cutoff 이후 행은 배제하고, `log1p` 최근성 가중 평균·산포·raw/effective
    표본 수·마스킹 비율·fallback 깊이·달력/원천/SHA provenance를 반환
- 실행 명령:
  - `cd backend && uv run pytest`
  - `cd backend && uv run python -m compileall -q app scripts tests`
  - `cd backend && uv run python scripts/download_living_population_history.py --start-month 202301 --end-month 202606`
- 실제 결과: **375 passed**, compileall PASS, 42개월 dry-run PASS. dry-run은 네트워크와
  파일 쓰기를 수행하지 않았다. 기존 Starlette/httpx deprecation warning 1건은 유지
- 판정: PASS(shadow 기반). 공개 `v1-idw-point`, DB schema와 API 응답은 변경하지 않았다
- gate 정정: 7일은 각 요일이 1회뿐이므로 fusion 탐색에만 사용한다. 최소 4주부터 feature
  비교, 권장 8~12주와 Phase 6 관측 후에만 공개 승격 후보를 판단한다
- 의미상 제한: 생활인구와 citydata는 통신계열 upstream 편향을 공유할 수 있어 둘의 높은
  상관은 독립 정확도 증거가 아니다. Phase 6 현장 관측을 계속 최종 gate로 사용한다
- 미실행 `[HUMAN]`: 약 16~25GB로 추정되는 42개월 `--apply`, 한국천문연 특일 API와
  ASOS 활용신청·fixture, OA-22785/22786 외국인 원본 확보, 전용 citydata worker 배포
- 계획과의 차이: 월별 full download 전 parser를 만들지 않는다는 ADR-0009 초안은 일별
  full ZIP 실측 후 strict parser를 허용하도록 갱신했다. 첫 월 backfill의 계약 대조가
  통과하기 전에는 역사 집계를 시작하지 않는다

## 2026-07-13 — 국내·해외 확장 현실성 및 대량 집계 경로

- 관련 커밋: DuckDB compactor `23f3442`, 지역 범위 POI 안전 수정 `128fb0e`,
  결정·조사 문서는 ADR-0010과
  `docs/research/2026-07-13-regional-expansion-feasibility.md`
- 공식 소스 read-only 조사:
  - 전국 시간×250m 생활/유동인구 공공 피드는 확인되지 않았다. 국토지리정보원 전국
    인구격자는 정적 인구 통계이며 서울 생활인구의 대체재가 아니다
  - 부산 스마트교차로 API에서 날짜·시간 입력, `walkCnt` 보행자수와 별도 위경도 API,
    개발계정 500회/일 메타를 확인. 실제 cadence·coverage·pagination은 fixture 대기
  - Melbourne 공식 API에서 sensor 위치 134개, 최근 응답 89개 sensor ID·2,135행,
    2009년 이후 시간별 이력을 확인. `Past Hour` 응답이 약 2시간 39분 범위를 포함해
    48시간 dedupe/지연 shadow를 선행 gate로 지정
  - Foursquare current Place Details SSR OpenAPI에서 `hours_popular` array와
    `popularity` double 필드 존재를 재확인. 전자는 인기 시간 구간, 후자는 의미 설명이
    부족한 scalar이므로 live 혼잡으로 사용하지 않는다
- 설계 판정:
  - public v1과 서울 운영을 최우선으로 유지
  - 국내 두 번째 provider fixture 후보는 부산, 해외 후보는 Melbourne
  - `universal_contracts.py`는 런타임 import가 없는 experimental seam inventory로 동결.
    두 번째 provider fixture 전 contract 필드·Protocol·DB migration 확장 금지
  - 제품 상태는 향후 `catalog_only`, `baseline_only`, `live_supported`, `suspended`를
    구분하고 과거 기준선을 “지금”으로 표현하지 않는다
- 구현:
  - `compact_living_population.py`는 모든 CP949 source row의 날짜·시간·행정동·CELL_ID·
    총계·중복을 DuckDB set scan으로 검증하고 allowlist 셀만 Parquet으로 게시한다
  - allowlist 셀이 일부라도 원본에 없으면 output 생성 전에 실패한다. 입력/allowlist/output
    SHA-256, 크기, row counts, DuckDB/query/schema version을 원자 manifest로 보존한다
  - Overture seed/deactivation은 호출자가 지정한 bbox 내부로 제한한다. A지역 seed 뒤
    B지역 seed를 실행해도 A지역 cafe가 active 상태를 유지하는 회귀 테스트를 추가했다
- 실행 명령:
  - `cd backend && uv run pytest`
  - `cd backend && uv run python -m compileall -q app scripts tests`
  - 최소 CP949 fixture와 1-cell allowlist를 이용한 compactor dry-run
- 실제 결과: **395 passed**, compileall PASS, compactor dry-run PASS. dry-run output 파일은
  생성되지 않았다. 기존 Starlette/httpx deprecation warning 1건만 유지
- 판정: PASS. 공개 API·score·DB schema 변경 없음
- `[HUMAN]` 다음 입력: 부산 공공데이터포털 API 키, 두 도시 라이선스·파생 표시 최종 확인.
  Melbourne 48시간 read-only shadow와 fixture 확보는 다음 구현 단계에서 진행 가능

## 2026-07-13 — 도시 활동도 코어 결정과 source-local activity shadow

- 실행 환경: macOS, backend/frontend, Python 3.14, uv/pytest, Vite/TypeScript
- 관련 결정: [ADR-0011](adr/ADR-0011-urban-activity-core-cafe-overlay.md)
- 입력: 고정 합성 observation·baseline fixture. 실 API와 네트워크 호출 없음
- 실행 명령:
  - `cd backend && uv run pytest`
  - `cd backend && uv run python -m compileall -q app scripts tests`
  - `cd frontend && npm run typecheck`
  - `cd frontend && npm run build`
- 실제 결과: backend **417 passed**, compileall PASS. frontend typecheck와 production
  build PASS. Vite의 500kB 초과 bundle 경고는 기존과 동일하며 신규 오류는 없음
- 구현 계약:
  - 제품 코어를 도시 활동도 surface로, 카페를 첫 활용 overlay로 정의한다. 지역 활동도를
    카페 좌석 점유율이나 대기 인원으로 표현하지 않는다
  - observation type과 `source_id`가 같은 입력만 source-local 기준선으로 비교한다.
    서로 다른 provider의 raw 값은 직접 결합하지 않는다
  - `signal_mode`와 freshness를 별도 축으로 유지한다. stale 관측은 출처·관측 및 수집
    시간 범위를 보존하지만 현재 값과 anomaly는 `NULL`로 반환한다
  - 결정적 frozen dataclass, `log1p` anomaly와 구간 envelope, contributor disagreement,
    입력 품질·신선도·provenance를 제공한다
- UI 의미 수정: 퍼센트형 `신뢰도`를 `근거 강도 높음/보통/낮음`으로 바꾸고,
  Overture confidence는 정확도 확률이 아닌 `장소 원장 품질`로 표시한다
- 공개 영향: 공개 `v1-idw-point`, `/api/cafes` 계약, DB schema와 materialized score는
  변경하지 않았다. activity shadow는 DB/API 의존이 없는 비공개 비교 경로다
- heatmap 판정: 독립적인 cell artifact와 평가 근거가 생기기 전에는 카페 point 추정치를
  heatmap으로 확장하지 않는다. 이후에도 활동도는 별도 `/api/activity` 계약으로 제공한다
- 판정: PASS(shadow 및 의미 수정). 공개 모델·기본 레이어 승격은 Phase 6와 source별
  empirical gate 통과 전까지 차단한다

## 2026-07-13 — activity shadow 계약 강화와 offline 250m cell artifact

- 실행 환경: macOS, backend/, Python 3.14, uv/pytest
- 입력: 고정 합성 observation·baseline fixture와 테스트용 compact Parquet·버전 지정 달력.
  실 API와 네트워크 호출은 사용하지 않았다
- 실행 명령:
  - `cd backend && uv run pytest`
  - `cd backend && uv run python -m compileall -q app scripts tests`
- 실제 결과: backend **429 passed**, compileall PASS
- activity shadow 강화:
  - 여러 셀의 raw 관측값과 raw 기준선은 같은 source 안에서도 제품 값으로 집계하지 않고,
    각 contributor가 자기 기준선과 비교해 만든 source-local anomaly만 결합한다. raw 값은
    contributor별 근거로 보존하고 단일 contributor일 때만 estimate의 raw 필드에 노출한다
  - 모든 결합 contributor는 같은 `source_id`, observation type과 `source_version`을
    가져야 한다. fresh와 stale 입력이 섞인 estimate도 별도 계산을 요구하며 거부한다
  - 만료된 forecast와 생성 시점보다 앞선 forecast target을 거부한다. baseline은 model·
    source·달력 버전, source hash, window, bucket, 표본 수, fallback과 masking 비율을
    구조화해 보존하고, cutoff가 관측일 이후이거나 window 종료와 다르면 누수 가능 입력으로
    보고 fail-closed 처리한다
- offline artifact 구현:
  - `scripts/build_activity_artifact.py`는 compact 생활인구 Parquet과 명시적 target date·
    hour, `source_version`, 버전 지정 달력을 받아 셀별 GeoJSON FeatureCollection을
    결정적으로 생성한다. target 이후 이력은 기준선에서 제외한다
  - 셀 polygon은 decoder가 산출한 네 모서리를 그대로 닫힌 quadrilateral로 사용하고
    `CELL_GEOMETRY_VERSION`을 provenance에 기록한다. 공식 격자 파일 전수 대조 전까지
    이 geometry는 shadow-unverified이며 공개 활동도 또는 위치 정확도 근거로 승격하지 않는다
  - 현재 관측이 마스킹되면 값을 대치하지 않고 `baseline_only`, 현재 행이 없으면
    `baseline_only`, 기준선까지 없으면 `unsupported`로 표현한다. 각 상태에서 현재 값과
    anomaly를 만들어내지 않는다
  - feature 순서와 JSON key를 고정해 동일 입력의 byte output을 결정적으로 유지한다.
    기본은 dry-run이고 `--apply`만 `.part`를 거쳐 원자적으로 게시하며, 기존 output과
    partial file이 있으면 덮어쓰지 않고 실패한다
- 공개 영향: API, DB schema, UI와 공개 `v1-idw-point`는 변경하지 않았다. 417-test 단계에서
  통과한 기존 frontend typecheck/build 이후 frontend 변경도 없다. 이번 artifact는 offline
  shadow 산출물이며 heatmap이나 공개 preview를 추가하지 않았다
- 판정: PASS(shadow 계약과 offline artifact 자동 검증). 공식 격자 전수 대조와 empirical
  평가 전까지 공개 `/api/activity`, heatmap과 기본 레이어 승격은 계속 차단한다

## 2026-07-13 — stale 현재 혼잡도 fail-closed 처리

- 관련 인시던트: [INC-2026-014](INCIDENTS.md#inc-2026-014--오래된-혼잡-스냅샷을-현재값처럼-표시)
- 재현 상태: production poll이 `PRODUCTION_POLL_ENABLED=false`로 중단돼 있었고,
  활성화한 직전 실행들은 timeout 또는 `saved=0, failed=121`로 종료됐다. 후자의 실제
  요청은 첫 5개 실패 뒤 circuit-open된 116개를 실패 집계에 포함한다. 7월 13일 새벽
  API는 2026-07-12T05:10Z 전후의 오후 관측에서 만든 level을 현재값처럼 반환했다
- 실행 명령:
  - `cd backend && uv run pytest`
  - `cd backend && uv run python -m compileall -q app scripts tests`
  - `cd frontend && npm run typecheck`
  - `cd frontend && npm run build`
- 실제 결과: backend **431 passed**, compileall PASS. frontend typecheck와 production
  build PASS. Vite의 500kB 초과 bundle 경고는 기존과 동일하며 신규 오류는 없음
- 회귀 계약:
  - 요청 시각 기준 관측 나이가 `STALE_WARN_MIN`을 초과하면 `freshness=stale`이고,
    경계값 자체는 fresh다
  - 관측 시각이 없거나 `FRESHNESS_MAX_FUTURE_SKEW_MIN`을 초과해 미래이면 stale다
  - stale 카페 응답은 level·score·confidence·confidence_tier를 `NULL`로 반환하지만
    coverage, model version, 기준 핫스팟·거리와 원본 observed_at은 보존한다
  - stale 항목은 양수 `min_conf` 조회에서 제외하며, 상세 `forecast_1h`와 핫스팟 level도
    `NULL`로 반환한다
  - 프론트 상세 패널은 “갱신 지연 · 현재 혼잡도 숨김”, “오래된 근거 · 현재값 미표시”로
    현재값이 아님을 설명한다
- 판정: PASS(자동 회귀 기준). 오래된 값을 현재값처럼 표시하는 경로는 fail-closed로
  차단됐다. 이 결과는 요일·시간대별 모델 정확도를 검증하지 않으며, production 수집
  연속성도 복구하지 않는다. 시간대별 정확도는 historical baseline과 Phase 6 현장 관측
  gate를 통과하기 전까지 주장하지 않는다

## 2026-07-13 — 서울 API 순차 probe와 production concurrency 축소

- 선행 production 증거: fetch concurrency 4로 실행한 마지막 활성 cycle은 첫 5개
  target이 실패해 circuit이 열렸고 나머지 116개는 호출하지 않았다. 별도의 단일 고정
  probe는 성공했으므로 서울 API 전체 장애나 키 오류로 판정하지 않았다
- 실행 환경: 로컬 macOS, read-only 서울 API probe. DB write와 materialize 없음
- 대상: 명동 관광특구, 강남 MICE 관광특구, 동대문 관광특구, 이태원 관광특구,
  잠실 관광특구를 bounded sequential 방식으로 호출
- 실제 결과: 5/5 PASS, 각 응답의 `PPLTN_TIME`은 2026-07-13 09:10
- 조치: 보수적인 다음 production canary를 위해 `POLL_FETCH_CONCURRENCY=1`로 축소
- 판정: 로컬 순차 호출 경로 PASS. GitHub hosted runner의 121개 production cycle과
  1시간 연속 수집은 아직 검증하지 않았으므로 production 복구를 주장하지 않는다.
  이 판정 시점에는 `PRODUCTION_POLL_ENABLED=false`를 유지했으며, 이후 canary 결과는
  아래 별도 기록으로 남긴다

## 2026-07-13 — stale 마스킹 production 배포와 concurrency 1 canary

- 관련 인시던트: [INC-2026-014](INCIDENTS.md#inc-2026-014--오래된-혼잡-스냅샷을-현재값처럼-표시)
- 배포 검증: Vercel 공개 화면과 API에서 stale 카페의 level·score·confidence가 숨겨지고,
  오래된 관측 시각은 근거로 보존되는 것을 확인했다. 오래된 마커는 현재 혼잡 색상으로
  표시되지 않는다
- production 실행: GitHub Actions run `29215956791`,
  `POLL_FETCH_CONCURRENCY=1`
- 실제 결과:
  - 총 44.715초, poll phase 31.815초
  - `saved=121, failed=0`
  - 121개 source `PPLTN_TIME`은 모두 `2026-07-13T00:15Z`
  - fetch는 약 `2026-07-13T00:45Z`에 이뤄져 source 관측 지연은 약 30분
- freshness 판정: 약 30분의 source 지연이 `STALE_WARN_MIN=25`를 초과하므로, 새로
  저장된 121개 현재값도 API에서 모두 stale로 마스킹됐다. 이는 gate가 의도대로 작동한
  결과이며 색상을 표시하기 위해 임계값을 완화하지 않았다
- 운영 상태: canary 성공 뒤 `PRODUCTION_POLL_ENABLED=true`로 활성화했다. 단일 cycle
  성공만 확인했으므로 1시간 연속 6 cycle 검증은 남아 있다. 최근 GitHub schedule 실행도
  `*/10` 선언과 달리 약 1시간 간격으로 관측돼 10분 SLA의 근거로 사용하지 않는다
- 판정: stale 오정보 차단과 단일 production 수집은 PASS. 시간대별 정확도와 운영
  연속성은 미검증이며, source 제공 지연이 현재 25분 제품 약속을 초과하는 문제도
  해결되지 않았다. 제공 지연 분포를 별도로 측정한 뒤 freshness 약속 또는 제품 상태
  모델을 근거 있게 결정한다

## 2026-07-13 — 2시간 참고용 표시 경계 검증

- 배경: 서울 API의 약 30분 source 지연이 25분 운영 경계를 일상적으로 넘겨, 기존
  fail-closed 정책에서는 정상 수집 직후에도 모든 현재값이 숨겨졌다. 사용자가 2시간
  기준에서 실제 표시 결과를 확인하도록 요청했다
- production 관측:
  - 121개 핫스팟의 원본 관측 시각은 `2026-07-13T00:15Z`~`00:35Z` 범위였다
  - 마스킹 전 배포의 동일 viewport 조회에서는 카페 522곳이 모두 level 1(한산)이고
    근거 강도는 모두 낮았다. 이는 시간대별 정확도가 확보됐다는 증거가 아니라 현재
    모델과 당시 원본의 출력 상태다
  - 120분 상한을 적용하면 522곳 모두 표시 대상이지만 `delayed`이며, confidence와
    forecast는 노출 대상이 아니다
- 표시 계약:
  - 관측 나이 25분 이하는 `fresh`
  - 25분 초과 120분 이하는 `delayed`: level·score 표시, 지도와 패널 비중 축소,
    `지연 데이터 · 참고용` 표시, confidence·confidence tier·forecast는 `NULL`
  - 120분 초과는 `stale`: level·score를 포함한 현재 필드 숨김, provenance만 보존
  - 25분은 운영/fresh 경계로 유지하며 `delayed`를 fresh로 부르지 않는다
- health 계약: `stale_warn_min=25`와 `current_display_max_age_min=120`을 함께 반환한다
- 실행 명령:
  - `cd backend && uv run pytest`
  - `cd backend && uv run python -m compileall -q app scripts tests`
  - `cd frontend && npm run typecheck`
  - `cd frontend && npm run build`
- 실제 결과: backend **432 passed**, compileall PASS. frontend typecheck와 production
  build PASS. Vite의 기존 500kB 초과 bundle 경고 외 신규 오류 없음
- 운영 참고: 마지막 scheduled cycle은 대림역의 no-record 응답 파싱으로
  `saved=120, failed=1`인 partial이었다. 별도 수집 안정성 후속 조치가 필요하다
- 판정: PASS(두 단계 표시 계약과 회귀 테스트). 이는 데이터 사용 가능성을 높이는
  표시 정책 변경일 뿐 시간대별 추정 정확도 개선이나 2시간 정확도 보장을 의미하지 않는다

### Production 배포 후 확인

- 배포: Vercel production과 정확한 `busy-cafe.vercel.app` alias에 반영
- 수집 canary: GitHub Actions run `29221794487`, 총 37.898초,
  `saved=121, failed=0`
- 수집 시각: 원본 `PPLTN_TIME=2026-07-13T03:00Z`, 최신 fetch 약 `03:30Z`로
  약 30분 지연
- 동일 viewport 결과: 카페 522곳 모두 `freshness=delayed`; level 1은 455곳,
  level 2는 67곳이며 confidence와 confidence tier는 전부 `NULL`
- health: `current_display_max_age_min=120`, 최신 cycle `complete`, 121/121 저장 확인
- 판정: production에서 delayed 표시 계약 PASS. 원본의 시간대별 정확도는 별도 미검증

## 2026-07-15 — Supabase 보안, egress 최적화와 공개 베타 운영 경계

- production DB 보안:
  - migration `20260715_0008`로 애플리케이션 table 8개와 `alembic_version`의 RLS를
    활성화했다. 정책은 만들지 않고 `anon`, `authenticated`의 table·소유 sequence 권한과
    future default grant를 회수했다. 서버 owner/pooler CRUD는 유지했다.
  - 관련 커밋: `3c21984`, `b99cec0`; PostgreSQL CI run `29345671857` PASS
  - migration 뒤 production poll run `29345742950`은 targets/saved/failed
    `121/121/0`, materialize 34.438초, complete cycle을 기록했다. 공개 health·summary·detail
    API도 HTTP 200이었다.
- Supabase 사용량 `[HUMAN: dashboard 확인]`:
  - Egress `7.306/5GB`, Database Size `0.156/0.5GB`, Cached Egress `0/5GB`
  - Auth MAU, Storage, Realtime, Edge Function은 모두 0이며 현재 아키텍처에서 정상이다.
    Cached Egress는 PostgreSQL cache가 아니라 Storage CDN이므로 DB query 최적화 지표로
    사용하지 않는다.
- egress 원인과 1차 조치:
  - 매 5분 materialize가 12시간 history의 모든 `forecast_json`을 전송하고 최신 121개만
    사용했다. local preview의 forecast JSON 2,847개는 평균 1,482.7 bytes였다.
  - 121곳 × 12시간 × 5분 cadence의 최대 17,424개는 회당 약 25.8MB, 하루 약 7.4GB의
    반복 전송 가능성이 있어 dashboard 증가량의 주된 구조적 원인으로 판정했다.
  - 커밋 `cd28cb3`에서 latest query만 forecast를 읽고 history는 시각·레벨만 읽도록 변경.
    CI run `29380064332` PASS, 첫 production poll `29380185039`은 `121/121/0`, materialize
    27.937초, total 66.820초였다. 직전 34.438초/74.283초보다 각각 18.9%/10.0% 짧았으나
    단일 cycle timing은 보조 증거로만 사용한다.
- egress 2차 조치와 public hardening:
  - 덮어쓸 기존 `hotspot_serving_states`는 PK만 projection하고 과거 trend/forecast JSON을
    읽지 않도록 변경했다. 실제 144-point trend 구조 기준 약 1.02MB/cycle,
    291MB/day, 8.7GB/30일의 불필요한 전송 제거를 예상한다.
  - frontend의 canonical z10 이상 tile보다 넓은 0.5도 초과 bbox를 HTTP 422로 거부한다.
    viewport 5천 건 상한과 함께 arbitrary cache-bust의 광범위 DB scan을 제한한다.
  - `nosniff`, `DENY` frame, `no-referrer`, 최소 Permissions Policy header와 공개
    `/privacy.html`, 비공개 취약점 제보 경로를 추가했다.
- 수익화·측정 결정:
  - ADR-0013에 따라 정확도와 사용량 gate 전에는 광고하지 않는다. 후원은 확인된 URL을
    받은 뒤 정보 영역 텍스트 링크 한 개만 허용하며, 향후 스폰서도 점수·색·순위를
    변경할 수 없다.
  - 사용자가 Vercel Web Analytics를 enable한 직후 기존 배포의
    `/_vercel/insights/script.js`는 HTTP 404였다. commit `bd3d9ee` 재배포 뒤 immutable
    deployment와 정식 alias에서 HTTP 200 JavaScript를 확인했다. dashboard toggle이 아니라
    script HTTP 200과 첫 pageview를 활성 gate로 고정했으며 첫 dashboard pageview 확인은
    `[HUMAN]`으로 남긴다.
- 로컬 검증:
  - backend `731 passed, 1 skipped`, compileall PASS
  - frontend `4 passed`, typecheck와 production build PASS
  - JSON config와 `git diff --check` PASS
- production 배포 검증:
  - CI run `29381047818` PASS. Vercel immutable deployment
    `busy-cafe-pe3dkgbpw-jaemanis-projects.vercel.app`에서 commit `bd3d9ee`, Ready 상태를
    확인한 뒤 `busy-cafe.vercel.app` alias를 전환했다.
  - root, `/privacy.html`, health, summary와 detail은 HTTP 200이었다. Analytics script도
    HTTP 200이며, 0.5도 초과 bbox는 의도대로 HTTP 422였다.
  - root/privacy/API에 HSTS, `nosniff`, frame deny, `no-referrer`와 Permissions Policy가
    적용됐다. Vercel 관리 Analytics asset에는 app header가 적용되지 않지만 자체
    cross-origin resource policy와 장기 cache가 있어 정상으로 판정했다.
  - `budy-cafe.vercel.app`이 단순 alias가 아니라 project domain에 남아 새 배포마다 자동
    재연결되는 원인을 확인했다. project domain에서 제거했고 정식 URL은 HTTP 200,
    오타 URL은 HTTP 404다.
- 현재 판정: 코드·문서·자동 회귀와 production read 경로는 PASS. Analytics dashboard의
  첫 pageview와 egress 2차 최적화의 연속 production poll timing은 추가 확인한다.

### Egress 2차 최적화 연속 production 성능 gate

- 대상 커밋: `65358c3`
- 선행 CI: GitHub Actions run `29381047818`, backend 732 tests와 PostgreSQL migration,
  frontend typecheck/build PASS
- production poll 결과:

  | Run | 저장/대상 | 실패 | materialize | 전체 |
  |---|---:|---:|---:|---:|
  | `29381246607` | 121/121 | 0 | 26.841초 | 65.922초 |
  | `29381448386` | 121/121 | 0 | 27.957초 | 61.019초 |
  | `29381650031` | 121/121 | 0 | 24.933초 | 55.729초 |

- materialize p50은 26.841초, 최대는 27.957초다. 사전 고정한 회귀 상한인 p50
  28.54초, 최대 30.73초를 모두 통과했다.
- 변경 전 production p50 27.177초 대비 p50은 약 1.24% 짧아졌다. 세 cycle 모두
  `status=complete`, `saved=121`, `failed=0`이므로 속도를 위해 수집 완전성을 낮추지 않았다.
- 판정: **PASS**. 기존 `hotspot_serving_states`의 큰 JSON을 읽지 않는 2차 egress
  최적화를 유지한다.

### 잔여 materialize egress와 추가 최적화 보류

- 최소 column projection을 기준으로 한 DB→worker 원시 전송 추정은 cycle당 다음과 같다.
  이 값은 wire protocol과 압축을 포함한 Supabase 청구 실측이 아니며 dashboard가 최종
  판정 기준이다.
  - 12시간 hotspot history 약 0.7~0.9MB
  - 활성 카페 29,917곳의 ID·좌표 약 1.7~1.9MB
  - 기존 cafe score ID 약 0.4~0.5MB
  - 최신 hotspot forecast와 기타 약 0.2MB
- 합계 추정은 약 3.2~3.6MB/cycle이다. 5분 cadence를 단순 환산하면 약
  0.92~1.04GB/day, 27.6~31.1GB/30일이며 public API cache miss egress는 포함하지 않는다.
- 현재 큰 SELECT는 Python score가 실제로 사용하는 최소 컬럼만 읽는다. 더 줄이려면
  PostgreSQL upsert, DB 내부 trend 집계 또는 전체 SQL scoring이 필요하다. 이 변경은
  Python 결정성과 DB CPU/API 경합에 영향을 줄 수 있어 즉시 production에 적용하지 않는다.
- 다음 최적화는 Python 결과를 oracle로 한 shadow parity, 동일 fixture의 허용 오차 비교,
  세 cycle timing과 public API p95 회귀 gate를 통과한 뒤에만 승격한다.
- 후속 확인: 최적화 뒤 24시간 Supabase Egress 증가량과 Analytics 첫 production pageview는
  `[HUMAN]` dashboard 확인이 필요하다. 결제 주기 누적값 7.306GB 자체는 최적화 뒤에도
  즉시 줄어들지 않는다.

### 서울 API partial cycle과 다음 cycle 자동 복구

- production poll run `29381856691`에서 `수유역`, `쌍문역`이 서울 API no-record
  응답을 반환했다. worker는 다른 대상을 계속 처리해 `saved=119`, `failed=2`,
  `status=partial`로 종료했고 monitor run `29381943104`도 latest partial을 정상적으로
  실패 판정했다.
- 직전 complete cycle은 계속 보존됐으며 오래된 상태를 새 complete로 위장하지 않았다.
- 5분 뒤 poll run `29382069570`은 `121/121`, 실패 0으로 자동 복구했다. materialize
  25.667초, total 56.119초였고 health의 latest cycle은 `complete`로 돌아왔다.
  후속 monitor run `29382151199`도 PASS했다.
- 판정: **PASS(실패 격리·감지·자동 회복)**. 한 cycle의 upstream no-record는 기존 partial
  계약으로 처리됐고 별도 코드 변경이나 수동 DB 보정은 하지 않았다. 반복 빈도가 freshness
  SLO를 훼손하면 area별 no-record 비율을 집계해 별도 incident로 승격한다.

## 2026-07-15 — 사용자용 상세 문구와 About 정보 구조

- 사용자 확인: Vercel Analytics dashboard에서 production pageview가 실제로 보이는 것을
  `[HUMAN]` 확인했다. script HTTP 200만 확인했던 이전 pending gate를 PASS로 전환한다.
- 문제: 상세 패널이 장소 release timestamp, `장소 원장 품질 1.00`, 원본 검증 설명과
  관측 나이를 그대로 이어 붙였다. `경계 지역 · 참고용`과 `34분 지연 · 참고용`처럼 같은
  주의 문구도 반복돼, 사용자가 필요한 혼잡도·거리·시각보다 내부 metadata가 앞섰다.
- 변경 계약:
  - API와 DB의 원본 provider, release, confidence와 검증 metadata는 삭제하지 않는다.
  - 상세 패널은 `주변은 여유로 추정돼요`, `뚝섬역 관측 기준 · 846m 거리`,
    `경계 지역`, `34분 전 · 참고용`처럼 의미를 한 번씩만 표시한다.
  - 숫자형 장소 confidence는 매장 존재·혼잡 정확도 확률이 아니므로 사용자 패널에서
    숨기고, provider는 `카카오맵에서 확인한 장소`처럼 짧게 번역한다.
  - 산정 반경·신선도 경계, 카페 원장 구성, provider별 이용조건과 전체 manifest 링크는
    `/about.html`이 소유한다. 지도 header에는 서울시 실시간 도시데이터 기반임과 About
    진입점을 남기고 MapLibre의 필수 베이스맵 attribution은 그대로 유지한다.
  - 확인되지 않은 후원 URL은 노출하지 않는다. About 하단에는 검증된 Buy Me a Coffee
    URL을 나중에 텍스트 링크 한 개로 추가할 위치만 코드 주석으로 남긴다.
- 자동 검증:
  - frontend Vitest 5 passed
  - TypeScript `tsc --noEmit` PASS
  - Vite production build PASS; `dist/about.html`, `dist/privacy.html` 존재 확인
  - 상세 회귀 테스트에서 raw timestamp·`장소 원장 품질` 미노출과 거리·나이 중복 제거 확인
- 격리 배포:
  - 대상 커밋 `de617fa`
  - deployment `busy-cafe-pato8ofcc-jaemanis-projects.vercel.app`, Ready
  - root와 `/about.html`, `/api/sources`, `/api/health` HTTP 200. Preview에 production
    secret을 노출하지 않아 snapshot mode·4,933곳인 것도 의도와 일치
- CI와 production:
  - GitHub Actions run `29383527901`에서 frontend typecheck/build, backend tests,
    compileall, PostgreSQL migration과 schema smoke PASS
  - production deployment `busy-cafe-mmwxys41y-jaemanis-projects.vercel.app`, Ready
  - immutable URL에서 live DB, 카페 29,917곳과 latest `121/121`, `status=complete` 확인 후
    `busy-cafe.vercel.app` alias를 전환
  - canonical root, `/about.html`, `/api/sources`, Analytics script와 health HTTP 200.
    production JS에서 간결한 혼잡·근거·provider 문구가 포함된 것을 확인
- 판정: **PASS(배포 포함)**. 기존 MapLibre attribution control은 삭제·강제 숨김 없이
  유지했다. 모바일에서 실제 패널의 줄바꿈과 스크롤 체감 확인은 HUMAN 수동 점검으로 남긴다.

## 2026-07-15 — Kakao-first 서울 카페 원장과 검색 사전 검증

- 구현 기준 커밋: `3cc82b4`
- 제품 결정: ADR-0014로 Overture 단독 원장을 대체했다. MapLibre/OpenFreeMap과
  cache-first 읽기 경로는 유지하고, Kakao Local CE7 complete snapshot을 서울 recall의
  우선 근거로 사용한다.
- 실측 원장:
  - refresh run `29330994394`: Kakao CE7 33,243곳, API 3,794회, unresolved 0,
    `complete=true`
  - 최초 apply run `29332078493`: dry-run 신규 19,451곳, production 활성 카페는
    10,466곳에서 29,917곳으로 증가. apply 단계는 commit 뒤 취소돼 INC-2026-015로 기록
  - 보존된 complete cache 기준 peer collision 986곳은 좌표·전화번호 공유를 이유로 양쪽을
    차단하던 false-negative 후보였다. 정책 변경 뒤 peer collision은 advisory이고 기존
    canonical strong collision만 blocking이다
- 좌표·존재 안전 계약:
  - Kakao `x=longitude`, `y=latitude`를 fixture와 회귀 테스트로 고정
  - 서울 주소와 `SEOUL_BBOX`를 동시에 통과한 장소만 신규·refresh 대상으로 사용
  - Kakao-origin은 source-primary Place ID로 갱신하고, 다른 origin은 이름+전화,
    이름+주소 등 독립 exact 신호가 2개 이상인 경우만 display field 갱신
  - 250m 초과 좌표 이동은 dry-run 분포·상위 표본과 `--max-large-moves` 명시 상한을
    통과해야 하며 한 번의 미관측은 자동 폐업으로 처리하지 않음
- 검색 계약:
  - `/api/cafes/search`는 활성 서울 DB cache에서 이름·주소를 검색하고 provider API를
    호출하지 않음
  - 2~80자, 기본 20건·최대 50건, SQL wildcard literal 처리, 7개 브랜드 alias allowlist
  - PostgreSQL `pg_trgm` partial GIN index를 migration `20260715_0009`로 설치하고 SQLite는
    기능 호환을 위해 no-op
  - 자유 검색은 `private, max-age=30`, 고정 brand-only 요청만 shared cache 사용
- 사용자 UI:
  - 300ms debounce, 2글자 guard, 빈 결과·오류 상태와 모바일 결과 panel
  - 결과 선택 이동 좌표는 `[longitude, latitude]`; 선택 뒤 기존 상세·Kakao direct link 사용
  - 브랜드 필터는 현재 viewport 마커에도 즉시 적용
  - analytics에는 검색어·카페 ID·주소·좌표를 보내지 않고 결과 수 bucket과 검색 mode만 허용
- Kakao 운영정책 확인: 2026-07-15 공식 운영정책에서 사용자 경험 개선 목적 cache를
  허용하되 최신 상태 미유지를 금지하는 조항과, 정보 복제·출판·검색 디렉터리 입력의
  사전 승낙 조항을 함께 확인했다. raw 응답 재배포는 하지 않으며 상업화 전 명시적 사용
  확인은 `[HUMAN]`으로 남긴다.
- 로컬 자동 검증:
  - backend `753 passed, 2 skipped`, mypy와 compileall PASS
  - frontend Vitest `11 passed`, TypeScript와 production build PASS
  - Alembic offline SQL과 workflow YAML parse, `git diff --check` PASS
- 판정: **PASS(코드·fixture·로컬 gate)**. PostgreSQL production migration, Kakao refresh
  dry-run/apply, score materialize, 공개 검색·브랜드·좌표 표본과 최종 활성 카페 수는 배포
  뒤 별도 기록하기 전까지 PASS로 주장하지 않는다.

## 2026-07-15 — Kakao-first 검색·원장 production 승격

- 기준 구현:
  - 검색·UI·Kakao-first 원장 commit `3cc82b4`
  - production 사전 기록 commit `55d6fbf`
  - 큰 좌표 이동 격리 commit `9d34118`
  - CI run `29384841814`, `29385873423` PASS. 후자는 실제 PostgreSQL migration 적용과
    schema smoke, backend pytest, frontend typecheck/build를 모두 통과했다.
  - production migration run `29384901413` PASS, revision `20260715_0009`
  - Vercel deployment `busy-cafe-6l8j29dhw-jaemanis-projects.vercel.app`을
    `busy-cafe.vercel.app` alias로 승격했다.
- 첫 apply run `29384993264`:
  - complete Kakao snapshot 33,229곳, API 3,797회, unresolved 0
  - 신규 후보 566곳은 상한 2,000 이내였지만 기존 identity의 250m 초과 이동 24곳이
    허용 상한 0을 넘어 apply가 DB mutation 전에 중단됐다.
  - 활성 카페는 29,917곳 그대로였고 안전 실패로 판정했다. 정상 행까지 함께 막은 정책
    결손은 INC-2026-016으로 기록하고 큰 이동 격리 방식으로 수정했다.
- 성공한 apply run `29385929508`:
  - complete Kakao snapshot 33,230곳, API 3,797회, unresolved 0,
    complete leaf 1,882개, split cell 627개
  - dry-run/apply의 candidate ID SHA-256은
    `1672822e9fc0cb58c3f988698f833e56a64b4ef3985e29be18cdc91be125f86a`로 동일
  - 기존 Kakao refresh eligible/seen/missing/rejected = 19,826/19,802/24/5
  - 정상 refresh 계획/적용 = 19,778/19,778
  - 250m 초과 이동 발견/계획/적용/격리 = 24/0/0/24. 격리 사유는
    `large_move_batch_exceeds_allowed_bound`; cafe와 provider 검증 상태를 동결했다.
  - conflict 총 596건 중 blocking 40, advisory 556. 신규 cafe/provider 566/566 적용
  - score materialize 성공. 활성 카페 29,917→30,483
  - step duration: sweep 12분 57초, dry-run 12초, apply 35분 15초, materialize 39초.
    apply 시간은 사용자 요청 경로 장애를 만들지 않았지만 weekly 작업으로 과도해 bulk
    update와 batch progress log를 후속 운영 과제로 남겼다.
- 공개 API 표본:
  - `/api/health`: live, cafes_count 30,483, 최근 complete cycle 121/121/0
  - `루트비커피 성수점`(cafe 29971): 서울 성동구 주소, `[lng, lat]`가 서울 bbox 안,
    Kakao source release `2026-07-15T03:24:42.491325+00:00`, direct detail URL
    `https://place.map.kakao.com/1189682256`, `v1-idw-point` score/level 존재
  - 신규 표본 `스타벅스 을지로경기빌딩점`, `공차 잠실지하상가점`,
    `메가MGC커피 잠실지하상가점`도 검색되고 사전 계산 level이 반환됨
  - 스타벅스 브랜드 결과 50건의 좌표가 모두 `SEOUL_BBOX` 안임을 확인
  - 동일 서울 검색 5회 응답 0.364~0.379초, warm 브랜드 CDN 요청 5회
    0.032~0.041초. 단일 cold/DB busy 표본 4.170초도 관측했으므로 이를 p95 주장으로
    일반화하지 않고 분포 측정 과제로 남긴다.
- 로컬 회귀: backend `755 passed, 2 skipped`, compileall과 `git diff --check` PASS.
- 판정: **PASS** — 이름·주소 검색, 7개 브랜드 필터, Kakao direct detail link, Kakao-first
  누락 회복, 좌표 안전 격리, score 사전 계산이 production에서 확인됐다. 큰 이동 격리 24곳
  원본 대조와 apply bulk 최적화는 미완 후속 작업이다.

## 2026-07-15 — 수도권 생활이동 OA-22300 하루치 offline shadow 실측

### 공식 원천과 다운로드 계약

- 공식 페이지: <https://data.seoul.go.kr/dataList/OA-22300/F/1/datasetView.do>
- 데이터셋명: `수도권 생활이동 (출발-도착지 기준)`. 서울시·KT가 개발했으며 전국
  내/외국인의 수도권 출발·도착 이동을 일별·시간대별, 목적 7종으로 제공한다고 설명한다.
- 페이지 파일 목록 실측: 2023-01-01~2026-06-30 일별 1,277개.
- 다운로드 endpoint:
  `POST https://datafile.seoul.go.kr/bigfile/iot/inf/nio_download.do?useCache=false`
  with `infId=OA-22300`, `seq=260630`, `infSeq=1`. 인증키·로그인·쿠키 없이 성공했다.
- 권리: 서울특별시, 제3저작권자 없음, 공공누리 제1유형(출처표시, 상업적 이용·변경 가능).
- 실파일: `seoul_purpose_admdong3_20260630.zip`, 75,286,481 bytes,
  SHA-256 `11623a80a0cd54f2451ac969538049c527f74f565368cacb1486a0cdcff84a09`.
- ZIP integrity PASS. 단일 내부 파일
  `seoul_purpose_admdong3_final_20260630.csv`, 445,664,262 bytes,
  UTF-8, header 제외 6,414,571행.
- 공식 layout `purpose_od_layout.xlsx`, 17,084 bytes, SHA-256
  `35540d258b7824ae3120f0a35f75bd93f6a180e891cbd6ef48f212de0f241775`.

공식 layout과 실파일로 다음 11개 필드를 확정했다.

| 필드 | 의미 |
|---|---|
| `o_admdong_cd`, `d_admdong_cd` | 출발·도착 행정구역 코드 |
| `st_time_cd`, `fns_time_cd` | 출발·도착 시간 코드 |
| `in_forn_div_nm`, `forn_citiz_nm` | 내/외국인 구분, 국적 |
| `move_purpose` | 1 출근, 2 등교, 3 귀가, 4 쇼핑, 5 관광, 6 병원, 7 기타 |
| `move_dist`, `move_time` | 평균 이동거리 m, 평균 이동시간 분 |
| `cnt` | 이동인구 추정치. 정수 개인 수로 표현하지 않음 |
| `etl_ymd` | 기준일 |

실파일의 시간 코드는 단일 형식이 아니었다. 평시에는 `00..06`, `10..16`, `20..23`의
두 자리 1시간 코드, 출퇴근대에는 `0700/0720/0740`부터 `1940`까지 20분 코드가 사용됐다.
출발·도착 각각 고유 bin은 36개였다. parser는 이 실측 집합만 허용하고 원본 분 단위를
보존한다. 현 shadow는 생활인구 비교를 위해 `floor-to-hour`로 정규화하며 이 결정을 artifact
provenance에 기록한다.

### 행정구역 중심점과 방향의 한계

- OD 기준일 직전 경계로 `vuski/admdongkor ver20260401`을 commit
  `e24f80c67e1fd87fb124afe2e5532f7b1bb5b0d1`에서 고정했다. 원본 GeoJSON 34,641,788
  bytes, SHA-256 `6a63d079ba8af4701ab200ad0b54ebdea8689808b6e0e9f17973b9ba7883dc6a`.
- WGS84 경계를 EPSG:5179로 투영해 면적 중심점을 계산한 뒤 WGS84로 되돌렸다. 결과는
  서울 행정동 427개 + 전국 시군구 255개 = 682개, artifact SHA-256
  `bf84a8a4d7d3df2e8798a43f87d30b2f5b7cac093797484458e0425a918c352f`.
- 하루 OD에 실제 등장한 코드는 서울 행정동 427개 + 비서울 시군구 230개 = 657개이며
  657/657 모두 exact code로 매칭됐다. 지오코딩이나 이름 추정은 사용하지 않았다.
- `admdongkor` 가공분은 CC BY 4.0, 원 경계는 SGIS 공공누리 제1유형이다. 현재 artifact는
  local/gitignored research 자료이고 공개 게시 전 두 출처표시를 모두 추가해야 한다.
- 방향은 출발 중심점→도착 중심점의 이동인구 가중 합성 방향이다. 실제 이동 궤적,
  순간 진행방향, 골목·도로·고속도로 통과를 뜻하지 않는다. 구역 내 이동은 총량에는
  포함하지만 방향에서는 제외하며, 상쇄 정도를 `direction_strength`로 별도 보존한다.

### 구현 중 발견한 시간축 오류

최초 엔진은 유입과 유출을 모두 도착시각으로 묶었다. 이 경우 `net`이 같은 시간대 사건을
비교하지 않아 잘못된 순유입이 된다. 실 artifact 게시 전에 발견해 다음 계약으로 수정했다.

- 유입·목적·접근 방향: 목적지 `arrival_hour`
- 유출: 출발지 `departure_hour`
- 결과: 두 키의 합집합. 유출만 있는 구역·시간도 보존
- 구역 내 이동: 도착시 유입, 출발시 유출에 각각 포함; 방향에서는 제외

서로 다른 출발·도착 시간과 outbound-only fixture가 이 계약을 회귀 테스트로 고정한다.
공개 영향은 없으며 상세 기록은 INC-2026-017에 남겼다.

### 하루치 3개 시간대 실행

원본 하루 전체를 엄격 파싱하고, 08·14·18시가 출발 또는 도착인 행을 선택해 아침·낮·저녁
shadow를 만들었다. 요청 경로, DB, production API와 frontend는 변경하지 않았다.

재현 명령의 핵심은 다음과 같다.

```bash
cd backend
uv run python scripts/build_purpose_od_shadow.py \
  --input data/od/seoul_purpose_admdong3_20260630.zip \
  --centroids data/od/purpose_od_centroids_ver20260401_v1.json \
  --target-date 2026-06-30 \
  --source-version oa-22300-20260630 \
  --schema-version oa-22300-purpose-od-csv-v1 \
  --output data/od/purpose_od_shadow_20260630_h08_h14_h18.json \
  --hour 8 --hour 14 --hour 18
```

- dry-run/apply 모두 source 6,414,571행 전수 파싱, 선택 2,392,689행, 결과 1,970개
  `zone × hour` group.
- centroid code/row/추정인구 coverage는 모두 1.0, 누락 origin/destination code 0.
- dry-run과 apply artifact SHA-256 동일:
  `e7ea3320fc169304b219418a8bac7e580c8def805c12a4ee825a4ee1bd438451`.
- apply 실행: 69.86초, max RSS 232,456,192 bytes, output 약 1.7MB. 원본과 결과는
  `backend/data/` 아래 gitignored이며 Supabase에 원시행을 적재하지 않았다.
- 초기 Sequence 구현은 선택 3시간도 과도한 객체 복제를 일으켰다. exact `Decimal`
  streaming accumulator로 바꿔 입력 순서 독립성과 bounded aggregate state를 함께 유지했다.
- 전체 backend 회귀는 `821 passed, 2 skipped`, 대상 모듈 mypy·compileall과
  `git diff --check` 모두 PASS했다.
- 기준 커밋 `d5a49cf`의 GitHub Actions CI run `29392020000`에서 frontend
  typecheck/build, backend pytest·compileall, 실제 PostgreSQL migration·schema smoke가
  모두 PASS했다.

서울 행정동 합계와 대표 표본은 다음과 같았다. `cnt` 추정치이므로 반올림한 탐색값이다.

| 시각 | 서울 유입 | 서울 유출 | 순유입 | 해석 |
|---:|---:|---:|---:|---|
| 08시 | 2,130,513 | 1,701,064 | +429,448 | 업무지역 출근 유입이 뚜렷 |
| 14시 | 1,216,595 | 1,220,351 | -3,757 | 전체 서울은 대체로 균형 |
| 18시 | 1,887,680 | 1,932,426 | -44,746 | 업무지역 유출·주거지역 귀가 유입 |

- 08시 순유입 상위는 여의동 +57,247, 가산동 +41,670, 역삼1동 +40,283이었다. 각각
  유입 목적에서 출근이 약 85%, 89%, 83%로 가장 컸다.
- 14시 서교동은 유입 13,597, 유출 11,072, 순유입 +2,525였지만 목적 7 `기타`가
  약 77%였다. 목적 코드만으로 카페 방문 수요라고 해석할 수 없다.
- 18시 여의동 -31,559, 가산동 -29,191, 역삼1동 -25,060으로 아침 업무지역 유입의
  역방향이 나타났다.
- 성수·서교·연남의 합성 방향 강도는 대체로 0.07~0.32로 낮았다. 단일 화살표보다 여러
  방향의 상쇄가 큰 경우가 많아 strength 없는 방향 표시는 금지한다.

판정: **PASS(feasibility only)**. 과거 OD를 결정적으로 처리해 시간대별 유입·유출·목적·
평균 접근방향을 만들 수 있다. 그러나 하루치 자체는 예측력이나 카페 혼잡 정확도 근거가
아니다. 여러 주의 같은 요일·공휴일·시간대를 만든 뒤 생활인구 단독 대비 citydata/현장
라벨의 rolling-origin 개선을 확인할 때만 shadow feature로 채택한다. 공개 v1은 변경하지
않는다.

## 2026-07-15 — 수도권 생활이동 OA-22300 다일 반복성 pilot

### 사전등록과 입력 완전성

- 결과 확인 전 일반 화요일 `2026-06-09/16/23/30`, 08·14·18시, 서울 행정동 427개와
  scalar·목적·방향별 threshold를
  [`2026-07-13-historical-baseline-and-prior-art.md`](research/2026-07-13-historical-baseline-and-prior-art.md)에
  고정했다. 어린이날 `05-05`, 토요일 `06-27`, 일요일 `06-28`은 유형별 반복 표본이
  1일뿐이므로 verdict에서 제외하고 기술통계에만 사용했다.
- 7개 ZIP은 공식 OA-22300 endpoint에서 받은 뒤 파일명·Content-Length·ZIP integrity·내부
  기준일을 확인했다. 모든 artifact는 원본 전체를 엄격 파싱했고 centroid 코드·행·추정인구
  coverage 1.0, 관측 코드 657/657 exact match, 누락 origin/destination 0이었다.

| 날짜 | 역할 | 원본 행 | 08·14·18시 선택 행 | shadow SHA-256 |
|---|---|---:|---:|---|
| 2026-05-05 | 기술통계 | 5,192,049 | 1,682,899 | `bb02e86e…a4fb1` |
| 2026-06-09 | 화요일 | 6,503,644 | 2,435,638 | `d3f32015…4f38` |
| 2026-06-16 | 화요일 | 6,474,893 | 2,422,887 | `6024284e…ca02` |
| 2026-06-23 | 화요일 | 6,480,200 | 2,415,896 | `8314ff6a…e70` |
| 2026-06-27 | 기술통계 | 5,999,425 | 2,000,062 | `020ac759…9549` |
| 2026-06-28 | 기술통계 | 5,172,806 | 1,684,956 | `3845647d…31ed` |
| 2026-06-30 | 화요일 | 6,414,571 | 2,392,689 | `e7ea3320…8451` |

전체 source/artifact SHA는 결정적 report의 `inputs`에 보존했다. 3개 신규 화요일 artifact는
dry-run/apply/final file SHA가 각각 일치했고 실행은 회당 약 71~74초, max RSS 약
221~225MiB였다. 세 기술통계 artifact도 같은 검증을 통과했으며 회당 약 56~66초,
max RSS 약 225MiB였다.

### 실행과 결과

기준 구현 commit은 `6cdd106`이다. 외부인 재현 명령은 로컬 전용 wrapper 없이 다음 형태다.

```bash
cd backend
uv run python scripts/run_purpose_od_stability.py \
  --weekly-artifact data/od/purpose_od_shadow_20260609_h08_h14_h18.json \
  --weekly-artifact data/od/purpose_od_shadow_20260616_h08_h14_h18.json \
  --weekly-artifact data/od/purpose_od_shadow_20260623_h08_h14_h18.json \
  --weekly-artifact data/od/purpose_od_shadow_20260630_h08_h14_h18.json \
  --descriptive-artifact data/od/purpose_od_shadow_20260505_h08_h14_h18.json \
  --descriptive-artifact data/od/purpose_od_shadow_20260627_h08_h14_h18.json \
  --descriptive-artifact data/od/purpose_od_shadow_20260628_h08_h14_h18.json \
  --output ../docs/research/artifacts/purpose-od-stability-20260609-20260630.json
```

- dry-run과 apply report SHA-256 동일:
  `39cbe77ce4f6eed592bbaa69f18515c9f342cad5b2cce4860ff0b32b2e7cc32c`
- scalar: **conditional**. net Spearman median/minimum 0.96955/0.59424,
  inbound median 0.99465, outbound median 0.99512, 상·하위 10% Jaccard median
  0.62264/0.82979. full-support의 net minimum 0.60을 사전 기준대로 통과하지 못했다.
- 14시 net Spearman은 세 pair에서 0.69302, 0.65048, 0.59424였고 상위 10% Jaccard는
  0.43333, 0.34375, 0.30303이었다. 결과 뒤 threshold를 바꾸지 않았다.
- 목적: **stable**. 동일 요일·시각 pair의 Jensen–Shannon distance median/P90은
  0.00717/0.01899였다. 목적 7 `기타`가 최대 60.37%여서 카페 수요로 해석하지 않는다.
- 방향: **usable as challenger**. 모든 pair×hour에서 eligible 비율 minimum 34.89%,
  각도차 median maximum 6.11°, 각도차 P90 maximum 14.70°, 45° 이내 비율 minimum
  100%, strength 차이 median maximum 0.0312였다. 실제 도로 궤적 정확도 주장은 금지한다.
- decision: scalar prior 후보 false, 목적·방향 feature 후보 true. 정확도 주장과 공개 모델
  승격은 false이며 API·DB·frontend·production v1은 변경하지 않았다.

### 구현 검증과 near miss

첫 실결과를 읽기 전 코드 검토에서 사전등록의 “모든 pair” 방향 조건 중 두 항목을 pair
통계의 중앙값으로 잘못 합친 구현을 발견했다. pair 통계의 maximum으로 수정하고 중간 한
pair만 실패하는 회귀 테스트를 추가한 뒤 real-data report를 처음 생성했다. 상세는
INC-2026-018에 기록했다.

- backend: `828 passed, 2 skipped`; 대상 mypy PASS; app/scripts/tests compileall PASS
- frontend: TypeScript PASS, production build PASS. 기존 500kB chunk 경고만 존재하며 이번
  offline 변경과 무관하다.
- `git diff --check` PASS
- 기준 커밋 `98a9cea`의 GitHub Actions CI run `29393332962`에서 frontend
  typecheck/build, backend pytest·compileall, 실제 PostgreSQL migration·schema smoke가
  모두 PASS했다.

판정: **PASS(repeatability pilot only)**. OD의 동일 화요일 목적 구성과 거친 합성 방향은
반복됐지만, 한낮 순유입 순위는 불안정했고 실제 활동도·보행 혼잡·카페 좌석 정확도는 아직
검증하지 않았다. 다음 gate는 OA-22784와 같은 `2026-06-30` 비교, 이후 7월 OA-22300과
동일 날짜 citydata 비교, 마지막으로 Phase 6 현장 라벨에서 생활인구 단독 대비 개선 확인이다.

## 2026-07-15 — OA-22784 생활인구 ↔ OA-22300 동일 날짜 관계 screen

### 원본 확보와 계약 실측

- OA-22784 공식 페이지를 다시 조회해 일별 파일은 `2026-07-01`부터, 6월은 월파일만
  제공됨을 확인했다. 존재하지 않는 `seq=260630` 요청은 attachment가 아닌 응답으로
  fail-closed됐고 파일을 만들지 않았다.
- 공식 월파일 `250_LOCAL_RESD_202606.zip`: 448,638,322 bytes, SHA-256
  `953e9790e174220eee0d028f1ae393ccd3e5fd88579db32b5b4a60cf2ba13d62`. ZIP 30개 member
  integrity PASS.
- 내부 `250_LOCAL_RESD_20260630.csv`: 58,627,465 bytes, CP949, header 제외
  253,946행, SHA-256
  `857b6273d58a83949e653a95720207be0344c740fe4d986fad29b3f4033024ba`.
- 실파일에서 `(date,hour,CELL_ID)` 중복 group 44,837개,
  `(date,hour,administrative_dong_code,CELL_ID)` 중복 0개를 확인했다. 같은 cell은 행정동
  경계에서 여러 부분행을 가진다.
- `생활인구합계='540.'` 같은 trailing decimal token은 2,477개였고, `*` 또는
  `[0-9]+(?:\.[0-9]*)?` 밖의 token은 0개였다. parser와 DuckDB validator에 실측 문법을
  반영했다.

### fail-closed v1과 v2 재등록

첫 두 실행은 각각 잘못된 CELL_ID 유일키와 trailing decimal regex에서 상관 계산 전에
중단됐다. 입력계약 수정 뒤 v1은 08시 zone-cell Jaccard 0.985120719가 사전 하한 0.99에
미달해 다시 중단됐다. threshold를 결과 뒤 완화하지 않고 v1을 무효로 기록했다.

진단 결과 07→08시 bare cell은 8,536→8,535개, 이탈 6·진입 5, Jaccard 0.998712였다.
반면 zone-cell 부분행은 이탈 81·진입 78이었다. geometry와 행정동 부분행 존재 변화를
분리해야 함을 확인해 상관 계산 전 v2 계약을 commit `4521482`에 고정했다.

- bare-cell 인접 Jaccard ≥0.99만 geometry gate로 사용
- zone-cell pair 합집합에서 absent 0, masked 2를 primary로 사용
- 한 요인씩 `mask=0/3`, `absent=2/3` 네 sensitivity variant 추가
- 날짜 2026-06-30, 08·14·18시, exact code, Spearman과 기존 screening threshold는 유지
- one-day·통신계열 공통 편향 때문에 accuracy·causality·public promotion은 항상 false

### v2 결과

실행기는 commit `a37793c`, hash-seed 결정성 수정은 `3dd4be6`이다. 결정적 report:
[`research/artifacts/living-od-same-day-20260630.json`](research/artifacts/living-od-same-day-20260630.json),
SHA-256 `f65313105d2aa62d8991d2a1d16737d994f60f20ceeba60cba665b0940e716f7`.

| 지표 | 08시 | 14시 | 18시 |
|---|---:|---:|---:|
| 행정동 exact coverage | 427/427 | 427/427 | 427/427 |
| bare-cell Jaccard minimum | 0.99871 | 0.99860 | 0.99778 |
| primary `net(h)` ↔ `LP(h+1)-LP(h)` rho | 0.92870 | 0.59438 | 0.90204 |
| secondary `net(h)` ↔ `LP(h)-LP(h-1)` rho | 0.91954 | 0.23811 | 0.83956 |
| secondary gross flow ↔ stock rho | 0.89603 | 0.95610 | 0.93839 |

- primary 세 rho 모두 양수, median 0.90204로 verdict `screening`.
- 5개 mask/absence variant 모두 같은 verdict. 시간별 rho range maximum 0.000503으로
  `imputation_sensitive=false`.
- 최초 v2 dry-run/apply는 verdict가 같아도 report SHA가 `9399…`/`648d…`로 달랐다.
  정렬되지 않은 set float 합산을 발견해 두 artifact를 폐기하고 cell ID 정렬과 서로 다른
  `PYTHONHASHSEED` subprocess 테스트를 추가했다. 수정 뒤 dry-run/apply SHA는 모두 위
  `f653…16f7`로 일치했다. 상세는 INC-2026-020.
- backend 전체 `841 passed, 2 skipped`; 대상 7파일 mypy PASS; app/scripts/tests compileall과
  `git diff --check` PASS.
- frontend TypeScript와 production build PASS. 기존 500kB chunk warning만 있으며 이번
  offline 변경과 무관하다.

판정: **PASS(cross-source relationship screen only)**. OD 순유입과 다음 시간 생활인구 재고
변화의 구조적 관계는 확인했지만 두 데이터 모두 통신계열이고 하루치뿐이다. public v1 API,
DB, frontend, confidence는 변경하지 않았다. `historical_feature_candidate=false`를 유지한다.
다음은 06-09/16/23 held-out 화요일 반복과 06-27/28 주말 기술통계이며, compactor의 행정동
부분행·부분 마스킹 schema는 INC-2026-019 미완 조치로 별도 해결한다.

## 2026-07-15 — OA-22784 ↔ OA-22300 held-out 화요일 반복

### 명령과 입력 역할

사전등록 뒤 다음 명령으로 같은 single-day v2 evaluator를 역할별로 반복했다.

```bash
cd backend
uv run python scripts/run_living_od_repeats.py \
  --pair held_out 2026-06-09 data/living_population/250_LOCAL_RESD_20260609.csv data/od/purpose_od_shadow_20260609_h08_h14_h18.json \
  --pair held_out 2026-06-16 data/living_population/250_LOCAL_RESD_20260616.csv data/od/purpose_od_shadow_20260616_h08_h14_h18.json \
  --pair held_out 2026-06-23 data/living_population/250_LOCAL_RESD_20260623.csv data/od/purpose_od_shadow_20260623_h08_h14_h18.json \
  --pair descriptive_only 2026-06-27 data/living_population/250_LOCAL_RESD_20260627.csv data/od/purpose_od_shadow_20260627_h08_h14_h18.json \
  --pair descriptive_only 2026-06-28 data/living_population/250_LOCAL_RESD_20260628.csv data/od/purpose_od_shadow_20260628_h08_h14_h18.json \
  --pair discovery 2026-06-30 data/living_population/250_LOCAL_RESD_20260630.csv data/od/purpose_od_shadow_20260630_h08_h14_h18.json \
  --output ../docs/research/artifacts/living-od-held-out-repeats-202606.json \
  --apply
```

- `held_out`: 결과를 보지 않고 고정한 일반 화요일 `06-09/16/23`만 confirmatory verdict에
  사용했다.
- `descriptive_only`: 토요일 `06-27`, 일요일 `06-28`은 유형별 하루라 수치만 보존하고
  verdict에서 제외했다.
- `discovery`: 계약을 만든 `06-30` 결과는 report에 보존하되 held-out 판정에서 제외했다.
- OA-22784 CSV는 해당 날짜의 생활인구 재고, OA-22300 artifact는 같은 날짜의 행정동
  순유입이다. 08·14·18시, exact 서울 행정동 427개, primary
  `net(h)` ↔ `LP(h+1)-LP(h)`, 사전등록한 5개 mask/absence variant를 그대로 사용했다.

결정적 report는
[`research/artifacts/living-od-held-out-repeats-202606.json`](research/artifacts/living-od-held-out-repeats-202606.json),
82,775 bytes, SHA-256
`2ba2485e74572076d7839d86cc82ade457aa8ef245c29b49866fa868443e6ea9`다.

### 핵심 수치와 판정

| held-out 날짜 | 08시 rho | 14시 rho | 18시 rho | single-day verdict |
|---|---:|---:|---:|---|
| 2026-06-09 | 0.933321 | 0.645739 | 0.894579 | screening |
| 2026-06-16 | 0.944993 | 0.646217 | 0.906342 | screening |
| 2026-06-23 | 0.929796 | 0.637114 | 0.896220 | screening |

- primary rho 9/9가 정의되고 모두 양수였다. pooled median은 0.896220, minimum은
  0.637114이며, 세 날짜 모두 single-day `screening`이어서 사전등록한 최종 verdict는
  **supported**다.
- 모든 pair·시각의 exact code coverage는 427/427였다. 전체 bare-cell Jaccard minimum은
  0.997775, held-out imputation rho range maximum은 0.000347로
  `imputation_sensitive=false`였다.
- 토요일 primary rho는 0.785102/0.583985/0.671233, 일요일은
  0.771764/0.612807/0.756499였지만 기술통계일 뿐 주말 일반화 근거가 아니다.
- dry-run과 `--apply`의 report SHA가 모두 `2ba2485e…e6ea9`로 일치했다. 독립 재계산에서도
  report 안의 6개 pair SHA가 각 pair payload와 모두 일치했다.
- 코드 회귀: backend **846 passed, 2 skipped**, 대상 4파일 mypy와 app/scripts/tests
  compileall PASS. frontend TypeScript와 production build PASS. 기존 500kB chunk warning과
  Starlette/httpx deprecation warning 1건만 유지됐고 `git diff --check`도 PASS했다.

판정: **PASS(held-out cross-source repeatability only)**. 같은 월의 일반 화요일에서 OD
순유입과 다음 시간 생활인구 재고 변화의 행정동 순위 관계가 반복됐다. 그러나 날짜들이 같은
OA-22784 월 릴리스에 속해 독립 source release가 아니고, OA-22784와 OA-22300 모두
통신계열 추정치라 공통 편향 가능성이 있다. 이는 실제 활동도·보행 혼잡·카페 좌석 정확도나
독립 ground truth가 아니다. 다른 월 rolling-origin과 Phase 6 현장 라벨 전에는
`historical_feature_candidate=false`, 정확도 주장·공개 promotion=false이며 public v1 API,
DB, frontend, confidence는 변경하지 않는다.

## 2026-07-15 — OA-22784 fragment-aware compact v2와 activity consumer

### 원본 전수 계약과 compact v2

- 입력은 공식 `250_LOCAL_RESD_20260630.csv`, 253,946 fragment다. SHA-256은
  `857b6273d58a83949e653a95720207be0344c740fe4d986fad29b3f4033024`다.
- `(date, hour, cell_id)`로 합치면 204,780 cell observation이다. 둘 이상의 행정동
  fragment를 가진 observation은 44,837개, 부분 마스킹은 4,355개, 전부 마스킹은
  8,952개, observation당 최대 fragment는 4개였다.
- compact schema version은 2, query version은
  `oa-22784-cp949-cell-fragments-json-v2`다. 각 행은 exact Decimal `known_total`,
  `fragment_count`, `masked_fragment_count`와 canonical `fragments_json`을 보존한다.
  `known_value`는 exact decimal string 또는 `null`이고 `total_raw`, 행정동 코드,
  마스킹 여부와 source filename을 함께 유지한다. 전부 마스킹이면 `known_total=0`이지만
  이를 관측 점값으로 해석하지 않는다.
- 첫 real apply는 DuckDB의 CP949 CSV decode 경로에서 native SIGSEGV로 종료됐다. 입력
  검증을 완화하지 않고 Python stdlib의 strict CP949 decode로 전용 임시 디렉터리의 UTF-8
  임시 파일을 만든 뒤 DuckDB가 읽도록 변경했다. 성공·실패 모두 임시 파일을 정리하며
  검증 뒤 잔여 temp file은 0개였다.
- 대표 4-cell allowlist의 real apply는 96 cell observation/168 fragment를 만들었다.
  부분 마스킹 13개, 전부 마스킹 31개였고 Parquet SHA-256은
  `0e7d26e8fcc083a6ba1165c34f2f8ad1c05ad5621d2c93adf0eba70f5ddf5a97`다.
  sidecar manifest는 schema/query, 원본·allowlist·output SHA/size/row count와 집계 계약을
  보존하며 Parquet과 함께 원자 게시된다.

### activity consumer와 마스킹 abstention

- consumer는 sidecar manifest를 필수로 읽고 schema/query/output filename/size/SHA/row
  count를 실제 Parquet과 대조한다. v1, mixed query, missing/extra column, manifest 변조와
  중복 cell identity를 fail-closed한다.
- 모든 fragment에서 8자리 ASCII 행정동 코드, canonical JSON, exact Decimal 합,
  fragment/masked count, 원본 token과 source filename을 재검증한다. 원본 fragment JSON과
  검증된 manifest evidence는 activity artifact provenance에 보존한다.
- target 상태는 `complete`, `partially_masked`, `masked`, `missing`으로 분리한다. 부분 또는
  전부 마스킹된 history는 `HistoricalCellObservation(total=None, masked=True)`로 전달한다.
  현재 target이 부분/전부 마스킹이면 suppression bound를 가정하지 않고
  `baseline_only` 또는 `unsupported`로 abstain하며 0/2/3 점대치나 point/range anomaly를
  생성하지 않는다.
- 대표 Parquet의 2026-06-30 00시 real consumer dry-run/apply는 4 features를 만들었고
  상태는 complete 1, partially masked 1, masked 2였다. 두 실행의 직렬화가 일치했고
  artifact SHA-256은
  `7576237fdd1a87d318b4de04d3f8283ebf8eceab9046db328d41308ab16d89d4`다.

재현 검증 명령은 로컬 wrapper 없이 다음 표준 명령을 사용했다.

```bash
cd backend
uv run pytest -q tests/test_compact_living_population.py tests/test_build_activity_artifact.py
uv run pytest -q
uv run --with mypy mypy app/config.py scripts/compact_living_population.py scripts/build_activity_artifact.py tests/test_compact_living_population.py tests/test_build_activity_artifact.py
uv run python -m compileall -q app scripts tests
```

- focused producer/consumer: 64 passed
- full backend: 884 passed, 2 skipped
- 변경 5파일 mypy와 app/scripts/tests compileall: PASS
- frontend TypeScript와 production build: PASS. 기존 500kB chunk warning만 유지
- `git diff --check`: PASS

판정: **PASS(fragment-preserving offline pipeline)**. 행정동 경계 fragment와 부분 마스킹을
손실 없이 보존하고 consumer가 불확실한 현재값에서 abstain함을 real apply와 회귀 테스트로
확인했다. 이는 historical/activity shadow 입력 계약의 수정이며 public v1 API, DB,
frontend, production score와 confidence는 변경하지 않는다.

## 2026-07-15 — OA-16095 면적 단위·전수 프로파일과 capacity shadow

- 관련 커밋: `8840ac3`
- 입력: 서울시 휴게음식점 인허가 OA-16095 `LOCALDATA_072405` 전체
  146,286행
- 단위 근거:
  - [LOCALDATA 관리자 Q&A `nttId=1011`](https://www.localdata.go.kr/devcenter/bbs/devQnaDetail.do?nttId=1011&bbsId=B0000100&menuNo=20003)
  - [식품위생법 시행규칙 별지 37](https://www.law.go.kr/LSW/flDownload.do?gubun=&flSeq=160975813&bylClsCd=110202)
  - [식품위생법 시행규칙 별지 34](https://www.law.go.kr/LSW/flDownload.do?gubun=&flSeq=160975791&bylClsCd=110202)
- 행정상 `FACILTOTSCP`(시설총규모)와 `SITEAREA`(소재지면적)의 단위는 모두
  `㎡`로 확인했다. 다만 machine-readable OpenAPI schema에는 unit metadata가 없어
  단위의 행정적 근거와 기계 스키마 한계를 provenance에 함께 보존했다.
- 영업 중 커피숍은 14,663곳이다. `FACILTOTSCP`는 14,663/14,663이 숫자,
  14,649건이 양수, 14건이 0㎡였다. 양수 분포는 p5 10㎡, p50 42.9㎡,
  p95 234㎡, 최댓값 1,124.36㎡였다.
- 유효한 서울 좌표는 14,364건, 주소는 14,663건이다. 두 면적 필드가 모두
  숫자인 14,646건 중 14,581건은 같고 65건은 다르다. 의미가 다른
  `SITEAREA`를 `FACILTOTSCP` 대체값으로 사용하지 않는다.
- 집계 artifact:
  [`research/artifacts/seoul-refreshment-permit-area-profile-20260715.json`](research/artifacts/seoul-refreshment-permit-area-profile-20260715.json),
  SHA-256 `fab4b97de606ef756fb9fac498beed67779f5eb14f979d8952bc805e6e62f672`.
- capacity shadow는 `regional_demand × (42.9㎡ / max(FACILTOTSCP, 10㎡))^alpha`를
  사용하며 `alpha={0.25,0.5,0.75,1.0}`, 기본값 0.5를 사전 등록했다.
  같은 수요에서 10㎡는 약 2.07배, 42.9㎡는 1배, 234㎡는 약 0.43배의
  상대 pressure를 만든다. 이 값은 좌석 점유율나 정확도 확률이 아니다.

판정: **PASS(unit/profile and offline shadow only)**. 면적 단위와 분포는 확정했지만
독립 매장 매칭과 Phase 6 held-out 검증이 남았다. 공개 `v1-idw-point`, API,
DB, frontend는 변경하지 않았다.

## 2026-07-15 — Production capacity 매칭 gate 실측

- 첫 production report run
  [`29401122318`](https://github.com/Jaemani/BusyCafe/actions/runs/29401122318)의 verified
  4,637건은 모두 `origin_provider=seoul_refreshment_permits`였다. 인허가에서 파생한
  카페를 같은 인허가로 다시 검증한 자기매칭이므로 결과를 폐기했다.
- 커밋 `3ab9241`에서 동일 source 카페를 spatial candidate 구성 전에 전체
  제외하고 제외 수를 report에 남겼다.
- 후속 run
  [`29401367671`](https://github.com/Jaemani/BusyCafe/actions/runs/29401367671)은 active 카페
  30,483곳 중 same-source 5,064곳을 제외한 25,419곳과 유효 커피숍 인허가
  14,350건을 비교했다. 기존 strict rule에서 verified 0, missing 14,350이었다.
- 결과를 보고 threshold를 완화하지 않고 커밋 `0cef73d`에서 ID·이름·주소·전화를
  출력하지 않는 단계별 집계 진단을 추가했다.
- v2 run
  [`29401829561`](https://github.com/Jaemani/BusyCafe/actions/runs/29401829561)에서 50m 이내
  독립 카페가 있는 인허가는 10,601/14,350(73.87%)이었다. 정확한 이름은
  383건, 전화는 160건의 인허가에서 일치했지만 기존 전체 주소 문자열 일치는
  0건이었다. 근접 후보 부족이 아니라 인허가의 층·호·법정동 후치와 소비자 POI
  주소 표현 차이가 병목임을 확인했다.
- [도로명주소법 시행령 제6조](https://www.law.go.kr/법령/도로명주소법시행령)의 표기 순서와
  [공식 주소 API 구성요소](https://business.juso.go.kr/jst/jstRoadNmAddrApiSearch)를 근거로
  커밋 `29b55db`에서 `seoul-road-address-components-v1`을 고정했다. 서울 별칭,
  25개 구, 도로명, 건물 본번·부번만 exact 구성요소로 비교하고 건물번호 뒤의
  층·호·동·괄호 참고항목만 identity에서 제외한다. 파싱 불가, 비서울,
  지번, 알 수 없는 후치, 깨진 괄호는 모두 abstain하며 원본은 보존한다.
- 단일 인허가 안에서만 후보가 하나인 것으로 충분하지 않다. 두 인허가가 같은
  카페를 점유하면 관련 결과를 모두 ambiguous로 강등하는 전역 1:1 gate를 같은
  커밋에 추가했다. report는 reverse collision의 인허가·카페 수만 출력한다.
- 커밋 `29b55db` 검증: backend 988 passed, 2 skipped, 1 warning; 변경 5파일
  mypy PASS; 변경 Python 파일 Ruff PASS; app/scripts/tests compileall PASS; frontend
  TypeScript와 production build PASS. 기존 1,088.85kB JS chunk warning만 유지됐다. GitHub
  [CI run `29403042381`](https://github.com/Jaemani/BusyCafe/actions/runs/29403042381)에서도
  backend·frontend·실제 PostgreSQL migration smoke가 모두 통과했다.
- 같은 커밋의 v3 production run
  [`29403047910`](https://github.com/Jaemani/BusyCafe/actions/runs/29403047910)은 기존과 같은
  독립 카페 25,419곳과 인허가 14,350건을 read-only로 비교했다. 50m 안에서
  구조화 건물주소가 일치한 후보는 5,005 pair/3,204 permit, 주소+이름은
  32 pair/32 permit, 주소+전화는 10 pair/9 permit였다. 전역 reverse collision은
  2개 cafe/4개 permit에서 발생해 관련 결과를 모두 중단했다.
- 최종 결과는 verified 36, ambiguous 5, missing 14,309였다. 근거별로는 name-only
  30, phone-only 6, both 0이고 provider는 Kakao 24, Overture 12였다. 연결된
  `FACILTOTSCP`는 6~442.2㎡, p50 63.88㎡였다. report에는 ID·이름·주소·전화를
  출력하지 않았다.

판정: **BLOCKED(independent precision gate)**. 도로명주소 구성요소 일치를 별도
challenger로 검증하되, 50m·정확한 이름 또는 전화·단일 후보 조건을 유지한다.
HUMAN precision sample과 전역 1:1 충돌 검증 전에는 capacity를 공개 점수에 쓰지 않는다.
관련 사건은 INC-2026-021에 기록했다.

## 2026-07-15 — Production ingest SLO와 nowcast shadow 추적

### Ingest SLO

- analyzer schema v2 관련 커밋: `a542e4a`
- 24시간 run
  [`29401552844`](https://github.com/Jaemani/BusyCafe/actions/runs/29401552844): terminal 289,
  complete 273, failed 14, partial 2. Complete rate 94.4637%, target 저장 성공률
  95.1471%였다. Cycle duration은 p50 59.093초, p95 121.496초, 최댓값
  141.643초였고 hotspot coverage는 121/121이었다.
- source observation lag는 p50 32.660분, p95 32.881분, 최댓값 33.104분이었다.
  이 수치는 `fetched_at-observed_at`이며 HTTP 응답 시간이나 API worker 실행 시간이 아니다.
- full failure 14건은 첫 5개 hotspot에서 각각 4회 `ConnectTimeout` 후 circuit가
  열린 cycle이었다. Partial 2건은 no-record/parse 응답으로 120/121,
  119/121개를 저장했다. DB commit이나 materialize 실패 증거는 없었다.
- 168시간 run
  [`29402148225`](https://github.com/Jaemani/BusyCafe/actions/runs/29402148225)은 운영 시작
  전 window head 5,539분을 포함했으므로 7일 연속 SLO로 판정할 수 없다. 관측된
  terminal 553건의 complete rate는 94.7559%, target 성공률은 96.0068%였다.
- 최근 6시간 run
  [`29402225836`](https://github.com/Jaemani/BusyCafe/actions/runs/29402225836)은 terminal 72,
  complete 70, failed 1, partial 1로 complete rate 97.2222%, target 성공률 98.5882%,
  coverage 121/121이었다. 개선 방향은 보이지만 99% 목표는 아직 통과하지 못했다.
- commit `29b55db` 배포 후 성수 0.04°×0.025° bbox를 동일 URL로 연속 조회한
  공개 API 표본은 첫 응답 654ms, 두 번째 43ms였다. 두 응답은 HTTP 200,
  gzip 전송량 약 49.6kB였고 후속 header에서 `x-vercel-cache: HIT`, `age: 6`,
  `cache-control: public, max-age=30`을 확인했다. 이는 단일 표본이며 p95 주장이 아니다.

판정: **FAIL(99% production SLO)**. 원인 확정 전 concurrency나 retry를 늘리지 않는다.
연속 7일 창이 쌓였을 때 다시 측정하고, 그때도 99%에 미달하면 서울 리전의
상시 worker를 검토한다. 관련 사건은 INC-2026-022에 기록했다.

### Nowcast shadow

- 일일 read-only run
  [`29363194234`](https://github.com/Jaemani/BusyCafe/actions/runs/29363194234)은 121개 hotspot,
  44,176표본, 2.593일을 평가했다. 관측 지연 평균은 32.606분이었다.
- forecast interpolation은 인구 WAPE를 delayed baseline 6.2424%에서 3.8746%로
  낮춰지만 4단계 exact accuracy는 90.3477%에서 75.7583%, adjacent accuracy는
  99.0628%에서 97.7409%로 악화됐다.
- hybrid comparator는 인구만 forecast로 보정하고 레벨은 latest observed를 유지해
  레벨 회귀를 피하지만, 현재 공개 카페 순위는 4단계 레벨이 주 신호라 즉시
  승격 이익이 없다.

판정: **BLOCKED(insufficient span and ordinal regression)**. `promotion_eligible=false`를
유지했고 public model을 변경하지 않았다.

## 2026-07-15 — 서울 실시간 인구의 시각 의미와 산출 근거 재검증

### 공식 자료와 재현 경로

- 공식 안내: [서울 실시간 도시데이터 안내](https://data.seoul.go.kr/dataVisual/seoul/guide.do).
  안내 페이지는 실시간 인구를 통신사 KT·SKT 기지국 사용자 인구의 5분 단위 집계,
  전체 이동통신 사용자 인구 전수화, 50m×50m 격자 가중 분배 결과라고 설명한다.
- 공식 설명서: 위 안내 페이지의 `서울 실시간 도시데이터 설명서` 링크에서 내려받았다.
  2026-07-15 당시 form action은
  `https://datafile.seoul.go.kr/bigfile/iot/inf/nio_download.do?useCache=false`, POST 값은
  `infId=DOWNLOAD`, `infSeq=4`, `seq=14`였다. 파일은 V8.5(2026년 4월), 49쪽,
  5,483,900 bytes이고 SHA-256은
  `c0ccb5bcc70dbed1c588019c85a6349e0437fc20b570132671c81aa37c1d7769`였다.
  다운로드 route와 파일은 서울시가 교체할 수 있으므로 안내 페이지를 정본 진입점으로
  사용한다.

### 시각 의미와 제공 지연

- 공식 설명서는 5분 구간으로 인구를 집계하고 전체 인구 추정·보정을 거쳐 사용자에게
  제공되기까지 약 15분이 걸린다고 설명한다. 예시는 `10:10~10:15` 집계분이
  `10:30`에 제공되는 경우다. 여기서 5분은 관측 구간/표출 시각의 해상도이지,
  사용자에게 5분 이내 도착한다는 보장이 아니다.
- 실제 API의 `PPLTN_TIME` 문자열에는 UTC offset이 없다. BusyCafe는 이를 서울 현지시각
  KST로 해석해 timezone-aware UTC `observed_at`으로 저장하고, 서버 수신 시각은 별도
  `fetched_at`으로 저장한다. 원본 관측시각을 수신시각으로 덮어쓰지 않는다.
- 위 Production ingest SLO의 24시간 표본에서 `fetched_at-observed_at`은 p50 32.660분,
  p95 32.881분, 최댓값 33.104분이었다. 이는 공식 설명서의 약 15분보다 약 18분 길다.
  원인은 확인되지 않았으며 HTTP 왕복시간, worker 실행시간 또는 물리적 현장 상태와의
  오차로 재해석하지 않는다. 현재 확인된 것은 API에 적힌 시각과 우리 서버 수신시각의
  차이뿐이다.

### 서울시 산출 방법

- KT는 LTE/5G 신호에서 5분별·기지국별 방문자를 집계하고 가입자 표본을 시장점유율,
  LTE 가입률, 휴대전화 활성 비율, 성·연령 구성 등으로 전수화한다. 한 사람이 같은
  5분 구간에 여러 기지국을 방문하면 기지국별 집계에서 중복될 수 있다. 기지국 인구는
  격자 유형, 건물·도로, 생활주기, 전파 특성 등의 가중치로 50m 격자에 분배된다.
- SKT는 3G/4G/5G 신호의 시간 연속성으로 체류·이동 위치를 정하고, 지형·입지 등
  30개 이상의 요소를 사용한 ML 모델로 50m pCell에 배분한다. 이후 추정 실거주지와
  행정동별 성·연령 구성을 사용해 전체 인구로 전수화한다.
- 서울시는 장소별로 두 통신사의 추세를 대중교통 승·하차, 장소 고유 특성,
  시설 입장객, 주민등록인구와 비교한다. 장소에 따라 적합한 통신사를 선택하거나,
  둘 다 적합하면 날짜·시간·도로소통·대중교통 등을 사용한 가중 융합값을 만든다.
- 4단계 혼잡도는 최근 28일 인구 범위 대비 현재 인구와 장소 면적 대비 밀집도를
  결합한다. 관광지·오피스·생활권·혼합형·공원·공연장 같은 장소 유형, 일부 유형의
  건물 면적 제외, 표준점수(z-score), 사분위범위(IQR), 대중교통 승·하차가 추가 조정에
  쓰인다. 이 면적은 카페 면적이 아니라 서울시 장소와 그 안의 격자 면적이다.
- 향후 12시간 예측은 날짜·시간 특성과 과거 12시간 인구를 쓰는 Sequence-to-Sequence
  모델이다. 5분 신호로 이벤트 여부를 추정하고 이벤트 특화 모델도 사용하지만,
  서울시는 급격한 행사 변화 일부를 놓칠 수 있고 실제 인구와 다를 수 있다고 명시한다.

판정: **VERIFIED(source semantics), UNVERIFIED(physical ground truth)**. 서울시가 제공한
`PPLTN_TIME` 기준의 내부 시각 정렬과 산출 설명은 확인했다. 그러나 서울시 데이터 자체가
통신 신호 기반 추정이고, 공식 약 15분과 production 약 33분의 차이도 설명되지 않았다.
따라서 나중 서울시 snapshot을 정답으로 쓴 nowcast 평가는 같은 source 내부 재현 평가이며,
실제 거리 보행 혼잡이나 카페 좌석 혼잡의 정확도 증거가 아니다. 개선·승격 gate의 소유
문서는 [Track 1](tracks/TRACK-1-ACCURACY.md)이다.

## 2026-07-15 — 카페 검색의 거리 기준과 지도 필터

기준 구현은 `1048c16`이다. 검색 기준점은 브라우저에서 위치 권한이 성공한 경우 사용자
위치, 그 외에는 현재 지도 중심이다. 서버에는 기준점을 소수점 셋째 자리로 낮춰 보내고,
SQL에서 서울 중간 위도의 경도 축척을 적용한 equirectangular 제곱거리와 `Cafe.id`로
정렬한 뒤 최대 50건을 선택한다. 브라우저는 수신 후보를 원래 기준점과의 haversine 거리로
다시 정렬해 `850m`, `1.2km` 형식으로 표시한다. 좌표와 검색어는 제품 분석 이벤트에 넣지
않으며, 기준점은 BusyCafe DB에 저장하지 않는다. 다만 GET query는 호스팅 사업자의 일반
HTTP 접근 로그 정책을 따르므로 위치를 영구 보존하지 않는다는 의미로 확대 해석하지 않는다.

검색 결과가 한 건 이상이면 그 결과만 지도 GeoJSON source에 게시한다. 검색 로딩, 0건,
오류, 두 글자 미만, 검색어와 브랜드 필터 해제 상태에서는 기존 viewport collection을
복원한다. 지도 이동과 background refresh는 활성 검색 필터를 임의 해제하지 않는다.

검증:

- `cd frontend && npm test -- --run`: 14 tests passed
- `cd frontend && npm run typecheck`: passed
- `cd frontend && npm run build`: passed, gzip JavaScript 296.01kB. 기존 500kB chunk 경고 유지
- `cd backend && uv run pytest -q`: passed, 2 skipped
- `git diff --check`: passed

판정: **PASS(search ordering and filtering contract)**. 현재 서버 후보는 검색을 실행한 시점의
기준점으로 최대 50건을 고른다. 위치 권한 성공이나 지도 이동 뒤 브라우저가 기존 후보를
재정렬하지만 서버 후보를 자동 재조회하지는 않는다. 다음 검색 개선은 기준점이 의미 있게
이동했을 때만 debounce한 재조회 또는 명시적인 `이 지역에서 다시 검색` 동작을 비교해,
DB 부하와 사용자의 지도 조작 예측 가능성을 함께 측정한 뒤 선택한다.

## 2026-07-15 — 프랜차이즈 필터 확대와 출퇴근시간 안내

기준 구현은 `fefa9eb`이다. 프랜차이즈 canonical allowlist를 7개에서 15개로 확대하고,
더벤티·매머드커피·텐퍼센트커피·할리스·탐앤탐스·카페베네·커피빈·엔제리너스의 한글·영문
표기 변형을 cache-only 검색 alias로 추가했다. 변경 전 production text search에서 최대
50건 상한 기준 더벤티·매머드·텐퍼센트·할리스·커피빈은 각각 50건, 탐앤탐스 44건,
카페베네 21건, 엔제리너스 44건이 확인됐다. 이는 문자열 검색 recall 표본이며 본사 가맹점
목록이나 영업 상태 검증 건수로 해석하지 않는다.

15개 chip은 각각 하나의 flat background와 흰색 또는 짙은 전경색만 사용한다. `5b90eb1`에서
초기 팔레트의 채도를 낮춘 뒤 WCAG 상대 휘도 공식으로 base 상태를 다시 계산한 최소
명암비는 스타벅스 chip의 4.838:1이었고 나머지도 그 이상이었다. hover는 색을 밝히지 않고
1px 위로 이동한다. 선택과 keyboard focus는 외부 shadow·outline 대신 border와 inset ring을
사용해 가로 스크롤 container의 overflow에 잘리지 않는다.

출퇴근시간 안내는 `Asia/Seoul` 기준 일반 평일 07:00 이상 10:00 미만, 17:00 이상
20:00 미만에 페이지를 초기화할 때마다 표시한다. 닫힘 상태를 local/session storage에
저장하지 않으므로 새 방문에서는 다시 평가한다. 토요일·일요일과 2026년 정적 비근무일
목록에는 표시하지 않으며, 달력이 확인되지 않은 연도에는 잘못된 공휴일 경고를 피하려고
안내 전체를 숨긴다. 2027년 공개 전 공식 연간 달력으로 목록과 지원 연도를 갱신해야 한다.

검증:

- `cd frontend && npm test -- --run`: 4 files, 17 tests passed
- `cd frontend && npm run typecheck`: passed
- `cd frontend && npm run build`: passed, gzip JavaScript 296.68kB
- `cd backend && uv run pytest -q`: passed, 2 skipped
- 15개 softened base chip contrast 계산: minimum 4.838:1
- `git diff --check`: passed

판정: **PASS(UI schedule and filter contract), PROVISIONAL(annual calendar maintenance)**.
검색 alias와 UI 시간 판정은 fixture 테스트로 고정했다. 프랜차이즈 identity는 기존 장소
원장의 provenance를 따르며 이름 substring만으로 본사 인증을 주장하지 않는다. 달력은
엔진 feature가 아니라 경고 노출 조건이지만, 연도별 공식 공휴일 artifact를 자동 생성하고
revision을 검증하는 경로가 생기기 전에는 매년 수동 갱신 gate를 유지한다.
