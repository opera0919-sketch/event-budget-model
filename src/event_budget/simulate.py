"""몬테카를로 예산 분포.

take_rate·목표달성률·지급률·평균리워드에 분포를 부여해 총예산 분포를 산출하고
분위수(P50/P90/P95 등)를 반환한다. 결정론 시나리오를 넘어선 편성 안전마진 근거.
"""
from __future__ import annotations

import numpy as np


def _beta_from_mean_cv(mean: float, cv: float, size: int, rng) -> np.ndarray:
    """평균·변동계수로 Beta 표본(0~1 비율 변수용)."""
    mean = min(max(mean, 1e-6), 1 - 1e-6)
    var = (cv * mean) ** 2
    max_var = mean * (1 - mean) * 0.999
    var = min(var, max_var)
    if var <= 0:
        return np.full(size, mean)
    common = mean * (1 - mean) / var - 1
    a = mean * common
    b = (1 - mean) * common
    return rng.beta(a, b, size)


def _tri(lo: float, mode: float, hi: float, size: int, rng) -> np.ndarray:
    if lo == hi:
        return np.full(size, mode)
    return rng.triangular(lo, mode, hi, size)


def run_montecarlo(base_applicants: float, take_rate_band: tuple[float, float, float],
                   goal_band: tuple[float, float, float],
                   payout_mean: float, payout_cv: float,
                   avg_reward: float, reward_cv: float = 0.0,
                   base_customers: float | None = None,
                   fixed_costs: float = 0.0, tax_rate: float = 0.0,
                   n: int = 10000, seed: int = 42) -> dict:
    """총예산 분포.

    take_rate_band = (보수, 기준, 낙관). base_customers 주어지면 신청자 =
    기준고객수 × take_rate 표본; 아니면 base_applicants × (take/기준take) 스케일.
    goal_band = (lo, mode, hi) 삼각. payout = Beta(mean, cv). reward = 삼각(±cv).
    """
    rng = np.random.default_rng(seed)
    tr_lo, tr_mid, tr_hi = take_rate_band

    tr = _tri(tr_lo, tr_mid, tr_hi, n, rng)
    if base_customers is not None:
        applicants = base_customers * tr
    else:
        applicants = base_applicants * (tr / tr_mid)

    goal = _tri(goal_band[0], goal_band[1], goal_band[2], n, rng)
    payout = _beta_from_mean_cv(payout_mean, max(payout_cv, 1e-6), n, rng)
    if reward_cv > 0:
        reward = _tri(avg_reward * (1 - reward_cv), avg_reward,
                      avg_reward * (1 + reward_cv), n, rng)
    else:
        reward = np.full(n, avg_reward)

    recipients = applicants * goal * payout
    total = recipients * reward * (1 + tax_rate) + fixed_costs

    q = np.percentile(total, [5, 50, 90, 95, 99])
    return {
        "applicants_mean": float(applicants.mean()),
        "mean": float(total.mean()),
        "p5": float(q[0]),
        "p50": float(q[1]),
        "p90": float(q[2]),
        "p95": float(q[3]),
        "p99": float(q[4]),
        "max": float(total.max()),
        "samples": total,
    }
