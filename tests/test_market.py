"""#2 시장 결합 · #4 분모 분해 검증."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from event_budget import demand
from event_budget.schema import Product, load_benchmarks, load_history


def _hist():
    return load_history("data/history.csv")


PENSION = Product(name="pension", label="연금저축", base_col_end="pension_end",
                  applicants_col="pension_applicants", avg_reward=40000)


def test_market_multiplier_neutral_by_default():
    # 데이터/기준/설정 없으면 항상 1.0
    assert demand.market_multiplier(None) == 1.0
    assert demand.market_multiplier({"turnover_avg_bil": 20000}, None, None) == 1.0
    # 탄력성 0이면 값이 있어도 1.0
    mm = demand.market_multiplier({"turnover_avg_bil": 27000},
                                  {"turnover": 18000}, {"turnover_elasticity": 0.0})
    assert mm == 1.0


def test_market_multiplier_power_law():
    mm = demand.market_multiplier({"turnover_avg_bil": 27000},
                                  {"turnover": 18000}, {"turnover_elasticity": 0.3})
    assert abs(mm - (27000 / 18000) ** 0.3) < 1e-9
    assert mm > 1.0                      # 거래대금↑ → 배수>1


def test_market_off_gives_identical_prediction():
    hist = _hist()
    bench = load_benchmarks()
    fc_none = demand.predict_product(hist, PENSION, bench, ["2026_04"])
    fc_mkt = demand.predict_product(hist, PENSION, bench, ["2026_04"], market=None)
    assert fc_none.applicants["2026_04"] == fc_mkt.applicants["2026_04"]
    # 검증된 기준 신청자 유지
    assert abs(fc_none.applicants["2026_04"][1] - 26_132) / 26_132 < 0.01


def test_market_bull_raises_forecast():
    hist = _hist()
    bench = dict(load_benchmarks())
    bench["market"] = {"turnover_elasticity": 0.3}
    # 학습 회차 거래대금 균일 + 예측회차만 상승 → 배수>1 → 신청 증가
    market = {r["round"]: {"turnover_avg_bil": 18000}
              for r in hist if r.get("pension_applicants") is not None}
    market["2026_04"] = {"turnover_avg_bil": 27000}
    base = demand.predict_product(hist, PENSION, bench, ["2026_04"])
    bull = demand.predict_product(hist, PENSION, bench, ["2026_04"], market)
    assert bull.applicants["2026_04"][1] > base.applicants["2026_04"][1]


def test_base_mode_net_new():
    hist = _hist()
    bench = load_benchmarks()
    p = Product(name="pension", label="연금", base_col_end="pension_end",
                applicants_col="pension_applicants", avg_reward=40000,
                base_mode="net_new", base_col_start="pension_start")
    fc = demand.predict_product(hist, p, bench, ["2026_04"])
    # 순증 기준값은 총 고객수보다 훨씬 작다
    assert 0 < fc.base["2026_04"] < 1_000_000
    # 신청자 예측은 양수
    assert fc.applicants["2026_04"][1] > 0
