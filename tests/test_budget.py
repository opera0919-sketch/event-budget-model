"""예산 레버·티어·캡·스윕 검증."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from event_budget.budget import compute_budget, compute_payout_tiered
from event_budget.scenario import scenario_grid, three_scenario, tornado


def test_compute_budget_matches_hand_calc():
    # 신청자 20,000 × 목표 1.0 × 지급률 0.7 × 4만원 = 5.6억
    res = compute_budget(20_000, 1.0, 0.7, 40_000)
    assert res.recipients == 14_000
    assert abs(res.total - 560_000_000) < 1e-3


def test_tax_and_fixed_costs():
    res = compute_budget(10_000, 1.0, 0.5, 50_000, fixed_costs=5_000_000, tax_rate=0.22)
    # 지급 = 5000 × 5만 = 2.5억; 세금 22% = 5500만; +고정 500만
    assert abs(res.reward_cost - 250_000_000) < 1e-3
    assert abs(res.tax - 55_000_000) < 1e-3
    assert abs(res.total - (250_000_000 + 55_000_000 + 5_000_000)) < 1e-3


def test_levers_are_multiplicative():
    base = compute_budget(10_000, 1.0, 0.7, 40_000).total
    # 지급률만 0.7→0.8: 총예산 8/7배
    hi = compute_budget(10_000, 1.0, 0.8, 40_000).total
    assert abs(hi / base - 0.8 / 0.7) < 1e-9


def test_tiered_first_come_cap():
    tiers = [{"share": 1.0, "reward": 30_000}]
    # 선착순 5,000명 캡: 대상 10,000이어도 5,000만 지급
    payout = compute_payout_tiered(10_000, tiers, cap_type="first_come", cap_value=5_000)
    assert abs(payout - 5_000 * 30_000) < 1e-3


def test_tiered_draw_is_fixed():
    tiers = [{"share": 0.5, "reward": 100_000}, {"share": 0.5, "reward": 50_000}]
    # 추첨 100명: 참여규모 무관, 100 × 가중평균(75,000)
    payout = compute_payout_tiered(999_999, tiers, cap_type="draw", cap_value=100)
    assert abs(payout - 100 * 75_000) < 1e-3


def test_budget_cap_limits_total():
    tiers = [{"share": 1.0, "reward": 100_000}]
    payout = compute_payout_tiered(10_000, tiers, cap_type="budget_cap",
                                   cap_value=500_000_000)
    assert payout == 500_000_000     # 10억 산정되지만 5억 상한


def test_scenario_grid_size_and_reproducible():
    g1 = scenario_grid(20_000, [0.8, 1.0, 1.2], [0.6, 0.7, 0.8], [40_000])
    g2 = scenario_grid(20_000, [0.8, 1.0, 1.2], [0.6, 0.7, 0.8], [40_000])
    assert len(g1) == 9                         # 3×3×1
    assert [r["total"] for r in g1] == [r["total"] for r in g2]


def test_three_scenario_ordered():
    sc = three_scenario((15_000, 20_000, 25_000),
                        {"goal_achievement": [0.8, 1.0, 1.2],
                         "reward_payout_rate": [0.6, 0.7, 0.8]}, 40_000)
    assert sc["보수"]["total"] < sc["기준"]["total"] < sc["낙관"]["total"]


def test_tornado_ranks_by_swing():
    t = tornado(20_000,
                {"goal_achievement": 1.0, "reward_payout_rate": 0.7, "avg_reward": 40_000},
                {"applicants": (15_000, 25_000),
                 "goal_achievement": (0.8, 1.2),
                 "reward_payout_rate": (0.6, 0.8),
                 "avg_reward": (32_000, 48_000)})
    swings = [r["swing"] for r in t["rows"]]
    assert swings == sorted(swings, reverse=True)   # 내림차순 정렬
