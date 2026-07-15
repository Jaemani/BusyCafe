# ADR-0012: Supabase가 production poll과 monitor workflow를 예약 실행한다

- 상태: Accepted
- 날짜: 2026-07-15
- 대체 범위: ADR-0008의 전용 상시 container를 유일한 canonical scheduler로 둔 결정
- 관련 구현: `configure-supabase-scheduler.yml`, `configure_supabase_scheduler.py`,
  `poll-production.yml`, `monitor-production.yml`

## 맥락

GitHub Actions의 자체 `schedule` 이벤트는 실측에서 긴 지연과 누락이 발생해 5~10분
freshness를 보장하지 못했다. 별도 상시 container는 이 문제를 해결하지만 추가 플랫폼,
billing, 재시작 정책과 secret 운영이 필요했다.

Supabase production DB에는 `pg_cron`, `pg_net`, Vault를 사용할 수 있다. DB의 정확한 cron이
GitHub `workflow_dispatch` API를 호출하면 GitHub 자체 cron을 사용하지 않으면서 기존
one-shot worker, environment secret, timeout, concurrency와 감사 로그를 그대로 재사용할 수
있다. 2026-07-13~15 운영에서 이 경로가 5분 간격 poll과 후속 monitor를 반복 실행했고,
121개 대상의 complete cycle과 freshness monitor를 확인했다.

## 결정

현재 production scheduler는 Supabase `pg_cron`으로 한다.

- 매시 `02,07,12,...,57분`에 `poll-production.yml`을 dispatch한다.
- 각 poll 2분 뒤 `monitor-production.yml`을 dispatch한다.
- GitHub workflow에는 자체 `schedule`을 두지 않고 `workflow_dispatch`만 허용한다.
- poll concurrency group은 중복 write cycle을 직렬화한다.
- GitHub PAT는 Supabase Vault에 한 개만 저장하고 SQL·로그·문서에 평문으로 남기지 않는다.
- GitHub Production environment의 `DATABASE_URL`, `SEOUL_API_KEY`와 독립 enable gate를
  유지한다.
- poll 실패 시 monitor는 계속 실행해 stale 상태를 감지한다.

ADR-0008의 Docker worker는 폐기하지 않는다. pg_cron, pg_net, GitHub Actions의 가용성이나
비용이 제품 SLO를 만족하지 못하면 검증된 fallback 후보로 유지한다.

## 근거와 경계

이 결정은 GitHub의 불규칙한 cron을 다시 채택한 것이 아니다. 시간 기준은 Supabase가
소유하고 GitHub는 요청받은 one-shot 실행 환경으로만 동작한다. scheduler 적용 run은
extension, Vault secret 수와 두 cron command의 exact match를 검증한다.

현재 5분 주기는 서울 API의 관측 갱신과 제품 freshness를 맞추기 위한 값이다. API source
지연은 별도이며, poll 성공만으로 관측이 fresh하다고 주장하지 않는다. DB egress와 GitHub
실행량은 월간 비용 gate로 계속 측정한다.

## 재검토 조건

다음 중 하나가 반복되면 전용 worker 또는 DB 내부 계산으로 전환을 다시 평가한다.

- 30일 freshness SLO 미달 또는 3회 연속 dispatch 누락
- cycle p95가 다음 poll 시작 시각과 겹침
- GitHub/Supabase 비용이 상시 worker보다 높아짐
- DB egress·connection 사용량이 공급자 한도를 지속 초과
- Vault/PAT 운영이 조직 보안 기준을 만족하지 못함
