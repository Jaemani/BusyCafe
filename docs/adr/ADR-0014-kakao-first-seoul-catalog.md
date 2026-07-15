# ADR-0014: 서울 카페 원장은 Kakao-first recall 정책을 사용한다

- 상태: Accepted
- 날짜: 2026-07-15
- 관련 문서: `docs/PLAN.md`, `docs/OPERATIONS.md`, `docs/VERIFICATION.md`
- 대체 관계: ADR-0003의 Overture 단독 원장 결정을 대체한다. MapLibre/OpenFreeMap 지도와
  cache-first 읽기 경로 결정은 유지한다.

## 맥락

초기 Overture 중심 원장은 서울 전역에서 약 1만 곳을 제공했지만, 사용자가 알고 있거나
방문하려는 카페가 검색되지 않는 문제가 반복됐다. 이 제품에서는 드문 중복보다 실제
목적지 누락이 더 큰 실패다. 카페가 없으면 사용자는 혼잡도 추정을 확인할 기회조차 없다.

2026-07-15 Kakao Local CE7 서울 전체 분할 수집은 3,794회 호출로 33,243개 고유 Place
ID를 반환했고 모든 분할 셀이 완료됐다. 서울 주소와 서울 좌표 경계를 함께 통과한 장소는
약 2.6만 곳이었다. 서로 다른 Kakao Place ID가 같은 건물 좌표나 대표 전화번호를 공유하는
사례를 모두 중복으로 차단하면 쇼핑몰 지점과 밀집 상권 카페까지 함께 누락됐다.

## 결정

1. 서울 카페 발견과 존재 확인의 우선 근거는 주기적으로 완전 수집한 Kakao Local CE7
   snapshot으로 한다. Overture Places와 서울 인허가 데이터는 fallback, provenance와
   보조 검증에 사용한다.
2. 사용자 검색과 지도 이동은 PostgreSQL에 미리 계산·저장된 원장과 점수만 읽는다.
   사용자 요청 경로에서 Kakao, Naver 또는 다른 외부 장소 API를 호출하지 않는다.
3. Kakao 응답의 좌표 계약을 `x=longitude`, `y=latitude`로 고정한다. 서울 주소 확인,
   `SEOUL_BBOX`, latitude/longitude 범위 검증을 모두 통과한 행만 원장 후보가 된다.
4. 서로 다른 유효 Kakao Place ID끼리의 동일 좌표·전화번호·근접 이름은 감사용 충돌로
   기록하되 자동 차단하지 않는다. 기존 canonical cafe와 강하게 충돌하는 경우만 신규
   canonical 생성을 차단한다.
5. 활성 Kakao identity와 확실히 연결된 cafe의 이름·좌표·주소·전화번호는 검증된 최신
   snapshot으로 갱신한다. Kakao-origin은 해당 Place ID를 정본으로 사용한다. 다른
   origin의 cafe는 안전한 exact identity가 있을 때만 갱신하며 canonical ID와 원래
   추적 ID는 유지한다.
6. 좌표 이동 거리를 dry-run에서 구간별로 보고한다. 250m 초과 이동은 발견 수 전체가
   명시적 운영자 상한 이내일 때만 일괄 적용한다. 상한을 넘으면 임의의 일부를 고르지
   않고 큰 이동 전부를 격리하되, 정상 이동 갱신과 신규 삽입은 계속한다. 격리된 cafe와
   provider 검증 상태는 동결한다. 한 번의 complete snapshot에서 사라졌다는 이유만으로
   카페를 폐업 처리하지 않는다.
7. 검색은 카페명과 주소를 지원하고, 브랜드 필터는 명시적인 alias allowlist만 사용한다.
   검색 결과의 혼잡도는 매장 좌석 점유율이 아니라 해당 좌표 주변 지역의 추정치다.

## Kakao 정책 조건

2026-07-15에 확인한 Kakao Developers 운영정책은 사용자 경험 개선 목적의 cache를
허용하되 cache를 최신 상태로 유지하지 않는 행위를 금지한다. 동시에 Developers에서 얻은
정보의 복제·출판·검색 디렉터리 입력과 제3자 제공에는 사전 승낙 조항이 있다.

- 공식 근거: <https://developers.kakao.com/terms/ko/site-policies>
- 원본 API 문서: <https://developers.kakao.com/docs/ko/local/common>

따라서 이 결정은 raw 응답을 공개 데이터셋으로 재배포하는 허가가 아니다. 제품 기능에
필요한 최소 장소 필드, Place ID, Kakao direct URL과 provenance만 보관하고 정기 complete
refresh로 최신성을 관리한다. 별도 서면 답변이 이 사용을 허용하지 않는다고 확인되면
Kakao refresh와 Kakao-origin 공개를 중단하고 Overture·공공 원장으로 되돌린다. 상업화
전에 이 사용 방식에 대한 Kakao의 명시적 확인을 받는 작업은 `[HUMAN]`이다.

## 결과와 후속 조치

- 목적지 recall은 크게 증가하지만 중복 장소가 일부 늘 수 있다. 검색 miss와 사용자
  신고를 측정해 누락과 중복을 별도 지표로 관리한다.
- CE7 오분류로 빠지는 카페는 주요 브랜드 keyword sweep과 검색 miss batch 후보로
  보완한다. 두 경로도 실시간 사용자 요청이 아니라 검증된 운영 cache를 거쳐 반영한다.
- complete refresh 주기, 연속 미관측 횟수와 폐업 판정 기준은 실제 변동률을 측정한 뒤
  별도 gate로 확정한다. 그 전에는 자동 비활성화를 하지 않는다.
- Kakao 원장 갱신 뒤 전체 `cafe_scores`를 다시 materialize해 새 좌표가 회색으로 남지
  않게 한다.
