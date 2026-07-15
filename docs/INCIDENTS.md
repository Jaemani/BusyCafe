# Incident Log

서비스 장애뿐 아니라 데이터 오해, 잘못된 추정 표시, 쿼터 소진, 시크릿 노출 위험, 중요한 개발 실수를 기록한다. 개인을 탓하지 않고 원인과 시스템 개선에 집중한다.

## 심각도 기준

- `SEV-1`: 사용자 안전·보안·광범위한 잘못된 정보 또는 전체 중단
- `SEV-2`: 핵심 기능의 상당한 장애나 일부 사용자에게 중대한 오정보
- `SEV-3`: 제한된 기능 저하, 운영 오류, 사용자 영향이 작은 실수

## 인시던트 템플릿

```md
## INC-YYYY-NNN — 제목

- 상태: Open | Monitoring | Resolved
- 심각도: SEV-1 | SEV-2 | SEV-3
- 시작 시각(KST):
- 감지 시각(KST):
- 해결 시각(KST):
- 작성자:
- 관련 이슈/커밋:

### 요약

무슨 일이 있었고 사용자에게 어떤 영향이 있었는가?

### 타임라인

- HH:MM — 사건/조치

### 탐지

어떻게 발견했으며 더 빨리 발견할 수 있었는가?

### 근본 원인

직접 원인과 이를 허용한 시스템·프로세스 원인은 무엇인가?

### 대응과 복구

어떤 조치로 영향을 줄이고 복구했는가?

### 잘된 점 / 어려웠던 점

- 잘된 점:
- 어려웠던 점:

### 재발 방지 조치

- [ ] 조치 — 담당자 — 기한 — 추적 링크

### 교훈

다음 구현과 운영에 반영할 원칙은 무엇인가?
```

## INC-2026-010 — 비활성 production poll의 preflight가 checkout 전에 실패

- 상태: Resolved
- 심각도: SEV-3
- 시작 시각(KST): 2026-07-11 production poll workflow 추가 시점
- 감지 시각(KST): 2026-07-12
- 해결 시각(KST): 2026-07-12
- 작성자: Codex
- 관련 커밋/실행: `b0712e6`, 실패 run `29162731378`, 검증 run `29164391760`

### 요약

production secret이 없는 동안 성공 skip해야 하는 scheduled poll이 checkout 전
`backend/` working directory에서 preflight shell을 시작하려 했다. repository가 아직
checkout되지 않아 디렉터리가 없었고, 외부 API나 DB 호출 전에 job이 실패했다. 공개
snapshot과 사용자 요청에는 영향이 없었지만 workflow가 반복해서 불필요한 실패를 만들었다.

### 근본 원인

job-level 기본 working directory를 `backend`로 지정하면서 checkout 전 실행하는 설정 확인
step에도 같은 기본값이 적용된다는 점을 놓쳤다. secret 누락 분기는 테스트했지만 GitHub의
checkout 이전 파일 시스템 상태를 재현하지 않았다.

### 대응과 복구

preflight step의 working directory를 workspace root로 명시했다. `PRODUCTION_ENABLED`가
`true`가 아니면 명시적으로 skip하고, 활성화 후 secret이 빠지면 실패하도록 계약도 분리했다.
현재 revision의 workflow dispatch에서 checkout 이후 단계가 모두 skip되고 job이 성공하는
것을 확인했다. freshness monitor도 같은 비활성 경로를 별도 run으로 검증했다.

### 재발 방지 조치

- [x] checkout 전 step은 repository 내부 working directory를 사용하지 않도록 고정
- [x] 비활성 poll workflow dispatch 성공 검증 — run `29164391760`
- [x] 비활성 freshness monitor dispatch 성공 검증 — run `29164392552`
- [ ] managed DB 전환 시 `PRODUCTION_POLL_ENABLED=true`에서 secret 누락 실패와 정상 cycle 성공을 각각 검증

### 교훈

workflow의 skip 경로도 실제 runner의 checkout 전 상태에서 실행 가능한 독립 경로여야 한다.

## INC-2026-011 — Vercel Preview 환경변수 제거가 Production 값까지 삭제

- 상태: Resolved (올바른 pooled URL 재등록 대기)
- 심각도: SEV-3
- 시작/감지/해결 시각(KST): 2026-07-12
- 작성자: Codex
- 관련 상태: Vercel `busy-cafe`, `DATABASE_URL` 미설정

### 요약

사용자가 Vercel `DATABASE_URL`을 Preview와 Production에 함께 등록한 상태에서 Preview의
production DB 접근만 제거하려 했다. `vercel env rm DATABASE_URL preview --yes`가 환경별
binding만 제거할 것으로 가정했지만 실제로는 변수 전체를 삭제했다. 당시 공개 deployment는
managed DB를 사용하지 않는 snapshot이어서 API와 사용자 데이터 영향은 없었다.

### 근본 원인

CLI의 삭제 범위를 help 또는 dry-run으로 확인하지 않고 환경 인자가 안전하게 scope를
제한한다고 가정했다. 암호화된 기존 값은 CLI로 다시 읽을 수 없으므로 자동 복원도 불가능했다.

### 대응과 복구

즉시 `vercel env ls`에서 변수가 없음을 확인하고 공개 `/api/health`가 HTTP 200,
`data_mode=snapshot`, `cafes_count=4933`을 유지하는지 검증했다. 삭제된 값은 serverless에
부적합할 수 있는 direct connection string이었으므로 복원하지 않는다. 사용자가 Supabase의
pooled connection string을 Production 전용으로 다시 등록한 뒤 새 deployment에서 연결을
검증한다.

### 재발 방지 조치

- [x] 삭제 직후 전체 environment 목록과 공개 health 확인
- [x] 운영 Runbook에 Supabase Project URL/publishable key가 DB URL이 아님을 명시
- [ ] pooled `DATABASE_URL`을 Production 전용으로 등록하고 Preview 미노출 확인
- [ ] Vercel 환경변수 변경 전 CLI scope를 help로 확인하고 복구값 준비

### 교훈

암호화되어 다시 읽을 수 없는 설정을 삭제할 때는 scope 가정을 금지하고, 복구 가능한 새 값을
먼저 준비해야 한다.

## INC-2026-012 — 애플리케이션과 Alembic의 Supabase URL 처리 불일치

- 상태: Resolved
- 심각도: SEV-3
- 시작/감지/해결 시각(KST): 2026-07-12
- 작성자: Codex
- 관련 커밋/실행: `7139f3c`, `682f151`, bootstrap run `29165824349`

### 요약

Supabase 표준 `postgresql://` URL을 애플리케이션 engine에서는 psycopg 3 dialect로
정규화했지만 Alembic은 별도 설정 경로에서 raw URL을 사용했다. 첫 production bootstrap
dry-run이 migration 전에 `psycopg2` 모듈을 찾지 못하고 종료됐다. DB 연결과 schema 변경은
발생하지 않았다.

### 근본 원인

runtime `create_db_engine()`만 production DB 진입점으로 간주했고, Alembic `env.py`가
`engine_from_config()`로 독립 engine을 만든다는 점을 URL 호환 테스트 범위에 포함하지 않았다.

### 대응과 복구

URL 정규화 함수를 공개 단일 함수로 만들고 runtime과 Alembic이 함께 사용하도록 변경했다.
Alembic에도 psycopg 3 transaction-pooler 호환 설정을 적용했다. raw `postgresql://` URL을
사용한 offline migration render와 전체 테스트를 통과시킨 뒤 dry-run을 재실행했다.

### 재발 방지 조치

- [x] runtime과 migration URL 정규화 단일화
- [x] `postgresql://`, `postgres://`, explicit psycopg, SQLite 회귀 테스트
- [x] raw Supabase 형식으로 Alembic PostgreSQL SQL render 검증
- [x] Session pooler로 실제 remote migration dry-run 통과 — run `29166166360`

### 교훈

DB 호환성은 요청 runtime뿐 아니라 migration, worker, backup 도구의 모든 연결 경로에서
검증해야 한다.

## INC-2026-001 — 추정 스키마가 원본 fixture 저장을 막을 수 있었던 설계

- 상태: Resolved
- 심각도: SEV-3
- 시작 시각(KST): 2026-07-11 (초기 구현 중)
- 감지 시각(KST): 2026-07-11 (코드 리뷰 중)
- 해결 시각(KST): 2026-07-11
- 작성자: Codex
- 관련 이슈/커밋: 문서 `f1be918`, 수정 및 회귀 테스트 `e2c044d`

### 요약

초기 `verify_apis.py`가 학습 지식 기반의 provisional Pydantic 모델로 응답을 먼저
검증한 뒤 fixture를 저장하도록 작성됐다. 실제 응답 구조가 추정과 다르면 가장
필요한 원본 응답이 저장되지 않아 Phase 0의 검증 순서를 위반할 수 있었다.
커밋과 실 API 실행 전에 리뷰로 발견되어 사용자 영향과 데이터 손실은 없었다.

### 근본 원인

"잘못된 fixture를 만들지 않는다"는 방어와 "원본을 먼저 확보해 스키마를 확정한다"는
목적을 혼동했다. 검증 스크립트와 확정 파서를 한 단계로 묶은 것이 직접 원인이다.

### 대응과 복구

검증 스크립트가 HTTP JSON 원본을 overwrite 없이 먼저 보존하고, 파싱 결과는 그 뒤
별도로 보고하도록 수정한다. 파싱 실패 시에도 원본 fixture가 남는 테스트를 추가한다.

### 재발 방지 조치

- [x] 원본 수집과 스키마 검증 단계를 분리 — Codex — 2026-07-11
- [x] 파싱 실패 시 fixture 보존 테스트 추가 — Codex — 2026-07-11
- [ ] 실제 fixture 확보 후 provisional 모델과 `[VERIFY]` 문서 동시 갱신 — 사용자/Codex — API 키 준비 후

### 교훈

외부 스키마를 검증하는 도구는 알려진 모델보다 원본 증거를 우선 보존해야 한다.

## INC-2026-002 — 단위 테스트가 개발자의 실제 `.env`에 의존

- 상태: Resolved
- 심각도: SEV-3
- 시작/감지/해결: 2026-07-11
- 작성자: Codex
- 관련 커밋: `c641095`

### 요약

키 누락 preflight 테스트가 환경 변수를 제거했지만 Settings가 실제 `backend/.env`를
다시 읽을 수 있었다. 키가 없던 초기 환경에서는 통과했으나 사용자가 키를 설정한
뒤 실패해, 테스트가 머신 상태에 의존한다는 사실이 드러났다. 외부 호출이나 사용자
데이터 변경은 없었다.

### 근본 원인과 조치

환경 변수만 조작하면 `.env` 로딩도 차단된다고 잘못 가정했다. 테스트가 Settings
객체를 직접 주입하도록 변경해 파일과 프로세스 환경 모두에서 격리했고 전체 테스트를
재실행했다.

### 재발 방지 조치

- [x] 설정/시크릿 관련 테스트에서 실제 Settings loader 대신 명시적 test double 사용
- [x] 키가 존재하는 로컬 환경에서 전체 테스트 재실행

## INC-2026-003 — 기존 서비스의 5173 응답을 BusyCafe로 잘못 확인

- 상태: Resolved
- 심각도: SEV-3
- 시작/감지/해결: 2026-07-11
- 작성자: Codex

### 요약

5173 포트의 HTTP 200만 확인하고 BusyCafe 개발 서버라고 잘못 안내했다. 실제로는
기존 `iNeed` Vite 서비스가 wildcard 주소에서 5173을 사용 중이었고, BusyCafe도
별도 loopback listener로 같은 포트에 올라가 사용자가 다른 화면을 볼 수 있었다.

### 근본 원인과 조치

서비스 고유 내용이나 리스닝 프로세스를 확인하지 않고 상태 코드만 health evidence로
사용했다. BusyCafe 세션을 종료하고 비어 있는 5188에서 재기동했으며, 프로세스 경로와
`BUSY CAFE · DEVELOPMENT PREVIEW` 응답 문구를 함께 확인했다.

### 재발 방지 조치

- [x] 프로젝트 dev 명령을 5188 + `--strictPort`로 고정
- [x] 로컬 서버 확인 시 PID/command와 서비스 고유 응답을 함께 검증
- [x] PLAN/README의 Kakao 도메인과 향후 CORS 포트를 5188로 갱신

## INC-2026-004 — 지도 확인 요청에 개발 현황 화면을 구현

- 상태: Resolved
- 심각도: SEV-3
- 시작/감지/해결: 2026-07-11
- 작성자: Codex

### 요약

사용자의 “현재 상태 확인” 요청을 실제 서비스 지도 확인이 아니라 개발 진행 상황을
보여달라는 의미로 잘못 해석해, 서비스와 무관한 상태 화면을 만들었다. 사용자는 실제
Kakao 지도 연결을 기대했으므로 요구를 충족하지 못했다.

### 근본 원인과 조치

현재 구현 단계 설명에 집중해 사용자가 확인하려는 제품 경험을 놓쳤다. 상태 화면을
삭제하고 Kakao Maps SDK, viewport CE7 검색, 밀집 영역 분할, 카페 마커와 상세 패널을
구현했다. 혼잡도 데이터가 아직 없는 상태는 가짜 색상 대신 “연결 전”으로 표시한다.

### 재발 방지 조치

- [x] “화면 확인” 요청은 실제 핵심 사용자 플로우를 우선 제공
- [x] 제품과 무관한 개발 메타 UI 제거
- [x] 외부 SDK는 빌드 성공뿐 아니라 등록 도메인별 실제 응답까지 검증

## INC-2026-005 — OSM 타일 POI를 카페 원장처럼 노출

- 상태: Resolved
- 심각도: SEV-2
- 시작/감지/해결: 2026-07-11
- 작성자: Codex

### 요약

OpenFreeMap 벡터 타일의 OSM POI를 즉시 미리보기 카페로 추출했으나, 이름과 위치가
제품 원장으로 사용할 만큼 정확하지 않았다. 사용자가 다수의 잘못된 장소를 발견했다.

### 근본 원인과 조치

베이스맵 렌더링 자료와 검증된 애플리케이션 POI 원장의 역할을 혼동했다. OSM 타일
추출을 제품 경로에서 제거하고 Overture 고신뢰 서울 extract를 PostgreSQL에 적재한
뒤 API로만 제공하도록 변경했다. 공급자 상세 링크는 검증된 place ID가 있을 때만
노출한다.

### 재발 방지 조치

- [x] 베이스맵 타일 POI와 제품 POI 원장을 코드 경계로 분리
- [x] API 응답만 카페 마커 source로 사용
- [ ] Overture 표본 30곳 이름·좌표·영업상태 HUMAN 대조

## INC-2026-006 — HTTP 요청 로그에 경로형 서울 API 키 노출

- 상태: Resolved
- 심각도: SEV-2
- 시작/감지: 2026-07-11
- 작성자: Codex

### 요약

121개 실시간 순회 검증 중 worker의 INFO 로깅이 `httpx` 요청 URL 전체를 출력했다.
서울 API는 인증키를 URL path에 넣으므로 키가 로컬 도구 출력과 이 대화 세션에
노출됐다. `.env`나 Git에는 키가 기록되지 않았다.

### 근본 원인과 조치

애플리케이션 로그의 secret 필드만 점검하고 HTTP 라이브러리의 기본 request log를
위협 모델에 포함하지 않았다. 실행을 즉시 중단하고 worker 시작 시 `httpx`와
`httpcore` 로그를 WARNING으로 고정했으며 회귀 테스트를 추가했다.

### 재발 방지 조치

- [x] HTTP request URL INFO 로그 차단 및 회귀 테스트
- [x] 추가 실 API 호출 중지
- [x] [HUMAN] 서울열린데이터광장 인증키 재발급 후 `.env` 교체
- [x] 새 키로 로그 미노출 단일 호출 확인 후 121곳 순회 재개 (121/121 성공)

## INC-2026-007 — SQLite 미리보기에서 혼잡 근거 시각의 timezone 표기 누락

- 상태: Resolved
- 심각도: SEV-3
- 시작/감지/해결: 2026-07-11
- 작성자: Codex

### 요약

SQLite는 timezone-aware datetime의 offset을 보존하지 않는다. UTC로 정규화해 저장한
`observed_at`이 API에서 timezone 없는 문자열로 나가자 브라우저가 이를 KST로 해석해
실제 약 30분 지연 데이터를 `572분 전`으로 표시했다.

### 대응과 재발 방지

- [x] API 경계에서 timezone 없는 SQLite 시각은 UTC로 복원해 ISO 8601 `Z`로 반환
- [x] 사용자 패널에서 경과 분 숫자 표시 제거; 신선도는 신뢰도 계산에만 반영
- [x] UTC 직렬화 API 회귀 테스트 추가

## INC-2026-008 — Vercel 프로젝트명 오타와 alias의 SSO 302 응답

- 상태: Resolved
- 심각도: SEV-3
- 시작/감지/해결: 2026-07-12
- 작성자: Codex

### 요약

프로덕션 Vercel 프로젝트와 URL이 `busy-cafe`가 아니라 `budy-cafe`로 생성됐다. 프로젝트를
`busy-cafe`로 변경한 뒤 수동으로 연결한 alias는 공개 홈 화면 대신 SSO 로그인으로
리디렉션하는 HTTP 302를 반환했다. 올바른 주소로 접속하려는 사용자가 서비스를 바로
열 수 없는 제한된 배포 장애였다.

### 근본 원인과 조치

배포 전 프로젝트명과 최종 공개 URL을 확인하는 단계가 없었고, 프로젝트 rename 뒤에도
자동 deployment alias가 이전 이름을 유지할 수 있다는 점을 확인하지 않았다. alias 연결과
공개 접근 정책도 하나의 검증 단위로 다루지 않았다. Vercel 프로젝트를 `busy-cafe`로
rename하고 새 production deployment를 만든 뒤 새 이름의 alias를 수동으로 다시 연결했다.
이후 SSO 공개 정책을 복원하고 홈과 `/api/health`가 인증 리디렉션 없이 HTTP 200을
반환하는 것을 확인했다. 당시에는 기존 `budy-cafe.vercel.app` alias를 이전 링크 호환용으로
유지했으나 2026-07-15 정식 URL 검증 뒤 제거했다. 이후 새 deployment에서 오타 alias가
다시 붙어 단순 deployment alias가 아니라 Vercel project domain으로 등록돼 있음을
확인했다. project domain에서 제거한 뒤 정식 URL HTTP 200과 오타 URL HTTP 404를 검증했다.

### 재발 방지 조치

- [x] 프로젝트명, production domain과 문서 URL을 `busy-cafe`로 통일
- [x] rename 후 새 production deploy, alias 재연결과 공개 접근 정책 복원
- [x] 홈과 `/api/health`의 최종 URL HTTP 200 검증
- [x] 배포 체크리스트에 프로젝트명, project domain·자동·수동 alias의 최종 이름, SSO 공개 정책,
  canonical/`og:url`, 홈·health·cache-busting API 검증 추가

## INC-2026-009 — 공유 workspace의 schema 변경 중 worker 실행

- 상태: Resolved
- 심각도: SEV-4
- 시작/감지/해결: 2026-07-12
- 작성자: Codex

### 요약

공유 workspace에서 `cafe_scores.model_version` schema 변경을 구현하는 동안 다른 작업의
one-shot worker가 이전 revision의 `preview.db`를 대상으로 실행됐다. 새 ORM은 아직 DB에 없는
컬럼을 조회해 materialize 단계에서 `no such column: cafe_scores.model_version`으로 실패했다.

### 영향과 조치

worker를 즉시 중단했다. 실패는 snapshot 저장 이후 materialize 단계에서 발생했으며, 저장된
snapshot 2,363건은 보존됐다. local DB와 Vercel snapshot DB를 백업하고 migration을 적용해
각각 4,933개 score를 `v1-idw-point`로 backfill한 뒤 NOT NULL을 확인했다. 이후 외부 호출 없이
materialize를 재실행해 4,933개 score 갱신을 복구했다. worker session 13858과 API session 50211을
재기동한 뒤 local health의 최근 1시간 snapshot 121건과 카페 응답의 model version을 확인했다.

### 재발 방지 조치

- [x] schema-edit agent 작업과 migration 검증이 끝날 때까지 관련 worker 중단
- [x] 기존 score 보유 DB의 backfill·NOT NULL migration 회귀 테스트 추가
- [x] local 및 배포 snapshot DB의 revision·행 수·materialize 성공 확인
- [x] worker·API 재기동 후 health와 카페 model version 확인
- [ ] 공유 workspace에서는 schema-edit agent 완료와 migration gate 통과 전 관련 서비스 실행 금지

## INC-2026-013 — production poll timeout이 수집 cycle을 running으로 남김

- 상태: Monitoring
- 심각도: SEV-3
- 시작/감지/해결: 2026-07-12
- 작성자: Codex
- 관련 GitHub Actions run: `29181020574`, `29181460312`, `29182006480`

### 요약

121곳 production poll의 두 번째 수동 실행이 job의 8분 제한에 도달해 취소됐다. worker가
`KeyboardInterrupt`를 일반 예외와 달리 cycle 종료 경로에서 처리하지 않아, 실제 프로세스가
종료된 뒤에도 최신 `ingest_cycles` 행과 `/api/health`가 `running`을 계속 표시했다. 직전
정상 complete cycle과 snapshot은 보존됐지만, 운영 상태가 실제 실행 상태와 달라졌다.

### 근본 원인과 조치

GitHub job 제한과 worker 실행 제한이 같은 하나의 경계였고, 정상 예외만 durable cycle을
`failed`로 마감했다. 또한 모든 target이 timeout일 때 121곳 각각을 최대 4회 재시도하면
worst-case가 약 85분이어서 10분 schedule 안에 끝날 수 없었다. job 제한을 9분으로 두되
worker 명령에는 8분 SIGINT 제한과 30초 hard-kill fallback을 별도로 적용했다. worker는
`KeyboardInterrupt`와 `SystemExit`도 실패 cycle로 마감한 뒤 다시 전파한다. 5개 target이
연속 실패하면 outage circuit을 열어 남은 target은 호출하지 않고 즉시 cycle을 끝낸다.

후속 실측에서 read-only 1-target canary run `29181444784`는 12초에 성공했지만, serial
121-target run `29181460312`는 snapshot 121건을 처리하고도 8분 worker deadline에 걸렸다.
bounded concurrency와 batch persistence를 적용한 run `29182006480`은 65.536초에 종료됐으나,
첫 5개 target의 GitHub runner→서울 API 요청이 모두 실패해 circuit이 정상 작동했다.
동일 시각 로컬 연결은 성공했으므로 코드·키·서울 API 전체 장애로 단정할 수 없고,
GitHub hosted runner 경로의 간헐적 연결 실패가 현재 가장 강한 가설이다.

또한 `*/10` schedule의 실제 실행 시각이 약 1시간 간격이었고 최대 3시간 46분 누락됐다.
따라서 GitHub Actions cron은 25분 freshness 약속을 보장할 수 없다. poll gate는 끄고
monitor gate만 유지했으며, 운영 scheduler를 별도 상시 Docker worker로 이전할 때까지
인시던트는 Monitoring 상태로 둔다.

### 재발 방지 조치

- [x] interrupt가 cycle을 `failed`와 `completed_at`으로 마감하는 회귀 테스트 추가
- [x] worker deadline과 GitHub job deadline을 분리해 cleanup 여유 확보
- [x] 연속 5개 target 실패 시 bounded circuit breaker 적용 및 회귀 테스트 추가
- [x] DB 접근 없는 GitHub runner 1곳 read-only canary 경로 추가
- [x] 4개 bounded fetch, connection reuse, snapshot batch transaction으로 정상 경로 단축
- [x] poll/monitor gate를 분리해 poll 중단 중에도 stale 경보 유지
- [x] phase별 poll/fetch/persist/materialize/finalize/total duration 기록
- [x] mid-cycle interrupt에서 미커밋 target을 saved로 계산하지 않는 회귀 테스트
- [ ] hard kill·runner 장애처럼 cleanup 불가능한 경우의 오래된 `running` 회수 정책 추가
- [ ] 상시 Docker worker 배포 후 1시간 연속 6 cycle의 `121/121/0` 확인

## INC-2026-014 — 오래된 혼잡 스냅샷을 현재값처럼 표시

- 상태: Monitoring (두 단계 freshness 배포 확인, source 지연과 연속 수집 검증 대기)
- 심각도: SEV-2
- 시작 시각: 2026-07-12T05:10Z 이후
- 감지 시각: 2026-07-13
- 작성자: Codex
- 관련 인시던트: INC-2026-013

### 요약

production 수집이 중단된 상태에서 2026-07-12T05:10Z 전후에 관측된 오후 혼잡 레벨이
7월 13일 새벽에도 현재 혼잡도처럼 카페 마커 색상과 상세 패널에 표시됐다. 사용자는
시간대와 맞지 않는 오래된 값을 현재 추정으로 오인할 수 있었다. 이 사건은 시간대별
추정 정확도의 문제가 아니라, 현재성이 상실된 관측을 제품이 계속 현재값으로 노출한
표시 안전성 결함이다.

### 타임라인

- 2026-07-12 — GitHub hosted runner의 서울 API 요청 실패와 실행 주기 불안정 때문에
  production poll을 `PRODUCTION_POLL_ENABLED=false`로 중단했다. 활성화했던 후속 cycle은
  timeout 또는 실패로 끝났다. 마지막 활성 production cycle은 fetch concurrency 4에서
  첫 5개 대상이 실패해 circuit이 열렸고 나머지 116개는 호출하지 않았다. 같은 경로의
  단일 고정 probe는 성공했다.
- 2026-07-13 새벽 — 전날 오후 스냅샷에서 materialize된 혼잡 레벨이 지도 전역에서
  현재값처럼 보이는 현상을 사용자가 발견했다.
- 2026-07-13 — DB write 없는 로컬 bounded sequential probe로 명동·강남 MICE 관광특구·
  동대문 관광특구·이태원 관광특구·잠실 관광특구 5곳을 확인했고 모두
  `PPLTN_TIME=2026-07-13 09:10` 응답에 성공했다. 이를 근거로 production fetch
  concurrency 기본값을 1로 낮췄다.
- 2026-07-13 — API 응답 시점의 관측 신선도를 판정하고 오래된 현재값을 숨기는
  fail-closed 경로와 회귀 테스트를 추가했다.
- 2026-07-13 — Vercel 배포 뒤 공개 API와 지도에서 stale 현재값이 숨겨지는 것을
  확인했다. production canary run `29215956791`은 concurrency 1로 121개를 모두 저장하고
  실패 0건으로 완료됐다.
- 2026-07-13 — canary가 약 00:45Z에 수집한 121개 응답의 `PPLTN_TIME`은 모두
  00:15Z였다. 약 30분의 source 지연이 현재 25분 제품 freshness 기준을 넘었으므로,
  새로 수집된 값도 의도대로 stale 처리됐다.
- 2026-07-13 — production 실측에서 121개 관측 시각이 00:15Z~00:35Z에 분포하고,
  25분 경계만으로는 정상 수집 직후에도 서비스가 비어 보이는 문제를 확인했다. 사용자
  승인에 따라 25분 운영 경계와 120분 표시 상한을 분리했다.

### 탐지

사용자가 새벽 시간의 지도 색상이 실제 상황과 맞지 않는다고 지적해 발견했다.
`/api/health`에는 수집 지연 정보가 있었지만, 개별 카페·핫스팟 응답이 오래된 현재값을
계속 반환하는지 감시하거나 차단하는 검사가 없었다. 따라서 상단 지연 배너는 표시돼도
마커의 강한 색상은 그대로 남았다.

### 근본 원인

직접 원인은 `/api/cafes`, `/api/cafes/{id}`, `/api/hotspots`가 관측 시각의 나이를
검사하지 않고 저장된 level·score·confidence를 그대로 반환한 것이다. 운영 측에서는
전용 상시 worker 없이 GitHub Actions poll에 의존했고, 실패가 반복된 뒤 poll을 끈 상태라
새 스냅샷이 들어오지 않았다. 프론트의 전역 지연 배너는 운영 상태만 설명했으며 개별
마커의 현재값을 무효화하지 않았다. 즉 수집 실패, API의 freshness 비강제, UI의 시각적
비중립화가 함께 사용자 오해를 허용했다.

마지막 production 실패는 fetch concurrency 4에서 연속 5개 대상 실패로 circuit이 열린
것이지만, 단일 production probe와 로컬 순차 5곳 probe는 성공했다. 따라서 서울 API 전체
장애나 키 오류로 단정하지 않으며, 동시 요청 또는 실행 환경 경로의 영향을 분리 검증해야
한다.

### 대응과 복구

API가 요청 시각을 기준으로 개별 관측의 freshness를 계산하도록 변경했다.
`STALE_WARN_MIN`을 초과했거나 관측 시각이 없거나 허용 범위를 넘는 미래 시각이면
`freshness=stale`로 판정한다. 이때 level·score·confidence·confidence_tier는 `NULL`로
숨기되 coverage, model version, 기준 핫스팟과 거리, 원본 관측 시각은 감사 가능한
근거로 보존한다. 오래된 상세 응답의 1시간 예측은 `NULL`로 만들고, 핫스팟 응답도
오래된 level을 숨긴다. 프론트 패널은 이를 “갱신 지연 · 현재 혼잡도 숨김”과
“오래된 근거 · 현재값 미표시”로 설명한다.

수집 경로는 보수적인 production canary를 위해 `POLL_FETCH_CONCURRENCY=1`로 낮췄다.
run `29215956791`은 총 44.715초, poll 31.815초에 `saved=121, failed=0`으로 완료됐다.
이에 따라 `PRODUCTION_POLL_ENABLED=true`로 다시 활성화했지만, 단일 성공은 운영 연속성의
증거가 아니므로 1시간 연속 6 cycle 검증은 계속 남아 있다.
또한 최근 schedule 실행은 workflow의 `*/10` 선언과 달리 약 1시간 간격으로만 관측됐다.
GitHub scheduled workflow만으로 10분 수집 SLA를 충족한다고 간주하지 않는다.

이 canary는 별도의 source latency 문제도 드러냈다. 수집 시각 약 00:45Z에 모든 장소의
관측 시각이 00:15Z로 같아 약 30분 지연됐고, 현재 `STALE_WARN_MIN=25` 기준에서는 모두
stale가 맞다. 화면에 색상을 보이게 만들 목적으로 임계값을 완화하지 않는다. 실제 제공
지연 분포를 더 측정한 뒤 제품의 freshness 약속과 임계값을 함께 결정해야 한다.

이 조치는 오래된 값의 현재값 오인을 막는 안전장치다. 요일·시간·공휴일 기준선과
현장 관측을 이용한 시간대별 정확도가 검증됐다는 의미는 아니며, 현재 모델의 시간대별
정확도를 주장하지 않는다.

후속 사용성 검토에서는 25분을 fresh 운영 경계로 유지하되 25분 초과 120분 이하를
`delayed`로 분리했다. 이 구간은 level·score만 낮은 시각적 비중으로 표시하고 패널에
“지연 데이터 · 참고용”을 명시하며 confidence·confidence tier·forecast는 숨긴다.
120분을 초과한 경우에만 현재 level·score까지 숨긴다. `/api/health`는 두 임계값을
공개한다. 이 완화는 표시 가용성을 위한 제품 결정이며 정확도 개선으로 해석하지 않는다.

### 잘된 점 / 어려웠던 점

- 잘된 점: 관측 시각과 근거 provenance가 보존돼 원인을 즉시 분리할 수 있었고,
  저장 점수를 재작성하지 않고 요청 경계에서 fail-closed 처리가 가능했다.
- 어려웠던 점: 전역 health 상태와 개별 관측 freshness가 분리돼 있었고, 색상 마커가
  지연 배너보다 강한 현재성 신호를 전달했다.

### 재발 방지 조치

- [x] 카페 목록·상세 응답에 요청 시점 freshness 판정과 stale 현재값 마스킹 추가
- [x] 관측 시각 누락과 과도한 미래 시각도 stale로 처리
- [x] stale 상세 예측과 핫스팟 level을 숨기고 원본 근거 시각은 보존
- [x] stale 경계, 미래 시각, `min_conf` 필터의 회귀 테스트 추가
- [x] 프론트 패널에서 오래된 근거와 현재값 미표시를 명시
- [x] DB write 없는 로컬 순차 5곳 probe 성공 확인 후 fetch concurrency를 1로 축소
- [x] production 배포 후 오래된 마커가 중립화되고 API 현재값이 `NULL`인지 확인
- [x] production concurrency 1 canary에서 `saved=121, failed=0` 확인 — run `29215956791`
- [x] 25분 운영 경계와 120분 참고용 표시 상한을 분리하고 delayed 회귀 테스트 추가
- [x] delayed 지도·패널의 시각적 비중을 낮추고 confidence·forecast 노출 차단
- [ ] production poll의 1시간 연속 6 cycle 성공 확인
- [ ] 실제 10분 주기를 보장하는 전용 worker에서 수집 SLA 확인
- [ ] source 제공 지연 분포를 측정하고 25분 freshness 약속의 현실성을 별도 결정

### 교훈

stale 경고는 설명만으로 충분하지 않다. 현재성을 잃은 관측은 현재값을 생성하는 모든
API와 시각 표현에서 기계적으로 무효화해야 한다. 수집 가용성과 시간대별 모델 정확도는
별도 문제이므로, 이 안전 수정으로 정확도가 개선됐다고 주장하지 않는다.

## INC-2026-015 — Kakao 대량 원장 적용이 commit 뒤 취소됨

- 상태: Resolved
- 심각도: SEV-3
- 시작/감지/해결: 2026-07-14
- 작성자: Codex
- 관련 GitHub Actions run: `29332078493`

### 요약

Kakao 서울 CE7 원장 후보 19,451곳을 production에 적용하는 작업이 DB commit 뒤에도
수분 동안 끝나지 않아 취소됐다. commit은 이미 완료돼 활성 카페 수가 10,466곳에서
29,917곳으로 늘었지만, workflow의 후속 score materialize와 정상 완료 보고는 실행되지
않았다. 다음 materialize 전까지 새 카페는 혼잡 점수가 없어 회색으로 보일 수 있었다.

### 근본 원인과 영향

ORM session의 기본 `expire_on_commit` 때문에 commit 뒤 보고서를 만들 때 방금 삽입한
수천 개 객체의 속성을 다시 읽었다. 이 과정이 개별 DB refresh를 대량으로 발생시켜 실제
쓰기보다 긴 post-commit read 구간을 만들었다. 하나의 단계 안에 원자적 DB commit과 느린
보고서 생성이 함께 있어 GitHub Actions 화면의 `cancelled` 상태만으로 rollback 여부를
판단하기도 어려웠다.

원장 행과 provider identity는 commit돼 유실되지 않았고 이후 score materialize로 회복됐다.
다만 작업 상태와 실제 DB 상태가 달랐고, 후속 단계가 건너뛰어진 운영 불확실성이 있었다.

### 대응과 재발 방지

- [x] commit 전에 보고서에 필요한 scalar 값을 모두 복사해 post-commit ORM refresh 제거
- [x] 대량 삽입은 cafe batch flush와 provider batch flush를 사용하고 행별 원격 refresh 금지
- [x] 회귀 테스트에서 commit 뒤 ORM 객체를 다시 읽지 않아도 보고서가 완성되는지 확인
- [x] 원장 apply 뒤 `materialize_scores.py`를 별도 단계로 유지
- [x] workflow가 dry-run/apply JSON 보고서를 artifact로 보존하도록 변경
- [x] `python | tee`에 `pipefail`을 적용해 Python 실패가 성공으로 가려지지 않게 함
- [x] production health의 활성 카페 수와 후속 viewport 점수 존재를 확인

관련 수정은 commit `6cc9ffd`에 반영했다. 이후 원장 갱신은 candidate 수와 250m 초과 좌표
이동 수에 명시적 상한을 두며, complete Kakao snapshot만 적용한다.

## INC-2026-016 — 큰 좌표 이동 gate가 안전한 Kakao 원장 갱신까지 중단

- 상태: Resolved
- 심각도: SEV-3
- 시작/감지/해결: 2026-07-15
- 작성자: Codex
- 관련 GitHub Actions run: `29384993264`, `29385929508`

### 요약

Kakao CE7 complete snapshot을 production에 적용하는 첫 run에서 기존 identity 24곳의 좌표가
250m를 넘게 이동한 것으로 계산됐다. 운영 상한은 0건이어서 apply가 DB mutation 전에
정상 중단됐고 기존 29,917곳은 그대로 보존됐다. 그러나 안전한 기존 장소 19,778곳의 정보
갱신과 신규 566곳 삽입까지 함께 막혀, 큰 이동 검토와 정상 원장 게시가 불필요하게 결합돼
있음이 드러났다.

### 근본 원인과 대응

큰 이동 gate를 격리 정책이 아니라 전체 transaction의 성공 조건으로 구현했다. 대규모
이동을 자동 적용하지 않는 안전 목표는 맞았지만, 좌표가 정상인 행과 신규 Place ID까지
중단할 이유는 없었다.

발견된 큰 이동 수가 명시적 허용 상한을 넘으면 250m 초과 이동 전부를 결정적으로
격리하도록 변경했다. 임의의 일부는 선택하지 않는다. 격리된 cafe의 이름·좌표·주소·전화,
source release와 provider detail URL·last_seen·verified_at을 모두 동결한다. 정상 refresh와
신규 삽입은 같은 transaction에서 계속하며 report는 발견·계획·적용·격리 수와 사유를
분리한다. 발견 수 전체가 운영자 상한 이내일 때만 큰 이동 배치를 일괄 허용한다.

재실행 run `29385929508`은 큰 이동 24곳을 격리하고 적용 0건을 유지하면서 정상 refresh
19,778곳과 신규 cafe/provider 566곳을 반영했다. 후속 score materialize까지 성공했고 활성
카페는 29,917곳에서 30,483곳으로 증가했다.

### 추가 관측과 재발 방지

apply 단계는 원격 PostgreSQL에서 35분 15초가 걸렸다. 사용자 읽기 경로는 기존 snapshot을
계속 제공했고 장애는 없었지만, cafe/provider ORM update가 대량 원격 왕복을 만들고 내부
진행률 로그도 없었다.

- [x] 상한 초과 큰 이동 전부 격리, 정상 refresh·insert 계속
- [x] 격리된 cafe와 provider 검증 상태 완전 동결 회귀 테스트
- [x] dry-run과 apply의 계획·격리 수 동일성 테스트
- [x] 큰 이동 배치 all-or-none 승인과 정상 refresh+신규 insert 혼합 테스트
- [x] production에서 큰 이동 적용 0, 격리 24, 신규 566, score materialize 확인
- [ ] PostgreSQL bulk update로 cafe/provider 원격 왕복 축소
- [ ] batch별 처리 수와 elapsed time을 secret·raw 장소 정보 없이 로그에 추가
- [ ] 격리 24곳의 provider identity를 원본 Kakao 상세와 수동 대조

## INC-2026-017 — OD 순유입의 유출 시간을 도착시각으로 잘못 정렬한 near miss

- 상태: Resolved before release
- 심각도: SEV-4 (공개 영향 없음)
- 시작/감지/해결: 2026-07-15
- 작성자: Codex
- 영향 범위: offline 수도권 생활이동 shadow 구현 중 테스트 단계만 해당

### 요약

최초 OD 집계 엔진은 목적지 유입과 출발지 유출을 모두 레코드의 도착시각에 묶었다. 이
상태의 `net=inbound-outbound`는 같은 시각의 인구 이동을 비교하지 않아 시간대별 순유입을
왜곡한다. 실파일 artifact를 만들기 전에 코드 검토에서 발견했으며 production API·DB·지도와
사용자에게 노출된 값은 없다.

### 근본 원인과 대응

처음 결과 계약을 `destination × arrival_hour`로만 설계해 유출도 같은 축에 억지로 넣었다.
레코드에 출발·도착 시각이 모두 있다는 원천 의미보다 결과 테이블 모양을 먼저 고정한 것이
원인이다.

관측 계약을 `departure_hour`와 `arrival_hour` 두 축으로 분리했다. 유입·목적·접근 방향은
목적지 도착시각에, 유출은 출발지 출발시각에 집계한다. 결과 키는 유입과 유출의 합집합으로
만들어 유출만 있는 구역·시간도 누락하지 않는다. 구역 내 이동은 도착시 유입과 출발시
유출에 각각 포함하되 방향 벡터에서는 제외한다.

### 재발 방지

- [x] 관측 타입에 `departure_hour`와 `arrival_hour`를 별도 필드로 강제
- [x] 서로 다른 출발·도착 시각 fixture로 순유입 회귀 테스트 추가
- [x] 유출만 존재하는 구역·시간 결과 테스트 추가
- [x] 실 artifact provenance에 유입·유출 시간축을 명시
- [x] 공개 모델 영향이 없는 offline shadow로 격리

### 교훈

OD의 순유입은 단순한 목적지별 합계가 아니다. 유입 사건은 도착시각, 유출 사건은
출발시각에 놓아야 하며, 결과 모양보다 원천 사건의 시간 의미를 먼저 보존해야 한다.

## INC-2026-018 — OD 방향 반복성의 “모든 주차쌍” 조건을 중앙값으로 구현한 near miss

- 상태: Resolved before first real evaluation
- 심각도: SEV-3 (공개·production 영향 없음)
- 시작/감지/해결: 2026-07-15
- 작성자: Codex
- 관련 구현: `6cdd106`

### 요약

다일 OD pilot 사전등록은 각 `pair × hour`가 방향 조건을 모두 만족해야 한다고 고정했다.
첫 구현은 eligible 비율·P90·45° 이내 비율은 모든 pair를 보면서도, pair별 각도차 median과
strength 차이 median만 다시 전체 중앙값으로 판정했다. 불안정한 한 주가 양옆의 안정적인
주에 가려져 `usable`로 잘못 판정될 수 있었다. 첫 실데이터 결과를 읽기 전 코드 검토에서
발견했으며 공개 모델에는 영향이 없다.

### 근본 원인과 대응

“각 pair에서 계산하는 median”과 “pair median들을 다시 합친 median”의 서로 다른 집계층을
변수 이름만으로 구분하지 못했다. 판정은 pair별 통계의 `max`가 상한을 넘지 않아야 하도록
수정했다. report summary에도 `maximum`을 보존하고, 중간 한 pair만 각도 40° 또는 strength
차이 0.15로 흔들리는 fixture가 반드시 `not_usable`이 되는 회귀 테스트를 추가했다.

### 재발 방지

- [x] 사전등록의 `모든` 조건을 pair 통계의 `max`/`min`으로 직접 표현
- [x] 한 pair만 실패하는 parameterized 회귀 테스트
- [x] 판정에 사용한 minimum·maximum을 결정적 report에 함께 보존
- [x] 수정 뒤에만 첫 real-data dry-run/apply 실행

### 교훈

반복성 gate에서는 지표 계산층과 gate 집계층의 quantifier를 분리해야 한다. “각 pair의
median”은 “pair median의 median”이 아니며, 사전등록의 `모든`은 코드에서도 `max` 또는
`min`으로 보이게 구현한다.

## INC-2026-019 — 생활인구 CELL_ID 유일키·숫자 문법 가정 오류

- 상태: Monitoring (evaluator 해결, compactor 재설계 미완)
- 심각도: SEV-3 (공개·production 영향 없음)
- 시작/감지: 2026-07-15
- 작성자: Codex
- 관련 구현: `464ff8c`, `a30a230`

### 요약

OA-22784와 OA-22300 동일 날짜 screen의 첫 실파일 실행은 같은 날짜·시간·`CELL_ID`가
중복됐다는 이유로 상관 계산 전에 멈췄다. 실측 결과 같은 250m `CELL_ID`가 여러 행정동 코드의
서로 다른 값으로 반복된다. 3-key 중복 group은 44,837개였지만 행정동 코드를 포함한 4-key
중복은 0개였다. 두 번째 실행은 `생활인구합계='540.'`에서 멈췄다. 기존 regex는 소수점 뒤
숫자가 반드시 있어야 한다고 가정했지만 실파일에는 trailing decimal 토큰이 2,477개 있었다.

### 근본 원인과 대응

5행 API 표본과 최소 fixture에는 행정동 경계를 걸친 격자와 trailing decimal 표현이 없었다.
따라서 synthetic fixture에서만 맞는 `(date,hour,CELL_ID)` 유일키와 숫자 문법을 전체 파일
계약으로 잘못 일반화했다.

evaluator는 `(date,hour,administrative_dong_code,CELL_ID)`를 행 identity로 바꾸고
zone-cell 부분행 존재 변화와 bare-cell geometry를 분리했다. parser와 compactor validation은
`[0-9]+(?:\.[0-9]*)?`를 허용하되 지수·음수·NaN 등은 계속 거부한다. 두 실패 모두 상관 계산과
artifact 게시 전에 fail-closed로 멈춰 잘못된 결과는 남지 않았다.

기존 compactor는 여전히 date-hour-cell 중복을 거부하고 cell 단위 Parquet에서 행정동 부분행을
어떻게 합칠지 정하지 못했다. 일부 부분행이 `*`일 때 known 합계와 masked fragment 수를 함께
보존해야 하므로 단순 합산이나 전체 cell 마스킹으로 임의 우회하지 않는다.

### 재발 방지

- [x] 전체 253,946행에서 3-key/4-key 중복 수 실측
- [x] 경계 cell을 서로 다른 행정동 행으로 허용하는 evaluator 회귀 테스트
- [x] trailing decimal parser·DuckDB validation 회귀 테스트
- [x] v1 input gate 실패를 결과와 분리해 보존하고 v2 계약을 첫 상관 계산 전에 사전등록
- [ ] compactor v2에 `known_total + masked_fragment_count` 원본 의미를 보존하는 schema 확정
- [ ] activity artifact/baseline consumer가 부분 마스킹을 임의 점대치하지 않는 계약 추가
- [ ] 전체 2026-06 월파일 compaction dry-run 재검증

### 교훈

공간 격자 ID는 행 유일키와 같지 않다. 경계 격자는 행정구역 차원에서 여러 부분행을 가질 수
있고 숫자 직렬화 문법도 실파일 전수 검증 전에는 확정할 수 없다. 표본 fixture는 parser 시작점,
전체 파일 fail-closed 실행이 최종 데이터 계약 gate다.

## INC-2026-020 — hash seed에 따라 same-day report SHA가 달라진 near miss

- 상태: Resolved before commit/promotion
- 심각도: SEV-3 (공개·production 영향 없음)
- 시작/감지/해결: 2026-07-15
- 작성자: Codex
- 관련 구현: `3dd4be6`

### 요약

v2 실데이터의 첫 dry-run과 apply가 같은 `screening` verdict를 냈지만 serialized report SHA가
각각 `9399…`와 `648d…`로 달랐다. report는 즉시 삭제했고 지표를 문서나 공개 모델에
반영하지 않았다.

### 근본 원인과 대응

동별 zone-cell 합집합을 Python `set`으로 만든 뒤 정렬하지 않고 float 합산했다. 프로세스별
hash seed가 순회 순서를 바꾸고 부동소수점 덧셈의 끝자리를 흔들었다. cell ID를 정렬한 뒤
합산하도록 수정했다. 서로 다른 `PYTHONHASHSEED=1/2` subprocess가 동일 report SHA를 내는
회귀 테스트를 추가한 뒤 실데이터 dry-run/apply를 다시 실행해 양쪽 모두
`f65313105d2aa62d8991d2a1d16737d994f60f20ceeba60cba665b0940e716f7`로 일치시켰다.

### 재발 방지

- [x] 결정적 경로의 set 순회 정렬
- [x] 서로 다른 hash seed subprocess SHA 회귀 테스트
- [x] SHA 불일치 artifact 삭제 후 dry-run부터 재실행
- [x] 동일 verdict만으로 결정성을 통과 처리하지 않고 serialized SHA 비교 유지

### 교훈

같은 프로세스에서 두 번 실행하는 테스트는 hash seed 비결정성을 잡지 못한다. 결정적 artifact는
입력 순서뿐 아니라 프로세스 hash seed를 바꾼 실행까지 비교해야 한다.
