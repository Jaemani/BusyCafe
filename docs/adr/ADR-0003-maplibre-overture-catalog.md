# ADR-0003: MapLibre/OpenFreeMap 지도와 Overture 캐시 원장

- 상태: Accepted
- 결정일: 2026-07-11
- 관련 계획: `docs/PLAN.md` v1.4, Phase 2, Phase 4, Phase 5
- 대체 관계: Kakao Maps/Local 기반 지도·CE7 카페 seed 초안을 제품 경로에서 대체한다. Phase 0 Kakao 실응답 fixture와 도메인 활성화 기록은 legacy 증거로 유지한다.

## 배경

사용자는 Kakao Maps 같은 기본 지도 UI가 아닌 자유로운 지도 표현과 서울 전역 이동을
요청했다. 기존 viewport별 Kakao Local CE7 검색은 한 요청당 45건 제한이 있고, 혼잡
지역에서 재귀 분할이 필요했다. 더 중요한 문제는 Kakao Local 결과를 비-Kakao 지도에
표시하거나 장기 저장하는 권한이 제품 정책상 명확하지 않았다는 점이다. 이 경로는
장소명·위치가 부정확해 보일 때도 서버가 검증 가능한 영속 원장을 제공하지 못한다.

## 고려한 선택지

1. Kakao Maps + Kakao Local viewport 검색 — 한국 장소 품질은 좋지만 지도 UX 자유도가
   낮고, 검색 결과를 다른 지도에 영속·표시하는 정책 경계가 불명확하다.
2. MapLibre/OpenFreeMap + Overture Places 서버 원장 — 지도 UI와 POI catalog를 분리하고,
   release·원본 sources·ID를 보존하고 extract hash를 검증 기록에 남긴다.
3. Google/상용 지도 전체 전환 — 즉시 장소 상세·렌더링 선택지는 넓지만 사용량 비용,
   표시·저장 약관, vendor lock-in을 새로 검증해야 한다.

## 결정

지도 렌더러는 MapLibre GL JS, 기본 style은 OpenFreeMap Positron으로 사용한다. 카페
목록의 제품 원장은 Overture Places의 versioned GeoParquet release를 서울 범위로
ingest한 PostgreSQL cache다. 지도 이동·카페 API 요청은 외부 POI provider를 호출하지
않고 cache만 읽는다. 서울 인허가 데이터는 영업상태 보정용 별도 cache로 둔다.

Naver/Kakao/Google의 매장 상세 버튼은 검증된 해당 provider ID 또는 그 ID로부터 온
canonical detail URL이 있을 때만 노출한다. 이름/좌표 검색 URL, 스크레이핑, fuzzy
matching으로 provider ID를 만들지 않는다. ID가 없으면 버튼을 숨긴다.

Kakao Local fixture, 키 활성화 확인과 과거 preview 코드는 API 회귀/인시던트 기록을
위해 보존할 수 있으나, 운영 지도·POI seed·cache 경로에 포함하지 않는다.

## 근거

- MapLibre는 베이스맵과 제품 레이어(카페·혼잡도·근거 패널)를 독립적으로 설계하게
  해 사용자가 요청한 지도 UX를 제공한다.
- Overture release ID와 source record ID를 저장하면 카페 위치·이름 오류를 특정
  release까지 추적하고 재적재할 수 있다.
- cache-first API는 지도 이동 시 외부 API latency·쿼터·정책 변경을 사용자 요청 경로
  에서 제거한다.
- 검색 결과 링크는 동명 매장·이전 매장으로 오매칭될 수 있다. 공급자 ID가 없는 링크를
  숨기는 편이 잘못된 가게 상세로 보내는 것보다 정직하다.

## 결과와 후속 조치

- Phase 2는 Overture release, source, extract hash, 빈 입력 거부와 단일 transaction
  적용을 구현했으며 인허가 보정과 격리 리포트는 계속 진행한다.
- Phase 5는 OpenFreeMap/OSM attribution을 유지하고, 외부 style 장애 시 cached app UI가
  오류를 명확히 보이게 한다. PMTiles/자체 CDN은 실제 트래픽·가용성 요구가 확인될 때
  재검토한다.
- Overture 분류 규칙, 서울 행정경계 필터, 인허가 매칭과 provider direct-link 허용
  형식은 fixture와 표본 검수로 확정해 `VERIFICATION.md`에 남긴다.
- Kakao의 제품 정책 또는 정식 별도 라이선스가 이 사용을 명시적으로 허용하는 증거가
  생기기 전에는 Kakao Local을 운영 POI 원장으로 재도입하지 않는다.
