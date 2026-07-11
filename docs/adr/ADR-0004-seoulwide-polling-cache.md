# ADR-0004: 121개 전체 폴링과 cache-first 읽기 경로

- 상태: Accepted
- 결정일: 2026-07-11
- 관련 계획: `docs/PLAN.md` v1.4, Phase 1, Phase 2, Phase 4
- 대체 관계: 이전 MVP의 `≤12곳 × 10분` 폴링 범위 결정을 대체한다. 해당 10곳의 초기
  seed/제어 호출 검증 기록은 당시의 증거로 남기며, 현재 운영 범위를 뜻하지 않는다.

## 배경

사용자는 서울 전역을 지도처럼 이동하면서 카페를 보고 싶어 한다. 서울 OpenAPI 안내는
한 호출에 최대 1,000건, 호출 횟수 제한 없음으로 확인됐다. `citydata_ppltn`은 장소당
한 호출이므로 행 제한은 이 API에서 병목이 아니다. 공식 마스터는 121개 장소이고,
10분 주기는 하루 `121 × 144 = 17,424` 호출이다.

동시에 지도 이동마다 외부 장소 검색을 하면 많은 사용자의 bbox 이동이 외부 API 호출,
비용, rate limit, 서로 다른 결과로 직접 번진다. 카페 원장과 혼잡도는 갱신 특성이
다르므로 같은 요청 경로에서 갱신하면 안 된다.

## 고려한 선택지

1. 성수·홍대·연남 주변 ≤12개만 10분 폴링 — 초기 호출량은 작지만 서울 전역 UX와
   coverage가 불일치한다.
2. 공식 121개를 10분마다 폴링 — 서울 주요 장소 coverage를 일관되게 제공하고,
   확인된 호출 정책 안에서 동작한다.
3. 사용자가 보는 bbox만 demand-poll — 외부 요청량이 사용자 수에 비례하고 결과
   freshness가 사용자마다 달라져 cache/관측을 복잡하게 만든다.

## 결정

공식 121개 마스터 레코드 모두에 `is_polled=true`를 설정하고, 단일 congestion worker가
10분 non-overlapping cycle로 폴링한다. 한 cycle의 목표는 121개 시도이며, 성공·실패·
duration·마지막 complete cycle 시각을 기록한다. 실패 대상은 retry/backoff 후 다음
대상으로 진행한다. 다음 cycle은 이전 cycle이 끝나기 전에 중복 시작하지 않는다.

카페 원장은 Overture release ingest와 인허가 보정 job에서만 갱신한다. FastAPI의
`/api/cafes`와 `/api/cafes/{id}`는 PostgreSQL의 검증된 cache와 materialized score만
조회한다. read cache가 필요한 시점에는 bbox·필터·catalog release·score 시각을 포함한
key를 사용해 새 release/점수가 오래된 응답으로 가려지지 않게 한다.

## 근거

- 데이터 제공 범위와 지도 UX가 일치한다. 단, 121개 대표점과 `R_MAX` 밖은 여전히
  `uncovered`로 표시해 커버리지를 과장하지 않는다.
- 캐시 원장은 요청 수와 외부 데이터 호출 수를 분리해 사용자 증가가 곧 외부 provider
  과호출이 되는 구조를 막는다.
- 단일 worker와 cycle metric은 API 서버 수평 확장 때 중복 폴링을 방지하고, 10분 SLA
  위반을 관측 가능하게 한다.

## 결과와 후속 조치

- Phase 1 DoD는 121개 각각 1시간에 5개 이상 snapshot, full cycle 10분 이내, 17,424
  일일 호출 계산·정책 기록으로 변경한다.
- API provider가 이후 rate limit을 도입하거나 cycle이 10분을 넘으면, 무제한 동시성
  확대가 아니라 concurrency cap·재시도·주기 변경을 계측 결과와 함께 새 ADR로
  결정한다.
- Phase 2/4는 release atomicity, stale badge, last complete cycle health 정보를
  구현·검증한다. PostgreSQL/PostGIS, Redis, replica는 실제 read/write 병목 측정 뒤
  단계적으로 추가한다.
