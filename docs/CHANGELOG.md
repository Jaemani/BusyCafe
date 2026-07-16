# Changelog

사용자가 체감하는 앱 변경과 운영상 중요한 변경을 기록한다. 형식은 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)를 따른다.

## [Unreleased]

### Added

- 카페 상세에서 주변 거리 혼잡 비교값과 매장 좌석 상태를 분리해 제출하는 익명 피드백.
  제출 당시 카페·모델 버전·예측 단계·coverage·관측 시각을 `unverified` 상태로 보존하지만
  공개 점수나 추천에는 자동 반영하지 않는다(`0a0702f`, `72fd31d`)
- 선택 카페의 `카페가 아님`·`이름·위치 오류`·`폐업` 신고와 검색 결과 0건의 누락 카페명
  신고. 모든 신고는 `pending` 검증 대기열에만 저장하고 카페 원장을 자동 수정하지 않는다
  (`0a0702f`, `72fd31d`)
- 익명 제출용 PostgreSQL migration과 Supabase RLS·client role 권한 회수, 2KB body 제한,
  PostgreSQL 전용 write guard, runtime kill switch와 모든 POST 응답의 `no-store` 정책
  (`0a0702f`)

- 평일 서울시간 07:00~10:00·17:00~20:00에 페이지에 진입하면 표시되는 출퇴근시간
  정확도 안내. 현재 방문에서 닫을 수 있고 다음 방문에는 다시 표시하며, 토·일과 버전이
  확인된 2026 비근무일에는 표시하지 않는다. 달력이 없는 연도는 fail-closed로 숨긴다
- 공식 `㎡` 단위가 확인된 OA-16095 `FACILTOTSCP`의 전수 분포를 사용하는
  결정적 venue-capacity offline shadow. 상대 면적 계수로 작은 매장의 구조적
  pressure를 표현하되 좌석 점유율·확률·공개 점수로 사용하지 않는다
- 서울 도로명주소의 시·구·도로명·건물 본번·부번을 정확히 비교하는
  capacity entity-resolution challenger. 50m·이름 또는 전화 정확 일치·전역
  1:1을 모두 요구하고 출처·부정형 주소·다중 후보에서 abstain한다
- 비정상 ingest cycle별 상태·지속시간·저장 수·신호를 개인정보 없이 보고하는
  SLO analyzer schema v2와 주간 168시간 read-only 점검. Capacity 매칭도 단계별
  aggregate만 보고하며 식별자·이름·주소·전화를 출력하지 않는다
- 수도권 생활이동 OA-22300의 엄격한 UTF-8 CSV/ZIP 스트리밍 파서와 offline 이동 shadow.
  유입은 도착시각, 유출은 출발시각으로 정렬하고 목적별 비율·순유입·행정구역 중심점 간
  합성 방향·방향 강도·좌표 coverage를 결정적으로 산출한다. dry-run 기본, 원자 게시,
  원본·경계 SHA와 버전을 보존하며 공개 API·DB·지도에는 아직 반영하지 않음
- OA-22300 화요일 4주의 scalar·목적·방향 반복성을 결과 확인 전 고정한 threshold로
  분리 판정하는 offline evaluator. 어린이날·토요일·일요일은 기술통계에만 쓰고, 입력
  source/artifact SHA와 pair별 지표를 결정적 report로 보존한다. 결과는 scalar
  `conditional`, 목적 `stable`, 방향 `usable`이며 정확도 주장·공개 승격은 금지
- 같은 `2026-06-30`의 OA-22784 생활인구 재고 변화와 OA-22300 순유입을 exact 행정동
  코드로 비교하는 offline screen. 경계 격자의 행정동별 부분행·bare-cell universe·마스킹과
  미게시 행 민감도를 분리하고 결과는 `screening`이지만 정확도 주장·공개 승격은 금지
- 사전등록한 일반 화요일 3일의 OA-22784 재고 변화와 OA-22300 순유입 관계를 held-out
  반복한 offline report. 9개 rho가 모두 양수여서 verdict는 `supported`지만 같은 월 릴리스·
  통신계열 공통 편향 한계가 있어 카페 정확도·ground truth·공개 v1 변경 근거로 쓰지 않음
- 카페명·주소 전역 검색과 스타벅스·투썸·메가MGC·컴포즈·빽다방·이디야·폴바셋
  필터. 검색은 PostgreSQL cache만 읽고 exact→prefix→부분 일치 순으로 정렬하며,
  PostgreSQL `pg_trgm` index와 2~80자·최대 50건 제한을 적용
- 검색 결과 선택 시 `[longitude, latitude]` 순서로 지도를 이동하고 기존 카페 상세 패널을
  여는 모바일 검색 패널. 검색어·카페 ID·좌표를 보내지 않는 저카디널리티 검색 측정 추가
- Kakao identity가 확실한 기존 cafe의 이름·좌표·주소·전화번호를 complete snapshot에서
  갱신하는 dry-run 우선 도구. 좌표 이동 분포와 250m 초과 표본을 보고하고 명시적 운영자
  상한 없이는 적용하지 않음
- 주 1회 Kakao CE7 complete refresh. 자동 실행은 신규 2,000곳, 250m 초과 이동 0건을
  고정 상한으로 사용하고 초과 시 적용 전 실패하며, 원장 적용 뒤 점수를 미리 재계산
- 사용자용 `/about.html`: 주변 혼잡도 산정 경계, 데이터 지연, 카페 원장 구성과
  서울시·Overture·Kakao·OpenFreeMap/OpenStreetMap 이용조건을 한 화면에 정리
- Supabase public table RLS와 `anon`/`authenticated` table·sequence grant 차단. 서버의
  PostgreSQL owner/pooler 경로는 유지하면서 브라우저 Data API 접근을 fail-closed 처리한다
- Supabase `pg_cron`이 5분 poll과 후속 freshness monitor를 GitHub workflow로 dispatch하는
  production scheduler와 exact configuration verifier
- Vercel 익명 pageview와 privacy-safe custom event allowlist, 공개 개인정보·운영 안내,
  취약점 비공개 제보 정책과 기본 보안 header
- 공개 bbox 최대 span 제한으로 고카디널 cache-bust와 광범위 DB scan을 fail-closed 처리
- 제품 지표 단일 정본과 검증 우선 수익화 ADR. 공개 베타에는 광고를 넣지 않고, 후원과
  향후 스폰서가 혼잡 점수·색·순위에 영향을 주지 않도록 고정
- 도시 활동도를 제품 코어, 카페를 첫 활용 레이어로 정의한 ADR-0011. 서울을 우선
  검증 지역으로 유지하고, 공개 모델 승격 전에는 카페 내부 좌석 점유율과 지역 활동도를
  구분한다
- source별 원본 의미와 기준선을 보존하는 결정적 `activity-shadow`. 서로 다른 provider의
  raw 값을 합치지 않고, `signal_mode`와 freshness를 분리하며 stale 관측은 provenance만
  남기고 현재 anomaly는 생성하지 않는다
- 과거 생활인구 Parquet에서 명시적인 기준일·시간의 250m 셀 활동도 GeoJSON을 만드는
  offline artifact builder. 버전이 명시된 달력과 source를 입력으로 받고, 마스킹·결측·
  기준선 부족을 임의 보간하지 않으며, 결정적 직렬화와 dry-run 기본·원자적 게시·기존
  결과 덮어쓰기 거부를 적용한다
- OA-22784 행정동 경계 fragment와 부분 마스킹을 손실 없이 유지하는 compact schema v2.
  exact Decimal 합·canonical fragment JSON·sidecar manifest를 원자 게시하고 consumer가
  schema/query/hash/count를 재검증한다. 부분·전부 마스킹된 현재값은 suppression bound를
  가정한 점·구간으로 대치하지 않고 `baseline_only`/`unsupported`로 abstain하며 공개 v1은
  변경하지 않는다
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
- OA-22784 월별 이력을 범위 단위로 계획·수집하는 안전한 backfill CLI. dry-run 기본,
  순차 다운로드, 원자 manifest, 무덮어쓰기, prior SHA-256 대조 resume 계약을 적용
- 정확한 요일과 공휴일·3일 이상 연휴 유형을 분리하는 offline temporal-baseline shadow.
  일반일은 최근 84일, 희소한 공휴일·연휴는 최대 3년의 `log1p` 생활인구를 서로 다른
  반감기로 계층 수축하며 달력·원천 버전과 SHA-256을 provenance에 강제
- 공식 과거 데이터와 선행 플랫폼·연구를 비교한 Track 1 리포트. citydata 공식 과거치가
  제공되지 않음을 확인하고 42개월 생활인구 기준선 + 자체 citydata 이력 구조를 채택
- DuckDB 기반 생활인구 compactor. 전체 CP949 원본을 strict 검증하고 allowlist 셀만
  결정적 Parquet으로 축약하며 입력·출력 SHA, 행 수, 스키마와 누락 셀을 manifest에 기록
- 국내·해외 확장 feasibility 리포트와 ADR-0010. 전국/전세계 단일 실시간 피드 가정을
  폐기하고 부산 보행자 API와 Melbourne pedestrian sensor를 두 번째 provider 후보로 선정
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

- 프랜차이즈 필터에 더벤티·매머드·텐퍼센트·할리스·탐앤탐스·카페베네·커피빈·
  엔제리너스를 추가했다. 기존 포함 15개 chip은 브랜드별 단일 배경색과 명시적인
  전경색을 사용한다. 후속 조정에서 팔레트 채도를 낮추고 선택·키보드 focus ring을
  chip 내부에 그려 가로 스크롤 영역에서 잘리지 않게 했다(`fefa9eb`, `5b90eb1`)
- 카페 검색을 내 위치 우선, 위치를 쓰지 않으면 지도 중심 기준의 거리순으로 변경했다.
  서버가 거리순 후보를 먼저 고른 뒤 최대 50건을 반환하고 브라우저가 haversine 거리로
  최종 정렬한다. 검색 결과가 있으면 해당 카페만 지도에 남기며, 검색 해제·0건·오류에는
  기존 화면의 카페를 복원한다(`1048c16`).
- 서울 실시간 인구의 5분 관측 구간과 공식 약 15분 처리 설명을 production 관측 지연과
  분리해 기록. `PPLTN_TIME` KST 해석, KT·SKT 50m 격자 추계, 장소별 융합,
  28일·밀집도 기반 혼잡도와 Seq2Seq 예측의 의미를 검증하고, nowcast는 인구·순서형
  지표가 함께 개선되기 전까지 shadow로 유지
- 서울 카페 원장을 Kakao-first recall 정책으로 전환. 서로 다른 유효 Kakao Place ID의
  좌표·전화번호 충돌은 advisory로 남기고 기존 canonical cafe와 강하게 충돌할 때만 신규
  생성을 차단한다. Overture와 서울 인허가는 fallback·보조 검증으로 유지
- 카페 상세의 release timestamp·숫자형 원장 품질·중복 `참고용` 문구를 사용자용 표현으로
  축약. 혼잡도, 관측 지점·거리, 경계 여부와 데이터 나이를 각각 한 번만 표시하고 원본
  metadata와 상세 라이선스는 API와 `/about.html`에 보존
- materialize가 최근 12시간 모든 snapshot의 forecast JSON과 이전 serving-state JSON을
  반복 전송하지 않고 최신 forecast와 필요한 PK만 조회하도록 축소
- 관측 신선도를 `fresh`/`delayed`/`stale`로 분리했다. 운영상 fresh 경계는 25분으로
  유지하고, 25분 초과 120분 이하는 level·score만 낮은 시각적 비중으로 표시하면서
  `지연 데이터 · 참고용`으로 명시한다. 이 구간의 confidence·등급·forecast는 숨기며,
  120분을 초과하면 현재 level·score도 숨긴다. `/api/health`는 두 임계값을 함께 반환한다
- 마지막 production 동시 fetch가 첫 5개 대상 실패 후 circuit-open된 운영 증거와 로컬
  순차 5곳 probe 성공을 근거로 `POLL_FETCH_CONCURRENCY`를 1로 낮춤. production canary
  run `29215956791`은 44.715초에 121개 저장·실패 0건으로 완료돼 poll을 다시 활성화했다.
  다만 1시간 연속 cycle 검증 전에는 운영 연속성이 복구된 것으로 간주하지 않음
- UI의 퍼센트형 `신뢰도` 표현을 정확도 확률로 오인되지 않는 `근거 강도` 등급으로
  변경하고, Overture confidence는 `장소 원장 품질`로 구분
- `activity-shadow`는 여러 셀의 raw 관측값과 raw 기준선을 집계하지 않고 source-local
  anomaly만 결합하도록 강화했다. 결합 입력은 동일 `source_version`을 요구하며,
  만료됐거나 생성 시점보다 앞선 forecast, fresh/stale 혼합, 구조화된 기준선 provenance와
  누수 방지 cutoff가 없는 입력을 fail-closed로 거부한다
- 격자 활동도 artifact의 공식 geometry 대조와 empirical gate 전에는 기존 카페 점수나
  검증 전 셀 결과로 공개 heatmap을 만들지 않는다. 공개 v1 API와 DB schema는 이번 shadow
  작업에서 변경하지 않음
- 공개 Vercel API를 Supabase PostgreSQL read 경로로 승격
- 서울 API client를 4개 bounded connection pool로 재사용하고, fetch 결과는 대상 순서대로
  검증한 뒤 snapshot을 한 transaction으로 저장
- score materialize 조회를 계산에 필요한 좌표·레벨·PK로 제한
- 요일×시간 모델의 7일 상관 실험을 탐색 gate로 한정. feature 비교는 최소 4주,
  공개 승격 후보는 권장 8~12주 연속 snapshot과 Phase 6 관측을 요구

### Fixed

- 인허가에서 파생된 카페가 같은 인허가 원장과 자기매칭돼 capacity 독립
  검증으로 집계되던 문제. 동일 source를 후보 index 구성 전에 제외하고 기존
  4,637건 결과를 폐기했다
- 두 개 이상 인허가가 하나의 카페를 각각 단일 후보로 점유할 수 있던
  reverse identity 충돌. 관련 매칭을 모두 ambiguous로 강등하고 임의로 승자를
  선택하지 않는다
- OA-22784 실파일의 동일 `CELL_ID`가 여러 행정동 부분행을 갖는 계약과 `540.` 같은 trailing
  decimal 숫자 표현을 strict parser에 반영. 동일 날짜·시각·행정동·CELL_ID를 실제 행
  identity로 사용하고 원천 문법 밖 값은 계속 거부
- same-day report가 Python hash seed에 따라 다른 SHA를 만들던 set 순회 float 합산을 정렬
  합산으로 교체하고 서로 다른 `PYTHONHASHSEED` subprocess 회귀 테스트 추가

- Kakao 대량 원장 적용이 DB commit 뒤 ORM 객체를 행별로 다시 읽어 workflow가 취소되고
  후속 score materialize가 건너뛰어질 수 있던 문제. commit 전 보고값 고정, batch flush,
  `pipefail`과 JSON apply report로 재발 방지
- 카카오 좌표를 뒤집거나 서울 주소지만 서울 bbox 밖인 장소를 적재할 수 있는 경로를
  `x=longitude`, `y=latitude`, 서울 주소와 bbox 동시 검증으로 차단
- production 수집 중단 뒤 전날 오후 혼잡값이 다음 날 새벽에도 현재값처럼 보이던 문제.
  API 요청 시점에 개별 관측 freshness를 판정해 120분 초과, 관측 시각 누락 또는 과도한
  미래 시각이면 level·score·confidence를 숨긴다. 25~120분 구간도 confidence와 예측은
  숨기고 지연 상태를 명시한다. coverage, model version, 기준 장소와 원본 관측 시각은
  보존하며 stale 상세 예측과 핫스팟 level은 노출하지 않는다
- 전역 갱신 지연 배너와 별개로 오래된 카페 마커 색상이 현재 혼잡도를 암시하던 문제.
  상세 패널에서 오래된 근거이며 현재값을 표시하지 않는다는 상태를 명시한다. 공개
  Vercel/API에서 stale 현재값 마스킹을 확인했으며, source 지연 때문에 색상이 보이지
  않더라도 freshness 임계값을 임의로 완화하지 않는다
- Overture 지역 seed가 DB 전체 카페를 조회해 다른 지역 카페까지 비활성화할 수 있던
  범위를 명시적 edge-inclusive bbox로 제한. 범위 밖 입력은 DB mutation 전에 전체 거부
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
