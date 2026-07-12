# ADR-0008: 운영 수집은 전용 상시 worker에서 실행한다

- 상태: Accepted (worker provider 배포 대기)
- 날짜: 2026-07-12
- 대체 범위: ADR-0005의 GitHub Actions 기본 scheduler 결정

## 맥락

Supabase production DB와 Vercel read API 승격 후 GitHub Actions에서 121개 서울 장소의
one-shot poll을 실측했다. 첫 complete cycle은 약 4분 6초였지만 후속 cycle은 서울 API
연결 실패 또는 8분 deadline에 걸렸다. bounded concurrency와 batch persistence 적용 후
장애 cycle은 65.536초에 fail-closed 처리됐으나, hosted runner에서 첫 5개 서울 API 요청이
모두 실패했다.

별도로 `*/10` cron의 실제 schedule event를 확인한 결과 약 1시간 간격으로 실행됐고
최대 3시간 46분 공백이 있었다. 이는 제품의 25분 stale 기준을 구조적으로 보장하지 못한다.

## 결정

production 10분 수집은 `backend/Dockerfile`의 `python -m app.ingest.worker`를 실행하는
전용 상시 worker 한 인스턴스가 담당한다. worker는 Vercel과 같은 Supabase PostgreSQL을
사용하되 `SEOUL_API_KEY`는 worker에만 둔다. 동일 cycle 중복 실행을 금지하고, 배포 후
1시간 동안 6개 complete cycle의 `targets=121, saved=121, failed=0`을 승격 gate로 삼는다.

GitHub Actions poll workflow는 migration, canary, 장애 시 수동 fallback으로만 유지한다.
자동 poll gate는 전용 worker 검증 전까지 끈다. freshness monitor는 독립 gate로 계속
실행해 stale 상태를 실패로 보고한다.

## 구현 조건

- 서울 또는 인접 region에서 outbound 연결이 가능한 장기 실행 container
- 프로세스 재시작 정책과 단일 instance 보장
- Supabase Session pooler 또는 장기 worker에 맞는 connection URL
- cycle duration, complete age, saved/failed와 worker restart 로그 보존
- secret rotation, container rollback, DB backup/restore runbook 연결

provider 선택과 계정·billing 생성은 `[HUMAN]` 작업이다. Cloud Run의 instance-based worker,
Railway worker 등 위 조건을 만족하는 플랫폼을 선택할 수 있으나, 공급자별 설정을 core
코드에 넣지 않는다.

## 결과

GitHub cron의 편의보다 freshness 약속과 네트워크 안정성을 우선한다. 관리형 worker 비용과
운영 설정이 추가되지만 read API와 ingest의 분리, cache-first 요청 경로, 기존 Docker image는
유지된다. 전용 worker가 검증되기 전 공개 앱은 live DB를 읽더라도 갱신 지연 상태로 표현한다.
