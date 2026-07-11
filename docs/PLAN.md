# cafe-crowd — 카페 혼잡도 맵 개발 계획서 (v1.1)

> **한 줄 정의**: 서울 주요 상권의 실시간 지역 혼잡도(서울 실시간 도시데이터)를 카페 위치에 공간 매핑하여, "지금 이 근처에서 상대적으로 한산할 카페"를 근거와 함께 보여주는 준실시간 지도 서비스.

> **v1.1 (2026-07-11)**: 운영 저장소를 PostgreSQL로 확정하고 ingest worker를 API
> 프로세스에서 분리했다. uncovered NULL 규칙과 timezone-aware 시각 저장을 DDL에
> 반영했다. API 관련 `[VERIFY]` 항목은 아직 실측 전이며 변경하지 않았다.

---

## 0. 이 문서의 사용법 (에이전트 필독)

- 이 문서가 **source of truth**다. 구현 중 실측 결과와 문서가 충돌하면, 문서를 수정하고 `docs/VERIFICATION.md`에 기록한 뒤 진행한다.
- `[VERIFY]` 태그가 붙은 항목은 학습 지식 기반 추정이므로 **Phase 0에서 실제 API 응답으로 반드시 검증 후 확정**한다.
- `[HUMAN]` 태그가 붙은 작업은 사람(사용자)만 할 수 있다. 해당 작업이 블로커면 멈추고 사용자에게 요청한다.
- Phase는 순서대로 진행하며, 각 Phase의 **DoD(Definition of Done) 체크리스트를 모두 통과해야** 다음 Phase로 넘어간다.
- 모든 튜닝 파라미터는 `backend/app/config.py`에 상수로 모은다. 코드 내 하드코딩 금지.

---

## 1. 제품 정의

### 1.1 핵심 원칙 (설계 전체를 지배)

1. **추정치임을 숨기지 않는다.** 모든 혼잡도 표시는 "추정"이며, 근거(기준 핫스팟, 거리, 갱신 시각)와 신뢰도를 항상 함께 노출한다.
2. **절대값이 아니라 상대 비교.** "이 카페 70% 참"이 아니라 "이 동네에서 지금 상대적으로 여유로울 가능성이 높은 순"을 제공한다.
3. **준실시간(5~10분 지연)이면 충분.** 데이터 갱신 주기와 제품 약속을 일치시킨다.

### 1.2 Non-goals (하지 않는 것)

- 매장 단위 좌석 점유율 / 대기 인원 추정
- 구글/네이버 지도 데이터 스크레이핑 (ToS 리스크 — 사용 금지)
- 서울 실시간 도시데이터 핫스팟 커버리지 밖 지역의 실시간 표시 (해당 지역은 "미커버"로 정직하게 처리)
- 초 단위 실시간

### 1.3 MVP 스코프

- **지역**: 서울, 핫스팟이 커버하는 동네 2~3곳으로 시작. 기본 중심 핫스팟은 실 API 호출로 확인한 `성수카페거리`(`POI068`), `홍대 관광특구`(`POI007`), `연남동`(`POI073`) 주변. 각 동네 반경 내 추가 폴링 대상은 121개 장소 마스터 확보 후 config에 확정한다. [VERIFY]
- **사용자 플로우**: 지도 열기 → 카페 마커의 4단계 색상으로 주변 혼잡도 확인 → 마커 클릭 → 근거 패널(기준 핫스팟, 거리, 레벨, 갱신 시각, 12시간 추이, 1시간 뒤 예측).

---

## 2. 데이터 소스 명세

| 소스 | 용도 | 갱신 주기 | 접근 방식 | Phase |
|---|---|---|---|---|
| 서울 실시간 도시데이터 (열린데이터광장) | 핫스팟별 실시간 인구 혼잡도 + 12h 예측 | 인구 5분 / 상권 10분 | REST API, 인증키 | P0~P1 |
| 카카오 로컬 API (카테고리 CE7) | 카페 POI (이름·좌표·place_id) | 온디맨드 | REST API, REST 키 | P2 |
| 서울시 주요 장소(핫스팟) 마스터 | 핫스팟 코드·명칭·좌표 | 정적 | 열린데이터광장 파일 다운로드 | P1 |
| 지방행정 인허가(휴게음식점) — 공공데이터포털 | 폐업 카페 필터 | 일 단위 | REST API | Backlog |
| 서울 생활인구 (집계구·시간대별) | "평소 대비" 베이스라인 | 시간별 (며칠 지연 공개) | 파일/API | Backlog |
| 기상청 단기예보 | 날씨 보정 | 시간별 | REST API | Backlog |

### 2.1 서울 실시간 도시데이터 (핵심 소스)

- 발급: 서울열린데이터광장(data.seoul.go.kr) 회원가입 → 일반 인증키 발급. **[HUMAN]**
- 대상: 서울시 주요 **121개 장소**(관광특구·발달상권·인구밀집지역 등). 실시간 인구는 KT·SKT 기지국 신호 5분 단위 집계 기반.
- 인구 전용 엔드포인트 [VERIFIED 2026-07-11]: `http://openapi.seoul.go.kr:8088/{KEY}/json/citydata_ppltn/1/5/{AREA_NM}`. `광화문광장` AREA_NM 호출로 정상 응답을 확인했다. 통합 `citydata` 엔드포인트는 사용하지 않으며 계획의 추정 경로에서 제거한다.
- 실제 응답 구조 [VERIFIED 2026-07-11]: root의 `SeoulRtd.citydata_ppltn[]`에 장소별 인구 필드가 직접 들어가는 평면 레코드다. 성공 결과는 별도 root `RESULT`에 `RESULT.CODE`, `RESULT.MESSAGE` 키로 제공됐다. 계획에 있던 `LIVE_PPLTN_STTS` 중첩 가정은 폐기한다.
- 주요 응답 필드 [VERIFIED 2026-07-11]:
  - `AREA_NM`, `AREA_CD` — 장소명/코드
  - `AREA_CONGEST_LVL` — 혼잡도 레벨 `여유` / `보통` / `약간 붐빔` / `붐빔`. 광화문 fixture와 MVP 대상 3곳의 제어된 실호출에서 4종을 모두 확인했다. [VERIFIED 2026-07-11]
  - `AREA_PPLTN_MIN`, `AREA_PPLTN_MAX` — 실시간 인구 추정 구간
  - `PPLTN_TIME` — 데이터 기준 시각
  - `FCST_YN`, `FCST_PPLTN[]` — 실측 응답에 12개 예측 레코드. 각 레코드는 `FCST_TIME`, `FCST_CONGEST_LVL`, `FCST_PPLTN_MIN`, `FCST_PPLTN_MAX`
- 호출 단위: **장소 1곳당 1콜**. 일괄 조회 없음 [VERIFY].
- **쿼터 제약 (중요)**: 열린데이터광장 인증키에는 기본 일일 트래픽 한도가 있다(한도 수치는 Phase 0에서 실측/문서 확인 — 통상 수백~수천 건 수준이며 상향 신청 가능). 121곳 × 5분 폴링 = 34,848콜/일은 불가능할 가능성이 높다. **대응 전략(이 순서로)**:
  1. 폴링 대상을 MVP 동네의 핫스팟만으로 한정 (≤12곳)
  2. 폴링 주기 10분 (요구사항상 허용) → 12곳 × 144회 = 1,728콜/일
  3. 그래도 초과 시: 주기 15분으로 완화 + 한도 상향 신청 [HUMAN]

### 2.2 카카오 로컬 API

- 발급: developers.kakao.com 앱 생성 → REST API 키. 지도 SDK용 JavaScript 키 + 사이트 도메인 등록도 함께. **[HUMAN]** Map/Local 제품 사용 활성화가 별도로 필요하며, 미활성화 시 `disabled OPEN_MAP_AND_LOCAL service` 403을 반환한다. JavaScript 키는 프론트에만 두고 REST 키는 백엔드에만 둔다.
- 카테고리 검색 [VERIFIED 2026-07-11]: `GET https://dapi.kakao.com/v2/local/search/category.json`
  - 헤더: `Authorization: KakaoAK {REST_KEY}`
  - 파라미터: `category_group_code=CE7`, `x`(경도), `y`(위도), `radius`(m, 최대 20000), `page`, `size`(≤15)
- 응답 구조 [VERIFIED 2026-07-11]: root `meta` + `documents[]`. `meta`는 `total_count`, `pageable_count`, `is_end`, `same_name`; document는 `id`, `place_name`, `category_*`, 주소, 전화, `x`, `y`, `place_url`, `distance`를 포함했다.
- **핵심 제약 [VERIFIED 2026-07-11]**: 광화문 반경 1km CE7 검색에서 `total_count=761`, `pageable_count=45`; size 15 기준 3페이지에 `is_end=true`를 확인했다. 쿼리 조건당 최대 **45건**(15건 × 3페이지)까지만 노출되므로 밀집 지역에서는 반드시 **재귀 4분할 스윕**을 구현한다: `total_count > 45`이면 검색 원을 4개 사분면 원으로 쪼개 재귀 호출, `is_end`까지 페이지 순회, `kakao_place_id`로 dedupe.

---

## 3. 핵심 설계 결정 (D1~D7)

- **D1. 혼잡도는 카페의 속성이 아니라 카페가 위치한 지역의 속성이다.** 카페 마커의 색은 "이 카페가 붐빈다"가 아니라 "이 카페 주변 지역이 붐빈다"를 의미하며, UI 문구도 이를 따른다("주변 혼잡도").
- **D2. 공간 매핑 = 거리 가중 보간(IDW).** 카페 기준 반경 `R_MAX` 내 최근접 핫스팟 최대 `K`곳의 레벨을 역거리제곱 가중 평균한다. (공식은 §4.5)
- **D3. 신뢰도는 별도 축이다.** 신뢰도 = f(최근접 핫스팟 거리, 데이터 신선도, 기여 핫스팟 수). 혼잡도 레벨과 독립적으로 계산·표시한다.
- **D4. 커버리지 3단계.** `covered`(d_min ≤ 600m) / `fringe`(600m < d_min ≤ R_MAX, "참고용" 라벨) / `uncovered`(d_min > R_MAX, 회색 마커 + "데이터 없음"). 미커버를 억지로 채색하지 않는다.
- **D5. 레벨 수치화.** `여유=1, 보통=2, 약간 붐빔=3, 붐빔=4`. 보간은 연속값으로 하고 표시 시 반올림.
- **D6. 근거 노출은 필수 기능이다.** 상세 패널에 기준 핫스팟명·거리·원본 레벨·갱신 시각을 항상 표시. 이것이 "러프한 추정"을 정당화하는 장치다.
- **D7. 스코어링은 결정적 순수 함수로.** 동일 입력(카페 좌표 + 스냅샷 셋 + config) → 동일 출력. ML 없음(MVP). 고정 fixture 기반 스냅샷 테스트로 회귀를 잡는다.

---

## 4. 시스템 아키텍처

### 4.1 컴포넌트 흐름

```text
[single ingest worker: POLL_INTERVAL마다]
  ingest/poller ──> 서울 citydata API (핫스팟별 1콜)
       │ 파싱(pydantic) 실패 시 raw 저장 + 경고 로그
       ▼
  hotspot_snapshots (append-only)
       │ 인제스트 훅
       ▼
  scoring/engine.materialize_all() ──> cafe_scores (전체 재계산, upsert)
       ▲
  cafes (P2에서 시드, 이후 정적)

[요청 시]
  frontend (Kakao Maps) ──> FastAPI ──> cafe_scores + cafes 조인 반환
```

### 4.2 기술 스택

- Backend: Python 3.12, FastAPI, SQLAlchemy 2 + PostgreSQL(운영·통합테스트 기준, 필요 시 PostGIS), httpx, pydantic v2, APScheduler(개발 단일 프로세스만; 운영은 단일 ingest worker로 분리)
- Frontend: Vite + TypeScript(vanilla), Kakao Maps JavaScript SDK. (React 전환은 자유 — MVP는 vanilla로 스코프 최소화)
- 테스트: pytest. **실 API 호출 테스트 금지** — 모든 테스트는 `fixtures/`의 실측 스냅샷 사용.

> 저장소 선택은 `docs/adr/ADR-0001-primary-database.md`에서 PostgreSQL로 확정했다. 관계·시계열·공간 질의와 사용자 증가 시 비용/운영성을 비교한 결정이며, Firebase는 향후 Auth·푸시·호스팅 후보로만 둔다. 로컬 단위 테스트에는 SQLite를 제한적으로 사용할 수 있으나 운영 저장소로 사용하지 않는다.

### 4.3 디렉토리 구조

```text
cafe-crowd/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI 엔트리(운영 인제스트 worker와 분리)
│   │   ├── config.py          # 모든 튜닝 상수 (§4.6)
│   │   ├── models.py          # SQLAlchemy 모델
│   │   ├── schemas.py         # pydantic 응답/외부API 모델
│   │   ├── clients/
│   │   │   ├── seoul_citydata.py   # 외부 호출은 여기로만
│   │   │   └── kakao_local.py
│   │   ├── ingest/poller.py
│   │   ├── ingest/worker.py   # 단일 스케줄러 프로세스
│   │   ├── scoring/engine.py  # 순수 함수 + materialize
│   │   └── api/routes.py
│   ├── scripts/
│   │   ├── verify_apis.py     # P0: 실측 → fixtures 저장
│   │   ├── seed_hotspots.py   # P1
│   │   ├── seed_cafes.py      # P2
│   │   └── run_eval.py        # P6
│   ├── tests/
│   ├── fixtures/              # 실측 API 응답 JSON
│   └── .env.example
├── frontend/
│   ├── index.html
│   └── src/ (main.ts, map.ts, panel.ts, api.ts)
└── docs/
    ├── PLAN.md                # 이 문서
    ├── VERIFICATION.md        # Phase별 검증 기록 (누적)
    └── EVAL_REPORT.md         # P6 산출물
```

### 4.4 DB 스키마 (DDL 초안)

```sql
CREATE TABLE hotspots (
  id INTEGER PRIMARY KEY,
  area_cd TEXT UNIQUE,          -- 광화문광장 실측값: POI088; 전체 마스터는 [VERIFY]
  name TEXT NOT NULL,           -- API 호출에 쓰는 정확한 AREA_NM
  category TEXT,                -- 관광특구/발달상권/인구밀집지역 등
  lat REAL NOT NULL,
  lng REAL NOT NULL,
  is_polled INTEGER DEFAULT 0   -- MVP 폴링 대상 여부
);

CREATE TABLE hotspot_snapshots (
  id INTEGER PRIMARY KEY,
  hotspot_id INTEGER REFERENCES hotspots(id),
  observed_at TIMESTAMPTZ NOT NULL, -- API의 PPLTN_TIME을 timezone-aware UTC로 정규화, API/UI에서 KST 표현
  fetched_at TIMESTAMPTZ NOT NULL,
  congest_level INTEGER NOT NULL,   -- 1~4 (D5 매핑)
  congest_label TEXT NOT NULL,      -- 원본 문자열
  ppltn_min INTEGER, ppltn_max INTEGER,
  forecast_json JSONB,              -- FCST_PPLTN 원본
  raw_json JSONB
);
CREATE INDEX ix_snap_hotspot_time ON hotspot_snapshots(hotspot_id, observed_at DESC);

CREATE TABLE cafes (
  id INTEGER PRIMARY KEY,
  kakao_place_id TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  lat REAL NOT NULL, lng REAL NOT NULL,
  road_address TEXT, phone TEXT, place_url TEXT,
  neighborhood TEXT,            -- config에 정의한 대상 동네 키
  active INTEGER DEFAULT 1
);

CREATE TABLE cafe_scores (
  cafe_id INTEGER PRIMARY KEY REFERENCES cafes(id),
  computed_at TIMESTAMPTZ NOT NULL,
  score REAL,                   -- covered/fringe: 1.0~4.0, uncovered: NULL
  level INTEGER,                -- round(score), uncovered: NULL
  confidence REAL,             -- covered/fringe: 0~1, uncovered: NULL
  confidence_tier TEXT,        -- high/mid/low, uncovered: NULL
  coverage TEXT NOT NULL,       -- covered/fringe/uncovered
  primary_hotspot_id INTEGER,
  primary_distance_m REAL,
  contributors_json JSONB       -- [{hotspot_id, distance_m, level, weight}]
);
```

### 4.5 스코어링 공식 (`scoring/engine.py` — 순수 함수)

카페 `c`에 대해:

1. 이웃 선정: `d(c, h) ≤ R_MAX`인 핫스팟 중 가까운 순 최대 `K`곳. 거리 계산은 haversine(m).
2. 각 이웃의 최신 스냅샷 레벨 `lvl_i`(1~4), 가중치 `w_i = 1 / max(d_i, D_FLOOR)²`
3. `score = Σ(w_i · lvl_i) / Σw_i`, `level = round(score)`
4. 신선도: 가장 최신 기여 스냅샷 기준 `freshness = exp(-Δt_min / TAU_MIN)` (`Δt_min` = 현재-observed_at, 분)
5. 커버리지 계수: `cov = clamp(1 - d_min / R_MAX, 0, 1)`
6. `confidence = cov × freshness × min(1, n_neighbors / 2)`
7. `coverage`: d_min ≤ `COVERED_M` → covered / ≤ R_MAX → fringe / else uncovered(스코어 계산 안 함)

이웃이 0곳이면 `coverage=uncovered`, level/score/confidence는 NULL 처리.

### 4.6 `config.py` 기본값 (전부 P6 캘리브레이션 대상)

| 상수 | 기본값 | 의미 |
|---|---|---|
| `POLL_INTERVAL_MIN` | 10 | 폴링 주기(분). 쿼터 실측 후 확정 |
| `R_MAX_M` | 1500 | 이웃 탐색 최대 반경 |
| `COVERED_M` | 600 | covered 판정 거리 |
| `K_NEIGHBORS` | 3 | 최대 기여 핫스팟 수 |
| `D_FLOOR_M` | 50 | 거리 하한 (0-나눗셈 방지) |
| `TAU_MIN` | 15 | 신선도 감쇠 시상수(분) |
| `CONF_HIGH` / `CONF_MID` | 0.55 / 0.30 | 신뢰도 등급 경계 |
| `TARGET_NEIGHBORHOODS` | {성수, 홍대, 연남} | 동네 키 → 중심좌표+반경 정의 |
| `STALE_WARN_MIN` | 25 | 이 이상 갱신 없으면 UI에 stale 배지 |

### 4.7 내부 API 스펙

- `GET /api/cafes?bbox={minLng},{minLat},{maxLng},{maxLat}&min_conf=0`
  → `[{id, name, lat, lng, level, score, confidence, confidence_tier, coverage, evidence: {hotspot_name, distance_m, observed_at}}]`
- `GET /api/cafes/{id}`
  → 위 필드 전체 + `contributors[]` + `trend_12h[]`(기준 핫스팟의 스냅샷 시계열) + `forecast_1h`(FCST에서 추출)
- `GET /api/hotspots` → 폴링 대상 핫스팟 현재 상태 (디버그 오버레이용)
- `GET /api/health` → `{last_ingest_at, snapshots_last_hour, cafes_count}`

---

## 5. 개발 페이즈

### Phase 0 — 준비 및 스키마 실측 검증

작업:

1. [HUMAN] 서울열린데이터광장 인증키 발급 → `.env`의 `SEOUL_API_KEY`
2. [HUMAN] 카카오 개발자 앱 생성 → `KAKAO_REST_KEY`, `KAKAO_JS_KEY` + 로컬 도메인(`http://localhost:5173`) 등록
3. 레포 스캐폴딩(§4.3 구조), `.env.example`, `config.py` 뼈대
4. `scripts/verify_apis.py` 작성·실행:
   - citydata 실호출 1건(예: `광화문광장`) → 응답 원본을 `fixtures/citydata_sample.json` 저장
   - `[VERIFY]` 항목 전부 확정: 엔드포인트, 필드명, 혼잡도 라벨 문자열 4종, FCST 구조, `AREA_NM` 정확 표기
   - 카카오 CE7 검색 실호출 1건 → `fixtures/kakao_ce7_sample.json`
   - 121개 장소 마스터 파일 확보 경로 확인(열린데이터광장 내 장소 목록) 및 다운로드
   - 인증키 일일 한도 확인(포털 마이페이지/문서) → `POLL_INTERVAL_MIN` 확정
5. fixtures 기반 pydantic 외부 API 모델(`schemas.py`) 확정

DoD:

- [ ] fixtures 2종 이상 커밋됨
- [ ] `docs/VERIFICATION.md`에 확정 스키마·라벨 문자열·쿼터 한도·폴링 주기 결정 기록
- [ ] 문서의 [VERIFY] 항목이 전부 해소되어 본 문서 갱신됨

### Phase 1 — 핫스팟 인제스트 파이프라인

작업:

1. `seed_hotspots.py`: 장소 마스터 → `hotspots` 적재. 좌표 누락 시 카카오 키워드 검색으로 중심좌표 보정 후 **수동 검수 목록 출력** [HUMAN 확인 1회]
2. `TARGET_NEIGHBORHOODS` 반경 내 핫스팟에 `is_polled=1` 설정 (≤12곳 확인)
3. `ingest/poller.py`와 단일 `ingest/worker.py`: APScheduler로 `POLL_INTERVAL_MIN`마다 폴링 대상 순회 호출 → 파싱 → `hotspot_snapshots` append. 실패 시 3회 지수 백오프, 최종 실패는 로그만 남기고 다음 대상 진행. FastAPI 프로세스에서는 스케줄러를 기동하지 않는다.
4. 파서 유닛테스트 (fixture 기반)

DoD:

- [ ] 로컬에서 1시간 무인 구동 → 대상 핫스팟당 스냅샷 ≥5개 적재
- [ ] 잘못된 응답(fixture 변조) 주입 시 크래시 없이 스킵·로깅
- [ ] 일일 예상 콜 수 계산치가 쿼터 이내임을 `VERIFICATION.md`에 기록

### Phase 2 — 카페 시드

작업:

1. `seed_cafes.py`: 동네별 중심좌표+반경으로 CE7 스윕. **total_count>45 시 재귀 4분할** 구현. `kakao_place_id` dedupe, 동네 키 태깅
2. 좌표 유효성 검사(서울 바운딩박스 내), 이름 정규화
3. 결과 리포트 출력: 동네별 카페 수, 분할 호출 횟수, 총 API 콜 수

DoD:

- [ ] 대상 동네 3곳 카페 적재 완료 (동네당 수백 건 규모 예상)
- [ ] 무작위 10곳 스팟체크 체크리스트 출력 → [HUMAN] 카카오맵과 대조 확인
- [ ] 재실행 시 멱등(중복 삽입 없음)

### Phase 3 — 스코어링 엔진

작업:

1. `scoring/engine.py`: §4.5 공식을 순수 함수로. haversine 직접 구현(외부 의존 최소화)
2. `materialize_all()`: 전체 카페 배치 계산 → `cafe_scores` upsert. 인제스트 성공 훅에서 호출
3. 스냅샷 테스트: 고정 fixture(핫스팟 3곳 × 레벨 조합) × 카페 좌표 5개 → 기대 score/confidence/coverage 값을 테스트에 박제
4. 경계 케이스 테스트: 이웃 0곳, stale 스냅샷(freshness→0 근접), d < D_FLOOR

DoD:

- [ ] 전 테스트 그린, 커버리지: `engine.py` 분기 전부
- [ ] 전체 배치(카페 ~1,000건 가정) 1초 이내
- [ ] `cafe_scores`가 인제스트마다 자동 갱신됨

### Phase 4 — API 서버

작업: §4.7 엔드포인트 구현, CORS(localhost:5173), bbox 필터는 SQL where로, 응답 pydantic 스키마 고정

DoD:

- [ ] `/docs` OpenAPI에서 4개 엔드포인트 동작 확인
- [ ] bbox 쿼리 로컬 p95 < 100ms
- [ ] health가 마지막 인제스트 시각을 정확히 반환

### Phase 5 — 프론트엔드 지도

작업:

1. Kakao Maps SDK 로드(JS 키), 초기 뷰 = 첫 번째 대상 동네
2. bbox 변경(이동/줌 종료) 시 `/api/cafes` 재조회, 마커 렌더
3. 마커: level 1~4 → 4색(초록→노랑→주황→빨강 계열, 프론트 재량). `coverage=fringe` → 테두리 점선, `uncovered` → 회색. `confidence_tier=low` → 투명도 60%
4. 마커 클릭 → 근거 패널: 카페명, 주변 혼잡 레벨 문구("○○ 기준 · 620m · 8분 전 갱신"), 신뢰도 뱃지, 12h 미니 추이(단순 스파크라인), 1시간 뒤 예측
5. `STALE_WARN_MIN` 초과 시 상단 배너 "데이터 갱신 지연 중"

DoD (수동 시나리오 체크리스트를 docs에 기록):

- [ ] 지도 이동/줌 시 마커 갱신
- [ ] covered/fringe/uncovered 3종이 시각적으로 구분됨
- [ ] 근거 패널에 D6 요소(핫스팟명·거리·갱신시각) 전부 표시
- [ ] 백엔드 중단 상태에서 프론트가 죽지 않고 에러 표시

### Phase 6 — 캘리브레이션 및 검증 (eval)

프로토콜:

1. 골든셋: 대상 동네 2곳 × 카페 8곳 = 16곳 선정(핫스팟 근접/중간/외곽 거리 분포 섞어서)
2. 관측 슬롯 6개: 평일 14시·19시, 금요일 19시, 토요일 12시·15시·18시
3. 라벨 기준표(관측자용): 1=좌석 여유 많음(<30%) / 2=적당(30~60%) / 3=거의 참(60~90%) / 4=만석·대기(>90%) — [HUMAN] 현장 관측 또는 전화 확인
4. `scripts/run_eval.py`: 관측 CSV(cafe_id, slot, observed_level) vs 동시각 `cafe_scores` →
   - **Spearman rank correlation** (슬롯별 카페 순위 비교 — 핵심 지표)
   - **Adjacent accuracy**: `|pred - obs| ≤ 1` 비율
   - 거리 구간별(근접/중간/외곽) 분해 리포트
5. 파라미터 튜닝: `R_MAX, COVERED_M, K, TAU`를 그리드로 재계산(스냅샷은 저장돼 있으므로 오프라인 재실행 가능) → 최적 조합을 config에 반영

DoD:

- [ ] `docs/EVAL_REPORT.md` 커밋 (지표 + 거리 분해 + 선택 파라미터 근거)
- [ ] 합격선: Spearman ≥ 0.5 (미달 시 원인 가설 3개와 개선안을 리포트에 명시 — 실패도 산출물)

### Backlog (MVP 이후, 우선순위순)

1. **"평소 대비" 베이스라인**: 스냅샷이 2~3주 쌓이면 핫스팟별 요일×시간 평균 → "평소보다 붐빔/한산" 상대 표시 (자체 데이터만으로 가능, 외부 의존 없음)
2. FCST 12h 예측 활용 확대: "몇 시에 가면 여유" 추천
3. 인허가 데이터(공공데이터포털) 연동 폐업 필터 + 프론트 "폐업 신고" 버튼
4. 원탭 체크인("자리 있어요/없어요") → ground truth 축적 → 카페별 보정계수
5. 날씨 보정(기상청), 커버 동네 확장, PostGIS 공간 인덱스/읽기 replica(실제 병목 확인 시)

---

## 6. 리스크와 대응

| 리스크 | 영향 | 대응 |
|---|---|---|
| API 일일 쿼터 초과 | 인제스트 중단 | P0에서 한도 실측, 대상 핫스팟·주기 축소, 상향 신청. poller에 일일 콜 카운터 + 상한 도달 시 자동 정지 |
| citydata 스키마 변경 | 파싱 실패 | pydantic strict 파싱 + 실패 시 raw_json 보존, 알림 로그. 문서 갱신 절차(§0) |
| 핫스팟 신호와 골목 카페 괴리 (역 앞은 붐비는데 골목은 한산) | 추정 오차 | 이것이 P6 검증의 존재 이유. 거리 구간별 분해로 정량화, R/COVERED 튜닝, 한계는 confidence로 표현 |
| 데이터 지연/장애 | 낡은 값 표시 | freshness 감쇠 + STALE 배지. 절대 마지막 값을 "현재"처럼 위장하지 않음 |
| 폐업 카페 표시 | 신뢰 하락 | Backlog 3 (인허가 필터 + 신고 버튼) |

---

## 7. 에이전트 작업 규약

1. **시크릿**: `.env`만 사용, 커밋 금지. `.env.example`은 항상 최신 유지.
2. **외부 호출 격리**: 모든 외부 API 호출은 `clients/` 모듈을 통해서만. 테스트에서 실호출 금지(fixture만).
3. **결정성**: 스코어링·평가 스크립트는 동일 입력→동일 출력. 랜덤 요소 없음.
4. **커밋 단위 = Phase 하위 작업 단위**, Phase 완료 시 DoD 체크 결과를 `docs/VERIFICATION.md`에 append하고 태그성 커밋.
5. **설정 집중**: 수치 상수는 전부 `config.py`. 매직 넘버 발견 시 리팩터.
6. **막히면 멈춘다**: [HUMAN] 블로커, 쿼터 소진, 스키마 불일치 등은 임의로 우회하지 말고 상황 요약 + 선택지를 제시하고 사용자 결정을 기다린다.

---

## 8. 운영·확장성 및 기록 요구사항

- MVP 이후 사용자 증가를 고려해 저장소·스케줄러·배포 구조를 교체 가능한 경계로 설계한다. MongoDB/Firebase를 포함한 비교 결과 PostgreSQL을 채택했으며 근거와 재검토 조건은 `docs/adr/ADR-0001-primary-database.md`에 유지한다.
- 앱의 사용자 체감 변경은 `docs/CHANGELOG.md`, 주요 기술·제품 결정은 `docs/DECISIONS.md`, 중요한 장애와 실수 및 재발 방지 조치는 `docs/INCIDENTS.md`에 기록한다.
- 모든 기록은 관련 이슈·검증 결과·커밋을 연결해 Git으로 버전 관리한다.
- Phase 완료 커밋과 릴리스 태그 정책은 실제 배포 흐름이 정해질 때 ADR로 확정한다.
