import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from event_budget import stress
from event_budget.tiers import (average_distribution, credited_to_actual, gross_cost,
                                load_amount_distribution, load_empirical_tier_model,
                                load_tier_model)

TIER_KEY = "pension_transfer_2026"


@pytest.fixture(scope="module")
def model():
    """실측 금액 분포 기반 모델 — 기본 경로."""
    return load_empirical_tier_model(TIER_KEY)


@pytest.fixture(scope="module")
def legacy():
    """치환 이전의 로그정규 가정 모델 — 대조군으로만 남아 있다."""
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
    for shift in [-1.5, -1.0, 0.0, 0.5, 1.0, 1.5]:
        assert sum(model.shares(shift)) == pytest.approx(1.0)


def test_tilt_moves_mass_to_high_reward_tiers(model):
    """기울기를 올리면 고액 리워드 구간(15만원 이상) 비중이 커진다.

    단, 최저 구간 비중은 거의 움직이지 않는다 — 실측에서 회차 간 변동은 대상자 내부
    믹스가 아니라 '리워드 대상 비율'에 거의 전부 몰려 있기 때문이다(payout_rate 테스트 참조).
    """
    base, high = model.shares(0.0), model.shares(1.0)
    hi_idx = [i for i, r in enumerate(model.rewards) if r >= 150_000]
    assert sum(high[i] for i in hi_idx) > sum(base[i] for i in hi_idx)
    assert high[1] < base[1]      # 1천만~3천만(4만원) 비중 감소


def test_avg_cost_monotonic_in_shift(model):
    costs = [model.avg_budget_cost(s) for s in [-1.0, -0.5, 0.0, 0.5, 1.0, 1.5]]
    assert costs == sorted(costs)


def test_legacy_calibration_reproduces_observed_unit_cost(legacy):
    """대조군 회귀 테스트 — 로그정규 가정판은 실측 단가 12.7665만원에 맞춰져 있었다."""
    assert legacy.avg_budget_cost(0.0) == pytest.approx(127_665, rel=0.001)


def test_legacy_condition_rate_anchor_holds(legacy):
    """대조군 앵커: P(이전금액 >= 5백만) = 51%."""
    from math import log
    from statistics import NormalDist
    p = 1 - NormalDist().cdf((log(legacy.floor) - legacy.mu) / legacy.sigma)
    assert p == pytest.approx(0.51, abs=0.005)


def test_mean_deposit_within_observed_range(model):
    """대상자 평균 수관금액 — 최저 구간 하한(5백만) 위, 최상위 구간(3억) 아래."""
    assert 20_000_000 < model.mean_deposit(0.0) < 100_000_000


def test_gross_avg_exceeds_net_avg(model):
    assert model.avg_budget_cost(0.0) > model.avg_reward(0.0)


# --- 스트레스 매트릭스 -----------------------------------------------------

def test_matrix_covers_full_grid(model):
    cells = stress.stress_matrix(10_000, model, mults=[0.8, 1.0, 1.8],
                                 shifts=[-1.0, 0.0, 1.0])
    assert len(cells) == 9


def test_total_scales_linearly_with_applicant_multiplier(model):
    cells = {(c["mult"], c["shift"]): c
             for c in stress.stress_matrix(10_000, model, mults=[1.0, 1.8],
                                           shifts=[0.0])}
    base = cells[(1.0, 0.0)]["total"]
    assert cells[(1.8, 0.0)]["total"] == pytest.approx(base * 1.8)


def test_base_cell_matches_hand_calculation(model):
    c = stress.cell(10_000, 1.0, 0.0, model)
    expected = 10_000 * model.payout_rate(0.0)
    assert c["recipients"] == pytest.approx(expected)
    assert c["total"] == pytest.approx(expected * model.avg_budget_cost(0.0))


def test_worst_case_is_max_mult_and_shift(model):
    cells = stress.stress_matrix(10_000, model, mults=[0.8, 1.0, 1.8],
                                 shifts=[-1.0, 0.0, 1.0])
    w = stress.worst_case(cells)
    assert (w["mult"], w["shift"]) == (1.8, 1.0)


# --- 몬테카를로 ------------------------------------------------------------

def test_montecarlo_quantiles_ordered(model):
    mc = stress.stress_montecarlo(10_000, model, n=4000)
    assert mc["p5"] < mc["p50"] < mc["p90"] < mc["p95"] < mc["p99"]


def test_montecarlo_sits_between_base_and_worst_case(model):
    """분포는 기준셀과 worst case 사이에 놓인다.

    P50이 기준셀보다 높은 것은 정상이다. 신청배수(0.8/1.0/1.8)와 기울기(-1/0/+1.5)가
    모두 우측으로 치우친 삼각분포라 중앙값이 최빈값보다 위에 형성되기 때문 —
    '상방 리스크가 하방보다 크다'는 시나리오 설정을 그대로 반영한 결과다.
    """
    base = stress.cell(10_000, 1.0, 0.0, model)["total"]
    worst = stress.cell(10_000, 1.8, 1.5, model)["total"]
    mc = stress.stress_montecarlo(10_000, model, n=20000)
    assert base < mc["p50"] < mc["p99"] < worst


def test_montecarlo_p50_tracks_expected_multiplier(model):
    """P50 ≈ 기준셀 × (배수 평균 1.2) × (기울기 평균 0.167의 신청1인당 배율)."""
    base = stress.cell(10_000, 1.0, 0.0, model)["total"]
    theta_mean = (-1.0 + 0.0 + 1.5) / 3
    expected = base * 1.2 * (model.per_applicant(theta_mean) / model.per_applicant(0.0))
    mc = stress.stress_montecarlo(10_000, model, n=20000)
    assert mc["p50"] == pytest.approx(expected, rel=0.15)


def test_portfolio_diversifies_below_comonotonic_sum(model):
    per_round = {
        f"r{i}": stress.stress_montecarlo(10_000, model, n=8000, seed=42 + i)
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
        load_empirical_tier_model("does_not_exist")


def test_avg_cost_matches_manual_share_weighting(model):
    shares = model.shares(0.5)
    manual = sum(s * g for s, g in zip(shares, model.gross))
    assert model.avg_budget_cost(0.5) == pytest.approx(manual)


# --- 실측 금액 분포 -------------------------------------------------------

def test_distribution_rows_sum_to_one():
    for r in load_amount_distribution():
        assert sum(r["shares"]) == pytest.approx(1.0, abs=0.002), r["event_no"]


def test_average_distribution_excludes_immature():
    rows = load_amount_distribution()
    avg = average_distribution(rows, mature_only=True)
    assert sum(avg) == pytest.approx(1.0)
    # 미성숙 회차(1647)는 100% 가 최저 구간 → 포함하면 최저 구간 비중이 올라간다
    with_all = average_distribution(rows, mature_only=False)
    assert with_all[0] > avg[0]


def test_payout_rate_matches_observed_eligible_share(model):
    """지급률 = 1 - (5백만원 미만 비중). 실측 10회차 평균 21.19%."""
    avg = average_distribution(load_amount_distribution())
    assert model.payout_rate(0.0) == pytest.approx(1 - avg[0], abs=0.001)
    assert model.payout_rate(0.0) == pytest.approx(0.2119, abs=0.002)


def test_empirical_unit_cost_far_above_legacy(model, legacy):
    """실측 분포는 저액 구간 비중이 훨씬 작아 단가가 크게 높다."""
    assert model.avg_budget_cost(0.0) > 2 * 1e5
    assert model.avg_budget_cost(0.0) > legacy.avg_budget_cost(0.0)
    assert model.shares(0.0)[0] < legacy.shares(0.0)[0]


def test_per_applicant_budget_reconciles_with_legacy(model, legacy):
    """분해는 달라도 신청 1인당 예산은 20% 이내로 수렴한다."""
    old = 0.639 * 0.508 * legacy.avg_budget_cost(0.0)
    assert model.per_applicant(0.0) == pytest.approx(old, rel=0.2)


def test_theta_endpoints_reproduce_observed_events(model):
    """theta=±1 이 관측 최고·최저 회차를 그대로 재현해야 눈금이 의미를 갖는다."""
    from event_budget.tiers import event_tier_models
    evs = event_tier_models(TIER_KEY)
    hi = evs[model.tilt_hi_event]
    lo = evs[model.tilt_lo_event]
    assert model.per_applicant(1.0) == pytest.approx(hi.per_applicant(0.0), rel=0.001)
    assert model.per_applicant(-1.0) == pytest.approx(lo.per_applicant(0.0), rel=0.001)


def test_per_applicant_monotonic_in_theta(model):
    vals = [model.per_applicant(t) for t in [-1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5]]
    assert vals == sorted(vals)


def test_payout_rate_rises_with_theta(model):
    """대량입금 유입이 늘면 5백만원 문턱을 넘는 고객도 함께 늘어야 한다."""
    assert model.payout_rate(-1.0) < model.payout_rate(0.0) < model.payout_rate(1.0)


def test_december_event_is_the_observed_low(model):
    """연말효과 가정과 반대 — 유일한 12월 단독 회차(1536)가 관측 최저다."""
    assert model.tilt_lo_event == "1536"


def test_within_bracket_split_uses_log_uniform():
    """실제 2,000만원(=인정 3,000만원)이 6만원 구간의 경계여야 한다."""
    m = load_empirical_tier_model(TIER_KEY)
    idx = next(i for i in range(len(m.rewards))
               if m.edges[i] <= 20_000_000 < m.edges[i + 1])
    assert m.rewards[idx] == 60_000
    assert m.edges[idx] == 20_000_000


def test_event_scenarios_sorted_and_complete():
    from event_budget.tiers import event_tier_models
    rows = stress.event_scenarios(10_000, event_tier_models(TIER_KEY))
    assert len(rows) == 10
    assert [r["total"] for r in rows] == sorted(r["total"] for r in rows)


def test_equivalent_shift_roundtrips(model):
    for theta in [-0.8, 0.0, 0.7]:
        target = model.per_applicant(theta)
        assert stress.equivalent_shift(model, target) == pytest.approx(theta, abs=0.01)
