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


def _holdout(product_idx: int):
    spec = load_spec("events/2026_pension_irp.yaml")
    history = load_history(spec.history_csv)
    raw = load_benchmarks()
    market = load_market(spec.market_csv)
    idx = next(i for i, r in enumerate(history) if r["round"] == "2026_02")
    p = spec.products[product_idx]

    trunc = backtest._blank_from(history, p, idx)
    fc = demand.predict_product(trunc, p, apply_calibration(trunc, [p], raw),
                                ["2026_02"], market)
    cons, base, opt = fc.applicants["2026_02"]
    row = history[idx]
    return {
        "cons": cons, "pred": base, "opt": opt, "actual": row[p.applicants_col],
        "base_pred": fc.base["2026_02"], "base_actual": row[p.base_col_end],
        "tr_pred": fc.take_rate["2026_02"][1],
        "tr_actual": row[p.applicants_col] / row[p.base_col_end],
    }


def test_holdout_2026_02_pension_is_accurate():
    """연금저축 26.5~7월은 -1% 안으로 맞고 밴드 안에 들었다."""
    r = _holdout(0)
    assert abs(r["pred"] - r["actual"]) / r["actual"] < 0.02
    assert r["cons"] <= r["actual"] <= r["opt"]


def test_holdout_2026_02_irp_misses_low_and_out_of_band():
    """IRP 는 같은 구조에서 크게 과소예측하고 밴드를 벗어났다."""
    r = _holdout(1)
    err = (r["pred"] - r["actual"]) / r["actual"]
    assert -0.32 < err < -0.20
    assert r["actual"] > r["opt"], "실적이 낙관 밴드 위 — 커버리지 실패"


def test_holdout_2026_02_base_projection_is_sound_for_both():
    """두 상품 모두 Stage1 기준고객수는 3% 안 — 오차는 Stage2 take-rate 몫이다."""
    for i in (0, 1):
        r = _holdout(i)
        assert abs(r["base_pred"] - r["base_actual"]) / r["base_actual"] < 0.03
    irp = _holdout(1)
    assert abs(irp["tr_pred"] - irp["tr_actual"]) / irp["tr_actual"] > 0.20


def test_holdout_1607_unit_cost_matches_actual():
    """유효 검증 표본 — 현재 지급 조건으로 종료된 1607 에서 단가가 ±10% 안으로 맞는다.

    1607 이전 회차는 리워드 지급 조건이 달라 구간모델 검증에 쓸 수 없다
    (data/reference_deposit_events.csv 주석 참조). 그래서 표본은 이 한 건뿐이다.
    """
    cfg = tiers.load_tier_table(KEY)
    rows, ref = _pairs()
    i = next(i for i, r in enumerate(rows) if r["event_no"] == "1607")
    m = _model(cfg, [r for j, r in enumerate(rows) if j != i])   # 1607 제외 학습
    actual_pa = float(ref[i]["budget_eok"]) * 1e8 / float(ref[i]["applicants"])
    assert abs(m.per_applicant() - actual_pa) / actual_pa < 0.10


def test_pre_1607_rounds_are_not_used_for_calibration():
    """조건 변경 전 회차는 현재 구간표와 수준이 다르다 — 보정계수로 쓰면 안 된다."""
    cfg = tiers.load_tier_table(KEY)
    rows, ref = _pairs()
    i = next(i for i, r in enumerate(rows) if r["event_no"] == "1607")
    ratio = [float(rr["budget_eok"]) * 1e8 / float(rr["applicants"])
             / _model(cfg, [r]).per_applicant() for r, rr in zip(rows, ref)]
    assert ratio[i] > max(ratio[:i] + ratio[i + 1:]), \
        "현재 조건 회차가 가장 높아야 한다 — 이전 회차 수준으로 보정하면 과소편성된다"


def test_own_distribution_explains_actual_unit_cost():
    """자체 분포로 계산한 단가는 실측과 강하게 상관한다.

    지급 조건이 달랐던 회차까지 포함해도 방향은 같다 — 분포→예산 계산 구조가
    틀리지 않았다는 약한 방증. 수준(level)까지 맞을 이유는 없다.
    """
    cfg = tiers.load_tier_table(KEY)
    rows, ref = _pairs()
    own = [_model(cfg, [r]).per_applicant() for r in rows]
    act = [float(rr["budget_eok"]) * 1e8 / float(rr["applicants"]) for rr in ref]
    assert st.correlation(own, act) > 0.85


def test_applicant_populations_agree_across_sources():
    """기간이 정확히 겹치는 2026_02/1607 에서 세 파일의 신청자가 1% 안으로 맞는다.

    기준셀이 history 신청자 × 이벤트 기반 단가로 계산되므로, 두 계열이 같은 모집단을
    세고 있어야 곱이 성립한다.
    """
    history = load_history("data/history.csv")
    hist = next(r for r in history if r["round"] == "2026_02")["pension_applicants"]
    ev = next(r for r in _reference_rows() if r["period"].startswith("2026-05"))
    dist = next(r for r in tiers.load_amount_distribution()
                if r["event_no"] == "1607")["applicants"]

    assert float(ev["applicants"]) == hist
    assert abs(dist - hist) / hist < 0.01, "금액분포는 종료 직전 스냅샷이라 소폭 차이"
