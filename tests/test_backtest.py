"""#7 백테스트 검증."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from event_budget import backtest
from event_budget.schema import Product, load_benchmarks, load_history


def _hist():
    return load_history("data/history.csv")


PENSION = Product(name="pension", label="연금저축", base_col_end="pension_end",
                  applicants_col="pension_applicants", avg_reward=40000)


def test_backtest_no_leakage_and_metrics():
    hist = _hist()
    raw = load_benchmarks()
    res = backtest.backtest_product(hist, PENSION, raw, min_train=3)
    assert len(res) >= 2
    # 파일럿(2024_03)은 평가 대상에서 제외
    assert all(r["round"] != "2024_03" for r in res)
    for r in res:
        assert r["actual"] > 0 and r["pred_base"] > 0
        assert r["ape"] >= 0
        assert r["pred_cons"] <= r["pred_base"] <= r["pred_opt"]
    s = backtest.summarize(res)
    assert 0 <= s["coverage"] <= 1
    assert s["mape"] >= 0
    assert s["n"] == len(res)


def test_backtest_predicts_only_from_prior():
    # 사후예측 값이 실제와 완전히 같지 않아야(미래 누수 없음을 방증)
    hist = _hist()
    raw = load_benchmarks()
    res = backtest.backtest_product(hist, PENSION, raw, min_train=3)
    assert any(r["pred_base"] != r["actual"] for r in res)


def test_min_train_filters_early_rounds():
    hist = _hist()
    raw = load_benchmarks()
    few = backtest.backtest_product(hist, PENSION, raw, min_train=3)
    many = backtest.backtest_product(hist, PENSION, raw, min_train=5)
    # 학습표본 요건이 크면 평가 회차가 더 적거나 같다
    assert len(many) <= len(few)


def test_suggested_cv_nonnegative():
    hist = _hist()
    raw = load_benchmarks()
    res = backtest.backtest_product(hist, PENSION, raw, min_train=3)
    cv = backtest.suggested_cv(res, target_coverage=0.8)
    assert cv is not None and cv >= 0
    assert backtest.suggested_cv([], 0.8) is None
