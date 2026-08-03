"""26.5~7월 홀드아웃 검증의 결론을 고정한다(scripts/validate_holdout.py).

여기서 깨지는 것은 대개 '모델을 고쳤다'는 뜻이므로, 실패하면 값을 갱신하고
reports/2026_05-07_예측검증.md 를 다시 생성해야 한다.
"""
import csv
import statistics as st

from event_budget import backtest, demand, tiers
from event_budget.calibrate import apply_calibration
from event_budget.schema import (_resolve, load_benchmarks, load_history,
                                 load_market, load_spec)

KEY = "pension_transfer_2026"


def _reference_rows():
    with open(_resolve("data/reference_deposit_events.csv"), encoding="utf-8") as f:
        reader = csv.DictReader(l for l in f if not l.lstrip().startswith("#"))
        return [r for r in reader if (r.get("round_label") or "").strip()]


def _pairs():
    rows = tiers.load_amount_distribution()
    ref = _reference_rows()
    assert len(rows) == len(ref), "분포 회차와 실적 회차가 1:1로 대응해야 한다"
    return rows, ref


def _model(cfg, rows):
    return tiers.build_empirical_tier_model(
        cfg, shares=tiers.average_distribution(rows, True), n_events=len(rows))


def test_holdout_2026_02_underpredicts_applicants():
    """26.5~7월 신청자는 과소예측이고 밴드 밖이었다 — 밴드 폭이 부족하다."""
    spec = load_spec("events/2026_pension_irp.yaml")
    history = load_history(spec.history_csv)
    raw = load_benchmarks()
    market = load_market(spec.market_csv)
    idx = next(i for i, r in enumerate(history) if r["round"] == "2026_02")
    p = spec.products[0]

    trunc = backtest._blank_from(history, p, idx)
    fc = demand.predict_product(trunc, p, apply_calibration(trunc, [p], raw),
                                ["2026_02"], market)
    cons, base, opt = fc.applicants["2026_02"]
    actual = history[idx][p.applicants_col]

    assert base < actual, "실적은 예측보다 컸다(과소예측)"
    assert -0.30 < (base - actual) / actual < -0.22
    assert actual > opt, "실적이 낙관 밴드 위 — 커버리지 실패"


def test_holdout_2026_02_error_is_take_rate_not_base():
    """Stage1 기준고객수는 맞고 오차는 Stage2 take-rate 에서 났다."""
    spec = load_spec("events/2026_pension_irp.yaml")
    history = load_history(spec.history_csv)
    raw = load_benchmarks()
    market = load_market(spec.market_csv)
    idx = next(i for i, r in enumerate(history) if r["round"] == "2026_02")
    p = spec.products[0]

    trunc = backtest._blank_from(history, p, idx)
    fc = demand.predict_product(trunc, p, apply_calibration(trunc, [p], raw),
                                ["2026_02"], market)
    base_err = abs(fc.base["2026_02"] - history[idx][p.base_col_end]) / history[idx][p.base_col_end]
    tr_actual = history[idx][p.applicants_col] / history[idx][p.base_col_end]
    tr_err = abs(fc.take_rate["2026_02"][1] - tr_actual) / tr_actual

    assert base_err < 0.03, "기준고객수 투영은 3% 안"
    assert tr_err > 0.20, "take-rate 오차가 지배적"


def test_budget_model_has_upward_bias_against_actuals():
    """구간모델 예산은 실측 집행예산보다 체계적으로 높다(LOO 편향 +40% 이상)."""
    cfg = tiers.load_tier_table(KEY)
    rows, ref = _pairs()
    errs = []
    for i, rr in enumerate(ref):
        m = _model(cfg, [r for j, r in enumerate(rows) if j != i])
        act = float(rr["budget_eok"]) * 1e8
        errs.append((float(rr["applicants"]) * m.per_applicant() - act) / act)
    assert st.mean(errs) > 0.40
    assert sum(1 for e in errs if e > 0) >= 9, "거의 모든 회차에서 과대예측"


def test_own_distribution_explains_actual_unit_cost():
    """회차 자체 분포는 실측 단가와 강하게 상관한다 — 계산식이 아니라 예측이 문제."""
    cfg = tiers.load_tier_table(KEY)
    rows, ref = _pairs()
    own = [_model(cfg, [r]).per_applicant() for r in rows]
    act = [float(rr["budget_eok"]) * 1e8 / float(rr["applicants"]) for rr in ref]
    assert st.correlation(own, act) > 0.85


def test_calibration_factor_is_stable_near_0_70():
    """실측/모델 비율이 회차별로 안정적이라 단일 계수 보정이 성립한다."""
    cfg = tiers.load_tier_table(KEY)
    rows, ref = _pairs()
    ratios = [float(rr["budget_eok"]) * 1e8 / float(rr["applicants"])
              / _model(cfg, [r]).per_applicant()
              for r, rr in zip(rows, ref)]
    assert 0.65 < st.mean(ratios) < 0.75
    assert st.stdev(ratios) / st.mean(ratios) < 0.20
    # 제세 gross-up 제거(×0.78)만으로는 설명되지 않는다
    assert st.mean(ratios) < 0.78 - 0.03


def test_applicant_populations_differ_between_sources():
    """history.csv 신청자와 이벤트 실적 신청자는 같은 기간에도 다르다."""
    history = load_history("data/history.csv")
    row = next(r for r in history if r["round"] == "2026_02")
    ref = {r["period"]: r for r in _reference_rows()}
    ev = ref["2026-05~07(진행중)"]
    ratio = float(ev["applicants"]) / row["pension_applicants"]
    assert 0.6 < ratio < 0.8, "두 계열을 곱셈으로 섞으면 이 비율만큼 어긋난다"
