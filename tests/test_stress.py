import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from event_budget import stress
from event_budget.tiers import (build_tier_model, credited_to_actual, gross_cost,
                                load_tier_model)

TIER_KEY = "pension_transfer_2026"


@pytest.fixture(scope="module")
def model():
    return load_tier_model(TIER_KEY)


# --- 규칙2: 제세 gross-up -------------------------------------------------

def test_gross_cost_under_threshold_is_unchanged():
    assert gross_cost(20_000, 50_000, 0.22) == 20_000
    assert gross_cost(40_000, 50_000, 0.22) == 40_000


def test_gross_cost_over_threshold_grosses_up():
    # 리워드 + (리워드/0.78)×0.22 == 리워드/0.78
    assert gross_cost(60_000, 50_000, 0.22) == pytest.approx(76_923.08, abs=0.5)
    assert gross_cost(150_000, 50_000, 0.22) == pytest.approx(192_307.69, abs=0.5)
    assert gross_cost(1_000_000, 50_000, 0.22) == pytest.approx(1_282_051.28, abs=0.5)
    r = 300_000
    assert gross_cost(r, 50_000, 0.22) == pytest.approx(r + (r / 0.78) * 0.22)


# --- 규칙1: 1천만원 이상 ×1.5배 실적 인정 ---------------------------------

def test_credited_to_actual_below_threshold_unchanged():
    assert credited_to_actual(5_000_000, 10_000_000, 1.5) == 5_000_000


def test_credited_to_actual_divides_by_multiplier():
    # 인정 3천만원 구간 하한 → 실제 2천만원 (사용자 예시: 2천만 입금 → 3천만 인정 → 6만원)
    assert credited_to_actual(30_000_000, 10_000_000, 1.5) == 20_000_000
    assert credited_to_actual(50_000_000, 10_000_000, 1.5) == pytest.approx(33_333_333, abs=1)
    assert credited_to_actual(300_000_000, 10_000_000, 1.5) == 200_000_000


def test_credited_to_actual_clamps_unreachable_band():
    # 인정 [1천만, 1.5천만)은 어떤 실제금액으로도 도달 불가 → 배수 적용 시작점으로 클램프
    assert credited_to_actual(10_000_000, 10_000_000, 1.5) == 10_000_000


def test_example_2000man_lands_in_60000_tier(model):
    """2,000만원 입금 → 3,000만원 인정 → 6만원 구간."""
    idx = next(i for i in range(len(model.rewards))
               if model.edges[i] <= 20_000_000 < model.edges[i + 1])
    assert model.rewards[idx] == 60_000


# --- 구간 비율과 믹스 시프트 ----------------------------------------------

def test_shares_sum_to_one(model):
    for shift in [0.0, 0.25, 0.5, 1.0, 1.5]:
        assert sum(model.shares(shift)) == pytest.approx(1.0)


def test_shift_moves_mass_upward(model):
    base = model.shares(0.0)
    high = model.shares(1.0)
    assert high[0] < base[0]      # 최저 구간 비중 감소
    assert high[-1] > base[-1]    # 최고 구간 비중 증가


def test_avg_cost_monotonic_in_shift(model):
    costs = [model.avg_budget_cost(s) for s in [0.0, 0.25, 0.5, 1.0, 1.5]]
    assert costs == sorted(costs)


def test_calibration_reproduces_observed_unit_cost(model):
    """회귀 테스트 — shift=0 평균 예산반영 단가가 최근 실측 12.7665만원을 재현."""
    assert model.avg_budget_cost(0.0) == pytest.approx(127_665, rel=0.001)


def test_calibration_condition_rate_anchor_holds(model):
    """조건충족률 앵커: P(이전금액 >= 5백만) = 51%."""
    from math import log
    from statistics import NormalDist
    p = 1 - NormalDist().cdf((log(model.floor) - model.mu) / model.sigma)
    assert p == pytest.approx(0.51, abs=0.005)


def test_mean_deposit_within_observed_range(model):
    """당첨자 평균 이전금액이 실적 순입금 평균(1,199~2,578만원)보다 크되 상식적 범위."""
    assert 25_000_000 < model.mean_deposit(0.0) < 50_000_000


def test_gross_avg_exceeds_net_avg(model):
    assert model.avg_budget_cost(0.0) > model.avg_reward(0.0)


# --- 스트레스 매트릭스 -----------------------------------------------------

def test_matrix_covers_full_grid(model):
    cells = stress.stress_matrix(10_000, model, 0.325,
                                 [0.8, 1.0, 1.8], [0.0, 0.5, 1.5])
    assert len(cells) == 9


def test_total_scales_linearly_with_applicant_multiplier(model):
    cells = {(c["mult"], c["shift"]): c
             for c in stress.stress_matrix(10_000, model, 0.325,
                                           [1.0, 1.8], [0.0])}
    base = cells[(1.0, 0.0)]["total"]
    assert cells[(1.8, 0.0)]["total"] == pytest.approx(base * 1.8)


def test_base_cell_matches_hand_calculation(model):
    c = stress.cell(10_000, 1.0, 0.0, 0.325, model)
    assert c["recipients"] == pytest.approx(3250.0)
    assert c["total"] == pytest.approx(3250.0 * model.avg_budget_cost(0.0))


def test_worst_case_is_max_mult_and_shift(model):
    cells = stress.stress_matrix(10_000, model, 0.325,
                                 [0.8, 1.0, 1.8], [0.0, 0.5, 1.5])
    w = stress.worst_case(cells)
    assert (w["mult"], w["shift"]) == (1.8, 1.5)


# --- 몬테카를로 ------------------------------------------------------------

def test_montecarlo_quantiles_ordered(model):
    mc = stress.stress_montecarlo(10_000, model, 0.325, n=4000)
    assert mc["p5"] < mc["p50"] < mc["p90"] < mc["p95"] < mc["p99"]


def test_montecarlo_sits_between_base_and_worst_case(model):
    """분포는 기준셀과 worst case 사이에 놓인다.

    P50이 기준셀보다 높은 것은 정상이다. 신청배수(0.8/1.0/1.8)와 시프트(0/0/1.5)가
    모두 우측으로 치우친 삼각분포라 중앙값이 최빈값보다 위에 형성되기 때문 —
    '상방 리스크가 하방보다 크다'는 시나리오 설정을 그대로 반영한 결과다.
    """
    base = stress.cell(10_000, 1.0, 0.0, 0.325, model)["total"]
    worst = stress.cell(10_000, 1.8, 1.5, 0.325, model)["total"]
    mc = stress.stress_montecarlo(10_000, model, 0.325, n=20000)
    assert base < mc["p50"] < mc["p99"] < worst


def test_montecarlo_p50_tracks_expected_multiplier(model):
    """P50 ≈ 기준셀 × (배수 평균 1.2) × (시프트 평균 0.5의 단가 배율)."""
    base = stress.cell(10_000, 1.0, 0.0, 0.325, model)["total"]
    expected = base * 1.2 * (model.avg_budget_cost(0.5) / model.avg_budget_cost(0.0))
    mc = stress.stress_montecarlo(10_000, model, 0.325, n=20000)
    assert mc["p50"] == pytest.approx(expected, rel=0.1)


def test_portfolio_diversifies_below_comonotonic_sum(model):
    per_round = {
        f"r{i}": stress.stress_montecarlo(10_000, model, 0.325, n=8000, seed=42 + i)
        for i in range(3)
    }
    pf = stress.portfolio_montecarlo(per_round)
    assert pf["p90"] < pf["comonotonic_p90"]
    assert pf["p50"] == pytest.approx(sum(r["p50"] for r in per_round.values()), rel=0.05)


# --- 구간표 구성 -----------------------------------------------------------

def test_build_tier_model_uses_credited_edges_for_actual(model):
    # 인정실적 경계는 원본 구간표 그대로, 실제금액 경계는 1천만 이상부터 축소
    assert model.credited_edges[:3] == [5_000_000, 10_000_000, 30_000_000]
    assert model.edges[2] == 20_000_000


def test_missing_tier_key_raises():
    with pytest.raises(ValueError):
        load_tier_model("does_not_exist")


def test_avg_cost_matches_manual_share_weighting(model):
    shares = model.shares(0.5)
    manual = sum(s * g for s, g in zip(shares, model.gross))
    assert model.avg_budget_cost(0.5) == pytest.approx(manual)
