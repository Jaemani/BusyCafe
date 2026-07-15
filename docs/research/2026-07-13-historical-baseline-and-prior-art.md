# 과거 데이터 기반 혼잡 베이스라인과 선행 사례 조사

- 작성일: 2026-07-13
- 상태: 조사·설계 제안. 공개 `v1-idw-point`에는 반영하지 않음
- 범위: Track 1(서울 엔진 정확도)만 해당. 국내·해외 확장 Track 2/3은 동결 유지
- 관련 문서: [생활인구 조사](2026-07-12-living-population.md),
  [250m 격자 ADR](../adr/ADR-0009-living-population-250m-grid.md),
  [베이스라인 상관 실험](2026-07-12-baseline-correlation-design.md),
  [Track 1](../tracks/TRACK-1-ACCURACY.md),
  [Phase 6 사전등록](../PHASE6_PREREGISTRATION.md)

## 1. 결론

과거 데이터는 지금 확보할 수 있다. 가장 직접적인 원천은 서울시 250m 격자 생활인구다.
파일 목록 실측 기준 내국인 월별 파일은 2023-01부터 2026-06까지 **42개월** 존재한다.
따라서 요일·시간대·계절의 평상시 공간 패턴은 worker가 수개월 쌓일 때까지 기다리지 않고
만들 수 있다. 반면 서울 실시간 도시데이터(citydata)의 과거 일괄 아카이브나 기간 조회
엔드포인트는 현재 공식 명세에서 확인되지 않았다. citydata의 실시간 이상치와 생활인구의
관계를 학습·검증하는 겹침 이력은 우리가 append-only snapshot으로 직접 모아야 한다.

권고 엔진은 단순한 “과거 평균 + 현재값”이 아니다.

1. 250m 셀마다 요일·시간·공휴일 유형별 **평소 존재인구 베이스라인**을 만든다.
2. citydata의 각 핫스팟이 자기 평소 패턴에서 얼마나 벗어났는지를 **동일 소스 안에서**
   표준화한다.
3. 그 실시간 이상치만 공간적으로 인접한 250m 셀에 전파한다.
4. 지역 보행 혼잡 관측과 카페 좌석 관측으로 서로 다른 두 목표를 따로 평가한다.

Google Maps의 Popular Times도 “장소별 평소 패턴”과 “현재가 평소 대비 얼마나 붐비는지”를
구분한다. 다만 Google은 자사 위치 데이터 규모를 이용하고, BusyCafe는 공개 지역통계와
정직한 불확실성 표시를 이용한다는 차이가 있다. Google Popular Times를 스크레이핑하거나
비공식 API에 의존하지 않는다.

## 2. 과거 데이터 원천 인벤토리

`[VERIFIED]`는 공식 페이지 또는 실제 파일로 확인한 사항, `[VERIFY]`는 도입 전에 실응답·
명세·계약을 추가 확인할 사항, `[HUMAN]`은 계정·계약·법률 판단이 필요한 사항이다.

| 우선순위 | 원천 | 확보 가능 기간 | 공간/시간 단위 | 공개 지연 | 이용조건 | 권장 용도 | 상태 |
|---|---|---|---|---|---|---|---|
| P0 | 서울 250m 격자 내국인 생활인구 OA-22784 | 2023-01~2026-06 월별 42파일 `[VERIFIED 2026-07-12]` | 250m 셀, 1시간 | Sheet/API 약 4일; 파일 최신성은 목록 기준 | 공공누리 1유형, 출처표시 | 요일×시간×계절 베이스라인, 지연 백테스트 | 파일 1건·스키마·`cp949`·마스킹 실측 완료 |
| P0 | 장기/단기체류 외국인 생활인구 OA-22785/OA-22786 | 과거 월별 파일 제공 `[VERIFY: 시작월·누락월 전수목록]` | 250m 셀, 1시간 `[VERIFY: 실제 두 파일]` | 내국인과 유사할 가능성 `[VERIFY]` | 공공누리 유형 개별 메타 확인 `[VERIFY]` | 관광특구·상권의 외국인 수요 ablation | 원본 미확보 |
| P0 | 서울 실시간 도시데이터 | 공식 API는 현재 관측과 12시간 예측 | 121개 공식 핫스팟, 약 5분 | 준실시간 | 서울 열린데이터 이용조건 | 실시간 이상치, 공개 v1의 현재 신호 | 자체 snapshot만 과거 이력으로 사용 |
| P1 | 한국천문연구원 특일 정보 API | 연도별 달력 생성 가능 | 전국, 날짜 | 사전 공표 | 공공데이터포털 이용조건 `[VERIFY]` | 법정·대체공휴일, 명절·연휴 파생 | API 응답 fixture 필요 |
| P1 | 기상청 ASOS 시간자료 | 장기 관측 이력 제공 | 관측소, 1시간 | 관측 후 공개 | 기상자료개방포털 조건 `[VERIFY]` | 강수·기온·적설 ablation, 평가 시 actual/serving 시 forecast 분리 | 서울 관측소·결측 규칙 확정 필요 |
| P2 | 서울교통공사 역별 시간대별 승하차 OA-12252 | 장기 파일/API 제공 `[VERIFY: 시작일·누락]` | 역, 1시간 | 소스별 상이 | 개별 데이터셋 조건 `[VERIFY]` | 역세권 유동량 보조 신호 | 원본 fixture 필요 |
| P2 | 서울시 버스 정류장별 시간대별 승하차 OA-12913 | 장기 파일/API 제공 `[VERIFY: 시작일·누락]` | 정류장, 1시간 | 소스별 상이 | 개별 데이터셋 조건 `[VERIFY]` | 지하철 밖 유입 보조 신호 | 원본 fixture 필요 |
| P3 | 서울 상권분석 길단위인구 OA-15568 | 과거 분기 자료 `[VERIFY]` | 상권/길단위, 시간대별 집계 `[VERIFY]` | 분기 | 개별 데이터셋 조건 `[VERIFY]` | 통신기반 생활인구와 다른 공간 집계 challenger | 정의·중복계보 확인 필요 |
| P3 | 서울 상권분석 추정매출 OA-15572 | 분기별 과거 자료 | 상권/점포군, 분기 | 수개월 | 개별 데이터셋 조건 `[VERIFY]` | 지역별 구조적 수요 prior, POI 품질 감사 | 실시간 feature로는 사용하지 않음 |
| P3 | 수도권 생활이동 OA-22300 | 2023-01-01~2026-06-30 일별 1,277파일 `[VERIFIED 2026-07-15]` | 서울 행정동·비서울 시군구 OD, 혼합 20분/1시간 코드, 목적 7종 | 지연 공개 | 공공누리 제1유형 | “존재”가 아닌 이동 흐름의 보조 가설 | 하루치 shadow 완료, 예측력 검증 전 |
| P3 | 서울시 문화행사 OA-15486 | 행사 일정 이력·예정 `[VERIFY: 보존범위]` | 장소/기간 | 등록 시점 | 개별 데이터셋 조건 `[VERIFY]` | 대형 행사 이상치 설명 | 예상 관객수·취소 정보 품질 확인 필요 |

공식 근거:

- 서울 생활인구 정의와 데이터셋 목록:
  <https://data.seoul.go.kr/dataVisual/seoul/seoulLivingPopulation.do>
- 250m 내국인 OA-22784:
  <https://data.seoul.go.kr/dataList/OA-22784/S/1/datasetView.do>
- 250m 장기·단기체류 외국인 OA-22785/OA-22786:
  <https://data.seoul.go.kr/dataList/OA-22785/S/1/datasetView.do>,
  <https://data.seoul.go.kr/dataList/OA-22786/S/1/datasetView.do>
- 서울 실시간 도시데이터 OA-21285:
  <https://data.seoul.go.kr/dataList/OA-21285/A/1/datasetView.do>
- 생활인구 개편·집계구 서비스 중지 공지:
  <https://data.seoul.go.kr/together/notice/boardView.do?seq=721010a1522630fbf7a78d381a8326ee>
- 공공데이터포털 한국천문연구원 특일 정보:
  <https://www.data.go.kr/data/15012690/openapi.do> `[VERIFY: 최신 서비스 ID와 운영계정 호출]`
- 기상자료개방포털 ASOS 시간자료:
  <https://data.kma.go.kr/data/grnd/selectAsosRltmList.do?pgmNo=36>
- 공공데이터포털 ASOS 시간자료 조회서비스:
  <https://www.data.go.kr/data/15057210/openapi.do>
- 서울 지하철·버스 시간대별 승하차 OA-12252/OA-12913:
  <https://data.seoul.go.kr/dataList/OA-12252/S/1/datasetView.do>,
  <https://data.seoul.go.kr/dataList/OA-12913/S/1/datasetView.do>
- 서울 상권분석 길단위인구·추정매출 OA-15568/OA-15572:
  <https://data.seoul.go.kr/dataList/OA-15568/S/1/datasetView.do>,
  <https://data.seoul.go.kr/dataList/OA-15572/S/1/datasetView.do>
- 수도권 생활이동 OA-22300, 서울시 문화행사 OA-15486:
  <https://data.seoul.go.kr/dataList/OA-22300/F/1/datasetView.do>,
  <https://data.seoul.go.kr/dataList/OA-15486/S/1/datasetView.do>
- 서울 열린데이터 이용조건:
  <https://data.seoul.go.kr/etc/openInfo.do>

### 2.1 citydata 과거 이력의 한계

현재 검증한 citydata 응답에는 현재 `PPLTN_TIME` 관측과 미래 예측이 있고, 임의의 과거
기간을 요청하는 인자는 없다. 더 직접적으로 OA-21285 페이지의 공식 FAQ는 분단위 대용량
실시간 데이터를 API-to-API로 받아 **과거 데이터를 별도 적재할 수 없어 제공이
불가능하다**고 명시한다. 근거는 OA-21285 페이지 FAQ의 “실시간 데이터 서비스의 경우 과거
데이터를 제공받을 수 있나요?” 항목이다.
<https://data.seoul.go.kr/dataList/OA-21285/A/1/datasetView.do>

따라서 공식 과거 citydata를 제품 일정의 입력으로 가정하지 않는다. FAQ가 안내한 원천부서
별도 제공 가능성은 문의할 수 있지만 결과는 `[VERIFY][HUMAN]`이며, 자체 append-only
snapshot 수집을 대체하지 않는다.

이 제약은 두 종류의 역사를 구분하게 한다.

- 42개월 생활인구: 장기적인 평소 공간·시간 패턴을 지금 계산할 수 있다.
- 자체 citydata snapshot: 실시간 이상치 전파 계수와 신선도 성능을 앞으로 검증한다.

### 2.2 수도권 생활이동 OA-22300 하루치 실험

2026-07-15에 목적별 수도권 생활이동 하루치(`2026-06-30`)를 실제로 내려받아 shadow
집계를 실행했다. 이 실험은 데이터 계약과 계산 가능성을 확인한 것이며 예측 정확도
검증은 아니다.

- ZIP 75,286,481 bytes, 내부 UTF-8 CSV 445,664,262 bytes, 6,414,571행
- 필드는 출발·도착 행정코드, 출발·도착 시각, 내/외국인 구분, 국적, 목적, 평균 거리·
  시간, 이동인구 추정치, 기준일의 11개다. `cnt`는 개인 수의 정수가 아닌 추정치다.
- 목적 코드는 출근·등교·귀가·쇼핑·관광·병원·기타의 1~7이다.
- 시간 코드는 평시의 두 자리 시간과 출퇴근대의 20분 코드가 섞여 있다. 현 shadow는
  생활인구의 시간 해상도와 맞추기 위해 시간 단위로 내림 집계하되 원본 36개 bin 수를
  provenance에 보존한다.
- 하루에 등장한 657개 코드는 서울 행정동 427개와 비서울 시군구 230개였다. OD 기준일
  직전의 `admdongkor ver20260401` 경계를 EPSG:5179에서 면적 중심점으로 계산해 657개를
  모두 매칭했다. 이 경계는 SGIS 공공누리 제1유형 원자료를 가공한 CC BY 4.0 자료다.
- 08·14·18시가 출발 또는 도착인 2,392,689행을 선택한 dry-run과 apply가 같은 artifact
  SHA-256을 냈다. 원시 파일 전수 6,414,571행을 엄격 파싱했고 좌표 코드 coverage는
  657/657이었다.

실측은 두 가지를 동시에 보여줬다. 08시 서울 행정동 합계는 순유입 약 42.9만으로 업무
지역 유입이 뚜렷했고, 18시는 약 4.5만 순유출이었다. 반면 개별 동의 합성 방향 강도는
대체로 낮았다. 여러 방향의 흐름이 서로 상쇄되기 때문이다. 따라서 지도에 큰 단일 화살표를
그리면 오해를 만들 수 있으며, 방향은 `direction_strength`와 좌표 coverage를 함께 가진
shadow 근거로만 유지한다. 또한 14시 서교동의 유입 목적은 `기타`가 약 77%여서 목적 코드만
카페 수요로 직접 번역할 수 없다.

구현 중 최초 순유입 계산이 유입과 유출 모두를 도착시각에 맞추는 오류를 발견했다. 공개
전에 유입은 도착시각, 유출은 출발시각으로 정렬하고 유출만 존재하는 구역·시간도 결과에
포함하도록 수정했다. 회귀 테스트가 이 의미를 고정한다.

결론은 OD를 채택했다는 뜻이 아니다. OA-22300은 실제 궤적·고속도로 통과·골목 보행 방향이
아니라 행정구역 중심점 사이의 집계 흐름이다. 다음 비교에서 생활인구 단독보다 citydata
및 현장 보행 라벨을 개선할 때만 시간대별 지역 활동 prior 또는 challenger로 채택한다.

### 2.3 다일 반복성 pilot 사전등록

2026-06-30 하루의 그럴듯한 출퇴근 모양은 채택 근거가 아니다. 결과를 보기 전에 다음
소규모 pilot을 고정한다. 목적은 “OD가 같은 요일의 반복 가능한 시간 prior인가?”를 보는
것이며 실제 혼잡·카페 좌석 정확도 검증이 아니다.

- 일반 화요일: 2026-06-09, 06-16, 06-23, 06-30. 인접한 7일 pair 3개를 미래 방향으로
  비교한다.
- 보조 기술통계: 2026-06-27 토요일, 06-28 일요일, 05-05 어린이날. 각 유형이 한 날짜뿐인
  이번 pilot에서는 주말·공휴일 분리 여부를 판정하지 않는다.
- 시각 08·14·18시, `ver20260401` 서울 행정동 427개를 고정한다.
- primary는 동별 `net` 평균 tie-rank Spearman이다. 보조로 inbound/outbound Spearman,
  상·하위 10%(43개) Jaccard를 본다.
- scalar 반복성 지지: 9개 pair×hour의 net Spearman median ≥0.80, minimum ≥0.60,
  inbound/outbound median 각각 ≥0.85, 상·하위 Jaccard median 각각 ≥0.50. net median ≥0.60과
  모든 pair×hour ≥0.50이면 조건부, 그 외는 미지지다.
- 서울 전체 목적 7종 분포의 Jensen–Shannon distance는 median ≤0.05, P90 ≤0.10일 때만
  안정적이라고 본다. `기타`가 50%를 넘는 경우 안정적이어도 쇼핑·관광 수요로 번역하지
  않는다.
- 방향은 양쪽 날짜 모두 coverage ≥0.95, strength ≥0.20인 동만 평가한다. 각 pair×hour의
  eligible 비율 ≥20%, 각도차 median ≤30°, P90 ≤75°, 45° 이내 비율 ≥70%, strength 차이
  median ≤0.10을 모두 만족해야 사용 가능으로 본다. 미달이면 방향은 폐기하고 scalar만
  유지한다.
- threshold 변경은 현 결과와 분리한 post-hoc 기록으로만 허용한다. pilot을 통과해도 공개
  v1은 바꾸지 않으며, 이후 rolling-origin에서 생활인구 단독 대비 citydata/현장 라벨 개선을
  별도로 증명해야 한다.

citydata 자체 이력은 2026-07-11 이후이고 OA-22300 최신 공개일은 2026-06-30이라 현재
겹치는 날짜가 0일이다. 비슷한 요일의 서로 다른 날짜를 직접 상관시키는 대체 실험은
계절·행사 차이를 섞으므로 금지한다. 7월 OA 파일이 공개되면 production snapshot과 정확한
동일 날짜·시간으로 비교한다.

### 2.4 다일 반복성 pilot 결과

사전등록 뒤 원본 7일을 모두 전수 파싱했다. 화요일 4일은 판정에, 어린이날·토요일·일요일은
기술통계에만 사용했다. 모든 날짜에서 관측 코드 657/657이 경계 원장과 exact match였고,
centroid 코드·행·추정인구 coverage는 1.0, 누락 출발·도착 코드는 0이었다. 실행기와 고정
threshold는 commit `6cdd106`에 있다. 결정적 전체 결과는
[`artifacts/purpose-od-stability-20260609-20260630.json`](artifacts/purpose-od-stability-20260609-20260630.json)이며
SHA-256은 `39cbe77ce4f6eed592bbaa69f18515c9f342cad5b2cce4860ff0b32b2e7cc32c`다.

결과는 한 덩어리로 “통과”하지 않았다.

- scalar는 `conditional`이다. 순유입 순위 Spearman median은 0.96955였지만 minimum
  0.59424로 사전등록한 full-support 하한 0.60을 넘지 못했다. 유입·유출 순위 median은
  각각 0.99465·0.99512, 상·하위 10% Jaccard median은 0.62264·0.82979였다.
- 약점은 세 번 모두 14시였다. 화요일 인접 pair의 14시 순유입 Spearman은
  0.69302→0.65048→0.59424, 상위 10% Jaccard는 0.43333→0.34375→0.30303이었다.
  유입과 유출 각각의 순위는 매우 안정적이어도 두 큰 값의 차인 `net`은 한낮에 불안정할
  수 있다. threshold를 결과 뒤 완화하지 않으며, `net` 단독 prior 채택은 보류한다.
- 같은 요일·시각의 서울 전체 목적 구성은 `stable`이었다. Jensen–Shannon distance
  median 0.00717, P90 0.01899로 고정 상한 0.05·0.10 이하였다. 다만 목적 7 `기타` 비율은
  시각에 따라 0.20578~0.60370이었으므로 목적 안정성을 쇼핑·관광 또는 카페 수요로
  번역하지 않는다.
- 방향 반복성은 사전등록한 모든 pair×hour 조건에서 `usable`이었다. eligible 비율 minimum
  0.34895, pair별 각도차 median의 maximum 6.11°, P90의 maximum 14.70°, 45° 이내 비율
  minimum 1.0, strength 차이 median의 maximum 0.03118이었다. 이는 행정동 사이 합성
  유입방향이 반복됐다는 뜻이지 도로·골목의 실제 진행방향이 맞다는 증거가 아니다.

최종 decision은 scalar prior 후보 `false`, 목적 feature 후보 `true`, 방향 feature 후보
`true`, 정확도 주장과 공개 승격은 모두 `false`다. 후보 `true`도 다음 challenger에 넣어볼
자격일 뿐이다. OA-22784 생활인구와 OA-22300이 같은 `2026-06-30`을 가진 비교로 파이프라인
관계를 먼저 확인하고, 7월 OA-22300 공개 뒤 동일 날짜 citydata 및 Phase 6 현장 라벨에서
생활인구 단독 모델보다 개선될 때만 엔진에 반영한다.

### 2.5 OA-22784 ↔ OA-22300 동일 날짜 관계 실험 사전등록

2026-07-15 공식 OA-22784 파일 목록을 다시 실측했다. 일별 파일은 `2026-07-01`부터이고
`2026-06-30` 일별 seq는 존재하지 않는다. 같은 날짜 비교에는 공식 월파일
`250_LOCAL_RESD_202606.zip`의 내부 `250_LOCAL_RESD_20260630.csv`를 사용한다. 잘못된 일별
요청이 attachment가 아닌 응답으로 거부된 뒤 월파일 448,638,322 bytes를 받았으며,
SHA-256은 `953e9790e174220eee0d028f1ae393ccd3e5fd88579db32b5b4a60cf2ba13d62`다.

결과를 계산하기 전에 다음 계약을 고정한다.

- 날짜 `2026-06-30`, 시각 08·14·18시, OA-22300 서울 행정동 427개를 고정한다. 두 원천의
  8자리 행정동 코드 exact intersection만 비교하며 이름·좌표로 추정 매칭하지 않는다.
  모든 비교 시각의 code coverage가 95% 미만이면 실험을 무효 처리한다.
- 첫 결과 실행 전 입력 무결성 gate를 보강했다. OD 세 시각은 같은 427개 코드 집합이어야
  한다. 첫 dry-run은 `CELL_ID` 단독을 유일키로 본 가정 때문에 상관 계산 전에 중단됐다.
  실파일 전수 확인 결과 `(날짜, 시각, CELL_ID)` 중복 group은 44,837개지만
  `(날짜, 시각, 행정동코드, CELL_ID)` 중복은 0개였다. 한 격자가 행정동 경계를 걸쳐 여러
  값으로 나뉘므로 후자를 원천 행 identity로 확정했다. 각 `h`의 `h-1↔h`, `h↔h+1`
  `(행정동코드, CELL_ID)` universe Jaccard가 0.99 미만이면 재고 변화 비교를 중단한다.
- 두 번째 dry-run도 상관 계산 전에 `생활인구합계='540.'`에서 strict parser가 멈췄다.
  전체 253,946행에는 소수점 뒤 숫자가 없는 유효 토큰이 2,477개 있었고, `*` 또는
  `[0-9]+(?:\.[0-9]*)?` 밖의 토큰은 0개였다. Decimal 의미를 바꾸지 않고 이 실측 문법만
  parser·compactor에 허용하고 회귀 테스트를 추가했다.
- 생활인구는 250m 셀을 행정동·시각별로 합산한다. `생활인구합계='*'`의 primary 대치는
  사전 확정값 2.0이며 0.0·3.0을 민감도 경계로 함께 계산한다. 마스킹 대치마다 각 시각의
  전체량·마스킹 행/비율을 보존한다.
- primary는 OD의 시간 `h` 순유입 `net(h)=arrival(h)-departure(h)`과 생활인구 재고 변화
  `LP(h+1)-LP(h)`의 동별 average-tie Spearman이다. OD 사건이 일어난 시간 뒤 재고로
  정렬한다. 08·14·18시 세 값을 결과 전에 고정한다.
- secondary는 `LP(h)-LP(h-1)` 정렬과 `LP(h)` 대 OD `inbound+outbound(h)`의 Spearman이다.
  시간 의미 민감도와 규모 sanity check일 뿐 primary를 대체하지 않는다. 결과가 더 좋은
  정렬을 골라 primary로 바꾸지 않는다.
- screening 지지: primary 2.0 대치의 세 rho가 모두 양수이고 median ≥0.30. 조건부 지지는
  median ≥0.20이고 3개 중 2개 이상 양수. 그 외는 미지지다. 각 시간의 0/2/3 대치 rho
  범위가 0.02를 넘거나 대치별 verdict가 달라지면 `imputation_sensitive`로 강등한다.
- 공간 인접 동끼리 독립이 아니므로 iid p-value를 만들지 않는다. 하루 하나의 횡단면은
  인과·예측 정확도·독립 ground truth가 아니다. OA-22784와 OA-22300 모두 통신계열 추정치라
  공통 편향도 있다.
- 결과와 source SHA, 코드 coverage, 모든 primary/secondary 지표를 결정적 JSON으로 남긴다.
  통과해도 공개 v1과 confidence를 바꾸지 않는다. 같은 검사를 여러 날짜 rolling-origin으로
  반복하고, 마지막에 독립 현장 라벨에서 baseline 대비 개선해야 feature 승격이 가능하다.

### 2.6 동일 날짜 관계 실험 v1 무효와 v2 입력계약 사전등록

commit `a30a230` 뒤 첫 상관 실행은 상관을 계산하기 전에 08시 인접
`(행정동코드, CELL_ID)` universe Jaccard 0.985120719가 고정 gate 0.99에 미달해 중단됐다.
threshold를 완화하거나 실패 뒤 상관만 꺼내지 않았다. v1은 입력계약 무효로 보존한다.

추가 입력 진단에서 07→08시 원천 행은 10,608→10,605개였고, 행정동-cell pair는 이탈
81개·진입 78개였다. 반면 bare `CELL_ID`는 8,536→8,535개, 이탈 6개·진입 5개,
Jaccard 0.998712였다. 예를 들어 `다사43754600`은 07시에 두 행정동 부분행이 있다가 08시에는
한 부분행만 남았다. 즉 250m geometry가 크게 바뀐 게 아니라 경계 격자의 행정동별 부분행
존재 여부가 인구 변화와 함께 바뀐다. zone-cell Jaccard를 geometry 완전성 gate로 쓴 것이
잘못이었다.

상관값을 한 번도 계산하지 않은 상태에서 v2 입력계약을 다음처럼 고정한다.

- geometry gate는 bare `CELL_ID`의 `h-1↔h`, `h↔h+1` Jaccard ≥0.99로 바꾼다.
  `(행정동코드, CELL_ID)` Jaccard와 이탈·진입 수는 진단값으로만 보존한다.
- 동별 재고 변화는 두 시각의 zone-cell pair 합집합에서 계산한다. 한 시각에 행이 없으면
  primary는 0.0으로 두고, masked row와 동일하게 2.0·3.0 민감도도 함께 계산한다. 따라서
  “누락은 무조건 0” 가정에 결과가 의존하는지 별도로 드러난다.
- 기존 날짜·시각·행정동 code coverage·Spearman·screening threshold는 바꾸지 않는다.
  report version만 `v2`로 올린다. primary는 `masked=2, absent=0`이고, 한 요인씩만 바꾸는
  `masked=0/3, absent=0`과 `masked=2, absent=2/3`의 총 5개 variant를 고정한다. variant별
  verdict가 달라지거나 시간별 rho range가 0.02를 넘으면 기존 규칙대로 강등한다.
- 이 변경은 source row 단위 실측에 따른 representation 수정이다. 결과 threshold 조정이
  아니며 v2도 정확도·인과·독립 검증으로 해석하지 않는다.

#### v2 결과

입력계약 수정과 서로 다른 `PYTHONHASHSEED` 회귀 테스트를 고정한 뒤 v2를 실행했다.
결정적 report는
[`artifacts/living-od-same-day-20260630.json`](artifacts/living-od-same-day-20260630.json)이며
SHA-256은 `f65313105d2aa62d8991d2a1d16737d994f60f20ceeba60cba665b0940e716f7`다.

- 세 시각 모두 427/427 행정동 code coverage 1.0이었다. bare-cell Jaccard minimum은
  08시 0.99871, 14시 0.99860, 18시 0.99778로 v2 gate를 통과했다. zone-cell Jaccard는
  0.98512~0.98901이었고 진단값으로 보존했다.
- primary `OD net(h)` 대 `LP(h+1)-LP(h)` Spearman은 08시 0.92870, 14시 0.59438,
  18시 0.90204였다. 모두 양수이고 median 0.90204로 고정 screening 기준을 통과했다.
- secondary 이전 정렬 `LP(h)-LP(h-1)`은 0.91954, 0.23811, 0.83956이었다. 특히 14시에
  primary보다 낮았다. 결과가 더 좋은 정렬을 고른 것이 아니라 사건시간 뒤 재고를 primary로
  미리 고정한 결과다.
- 규모 sanity check인 gross flow 대 동별 stock은 0.89603, 0.95610, 0.93839였다. 동 크기와
  중심성이라는 공통 요인이 큰 상관을 만들 수 있으므로 독립적인 정확도 증거로 쓰지 않는다.
- `masked=2, absent=0` primary와 네 sensitivity variant의 verdict는 모두 `screening`이었다.
  시간별 rho range maximum은 0.000503으로 0.02 상한보다 작아
  `imputation_sensitive=false`였다.

판정은 **screening relationship supported, promotion denied**다. 같은 날짜 두 통신계열
추정치에서 시간대별 이동의 순유입과 다음 시간 생활인구 재고 변화가 구조적으로 연결됐다.
그러나 하루치·공통 원천·행정동 횡단면이므로 실제 보행 혼잡이나 카페 좌석 정확도를 검증한
것은 아니다. decision의 `historical_feature_candidate`, `accuracy_claim_allowed`,
`public_promotion_allowed`는 모두 `false`를 유지한다. 다음 단계는 06-09/16/23 화요일을
held-out 반복으로 사용하고 06-27/28 주말은 기술통계로만 보는 다일 동일날짜 검증이다.

## 3. 요일·공휴일·시간 기준선

단순 월평균이나 “평일/주말” 두 그룹만으로는 부족하다. 월요일 출근시간, 금요일 저녁,
토요일 오후, 대체공휴일은 발생 과정이 다르다. 1차 후보는 다음 계층이다.

1. `hour_of_week`: 월요일 00시부터 일요일 23시까지 168개 슬롯
2. `day_type`: 일반 평일, 토요일, 일요일, 공식 공휴일, 3일 이상 연휴
3. `holiday_context`: 연휴 전날, 연휴 첫날/중간일/마지막 날, 징검다리 평일
4. `season`: 월 또는 계절. 장기 추세가 크면 최근 8~12주에 더 높은 가중치

공휴일을 처음부터 “어린이날”, “추석 둘째 날”처럼 각각 독립 그룹으로 만들면 표본이 너무
작다. 현재 구현된 shadow는 `log1p(인구)` 공간에서 최근 관측에 지수 가중한 평균·분산을
구하고 세부 버킷을 상위 버킷에 부분 수축한다. fallback 순서는
`ISO 요일+day_type → day_type → 공휴일의 명목 요일 → 같은 시각 전체`다. 대체공휴일은
별도 임의 타입이 아니라 공식 `public_holidays` 집합에 포함해야 한다. 중앙값·절사평균·
사분위 범위는 이 기준 모델과 비교할 challenger이지 현재 구현이라고 표현하지 않는다.
일반일 provisional 기본은 84일 창·28일 반감기이고, 희소한 공휴일·연휴는 과거 반복을
빌릴 수 있도록 1,095일 창·365일 반감기를 별도로 쓴다. 이 값들은 calibration 전에는
공개 모델 파라미터가 아니며 모든 실행에서 실제 적용값을 provenance에 남긴다.
마스킹된 총계는 이미 사전 확정한 기본값 2.0과 0/3 민감도 분석을 그대로 쓴다.

내국인만 합산한 모델을 기준으로 다음 네 가지를 같은 검증셋에서 비교한다.

- L: 내국인
- LL: 내국인 + 장기체류 외국인
- LS: 내국인 + 단기체류 외국인
- LLS: 세 집단 전체

세 파일의 모집단이 상호배타적인지, 시간·격자 정의와 마스킹 규칙이 동일한지는 원본과
명세로 확인하기 전 합산하지 않는다 `[VERIFY]`. 예상 가설은 단기체류 외국인이 관광특구
프로파일을 개선하고 주거지역에는 효과가 작다는 것이다. 이 가설이 맞지 않으면 내국인
기준선을 유지한다.

## 4. 제안하는 2층 엔진

### 4.1 베이스라인 층

셀 `c`, 시각 `t`에 대해 과거 생활인구의 조건부 중심값을 `B(c,t)`로 둔다. 현재 shadow의
`B`는 `log1p` 변환과 최근성 가중 평균·분산, 계층적 fallback/부분 수축으로 계산한다.
조건은 요일·시간·공휴일 맥락·계절이다. `B`는 카페 좌석 점유율이 아니라 **그 셀에 평소
존재하는 인구**다.

서로 다른 산출체계의 원시 숫자를 직접 나누면 안 된다. citydata의 `ppltn_mid`와 생활인구
총계는 공간단위와 추계 방식이 다르므로 다음처럼 각 소스 안에서 먼저 정규화한다.

```text
hotspot_anomaly(h,t)
  = robust_standardize(citydata(h,t), historical_citydata_profile(h,t))

cell_estimate(c,t)
  = living_population_baseline(c,t)
    × bounded_transform(weighted_nearby_hotspot_anomalies)
```

citydata 자체 이력이 아직 짧을 때는 anomaly 계수를 학습했다고 표현하지 않는다. 7일
겹침으로 방향성만 탐색하고, 최소 4주 전에는 shadow 결과만 만든다. 실제 공개 승격은
8~12주와 Phase 6 관측을 요구한다.

또한 이 상관은 독립적인 정답 검증이 아니다. 서울 생활인구는 서울시·KT 통신데이터 기반이고,
citydata 인구도 이동통신 기지국 신호를 이용한다. 두 지표는 통신계열 원천과 보정 과정의
공통 편향을 일부 공유할 수 있어 상관이 실제 보행 혼잡 정확도를 과대평가할 수 있다.
OA-22784와 OA-21285의 공식 정의를 근거로, 7일 상관 실험은 **두 소스의 호환성 gate**로만
해석한다. 독립 정확도 근거는 Phase 6 현장 보행 관측 또는 계보가 다른 검증 소스에서 얻는다.
<https://data.seoul.go.kr/dataVisual/seoul/seoulLivingPopulation.do>,
<https://data.seoul.go.kr/dataList/OA-21285/A/1/datasetView.do>

### 4.2 실시간 이상치 층

- 카페 또는 셀이 공식 핫스팟 폴리곤 내부면 해당 핫스팟을 우선한다.
- 외부면 대표점이 아니라 폴리곤 경계거리와 교차면적을 사용한다.
- contributor별 신선도, 레벨·시각 합의도, 수집 cycle 건강도를 별도로 보존한다.
- citydata 이상치는 상한·하한을 둬 장애나 단일 급등이 서울 전체로 번지지 않게 한다.
- `uncovered`는 계속 NULL이며 베이스라인만으로 “실시간” 색을 칠하지 않는다.

공개 `v1-idw-point`는 이 연구 때문에 바뀌지 않는다. v2/v3 shadow가 사전등록된 gate를
통과해야만 model version을 승격한다.

## 5. 미래 정보 누출 방지

과거 파일이 많다고 평가가 자동으로 타당해지지는 않는다. 다음 규칙을 고정한다.

1. 시점 `t` 예측의 베이스라인은 `t` 이전에 제품이 실제로 취득 가능했던 파일만 사용한다.
2. 공개가 4일 지연된 생활인구를 `t` 당일 feature처럼 사용하지 않는다. 이는 baseline 학습과
   지연 평가에만 쓴다.
3. 무작위 행 분할을 금지하고 rolling-origin 방식으로 train/calibration/test 날짜를 나눈다.
4. 동일 월 파일의 수정본은 `fetched_at`, URL, 크기, SHA-256으로 버전 고정한다.
5. 실제 날씨(actual weather)를 과거 평가에 쓸 때, 운영 비교군은 그 시점에 이용 가능했던
   예보만 사용한다. actual을 운영 feature처럼 쓰면 미래 누출이다.
6. 공휴일 달력은 사전 확정 정보이므로 사용 가능하지만, 사후 지정·취소 이력은 당시
   가용 버전을 보존한다.
7. 파라미터·feature 선택에 쓴 기간과 최종 test 기간을 분리하고, test 결과를 본 뒤 같은
   기간에 재튜닝하지 않는다.

## 6. 선행 플랫폼에서 가져올 것과 가져오지 않을 것

### Google Maps Popular Times

Google의 공식 설명은 Popular Times가 위치 기록(Location History)을 켠 사용자의 집계·
익명화된 방문 데이터에서 시간대별 전형적 혼잡을 만들고, 충분한 방문 데이터가 있는
곳에는 live busyness를 평소 수준과 비교해 보여준다고 설명한다. 같은 글은 COVID-19 시기의
급격한 패턴 변화에 적응하려 최근 4~6주 데이터에 더 높은 비중을 두었다고 밝힌다.
BusyCafe가 참고할 핵심은 **장소별 기준선과 현재 이상치를 분리**하고, 표본이 충분한 곳만
표시하는 제품 원칙이다.

출처: Google 공식 블로그,
<https://blog.google/products-and-platforms/products/maps/maps101-popular-times-and-live-busyness-information/>
공식 블로그가 연결한 Business 도움말도 함께 보존한다.
<https://support.google.com/business/answer/6263531?hl=en>

그러나 Google Places API의 공식 Place Details 필드에는 Popular Times가 문서화돼 있지
않다(`[VERIFY]`: 문서는 변경될 수 있으므로 도입 검토 때 재확인).
<https://developers.google.com/maps/documentation/places/web-service/place-details>
따라서 HTML 스크레이핑·비공식 엔드포인트·역공학은 정확도, ToS, 차단 위험 때문에 사용하지
않는다. Google Maps Platform 약관도 함께 검토해야 한다.
<https://cloud.google.com/maps-platform/terms>

### Foursquare와 상용 foot-traffic 플랫폼

Foursquare의 Place Details 스키마에는 `popularity` 숫자와 `hours_popular` 시간 배열이
문서화돼 있다. 이는 Google 비공식 스크레이핑보다 검토 가능한 공식 API 후보지만, 두 필드의
정확한 의미·시간 정규화·가용 요금제, 대한민국 coverage, 표본 편향, cache/재배포 권리는
계약과 실응답으로 확인해야 한다 `[VERIFY][HUMAN]`.

- Foursquare Places 속성: <https://docs.foursquare.com/data-products/docs/places-attributes>
- Foursquare Place Details(`popularity`, `hours_popular`):
  <https://docs.foursquare.com/fsq-developers-places/reference/place-details>
- Foursquare Analytics 개요: <https://docs.foursquare.com/analytics-products/docs/overview>

Placer.ai, BestTime 같은 상용 서비스도 유동량·혼잡 예측을 표방하지만 대한민국 coverage와
원천 데이터 계보, 카페 단위 재배포 권리가 확인되지 않았다 `[VERIFY][HUMAN]`. 이들은
공공데이터 모델의 대체제가 아니라 **동일 카페·동일 시각의 유료 표본을 받아 외부 타당도와
비용 대비 개선폭을 검증하는 challenger**로만 고려한다.

- Placer.ai 제품 개요: <https://www.placer.ai/platform/overview>
- BestTime 제품 페이지: <https://besttime.app/> `[VERIFY: 원천·한국 coverage·약관]`

### 연구 문헌

BusyCafe에 직접 적용할 수 있는 공통 원리는 다음과 같다.

- ST-ResNet은 도시를 격자로 나누고 최근성(closeness), 주기(period), 장기 추세(trend)와
  날씨·휴일 같은 외생변수를 결합한다. 이는 250m 격자, 요일×시간 baseline, 날씨/공휴일
  ablation의 근거가 된다. 다만 대규모 학습자료를 전제로 하므로 지금 바로 신경망을
  도입하라는 근거는 아니다. Zhang et al., “Deep Spatio-Temporal Residual Networks for
  Citywide Crowd Flows Prediction,” AAAI 2017,
  <https://doi.org/10.1609/aaai.v31i1.10735>.
- 도시 컴퓨팅 연구는 교통·이동·POI·기상 같은 이질적 소스를 공간·시간 정합한 뒤 도시
  현상을 추론하는 방법과 데이터 품질 문제를 정리한다. Zheng et al., “Urban Computing:
  Concepts, Methodologies, and Applications,” ACM TIST 2014,
  <https://doi.org/10.1145/2629592>.
- Prophet의 실무 예측 구조는 여러 계절성과 휴일 효과를 명시적으로 분리한다. 여기서는
  Prophet 자체 채택보다 “요일·연간 계절·휴일을 별도 성분으로 검증한다”는 설계 참고다.
  Taylor and Letham, “Forecasting at Scale,” The American Statistician 2018,
  <https://doi.org/10.1080/00031305.2017.1380080>.
- 사람 이동에는 높은 규칙성이 있지만 개인·표본·공간 집계 수준에 따라 예측 가능성의
  상한이 달라진다. 정확도를 보장하는 인용이 아니라, 경험적 gate와 uncovered 처리가
  필요한 이유다. Song et al., “Limits of Predictability in Human Mobility,” Science 2010,
  <https://doi.org/10.1126/science.1177170>.
- 신규 장소의 활동 패턴은 주변 장소, 범주와 지역의 기존 시간 패턴으로 추정할 수 있다는
  연구가 있다. 이는 과거가 없는 신규 카페에 셀·범주 prior를 쓰는 challenger 근거지만,
  서울 데이터로 다시 검증해야 한다. D’Silva et al., “Predicting the temporal activity
  patterns of new venues,” EPJ Data Science 2018,
  <https://doi.org/10.1140/epjds/s13688-018-0142-z>.
- 이동통신 자료로 동적 인구지도를 만들 때 정적 인구자료와 통신활동의 보정 관계를 먼저
  추정해야 한다. 이는 기지국 신호를 곧바로 보행 인원으로 해석하지 않는 근거다. Deville
  et al., “Dynamic population mapping using mobile phone data,” PNAS 2014,
  <https://doi.org/10.1073/pnas.1408439111>.
- 시공간 자료에 일반 random cross-validation을 쓰면 가까운 시점·공간이 train/test에
  함께 들어가 성능이 낙관적으로 보일 수 있다. rolling-origin과 공간/시간 block 평가의
  방법론적 근거다. Roberts et al., “Cross-validation strategies for data with temporal,
  spatial, hierarchical, or phylogenetic structure,” Ecography 2017,
  <https://doi.org/10.1111/ecog.02881>.

## 7. Feature 우선순위와 가설

한 번에 하나의 feature군만 추가해 같은 고정 검증셋에서 ablation한다.

| 순서 | 가설 | 실험 | 채택 조건 |
|---|---|---|---|
| H1 | 요일×시간 기준선이 단순 시간 평균보다 낫다 | `hour` vs `hour_of_week` | 전체·주말 Spearman 개선, tail 회귀 없음 |
| H2 | 공휴일·대체휴일·연휴 맥락이 공휴일 오차를 줄인다 | H1 + holiday hierarchy | 공휴일 MAE/순위 개선, 일반일 회귀 없음 |
| H3 | 단기체류 외국인은 관광특구 설명력을 높인다 | L/LL/LS/LLS 비교 | 관광특구 개선 + 비관광 지역 악화 없음 |
| H4 | 최근 계절 가중이 42개월 전체 동일가중보다 현재 패턴에 가깝다 | expanding vs rolling 8/12주 vs 감쇠 | 홀드아웃 개선, 계절 전환 급등 recall 유지 |
| H5 | 강수·폭염·한파·적설은 보행 혼잡과 카페 선택을 바꾼다 | 기상 feature 단독 추가 | forecast-available 평가에서 개선할 때만 채택 |
| H6 | 지하철 승하차는 역세권 단기 유입을 설명한다 | 역거리×시간대 승하차 | 역세권 층 개선, 비역세권 회귀 없음 |
| H7 | 생활이동 OD는 생활인구보다 보행 “흐름”에 가깝다 | OD 단독 challenger | citydata/현장 보행 라벨 상관이 생활인구보다 높을 때 채택 |
| H8 | 분기 매출은 지역의 구조적 상업 수요를 설명한다 | 정적 prior 단독 | 시간 변화가 아닌 공간 bias만 개선할 때 제한 사용 |
| H9 | 핫스팟 범주별 anomaly 전파계수가 다르다 | 관광/상권/밀집 분리 | 충분한 표본과 시간 test에서 일관될 때만 분리 |
| H10 | 지역 혼잡과 카페 내부 좌석 혼잡의 관계는 시간·매장형태별로 다르다 | Phase 6 두 라벨 동시 수집 | 지역 엔진과 추천 효용을 별도 metric으로 유지 |

이 표의 “개선”은 전체 평균만 뜻하지 않는다. Track 1의 거리·coverage·평일/주말·야간
분해와 기존 안전성 guardrail을 모두 통과해야 한다.

## 8. 데이터 기간과 승격 gate

42개월 생활인구가 있어도 citydata와 현장 관측의 겹침이 짧으면 모델 승격 증거가 되지
않는다.

| Gate | 최소 자료 | 허용되는 결론 | 제품 행동 |
|---|---|---|---|
| G0 원천 | 원본 URL·크기·SHA-256·라이선스·스키마 fixture | 재현 가능한 입력 확보 | parser/aggregate 구현 가능 |
| G1 품질 | 월별 누락, 셀 coverage, 마스킹률, 시간대·좌표 검증 | baseline 계산 가능 여부 | 실패 월 제외 규칙을 사전 기록 |
| G2 탐색 | citydata 연속 7일 + 동일 시각 생활인구 | 상관 방향, 파이프라인 결함 발견 | **공개값 변경 금지** |
| G3 최소 비교 | 연속 4주 + 사전 고정 test + Phase 6 일부 | feature ablation의 초기 안정성 | shadow 유지, 확률 표현 금지 |
| G4 승격 후보 | 8~12주 연속 + 주말/공휴일/기상 층 표본 + Phase 6 | v1/v2/v3 비교와 calibration | 사전등록 gate 통과 시에만 승격 |
| G5 운영 | 최소 2주 shadow, rollback·신선도·비용 측정 | 실제 운영 적합성 | model_version 단위 점진 승격 |

7일은 탐색용이지 최소 학습기간이 아니다. 4주는 “비교를 시작할 수 있는 최소”, 8~12주는
일반 주간 패턴의 promotion 후보 기준이다. 공휴일별 모델은 8~12주로도 표본이 부족하므로
계층적 묶음을 유지하고, 특수 공휴일을 독립 분리하려면 여러 해의 관측 또는 외부 검증이
필요하다.

## 9. 빠르고 비용 효율적인 구축 방식

월별 원본 전체를 PostgreSQL row로 적재하면 비용만 커지고 요청 경로가 느려진다. 처리와
서빙을 분리한다.

1. 원본 ZIP/CSV는 immutable object/local archive에 두고 URL·SHA-256 manifest를 저장한다.
2. 기존 strict Python parser는 원본 스키마·날짜·시간·CELL_ID·마스킹을 표본/경계 행에서
   검증하는 품질 gate로 유지한다. 모든 행을 Python 객체로 만들며 월별 수억 건을 합산하는
   실행 경로로 쓰지 않는다.
3. DuckDB는 이미 backend 의존성이며 실파일 `cp949` 직접 읽기를 확인했다. 월별 대량 집계는
   DuckDB의 set-based scan/group-by를 기본으로 하고, strict parser와 동일한 행 수·합계·
   마스킹 수를 표본 월에서 대조한다. 다운로드와 계산은 요청 경로에서 실행하지 않는다.
4. 연구 단계에는 핫스팟과 교차하거나 평가에 필요한 셀만 추출한다. 서울 전체 밀도맵이
   필요해지면 별도 columnar partition을 만든다.
5. DB에는 원시 수억 행 대신 `cell × hour_of_week × day_type × season`의 집계값, 표본 수,
   산포, masking 비율, dataset version만 저장한다.
6. 계산 결과는 `baseline_version`으로 immutable하게 게시하고 API worker는 최신 승인
   버전을 읽기만 한다.
7. DuckDB scan과 집계 결과를 측정한 뒤에만 Parquet 중간 partition의 필요성을 판단한다.
   Polars 같은 새 의존성은 동일 입력 벤치마크에서 시간·메모리 이득이 확인될 때만 검토한다.
8. 일일 증분은 새 날짜만 반영하되, 과거 수정 파일이 발견되면 해당 partition을 SHA 기준으로
   재빌드한다. 결정적 full rebuild 경로를 항상 유지한다.

OA-22784 페이지의 월 파일 크기(약 380~600MB)를 42개월에 단순 적용하면 다운로드 원본은
대략 16~25GB 규모다. 이는 계획용 추정치이며 실제 manifest의 총 bytes로 교체한다
`[VERIFY]`. 전체 파일을 매 요청 읽지 않고 한 번 스트리밍 집계하면 운영 API 비용에는 거의
영향을 주지 않는다.

## 10. 즉시 실행 순서

1. OA-22784 42개 월 파일 manifest를 고정하고 누락·중복·총 bytes를 검증한다.
2. OA-22785/OA-22786 원본 각 1건을 확보해 내국인과 스키마·격자·모집단·라이선스를
   비교한다 `[HUMAN: 필요 시 포털 로그인]`.
3. 특일 정보와 ASOS 각 최소 fixture를 확보한 뒤에만 parser/schema를 확정한다.
4. 현재 구현된 `B1=ISO weekday+day_type` shadow의 model version을 고정하고,
   `B0=hour-only` 비교군 → 공식 달력을 넣은 `B2=holiday hierarchy` →
   `B3=foreign ablation` 순서로 오프라인 비교한다.
5. worker 연속 수집을 복구해 7일 상관 탐색을 실행한다. 결과를 본 뒤 판정 기준을 바꾸지
   않는다.
6. 4주 전에는 feature 승격 판단을 하지 않고, 8~12주 및 Phase 6까지 공개 v1을 유지한다.
7. 기상·교통·매출·생활이동은 앞 단계가 통과한 뒤 한 종류씩 추가한다. 동시에 넣지 않는다.

가장 빠른 길은 복잡한 ML이 아니라 **이미 있는 42개월을 정직하게 조건부 기준선으로 만들고,
새로 모으는 실시간 신호와 현장 관측으로 한 가설씩 제거하는 것**이다. 실패한 feature도
가설·입력 버전·코드 commit·결과·폐기 이유를 남긴다.
