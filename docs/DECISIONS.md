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
- [ADR-0003: MapLibre/OpenFreeMap 지도와 Overture 캐시 원장](adr/ADR-0003-maplibre-overture-catalog.md) — 2026-07-11, Accepted
- [ADR-0004: 121개 전체 폴링과 cache-first 읽기 경로](adr/ADR-0004-seoulwide-polling-cache.md) — 2026-07-11, Accepted
- [ADR-0005: 관리형 PostgreSQL과 분리 worker 기반 실시간 운영](adr/ADR-0005-live-production-runtime.md) — 2026-07-11, Accepted
- [ADR-0006: 세 개 제품 트랙과 유니버설 혼잡 데이터 계약](adr/ADR-0006-universal-expansion-tracks.md) — 2026-07-11, Accepted

## 현재 제품 경로 요약

| 영역 | 채택 | 제외/제약 |
|---|---|---|
| 지도 | MapLibre GL + OpenFreeMap | Kakao Maps SDK는 제품 런타임에서 로드하지 않음 |
| 카페 원장 | Overture Places release를 PostgreSQL에 cache, 서울 인허가로 보정 | viewport마다 외부 POI 검색 금지 |
| 혼잡도 | 서울 공식 121개 장소, 10분 non-overlapping polling | 과거 `≤12곳` 범위는 legacy 결정 |
| 외부 매장 링크 | 검증된 provider ID/canonical direct detail URL만 표시 | 이름/좌표 검색 링크·스크레이핑·추측 매칭 금지 |
| 제품 모드 | 카페 찾기·지역 밀집도·데이터 커버리지 | 데이터 없는 영역의 임의 보간 금지 |
| 지역 확장 | `RegionProfile`과 공급자 어댑터를 국내·해외가 공유 | 도시명 기반 코어 분기 금지 |

Kakao Local의 실응답 fixture와 키/도메인 활성화 기록은 Phase 0의 역사적 검증 증거다.
ADR-0003가 대체한 제품 POI 경로로 해석하지 않는다.

## 초기 결정 후보

다음 항목은 `docs/PLAN.md`의 현재 설계안이며, 구현 또는 실측으로 확정할 때 개별 ADR을 작성한다.

- 추정치와 근거·신뢰도를 함께 노출
- 거리 가중 보간(IDW) 및 3단계 커버리지
- 결정적 순수 함수 기반 스코어링
- IDW·coverage·신뢰도 파라미터의 Phase 6 보정
