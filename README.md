# event-budget-model

연금 **타사이전 리워드 이벤트**의 참여·예산을 예측하고, 실무 적용 가능한
**최적 리워드 지급구조**를 도출하는 시뮬레이션 모델.

## 개요

- 타사이전금액 구간에 따라 리워드(혜택금액)를 지급하는 이벤트를 대상으로,
  가상 데이터를 생성하고 여러 리워드 케이스를 시뮬레이션해 **3가지 목표별
  최적 구조**를 도출한다.
  - **A. 예산 최소화** (매력도 하한 유지)
  - **B. 비용효율(ROI) 최대화** (예산 상한 내)
  - **C. 목표예산 달성** (현행 ≈75%)
- 순수 Python 표준 라이브러리로 코어를 구현(재현성·이식성). 엑셀/차트에만
  `openpyxl`·`matplotlib`을 선택적으로 사용한다.

## 핵심 가정

| 항목 | 내용 |
|---|---|
| 예산(제세 포함) | 리워드 ≥ 5만원이면 예산 = `(리워드/0.78)*0.22 + 리워드` (제세 22% 그로스업). 고객 수령액은 리워드 그대로 |
| 수요반응(탄력성) | 비선형 로지스틱 S-커브. 소폭 하향엔 **sticky(비탄력)**, 대폭 하향 시 참여 급감. 현행 대비 정규화 |
| 구간내 분포 | 순입금 구간 내부는 하한(최소 기준) 쪽으로 쏠린 **triangular** 분포 |
| 배수 참여 보너스 | 배수 적용 시 수혜 고객 참여율 가산(1.5배 +3%, 2.0배 +5%) |
| 리워드 단위 | ≤5만원 1만원 단위 / >5만원 5만원 단위, 구간별 **단조 증가** |
| 시뮬레이션 | 30회 반복(흥행 15 / 비흥행 15), 신청자 4,000~5,000명, 분포 진동 반영 |

## 실행

```bash
python scripts/run_generate_data.py   # data/ 에 30개 데이터셋 CSV 생성
python scripts/run_simulation.py      # 대표 케이스 시뮬레이션 -> results/case_metrics.csv
python scripts/run_optimize.py        # 2단계 최적화 -> 목표별 최적안 + Pareto + 한글 리포트
python scripts/build_excel.py         # 엑셀 워크북(+ Pareto 차트) 생성
python -m pytest tests/ -q            # 테스트 (또는: python -m unittest discover -s tests)
```

## 산출물

- `data/round_XX_{hit|flop}.csv` — 회차별 베이스 데이터(고객ID/타사이전금액/혜택금액)
- `results/case_metrics.csv` — 대표 케이스 지표
- `results/objective_optima.csv` — 목표별(A/B/C) 최적안 지표
- `results/pareto.csv`, `results/pareto.png` — 예산↔유치금액 Pareto frontier
- `results/simulation_workbook.xlsx` — 데이터셋/케이스/최적안/Pareto(차트) 워크북
- `report/recommendation_ko.md` — 한글 추천 리포트

## 구조

```
config/simulation_config.py   # 분포·티어·배수·탄력성·시나리오·시드
src/reward_engine.py          # 리워드/예산(제세) 계산, 단위·단조 헬퍼
src/distribution.py           # 분포 진동 + 구간내 triangular/beta 샘플링
src/data_generator.py         # 30개 데이터셋 생성
src/demand_model.py           # 로지스틱 탄력성 + 배수 참여 보너스
src/simulation.py             # 케이스 평가·KPI(예산/ROI/CPA/평균이전/worst-case)
src/optimizer.py              # 2단계 탐색(coarse→top20→fine), 목표별 선정, Pareto
src/cases.py                  # 대표 리워드 케이스
src/report.py                 # CSV·마크다운 리포트·민감도
src/viz.py                    # Pareto 차트(matplotlib)
scripts/                      # 실행 스크립트
tests/                        # 단위 테스트(40+)
```

## KPI 정의

- **예산**: 제세 포함 회사 부담액 · **효율**: 유치이전금액/예산(배)
- **ROI**: (유치이전금액−예산)/예산 × 100(%) · **CPA**: 예산/리워드 수령자
- **평균이전금액**: 유치이전금액/신청자 · **매력도지수**: 유치이전금액 vs 현행(%)
- **worst-case 예산**: 30회 중 최대/최소 예산
