# Security Policy

## Reporting a vulnerability

민감한 보안 문제는 공개 GitHub 이슈에 작성하지 말고
[GitHub Private Vulnerability Reporting](https://github.com/Jaemani/BusyCafe/security/advisories/new)을
사용해 주세요. 재현 절차, 영향을 받는 URL·commit과 예상 영향을 포함하되 실제 사용자
데이터, API key, database URL과 access token은 첨부하지 마세요.

장소 누락, 위치 오류, 혼잡도 정확도와 일반 기능 버그는 보안 취약점이 아니므로
[일반 이슈](https://github.com/Jaemani/BusyCafe/issues/new)를 사용합니다.

## Supported version

현재 `busy-cafe.vercel.app` production과 `main` branch의 최신 revision만 지원합니다.
과거 preview deployment는 검증 대상이 아닙니다.

## Security boundaries

- 브라우저는 Supabase에 직접 접속하지 않는다. publishable key를 제품에 넣지 않는다.
- public schema의 애플리케이션 테이블은 RLS를 켜고 `anon`, `authenticated` 권한을
  회수한다. 서버의 PostgreSQL owner/pooler 경로만 사용한다.
- 외부 API key와 DB URL은 Vercel/GitHub/Supabase Vault의 secret으로만 저장한다.
- public API는 읽기 전용이며 GET만 허용한다. 넓은 bbox와 5천 건 초과 viewport를
  fail-closed 처리한다.
- 외부 직접 장소 링크는 허용된 HTTPS provider host와 canonical detail path만 반환한다.
  Naver ID가 없을 때의 주소+이름 검색 fallback은 별도 필드와 `네이버맵 검색` 라벨로만
  제공하며 canonical identity로 취급하지 않는다.
- analytics에는 장소·검색어·정확한 위치와 URL query/fragment를 보내지 않는다.
- 오류 응답과 인증 header가 있는 응답은 public cache 대상으로 만들지 않는다.

현재 남은 중요 hardening gap은 DB 역할 분리다. Vercel read API, ingest worker와 migration이
같은 owner급 연결을 쓰면 애플리케이션 취약점이나 credential 유출이 DB write·DDL 권한으로
확대될 수 있다. 공개 홍보 전 `web_readonly`(필요 SELECT만),
`ingest_writer`(정해진 DML만), `migration_owner` 역할과 connection URL을 분리하고 각 경로의
권한 회귀 테스트를 통과시킨다. 분리 전에는 서버 credential 유출을 production write 권한
유출로 간주해 즉시 교체하고 DB 무결성을 점검한다.

단일 요청의 0.5도 bbox와 5천 건 상한은 대량 scan만 막으며 요청률이나 임의
`data_version` cache-bust를 막지 않는다. 대규모 홍보 전에 canonical query 검증,
Vercel rate limit/WAF, 함수·DB 비용 alert와 읽기 API kill switch를 실제 production에서
검증한다. 이 방어가 없을 때 트래픽 1,000명을 안전하게 수용한다고 주장하지 않는다.

## Known operational risks

- 고카디널 query와 `data_version` 남용은 CDN cache를 우회해 DB 비용을 만들 수 있다.
- serverless cold instance가 동시에 DB에 연결하면 connection fan-out이 생길 수 있다.
- poll과 freshness monitor가 같은 Supabase scheduler·GitHub PAT·Actions 경로를 공유하므로
  해당 경로 전체 장애에서는 둘 다 침묵할 수 있다.
- 사용자 피드백 저장을 도입하면 봇·중복·위치 재식별과 보존 기간 문제가 새로 생긴다.
- OpenFreeMap, 서울 API, Vercel, GitHub Actions와 Supabase 장애는 애플리케이션 밖의
  공급망 위험이다. 오래된 관측은 현재값으로 표시하지 않는다.

운영 대응과 복구 절차는 `docs/OPERATIONS.md`, 사고 기록은 `docs/INCIDENTS.md`가 소유한다.
