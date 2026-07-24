# 이벤트 신청고객수·예산 예측 방법론

증권사(삼성 POP 등) 마케팅 이벤트의 **신청 고객 수**와 **소요 예산**을 회차별로
사전 추정하기 위한 모델. 매 분석 회차의 이벤트를 독립된 명세 파일(`events/*.yaml`)로
관리하고, 공통 엔진이 실적 데이터로 예측한다.

---

## 1. 왜 이런 구조인가

이벤트 예산 = (신청 고객 수) × (지급 조건·리워드). 그런데:

1. **상위 퍼널(노출→도달→참여)의 단계 수치는 관측 불가**하다. 노출수·도달수·단계별
   전환율을 알 수 없으므로 이를 곱으로 모델링하지 않는다. 대신 **관측 가능한 기준 고객수
   대비 신청 비율(take-rate)** 로 신청자를 직접 추정한다(reduced-form).
2. 예측 대상 미래 회차는 **신청자수뿐 아니라 기준 고객수도 공란**이다. 따라서 기준
   고객수를 먼저 투영하는 **2단계 예측**이 필요하다.
3. 지급 조건·리워드 금액은 이벤트마다 다르고 아직 확정 전일 수 있으므로, **분석가가
   조정하는 레버**로 두어 시나리오 테스트가 가능해야 한다.

---

## 2. 신청 고객 수 — 2단계 간접 추정

### Stage 1. 기준 고객수 투영 (`demand.project_base`)
연금저축·IRP의 **종료연월 고객수** 시계열을 회차 전이별 평균 성장률로 순차 투영한다.
성장에는 계절성이 있어(연말정산 시기 유입↑) **도착 시즌별 평균 성장률**을 쓴다.

```
성장률(시즌 s) = mean( 고객수_t / 고객수_{t-1} )   # t의 시즌이 s인 전이들의 평균
투영            = 마지막 실적 × 성장률 연쇄 (중간 회차를 건너뛰지 않고 연쇄)
```

시즌 코드: `1=_01(2~4월)`, `2=_02(5~7월)`, `3=_03(8~10월)`, `4=_04(11~1월, 연말정산 성수기)`.

### Stage 2. 시즌 take-rate 적용 (`demand.predict_product`)
```
take_rate      = 신청고객수 / 기준고객수(종료연월)
기준 take_rate = blend_recent_weight × (최근 N회차 평균)
               + (1 − blend_recent_weight) × (해당 시즌 평균)
신청자(기준)   = 투영 기준고객수 × 기준 take_rate
밴드           = 기준 × (1 ± CV)      # CV = 시즌 관측 변동계수, 하한 min_cv
```

- **시즌 평균**으로 계절성을, **최근추세**로 프로그램 성숙에 따른 상승을 함께 반영.
- **파일럿/초기 회차(예: 2024_03)** 는 take-rate 산정에서 제외(`benchmarks.exclude_rounds`).
- 실적이 없는 시즌은 `params/benchmarks.yaml`의 사전값으로 대체.

### 보정 (`calibrate.py`)
`data/history.csv`에 실적이 쌓이면 상품·시즌별 take-rate 평균으로 벤치마크 사전값을
덮어쓴다. 회차가 누적될수록 사전값 의존도가 낮아지고 정확도가 오른다.

### (선택) 진행 중 재추정 (`demand.nowcast_applicants`)
이벤트 진행 중이면 진행률 대비 누적 신청자를 포화 곡선(로지스틱/선형)으로 외삽해
최종 신청자를 갱신한다 → 예산 조기 경보.

### 시장·거시 지표 결합 (#2, `demand.market_multiplier`)
`data/market.csv`에 **KOSPI 월별 실제 데이터**(KRX 주가지수 추이(월), 기준월말일자별)를 채워 두었다.
`kospi_close`(월말 종가)·`kospi_trade`(월 거래 원자료). KOSDAQ은 중요도 낮아 미포함.
`benchmarks.yaml`의 `market.index_elasticity`/`trade_elasticity`를 >0으로 설정하면 활성화된다.
```
market_mult(회차) = Π (지표 / 학습기간평균)^탄력성
```
- 학습(실적) 회차의 take-rate를 **시장중립화**(관측rate ÷ market_mult)한 뒤, 예측 회차의
  시장 시나리오 배수를 다시 곱한다 → 장세 효과를 이중계상 없이 반영.
- **탄력성 0(기본)이면 배수=1.0 → 결과 불변**(OFF).
- 예측 회차(미래)의 시장 지표는 KRX에 없으므로 시나리오 가정으로 입력(없으면 중립).

**실증(현재 데이터, n=7)**: 연금저축 take-rate ↔ KOSPI종가 상관 **+0.59**(약한 양),
IRP ↔ KOSPI거래 **−0.68**(잡음·역상관, 2026_01 이상치 영향). 표본이 작고 신호가 혼재해
**탄력성 기본 OFF 유지**를 권고 — 회차 누적 후 회귀로 탄력성을 추정해 켜는 것이 안전.
주의: `2026_02`(2026-07)은 스냅샷 시점 진행 중 월이라 `kospi_trade`가 과소.

### 분모 분해 (#4, `Product.base_mode`)
take-rate 분모 선택: `total_end`(종료 고객수, 기본) · `start`(시작 고객수) · `net_new`(순증=end−start).
`net_new`/`start`는 `base_col_start` 필요. 어느 분모가 take-rate를 더 안정적으로 만드는지는
백테스트(§7)로 비교해 고른다. 신규/기존 완전 분리는 별도 데이터 컬럼 필요(향후 확장).

### 지급갭 실측 (#4, `calibrate.calibrate_payout_rate`)
`Product.payout_rate_col`(회차별 실지급률 = 실지급/신청) 실적이 있으면 평균을 계산해
`reward_payout_rate` 기준 레버를 대체한다. 없으면 가정값(레버) 사용.

### 백테스트·밴드 검증 (#7, `backtest.py`)
각 실적 회차를 **그 이전 데이터만**으로 2단계 예측(누수 방지: t 이후 공란 처리 + 절단 이력 보정)해
실제와 비교 → **MAPE·편향(bias)·밴드 커버리지** 산출. `cli backtest`로 실행.
- 편향 양(+) = 과소예측. 커버리지가 목표보다 낮으면 제안 CV로 `min_cv`를 넓혀 밴드 신뢰도 보정.
- base_mode·시장결합 설정을 바꿔가며 어느 구성이 MAPE가 낮은지 선택하는 기준으로 사용.

---

## 3. 예산 — 조정 가능한 3대 레버 (`budget.compute_budget`)

```
지급대상자 = 신청자 × goal_achievement (목표 달성률)
예산       = 지급대상자 × reward_payout_rate (리워드 지급률) × avg_reward (평균 리워드)
총예산     = 예산 + 제세공과금(reward × tax_rate) + 고정비
```

| 레버 | 의미 | 예시값 |
|---|---|---|
| `goal_achievement` | 목표 달성률(예측 신청자 대비 배수) | 0.8 / 1.0 / 1.2 |
| `reward_payout_rate` | 리워드 지급률(신청자 중 실지급 비율) | 0.6 / 0.7 / 0.8 |
| `avg_reward` | 평균 리워드 금액(원/인) | 이벤트별 지정 |

- `tax_rate`: 현금성 0.0, 경품·상품권 0.22(제세공과금).
- **시나리오 테스트**: `scenario.scenario_grid`로 세 레버 전 조합 예산 매트릭스,
  `scenario.three_scenario`로 보수/기준/낙관 정렬 시나리오, `scenario.tornado`로 민감도.

### (고급) 티어·캡형 리워드 (`budget.compute_payout_tiered`)
단일 평균리워드 대신 티어별 정밀 산정이 필요할 때. 캡 유형:
`all`(전원) · `first_come`(선착순 N) · `draw`(추첨 N 고정) · `budget_cap`(예산 상한).

### (선택) 몬테카를로 (`simulate.run_montecarlo`)
take-rate·레버에 분포를 부여해 총예산 분포를 산출하고 P50/P90/P95를 반환.
편성 안전마진(P90~P95) 근거로 사용.

---

## 4. 사용법

```bash
pip install -r requirements.txt

# 신청자·기준예산 예측 + 리포트 생성
python -m event_budget.cli estimate events/2026_pension_irp.yaml

# 레버 grid 시나리오 스윕
python -m event_budget.cli scenario events/2026_pension_irp.yaml \
    --goal 0.8,1.0,1.2 --payout 0.6,0.7,0.8 --reward 40000,50000

# 백테스트(사후예측 정확도·밴드 커버리지 검증)
python -m event_budget.cli backtest events/2026_pension_irp.yaml --min-train 3 --target 0.8

pytest tests/
```

산출물: 콘솔 요약 + `reports/<event_id>.md` 한국어 리포트.

---

## 5. 회차별 관리 · 운영 루프

1. 새 회차 실적을 `data/history.csv`에 추가(신청자·기준고객수).
2. 예측 대상 회차는 공란으로 두고 `events/<회차>.yaml` 명세 작성.
3. `estimate`/`scenario` 실행 → 리포트 검토.
4. 이벤트 종료 후 실적을 `history.csv`에 채워 넣으면 다음 회차 take-rate가 자동 보정됨.

파일이 회차별로 분리·버전관리되어 회차 이력이 git에 남는다.

---

## 6. 한계 · 백테스트가 드러낸 것

현재 데이터셋 백테스트(`cli backtest`) 결과:
- **연금저축**: MAPE ~19%, 편향 **+26%(과소예측)** — take-rate 상승추세를 블렌드가 다 못 따라감.
- **IRP**: MAPE ~44%(변동성 큼, 특히 2026_01 급락이 큰 오차).
- **밴드가 좁음**: 현재 `min_cv=0.15`로 커버리지 ~50% (목표 80%). 경험적 CV ≈ **0.30~0.35** 필요.
  → 편성 신뢰도를 위해 `min_cv`를 이 수준으로 넓히는 것을 권고.

개선 방향(우선순위):
1. **이벤트 오퍼 속성**(리워드 금액·조건 난이도·프로모션)을 take-rate feature로 → 최대 효과.
2. **시장 지표 실데이터** 채워 `market.*_elasticity` 활성화 → IRP 변동성 설명.
3. 시즌 표본 부족은 회차 누적으로 개선. `min_cv` 상향으로 밴드 신뢰도 즉시 보정.
4. 과소예측 편향은 `blend_recent_weight` 상향 또는 명시적 추세항 도입으로 완화.
