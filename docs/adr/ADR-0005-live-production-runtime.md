# ADR-0005: 실시간 운영은 관리형 PostgreSQL과 분리 worker를 사용한다

- 상태: Accepted (credentials/initial bootstrap 대기)
- 날짜: 2026-07-11

## 결정

Vercel은 프론트엔드와 FastAPI read API를 제공한다. 정본 데이터는 관리형 PostgreSQL에
저장하고, `app.ingest.worker`는 web 요청과 분리해 10분 간격으로 서울 도시데이터를
가져와 snapshot과 cafe score를 원자적으로 갱신한다.

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
- `SEOUL_API_KEY`는 worker에만 설정한다. Vercel read API에는 키를 배포하지 않는다.
