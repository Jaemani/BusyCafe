# Changelog

사용자가 체감하는 앱 변경과 운영상 중요한 변경을 기록한다. 형식은 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)를 따른다.

## [Unreleased]

### Added

- 프로젝트 계획, 검증, 의사결정, 변경 이력, 인시던트 기록 체계
- Phase 0 API 검증용 백엔드 스캐폴딩과 프론트엔드 Vite/TypeScript 뼈대
- 로컬 PostgreSQL 개발 환경
- 서울시 OA-21285의 121개 장소 목록·영역 원본을 안전하게 받는 다운로드 도구
- PostgreSQL/JSONB 기반 초기 schema와 Alembic migration
- 공식 WGS84 영역 대표점 기반 121개 핫스팟 멱등 seed 및 HUMAN 검수 dry-run
- API 프로세스와 분리된 10분 ingest worker, 재시도·대상 검증·파싱 실패 원본 보존

### Changed

- 운영 기본 저장소를 SQLite에서 PostgreSQL로 변경하고 향후 PostGIS 확장 경로를 문서화
- Kakao JavaScript 키를 프론트엔드에서만 관리하도록 중복 백엔드 설정 제거
- 서울 OpenAPI 호출 횟수 무제한 확인에 따라 MVP 폴링 주기를 10분으로 확정

### Fixed

- 서울 인구 API의 실제 평면 응답 구조와 dotted `RESULT` 키를 반영하도록 provisional 스키마 수정

### Removed

## 릴리스 템플릿

```md
## [X.Y.Z] — YYYY-MM-DD

### Added

### Changed

### Fixed

### Removed

### Security
```
