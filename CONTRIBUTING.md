# Contributing

## 작업 순서

1. `docs/PLAN.md`에서 현재 Phase와 DoD를 확인한다.
2. `[VERIFY]`는 실제 응답과 공식 문서로 확인하기 전 코드의 확정값으로 사용하지 않는다.
3. Phase 하위 작업 하나를 구현하고 fixture 기반 테스트를 실행한다.
4. 사용자 영향은 `docs/CHANGELOG.md`, 설계 결정은 ADR, 중요한 실수와 장애는
   `docs/INCIDENTS.md`에 기록한다.
5. `docs/VERIFICATION.md`에 실행 명령, 결과, 남은 블로커를 남긴다.
6. 하나의 Phase 하위 작업 단위로 커밋한다.

## 커밋

Conventional Commits 형식을 사용한다.

- `feat:` 사용자 기능
- `fix:` 버그 수정
- `docs:` 문서만 변경
- `test:` 테스트 추가 또는 수정
- `refactor:` 동작을 바꾸지 않는 구조 개선
- `chore:` 빌드, 도구, 의존성

커밋에 API 키, 토큰, 실제 `.env`, 개인정보가 포함되지 않았는지 확인한다.

## 검증 원칙

- 테스트에서 외부 API를 호출하지 않는다.
- 실호출은 `backend/scripts/verify_apis.py`를 통해 명시적으로 실행한다.
- 검증 스크립트가 만든 원본 fixture에서 비밀값이나 개인정보를 확인한 후 커밋한다.
- 계획과 실측이 충돌하면 `docs/PLAN.md`와 `docs/VERIFICATION.md`를 먼저 갱신한다.
- 운영 데이터베이스 스키마 변경은 Alembic migration으로 남긴다.

## 리뷰 체크

- 혼잡도를 매장 자체 혼잡도로 오해하게 하는 표현이 없는가?
- 근거, 거리, 관측 시각, 신뢰도, coverage가 함께 전달되는가?
- 수치 튜닝 값이 `backend/app/config.py` 밖에 하드코딩되지 않았는가?
- 외부 호출이 `backend/app/clients/`에 격리되어 있는가?
- 스케줄러가 운영에서 중복 실행될 가능성이 없는가?
