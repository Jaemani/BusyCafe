# BusyCafe 운영 Runbook

## 목적과 현재 상태

이 문서는 관리형 PostgreSQL을 사용하는 준실시간 production의 전환, 정상 운영, 비용,
보안, 장애 복구와 검증 절차를 정의한다. 2026-07-15 현재 공개
`busy-cafe.vercel.app`은 Supabase PostgreSQL을 읽고, Supabase `pg_cron`이 5분마다 GitHub
one-shot worker를 dispatch한다. SQLite snapshot은 DB 장애 시 제한된 rollback 후보일 뿐
현재 production 원장이 아니다.

필수 비밀값은 다음 두 개다.

- Vercel: `DATABASE_URL`만 저장한다. serverless 연결에는 공급자가 권장하는 pooled TLS
  URL을 사용한다.
- GitHub Actions: `DATABASE_URL`, `SEOUL_API_KEY`를 저장한다. runner에서 검증된 pooled TLS
  URL을 사용하며, direct URL을 쓸 때에는 IPv6 도달성과 connection 제한을 먼저 확인한다.
  Vercel과 GitHub URL은 같은 production DB를 가리켜야 한다.

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

### 4. 수집 scheduler와 worker 연결

GitHub repository secret에 `DATABASE_URL`, `SEOUL_API_KEY`를 설정한다. 값을 출력하지 않고
secret 이름만 확인한다.

```bash
gh secret list
gh workflow run poll-production.yml
gh run list --workflow poll-production.yml --limit 3
```

GitHub의 자체 cron은 사용하지 않는다. 실측에서 schedule event가 약 1시간 간격으로
지연·누락됐기 때문이다. canonical 경로는
[ADR-0012](adr/ADR-0012-supabase-dispatched-production-scheduler.md)의 Supabase
`pg_cron → pg_net → workflow_dispatch`다. poll은 매 5분 offset 2분, monitor는 각 poll
2분 뒤 실행한다. `production-citydata-poll` concurrency group으로 write cycle을 직렬화한다.

Supabase Vault에 `busy_cafe_github_pat`가 정확히 한 개 있고 `pg_cron`, `pg_net` extension이
활성화된 상태에서 다음 workflow를 dry-run한 뒤 적용한다.

```bash
gh workflow run configure-supabase-scheduler.yml -f apply=false
gh workflow run configure-supabase-scheduler.yml -f apply=true
```

적용 run은 두 cron job의 schedule, command와 active 상태가 코드와 exact match인지 확인한다.
전용 Docker worker는 scheduler 장애·비용 회귀 시 fallback으로만 유지한다.

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

공개 승격 전 최소 1시간 동안 expected complete cycle 12회를 확인한다. 각 cycle은 targets=121,
saved=121, failed=0이어야 하고 complete cycle age는 25분을 넘지 않아야 한다.

## 정상 운영 점검

- 5분마다 poll workflow 성공 여부와 중복 cycle 부재
- `last_complete_cycle_at`이 25분 이내인지
- 최근 cycle의 target/saved/failed 수
- cafe count와 active Overture release의 비정상 급감 여부
- migration head와 서비스 model version
- 서울 API schema parse failure와 secret-bearing 로그가 없는지

## Supabase 보안과 사용량

브라우저는 Supabase Data API를 사용하지 않는다. 애플리케이션 public table과
`alembic_version`은 RLS를 활성화하고 정책을 만들지 않으며, `anon`, `authenticated`의
table·sequence 권한과 향후 default grant를 회수한다. 서버의 owner/pooler 연결만 유지한다.
새 migration이 public table을 추가하면 같은 revision에서 RLS와 grant 검사를 함께 추가한다.

RLS와 `anon` 권한 회수는 브라우저의 Data API 접근을 막지만 server connection의
최소권한을 대신하지 않는다. 현재 owner급 URL 하나를 공유하는 상태는 임시 운영 경계다.
광범위한 홍보 전에 다음 세 역할을 별도 secret으로 전환한다.

- Vercel `web_readonly`: API가 사용하는 table/view의 SELECT만 허용
- GitHub `ingest_writer`: snapshot, cycle, materialized state에 필요한 SELECT·INSERT·UPDATE만 허용
- bootstrap/migration `migration_owner`: 자동 runtime에 주입하지 않고 승인된 workflow에서만 사용

전환은 새 역할을 먼저 만들고 각 URL의 허용·거부 SQL을 검증한 뒤 Vercel, worker 순서로
canary한다. owner URL을 먼저 폐기하지 않으며 rollback 확인 뒤 기존 secret을 교체한다.

Supabase dashboard에서 BusyCafe가 실제 사용하는 핵심 지표는 Database Size와 Egress다.
Cached Egress는 PostgreSQL query cache가 아니라 Storage CDN 지표이므로 Storage를 사용하지
않는 현재 구조에서는 0이 정상이다. Auth MAU, Realtime, Edge Function과 Storage도 사용하지
않으므로 0이 정상이다.

Egress는 다음 순서로 대응한다.

- 월 allowance 60%: 증가 원인과 일평균을 기록
- 80%: catalog refresh·부하테스트·cache-bust 여부 점검, 신규 대량 작업 중단
- 100%: 반복 전송 query를 우선 수정하고 plan 제한·서비스 영향 확인

Dashboard 누적값은 결제 주기 중 감소하지 않으므로 변경 전후 24시간 증가량으로 효과를
판정한다. 현재 Python materialize의 최소 projection도 5분 cadence에서 월 30GB 안팎의
DB→worker 원시 전송 가능성이 있다. DB 내부 계산은 egress를 크게 줄일 수 있지만 API와
DB CPU를 공유하므로 `VERIFICATION.md`의 parity·timing gate 없이 전환하지 않는다.

매 5분 materialize에서 전체 history JSON이나 이전 materialized JSON을 다시 읽지 않는다.
필요한 column projection을 회귀 테스트로 고정한다. connection pool은 지연과 동시성을
개선하지만 전송 byte를 줄이지 않으므로 egress 해결책으로 설명하지 않는다.

## Analytics와 공개 베타 점검

Vercel dashboard의 enable 표시만으로 Analytics 활성으로 판정하지 않는다.
`/_vercel/insights/script.js` HTTP 200과 첫 production pageview를 확인한다. custom event는
현재 plan이 지원하고 `VITE_ENABLE_CUSTOM_ANALYTICS=true`를 명시한 경우에만 활성화한다.
정확한 위치, bbox, 카페·검색 식별정보는 analytics에 넣지 않는다. 세부 계약은
`PRODUCT_METRICS.md`, 사용자 안내는 production의 `/privacy.html`이 소유한다.

실사용 홍보 전 다음을 확인한다.

- 위치 허용·거절, 모바일 지도, 상세와 외부 링크
- API 오류·stale 상황에서 현재값 오인 차단
- warm edge hit와 cold/cache-bust를 분리한 부하테스트
- bbox 최대 span, viewport row 상한과 DB pool exhaustion 방어
- 개인정보·면책·장소 정정과 비공개 보안 제보 경로
- production security header와 외부 링크 allowlist
- Vercel project name/domain/alias, SSO 공개 정책, canonical·`og:url`과 cache-bust smoke
- Vercel rate limit/WAF, function·DB spend alert와 읽기 API kill switch의 실제 동작
- 사용자에게 노출할 운영자 개인정보 문의 채널, 분석 처리 지역·보존기간의 법률 검토
- 주요 상권에서 사용자가 알고 있는 카페의 catalog hit-rate와 장소 정정 처리시간

현재 poll과 monitor는 같은 Supabase `pg_cron → pg_net → GitHub` 경로를 공유한다. worker
실패는 monitor가 잡지만 scheduler, PAT 또는 GitHub dispatch 전체 장애는 함께 놓칠 수 있다.
대규모 홍보 전 이 경로와 독립된 외부 uptime check를 `/api/health`에 연결하고, 25분 초과
시 실제 수신자에게 알림·확인·escalation되는 과정을 한 번 훈련한다.

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
