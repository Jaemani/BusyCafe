# BusyCafe 운영 Runbook

## Linux 개발 및 운영 준비

2026-09-29 점검 기준이다. 이 절은 개발 준비와 운영 후보를 구분하며, 아래 기존 운영
절차의 실제 전환 승인을 대신하지 않는다. 인프라 공용 서비스, DNS, Tunnel, 호스트 재부팅은
이번 작업 범위에서 변경하지 않는다.

### 구성 요소 판정

| 구성 요소 | 확인된 형태와 의존성 | 판정 | 남은 검증 |
|---|---|---|---|
| Vite·TypeScript·MapLibre 프론트 | Vercel 정적 asset, Node 22, 브라우저 WebGL | Linux 개발 가능 / 기존 클라우드 유지 | CachyOS 설치·빌드, 물리 모바일 UX |
| FastAPI 제공 API | Vercel Python, SQLAlchemy·psycopg, Supabase 연결 | Linux 개발 가능 / 기존 클라우드 유지 | 대상 서버의 빈 DB·API smoke |
| 인구 수집 worker | GitHub one-shot, Supabase cron dispatch; Python·서울 API | Linux 운영 후보 / 현재 클라우드 유지 | 단독 writer 전환, 한 시간 수집, 종료·재부팅 복구 |
| PostgreSQL 원장·피드백 | Supabase 관리형 저장소 | 기존 클라우드 유지 | 백업 복원 훈련·최소 권한 역할 분리 |
| Compose PostgreSQL | 로컬 개발용 설정만 존재; volume 사용 | Linux 개발 가능, Docker Compose 유지 후보 | 대상 Docker daemon·volume 권한·migration |
| backend Dockerfile | Python 3.12 이미지용 API/worker 설정 | 설정 존재, 운영 이미지 동작 미검증 | native wheel·migration 파일 누락·이미지 고정 |
| 평가·격자·POI·다운로드 도구 | 일회성 CLI; DuckDB·pyproj·Shapely·파일 데이터 | Linux 실행 후보, 상주 불필요 | x86_64 wheel과 대표 fixture·자원 사용 |
| Safari·iPhone 검증 | Mac 시뮬레이터·물리 기기 | Mac 유지 | Linux 변경 커밋별 검증 결과 전달 |
| Podman·Quadlet | 저장소에 기존 구성 없음 | 판단 보류 | 대상 설치 버전·rootless·부팅·볼륨 미검증 |

프로젝트에 Rust·Flutter·Xcode 빌드 대상은 없다. 해당 도구는 BusyCafe의 개발 선행 조건이
아니다. 소스 조사에서 macOS API, Keychain, MLX/Metal/MPS, Docker socket 의존성을 찾지
못했다. 이것은 CachyOS에서의 실제 기능 검증 완료를 뜻하지 않는다. 파일명 대소문자와
Python native wheel은 Linux CI와 대상 서버에서 각각 확인한다.

현재 Mac의 5188/8190 포트에 listening process는 관측되지 않았다. 다른 포트나 다른
호스트의 실행 부재까지 단정하지 않는다. 진행 중인 Codex와 타 프로젝트 프로세스는
종료하지 않았다. 로컬 `backend/data`는 약 1.3GB였으며 cache·원본·실험 산출물이 포함될
수 있다. 새 서버로 통째로 복사하지 않는다. 80GB 디스크에서는 프로젝트별 사용량과 남은
공간을 먼저 확인하고 대량 생활인구 다운로드·압축 해제는 별도 용량 승인 후 수행한다.
지속 snapshot 증가량, 메모리 peak, 로그 증가량은 아직 측정하지 않았다.

### 운영 방식

공개 웹·DB는 현재 관리형 서비스를 유지한다. 별도 worker가 필요해지면 native user
systemd를 먼저 검증한다. Python virtualenv와 외부 DB만 필요하므로 컨테이너를 추가할
근거가 아직 없다. 개발용 PostgreSQL은 기존 Compose를 유지할 수 있으나 서버의 Docker
준비 상태를 재확인해야 한다. Podman을 선택하지 않았으므로 미검증 Quadlet을 운영 파일로
추가하지 않는다. 향후 검토 시에는 설치 버전, rootless UID/volume mapping, 네트워크,
포트, healthcheck와 reboot 복구를 확인해야 한다.

`deploy/systemd/busy-cafe-worker.service`는 **미활성 운영 후보**다. 부팅 enable을 위한
Install 절이 없고, 별도 `worker-approved` 파일이 없으면 시작되지 않는다. 이 파일은
운영 승인 자체를 대체하지 않는다. 설정의 MemoryMax=1G, CPUQuota=100%, TasksMax=64는
초기 제한 후보이며 부하 측정으로 확정해야 한다. 프로세스 생존은 수집 성공 판정이 아니다.
health의 complete cycle, saved/failed와 관측 시각을 별도로 검사해야 한다.

- 실행은 프로젝트 전용 Unix 사용자 권장. 같은 사용자 아래 서비스끼리는 강한 보안 격리가
  되지 않으며, EnvironmentFile·다른 사용자 파일 접근 통제를 대체하지 않는다.
- release 경로는 사용자 홈 아래 `.local/share/busy-cafe/releases/<commit>`이며
  `current` symlink가 한 release를 가리킨다. 각 release에서 `uv sync --frozen`으로
  Linux virtualenv를 새로 만든다. Mac virtualenv·node_modules·계정 token은 복사하지 않는다.
- 사용자 소유 `.config/busy-cafe/worker.env`(0600, 상위 디렉터리 0700)에
  `DATABASE_URL`, `SEOUL_API_KEY`를 넣는다. 인자는 비밀값 전달에 사용하지 않는다.
- 네트워크는 외부 PostgreSQL과 서울 API outbound만 필요하며 worker는 inbound port를
  열지 않는다. DB migration head가 다르면 시작 전 검사가 실패한다. unit은 migration을
  자동 적용하지 않는다. cache 원본은 backend/data에 생성될 수 있으므로 release 교체 전
  위치와 필요 여부를 확인한다.
- SIGINT로 정상 종료를 요청하고 120초를 기다린다. 강제 종료가 발생하면 미완료 cycle과
  저장 상태를 확인해야 한다. restart 한도 도달·partial cycle은 경보 대상이다.
- journal의 rate limit은 저장량 상한이 아니다. journald 총 용량·보존기간은 인프라 담당자와
  조율하고, 공용 설정을 이 프로젝트가 임의 변경하지 않는다.
- user linger와 부팅 target 연결은 운영 전환 검증 뒤 인프라 담당자가 승인한다.
  이번 작업에서는 `enable`, `start`, `loginctl enable-linger`를 실행하지 않는다.

전환 순서: 백업·복구 검증 → 별도 test DB에서 fixture 검증 → 기존 scheduler의 poll 중단과
진행 중 cycle 종료 확인 → 새 writer 단독 시작 → 최소 1시간 12회 완전 cycle 확인이다.
rollback 시 새 writer를 먼저 정지하고 미완료 cycle·신규 snapshot·원장/피드백을 보존한다.
코드만 이전 release로 돌릴 때도 DB migration 호환성을 확인한다. DB를 과거 dump로
덮어쓰거나 새 관측을 삭제하지 않는다. 기존 scheduler 재개는 단독 writer 확인 후에만 한다.

### Linux Codex 인수인계

저장소는 README의 GitHub 저장소이며 작업 브랜치는 `chore/linux-readiness-20260929`다.
최종 검증 SHA는 `VERIFICATION.md`에서 확인하고 다음 순서로 독립 checkout한다.
예제의 `<verified-commit>`은 실제 검증 SHA로 바꾸어야 한다.

```bash
mkdir -p "$HOME/projects"
git clone --branch chore/linux-readiness-20260929 git@github.com:Jaemani/BusyCafe.git "$HOME/projects/busy-cafe"
cd "$HOME/projects/busy-cafe"
git checkout --detach <verified-commit>
uname -srmo
df -h .
free -h
node --version
uv --version
python3 --version
systemctl --version
docker version
docker compose version
docker buildx version
podman --version
```

미설치 명령의 실패도 기록하고, Docker CLI 존재만으로 daemon 정상이라 판정하지 않는다.
호스트 reboot 전후 커널 상태·Podman 설치 여부는 인프라 기록과 실제 결과를 대조한다.
이 프로젝트 준비를 이유로 재부팅하거나 공용 런타임을 설치·교체하지 않는다.

```bash
cd backend
UV_PROJECT_ENVIRONMENT=.venv-linux uv sync --frozen --extra dev --python 3.12
UV_PROJECT_ENVIRONMENT=.venv-linux uv run --frozen pytest
UV_PROJECT_ENVIRONMENT=.venv-linux uv run --frozen python -m compileall -q app scripts tests
cd ../frontend
npm ci
npm test
npm run typecheck
npm run build
```

fixture 테스트와 frontend build에는 운영 secret이 필요하지 않다. 테스트 뒤 API smoke는
별도 빈 SQLite 또는 격리 PostgreSQL만 사용하고, 운영 `.env`를 가져오지 않는다. SQLite
smoke는 PostgreSQL RLS·migration 검증을 대체하지 않는다. PostgreSQL migration 실적용은
Ubuntu CI의 임시 PostgreSQL 17 service가 검증한다.

개발 서버를 확인할 때는 README의 8190/5188 loopback 실행과 SSH port forwarding을
사용한다. 새 호스트 주소를 소스의 allowedHosts·CORS에 무조건 추가하거나 0.0.0.0으로
공개하지 않는다. API·Vite를 background 상주로 남기지 않는다.

Mac 후속 검증은 Linux에서 확정한 커밋을 독립 worktree로 checkout해 수행한다.
Safari 검색 input 확대, notch/safe-area, 지도 control, 상세 panel과 위치 권한을 확인하고
커밋·기기·OS·결과를 `VERIFICATION.md`에 기록한다. Mac offline이면 해당 검증을 대기로
표시한다. 코드 인수인계는 기존 Codex 대화의 실행 소유권·로그인 복제를 뜻하지 않는다.

### 직접 참여가 필요한 항목과 완료 기준

사용자가 비공개 대화로 CachyOS 접속 대상을 제공했다. 호스트는 도달하지만 현재 Mac
기본 공개키를 거부하므로 서버 검증이 막혀 있다. 사용자는 Mac 터미널에서
`ssh-copy-id -i ~/.ssh/id_ed25519.pub <approved-user@host>`를 실행하고 서버 비밀번호를
직접 입력한다. 별도 승인 키가 있다면 키 파일 경로만 제공해도 된다. 비밀번호·토큰은
대화나 Git에 전달하지 않는다. GitHub SSO/MFA가 필요한 경우 계정 화면에서 승인한다.
에이전트가 `ssh -o BatchMode=yes <alias> 'uname -srmo'` 성공을 확인한 뒤 독립 checkout,
설치 버전·디스크·커널 상태, tests/build와 임시 DB smoke를 이어서 수행한다.

서버 준비 완료는 실제 CachyOS에서 위 절차와 기능 smoke가 통과했을 때만 선언한다.
운영 이전 완료는 별도 전환 승인, 단독 writer, 복구 훈련, 한 시간 관측, 부팅·정상 종료
검증까지 포함한다. 이번 작업의 문서·CI 통과는 이 두 조건을 대신하지 않는다.

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

## Kakao 카페 원장 refresh

`.github/workflows/apply-kakao-catalog-production.yml`은 매주 화요일 03:23 KST에 서울
CE7 complete snapshot을 다시 수집한다. 사용자 검색과 지도 API는 이 작업 중에도 기존
PostgreSQL 원장만 읽는다.

자동 실행은 신규 candidate 최대 2,000곳, 250m 초과 좌표 이동 허용 0건으로 고정한다.
candidate 상한을 넘으면 DB mutation 전에 실패한다. 큰 이동 발견 수가 허용 상한을 넘으면
그 배치의 큰 이동 전부를 격리하고 cafe와 provider 검증 상태를 동결하되, 정상 갱신과 신규
삽입은 계속한다. 큰 이동을 승인하려면 dry-run JSON과 표본을 검토한 뒤 manual dispatch에서
발견 수 전체를 포함하는 상한과 `APPLY_KAKAO_CATALOG` 확인문을 명시해야 한다. incomplete
sweep, schema head 불일치, 서울 주소·bbox 실패도 적용을 차단한다.

성공 순서는 complete sweep → schema 확인 → dry-run → 단일 transaction apply → 전체
score materialize다. manifest와 dry/apply report는 30일 artifact로 보존하지만 raw Kakao
cache는 artifact로 공개하지 않는다. 한 번의 complete snapshot에서 보이지 않은 Place ID는
보고만 하고 자동 비활성화하지 않는다.

첫 실측 complete sweep은 3,794 API 호출을 사용했다. 호출량과 Kakao 앱 쿼터, 정책 변경을
주간 점검에 포함한다. 상업화 전 사용 범위 확인 조건은 ADR-0014와
`LICENSE_ATTRIBUTION_AUDIT.md`를 따른다.

2026-07-15 production에서 cafe/provider 약 1.98만 곳을 ORM으로 갱신한 apply는 35분 15초가
걸렸다. 사용자 읽기는 중단되지 않았지만 weekly maintenance로는 과도하다. bulk update와
batch progress 로그를 적용하기 전까지 90분 job 상한을 줄이지 않고, 실행 중 강제 취소하지
않는다. 최적화 뒤에는 같은 production 규모의 step duration을 다시 측정해 이 기록을
대체하지 말고 후속 기록으로 남긴다.

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

## 사용자 장소 신고와 혼잡도 피드백

브라우저는 Supabase Data API에 직접 쓰지 않는다. 세 POST endpoint는 Vercel FastAPI가
PostgreSQL에만 기록하며, bundled SQLite snapshot과 `CAFE_CROWD_SNAPSHOT=1`에서는 503으로
거부한다. 긴급 중단은 Vercel의 `USER_CONTRIBUTIONS_ENABLED=false`를 설정하고 재배포한다.
성공·검증 오류·서버 오류 응답은 모두 `Cache-Control: no-store`여야 한다.

장소 신고는 `cafe_place_reports.status=pending`, 혼잡도 피드백은
`cafe_crowd_feedback.status=unverified`로 시작한다. 운영자가 Kakao 원장 갱신 또는 별도
근거로 확인하기 전에는 status를 승격하지 않는다. 이 두 테이블을 카페 원장 update나 공개
score materialize 입력으로 연결하지 않는다. 애플리케이션 DB에는 IP, user-agent, 계정,
정확한 사용자 좌표와 검색어를 추가하지 않는다.

현재 Vercel 플랜은 custom Firewall을 제공하지 않는다. 서버는 PostgreSQL의
`user_contribution_rate_limits` 두 aggregate row로 feedback 분당 120건, 장소 신고 분당
30건의 global cap을 원자적으로 적용한다. row에는 kind, minute epoch와 count만 있고 IP,
사용자 token이나 위치는 없다. 과거 minute 요청이 새 bucket을 되돌릴 수 없으며 초과 응답은
429 `no-store`다. 이 cap은 DB row 증가와 대량 data poisoning을 제한하지만 serverless
connection 폭주나 분산 공격 자체를 edge에서 차단하지는 않는다.

광범위한 홍보 전에는 [HUMAN] custom Firewall이 가능한 plan, Turnstile 또는 외부 shared
limiter를 검토하고 429 응답 뒤 GET 지도 API가 정상인지 확인한다. serverless instance의
메모리 limiter는 인스턴스 사이에서 공유되지 않으므로 대체 수단이 아니다. 현재 global cap,
2KB body 제한과 UI 중복 클릭 차단을 유지하고, 비정상 429·DB connection·function invocation
증가 시 `USER_CONTRIBUTIONS_ENABLED=false`로 즉시 닫는다.

원시 제출은 최대 12개월 보관한다. 첫 삭제 시점 전에는 backup을 확인하고 다음 count를
기록한 뒤 승인된 maintenance transaction에서 같은 조건의 row만 삭제한다.

```sql
SELECT count(*) FROM cafe_place_reports
WHERE created_at < now() - interval '12 months';
SELECT count(*) FROM cafe_crowd_feedback
WHERE created_at < now() - interval '12 months';
```

삭제 결과에는 row count와 실행 시각만 남기고 카페명·카페 ID·제출 시각 원문을 문서나
공개 artifact에 기록하지 않는다. 자동 purge와 실패 alert는 첫 제출 후 12개월 전에 별도
운영 gate로 구현한다.

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

Vercel의 `Ready`와 GitHub deployment success만으로 공개 승격을 완료 처리하지 않는다.
최신 deployment URL과 `busy-cafe.vercel.app`에서 HTML asset fingerprint, `/api/health`,
변경 기능의 대표 API 응답을 각각 확인한다. 둘이 다르면 새 기능 홍보를 중단하고
`vercel inspect <canonical-domain>`으로 실제 대상 deployment를 확인한 뒤, 검증된 최신
Ready deployment에만 `vercel alias set <deployment-url> busy-cafe.vercel.app`을 실행한다.
alias 변경 뒤에는 canonical domain을 cache-bust하여 같은 smoke를 다시 수행한다.

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
