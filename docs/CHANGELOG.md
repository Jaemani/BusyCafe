# Changelog

사용자가 체감하는 앱 변경과 운영상 중요한 변경을 기록한다. 형식은 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)를 따른다.

## [Unreleased]

### Added

- BusyCafe 자체 코드에 Apache License 2.0을 적용하고, 외부 데이터의 제공자별
  라이선스·출처표시 조건과 명확히 구분
- live health의 complete-cycle freshness를 이용한 프론트 갱신 지연 표시
- Phase 6의 두 관측자 독립 라벨과 quadratic weighted Cohen's kappa 평가
- DB write 없는 GitHub runner 서울 API canary와 독립 poll/monitor 운영 게이트
- 공식 121개 polygon의 포함·겹침·경계거리를 사용하는 offline `v2-polygon-shadow`
- 입력 품질과 정확도 확률을 분리한 Confidence V2 구성요소
- v1/v2 historical paired evaluator, fail-closed promotion gate와 전 카페 구조 비교 도구
- Phase 6 사전 등록 분석 계획(`docs/PHASE6_PREREGISTRATION.md`): divergence 층화
  sign test, v2 과소 추정 guardrail, 스코어 교체와 coverage 확장의 분리 승격 규칙
- 4단계 라벨 대신 인구밀도(명/m²)를 log-space IDW로 보간하는 offline
  `v3-density-shadow` 채점기. v2 polygon 공간 선택과 국소 equirectangular 면적 근사를
  공유하고, ppltn 결측 관측은 제외·집계하며, 보정된 기준선 전에는 1~4 레벨 매핑을
  의도적으로 두지 않는다. 읽기 전용 `run_density_snapshot.py` 구조 리포트 포함
- 생활인구 대량 파일 다운로더(`scripts/download_living_population.py` +
  `clients/seoul_living_population_files.py`): dry-run 기본, `.part` 원자적 게시,
  덮어쓰기 거부. 일별 seq 파생 규칙(YYMMDD)을 실다운로드로 이중 확인하고 월별
  규칙(YYMM)은 포털 페이지에서 확인. 파일 실측에서 cp949 인코딩과
  `생활인구합계`의 `*` 마스킹 확인
- 국가 격자 `CELL_ID` 디코더(`app/ingest/national_grid.py`): 순수 EPSG:5179 역TM
  구현으로 250m 셀을 WGS84 중심·경계로 변환. 실데이터 817셀 표본 검증
  (bbox 817/817, 종로구 정합, 인접 간격 250.56m) — 공식 격자 경계 전수 대조는 `[VERIFY]`
- 생활인구 ↔ citydata 상관 실험 설계
  (`docs/research/2026-07-12-baseline-correlation-design.md`): 프로파일 상관 주 지표와
  판정 기준을 데이터 관측 전에 고정, worker 연속 수집을 선행 조건으로 명시
- 생활인구 bulk CSV 스트리밍 파서: 실측 `cp949` 인코딩, 엄격한 날짜·시간·행정동·
  `CELL_ID` 검증, 총계 `*` 마스킹의 원본 상태를 보존. 대치값은 연구 계산층으로 분리
- 핫스팟 polygon과 250m 생활인구 셀의 교차면적 가중치를 결정적으로 생성하는
  offline shadow 도구. 공식 격자 경계 전수 대조 전에는 provenance에 `unverified`를
  유지하고 공개 점수에 사용하지 않음
- 생활인구 베이스라인 단위를 250m 격자로 확정(ADR-0009, 사용자 승인). 기존
  `SEOUL_API_KEY`로 `Se250MSpopLocalResd` 실호출 검증(XML 정상·JSON 포털 결함),
  원본 5행 fixture와 SHA-256 보존
- `seed_cafes.py` 읽기 전용 `--confidence-report` 모드: 로컬 Overture extract의
  confidence 버킷(0.05 단위)·카테고리 분포와 현재 임계값 통과 비율을 DB 쓰기·네트워크
  없이 확인. cache가 사전 필터된 구간은 0 대신 filtered로 표기
- 카페 원장 recall 리서치(`docs/research/2026-07-12-catalog-recall.md`): 인허가
  데이터를 recall-우선 primary로, Overture를 enrichment로 재구성하는 계획과
  임계값 연구 절차, 조용한 동네 카페 누락 편향 리스크 명시
- 서울 생활인구 데이터 리서치(`docs/research/2026-07-12-living-population.md`):
  집계구 단위 데이터셋의 2026-07-31 서비스 종료와 250m 국가표준 격자 전환을 공식
  공지로 확인, 접근 방식·공공누리 1유형 라이선스·경계 geometry 조사와 미확인 항목 기록,
  250m 격자 목표 단위 권고(`[HUMAN]` 확정 대기)

### Changed

- 공개 Vercel API를 Supabase PostgreSQL read 경로로 승격
- 서울 API client를 4개 bounded connection pool로 재사용하고, fetch 결과는 대상 순서대로
  검증한 뒤 snapshot을 한 transaction으로 저장
- score materialize 조회를 계산에 필요한 좌표·레벨·PK로 제한

### Fixed

- 외부 API가 연속 실패할 때 최악 85분까지 retry할 수 있던 poll을 5-target circuit으로 제한
- GitHub job timeout이 durable ingest cycle을 `running`으로 남기던 interrupt cleanup
- poll 중단 시 미커밋 snapshot을 `saved`로 과대 계상할 수 있던 cycle counter

## [0.1.0-preview.1] — 2026-07-12

### Added

- 프로젝트 계획, 검증, 의사결정, 변경 이력, 인시던트 기록 체계
- Phase 0 API 검증용 백엔드 스캐폴딩과 프론트엔드 Vite/TypeScript 뼈대
- 로컬 PostgreSQL 개발 환경
- 서울시 OA-21285의 121개 장소 목록·영역 원본을 안전하게 받는 다운로드 도구
- PostgreSQL/JSONB 기반 초기 schema와 Alembic migration
- 공식 WGS84 영역 대표점 기반 121개 핫스팟 멱등 seed 및 HUMAN 검수 dry-run
- API 프로세스와 분리된 10분 ingest worker, 재시도·대상 검증·파싱 실패 원본 보존
- Tailnet 전용 HTTPS 개발 미리보기와 제한된 Tailscale 호스트 허용 설정
- Kakao 지도 이동 영역별 CE7 카페 검색, 중복 제거, 마커와 카페 상세 패널
- MapLibre/OpenFreeMap 서울 지도, 클러스터, 내 위치 버튼과 모바일 상세 패널
- Overture Places `2026-06-17.0` 고신뢰 서울 카페 4,933건 서버 cache ingest
- 공식 121개 핫스팟 전체 seed, 결정적 IDW score materialize, cache-only FastAPI bbox API
- 세 개 고도화 트랙 및 유니버설 확장 로드맵 문서화
- Phase 6 지역 혼잡/매장 효용 이중 라벨 evaluator와 거리대별 24곳 현장 후보
- 전체 수집 cycle 상태, production freshness monitor와 백업·복구 runbook
- Production environment secret을 사용하는 fail-closed DB bootstrap dry-run/apply workflow

### Changed

- 운영 기본 저장소를 SQLite에서 PostgreSQL로 변경하고 향후 PostGIS 확장 경로를 문서화
- Kakao JavaScript 키를 프론트엔드에서만 관리하도록 중복 백엔드 설정 제거
- 서울 OpenAPI 호출 횟수 무제한 확인에 따라 MVP 폴링 주기를 10분으로 확정
- 프론트 개발 포트를 충돌 없는 5188로 고정하고 자동 포트 변경을 금지
- 제품 지도/POI 경로를 Kakao에서 MapLibre + Overture cache로 변경
- 폴링 대상을 초기 10곳에서 공식 121곳 전체로 변경
- 외부 지도 검색 링크는 제거하고 검증된 가게 상세 URL이 있을 때만 표시
- Phase 6 순위 평가는 실제 관측 timestamp 대신 동네별 field slot로 묶되, 과거 예측
  재생에는 각 실제 관측 시각을 사용하도록 변경
- 공개 데이터 모드를 프론트 빌드 상수가 아닌 `/api/health.data_mode`의 runtime 상태로 표시

### Fixed

- 서울 인구 API의 실제 평면 응답 구조와 dotted `RESULT` 키를 반영하도록 provisional 스키마 수정
- 일부 hotspot만 갱신돼도 production이 fresh로 보일 수 있던 health 판정을 전체 cycle
  완료 시각 기준으로 교체
- 현장 지역 혼잡 라벨이 원시 보행량·흐름 방해 규칙과 모순돼도 평가에 포함되던 입력
  계약을 fail-closed 검증으로 교체
- Supabase 표준 PostgreSQL URL이 psycopg 2를 찾거나 Vercel bundle에서 driver가 누락될 수
  있던 production 연결 경로를 psycopg 3 자동 정규화와 serverless pooler 호환 설정으로 수정

### Removed

- 서비스 기능과 무관했던 개발 현황 상태 화면
- 제품 런타임의 Kakao SDK/Local 검색과 OSM 타일 POI 추출

### Security

- 경로형 서울 API 키가 HTTP client INFO 로그에 노출되지 않도록 `httpx/httpcore` 로그 차단
- 서울 API 키 교체 후 121개 전체 full-cycle을 secret-safe logging으로 재검증

## 릴리스 템플릿

```md
## [X.Y.Z] — YYYY-MM-DD

### Added

### Changed

### Fixed

### Removed

### Security
```
