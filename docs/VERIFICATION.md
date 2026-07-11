# Verification Log

실측 검증 결과, 계획과 실제의 차이, Phase별 DoD 통과 여부를 누적 기록한다. 인증키와 개인정보는 기록하지 않는다.

## 상태 요약

| Phase | 상태 | 완료일 | 근거 |
|---|---|---|---|
| Phase 0 | 진행 중 (HUMAN 대기) | - | 키 없이 가능한 스캐폴딩/테스트 완료, API 키 발급 및 실측 전 |
| Phase 1 | 대기 | - | Phase 0 미완료 |
| Phase 2 | 대기 | - | Phase 1 미완료 |
| Phase 3 | 대기 | - | Phase 2 미완료 |
| Phase 4 | 대기 | - | Phase 3 미완료 |
| Phase 5 | 대기 | - | Phase 4 미완료 |
| Phase 6 | 대기 | - | Phase 5 미완료 |

## 기록 템플릿

아래 블록을 복사해 Phase 또는 개별 검증 단위로 추가한다.

```md
## YYYY-MM-DD — Phase N / 검증 제목

- 실행 환경:
- 검증자:
- 관련 커밋:
- 입력/fixture:
- 실행 명령:
- 기대 결과:
- 실제 결과:
- 판정: PASS / FAIL / BLOCKED
- 계획과의 차이:
- 후속 조치:
- 관련 결정/인시던트:

### DoD

- [ ] 항목
```

## Phase 0 미확정 항목

다음 내용은 실제 API 응답과 공식 계정 화면/문서를 확인하기 전까지 확정하지 않는다.

- 서울 실시간 도시데이터 엔드포인트 패턴과 호출 단위
- 응답 필드명, 중첩 구조, 혼잡도 라벨 4종, 예측 구조
- 121개 장소의 정확한 명칭·코드·좌표와 마스터 파일 확보 경로
- 인증키 일일 쿼터와 이에 따른 폴링 주기
- 카카오 CE7 응답 구조와 검색·페이지 제한의 실제 동작
- MVP 대상 핫스팟의 정확한 API 표기

## 2026-07-11 — Phase 0 / 기본 저장소 설계 변경

- 실행 환경: 설계 검토(실 API 호출 없음)
- 검증자: Codex
- 관련 커밋: 초기 커밋 전
- 입력/fixture: `docs/PLAN.md`의 관계, 시계열, bbox, IDW 요구사항
- 기대 결과: 사용자 증가와 공간 질의를 감당하면서 데이터 정합성을 유지할 저장소 선택
- 실제 결과: PostgreSQL을 운영·통합테스트 기준으로 채택하고 PostGIS를 선택적으로 활성화하기로 결정
- 판정: PASS (설계 결정), API `[VERIFY]` 항목에는 영향 없음
- 계획과의 차이: 기존 SQLite 운영 가정을 PostgreSQL로 변경. 프로세스 내 스케줄러는 개발 단일 인스턴스에만 허용하고 운영에서는 ingest worker로 분리
- 후속 조치: SQLAlchemy/Alembic 기반 스키마, PostgreSQL CI, 운영 전 백업·복구 확인
- 관련 결정/인시던트: `docs/adr/ADR-0001-primary-database.md`

## 2026-07-11 — Phase 0 / DDL 내부 불일치 정정

- 실행 환경: 계획서 정적 검토(실 API 호출 없음)
- 검증자: Codex
- 관련 커밋: 초기 커밋 전
- 기대 결과: D4와 §4.5의 uncovered NULL 규칙이 DDL과 일치
- 실제 결과: 초기 DDL은 `score`, `level`, `confidence`를 `NOT NULL`로 선언해 규칙과 충돌
- 판정: PASS (문서 정정)
- 계획과의 차이: uncovered인 경우 `score`, `level`, `confidence`, `confidence_tier`를 NULL로 명시. PostgreSQL 채택에 맞춰 시각 컬럼을 timezone-aware `TIMESTAMPTZ`로 변경
- 후속 조치: SQLAlchemy 모델과 API 응답 스키마에서 nullable 규칙을 동일하게 유지

## 2026-07-11 — Phase 0 / 키 없이 가능한 스캐폴딩 검증

- 실행 환경: macOS, Python 3.14.6(프로젝트 기준 3.12+), Node.js 22.23.0
- 검증자: Codex
- 관련 커밋: 초기 커밋 전
- 입력/fixture: 실측 fixture 없음, in-memory HTTP transport만 사용
- 실행 명령: `rtk uv run pytest`, `rtk uv run python -m compileall -q app scripts tests`, `rtk npm run typecheck`, `rtk npm run build`
- 기대 결과: 외부 실호출 없이 검증 도구 안전장치와 양쪽 스캐폴딩이 통과
- 실제 결과: backend 9 tests passed, Python compileall 통과, TypeScript typecheck 및 Vite production build 통과
- 판정: PASS (부분); Phase 0 전체는 `[HUMAN]` 키와 실측이 없어 BLOCKED
- 계획과의 차이: upstream 스키마 성공 테스트는 합성 응답으로 고정하지 않고 실측 fixture 확보 뒤 추가. provisional 모델은 raw 저장 이후에만 실행
- 후속 조치: 키 준비 후 `rtk uv run python scripts/verify_apis.py --service all`, 121개 장소 마스터와 포털 쿼터 수동 확인
- 관련 결정/인시던트: `docs/INCIDENTS.md`의 INC-2026-001

### 추가 환경 확인

- 키 미설정 상태에서 검증 스크립트는 필요한 환경 변수 이름만 출력하고 exit 2로 안전 종료했다.
- 현재 머신에는 Docker CLI가 없어 `compose.yaml` 런타임 검증은 실행하지 못했다. PostgreSQL은 Phase 0 API 실측 도구의 선행 조건이 아니다.
