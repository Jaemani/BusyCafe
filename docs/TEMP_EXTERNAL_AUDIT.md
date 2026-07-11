# cafe-crowd 임시 외부감사 자료

> 상태: 외부 검토용 임시 문서
>
> 기준일: 2026-07-12 (Asia/Seoul)
>
> 이 문서는 `docs/PLAN.md`를 대체하는 source of truth가 아니다. 현재 팀의 자체 평가를
> 외부 감사자가 반박·수정할 수 있도록 사실, 미완료 항목과 재현 절차를 한곳에 모은다.
> 감사 종료 후 결과를 정식 검증 문서와 ADR에 반영하고 이 파일은 삭제하거나 보관 상태로
> 전환한다.

## 1. 감사 목적과 판정 요약

현재 결과물은 지도, 카페 원장, 서울 실시간 데이터 수집기, 결정적 점수 엔진과 공개
데모가 연결된 **기능성 기술 프리뷰**다. 그러나 현장 정답 데이터에 기반한 정확도 검증과
관리형 실시간 프로덕션 저장소가 없으므로, 검증된 추천 제품이나 실시간 운영 완료 상태로
판정할 수 없다.

- 자체 종합 점수: **2.6 / 5.0**
- 자체 판정: **기술 프리뷰 / 제한 공개 가능, 일반 사용자 대상 실시간 제품 출시는 미통과**
- 평가 확신도: 저장소·테스트·배포 사실은 높음, 실제 장소·혼잡도 정확성은 낮음
- 외부 감사자의 우선 과제: POI 표본 검수, 현장 정확도 평가, 라이선스 확인, 프로덕션
  실시간성 검증

이 점수는 독립 감사 결과가 아니라 감사 전 자체 기준선이다. 외부 감사자는 아래 점수와
판정을 자유롭게 교체하고 근거를 남긴다.

## 2. 평가표

| 평가 영역 | 가중치 | 자체 점수 | 근거 | 핵심 미달 사유 |
|---|---:|---:|---|---|
| 제품 정의와 정직성 | 10% | 4.0 | 지역 혼잡과 매장 점유를 구분하고 uncovered를 명시 | 실제 사용자 오인 여부를 사용성 시험하지 않음 |
| 데이터 수집 파이프라인 | 15% | 3.5 | 서울 121개 장소 cycle `121/121`, 실패 격리, fixture | 공개 프로덕션은 지속 수집 DB가 아닌 배포 스냅샷 |
| 카페 POI 품질 | 10% | 2.5 | Overture 고신뢰 필터로 서울 4,933개 캐시 | HUMAN 표본 검수·폐업 보정·직접 상세 링크 검증 미완료 |
| 모델의 과학적 타당성 | 20% | 1.5 | 결정적 IDW, 모델 버전, 단위 테스트 | 현장 ground truth·calibration·Phase 6 평가 없음 |
| 성능과 확장성 | 15% | 2.5 | CDN 캐시, bbox 인덱스, 번들 정리 | 대용량 JSON, 클라이언트 클러스터링, MVT/PostGIS 미구현 |
| 프로덕션 운영 준비 | 15% | 2.0 | 공개 Vercel 배포, health API, 분리 worker 코드 | 관리형 DB·상시 worker·경보·복구 훈련 없음 |
| 보안과 변경 거버넌스 | 10% | 4.0 | 시크릿 분리, 인시던트 기록, ADR·검증 로그 | 정식 위협 모델·의존성/운영 보안 감사 없음 |
| 국내·해외 확장 준비 | 5% | 1.5 | 세 트랙 문서와 유니버설 계약 구현 | 실제 비서울 데이터 source/fixture/license 검증 0개 |

가중 합계는 2.625이며 소수점 첫째 자리에서 2.6으로 표시한다.

## 3. 확인된 완료 사실

### 데이터와 백엔드

- 서울 주요 장소 master 121개와 WGS84 영역을 확보하고 코드 집합 일치를 확인했다.
- 서울 실시간 도시데이터의 실제 응답 구조, 라벨 4종과 예측 구조를 fixture로 보존했다.
- 자동 scheduler cycle에서 `targets=121`, `saved=121`, `failed=0`을 확인했다.
- Overture Places release `2026-06-17.0`에서 서울 카페 4,933개를 캐시했다.
- IDW 결과는 `model_version=v1-idw-point`로 저장하며 기존 4,933개 점수를 migration했다.
- `/api/cafes`, `/api/cafes/{id}`, `/api/hotspots`, `/api/health`가 구현돼 있다.
- 국내·해외 공통 경계로 `RegionProfile`, `CrowdObservation`, `CoverageSnapshot` 계약과
  source/license version 필드를 추가했다.

### 지도와 배포

- MapLibre GL과 OpenFreeMap 기반 지도, 카페 클러스터, 상세 패널과 내 위치 기능이 있다.
- 정확한 공개 주소는 `https://busy-cafe.vercel.app`이며 홈과 API HTTP 200을 확인했다.
- 배포 함수 번들은 불필요한 백업 DB를 제외해 19.88MB에서 최신 13.59MB로 줄었다.
- canonical과 Open Graph URL은 `busy-cafe.vercel.app`을 사용한다.
- 기존 `budy-cafe` 별칭은 초기 오타 링크 호환용으로 남아 있다.

### 자동 검증

- 백엔드 전체 테스트: 97 passed, 기존 Starlette deprecation warning 1건.
- 프론트엔드 TypeScript typecheck와 production build가 통과한다.
- 프론트 production JS: 1,065.34KB raw, 287.90KB gzip.
- 배포 snapshot health 실측 당시 카페 4,933개와 최신 수집 시각을 반환했다.

상세 증거는 [VERIFICATION.md](VERIFICATION.md), 변경 사고와 재발 방지는
[INCIDENTS.md](INCIDENTS.md), 결정 근거는 [DECISIONS.md](DECISIONS.md)와
[ADR 디렉터리](adr/)에 있다.

## 4. 중요한 미완료와 블로커

### 정확도와 신뢰도

- 현재 confidence는 거리, 신선도와 기여 이웃 수로 만든 입력 품질 휴리스틱이다.
  실제 정답률이나 적중 확률로 calibration되지 않았다.
- Phase 6 현장 관측, Spearman, adjacent accuracy와 ECE 결과가 없다.
- 공식 핫스팟 폴리곤 대신 대표점까지의 거리를 사용한다.
- 지역 보행 혼잡과 매장 좌석 점유의 상관을 검증하지 않았다.
- 상세 계획은 [Track 1](tracks/TRACK-1-ACCURACY.md)에 있으나 A2 이후는 구현 전이다.

### 카페 원장

- 사용자 또는 독립 감사자가 층화 표본을 지도와 대조한 정식 결과가 없다.
- 폐업·이전·중복 매장을 서울 인허가 데이터로 보정하는 경로가 완료되지 않았다.
- Naver/Kakao/Google canonical 상세 ID는 대부분 없어 버튼을 숨기는 상태다.
- Overture release의 국가별 품질과 장기 이용 조건은 확장 지역마다 재검증해야 한다.

### 실시간 프로덕션 운영

- 공개 Vercel은 읽기 전용 SQLite 배포 스냅샷이다. 로컬 worker의 10분 갱신이 Vercel에
  자동 반영되지 않는다.
- 관리형 PostgreSQL과 production worker는 코드 경계만 준비됐고 실제 credential,
  bootstrap과 운영 검증이 없다.
- stale 경보, 수집 실패 알림, 백업·복구, 재해복구 목표와 on-call 절차가 없다.
- 따라서 현재 공개 URL을 지속 실시간 서비스로 홍보하면 안 된다.

### 성능

- 홍대 bbox 실측: 392,406 bytes, total 0.88초.
- 서울 전체 bbox 실측: 2,704,411 bytes, total 1.93초.
- 지도 이동 때 상세 필드가 포함된 JSON을 받고 브라우저가 클러스터링한다.
- 벡터 타일, zoom별 서버 집계, PostGIS 공간 쿼리와 상세 지연 조회가 아직 없다.
- Vite는 500KB 초과 chunk 경고를 출력한다.

### 국내·해외 및 밀집도 지도

- 국내·해외 확장은 현재 로드맵, 검증 게이트와 Pydantic 공통 계약 단계다.
- 서울 외 실제 공급자 응답 fixture, 상업적 재배포 권리와 7일 shadow ingest가 없다.
- 카페·밀집도·커버리지 3개 모드는 결정됐지만 밀집도 MVT/API/UI는 구현되지 않았다.
- 자세한 범위는 [통합 로드맵](ROADMAP.md), [국내 트랙](tracks/TRACK-2-KOREA.md),
  [해외 트랙](tracks/TRACK-3-GLOBAL.md)에 있다.

## 5. 알려진 주요 인시던트

- 부정확한 OSM 타일 POI를 제품 카페로 사용했다가 제거했다.
- URL 경로에 포함된 서울 API 키가 HTTP INFO 로그에 노출되어 키를 교체하고 관련 로그를
  차단했다.
- SQLite timezone 손실로 `572분 전`이라는 잘못된 표시가 발생해 UTC 경계를 복구했다.
- Vercel 프로젝트명을 `budy-cafe`로 생성해 rename, alias와 SSO 공개 정책을 복구했다.
- 공유 작업공간에서 migration 완료 전 worker를 실행해 materialize가 실패했다. snapshot은
  보존됐고 migration/backfill 후 자동 cycle을 재검증했다.

각 원인, 영향과 재발 방지는 [INCIDENTS.md](INCIDENTS.md)에 기록돼 있다. 외부 감사자는
기록의 존재뿐 아니라 예방 조치가 코드·운영 절차에서 실제 강제되는지 확인해야 한다.

## 6. 외부 감사 재현 절차

실 API 호출 테스트는 금지한다. 저장된 fixture와 공개 read API만 사용하며 시크릿 값을
출력하지 않는다.

```bash
rtk git status --short

cd backend
rtk uv run pytest
rtk uv run python -m compileall -q app scripts tests

cd ../frontend
rtk npm run typecheck
rtk npm run build

rtk proxy curl -sS https://busy-cafe.vercel.app/api/health
rtk proxy curl -sS -o /dev/null -w '%{http_code}\n' https://busy-cafe.vercel.app/
```

추가 수동 검증:

1. 모바일과 데스크톱에서 지도 이동·확대·내 위치 허용/거부를 확인한다.
2. 지역과 confidence 구간을 나눠 카페 최소 30개 이름·좌표·영업 상태를 독립 확인한다.
3. covered, fringe, uncovered 표본에서 근거 핫스팟과 실제 공간 관계를 확인한다.
4. API 중단, stale snapshot과 빈 bbox에서 UI가 오해를 만들지 않는지 확인한다.
5. production health의 `last_ingest_at`이 자동 갱신되는지 확인한다. 현재는 갱신되지 않아야
   하며, 이 결과가 스냅샷 한계를 재확인한다.

## 7. 외부 감사자 핵심 질문

1. 카페 위치와 영업 상태의 표본 오류율은 허용 가능한가?
2. “주변 혼잡도”라는 문구가 매장 좌석 점유로 오인되지 않는가?
3. 현재 confidence가 사용자에게 확률처럼 보이지 않는가?
4. 실제 관측을 사용한 모델 순위·레벨·calibration 성능은 얼마인가?
5. 서울 데이터와 Overture 파생 결과의 상업적 표시·캐시·재배포 근거가 충분한가?
6. 수집 중단 시 stale 상태가 자동으로 사용자에게 드러나는가?
7. 관리형 DB 장애, provider schema 변경과 부분 cycle을 복구할 수 있는가?
8. 모바일 네트워크에서 초기 지도와 이동 중 payload가 예산을 만족하는가?
9. 국내·해외 후보 도시는 실제 데이터 권리와 품질 게이트를 통과했는가?
10. 배포·인시던트 문서와 실제 시스템 상태가 일치하는가?

## 8. 감사 결과 기입란

- 감사자/기관:
- 감사 일시와 commit SHA:
- 감사 범위:
- 독립 종합 점수(0~5):
- 출시 판정: 통과 / 조건부 통과 / 미통과
- 확인된 강점:
- 중대한 결함:
- 반드시 수정할 항목:
- 재검증 기한:
- 서명 또는 승인 링크:

## 9. 관련 기준선

- 계획: [PLAN.md](PLAN.md)
- 병렬 고도화 순서: [ROADMAP.md](ROADMAP.md)
- 실측 기록: [VERIFICATION.md](VERIFICATION.md)
- 결정 기록: [DECISIONS.md](DECISIONS.md)
- 사고 기록: [INCIDENTS.md](INCIDENTS.md)
- 실시간 운영 결정: [ADR-0005](adr/ADR-0005-live-production-runtime.md)
- 유니버설 확장 결정: [ADR-0006](adr/ADR-0006-universal-expansion-tracks.md)
- 기준 커밋: `317e110`, `f12a6db`, `5609477`, `a9bf121`, `b4f97be`
