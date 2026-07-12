# ADR-0011: 도시 활동도를 코어로 두고 카페를 첫 활용 레이어로 둔다

- 날짜: 2026-07-13
- 상태: Accepted
- 결정자: 사용자, Codex
- 관련: Track 1, ADR-0010, `docs/PLAN.md`

## 맥락

현재 공식 입력은 구역 존재인구, 보행 흐름, 교통 유입과 같은 **지역 신호**다. 카페 내부
좌석·대기 인원을 직접 측정하지 않는다. 그런데 제품 이름과 마커 중심 UX가 카페 혼잡도를
연상시키면 데이터가 말하는 범위보다 강한 약속을 하게 된다.

같은 지역 관측은 카페뿐 아니라 관광지 방문 시간, 산책·쇼핑 지역 선택, 행사 주변 회피와
같은 용도에도 쓰일 수 있다. 반대로 “밀집도”라는 단어는 정확한 인원/면적 측정처럼 들려,
존재인구·지점 통행량·상대 인기처럼 의미가 다른 신호를 함께 다루는 장기 구조에 부적합하다.

## 결정

1. 제품 코어는 **도시 활동도 surface**다. 이는 특정 지역이 자기 평소 패턴보다 얼마나
   활발하거나 한산한지를 근거와 함께 나타내는 상대 지표다.
2. 카페 찾기는 도시 활동도 surface를 소비하는 첫 번째 overlay이자 use case다. 카페
   주변 활동도와 매장 좌석 점유율을 같은 값으로 표현하지 않는다.
3. 활동도는 원본 의미를 보존한다. 최소 observation type은 `presence_count`,
   `pedestrian_flow`, `venue_popularity`, `transit_flow`, `proxy`이며 raw 값끼리 직접
   합치거나 도시 간 절대 비교하지 않는다.
4. 사용자 상태는 두 축으로 분리한다. signal mode는 `observed`, `forecast`,
   `baseline_only`, `unsupported`, freshness는 `fresh`, `stale`, `n/a`다. stale observed는
   관측이었다는 provenance를 보존하되 현재 anomaly로 표시하지 않는다.
5. 활동도 공통 index는 source-local baseline 대비 anomaly에서 시작한다. calibration 전에는
   0~100의 정밀 점수, 정확도 확률이나 도시 간 순위를 공개하지 않는다.
6. Track 1의 primary ground truth는 지역 보행 혼잡·활동 관측이다. 카페 좌석·대기 상태는
   카페 overlay의 효용을 평가하는 secondary label로 계속 분리한다.
7. 공개 `v1-idw-point`와 현재 API/UI는 승격 gate 전까지 유지한다. 새 계약과 계산은
   `activity-shadow` 및 offline preview에서 먼저 비교하고, 사용자 문구·기본 레이어 전환은
   Phase 6와 source별 gate를 통과한 model version에서만 수행한다.

## 제품 레이어

```text
provider observations + temporal baselines
                  │
        source-local activity anomalies
                  │
          urban activity surface
       ┌──────────┼───────────┐
       │          │           │
 cafe overlay  area map   future overlays
```

카페 overlay는 위치·카테고리·영업상태와 주변 surface를 결합할 수 있지만, venue-specific
관측이 없는 동안 내부 혼잡을 추정하지 않는다. 관광·산책·쇼핑 등의 후속 overlay도 같은
surface를 소비하되 각자의 별도 효용 지표를 가져야 한다.

## 결과와 재검토 조건

- 서울은 도시 활동도와 카페 overlay를 함께 검증하는 첫 지역으로 유지한다.
- 해외 확장은 카페 목록이 아니라 source별 activity surface 재현성을 검증하는 방식으로
  진행한다(ADR-0010).
- “혼잡도”는 원본 서울 label 또는 관측 라벨 문맥에서 사용할 수 있지만, 여러 의미를 묶는
  코어 제품명과 공통 수치에는 “도시 활동도”를 사용한다.
- Phase 6에서 지역 활동도는 맞지만 카페 효용이 낮게 나오면 surface는 유지하고 카페 추천
  규칙만 재설계한다. 둘을 함께 실패로 처리하거나 지표를 섞지 않는다.
