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
- Hobby 플랜에서 지원하지 않는 custom event는
  `VITE_ENABLE_CUSTOM_ANALYTICS=false`로 차단하고 관련 피드백 UI도 숨긴다.

Analytics dashboard에서 enable로 표시되는 것만으로 활성이라고 판정하지 않는다.
`/_vercel/insights/script.js`가 HTTP 200 JavaScript를 반환하고 실제 production pageview 한
건이 dashboard에 나타나야 PASS다.

## 이벤트 계약

custom event를 지원하는 플랜으로 전환한 뒤에만 다음 allowlist를 활성화한다.

| 이벤트 | 허용 속성 | 금지 데이터 |
|---|---|---|
| `map_ready` | 없음 | URL, 좌표 |
| `cafe_marker_click` | coverage, colored | cafe ID·이름·좌표 |
| `external_map_click` | provider, direct/search | 목적지 URL, 검색어 |
| `geolocation_click` | 없음 | 권한 전 좌표 |
| `geolocation_result` | success/error | 좌표, 오류 원문 |
| `viewport_load` | bounded count, colored count | bbox, 줌 중심 |
| `cafe_detail_error` | 없음 | cafe ID, stack trace |
| `crowd_feedback` | similar/busier/quieter, coverage:level | 사용자·카페 식별자 |

새 속성은 카디널리티와 재식별 위험을 검토하고 테스트를 추가하기 전에는 보내지 않는다.

## 핵심 지표

North star는 `유용 탐색 사용자`다. 카페 상세을 본 뒤 외부 지도에서 확인하거나, 지원되는
경우 혼잡 피드백을 남긴 사용자를 뜻한다. 초기 공개 베타의 판단 기준은 다음과 같다.

- 지도 준비 성공률 `>= 95%`
- 카페 marker-click visitor rate `>= 25%`
- 외부지도 이동 / marker click `>= 10~15%`
- 상세 API 오류율 `< 1%`
- complete-cycle freshness SLO `>= 99%`
- Phase 6 Spearman `>= 0.5`, adjacent accuracy `>= 0.8`

첫 제품 판단은 최소 1,000 unique visitor 뒤에 한다. Vercel의 단기 익명 식별만으로 D7/D28
retention을 정확히 주장하지 않는다. stable identifier가 필요해지면 개인정보 영향평가와
동의·보존 정책을 먼저 정한다.

사용자 피드백은 self-selection bias가 있으므로 Phase 6 현장 관측을 대체하지 않는다.

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
- 지역 스폰서: Phase 6·freshness 합격 + 월 5천~1만 사용자 또는 월 1천 외부지도 이동
- 디스플레이 광고 재검토: 월 5만 사용자 또는 월 20만 pageview 이후

스폰서 실험의 성과는 별도 슬롯 노출·클릭으로만 측정하며 혼잡 결과의 클릭률과 섞지 않는다.
