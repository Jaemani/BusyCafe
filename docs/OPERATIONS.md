# BusyCafe 운영 Runbook

## 목적과 현재 상태

이 문서는 관리형 PostgreSQL을 사용하는 준실시간 production의 최초 전환, 정상 운영,
장애 복구와 검증 절차를 정의한다. 공개 `busy-cafe.vercel.app`은 `DATABASE_URL`이 설정되기
전까지 배포 시점의 읽기 전용 SQLite 스냅샷을 사용한다. 관리형 DB와 자동 수집이 실제로
검증되기 전에는 실시간 또는 준실시간 production으로 승격하지 않는다.

필수 비밀값은 다음 두 개다.

- Vercel: `DATABASE_URL`만 저장한다. serverless 연결에는 공급자가 권장하는 pooled TLS
  URL을 사용한다.
- GitHub Actions: `DATABASE_URL`, `SEOUL_API_KEY`를 저장한다. migration과 worker에는
  공급자가 권장하는 direct/write TLS URL을 사용한다. 두 URL은 같은 production DB를
  가리켜야 한다.

GitHub secrets는 repository의 `Production` environment에 저장하고 poll job도 같은
environment를 명시한다. GitHub repository variable `PRODUCTION_POLL_ENABLED`와
`PRODUCTION_MONITOR_ENABLED`는 각각 쓰기 수집과 읽기 전용 신선도 감시를 독립 제어한다.
Supabase의 `Project URL`(`https://...supabase.co`)과 publishable
key는 PostgreSQL connection string이 아니므로 `DATABASE_URL`에 넣지 않는다.

### Supabase 연결 항목 대응

| Supabase 항목 | BusyCafe 사용처 |
|---|---|
| Project URL | 사용하지 않음. Supabase REST API endpoint이며 DB URL이 아님 |
| Publishable key | 사용하지 않음. 브라우저가 Supabase에 직접 접근하지 않음 |
| Direct connection string | GitHub migration/worker 후보. runner에서 IPv6 연결이 안 되면 session pooler 사용 |
| Transaction pooler connection string | Vercel Production `DATABASE_URL` 권장 |
| CLI setup command | 로컬 Supabase CLI용이며 production runtime에 사용하지 않음 |

Supabase가 제공하는 표준 `postgresql://` 문자열은 그대로 secret에 저장한다. 애플리케이션이
내부에서 psycopg 3 dialect로 정규화하며 transaction pooler 호환을 위해 client-side prepared
statement를 비활성화한다. Vercel 값은 Production에만 등록하고 Preview에는 production DB를
노출하지 않는다.

비밀값은 명령 인자, 로그, 문서, 이슈 또는 채팅에 출력하지 않는다. 로컬에서는 shell
환경변수나 커밋되지 않는 `.env`만 사용한다.

## 최초 production 전환

### 1. 관리형 PostgreSQL 준비 `[HUMAN]`

Neon 또는 Supabase에서 production DB를 만들고 다음을 확인한다.

- 서울과 가까운 region
- TLS 연결 강제
- 자동 백업 또는 point-in-time recovery 제공 여부와 보존 기간
- 연결 수 제한과 serverless connection pooling 방식
- 별도의 빈 recovery DB를 만들 수 있는 권한

공급자, region, 백업 보존 기간과 확인 날짜를 `docs/VERIFICATION.md`에 기록한다. 확인하지
않은 RPO/RTO를 제품 약속으로 쓰지 않는다.

### 2. 빈 DB bootstrap

Vercel을 DB에 연결하기 전에 운영자 로컬 환경에서 bootstrap한다. 먼저 migration을 적용한다.

원격 production은 `bootstrap-production.yml`을 기본 dry-run으로 먼저 실행한다.

```bash
gh workflow run bootstrap-production.yml -f apply=false
```

이 run은 migration을 적용하고 hotspot·카페 seed 예상 건수, Overture release와 cache hash를
출력하지만 seed와 외부 서울 API 수집은 적용하지 않는다. 로그 검수와 명시적 HUMAN 승인
뒤에만 `apply=true` run을 실행한다.

```bash
cd backend
uv sync --frozen
uv run alembic -c alembic.ini upgrade head
uv run alembic -c alembic.ini current --check-heads
```

핫스팟과 카페 원장은 dry-run 결과를 검수한 뒤에만 적용한다.

```bash
uv run python scripts/seed_hotspots.py
uv run python scripts/seed_hotspots.py --apply

uv run python scripts/seed_cafes.py --download --download-only
uv run python scripts/seed_cafes.py
uv run python scripts/seed_cafes.py --apply
```

dry-run과 apply의 source count, active count, release와 cache SHA-256을 보존한다. source가
비거나 이전 검증 release와 다르면 apply하지 않는다.

### 3. 첫 수집과 DB 검증

```bash
uv run python -m app.ingest.worker --once
```

로컬 API를 같은 DB에 연결해 다음을 확인한다.

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8190
curl --fail --silent http://127.0.0.1:8190/api/health
```

승격 전 조건:

- migration head 일치
- hotspot 121개와 예상 cafe 원장 건수 존재
- one-shot 수집 `saved=121`, `failed=0`
- `last_complete_cycle_at`이 현재 시각 기준 `STALE_WARN_MIN` 이내
- latest cycle이 `complete`이거나, 직전 complete가 fresh한 상태에서 현재 cycle이 `running`
- bbox 카페 응답에 현재 `model_version`, coverage와 evidence 존재

### 4. 수집 worker 연결

GitHub repository secret에 `DATABASE_URL`, `SEOUL_API_KEY`를 설정한다. 값을 출력하지 않고
secret 이름만 확인한다.

```bash
gh secret list
gh workflow run poll-production.yml
gh run list --workflow poll-production.yml --limit 3
```

GitHub workflow는 read-only canary와 장애 시 수동 fallback에만 사용한다. 2026-07-12
실측에서 cron event가 약 1시간 간격으로 지연·누락됐고 hosted runner의 서울 API 연결도
반복 실패했으므로 production 10분 scheduler로 사용하지 않는다. canonical 운영 경로는
[ADR-0008](adr/ADR-0008-dedicated-production-worker.md)의 `backend/Dockerfile` 상시 worker다.

상시 worker에서 121개 수집과 score materialize가 성공한 뒤 worker의 one-shot exit code와
`ingest_cycles.status=complete`를 확인한다. 이후 1시간 동안 6개 연속 complete cycle과
complete age 25분 이내를 검증하기 전 poll 운영 gate를 통과로 표시하지 않는다.

### 5. read API 전환

bootstrap과 worker 검증이 끝난 뒤에만 Vercel production에 `DATABASE_URL`을 추가하고
재배포한다. 전환 직후 다음을 확인한다.

```bash
curl --fail --silent https://busy-cafe.vercel.app/api/health
curl --fail --silent "https://busy-cafe.vercel.app/api/cafes?bbox=126.91,37.54,126.94,37.57"
```

`last_complete_cycle_at`, latest cycle, cafe count, model version과 evidence를 bootstrap DB의
값과 대조한다. 그 뒤 GitHub repository variable `PRODUCTION_HEALTH_URL`에 production health
URL을 설정한다. `PRODUCTION_MONITOR_ENABLED=true`로 읽기 전용 freshness monitor를 먼저
활성화하고, 수집 검증 뒤 `PRODUCTION_POLL_ENABLED=true`로 쓰기 poll을 별도 활성화한다.
장애 대응으로 poll을 멈출 때도 monitor는 켜 두어 stale 전환을 감지한다. 활성화된 workflow는
필수 URL 또는 secret 누락을 실패로 처리한다.

두 전용 변수는 값이 있으면 각각의 workflow에서 authoritative하며 정확히 `true` 또는
`false`만 허용한다. 전용 변수가 없는 기존 설치에 한해서만 `PRODUCTION_ENABLED`의 정확한
`true`/`false` 값을 legacy fallback으로 사용한다. 전용 변수와 legacy 변수를 섞어 운용하지
말고, 마이그레이션 후에는 전용 변수 두 개를 모두 명시한다.

공개 승격 전 최소 1시간 동안 complete cycle 6회를 확인한다. 각 cycle은 targets=121,
saved=121, failed=0이어야 하고 complete cycle age는 25분을 넘지 않아야 한다.

## 정상 운영 점검

- 10분마다 poll workflow 성공 여부
- `last_complete_cycle_at`이 25분 이내인지
- 최근 cycle의 target/saved/failed 수
- cafe count와 active Overture release의 비정상 급감 여부
- migration head와 서비스 model version
- 서울 API schema parse failure와 secret-bearing 로그가 없는지

주간 점검에서 `docs/INCIDENTS.md`의 미완료 재발 방지 항목도 함께 회수한다. 실패는 원본을
삭제하거나 마지막 정상값을 현재값처럼 표시하지 않고 stale 상태로 남긴다.

## 논리 백업

관리형 공급자의 자동 백업과 별도로 주요 schema 변경 또는 원장 release 승격 전에 암호화된
운영 저장소에 PostgreSQL custom-format dump를 만든다.

```bash
PGDATABASE="$DATABASE_URL" pg_dump --format=custom --no-owner --no-privileges > busy-cafe.dump
shasum -a 256 busy-cafe.dump > busy-cafe.dump.sha256
pg_restore --list busy-cafe.dump > busy-cafe.dump.manifest.txt
```

dump, checksum과 manifest는 repository에 커밋하지 않는다. 저장 위치, 암호화 방식, 생성
시각, source commit과 DB migration revision만 검증 문서에 기록한다.

## 복구 훈련

production DB에 직접 restore하지 않는다. 별도의 빈 recovery DB를 만들고 다음 순서로
검증한다.

```bash
shasum -a 256 -c busy-cafe.dump.sha256
PGDATABASE="$RECOVERY_DATABASE_URL" pg_restore --exit-on-error --no-owner --no-privileges busy-cafe.dump

cd backend
DATABASE_URL="$RECOVERY_DATABASE_URL" uv run alembic -c alembic.ini current --check-heads
```

recovery API를 별도 포트에 띄워 `/api/health`, 대표 bbox와 cafe detail을 확인한다. 테이블
건수, 최신 snapshot, model version과 대표 score를 원본 백업 기록과 대조한다. 성공한
훈련에만 실제 RPO/RTO를 기록하며 최소 분기 1회와 schema 변경 전후에 반복한다.

## 장애 시 rollback

1. `PRODUCTION_POLL_ENABLED=false`로 poll workflow의 추가 write를 막는다.
   `PRODUCTION_MONITOR_ENABLED=true`는 유지해 stale 상태를 계속 감지한다.
2. Vercel의 `DATABASE_URL`을 제거하거나 이전 정상 DB로 교체해 읽기 전용 snapshot 또는
   정상 DB로 되돌린다.
3. 손상 DB는 보존하고 별도 recovery DB에 point-in-time recovery 또는 logical dump를
   복구한다.
4. migration head, 데이터 건수, 최신 snapshot과 API smoke를 확인한다.
5. 새 connection URL로 Vercel과 worker를 순서대로 전환한다.
6. 비밀값 노출 가능성이 있으면 DB credential과 서울 API 키를 교체한다.
7. 타임라인, 영향, 복구 근거와 회귀 방지를 `docs/INCIDENTS.md`에 기록한다.

파괴적 migration downgrade나 production DB 위의 `pg_restore --clean`은 사용하지 않는다.
복구 검증 전 손상 DB를 삭제하지 않는다.
