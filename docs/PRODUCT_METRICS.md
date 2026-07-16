# BusyCafe 제품 측정 기준

## 목적

이 문서는 공개 베타의 제품 이벤트, 개인정보 경계, 핵심 지표와 수익화 gate의 단일
정본이다. 현장 정확도 평가는 `PHASE6_PREREGISTRATION.md`, 운영 장애 판단은
`OPERATIONS.md`가 소유한다.

## 현재 측정 경로

- Vercel Web Analytics의 익명 pageview만 기본 허용한다.
- URL query와 fragment는 전송 전에 제거한다.
- 카페 ID·이름·주소·전화번호, 검색어, 정확한 좌표와 지도 bbox를 analytics property로
  보내지 않는다.
- 브라우저 위치 권한의 결과는 제품 동작에만 사용한다. analytics에는 성공/오류 같은
  저카디널리티 상태만 허용한다.
- Hobby 플랜에서 지원하지 않는 custom analytics event는
  `VITE_ENABLE_CUSTOM_ANALYTICS=false`로 차단한다. 사용자가 명시적으로 제출하는 장소
  신고와 혼잡도 피드백은 analytics와 분리된 PostgreSQL 검증 대기열에 저장한다.

Analytics dashboard에서 enable로 표시되는 것만으로 활성이라고 판정하지 않는다.
`/_vercel/insights/script.js`가 HTTP 200 JavaScript를 반환하고 실제 production pageview 한
건이 dashboard에 나타나야 PASS다.

## 이벤트 계약

custom event를 지원하는 플랜으로 전환한 뒤에만 다음 allowlist를 활성화한다.

| 이벤트 | 허용 속성 | 금지 데이터 |
|---|---|---|
| `map_ready` | 없음 | URL, 좌표 |
| `cafe_marker_click` | coverage, colored | cafe ID·이름·좌표 |
| `external_map_click` | `provider`: naver/kakao/google, `link_type`: direct/search | 목적지 URL, 검색어 |
| `geolocation_click` | 없음 | 권한 전 좌표 |
| `geolocation_result` | success/error | 좌표, 오류 원문 |
| `viewport_load` | bounded count, colored count | bbox, 줌 중심 |
| `cafe_detail_error` | 없음 | cafe ID, stack trace |
| `crowd_feedback` | `feedback`: similar/busier/quieter, `context`: coverage:level | 사용자·카페 식별자 |
| `cafe_search_result` | `result_bucket`: 0/1-5/6-20, `mode`: text/brand/both | 검색어, 주소, 카페 ID·좌표 |
| `cafe_search_select` | `mode`: text/brand/both | 검색어, 선택 카페 ID·이름·좌표 |
| `brand_filter` | allowlist 브랜드, state: on/off | 자유 입력 브랜드, 카페 목록 |

새 속성은 카디널리티와 재식별 위험을 검토하고 테스트를 추가하기 전에는 보내지 않는다.
`crowd_feedback` analytics event는 aggregate funnel 측정용이며, PostgreSQL에 저장하는
카페 식별자·모델 문맥과 결합하지 않는다.

## 핵심 지표

North star 개념은 `유용 탐색`이다. 카페 상세를 본 뒤 외부 지도에서 확인하거나, 지원되는
경우 혼잡 피드백을 남기는 행동을 뜻한다. 현재 aggregate dashboard로 두 행동의 사용자
합집합을 정확히 계산할 수 없으므로 단일 unique-user 수치로 주장하지 않고 funnel count를
각각 본다. 초기 공개 베타의 판정 기준은 다음과 같다.

- `map_ready / production pageview >= 95%`
- `cafe_marker_click / map_ready >= 25%`
- `external_map_click / cafe_marker_click >= 12%`
- `cafe_detail_error / cafe_marker_click < 1%`
- complete-cycle freshness SLO `>= 99%`
- Phase 6 Spearman `>= 0.5`, adjacent accuracy `>= 0.8`

첫 제품 판단은 최소 14일에 걸쳐 누적된 production pageview 1,000건 뒤에 한다. Vercel의
24시간 단위 익명 visitor hash를 월간 unique user나 D7/D28 retention으로 합산하지 않는다.
stable identifier가 필요해지면 개인정보 영향평가와 동의·보존 정책을 먼저 정한다.

현재 Hobby 설정은 pageview만 수집하므로 marker click, 외부 지도 전환과 오류율 기반의
제품·스폰서 gate는 **측정 불가이며 판정 보류**다. 반복 클릭이나 봇이 event count를
부풀릴 수 있으므로 custom event를 켠 뒤에도 원시 count만으로 gate를 통과시키지 않는다.
분석 제공자가 지원하는 session/visitor 단위 중복 제거와 invalid-traffic 제외를 적용하고,
지원하지 않으면 별도 개인정보 검토 없이 stable identifier를 만들지 말고 해당 KPI를
계속 보류한다.

사용자 피드백은 self-selection bias와 고의·실수 입력 가능성이 있으므로 `unverified`로
저장한다. 주변 거리 혼잡 비교값과 매장 좌석 상태를 별도 열로 유지하며, Phase 6 현장
관측을 대체하거나 공개 점수·추천·장소 원장에 자동 반영하지 않는다. 모델 버전별 제출 수,
`not_entered` 비율과 예측 대비 방향 불일치는 탐색 지표로만 본다. 운영자가 독립 근거로
검증하기 전에는 정확도 개선으로 주장하지 않는다.

지도 탐색 지표와 별도로 `알고 있는 카페를 찾을 수 있는가`를 측정한다. 홍보 전 주요 상권
표본에서 카페명·주소 기준 catalog hit-rate, 외부 지도 direct/search link 성공률과 장소
정정 처리시간을 기록한다. marker click률이 높아도 표본 hit-rate가 낮으면 catalog 품질
실패로 판정하며 검색/deep-link 개선을 우선한다.

검색어 원문은 analytics에 보내지 않는다. 검색 결과 0건 비율은 저카디널리티 bucket으로만
측정하고, 어떤 카페가 누락됐는지는 사용자가 검색 결과의 장소 신고 양식에 카페 이름을
명시적으로 제출한 경우에만 본다. 장소 신고는 `pending` 검증 대기열에 저장하고 Kakao 원장
갱신 또는 운영자 확인 전에는 원장을 바꾸지 않는다. aggregate 0건 비율을 자동 Kakao
keyword 수집 입력으로 사용하지 않는다.

장소 신고 운영 지표는 유형별 pending 수, verified/rejected 비율과 제출부터 검증까지의
처리시간이다. 익명 제출에는 stable user identifier가 없으므로 제출자 수, 사용자별 반복률,
retention을 추정하지 않는다. 원시 장소 신고와 혼잡도 피드백은 최대 12개월 보관한다.

## 공개 베타 gate

- analytics script HTTP 200과 첫 pageview 확인
- 모바일 실기기에서 위치 허용·거절, 지도 이동, 상세, 외부 링크 확인
- API 중단과 stale 상태에서 오정보 대신 오류·지연 상태 표시
- 1시간 이상 연속 complete cycle과 freshness monitor 확인
- warm CDN hit와 cold/cache-bust 부하를 분리해 5xx, p95, DB connection 측정
- 개인정보 안내, 추정치 면책, 문의·장소 정정, 비공개 보안 제보 경로 공개

## 수익화 측정 gate

수익화 정책은 ADR-0013이 소유한다. 여기의 수치는 단계 진입 판단에만 사용한다.

- 후원: pageview 측정과 개인정보 안내가 정상인 상태에서 운영자 URL 확인
- 지역 스폰서: Phase 6·freshness 합격 + 최근 30일 production pageview 10,000건과
  `external_map_click` 1,000건을 모두 충족. custom event를 지원하지 않으면 판정 보류
- 디스플레이 광고 재검토: 최근 30일 production pageview 200,000건 이후

스폰서 실험의 성과는 별도 슬롯 노출·클릭으로만 측정하며 혼잡 결과의 클릭률과 섞지 않는다.
