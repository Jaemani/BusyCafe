# Decision Log

제품·아키텍처·데이터 정책에 영향을 주는 결정을 ADR 형식으로 누적 기록한다. 기존 결정을 지우지 않고 새 결정에서 대체 관계를 명시한다.

## 상태 값

- `Proposed`: 논의 중
- `Accepted`: 적용하기로 결정
- `Superseded`: 이후 결정으로 대체됨
- `Rejected`: 검토 후 채택하지 않음

## ADR 템플릿

```md
## ADR-NNN — 제목

- 날짜: YYYY-MM-DD
- 상태: Proposed | Accepted | Superseded | Rejected
- 결정자:
- 관련 문서/이슈/커밋:

### 맥락

어떤 문제와 제약 때문에 결정이 필요한가?

### 고려한 선택지

1. 선택지 A — 장점 / 단점
2. 선택지 B — 장점 / 단점

### 결정

무엇을 선택했고 적용 범위는 어디까지인가?

### 근거

검증 결과, 비용, 운영성, 확장성 등 선택의 근거는 무엇인가?

### 결과와 후속 조치

예상되는 이점·비용·리스크와 다시 검토할 조건은 무엇인가?
```

## 채택된 결정

- [ADR-0001: PostgreSQL을 기본 데이터베이스로 사용](adr/ADR-0001-primary-database.md) — 2026-07-11, Accepted
- [ADR-0002: 공식 핫스팟 폴리곤에서 대표 좌표 산출](adr/ADR-0002-hotspot-location.md) — 2026-07-11, Accepted
- [ADR-0003: MapLibre/OpenFreeMap 지도와 Overture 캐시 원장](adr/ADR-0003-maplibre-overture-catalog.md) — 2026-07-11, 지도 결정 Accepted; 원장 결정은 ADR-0014가 대체
- [ADR-0004: 121개 전체 폴링과 cache-first 읽기 경로](adr/ADR-0004-seoulwide-polling-cache.md) — 2026-07-11, Accepted
- [ADR-0005: 관리형 PostgreSQL과 분리 worker 기반 실시간 운영](adr/ADR-0005-live-production-runtime.md) — 2026-07-11, Accepted
- [ADR-0006: 세 개 제품 트랙과 유니버설 혼잡 데이터 계약](adr/ADR-0006-universal-expansion-tracks.md) — 2026-07-11, Accepted
- [ADR-0007: 구현 병렬화와 공개·승격 게이트 분리](adr/ADR-0007-parallel-implementation-release-gates.md) — 2026-07-12, Accepted
- [ADR-0008: 운영 수집은 전용 상시 worker에서 실행](adr/ADR-0008-dedicated-production-worker.md) — 2026-07-12, Superseded by ADR-0012
- [ADR-0009: 생활인구 베이스라인 단위로 250m 격자 채택](adr/ADR-0009-living-population-250m-grid.md) — 2026-07-12, Accepted
- [ADR-0010: 지역 확장은 두 번째 공급자 실측 후 최소 계약 확정](adr/ADR-0010-evidence-first-regional-expansion.md) — 2026-07-13, Accepted
- [ADR-0011: 도시 활동도를 코어로 두고 카페를 첫 overlay로 사용](adr/ADR-0011-urban-activity-core-cafe-overlay.md) — 2026-07-13, Accepted
- [ADR-0012: Supabase가 production poll과 monitor workflow를 예약 실행](adr/ADR-0012-supabase-dispatched-production-scheduler.md) — 2026-07-15, Accepted
- [ADR-0013: 검증 전에는 광고하지 않고 후원부터 제한적으로 도입](adr/ADR-0013-validation-first-monetization.md) — 2026-07-15, Accepted
- [ADR-0014: 서울 카페 원장은 Kakao-first recall 정책을 사용](adr/ADR-0014-kakao-first-seoul-catalog.md) — 2026-07-15, Accepted

## 현재 제품 경로 요약

| 영역 | 채택 | 제외/제약 |
|---|---|---|
| 지도 | MapLibre GL + OpenFreeMap | Kakao Maps SDK는 제품 런타임에서 로드하지 않음 |
| 카페 원장 | Kakao Local CE7 complete snapshot을 recall 우선 원장으로 cache, Overture·서울 인허가로 보정 | viewport마다 외부 POI 검색 금지; Kakao 정책 조건은 ADR-0014 참조 |
| 혼잡도 | 서울 공식 121개 장소, Supabase가 dispatch하는 5분 non-overlapping polling | 과거 `≤12곳`·10분 범위는 legacy 결정 |
| 외부 매장 링크 | 검증된 ID는 canonical direct, Naver ID가 없으면 주소+이름 `네이버맵 검색`을 별도 표시 | 좌표 검색·스크레이핑·추측 ID 금지; fallback을 직접 상세로 표현 금지 |
| 제품 모드 | 카페 찾기·지역 밀집도·데이터 커버리지 | 데이터 없는 영역의 임의 보간 금지 |
| 제품 코어 | 도시 활동도 surface + 카페 첫 overlay | 지역 활동도를 매장 좌석 점유율로 표현 금지 |
| 지역 확장 | 부산·Melbourne fixture 뒤 실제 최소 교집합만 계약 승격 | 선행 범용화·도시명 기반 코어 분기 금지 |

Kakao Local의 Phase 0 실응답 fixture와 키 검증은 ADR-0014의 parser·좌표 회귀 근거로
재사용한다. Kakao Maps JavaScript SDK와 도메인 등록은 현 MapLibre 제품 런타임에 필요 없다.

## 초기 결정 후보

다음 항목은 `docs/PLAN.md`의 현재 설계안이며, 구현 또는 실측으로 확정할 때 개별 ADR을 작성한다.

- 추정치와 근거·신뢰도를 함께 노출
- 거리 가중 보간(IDW) 및 3단계 커버리지
- 결정적 순수 함수 기반 스코어링
- IDW·coverage·신뢰도 파라미터의 Phase 6 보정
