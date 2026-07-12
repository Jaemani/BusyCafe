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
- [ ] managed DB 전환 시 `PRODUCTION_ENABLED=true`에서 secret 누락 실패와 정상 cycle 성공을 각각 검증

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
반환하는 것을 확인했다. 기존 `budy-cafe.vercel.app` alias는 이전 링크 호환용으로 유지했다.

### 재발 방지 조치

- [x] 프로젝트명, production domain과 문서 URL을 `busy-cafe`로 통일
- [x] rename 후 새 production deploy, alias 재연결과 공개 접근 정책 복원
- [x] 홈과 `/api/health`의 최종 URL HTTP 200 검증
- [ ] 배포 체크리스트에 프로젝트명, 자동·수동 alias의 최종 이름, SSO 공개 정책,
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
- 관련 GitHub Actions run: `29181020574`

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
연속 실패하면 outage circuit을 열어 남은 target을 실패 처리하고 즉시 cycle을 끝낸다.

### 재발 방지 조치

- [x] interrupt가 cycle을 `failed`와 `completed_at`으로 마감하는 회귀 테스트 추가
- [x] worker deadline과 GitHub job deadline을 분리해 cleanup 여유 확보
- [x] 연속 5개 target 실패 시 bounded circuit breaker 적용 및 회귀 테스트 추가
- [ ] hard kill·runner 장애처럼 cleanup 불가능한 경우의 오래된 `running` 회수 정책 추가
- [ ] 수정 배포 후 수동 poll과 이어지는 scheduled cycle의 `121/121/0` 확인
