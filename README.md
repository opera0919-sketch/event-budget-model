# event-budget-model

증권사(삼성 POP 등) 마케팅 이벤트의 **신청 고객 수**와 **소요 예산**을 회차별로 사전 추정하는 모델.

- 상위 퍼널(노출→도달→참여)이 관측 불가하므로, **기준 고객수 대비 신청 비율(take-rate)** 로
  신청자를 **간접 추정**한다.
- 미래 회차는 기준 고객수도 공란이라 **2단계 예측**(기준 고객수 투영 → 시즌 take-rate)한다.
- 예산은 **조정 가능한 3대 레버**(목표 달성률·리워드 지급률·평균 리워드 금액)로 시나리오 테스트한다.

## 빠른 시작

```bash
pip install -r requirements.txt

# 신청자·예산 예측 + 한국어 리포트 생성
python -m event_budget.cli estimate events/2026_pension_irp.yaml

# 레버 grid 시나리오 스윕
python -m event_budget.cli scenario events/2026_pension_irp.yaml \
    --goal 0.8,1.0,1.2 --payout 0.6,0.7,0.8 --reward 40000,50000

# 백테스트(사후예측 정확도·밴드 커버리지 검증)
python -m event_budget.cli backtest events/2026_pension_irp.yaml --min-train 3

# 스트레스 테스트(리워드 고정 · 신청자수 × 이전금액구간 믹스 2축)
python -m event_budget.cli stress events/2026_pension_transfer_stress.yaml
python scripts/build_stress_xlsx.py        # 엑셀 보고서

# 26.5~7월 홀드아웃 검증(신청자·예산 단가를 그 시점 데이터만으로 재예측해 실측 대조)
PYTHONPATH=src python scripts/validate_holdout.py --write

pytest tests/
```

## 정확도 강화 옵션

- **시장·거시 지표 결합**(#2): `data/market.csv`에 거래대금·지수·변동성을 채우고
  `params/benchmarks.yaml`의 `market.*_elasticity`를 >0으로 설정하면 장세 효과 반영(기본 OFF).
- **분모 분해**(#4): 명세의 `base_mode`를 `total_end`/`start`/`net_new`로 바꿔 take-rate 분모 선택.
- **백테스트**(#7): `backtest` 명령으로 MAPE·편향·밴드 커버리지 측정 → `min_cv` 등 보정.
- **지급 퍼널형 예산**: 순입금·조건충족 전원지급 이벤트는 명세에 `conversion_rate`(전환율)·
  `condition_rate`(조건충족률)를 지정하면 `예산 = 신청 × 전환율 × 조건충족률 × 리워드`로 계산.
  실적 보정값은 `data/reference_deposit_events.csv`(10회차) 참조 — 전환율 62%·조건충족 45%.

## 스트레스 테스트 (리워드 현행 유지 시)

리워드 수준을 **현재안대로 고정**한 채 두 축만 흔들어 예산 소요와 worst case를 확인한다.

- 축1 **신청 고객 수**: 0.8배(감소) ~ 1.0배(유지) ~ 1.8배(증가, 연말효과)
- 축2 **타사이전금액 구간 비율**: 연말 일시 대량입금 고객 유입 → 상위 구간 비중 확대

구간표는 `params/reward_tiers.yaml`에 있고 두 규칙을 반영한다 —
**1천만원 이상 ×1.5배 실적 인정**, **리워드 5만원 이상 제세 gross-up(÷0.78)**.

금액 구간 분포는 **가정이 아니라 실측**이다(`data/transfer_amount_distribution.csv`,
이벤트 10회차 · 구간별 고객 수 · 총 신청 100,058명). 지급률도 이 분포에서 나온다 —
리워드 대상 19,920명 ÷ 신청 100,058명 = **19.91%**.
축2의 눈금 역시 관측된 회차 간 변동 그대로 — θ=±1이 관측 최고·최저 회차를 재현한다.

> 실측 확인: 유일한 12월 단독 회차가 10회차 중 **최저**다. '연말에 대량입금이 몰린다'는
> 가정은 이 데이터로 지지되지 않아, 축2 상향은 계절 효과가 아닌 일반 상방 리스크로 읽는다.

## 구조

```
data/history.csv        회차별 실적(기준 고객수·신청자수). 미래 회차는 공란.
data/transfer_amount_distribution.csv  타사수관금액 구간별 고객 수(실측 10회차).
data/market.csv         (선택) 시장 지표.
params/benchmarks.yaml  시즌 take-rate 사전값·레버 기본값.
params/reward_tiers.yaml 타사이전금액 구간별 리워드 테이블(1.5배 인정·제세 규칙 포함).
events/*.yaml           회차별 이벤트 명세(1 회차 = 1 파일).
schema/event_schema.yaml 명세 필드 정의.
src/event_budget/       엔진: schema/demand/budget/scenario/simulate/calibrate/report/cli
                        + tiers(구간 리워드)/stress(2축 스트레스 테스트).
scripts/build_stress_xlsx.py  스트레스 결과 → 엑셀 보고서.
scripts/validate_holdout.py   26.5~7월 홀드아웃 검증 → reports/2026_05-07_예측검증.md.
reports/*.md            생성된 한국어 리포트.
docs/methodology.md     변수 정의·모델 수식·운영 루프(상세).
tests/                  pytest.
```

## 회차 추가

1. `data/history.csv`에 새 회차 실적 추가(예측 대상 회차는 공란).
2. `events/<회차>.yaml` 명세 작성(`avg_reward` 등 리워드 조건 입력).
3. `estimate`/`scenario` 실행 → `reports/`에 리포트 생성.
4. 이벤트 종료 후 실적을 채우면 다음 회차 take-rate가 자동 보정됨.

자세한 방법론은 [`docs/methodology.md`](docs/methodology.md) 참조.
