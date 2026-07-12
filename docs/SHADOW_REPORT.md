# v1 point-IDW vs v2 polygon shadow 구조 비교

> 이 문서는 **정확도 증거가 아니다**. 정답 관측 없이 두 결정적 모델의 coverage와 출력
> 차이만 측정한다. v2를 더 정확하다고 해석하거나 공개 승격 근거로 사용하지 않는다.

## 재현 기준

- 코드 commit: `7899d6bbbebe91a42751de8cd72f0a9de347377c`
- baseline: `v1-idw-point`
- challenger: `v2-polygon-shadow`
- geometry: `oa-21285-2026-04-02-make-valid-v1`
- snapshot as-of: `2026-07-11T15:55:00Z`
- active cafes: 4,933
- local DB SHA-256: `bdce186700bfde078ac26bc0b0a83a6abe4d30cecbf1ca99b6ccc5ccc26cd8eb`
- polygon ZIP SHA-256: `fda69cd2ee3812103931cfd0ef1a0146336f06a23b6e1c2e4f9e0653620262ac`
- master XLSX SHA-256: `60aedf332efef1535623e22c14af2acd6b3ccfa35e60423fbbea8cc8188f1ff7`
- DB write: 없음

```bash
cd backend
uv run python scripts/run_shadow_snapshot.py \
  --database-url sqlite+pysqlite:///data/preview.db
```

## 공식 geometry 검증

- 121개 공식 geometry를 모두 결합했다.
- normalization 후 Polygon 121개가 모두 valid였다.
- `POI070`만 `make_valid`가 필요했고 나머지는 원본 topology를 유지했다.
- 양의 면적으로 겹치는 polygon pair는 67개였다.
- 카페가 두 개 이상 polygon에 포함된 경우는 171곳이었다. v2는 이 경우 단일 영역을
  임의 선택하지 않고 모든 포함 영역을 동등 contributor로 보존한다. 동등 가중은 아직
  검증되지 않은 shadow 가설이다.

## Coverage 전환

| v1 coverage | v2 coverage | 카페 수 |
| --- | --- | ---: |
| covered | covered | 2,317 |
| covered | fringe | 0 |
| covered | uncovered | 0 |
| fringe | covered | 1,056 |
| fringe | fringe | 467 |
| fringe | uncovered | 0 |
| uncovered | covered | 16 |
| uncovered | fringe | 250 |
| uncovered | uncovered | 827 |

v2는 이 입력에서 coverage를 낮추지 않았고 1,322곳의 coverage를 높였다. 이는 공식
polygon의 면적을 반영한 구조적 결과일 뿐, 해당 확장이 정확하다는 증거는 아니다.

## Score와 표시 level 변화

| 항목 | 값 |
| --- | ---: |
| 양쪽 score 존재 | 3,840 |
| score 변경 | 137 |
| score 동일 | 3,703 |
| 평균 절대 score 차이 | 0.014549 |
| 최대 절대 score 차이 | 2.063372 |
| 양쪽 level 존재 | 3,840 |
| level 변경 | 29 |
| level 동일 | 3,811 |

## 상위 divergence 감사 목록

아래 목록은 절대 score 차이 내림차순, cafe ID 오름차순이다. ground truth가 없으므로
정확도 실패 목록이 아니라 현장 관측과 원인 분석의 우선순위다.

| ID | 카페 | v1 | v2 | 절대 차이 | overlap |
| ---: | --- | --- | --- | ---: | ---: |
| 3556 | 카페지니 | fringe / 3.716863 / L4 | covered / 1.653491 / L2 | 2.063372 | 0 |
| 3876 | Kenya Kiambu Coffee | fringe / 3.245193 / L3 | covered / 1.201593 / L1 | 2.043600 | 0 |
| 4060 | 할리스 | fringe / 3.723642 / L4 | covered / 1.791900 / L2 | 1.931741 | 0 |
| 4250 | Everything But The Hero | covered / 2.922586 / L3 | covered / 1.000000 / L1 | 1.922586 | 1 |
| 3810 | Saru | covered / 2.825418 / L3 | covered / 1.000000 / L1 | 1.825418 | 1 |
| 1999 | Tiger Espresso | covered / 3.016690 / L3 | covered / 1.325523 / L1 | 1.691167 | 0 |
| 2705 | 루나띵스 | covered / 3.089177 / L3 | covered / 1.524522 / L2 | 1.564655 | 0 |
| 4583 | Ohvenu Hannam | fringe / 1.000000 / L1 | fringe / 2.543853 / L3 | 1.543853 | 0 |
| 2269 | Standing Coffee | covered / 2.667575 / L3 | covered / 1.136971 / L1 | 1.530605 | 0 |
| 1708 | 와플팩토리 | covered / 2.795844 / L3 | covered / 1.305746 / L1 | 1.490098 | 0 |
| 4274 | Crate Coffee | fringe / 1.000000 / L1 | fringe / 2.393941 / L2 | 1.393941 | 0 |
| 1037 | Rain Report Gyeongridan | covered / 2.418741 / L2 | covered / 1.031330 / L1 | 1.387410 | 0 |
| 2796 | 스노잉 | covered / 3.076856 / L3 | covered / 1.696201 / L2 | 1.380656 | 0 |
| 897 | Starbucks | fringe / 1.000000 / L1 | fringe / 2.345509 / L2 | 1.345509 | 0 |
| 2893 | 크림커넥션 | covered / 3.116784 / L3 | covered / 1.836891 / L2 | 1.279893 | 0 |
| 4851 | 로코스 | covered / 2.229103 / L2 | covered / 1.000000 / L1 | 1.229103 | 1 |
| 2871 | Sugar Lane | covered / 3.099529 / L3 | covered / 1.893728 / L2 | 1.205801 | 0 |
| 2134 | 해방촌달카페 | covered / 1.987132 / L2 | covered / 1.000000 / L1 | 0.987132 | 1 |
| 3147 | 아베끄마망 | fringe / 1.000000 / L1 | fringe / 1.958025 / L2 | 0.958025 | 0 |
| 1575 | 블루스 카페 바 | covered / 1.912712 / L2 | covered / 1.090113 / L1 | 0.822599 | 0 |

상위 차이는 이태원·한남·해방촌 주변에 집중됐다. 기존 홍대·성수 Phase 6 기준선의
지표 선택을 결과 확인 뒤 바꾸지 않는다. 이 목록은 별도 targeted audit로만 사용해
사후 선택 편향을 방지한다.

## 다음 gate

1. 기존 Phase 6 primary 기준선은 그대로 수집한다.
2. 상위 divergence 목록은 별도 HUMAN 현장 감사로 관측한다.
3. 동일 primary row와 historical snapshot으로 v1/v2 paired 평가를 실행한다.
4. 전체 지표 기준선 통과, coverage loss 0, covered/fringe 구간 회귀 없음, fringe 개선
   근거가 모두 있어야 v2 승격을 검토한다.
5. 통과 전까지 public materialization, DB schema와 API는 `v1-idw-point`를 유지한다.
