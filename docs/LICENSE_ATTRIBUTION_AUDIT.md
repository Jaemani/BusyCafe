# 현행 서비스 라이선스·출처표시 감사

- 최초 확인일: 2026-07-12 (KST)
- Kakao 운영 경로 추가 확인일: 2026-07-15 (KST)
- 검토 기준 커밋: `4468dc84e08038f7f5d639330b750d10447c8d94`
- Kakao 운영 경로 추가 기준 커밋: `3cc82b4`
- 범위: 현재 프로덕션이 실제 사용하는 코드, Kakao Local·Overture Places 카페 원장,
  OpenFreeMap/OpenMapTiles/OpenStreetMap 베이스맵, 서울시 실시간 도시데이터
- 제외: 향후 국내·해외 provider

이 문서는 공식 제공자 문서와 저장소 증거를 대조한 공학적 감사 기록이며 법률 자문이
아니다. `PASS`는 확인한 현행 사용 범위에서 문서상 조건과 구현이 정렬되었다는 뜻이고,
`BLOCKED`는 공개 릴리스 전에 조치가 필요하다는 뜻이다. `[VERIFY]`는 공식 문서만으로
확정할 수 없으므로 추정하지 않은 항목이다.

## 결론

Overture·서울시·현행 베이스맵의 상업적 이용은 확인한 공식 문서상 가능하다. Kakao
Local은 사용자 경험 개선 목적 cache와 최신성 유지 조건을 확인했지만, 정보 복제·출판·
검색 디렉터리 입력에는 사전 승낙 조항도 있어 상업화 전 명시적 확인이 필요하다. 최초
감사에서 확인한 지도
attribution 강제 접힘, 서울시 출처 미표시와 Overture 데이터 공유 고지는 2026-07-12에
코드로 보완했다. 저장소 소유자가 코드 라이선스로 Apache License 2.0을 선택하고 루트
`LICENSE`를 추가해 최초 감사의 코드 라이선스 `BLOCKED`도 해소했다. 외부 데이터에는
BusyCafe 코드 라이선스가 아니라 아래 제공자별 조건이 계속 적용된다.

| 대상 | 판정 | 공식 근거 | 저장소·응답 증거 | 필요한 조치 |
|---|---|---|---|---|
| 저장소 코드 라이선스 | **PASS (2026-07-12 보완)** | 저장소 소유자가 Apache License 2.0을 선택 | 루트 `LICENSE`에 Apache License 2.0 공식 전문, README에 코드·외부 데이터 라이선스 구분 | 코드 배포 시 Apache License 2.0 조건을 유지. 외부 데이터에 이 코드 라이선스를 적용한다고 표현하지 않음 |
| Overture Places 현 release 수집·내부 cache | **PASS** | Places의 현행 공급자는 CDLA-Permissive-2.0, Apache-2.0, CC0 등 공급자별 조건. CDLA는 사용·변경을 허용함 | release `2026-06-17.0`, extract SHA-256 `5115e468…d8d184e`, 4,933건. `sources_json` 보존 | release마다 source/license 분포와 hash를 다시 기록 |
| Overture cache의 공개 API 제공 | **PASS (2026-07-12 보완)** | CDLA-Permissive-2.0 §2.1은 Data를 원형 또는 수정해 공유할 때 계약문을 함께 제공하도록 요구 | `GET /api/sources`가 release와 CDLA/CC0 링크를 제공하고 카페 응답이 `license_manifest_url`을 포함함 | release마다 실제 source/license 분포를 재검증 |
| Kakao Local complete snapshot cache와 앱 내 검색·표시 | **CONDITIONAL / HUMAN 확인 필요** | 운영정책은 UX 개선 목적 cache를 허용하되 최신 상태 미유지를 금지한다. 동시에 Developers 정보의 복제·출판·검색 디렉터리 입력에는 사전 승낙 조항이 있다 | raw snapshot은 artifact로 공개하지 않고 필요한 장소 필드·Place ID·direct URL만 DB에 저장. weekly complete refresh와 `/api/sources`, About 고지 적용 | 상업화 전 Kakao에 MapLibre 위 cache 검색·표시 범위를 명시해 확인. 불허 답변이면 Kakao refresh와 Kakao-origin 공개를 중단 |
| Overture API 필드가 `Data`인지 계산 `Results`인지 | **[VERIFY]** | CDLA §3.1은 계산 결과에는 의무를 부과하지 않지만, 원장명·좌표와 BusyCafe 점수의 법적 분류는 공식 문서만으로 확정 불가 | 응답에는 원장 필드와 BusyCafe 계산 결과가 함께 있음 | 법률 검토 전까지 원장 필드는 Data로 보수적으로 취급하고 §2.1 고지를 적용 |
| OpenFreeMap 공개 instance의 현행 상업 지도 사용 | **PASS** | 홈페이지가 상업 이용 허용, 공개 instance 무료, map view/request 제한 없음, API key 불필요를 명시 | `https://tiles.openfreemap.org/styles/positron` 사용 | 무상·무제한은 SLA가 아님. 장애 대비를 운영 리스크로 유지 |
| OpenFreeMap bulk 수집·미러링·offline cache | **[VERIFY]** | ToS는 허가 없는 automated collection을 금지하고 서비스 중단 가능·무보증을 명시 | 현행 코드는 브라우저의 정상 style/tile 요청만 수행하며 bulk 미러링은 없음 | prefetch, tile 저장, 자체 CDN 복제 전 OpenFreeMap의 서면 허가 또는 self-host 경로 확인 |
| 베이스맵 attribution 원문과 링크 | **PASS** | OpenFreeMap은 `OpenFreeMap © OpenMapTiles Data from OpenStreetMap`을 요구. OpenMapTiles와 OSM도 각각 가시적 credit와 라이선스 링크를 요구 | OpenFreeMap TileJSON이 세 링크를 반환하고 MapLibre attribution control이 이를 읽음 | 원문과 링크는 유지 |
| 베이스맵 attribution 표시 방식 | **FIXED, 브라우저 재검증 대기** | OSMF safe-harbour는 사용자가 상호작용하지 않아도 attribution을 보게 하고, 초기 표시 후 dismiss·지도 상호작용·5초 경과 등에 collapse할 수 있다고 설명. OpenMapTiles는 browsable map 모서리의 visible credit을 요구 | 강제 `maplibregl-compact-show`/`open` 제거와 CSS 강제 숨김을 삭제하고 MapLibre 기본 동작을 복구함 | 모바일·데스크톱 첫 화면에서 초기 노출과 이후 접근성을 수동 확인 |
| 서울시 실시간 도시데이터 상업 이용·변경·공유 | **PASS** | OA-21285는 저작권자 서울특별시, 제3저작권자 없음, 공공누리 제1유형이라고 명시. 공공누리 제1유형은 온·오프라인 공유, 변경, 영리 이용을 허용하고 출처표시를 요구 | 공식 121장소 응답을 cache하고 IDW 점수를 생성하며 `/api/hotspots`, 카페 evidence로 일부 제공 | 데이터셋의 라이선스 유형을 ingest 시 정기 재확인 |
| 서울시 출처표시 이행 | **PASS (2026-07-12 보완)** | 공공누리 제1유형의 필수 조건은 출처표시 | 지도 header가 서울특별시 OA-21285를 항상 표시하고 `/api/sources`가 공공누리 제1유형 링크를 제공함 | 배포 후 링크·모바일 가독성 수동 확인 |

## Overture Places 현 release 확인

공식 Overture attribution 페이지는 Places를 단일 라이선스로 선언하지 않고 공급자별
라이선스를 나열한다. 2026-07-12 현재 Meta, Microsoft, PinMeTo 등은
CDLA-Permissive-2.0, AllThePlaces는 CC0, Foursquare는 Apache-2.0과 별도 NOTICE를
표시한다.

현행 서울 extract의 `sources_json`을 집계한 결과는 다음과 같다. 하나의 장소가 여러
source record를 가질 수 있으므로 아래 수치는 고유 카페 수가 아니라 source record
수다.

| dataset | source record 수 | 기록된 라이선스 |
|---|---:|---|
| Overture | 4,933 | CDLA-Permissive-2.0 |
| meta | 4,408 | CDLA-Permissive-2.0 |
| Microsoft | 13 | CDLA-Permissive-2.0 |
| PinMeTo | 1 | CDLA-Permissive-2.0 |
| AllThePlaces | 511 | CC0-1.0 |

이 extract에는 Foursquare source record가 0건이므로 Foursquare NOTICE를 현행 서울
subset의 실제 공급자로 단정하지 않는다. 다만 다음 release에 포함될 수 있으므로 ingest
gate가 `sources_json`을 스캔하고 새 공급자·라이선스·NOTICE를 발견하면 배포를 중단해야
한다.

현재 구현은 `source_json`, `source_release`, Overture ID를 cache에 보존해 추적성은
확보했다. 반면 공개 API와 UI에는 Overture 이름과 release만 있고 계약문이나 라이선스
manifest로 가는 링크가 없다. 따라서 내부 cache의 생성은 `PASS`, 공개 공유 고지는
`BLOCKED`로 분리한다.

## 지도 attribution과 트래픽 조건

OpenFreeMap 홈페이지는 공개 instance에 map view/request 제한이 없고 상업 이용도
허용한다고 명시한다. 동시에 SLA나 개인 지원을 제공하지 않으며, ToS는 서비스가 예고
없이 중단될 수 있고 허가 없는 자동 수집을 금지한다. 따라서 현재의 일반 브라우저 지도
표시는 허용 범위로 판단하되, tile prefetch·미러링·오프라인 cache는 별도 확인 전
도입하지 않는다.

현재 OpenFreeMap TileJSON의 attribution은 다음 세 대상을 링크한다.

> OpenFreeMap © OpenMapTiles Data from OpenStreetMap

원문 자체는 공식 요구와 맞는다. 문제는 표시 시점이다. MapLibre의 attribution을
추가한 직후 코드가 강제로 접기 때문에 사용자는 정보 버튼을 누르기 전까지 출처를 보지
못한다. OSMF 지침은 초기 표시 후 특정 조건에서 접는 방식을 허용하므로, 모바일 공간을
보존하려면 최초 노출 후 사용자 dismiss, 첫 지도 상호작용 또는 5초 경과 시 접는 방식으로
바꿔야 한다.

## 서울시 데이터 표시 조건

OA-21285 데이터셋 페이지의 2026-07-12 표시 내용은 다음과 같다.

- 저작권자: 서울특별시
- 제3저작권자: 없음
- 라이선스: 공공누리 제1유형, 출처표시
- 허용 범위: 상업적 이용 및 변경 가능

공공누리 공식 제1유형 안내는 온라인·오프라인 공유와 영리 이용, 2차적 저작물 변경을
허용하며 출처표시를 필수로 한다. BusyCafe의 저장, 점수 계산, API 제공은 허용 범위에
들지만, 현재 사용자 화면에는 서울특별시와 데이터셋/라이선스가 명시되지 않는다.
혼잡도 레이어와 카페 evidence가 서울 데이터를 사용하므로 Overture 카페 출처와 별개로
서울시 출처를 항상 찾을 수 있어야 한다.

## 공식 근거

모든 URL은 2026-07-12에 직접 확인했다.

| 제공자·권리기관 | 공식 URL | 확인한 핵심 증거 |
|---|---|---|
| Overture Maps Foundation | https://docs.overturemaps.org/attribution/ | Places 공급자별 라이선스, Foursquare NOTICE, Overture/OSM attribution |
| CDLA | https://cdla.dev/permissive-2-0/ | 사용·변경 허용, Data 공유 시 계약문 제공, Results 비제한 |
| OpenFreeMap | https://openfreemap.org/ | 공개 instance 무료·무제한, 상업 이용 허용, 요구 attribution, 무SLA |
| OpenFreeMap ToS | https://openfreemap.org/tos/ | 무보증·중단 가능, 허가 없는 automated collection 금지 |
| OpenMapTiles | https://github.com/openmaptiles/openmaptiles/blob/master/LICENSE.md | BSD-3-Clause code, CC-BY 4.0 design, browsable map의 가시적 OpenMapTiles/OSM credit |
| OpenStreetMap | https://www.openstreetmap.org/copyright | ODbL, OpenStreetMap과 contributor credit, 변경 데이터의 share-alike 요약 |
| OpenStreetMap Foundation | https://osmfoundation.org/wiki/Licence/Attribution_Guidelines | interactive map attribution의 최초 표시, 허용되는 collapse 조건과 라이선스 접근성 |
| 서울 열린데이터광장 | https://data.seoul.go.kr/dataList/OA-21285/A/1/datasetView.do | 서울특별시 저작권, 제3저작권자 없음, 공공누리 제1유형, 상업·변경 가능 |
| 공공누리 | https://www.kogl.or.kr/info/licenseType1.do | 제1유형의 공유·변경·영리 이용 허용과 출처표시 의무 |
| Kakao Developers 운영정책 | https://developers.kakao.com/terms/ko/site-policies | UX 목적 cache와 최신성 의무, Developers 정보 이용·제공 제한 |
| Kakao Local API | https://developers.kakao.com/docs/ko/local/common | Local API의 장소·주소 검색 기능과 공식 제품 문서 |

## 공개 릴리스 해제 조건

- [x] 코드 라이선스로 Apache License 2.0을 선택하고 루트 `LICENSE`에 공식 전문을
  추가한다. 외부 데이터 라이선스와 코드 라이선스는 별도로 표시한다.
- [x] 지도 attribution 강제 초기 접힘을 제거하고 MapLibre 기본 표시 동작을 복구한다.
- [x] 사용자 접근 가능한 데이터 출처 화면에 Overture release·현행 라이선스와
  서울특별시 OA-21285·공공누리 제1유형을 링크한다.
- [x] 공개 API가 원장 데이터를 제공할 때 동일한 license manifest URL을 제공한다.
- [ ] Overture release ingest마다 source/license/NOTICE allowlist와 extract hash를
  검증한다.
- [ ] offline tile, prefetch, 미러링을 도입하기 전 OpenFreeMap 허용 범위를 다시 확인한다.
- [ ] 광고·후원 등 상업화 전에 Kakao Local cache를 MapLibre 기반 검색 원장으로 사용하는
  현재 범위에 대한 Kakao의 명시적 답변을 보존한다.
