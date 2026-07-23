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

pytest tests/
```

## 정확도 강화 옵션

- **시장·거시 지표 결합**(#2): `data/market.csv`에 거래대금·지수·변동성을 채우고
  `params/benchmarks.yaml`의 `market.*_elasticity`를 >0으로 설정하면 장세 효과 반영(기본 OFF).
- **분모 분해**(#4): 명세의 `base_mode`를 `total_end`/`start`/`net_new`로 바꿔 take-rate 분모 선택.
- **백테스트**(#7): `backtest` 명령으로 MAPE·편향·밴드 커버리지 측정 → `min_cv` 등 보정.

## 구조

```
data/history.csv        회차별 실적(기준 고객수·신청자수). 미래 회차는 공란.
data/market.csv         (선택) 시장 지표.
params/benchmarks.yaml  시즌 take-rate 사전값·레버 기본값.
events/*.yaml           회차별 이벤트 명세(1 회차 = 1 파일).
schema/event_schema.yaml 명세 필드 정의.
src/event_budget/       엔진: schema/demand/budget/scenario/simulate/calibrate/report/cli.
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
