# 2026-07-12 외부감사 결과와 대응 결정

## 요약

외부 리뷰는 문서·검증·인시던트 규율을 강점으로 평가했지만, 핵심 제품 가설의 현장 검증은
아직 0건이며 실시간 production, rollback 가능한 DB 운영과 라이선스 감사도 미완료라고
판정했다. “프로세스 성숙도와 제품 검증의 불균형”이라는 핵심 진단을 수용한다.

다만 “제품 검증 0%”는 **핫스팟 혼잡이 카페 선택에 유용한가**라는 제품 가설에 한정한다.
API schema, 수집기, fixture, 실패 격리와 배포 동작 검증까지 0으로 해석하지 않는다.
리뷰에 적힌 82개 테스트는 감사 시점 또는 집계 방식 차이이며 현재 기준선은 97 passed다.

## 수용한 지적

1. Phase 6 실측과 관리형 실시간 production을 최우선으로 올린다.
2. 국내·해외 확장 구현과 신규 확장 문서는 Phase 6 기준선 전까지 동결한다.
3. 구현 병렬화와 공개·승격 게이트를 ADR-0007로 분리한다.
4. 외부 공개 문서에서 로컬 전용 `rtk` 의존을 제거한다.
5. CI에서 PostgreSQL migration을 SQL 렌더가 아닌 실제 DB에 적용한다.
6. schema의 실행 진실은 Alembic migration으로 한정하고 PLAN의 DDL은 비규범 설명으로 둔다.
7. 현행 코드·데이터·지도 attribution과 재배포 조건을 확장보다 먼저 감사한다.
8. stale 경보, 백업·복구 절차, release tag와 인시던트 미완료 항목 회수 절차가 필요하다.

## 보류하거나 제한적으로 수용한 지적

- 이미 작성한 Track 2·3 문서와 유니버설 계약은 삭제하지 않는다. 추가 추상화와 실제 연결을
  동결하고 두 번째 검증 source가 생긴 뒤 계약을 수정한다.
- PLAN 대분할은 Phase 6보다 우선하지 않는다. 우선 schema source-of-truth와 중복 정책
  소유권만 명시하고, 대규모 문서 재편은 평가 이후에 한다.
- 코드 라이선스는 저장소 소유자의 법적 선택이므로 자동으로 MIT 또는 Apache-2.0을
  적용하지 않는다. 선택 전까지 `[HUMAN]` 블로커로 둔다.
- 공개 snapshot은 기술 프리뷰로 유지할 수 있지만 실시간 제품으로 홍보하지 않는다.

## 실행 우선순위

| 순위 | 작업 | 상태 | 완료 조건 |
|---:|---|---|---|
| 1 | 관리형 PostgreSQL provision과 production 전환 | `[HUMAN]` credential 대기 | Vercel read API와 10분 worker가 같은 DB 사용, 1시간 freshness 검증 |
| 2 | 축소 Phase 6 평가 | 이중 라벨 evaluator·24곳 후보 완료, `[HUMAN]` POI 검수·관측 대기 | 2개 동네 × 3개 거리대 × 4곳 후보를 동네별 3개 슬롯에서 관측, 지역 혼잡 Spearman 보고 |
| 3 | 현행 라이선스·attribution 감사 | 출처 보완 완료·코드 LICENSE `[HUMAN]` 대기 | 코드 라이선스 결정, 지도 attribution 브라우저 확인 |
| 4 | 외부인용 README와 감사 재현 절차 | 완료 | 표준 명령으로 CI와 로컬 검증 가능 |
| 5 | PostgreSQL migration CI | 완료 | GitHub Actions run `29160900757`에서 PostgreSQL 17 upgrade와 schema smoke 성공 |
| 6 | freshness 경보·백업/복구 runbook | complete-cycle probe와 runbook 구현, 활성화·복구 훈련 대기 | 25분 stale 탐지와 별도 recovery DB 복구 훈련 기록 |

## 작업 동결

다음 조건 전에는 새 국내·해외 provider 구현, 새 확장 ADR과 신규 제품 기능을 시작하지 않는다.

- 최소 Phase 6 관측 데이터가 수집되고 기준 모델 지표가 계산됨
- production 실시간 DB 전환 계획의 HUMAN 블로커가 명확히 해결되거나 일정이 확정됨
- 현행 source의 이용·표시 조건 감사 완료

## 감사 후 첫 산출물

- `backend/scripts/run_eval.py`: 저장 snapshot을 재생하는 결정적 평가 도구
- `backend/scripts/select_eval_candidates.py`: 거리대와 POI source confidence를 고정한
  결정적 현장 후보 선택기
- PostgreSQL 17 service를 사용하는 실제 migration CI
- 외부인용 README와 표준 감사 명령
- ADR-0007과 이 대응 문서

외부 리뷰는 참고 자료이며 제품 결정의 source of truth가 아니다. 채택한 변경은 PLAN,
ADR, VERIFICATION과 코드 검증을 통해서만 효력을 갖는다.
