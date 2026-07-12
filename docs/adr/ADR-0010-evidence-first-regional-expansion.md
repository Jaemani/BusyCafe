# ADR-0010: 지역 확장은 두 번째 공급자 실측 후 최소 계약을 확정한다

- 날짜: 2026-07-13
- 상태: Accepted
- 대체 범위: ADR-0006의 “범용 계약을 먼저 확정한다”는 순서
- 유지 범위: 세 제품 트랙, 데이터 없는 지역을 채우지 않는 원칙, 도시별 상대 비교
- 근거: `docs/research/2026-07-13-regional-expansion-feasibility.md`

## 맥락

지도와 카페 POI는 Overture·MapLibre로 세계화할 수 있지만 혼잡 관측은 지역마다 의미가
다르다. 서울은 구역 존재인구, 부산 후보는 교차로 보행 통과량, Melbourne 후보는 센서
지점의 분당 보행 흐름이다. 이를 실측 전에 하나의 확정 스키마로 일반화하면 잘못된 공통
필드와 도시별 예외가 코어에 굳어진다.

코드 감사 결과 `app/universal_contracts.py`는 자체 테스트 외 런타임 import가 없으며,
문서에 적힌 provider protocol도 구현돼 있지 않다. 현재 DB·worker·API·POI seed는 서울
단일 지역 구조다. 따라서 이 타입은 재사용이 검증된 계약이 아니라 provisional seam
inventory다.

## 결정

1. 확장은 가능하지만 국가 전체가 아니라 **검증된 region 단위**로 진행한다.
2. 제품 능력을 `catalog_only`, `baseline_only`, `live_supported`, `suspended`로 구분한다.
   과거 기준선만 있는 지역을 “현재 혼잡”으로 표시하지 않는다.
3. 현재 universal contract는 `experimental, not runtime contract`로 동결한다. 두 번째
   provider의 권리·실응답 fixture가 생기기 전에는 필드·Protocol·DB migration을 확장하지
   않는다.
4. 첫 국내 adapter spike 후보는 부산 스마트교차로 보행자수, 첫 해외 후보는 Melbourne
   CBD pedestrian sensor로 한다. 둘 다 제품 ingest가 아니라 read-only fixture와 shadow
   검증부터 시작한다.
5. adapter spike는 각 원본을 기존 순수 scorer가 소비할 수 있는 연구 입력으로 변환하되,
   존재인구·보행 흐름·장소 인기도를 같은 raw 단위나 같은 1~4 레벨로 저장하지 않는다.
   각 source의 자기 요일×시간 기준선 대비 percentile/anomaly만 비교 후보가 된다.
6. 두 번째 도시에서 확인된 최소 교집합만 계약으로 승격한다. 예상 최소치는
   `provider/region/area identity`, `observation_type`, 원본 값·단위, geometry reference,
   `observed_at/fetched_at`, provenance/license, quality state다.
7. 그 뒤에만 region/provider DB identity, region-scoped ingest cycle·score·API·POI seed
   migration을 설계한다. 서울 golden parity와 타지역 격리 테스트가 선행 gate다.

## 후보와 순서

- 서울: Track 1 정확도와 production worker를 계속 최우선으로 한다.
- 부산: 공공데이터포털 키로 좌표·보행자수 fixture를 받고 7일 cadence/coverage를 측정한다.
- Melbourne: 공개 API를 48시간 shadow 수집해 중복·지연·DST·센서 상태를 먼저 확정한다.
- BestTime/Foursquare: 공개 소스의 대체제가 아니라 서울·Melbourne 소표본 유료
  challenger로만 평가한다. Google Popular Times는 공식 API가 없어 사용하지 않는다.

## 결과와 재검토 조건

- POI·지도만 세계화하는 일과 혼잡도를 세계화하는 일을 분리한다.
- 전국/전세계 단일 실시간 공공 피드를 전제로 하지 않는다.
- 부산 또는 Melbourne fixture가 권리·현행성·coverage gate에서 실패하면 후보를 바꾸되
  공통 계약을 추측으로 확장하지 않는다.
- 같은 provider가 두 번째 도시에서도 반복되거나 의미가 다른 세 번째 provider가 들어올
  때만 실제 Provider Protocol을 일반화한다.
- public v1, 서울 DB schema와 API는 이 결정만으로 변경하지 않는다.
