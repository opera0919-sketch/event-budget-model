"""신청자 추정 로직 검증."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from event_budget import demand
from event_budget.schema import Product, load_benchmarks, load_history


def _history():
    return load_history("data/history.csv")


def _bench():
    return load_benchmarks()


PENSION = Product(name="pension", label="연금저축", base_col_end="pension_end",
                  applicants_col="pension_applicants", avg_reward=40000)


def test_project_base_uses_season_growth():
    hist = _history()
    proj = demand.project_base(hist, "pension_end", ["2026_03", "2026_04", "2027_01"])
    # 마지막 실적(2026_02=1,836,227)보다 크고 단조 증가
    assert proj["2026_03"] > 1_836_227
    assert proj["2026_04"] > proj["2026_03"]
    assert proj["2027_01"] > proj["2026_04"]
    # 검증된 투영값 근사(±1%)
    assert abs(proj["2026_03"] - 1_909_026) / 1_909_026 < 0.01
    assert abs(proj["2026_04"] - 2_051_354) / 2_051_354 < 0.01


def test_pilot_round_excluded_from_take_rate():
    hist = _history()
    bench = _bench()
    exclude = set(bench.get("exclude_rounds", []))
    assert "2024_03" in exclude
    seas = demand.season_take_rates(hist, "pension_end", "pension_applicants", exclude)
    rounds = [r for lst in seas.values() for r, _ in lst]
    assert "2024_03" not in rounds


def test_predict_bands_ordered():
    hist = _history()
    bench = _bench()
    fc = demand.predict_product(hist, PENSION, bench, ["2026_03", "2026_04", "2027_01"])
    for rnd in fc.rounds:
        cons, base, opt = fc.applicants[rnd]
        assert cons < base < opt                 # 밴드 정렬
        tcons, tbase, topt = fc.take_rate[rnd]
        assert tcons < tbase < topt


def test_predict_matches_validated_numbers():
    hist = _history()
    bench = _bench()
    fc = demand.predict_product(hist, PENSION, bench, ["2026_04"])
    _, base, _ = fc.applicants["2026_04"]
    assert abs(base - 25_306) / 25_306 < 0.01     # 검증된 기준 신청자


def test_nowcast_logistic_extrapolates_up():
    # 진행률 50%에서 누적 1만명이면 최종은 1만보다 큼
    final = demand.nowcast_applicants(10_000, 0.5, curve="logistic")
    assert final > 10_000
    # linear 50% → 정확히 2배
    assert abs(demand.nowcast_applicants(10_000, 0.5, curve="linear") - 20_000) < 1e-6
    # 진행률 100% → 그대로
    assert abs(demand.nowcast_applicants(10_000, 1.0, curve="logistic") - 10_000) < 1.0
