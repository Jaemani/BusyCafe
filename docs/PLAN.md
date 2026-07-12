# cafe-crowd — 카페 혼잡도 맵 개발 계획서 (v1.7)

> **한 줄 정의**: 서울 주요 상권의 실시간 지역 혼잡도(서울 실시간 도시데이터)를 카페 위치에 공간 매핑하여, "지금 이 근처에서 상대적으로 한산할 카페"를 근거와 함께 보여주는 준실시간 지도 서비스.

> **v1.1 (2026-07-11)**: 운영 저장소를 PostgreSQL로 확정하고 ingest worker를 API
> 프로세스에서 분리했다. uncovered NULL 규칙과 timezone-aware 시각 저장을 DDL에
> 반영했다. API 관련 검증 태그는 당시 실측 전이어서 변경하지 않았다.

> **v1.2 (2026-07-11)**: 서울·카카오 실응답, 라벨 4종, 호출 단위, 121개 장소
> 목록과 WGS84 영역을 검증했다. 초기 서울 중첩 응답 가정을 평면 구조로 수정하고,
> 공식 폴리곤의 내부 대표점을 사용하는 위치 산출 결정을 반영했다. 일일 쿼터와
> 폴링 주기만 사용자 포털 확인 대기 상태다.

> **v1.3 (2026-07-11)**: 지도 엔진을 Kakao Maps에서 MapLibre GL로 전환하고,
> OpenFreeMap 벡터 베이스맵을 채택했다. Kakao Local 데이터의 비-Kakao 지도 표시와
> 장기 저장은 공식 정책상 명확히 허용되지 않아 제품 POI 경로에서 제외한다. 카페
> 원장은 Overture Places 월간 서울 ingest, 폐업 보정은 서울시 인허가 데이터로 변경한다.

> **v1.4 (2026-07-11)**: 운영 경로를 확정했다. 지도는 MapLibre GL + OpenFreeMap,
> 카페는 Overture Places의 버전 있는 서울 원장을 서버에 적재·캐시해 제공한다. 서울
> 실시간 도시데이터는 공식 121개 장소를 모두 10분마다 폴링한다. Naver/Kakao/Google
> 링크는 검증된 공급자별 장소 ID 또는 canonical detail URL이 있는 경우에만 표시하며,
> 이름 검색 링크로 임의의 다른 매장을 연결하지 않는다.

> **v1.5 (2026-07-11)**: 서울 이후 작업을 엔진 신뢰도·정확도, 국내 확대, 해외 확대의
> 세 트랙으로 분리했다. 세 트랙은 유니버설 observation/coverage 계약과 공통 성능
> 플랫폼을 공유하며, 카페 찾기·지역 밀집도·데이터 커버리지 지도 모드를 제품 방향으로
> 채택했다. 상세 우선순위와 게이트는 `docs/ROADMAP.md`와 `docs/tracks/`에서 관리한다.

> **v1.6 (2026-07-12)**: 외부감사 결과를 반영해 Phase 6 실측과 실시간 production을
> 최우선으로 올리고 국내·해외 확장 작업을 동결했다. 독립 구현은 병렬로 허용하되 공개,
> 기본 경로 전환과 완료 선언은 선행 게이트 통과 후에만 허용하도록 ADR-0007로 규칙을
> 현실과 일치시켰다.

> **v1.7 (2026-07-12)**: Phase 6의 정답 라벨을 제품 정의와 일치시켰다. 주변 보행
> 혼잡은 엔진의 주 검증 라벨, 매장 좌석 혼잡은 제품 효용을 보는 선택 보조 라벨로
> 분리한다. 현장 순차 관측을 지원하도록 실제 관측 시각과 순위 비교용 슬롯을 분리하고,
> 과거 스냅샷 재생과 슬롯별 지표 계약을 확정했다.

> **v1.8 (2026-07-12)**: 공개 v1을 유지한 채 공식 polygon 기반
> `v2-polygon-shadow`, 분해된 Confidence V2와 paired promotion gate를 추가했다.
> 정답 없는 구조 비교는 정확도 증거에서 분리하며, Phase 6와 targeted divergence 감사의
> 표본 역할을 섞지 않는다.

---

## 0. 이 문서의 사용법 (에이전트 필독)

- 이 문서가 **source of truth**다. 구현 중 실측 결과와 문서가 충돌하면, 문서를 수정하고 `docs/VERIFICATION.md`에 기록한 뒤 진행한다.
- 서울 이후 병렬 트랙의 범위와 순서는 `docs/ROADMAP.md`, 세부 실행 항목은 `docs/tracks/`가 담당한다. 충돌 시 이 문서와 Accepted ADR이 우선한다.
- `[VERIFY]` 태그가 붙은 항목은 학습 지식 기반 추정이므로 **Phase 0에서 실제 API 응답으로 반드시 검증 후 확정**한다.
- `[HUMAN]` 태그가 붙은 작업은 사람(사용자)만 할 수 있다. 해당 작업이 블로커면 멈추고 사용자에게 요청한다.
- 독립적이고 되돌릴 수 있는 구현·테스트는 Phase 간 병렬로 진행할 수 있다. 사용자 공개, 기본 경로 전환, source 승격과 Phase 완료 선언은 선행 **DoD(Definition of Done)**를 모두 통과한 뒤에만 허용한다.
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

- **지역/지도 UX**: 사용자는 서울 전역을 일반 지도처럼 자유롭게 이동한다. 지도는 MapLibre GL + OpenFreeMap으로 렌더링하고, 카페는 서버의 검증·캐시된 원장에서 viewport bbox로 읽는다. 실시간 혼잡도는 121개 공식 핫스팟 coverage 안에서만 제공하고 나머지는 회색 `uncovered`로 정직하게 표시한다.
- **사용자 플로우**: 지도 열기 → 카페 마커의 4단계 색상으로 주변 혼잡도 확인 → 마커 클릭 → 근거 패널(기준 핫스팟, 거리, 레벨, 갱신 시각, 12시간 추이, 1시간 뒤 예측).
- **장소 확인 링크**: 상세 패널은 해당 공급자의 검증된 장소 식별자가 있는 경우에만 그 공급자의 **직접 상세 페이지** 링크를 보인다. 공급자 ID가 없으면 링크를 숨긴다. 이름·좌표를 넣은 검색 URL, 스크레이핑, 브라우저 자동화로 링크를 만들지 않는다.

---

## 2. 데이터 소스 명세

| 소스 | 용도 | 갱신 주기 | 접근 방식 | Phase |
|---|---|---|---|---|
| 서울 실시간 도시데이터 (열린데이터광장) | 핫스팟별 실시간 인구 혼잡도 + 12h 예측 | 인구 5분 / 상권 10분 | REST API, 인증키 | P0~P1 |
| Overture Maps Places | 카페 POI 원장(이름·좌표·주소·전화·GERS ID) | 월간 릴리스 | GeoParquet 서울 bbox ingest → PostgreSQL 캐시 | P2 |
| 서울시 지방행정 인허가(휴게음식점) | 영업상태·폐업 보정 | 일/파일 갱신 | 공공데이터포털 → 서버 캐시 | P2 보정 |
| OpenFreeMap | 지도 베이스맵 | 제공자 갱신 주기 | MapLibre vector style | P5 |
| 서울시 주요 장소(핫스팟) 마스터 | 핫스팟 코드·명칭·WGS84 영역 | 정적 | 열린데이터광장 OA-21285 첨부 파일 | P1 |
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
- 호출 단위 [VERIFIED 2026-07-11]: 공식 OA-21285 설명에 따라 **장소 1곳당 1콜**, 일괄 호출 불가. 장소명 또는 장소코드 중 하나로 호출한다.
- **호출 제한 [VERIFIED 2026-07-11, HUMAN 포털 확인]**: 열린데이터광장 OpenAPI는 1회 호출당 최대 1,000건이며 호출 횟수 제한은 없다. `citydata_ppltn`은 공식적으로 장소 1곳씩 호출하므로 1,000건 행 제한의 영향이 없다. 서울 전역 지도 UX에 맞춰 공식 마스터 **121개 전부**를 10분마다 폴링한다. 예상량은 121 × 144 = **17,424콜/일**이다. 단일 worker는 주기 겹침을 금지하고, cycle duration·성공/실패 수·마지막 완전 cycle 시각을 기록한다.

### 2.2 Overture Places 캐시 원장

- **제품 원장**: Overture Places의 릴리스별 GeoParquet에서 `SEOUL_BBOX`로 후보를 제한하고, 카페 분류·좌표·필수 GERS ID를 검증한 레코드만 PostgreSQL에 upsert한다. `overture_id`, release, confidence, category와 원본 `sources`를 보존하고 로컬 extract SHA-256은 ingest 보고서와 `VERIFICATION.md`에 기록한다.
- **갱신 방식**: 지도 이동이나 사용자 요청 때 Overture/외부 POI API를 호출하지 않는다. 월간 릴리스 ingest와 인허가 보정은 별도 job으로 실행하고, API는 캐시된 `cafes`/`cafe_scores`만 bbox 조회한다. 실패한 release는 현재 검증된 캐시를 계속 제공하며, 부분 파일이나 검증 실패 결과로 원장을 비우지 않는다.
- **정합성 경계**: Overture의 영업 상태가 비어 있거나 오래될 수 있으므로 서울시 휴게음식점 인허가 데이터로 후속 보정한다. 이름·좌표 유사도만으로 타 공급자 레코드와 병합하거나 ID를 만들어내지 않는다.
- **공급자 상세 링크**: `external_links_json`에는 공급자가 제공하거나 합법적 계약/공식 API로 검증된 canonical detail URL만 저장한다. Naver/Kakao/Google 각각의 ID가 없으면 해당 버튼은 없다. API는 HTTPS·provider host·detail path를 allowlist로 재검증하며 검색 URL은 제거한다.

### 2.3 Kakao Local API — legacy 검증 기록, 제품 경로 아님

- 다음 내용은 Phase 0에서 실제 응답과 키 활성화 상태를 검증한 **역사적 증거**다. MapLibre/OpenFreeMap 지도에 Kakao Local 결과를 표시·캐시·원장화하는 제품 기능에는 사용하지 않는다. 새 기능은 이 API를 호출하지 않으며 `KAKAO_*` 환경 변수나 JavaScript 키 등록을 요구하지 않는다.
- 카테고리 검색 [VERIFIED 2026-07-11]: `GET https://dapi.kakao.com/v2/local/search/category.json`
  - 헤더: `Authorization: KakaoAK {REST_KEY}`
  - 파라미터: `category_group_code=CE7`, `x`(경도), `y`(위도), `radius`(m, 최대 20000), `page`, `size`(≤15)
- 응답 구조 [VERIFIED 2026-07-11]: root `meta` + `documents[]`. `meta`는 `total_count`, `pageable_count`, `is_end`, `same_name`; document는 `id`, `place_name`, `category_*`, 주소, 전화, `x`, `y`, `place_url`, `distance`를 포함했다.
- **핵심 제약 [VERIFIED 2026-07-11]**: 광화문 반경 1km CE7 검색에서 `total_count=761`, `pageable_count=45`; size 15 기준 3페이지에 `is_end=true`를 확인했다. 이 한계와 비-Kakao 지도 표시/장기 저장 정책 불확실성 때문에 CE7 재귀 분할 수집안은 **채택하지 않았다**. 과거 fixture·검증 코드는 회귀 및 기록 목적 외 제품 실행 경로에서 사용하지 않는다.

### 2.4 서울시 주요 121장소 마스터

- 공식 데이터셋: [서울시 실시간 도시데이터 OA-21285](https://data.seoul.go.kr/dataList/OA-21285/A/1/datasetView.do)
- [VERIFIED 2026-07-11] 첨부 `서울시 주요 121장소 목록.xlsx`(seq 23)와 `서울시 주요 121장소 영역.zip`(seq 24), 게시일 2026-04-02를 확인했다.
- [VERIFIED 2026-07-11] XLSX는 `CATEGORY`, `NO`, `AREA_CD`, `AREA_NM`, `ENG_NM`의 121개 레코드다. 영역 ZIP은 같은 121개 장소의 Shapefile(`.shp/.shx/.dbf/.prj/.cpg`)이며 WGS84 경위도(`GCS_WGS_1984`)다.
- 목록에는 중심 좌표가 없으므로 Phase 1에서 WGS84 폴리곤을 검증하고, invalid topology는 `make_valid`로 정규화한 뒤 내부 대표점(`representative_point`)을 `hotspots.lat/lng`로 사용한다. 정규화할 수 없거나 geometry가 누락되면 자동 fallback하지 않고 seed를 중단해 HUMAN 결정을 요청한다. 결정 근거는 `docs/adr/ADR-0002-hotspot-location.md`에 기록한다.

---

## 3. 핵심 설계 결정 (D1~D10)

- **D1. 혼잡도는 카페의 속성이 아니라 카페가 위치한 지역의 속성이다.** 카페 마커의 색은 "이 카페가 붐빈다"가 아니라 "이 카페 주변 지역이 붐빈다"를 의미하며, UI 문구도 이를 따른다("주변 혼잡도").
- **D2. 공간 매핑 = 거리 가중 보간(IDW).** 카페 기준 반경 `R_MAX` 내 최근접 핫스팟 최대 `K`곳의 레벨을 역거리제곱 가중 평균한다. (공식은 §4.5)
- **D3. 신뢰도는 별도 축이다.** 신뢰도 = f(최근접 핫스팟 거리, 데이터 신선도, 기여 핫스팟 수). 혼잡도 레벨과 독립적으로 계산·표시한다.
- **D4. 커버리지 3단계.** `covered`(d_min ≤ 600m) / `fringe`(600m < d_min ≤ R_MAX, "참고용" 라벨) / `uncovered`(d_min > R_MAX, 회색 마커 + "데이터 없음"). 미커버를 억지로 채색하지 않는다.
- **D5. 레벨 수치화.** `여유=1, 보통=2, 약간 붐빔=3, 붐빔=4`. 보간은 연속값으로 하고 표시 시 반올림.
- **D6. 근거 노출은 필수 기능이다.** 상세 패널에 기준 핫스팟명·거리·원본 레벨·갱신 시각을 항상 표시. 이것이 "러프한 추정"을 정당화하는 장치다.
- **D7. 스코어링은 결정적 순수 함수로.** 동일 입력(카페 좌표 + 스냅샷 셋 + config) → 동일 출력. ML 없음(MVP). 고정 fixture 기반 스냅샷 테스트로 회귀를 잡는다.
- **D8. 지도 모드는 카페 찾기·지역 밀집도·데이터 커버리지로 분리한다.** 세 모드는 같은 정규화 관측을 사용하지만 표현 목적을 섞지 않는다.
- **D9. 국내·해외 확장은 유니버설 공급자 계약을 사용한다.** 장소 원장, 혼잡 관측, 커버리지와 도시 설정을 공통 계약으로 분리하고 도시명 기반 조건문을 금지한다.
- **D10. 입력 품질과 검증된 정확도 확률을 구분한다.** calibration을 통과하기 전의 confidence는 근거 충분도이며, 실제 적중 확률처럼 표현하지 않는다.

---

## 4. 시스템 아키텍처

### 4.1 컴포넌트 흐름

```text
[single congestion worker: 10분 non-overlapping cycle]
  ingest/poller ──> 서울 citydata API (공식 121개, 장소별 1콜)
       │ 파싱/대상 불일치 시 hotspot_parse_failures에 raw 저장 + 경고 로그
       ▼
  hotspot_snapshots (append-only)
       │ 인제스트 훅
       ▼
  scoring/engine.materialize_all() ──> cafe_scores (전체 재계산, upsert)
       ▲
  cafes (P2 Overture release cache + 인허가 보정)

[catalog worker: release/event driven]
  Overture Places + 서울 인허가 ──> 검증·release metadata 보존 ──> cafes

[요청 시]
  frontend (MapLibre GL + OpenFreeMap) ──> FastAPI ──> cached cafes + cafe_scores 조인 반환
```

### 4.2 기술 스택

- Backend: Python 3.12, FastAPI, SQLAlchemy 2 + PostgreSQL(운영·통합테스트 기준, 필요 시 PostGIS), httpx, pydantic v2, APScheduler(개발 단일 프로세스만; 운영은 단일 ingest worker로 분리)
- Frontend: Vite + TypeScript(vanilla), MapLibre GL JS + OpenFreeMap style. 브라우저 Geolocation control로 내 위치 이동을 제공하며 권한 거부 시 지도 기능은 계속 동작한다.
- 테스트: pytest. **자동 테스트에서 실 API 호출 금지** — Seoul/Overture/인허가 입력은 versioned fixture 또는 로컬 test data를 사용한다. Phase 검증용 명시적 실호출은 원본 보존·secret-safe logging 조건에서만 수행한다. 지도 SDK와 타일 style은 빌드·수동 브라우저 확인을 분리 기록한다.

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
│   │   │   └── seoul_citydata.py   # 실시간 외부 호출은 여기로만
│   │   ├── ingest/poller.py
│   │   ├── ingest/worker.py   # 단일 스케줄러 프로세스
│   │   ├── ingest/overture_places.py # Overture release cache와 검증
│   │   ├── scoring/engine.py  # 순수 함수 + materialize
│   │   └── api/routes.py
│   ├── scripts/
│   │   ├── verify_apis.py     # P0: 실측 → fixtures 저장
│   │   ├── seed_hotspots.py   # P1
│   │   ├── seed_cafes.py      # P2 Overture download/dry-run/apply
│   │   ├── materialize_scores.py # P3 offline recompute
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

### 4.4 DB 스키마 의도 (비규범 DDL 초안)

실행 schema의 유일한 source of truth는 `backend/migrations/`의 Alembic migration이다.
아래 DDL은 제품 의도와 주요 필드를 설명할 뿐 migration 적용 또는 schema 검증에 사용하지
않는다. 차이가 있으면 migration과 SQLAlchemy 모델을 따르고 이 설명을 갱신한다.

```sql
CREATE TABLE hotspots (
  id INTEGER PRIMARY KEY,
  area_cd TEXT UNIQUE,          -- 공식 마스터의 POI001~POI131 중 121개 코드
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

CREATE TABLE hotspot_parse_failures (
  id INTEGER PRIMARY KEY,
  hotspot_id INTEGER NOT NULL REFERENCES hotspots(id),
  fetched_at TIMESTAMPTZ NOT NULL,
  error_type TEXT NOT NULL,
  error_message TEXT NOT NULL,  -- raw 값을 포함하지 않는 안전한 요약
  raw_json JSONB NOT NULL
);
CREATE INDEX ix_parse_failure_hotspot_time ON hotspot_parse_failures(hotspot_id, fetched_at DESC);

CREATE TABLE cafes (
  id INTEGER PRIMARY KEY,
  overture_id TEXT UNIQUE NOT NULL, -- Overture GERS ID
  source_release TEXT NOT NULL,
  source_confidence REAL NOT NULL,
  primary_category TEXT NOT NULL,
  name TEXT NOT NULL,
  lat REAL NOT NULL, lng REAL NOT NULL,
  road_address TEXT, phone TEXT, website TEXT,
  source_json JSONB,
  external_links_json JSONB,
  active INTEGER DEFAULT 1
);
CREATE INDEX ix_cafes_bbox ON cafes(lng, lat);
CREATE INDEX ix_cafes_active_bbox ON cafes(active, lng, lat);

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

이웃이 0곳이면 `coverage=uncovered`이며 level/score/confidence/confidence_tier,
primary hotspot/distance, contributors evidence를 모두 NULL 처리한다.

### 4.6 `config.py` 기본값 (전부 P6 캘리브레이션 대상)

| 상수 | 기본값 | 의미 |
|---|---|---|
| `POLL_INTERVAL_MIN` | 10 | 121개 전체 폴링 주기(분). 주기 겹침 금지 |
| `OFFICIAL_HOTSPOT_COUNT` / `MAX_POLLED_HOTSPOTS` | 121 / 121 | 공식 마스터 전체만 폴링 |
| `SEOUL_BBOX` | `(126.76, 37.41, 127.20, 37.72)` | Overture bulk ingest의 전세계 오조회 방지용 guard; 행정경계 정밀 필터는 P2 검증 |
| `R_MAX_M` | 1500 | 이웃 탐색 최대 반경 |
| `COVERED_M` | 600 | covered 판정 거리 |
| `K_NEIGHBORS` | 3 | 최대 기여 핫스팟 수 |
| `D_FLOOR_M` | 50 | 거리 하한 (0-나눗셈 방지) |
| `TAU_MIN` | 15 | 신선도 감쇠 시상수(분) |
| `CONF_HIGH` / `CONF_MID` | 0.55 / 0.30 | 신뢰도 등급 경계 |
| `STALE_WARN_MIN` | 25 | 이 이상 갱신 없으면 UI에 stale 배지 |

### 4.7 내부 API 스펙

- `GET /api/cafes?bbox={minLng},{minLat},{maxLng},{maxLat}&min_conf=0`
  → cached 원장만 bbox filter해 `[{id, name, lat, lng, road_address, source_label, level, score, confidence, confidence_tier, coverage, evidence: {hotspot_name, distance_m, observed_at}, external_links: {naver?, kakao?, google?}}]`. 요청 처리 중 외부 POI 호출은 금지한다.
- `GET /api/cafes/{id}`
  → 위 필드 전체 + `contributors[]` + `trend_12h[]`(기준 핫스팟의 스냅샷 시계열) + `forecast_1h`(FCST에서 추출). `external_links`는 검증된 direct detail link만 포함하고 누락 provider 키는 `null`이다.
- `GET /api/hotspots` → 폴링 대상 핫스팟 현재 상태 (디버그 오버레이용)
- `GET /api/health` → `{data_mode, last_ingest_at, last_complete_cycle_at,
  last_cycle_status, last_cycle_targets, last_cycle_saved, last_cycle_failed,
  snapshots_last_hour, cafes_count}`. freshness 승격 판정은 개별 snapshot 시각이 아니라
  `last_complete_cycle_at`을 사용한다.

---

## 5. 개발 페이즈

### Phase 0 — 준비 및 스키마 실측 검증

작업:

1. [HUMAN] 서울열린데이터광장 인증키 발급 → `.env`의 `SEOUL_API_KEY`
2. 레포 스캐폴딩(§4.3 구조), `.env.example`, `config.py` 뼈대
   - 카카오 키/도메인 등록은 P0에 수행한 legacy API 검증의 전제였으며, 현 제품 경로의 요구사항은 아니다.
3. `scripts/verify_apis.py` 작성·실행:
   - citydata 실호출 1건(예: `광화문광장`) → 응답 원본을 `fixtures/citydata_sample.json` 저장
   - `[VERIFY]` 항목 전부 확정: 엔드포인트, 필드명, 혼잡도 라벨 문자열 4종, FCST 구조, `AREA_NM` 정확 표기
   - 카카오 CE7 fixture는 legacy 응답 검증용으로 보존하되, 제품 seed·지도에서 사용하지 않음
   - 121개 장소 마스터 파일 확보 경로 확인(열린데이터광장 내 장소 목록) 및 다운로드
   - 호출 정책 확인 → 121 × 144 = 17,424콜/일 및 non-overlapping worker 정책 확정
4. fixtures 기반 pydantic 외부 API 모델(`schemas.py`) 확정

DoD:

- [x] fixtures 2종 이상 커밋됨
- [x] `docs/VERIFICATION.md`에 확정 스키마·라벨 문자열·호출 제한·폴링 주기 결정 기록
- [x] 문서의 [VERIFY] 항목이 전부 해소되어 본 문서 갱신됨

### Phase 1 — 핫스팟 인제스트 파이프라인

작업:

1. `seed_hotspots.py`: XLSX 장소 마스터와 WGS84 Shapefile을 `AREA_CD`로 결합하고 각 폴리곤을 검증/정규화한 내부 대표점을 `hotspots.lat/lng`로 적재. 정규화 실패·geometry 누락·DB의 공식 마스터 외 코드는 자동 수정하지 않고 중단한다. CLI 기본은 dry-run이며 **수동 검수 목록 출력 → [HUMAN] 확인 → `--apply`** 순서만 허용한다.
2. 공식 마스터 121개 전부에 `is_polled=1`을 설정한다. 동네 중심점/반경은 더 이상 폴링 범위를 제한하지 않는다.
3. `ingest/poller.py`와 단일 `ingest/worker.py`: APScheduler로 `POLL_INTERVAL_MIN`마다 공식 121개를 호출 → 요청한 AREA_CD/AREA_NM 일치 검증 → 파싱 → `hotspot_snapshots` append. fetch 실패는 3회 지수 백오프 후 로그를 남기고, fetch 성공 후 파싱/대상 불일치는 재호출 없이 raw를 `hotspot_parse_failures`에 append한다. 한 대상 실패 후 다음 대상으로 진행하며 FastAPI 프로세스에서는 스케줄러를 기동하지 않는다. 새 cycle은 이전 cycle 완료 전 시작하지 않으며 cycle metric을 기록한다.
4. 파서 유닛테스트 (fixture 기반)

DoD:

- [ ] 로컬에서 1시간 무인 구동 → 121개 대상 각각 스냅샷 ≥5개 적재
- [ ] 잘못된 응답(fixture 변조) 주입 시 크래시 없이 스킵·로깅
- [ ] 121개 전체 cycle이 10분 안에 완료되고, 일일 예상 17,424콜과 호출 정책을 `VERIFICATION.md`에 기록

### Phase 2 — 카페 시드

작업:

1. `seed_cafes.py --download`: 지정한 Overture release를 서울 bbox·카페 category·confidence threshold로 제한해 immutable local GeoParquet cache를 만든다. 기존 파일은 덮어쓰지 않는다.
2. `overture_id`로 멱등 upsert하고 release·confidence·category·sources를 보존한다. 빈 extract는 적용을 거부하고, 새 release에서 사라진 레코드는 soft deactivate한다. dry-run이 기본이다.
3. ingest 보고서는 release, SHA-256, 입력/신규/갱신/유지/비활성 수를 출력한다.
4. 서울시 휴게음식점 인허가 보정은 후속 P2 작업이다. 이름/좌표 fuzzy match는 provider ID나 외부 상세 링크 생성에 사용하지 않는다.
5. `external_links_json`은 검증된 canonical detail URL만 허용하며 API가 provider별 host/path를 다시 검증한다.

DoD:

- [x] 선택한 Overture release의 고신뢰 서울 cache 4,933건 적재; release/hash/건수가 `VERIFICATION.md`에 기록됨
- [ ] 무작위 50곳의 이름·좌표·주소를 원본과 spot check하고, 카페가 아닌 분류/서울 밖 좌표는 격리됨
- [ ] 재실행과 동일 release 재시도는 멱등이며, 실패/부분 release가 기존 cache를 비우지 않음
- [ ] provider ID 없는 카페에서 Naver/Kakao/Google 링크가 숨겨지고, 있는 링크는 해당 provider direct detail URL만 사용함

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

작업: §4.7 엔드포인트 구현, CORS(개발 localhost:5188 및 허용 tailnet origin), bbox 필터는 SQL where/PostGIS 전환 가능 경계로, 응답 pydantic 스키마를 고정한다. `/api/cafes`와 상세는 DB cache만 읽으며 요청 중 외부 provider를 호출하지 않는다. 응답은 `Cache-Control: private, max-age=30, stale-while-revalidate=60`을 사용한다. Redis는 실제 병목이 확인될 때만 추가한다.

DoD:

- [ ] `/docs` OpenAPI에서 4개 엔드포인트 동작 확인
- [ ] bbox 쿼리 로컬 p95 < 100ms
- [ ] health가 마지막 인제스트 시각을 정확히 반환
- [ ] 같은 bbox 요청이 외부 POI 호출 없이 cache/DB만 읽고, catalog release와 score 갱신이 바뀌면 응답 cache가 무효화됨

### Phase 5 — 프론트엔드 지도

작업:

1. MapLibre GL에 OpenFreeMap style을 로드한다. 초기 뷰는 홍대이되, 사용자는 서울 전역을 자유롭게 이동한다. Kakao Maps SDK와 JavaScript 키는 로드하지 않는다.
2. bbox 변경(이동/줌 종료) 시 `/api/cafes` 재조회, 마커 렌더
3. 마커: level 1~4 → 4색(초록→노랑→주황→빨강 계열, 프론트 재량). `coverage=fringe` → 테두리 점선, `uncovered` → 회색. `confidence_tier=low` → 투명도 60%
4. 내 위치 버튼은 브라우저 위치 권한을 요청해 현 위치로 이동하고 정확도 원을 표시한다. 거부·오류 시 명확한 안내만 보이고 지도나 카페 조회는 중단하지 않는다.
5. 마커 클릭 → 근거 패널: 카페명, 원장 출처/release, 주변 혼잡 레벨 문구("○○ 기준 · 620m · 8분 전 갱신"), 신뢰도 뱃지, 12h 미니 추이(단순 스파크라인), 1시간 뒤 예측. provider ID가 검증된 경우에만 해당 provider의 직접 상세 링크 버튼을 보인다.
6. `STALE_WARN_MIN` 초과 시 상단 배너 "데이터 갱신 지연 중"

DoD (수동 시나리오 체크리스트를 docs에 기록):

- [ ] 지도 이동/줌 시 마커 갱신
- [ ] covered/fringe/uncovered 3종이 시각적으로 구분됨
- [ ] 근거 패널에 D6 요소(핫스팟명·거리·갱신시각) 전부 표시
- [ ] 내 위치 권한 허용/거부 모두에서 예측 가능한 UX, provider ID 누락 시 외부 링크 미표시
- [ ] 백엔드 중단 상태에서 프론트가 죽지 않고 에러 표시

### Phase 6 — 캘리브레이션 및 검증 (eval)

프로토콜:

1. 축소 기준선 후보는 `scripts/select_eval_candidates.py`로 고정한다. 홍대·성수 2개
   핫스팟 × 근접(`≤ EVAL_NEAR_MAX_M`)·중간(`≤ COVERED_M`)·외곽(`≤ R_MAX_M`)
   3개 거리대 × 각 4곳, 최대 24곳이다. 선택은 POI source confidence, 거리, cafe ID
   순서의 결정적 규칙을 사용한다. 현장 확인에서 잘못된 POI나 관측 불가 매장을 발견하면
   행을 지우지 않고 `poi_valid`와 `exclusion_reason`을 기록한다.
2. 축소 기준선은 동네별 3개 관측 세션을 목표로 한다. 같은 동네·세션에서 순위를 비교할
   카페들은 동일한 `slot` ID를 사용하고, 각 행의 `observed_at`에는 실제 현장 시각을
   timezone 포함 ISO 8601로 기록한다. 순차 관측을 같은 시각으로 조작하지 않는다. 각
   세션은 서로 다른 두 관측자가 수행한다. 모든 카페에는 `primary` 관측자를 정확히 한 명
   배정하고, 거리대별 최소 한 곳과 세션마다 순환하는 추가 거리대 한 곳을 합쳐 네 곳에는
   다른 관측자의 `reliability` 관측을 추가한다. 두 관측자는 서로의 라벨을 보지 않고
   독립적으로 기록한다.
3. **주 라벨 `observed_area_level`**은 카페 좌표 반경
   `EVAL_OBSERVATION_RADIUS_M` 안의 보행 흐름을 `EVAL_OBSERVATION_DURATION_MIN`분
   관측한다. 입구와 가장 가까운 주 보행로에 고정 가상선을 두고 통과 인원을 세며,
   `EVAL_AREA_PEDESTRIANS_PER_MIN_THRESHOLDS=(5, 15, 30)`을 기준으로
   1=한산, 2=보통, 3=혼잡, 4=매우 혼잡으로 기록한다. 보행 회피가 반복되면 최소 3,
   정체나 외부 대기열이 보행을 지속해서 막으면 4로 올리고 이유를 적는다. 보행자 수/분,
   흐름 방해 여부와 관측자 메모를 원본 CSV에 함께 보존한다. 이는 엔진 정확도의 유일한
   정답 라벨이다.
4. **선택 라벨 `observed_venue_level`**은 입장 또는 외부에서 신뢰성 있게 확인할 수 있을
   때만 1=좌석 여유 많음(<30%), 2=적당(30~60%), 3=거의 참(60~90%),
   4=만석·대기(>90%)로 기록한다. 지역 혼잡 예측과의 연관성을 보는 제품 효용 지표이며,
   엔진 정확도와 합산하거나 같은 주장으로 표현하지 않는다. `[HUMAN]`
5. `scripts/run_eval.py`는 필수 CSV 필드
   `cafe_id, observed_at, slot, observer_id, observation_role,
   observed_area_level, pedestrians_per_min, flow_obstruction, observer_notes`와 선택 필드
   `observed_venue_level`을 받는다. `observation_role`은 `primary|reliability`만 허용한다.
   동일 카페·슬롯에는 `primary`가 정확히 한 행이어야 하며, 동일 관측자·카페·슬롯 중복과
   주 관측 없는 신뢰도 관측은 계약 오류로 평가를 중단한다.
   `observed_area_level`은 보행량 임계값과 흐름 방해 규칙으로 다시 계산한 값과 일치해야
   하며, 불일치 행은 평가에서 제외한다. 각 실제 관측 시각까지 `observed_at`과
   `fetched_at`이 모두 도달한 스냅샷만 재생해 미래 데이터 누출을 막는다.
   - **주 지표:** `primary` 행만 사용해 슬롯별 Spearman을 계산한 뒤 슬롯 간 macro average
   - **주 보조 지표:** 지역 라벨 기준 `|pred - obs| ≤ 1` 비율
   - **제품 효용:** 매장 라벨이 있는 표본만 별도 Spearman·한 단계 이내 비율
   - **관측자 신뢰도:** 같은 카페·슬롯의 `primary`/`reliability` 쌍으로 quadratic
     weighted Cohen's kappa를 별도 계산한다. 두 쌍 미만이거나 어느 한쪽 라벨에 분산이
     없으면 `N/A`로 보고한다.
   - 거리 구간별 표본 수와 주 지표 분해
6. 첫 축소 기준선은 파라미터를 바꾸지 않은 `v1-idw-point`의 홀드아웃 결과로 남긴다.
   그 다음 `R_MAX, COVERED_M, K, TAU`를 별도 튜닝셋에서 그리드 재계산하고, 최종
   검증셋을 본 뒤 같은 데이터에 재튜닝하지 않는다.

DoD:

- [ ] 결정적 후보 목록과 POI 유효성 검수 기록
- [ ] 동네별 3개 슬롯의 현장 관측 원본과 `docs/EVAL_REPORT.md` 커밋
- [ ] `v1-idw-point`의 지역 혼잡 Spearman, 한 단계 이내 정확도, 거리 분해와 표본 수 기록
- [ ] 중복 관측 weighted Cohen's kappa ≥ 0.60 또는 `N/A` 사유와 추가 관측 계획 기록
- [ ] 합격선: 지역 혼잡 Spearman ≥ 0.5 (미달 시 원인 가설 3개와 개선안을 리포트에 명시 — 실패도 산출물)

### Backlog (MVP 이후, 우선순위순)

1. **"평소 대비" 베이스라인**: 스냅샷이 2~3주 쌓이면 핫스팟별 요일×시간 평균 → "평소보다 붐빔/한산" 상대 표시 (자체 데이터만으로 가능, 외부 의존 없음)
2. FCST 12h 예측 활용 확대: "몇 시에 가면 여유" 추천
3. 프론트 "폐업 신고" 버튼 + 인허가 보정 불일치 검토 큐
4. 원탭 체크인("자리 있어요/없어요") → ground truth 축적 → 카페별 보정계수
5. 날씨 보정(기상청), 커버 동네 확장, PostGIS 공간 인덱스/읽기 replica(실제 병목 확인 시)

---

## 6. 리스크와 대응

| 리스크 | 영향 | 대응 |
|---|---|---|
| 121개 폴링 cycle 지연 또는 정책 변경 | 혼잡도 stale/부분 갱신 | 현재 호출 횟수 제한 없음 확인. 121 × 144 = 17,424콜/일과 cycle duration·실패 수를 기록하고 cycle overlap을 금지한다. 정책 변경 또는 10분 초과 시 concurrency/backoff·주기를 config/ADR로 조정한다. |
| citydata 스키마 변경 | 파싱 실패 | pydantic strict 파싱 + 실패 시 raw_json 보존, 알림 로그. 문서 갱신 절차(§0) |
| POI 원장 stale·오분류·부분 release | 잘못된 카페/위치 또는 목록 소실 | versioned Overture cache, source hash, 서울 bbox·분류 검증, quarantine, release atomicity를 적용한다. 인허가 보정과 표본 검수 전에는 원본 release를 active로 승격하지 않는다. |
| provider 이름 검색 링크 오매칭 | 사용자가 다른 매장 상세를 봄 | 검색 URL 금지. 검증된 provider ID/canonical direct URL이 없으면 버튼을 숨긴다. |
| 핫스팟 신호와 골목 카페 괴리 (역 앞은 붐비는데 골목은 한산) | 추정 오차 | 이것이 P6 검증의 존재 이유. 거리 구간별 분해로 정량화, R/COVERED 튜닝, 한계는 confidence로 표현 |
| 데이터 지연/장애 | 낡은 값 표시 | freshness 감쇠 + STALE 배지. 절대 마지막 값을 "현재"처럼 위장하지 않음 |
| 폐업 카페 표시 | 신뢰 하락 | P2 인허가 cache 보정과 release별 soft deactivate를 적용하고, 미확정은 원장 출처/갱신 시각을 표시한다. |

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
