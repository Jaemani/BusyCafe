# Track 3 — 해외 확대와 유니버설 도시 혼잡 플랫폼

- 상태: Feasibility complete, runtime implementation frozen
- 작성일: 2026-07-11
- 선행 조건: Track 1의 신뢰도 모델과 Track 2의 국내 확장 검증
- 결정 상태: 공통 아키텍처와 단계는 ADR-0006에서 채택했다. 도시별 데이터의 권리, 품질, 갱신 주기와 비용은 실제 자료와 응답을 확인하기 전까지 `[VERIFY]`로 유지한다.

> **2026-07-13 순서 변경:** ADR-0010이 contract-first 순서를 대체한다. 현재
> `universal_contracts.py`는 experimental seam inventory이며, Melbourne 등 두 번째
> provider의 실응답 fixture 전에는 필드·Protocol·DB migration을 확장하지 않는다.

## 1. 목표

서울 전용 구현을 복제하지 않고, 동일한 제품과 코어 엔진으로 국내외 도시를 단계적으로 지원한다. 사용자는 지원 지역에서 다음 세 가지 모드를 이용할 수 있어야 한다.

1. **카페 찾기**: 주변 지역이 상대적으로 한산할 가능성이 높은 카페를 근거와 함께 비교한다.
2. **지역 밀집도**: 카페와 무관하게 현재 혼잡한 구역과 상대적으로 여유로운 구역을 지도에서 본다.
3. **데이터 커버리지**: 실시간·지연·미지원 구역과 데이터의 출처 및 신뢰도를 확인한다.

해외 확대의 핵심은 세계 카페 목록을 먼저 많이 보여주는 것이 아니다. 카페 POI와 혼잡 관측의 품질 및 권리를 분리해 검증하고, 근거가 있는 도시와 구역에만 혼잡도를 제공하는 것이다.

## 2. 타당성 판단

### 2.1 글로벌 POI와 혼잡 데이터는 확장 난도가 다르다

카페 POI는 세계 공통 스키마로 정규화하기 비교적 쉽다. 현재 사용하는 Overture Places가 여러 국가를 다루고 릴리스 ID와 원본 출처를 제공한다는 전제는 있으나, 국가별 카페 분류 정확도·상호·좌표·영업 상태·상업적 표시 및 재배포 조건은 출시 국가마다 확인해야 한다. `[VERIFY]`

혼잡 데이터는 보편적인 단일 공급원이 있다고 가정하지 않는다. 도시 또는 국가마다 다음과 같은 서로 다른 관측이 제공될 수 있다.

- 공공기관의 구역별 실시간 보행 인구
- 고정 센서 또는 카메라에서 집계한 익명 통행량
- 통신 기반 유동 인구
- 대중교통 승하차량
- 행사·날씨·도로 상태와 같은 보조 신호
- 상업 데이터 공급자의 구역별 추정치

각 신호의 의미와 공간·시간 단위가 다르므로 같은 숫자로 직접 합치지 않는다. 대중교통 승하차량이나 행사 정보만 있는 도시는 이를 실제 거리 혼잡도의 정답처럼 표시하지 않으며, 검증 전에는 보조 신호 또는 `catalog_only` 상태로 둔다.

### 2.2 해외 버전은 가능하지만 도시 단위로 출시한다

지도와 POI 카탈로그는 국제화할 수 있다. 실시간 혼잡도는 데이터 계약과 품질을 통과한 도시만 활성화한다. 따라서 제품의 확장 단위는 국가 전체가 아니라 `region` 또는 `city profile`이다.

동일 애플리케이션은 다음 상태를 구분한다.

| 상태 | 제공 기능 | 사용자 표시 |
|---|---|---|
| `catalog_only` | 지도와 검증된 카페 POI | 혼잡도 미지원 |
| `pilot` | 제한 구역의 실시간 또는 준실시간 혼잡도 | 내부 또는 제한 베타, 출처·커버리지·지연 명시 |
| `live` | 품질·운영 기준을 통과한 혼잡도 | 공식 지원 지역 |
| `suspended` | 데이터 장애 또는 권리 만료 | 마지막 값을 현재처럼 표시하지 않고 일시 중단 |

## 3. 유니버설 아키텍처

### 3.1 원칙

- 국가별 차이는 provider adapter와 city profile에 격리한다. 스코어링 코어, API와 지도 UI에 `seoul`, `korea` 또는 특정 공급자 조건문을 늘리지 않는다.
- 원본 값과 정규화 값을 모두 보존한다. 정규화 실패를 임의 기본값으로 대체하지 않는다.
- 데이터 출처와 관측 의미가 다른 경우 동일한 `confidence`로 포장하지 않는다.
- 시각은 UTC로 저장하고, 관측 지역의 IANA timezone으로 사용자에게 표시한다.
- 라이선스와 커버리지는 데이터와 함께 버전 관리한다. 권리가 확인되지 않은 소스는 ingest하지 않는다.
- 카페 스코어는 지역 밀집도 관측의 소비자 중 하나로 둔다. 밀집도 지도는 카페 원장이 없어도 동작할 수 있어야 한다.

### 3.2 Provider contract

다음 인터페이스를 공통 경계로 둔다. 실제 Python protocol과 Pydantic 스키마는 Track 1·2의 구현과 함께 확정한다.

#### `PlaceCatalogProvider`

- `discover(region, release)`: 지정 영역과 릴리스의 장소 후보를 가져온다.
- `normalize(raw_place)`: 공급자 레코드를 공통 장소 스키마로 변환한다.
- `validate(place, region)`: 좌표·분류·필수 ID와 지역 경계를 검증한다.
- `license_manifest(release)`: 출처, 표시 의무, 저장·재배포 가능 범위와 만료 조건을 반환한다.

공통 장소 레코드는 `provider`, `provider_place_id`, `source_release`, `categories`, `name`의 언어별 값, 좌표, 주소, 연락처, 공급자 신뢰도, 원본 출처와 검증 상태를 가진다. 외부 지도 상세 링크는 현재 정책과 같이 검증된 canonical URL 또는 provider ID가 있을 때만 제공한다.

#### `CrowdObservationProvider`

- `list_areas(region)`: 공급자가 관측하는 구역과 geometry를 버전과 함께 반환한다.
- `fetch_observations(area_ids, cursor)`: 신규 관측과 공급자 cursor를 반환한다.
- `normalize(raw_observation)`: 원본 단위·레벨·시간을 공통 관측 스키마로 변환한다.
- `health()`: 공급자 지연, 마지막 성공, 오류율과 정책상 호출 한도를 반환한다.

공통 관측은 최소한 `provider`, `region_id`, `area_id`, `geometry_version`, `observed_at`, `fetched_at`, 원본 값·단위·라벨, 정규화 수준, 데이터 품질 플래그, 예상 지연과 provenance를 보존한다. 인구수, 인구밀도, 통행량과 공급자 혼잡 레벨은 서로 다른 `observation_type`으로 저장한다.

#### `CoverageProvider`

- 특정 시점의 지원 geometry와 `live`, `delayed`, `stale`, `unsupported` 상태를 제공한다.
- 데이터 장애·계약 만료·스키마 변경 시 기존 coverage를 자동으로 `live`로 유지하지 않는다.
- 카페 점수와 밀집도 타일은 같은 coverage snapshot을 참조한다.

#### `RegionProfile`

지역별 설정은 `region_id`, 국가·도시 코드, 행정경계, IANA timezone, 기본 locale, 지원 언어, 단위 체계, provider 연결, freshness SLA, 레벨 정의와 공개 상태를 포함한다. 비밀키는 profile에 저장하지 않고 배포 환경의 secret reference만 사용한다.

### 3.3 데이터 흐름

```text
[provider별 ingest worker]
  raw payload 저장 → strict normalize → immutable observations
                                      │
                    region baseline / quality calibration
                                      │
                         normalized density surface
                         ┌────────────┴────────────┐
                         │                         │
                 density vector tiles      place scoring engine
                         │                         │
                         └────────────┬────────────┘
                                      │
                          공통 read API + MapLibre
```

공급자별 worker는 서로 독립적으로 배포하고 중복 cycle을 금지한다. 한 도시의 장애가 다른 도시 ingest를 멈추지 않게 queue 또는 region 단위 job 경계를 둔다. 정규화 모델 변경은 `model_version`을 올리고 기존 관측을 이용해 결정적으로 재계산할 수 있어야 한다.

## 4. 밀집도 지도 모드

### 4.1 표현 규칙

밀집도 모드는 카페 마커의 배경 장식이 아니라 독립적인 제품 레이어다. 다음 우선순위를 적용한다.

1. 공급자가 공식 geometry를 제공하면 해당 폴리곤을 원본 관측 단위로 표시한다.
2. 점 관측만 제공되면 검증된 영향 반경 안에서만 보간하며, 영역 밖은 투명한 `unsupported`로 둔다.
3. H3 또는 정사각 격자는 전송과 렌더링을 위한 공통 표현으로 사용할 수 있지만, 데이터가 없는 셀을 채우는 근거로 사용하지 않는다.
4. 도시별 상대 레벨은 해당 도시의 요일·시간 기준선으로 계산한다. 단위와 모집단이 검증되지 않은 도시끼리 절대 수치를 비교하지 않는다.
5. 지도 범례에는 관측 종류, 기준 시각, 갱신 지연, 출처, 커버리지와 모델 버전을 표시한다.

### 4.2 지도 API 제안

- `GET /api/regions`: 출시 상태, locale, timezone, 데이터 모드와 현재 freshness를 제공한다.
- `GET /api/tiles/density/{region_id}/{z}/{x}/{y}.mvt`: 정규화 레벨, confidence, coverage, observed time과 source key를 가진 밀집도 벡터 타일을 제공한다.
- `GET /api/tiles/places/{region_id}/{z}/{x}/{y}.mvt`: 저배율에서는 집계, 고배율에서는 최소 장소 필드만 제공한다.
- `GET /api/regions/{region_id}/sources`: 사용자에게 공개 가능한 출처·라이선스·갱신 주기·모델 설명을 제공한다.

장소 이름·주소·전화·근거 시계열은 타일에 넣지 않고 선택 시 상세 API로 지연 조회한다. 모든 타일은 `region_id`, source snapshot과 `model_version`을 cache key에 포함한다.

## 5. 현지화, 권리와 커버리지

### 5.1 Locale과 timezone

- 장소명은 공급자의 원문과 언어 태그를 보존하고, 사용자 언어가 없으면 지역 기본 언어, 원문 순서로 fallback한다.
- 주소 포맷, 거리 단위와 숫자 형식은 locale 정책으로 분리한다.
- DST가 있는 지역도 있으므로 고정 UTC offset을 저장하지 않는다. 중복되거나 존재하지 않는 현지 시각은 UTC 관측 시각으로 구별한다.
- 검색과 정렬에서 악센트·비라틴 문자·현지 표기를 손실시키지 않는다.

### 5.2 License manifest

도시를 활성화하기 전에 다음 항목을 법적 또는 공급자 공식 문서에서 확인하고 증거 URL·문서 버전·확인일을 기록한다. `[VERIFY]`

- 상업적 사용과 최종 사용자 표시 허용 여부
- 원본 및 파생 데이터의 저장·캐시·재배포 허용 범위
- 지도 또는 상세 패널의 attribution 문구와 위치
- 데이터 결합, 파생 점수와 벡터 타일 생성 허용 여부
- 보존 기간, 삭제 의무, 지역 외 전송 제한과 계약 만료 처리
- 사용자 관측을 결합할 때 적용되는 개인정보 및 동의 요건

조건이 불명확하면 해당 소스를 제품 경로에 넣지 않는다. 국가별 일반 개인정보 규정에 대한 판단은 별도 법률 검토가 필요하다. `[HUMAN] [VERIFY]`

### 5.3 Coverage 정책

coverage는 행정경계가 아니라 실제 관측 가능 geometry와 freshness로 계산한다. 한 도시 안에서도 지원·경계·미지원 구역을 나누며, provider 장애 시 stale 영역을 실시간으로 표시하지 않는다. 카페 POI가 존재한다는 이유만으로 crowd coverage를 생성하지 않는다.

## 6. 도시 진입 검증 게이트

새 도시는 아래 게이트를 순서대로 통과해야 한다. 하나라도 실패하면 `catalog_only`를 유지하거나 출시하지 않는다.

### Gate A — 권리와 접근성

- 공식 약관 또는 계약에서 상업적 사용, 저장, 파생 결과 표시와 attribution을 확인한다. `[VERIFY]`
- 인증, quota, 비용, 데이터 보존과 종료 조건을 기록한다. `[VERIFY]`
- secret이 URL과 로그에 노출되지 않는 호출 방식을 검증한다.

### Gate B — 스키마와 의미

- 최소 2종 이상의 실제 응답 fixture를 보존하고 정상·빈 값·오류 응답을 strict schema로 검증한다.
- 관측의 모집단, 공간 단위, 갱신 주기, 시간대와 결측 의미를 확인한다. `[VERIFY]`
- 공식 geometry와 stable area ID의 존재 및 버전 정책을 확인한다. `[VERIFY]`

### Gate C — 품질과 커버리지

- 카페 POI 표본은 지역·밀도 구간을 나눠 이름, 좌표, 분류와 영업 상태를 수동 확인한다. `[HUMAN]`
- crowd 데이터는 최소 2주 이상 수집한 뒤 시간대·평일/주말·거리 구간별 결측과 지연을 보고한다.
- 도시 핵심 사용 영역의 coverage 비율과 관측 밀도를 산출하고, 기준 미달 구역은 unsupported로 남긴다.
- 현장 관측 또는 신뢰 가능한 독립 기준과 Track 1의 평가 지표로 비교한다. `[HUMAN]`

### Gate D — 운영과 성능

- provider 장애, quota 초과, 부분 cycle과 스키마 변경을 다른 도시에 전파하지 않는지 검증한다.
- freshness SLA, cycle duration, 성공률, tile 생성 지연과 cache hit ratio에 경보를 둔다.
- 한 릴리스 또는 관측 배치 실패가 검증된 이전 snapshot을 지우지 않아야 한다.
- 비밀과 개인 식별 정보를 raw payload, 로그, fixture와 프론트 응답에 포함하지 않는다.

### Gate E — 출시 승인

- `VERIFICATION`, 평가 보고서, license manifest와 데이터 출처 페이지를 지역별로 완성한다.
- `pilot`은 출처와 한계를 UI에서 명시하고 운영자가 승인한 뒤 제한 공개한다. `[HUMAN]`
- 품질 목표와 최소 운영 기간을 통과한 뒤에만 `live`로 승격한다.

## 7. 성능 및 운영 목표

수치는 구현 전 목표이며, 트래픽과 인프라 비용을 측정한 뒤 ADR에서 확정한다.

| 영역 | 초기 목표 |
|---|---|
| 벡터 타일 API | origin p95 150ms 이하, CDN cache hit p95 50ms 이하 |
| 화면당 지도 데이터 | 일반적인 모바일 viewport에서 200KB 이하 |
| 상세 API | p95 200ms 이하 |
| 지도 상호작용 | 이동 중 장시간 메인 스레드 작업 100ms 미만 |
| crowd freshness | provider 공시 주기의 2배 이내, 초과 시 stale 전환 |
| ingest 완전 cycle | 설정 주기 안에 완료, 중복 cycle 0건 |
| 가용성 | 한 도시 장애가 다른 도시 read API와 ingest에 영향 없음 |

운영 저장소는 PostgreSQL/PostGIS를 기준으로 region·observed time 공간/시계열 인덱스를 사용한다. 대규모 관측은 월 또는 지역별 partition을 검토하고, 지도 데이터는 MVT와 CDN을 사용한다. Redis, read replica, 별도 타일 서버와 queue는 계측에서 병목이 확인될 때만 추가한다.

저배율에는 장소 개별 레코드가 아니라 cluster 또는 density aggregate를 제공한다. 고배율 장소 타일도 ID·좌표·표시 레벨·coverage 같은 최소 필드만 포함하고 상세 정보는 선택 시 조회한다. 공급자 ingest와 타일 materialization은 사용자 요청 경로에서 실행하지 않는다.

## 8. 단계별 실행안

### G0 — 공통 계약과 서울 추출

- 서울 구현에서 공급자별 parsing, geometry와 지역 설정을 adapter/profile로 분리한다.
- 기존 서울 fixture와 점수 결과가 변경되지 않는 contract 및 snapshot test를 만든다.
- 공통 observation, coverage, license manifest와 model version 스키마를 확정한다.

**완료 기준**: 서울을 첫 번째 provider/profile로 실행했을 때 API 결과와 Track 1 평가 결과가 허용 오차 안에서 동일하고, 코어 엔진에 서울 전용 분기가 없다.

### G1 — 국내 확장과 공통성 검증

- Track 2의 첫 비서울 도시를 동일 contract로 연결한다.
- 서울과 데이터 단위가 다른 공급자도 raw 의미를 잃지 않고 수용 가능한지 검증한다.
- 카페, 밀집도와 coverage 모드를 두 도시에서 동일 UI/API로 제공한다.

**완료 기준**: 새 도시 추가가 adapter, profile, fixture와 license manifest 추가만으로 가능하고 코어 API 변경이 없다.

### G2 — 해외 후보 조사

- 후보 도시의 crowd 공개 데이터와 상업 공급자를 공식 문서 기준으로 조사한다. `[VERIFY]`
- 언어, timezone, DST, 지도·POI 품질, 권리와 비용을 도시 진입 게이트로 비교한다.
- 데이터가 없는 도시는 `catalog_only` 제품 가치가 충분한지도 별도로 판단한다.

**완료 기준**: 최소 3개 후보의 동일 형식 검증 보고서와 한 개 파일럿 도시 선택 근거가 있다. 파일럿은 권리와 실제 fixture를 확인한 뒤에만 확정한다.

### G3 — 해외 한 지역 `pilot`

- 파일럿 adapter, region profile, locale 리소스와 모니터링을 구현한다.
- 최소 2주 shadow ingest 동안 데이터 지연·결측·coverage와 비용을 측정한다.
- 현장 또는 독립 관측 골든셋으로 Track 1 지표를 산출한다. `[HUMAN]`
- 카페/밀집도/coverage 모드와 출처 페이지를 제한 공개한다.

**완료 기준**: Gate A~E를 충족하고, 장애 시 자동 stale/suspended 전환과 지역 격리가 검증된다.

### G4 — 반복 가능한 도시 온보딩

- provider가 같은 도시는 profile 중심, 다른 도시는 adapter 중심으로 추가한다.
- 도시별 품질·비용·사용량을 비교해 유지 또는 중단 기준을 적용한다.
- 공통 계약 변경은 모든 기존 도시 fixture 및 모델 평가를 통과한 뒤 버전으로 배포한다.

**완료 기준**: 두 개 이상의 국가에서 동일 코어가 동작하며, 도시 온보딩 체크리스트와 rollback 절차가 반복 가능하다.

## 9. Track 간 의존성과 경계

- **Track 1 — 엔진 신뢰도 및 정확도**는 공통 confidence 구성요소, calibration 지표, model version과 골든셋 규격을 제공한다. 해외 도시는 별도 기준선과 평가 없이 서울 파라미터를 복사하지 않는다.
- **Track 2 — 서울에서 국내 확대**는 유니버설 contract의 첫 검증 구간이다. 국내 지자체별 차이를 코어에 하드코딩하지 않고 adapter/profile로 흡수한다.
- **Track 3 — 해외 확대**는 provider contract, 국제화, license manifest, 도시 진입 게이트와 밀집도 타일 운영을 담당한다.

Track 2와 3이 동일한 provider contract를 사용하되, 국내 데이터가 해외 전체의 표준이라고 가정하지 않는다. contract 변경은 세 Track의 fixture, 평가와 운영 문서를 함께 갱신해야 한다.

## 10. 리스크와 대응

| 리스크 | 영향 | 대응 |
|---|---|---|
| 글로벌 POI의 국가별 품질 편차 | 잘못된 매장·위치 표시 | release별 표본 검수, 지역 필터, quarantine, 검증 실패 시 catalog 미출시 |
| 도시별 crowd 정의 불일치 | 숫자와 색상이 잘못 비교됨 | observation type과 원본 단위를 보존하고 도시별 상대 기준선을 사용 |
| 상업적 재배포 또는 파생 권리 부재 | 서비스 중단·법적 위험 | Gate A와 license manifest 통과 전 ingest 및 공개 금지 |
| timezone/DST 처리 오류 | freshness와 추이 왜곡 | UTC 저장, IANA timezone 표시, DST fixture 테스트 |
| 희소 관측의 과도한 보간 | 미지원 구역을 실시간처럼 표시 | 공식 geometry 우선, 검증 반경 밖 unsupported, coverage 레이어 상시 제공 |
| 도시 수 증가에 따른 ingest 장애 전파 | 여러 지역 동시 stale | region/provider job 격리, 독립 cursor, circuit breaker와 이전 snapshot 유지 |
| 공급자 비용 증가 | 확장성 저하 | 사용자 요청과 ingest 분리, 타일 CDN, 도시별 원가·사용량 승격/중단 기준 |

## 11. 아직 검증해야 할 항목

- Overture Places의 파일럿 국가별 카페 분류 품질, 릴리스 범위, attribution과 파생 데이터 재배포 조건 `[VERIFY]`
- OpenFreeMap/OpenMapTiles 기반 스타일과 타일의 대상 국가 coverage, 가용성, attribution 및 대규모 상업 트래픽 조건 `[VERIFY]`
- 해외 후보 도시의 실시간 crowd API, 호출 한도, geometry, 갱신 주기, 상업적 사용 및 파생 표시 권리 `[VERIFY]`
- 국내외 통신·상업 유동 인구 공급자의 공간 해상도, 지연, 최소 계약비용과 파생 결과 공개 범위 `[VERIFY]`
- 각 파일럿 국가의 개인정보, 데이터 지역성, 사용자 관측 수집과 보존 요건 `[HUMAN] [VERIFY]`
- density MVT와 장소 타일의 실제 모바일 payload, CDN hit ratio, PostGIS 비용과 목표 p95 `[VERIFY]`

검증 결과가 이 제안과 충돌하면 구현을 우회하지 않는다. 해당 지역의 검증 문서와 ADR을 먼저 갱신하고, 필요한 경우 출시 상태를 `catalog_only` 또는 `suspended`로 낮춘다.
