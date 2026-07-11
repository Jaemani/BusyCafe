# ADR-0001: PostgreSQL을 기본 데이터베이스로 사용

- 상태: Accepted
- 결정일: 2026-07-11
- 관련 계획: Phase 0, 시스템 아키텍처

## 배경

cafe-crowd는 핫스팟, append-only 스냅샷, 카페, 최신 점수 사이의 관계와
시점별 평가를 다룬다. 주요 읽기 패턴은 지도 bbox 조회, 최근 스냅샷 조회,
거리 기반 이웃 검색이며 데이터 정합성을 위한 FK, unique constraint, 트랜잭션,
멱등 upsert가 필요하다.

사용자는 MongoDB 또는 Firebase를 선호 후보로 제시했고, 향후 사용자 증가에도
유지 가능한 구성을 요청했다. 기존 계획의 SQLite는 로컬 MVP에는 충분하지만
다중 프로세스 운영과 공간 질의 확장에는 제약이 있다.

## 결정

운영과 통합 테스트의 기준 데이터베이스는 PostgreSQL로 한다. 로컬 단위 테스트는
필요한 경우 SQLite를 사용할 수 있지만, SQLite 전용 타입이나 SQL에 의존하지 않는다.
공간 질의가 필요해지면 PostGIS의 `geography(Point, 4326)`와 GiST 인덱스를
마이그레이션으로 추가한다.

- SQLAlchemy 2와 Alembic으로 저장소 및 스키마 변경을 추상화한다.
- 외부 응답과 예측/기여자 구조는 PostgreSQL JSON/JSONB에 저장한다.
- `hotspot_snapshots(hotspot_id, observed_at)`에는 unique constraint를 둔다.
- 운영 인제스트 스케줄러는 API 프로세스와 분리한 단일 worker로 실행한다.
- Firebase는 향후 Auth, 푸시 또는 호스팅 후보로만 둔다.
- MongoDB와 Firestore는 핵심 저장소로 사용하지 않는다.

## 근거

- 현재 모델은 문서형보다 관계형이며 과거 시점 조인과 집계가 빈번하다.
- PostGIS는 bbox, 반경, 거리 정렬을 한 저장소 안에서 인덱싱할 수 있다.
- MVP 예상치인 12개 핫스팟의 10분 폴링은 연 약 63만 스냅샷으로 PostgreSQL에
  작은 부하이며, 사용자가 늘어도 주로 읽기 캐시 문제로 분리해 대응할 수 있다.
- Firestore는 반경 검색에 geohash 다중 조회와 중복 제거가 필요하고 지도 이동이
  읽기 과금으로 직결된다.
- MongoDB도 공간 검색은 가능하지만 참조 무결성, 시점별 평가, 점수 갱신의 상당
  부분을 애플리케이션 코드로 옮겨야 한다.

## 확장 경로

1. 관리형 PostgreSQL 1개, FastAPI, 단일 인제스트 worker로 시작한다.
2. bbox를 타일/격자로 정규화해 짧은 TTL의 HTTP 또는 Redis 캐시를 추가한다.
3. 실제 병목이 확인되면 PostGIS, connection pooling, 읽기 replica를 추가한다.
4. 장기 스냅샷은 월별 partition과 rollup을 사용하고 오래된 raw 응답은 object
   storage로 옮긴다.

## 결과와 주의점

초기 설정은 SQLite보다 많지만 운영 전 DB 교체가 사라진다. API 서버를 수평
확장할 때 각 프로세스에서 APScheduler를 기동하면 중복 폴링되므로 금지한다.
개발 편의를 위해 프로세스 내 스케줄러를 쓸 경우에도 단일 인스턴스임을 명시하고,
운영 전 worker 분리를 완료한다.
