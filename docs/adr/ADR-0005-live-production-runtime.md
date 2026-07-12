# ADR-0005: 실시간 운영은 관리형 PostgreSQL과 분리 worker를 사용한다

- 상태: Accepted (DB/API 승격 완료, worker 이전 대기)
- 날짜: 2026-07-11

> scheduler 선택은 2026-07-12 실측 후
> [ADR-0008](ADR-0008-dedicated-production-worker.md)이 대체한다. 이 문서의 GitHub Actions
> 기본 scheduler 설명은 역사적 bootstrap 경로로만 유지한다.

## 결정

Vercel은 프론트엔드와 FastAPI read API를 제공한다. 정본 데이터는 관리형 PostgreSQL에
저장하고, `app.ingest.worker`는 web 요청과 분리해 10분 간격으로 서울 도시데이터를
가져온 뒤 cafe score를 갱신한다. 대상별 snapshot 저장과 score materialize는 하나의 DB
transaction이 아니다. 대신 cycle 시작과 완료 결과를 별도 영속화하며, 전체 대상 저장과
materialize가 모두 성공한 경우에만 cycle을 `complete`로 표시한다.

Vercel의 SQLite 번들은 데모/장애 시 읽기 전용 fallback으로만 유지한다. Vercel 함수의
임시 파일 시스템과 함수 수명에 폴링 상태를 의존하지 않는다.

## 배포 경로

1. Neon 또는 Supabase PostgreSQL을 생성하고 `DATABASE_URL`을 Vercel과 GitHub Actions
   secrets에 각각 설정한다.
2. production DB에 migration, 121개 hotspot, versioned Overture cafe catalog를 한 번
   bootstrap한다. 이 단계는 데이터 출처와 건수를 검수한 운영자만 실행한다.
3. Vercel API는 `DATABASE_URL`이 있으면 자동으로 PostgreSQL을 읽는다. 없으면 명시적
   `배포 스냅샷` SQLite fallback을 읽는다.
4. 기본 scheduler는 `.github/workflows/poll-production.yml`의 10분 one-shot job이다.
   중복 실행은 concurrency group으로 차단한다.
5. 관측 지연 SLA가 엄격해지면 GitHub Actions scheduler를 항상 켜진 Docker worker
   (`backend/Dockerfile`, `python -m app.ingest.worker`)로 교체한다. 이때도 API와 worker는
   같은 PostgreSQL을 사용한다.

## 이유와 한계

- 121개 폴링과 전체 score materialize는 DB write가 필수다. 서버리스 SQLite는 이를
  보장하지 못한다.
- GitHub Actions cron은 편리한 MVP scheduler지만 지연될 수 있으므로 엄격한 정확한
  10분 SLA의 최종 해법은 아니다.
- freshness와 승격 판정은 개별 snapshot의 최신 시각이 아니라 마지막 `complete` cycle을
  기준으로 한다. 부분 성공이나 materialize 실패는 최신 snapshot이 있어도 정상으로 보지 않는다.
- `SEOUL_API_KEY`는 worker에만 설정한다. Vercel read API에는 키를 배포하지 않는다.
