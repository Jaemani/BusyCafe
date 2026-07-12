# 국내·해외 지역 확장 현실성 조사

- 작성일: 2026-07-13
- 상태: 공식 소스 후보 조사와 read-only 실측. 제품 provider 미채택
- 범위: Track 2·3 feasibility. 런타임 구현은 ADR-0010 gate 전 동결

## 1. 결론

한국에 국한되지 않는 제품은 가능하다. 다만 확장 난도는 두 층으로 갈린다.

- 지도와 카페 POI: Overture Places와 MapLibre를 이용해 전 세계로 확장 가능
- 현재 혼잡도: 전국·전세계 공통 공공 피드는 확인되지 않아 도시별 provider 필요

정확도와 재현성을 우선하면 `서울 → 부산 shadow → Melbourne shadow` 순서가 가장
현실적이다. 전 세계 출시 속도만 우선하면 BestTime 같은 상용 API를 얹을 수 있지만,
데이터 계보·장소별 coverage·비용·보존권이 불투명해 core source가 아니라 challenger로
검증해야 한다.

## 2. 한국 확장

### 전국 공통 데이터의 한계

국토지리정보원의 전국 100m~250m 인구격자는 정적 인구 통계이며 요일×시간 생활인구가
아니다. 행정안전부 생활인구도 89개 인구감소지역의 분기 집계라 서울 OA-22784를 전국에서
대체하지 못한다. 전국 카페 지도와 정적 공간 prior에는 쓸 수 있지만 “지금 붐빔”으로
표시하지 않는다.

- 국토지리정보원 통계지도 인구:
  <https://www.data.go.kr/data/15059921/fileData.do>
- 행정안전부 인구감소지역 생활인구:
  <https://www.data.go.kr/data/15130539/fileData.do>

### 첫 후보: 부산

부산 스마트교차로 API는 이번 조사에서 비서울 공식 소스 중 가장 직접적이었다.

- 날짜·시간을 입력으로 받고 `walkCnt` 보행자수, 수집시각, 교차로명을 반환
- 별도 공식 위치 API가 교차로·접근로 위경도를 제공
- 공공데이터포털 개발계정 500회/일, 무료, 자동승인
- 부산 지하철 역×일×시간 승하차 이력으로 baseline/보조 검증 가능

공식 소스:

- 스마트교차로 접근로 교통량:
  <https://www.data.go.kr/data/15121087/openapi.do>
- 스마트교차로 위치:
  <https://www.data.go.kr/data/15120896/openapi.do>
- 부산교통공사 역별 시간대 승하차:
  <https://www.data.go.kr/data/3057229/fileData.do>
- 광안리 방문자 센서 보조자료:
  <https://www.data.go.kr/data/15094452/fileData.do>

아직 `[VERIFY]`인 항목은 실제 cadence, 시간 조회의 retention, pagination별 일 호출량,
0과 결측 의미, 카페 상권 coverage, 캐시·파생 표시 권리다. point 보행센서를 서울의 1.5km
IDW에 넣지 않는다. 교차로·도로 연결성과 100~200m 후보 반경을 shadow에서 비교한다.

### 다른 국내 후보

- 대전 서구 유동인구 API는 포털의 “실시간” 표기와 달리 원 제공자 메타·필드가 연/월·
  주야간 집계여서 baseline 후보에 가깝다.
  <https://www.data.go.kr/data/15108957/openapi.do>
- 대구 수성구 CCTV 유동인구는 연도·분기 총계라 live source가 아니다.
  <https://www.data.go.kr/data/15097213/openapi.do>
- 제주 유동인구는 읍면동 단위이고 최신성·상업 캐시 권리를 실응답으로 다시 확인해야 한다.
  <https://www.data.go.kr/data/15074268/openapi.do>

공공데이터포털의 “업데이트 주기=실시간”만 믿지 않는다. 실제 최신 row의 observed time과
원본 필드 단위를 fixture로 확인한 뒤 판정한다.

## 3. 해외 확장

### 첫 후보: Melbourne CBD

City of Melbourne은 같은 센서군에 대해 위치, 과거 시간별 보행량, 최근 분단위 보행량을
공개한다. 2026-07-13 read-only 실호출에서 위치 134개, 최근 응답 89개 sensor ID와
2,135행을 확인했다. 과거 시간별 자료는 2009년부터 제공된다.

- 최근 분당 보행량:
  <https://data.melbourne.vic.gov.au/explore/dataset/pedestrian-counting-system-past-hour-counts-per-minute/information/>
- 과거 시간별 보행량:
  <https://data.melbourne.vic.gov.au/explore/dataset/pedestrian-counting-system-monthly-counts-per-hour/information/>
- 센서 위치·상태:
  <https://data.melbourne.vic.gov.au/explore/dataset/pedestrian-counting-system-sensor-locations/information/>

공식 설명은 최근 endpoint가 15분 갱신이라고 하지만 실응답의 earliest~latest가 약
2시간 39분이었고 일부 센서가 60분보다 많은 행을 보였다. 과거 dataset의 갱신 설명과
license metadata도 추가 확인이 필요하다. 따라서 48시간 shadow에서 sensor/time dedupe,
지연 p50/p95, 결측, DST, 비활성 센서와 라이선스를 먼저 확정한다.

Melbourne은 `pedestrian_flow` point 관측이다. 서울의 `presence_count` polygon과 같은
숫자가 아니며 CBD 보행로·블록 연결 범위에서만 카페와 연결한다.

### 비교 후보

- Dublin DLR: 7개 지점, 2010~2026 시간별 이력, 월 단위 공개. `baseline_only` 후보.
  <https://data.gov.ie/dataset/pedestrian-footfall-dlr>
- New York: 114개 지점의 5월·9월 수동 보행량 조사. 실시간이 아니라 benchmark 후보.
  <https://data.cityofnewyork.us/Transportation/Bi-Annual-Pedestrian-Counts/cqsj-cfgu>

## 4. 글로벌 상용·POI 후보

- Overture Places는 글로벌 카페 원장 후보지만 릴리스별 source와 CDLA Permissive,
  Apache 2.0, CC0 고지를 보존하고 국가별 카페 정확도를 표본 검수한다.
  <https://docs.overturemaps.org/guides/places/>
  <https://docs.overturemaps.org/attribution/>
- Foursquare Place Details에는 `hours_popular` 시간 구간과 `popularity` scalar가 공식
  스키마에 존재한다. 168-slot 혼잡 곡선이나 live 값이 아니며 의미·신선도·한국 coverage,
  요금·캐시·재배포 권리를 `[VERIFY][HUMAN]`으로 둔다.
  <https://docs.foursquare.com/fsq-developers-places/reference/place-details>
- BestTime은 공식 설명상 150개 이상 국가의 주간 baseline과 일부 live 값을 제공한다.
  서울·Melbourne 각 50개 카페에서 매칭률, baseline/live 제공률, 정확도와 월 원가를
  비교한 뒤에만 채택한다. raw 재판매 금지와 파생 UI 조건은 법률·계약 검토 대상이다.
  <https://documentation.besttime.app/>
  <https://besttime.app/terms>
- Google Popular Times는 공식 Places API 필드가 아니므로 스크레이핑하지 않는다.

## 5. 현실적인 제품 계약

| 상태 | 제공 내용 | 금지 표현 |
|---|---|---|
| `catalog_only` | 지도와 검증된 카페 | 혼잡도·평소 혼잡 |
| `baseline_only` | 요일×시간의 평소 지역/지점 패턴 | 지금·실시간 |
| `live_supported` | 평소 기준선 + 신선한 동일 source 이상치 | 매장 좌석 점유율 |
| `suspended` | 지도·카페, 장애·권리 중단 안내 | 마지막 값을 현재처럼 표시 |

관측 의미는 최소 `presence_count`, `pedestrian_flow`, `venue_popularity`, `transit_flow`,
`proxy`로 분리한다. raw 값을 도시끼리 비교하지 않고 각 source의 자기 기준선 대비
percentile/anomaly로 정규화한다.

## 6. 실행 순서와 gate

1. 서울 Track 1과 production worker를 계속 최우선으로 유지한다.
2. 부산 API 키로 교차로 위치·보행자수 raw fixture를 각각 1건 확보한다 `[HUMAN]`.
3. 부산 7일 shadow로 cadence·coverage·quota를 확인하고 최소 4주 baseline 전에는 공개하지
   않는다.
4. Melbourne 공개 API를 48시간 shadow 수집하고 과거 파일·license fixture를 확정한다.
5. 두 번째 provider fixture가 생기면 서울과의 실제 최소 교집합만 contract로 확정한다.
6. 그 뒤에 region/provider DB migration과 타지역 격리 테스트를 설계한다.
7. BestTime/Foursquare는 100개 카페 유료 challenger에서 독립 개선과 원가가 확인될 때만
   제한 채택한다.

빠른 세계화는 POI와 `baseline_only`부터 가능하다. 신뢰할 수 있는 `live_supported`는
도시별 관측·권리·현장 gate를 통과한 곳만 늘리는 것이 현실적이다.
