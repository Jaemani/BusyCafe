# ADR-0002: 공식 핫스팟 폴리곤에서 대표 좌표 산출

- 상태: Accepted
- 결정일: 2026-07-11
- 관련 계획: Phase 0~1, 공간 매핑

## 배경

초기 계획은 주요 장소 마스터가 중심 좌표를 제공한다고 가정하고, 좌표가 없으면
Kakao 키워드 검색으로 보정하도록 했다. OA-21285 실측 결과 XLSX는 장소 코드·명칭·
분류만 제공하고, 별도 첨부가 WGS84 Shapefile 폴리곤 121개를 제공한다.

## 결정

Phase 1에서 XLSX와 Shapefile을 `AREA_CD`로 결합한다. 각 공식 폴리곤의 내부
대표점(`representative_point`)을 핫스팟의 `lat/lng`로 사용하고 원본 geometry도
향후 PostGIS 적재를 위해 보존한다.

Kakao 키워드 검색은 공식 geometry가 누락되거나 손상된 예외에만 사용하며, 해당
레코드는 반드시 수동 검수 목록에 포함한다.

## 근거

- 서울시가 제공한 공식 영역을 사용하므로 검색 결과에 따른 위치 편차가 없다.
- 단순 bbox 중심이나 일반 centroid는 오목하거나 여러 부분으로 나뉜 폴리곤 밖에
  놓일 수 있지만 내부 대표점은 영역 안에 위치한다.
- 코드와 영역을 `AREA_CD`로 결정적으로 결합할 수 있다.
- 향후 PostGIS를 활성화하면 point뿐 아니라 영역 기반 coverage도 재검토할 수 있다.

## 결과와 검증

- Phase 1에 Shapefile reader와 geometry 처리 의존성이 추가된다.
- 121개 코드가 XLSX와 DBF에서 일대일로 일치하는지 seed 전에 검증한다.
- 산출한 모든 대표점이 해당 geometry 내부이고 서울 bounding box 안인지 테스트한다.
- geometry fallback 발생 건수와 수동 검수 목록을 `docs/VERIFICATION.md`에 남긴다.
