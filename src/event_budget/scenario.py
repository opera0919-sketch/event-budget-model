"""시나리오 테스트 — 레버 grid 스윕과 민감도(tornado)."""
from __future__ import annotations

from itertools import product as iproduct

from .budget import compute_budget


def scenario_grid(applicants: float, goal_values: list[float],
                  payout_values: list[float], reward_values: list[float],
                  fixed_costs: float = 0.0, tax_rate: float = 0.0) -> list[dict]:
    """레버 조합별 예산 리스트.

    goal_achievement × reward_payout_rate × avg_reward 전 조합.
    """
    out = []
    for g, p, r in iproduct(goal_values, payout_values, reward_values):
        res = compute_budget(applicants, g, p, r, fixed_costs, tax_rate)
        out.append({
            "goal_achievement": g,
            "reward_payout_rate": p,
            "avg_reward": r,
            "recipients": res.recipients,
            "total": res.total,
        })
    return out


def three_scenario(applicants_by_band: tuple[float, float, float],
                   levers: dict, avg_reward: float,
                   fixed_costs: float = 0.0, tax_rate: float = 0.0) -> dict:
    """보수/기준/낙관 정렬 시나리오.

    applicants_by_band = (보수, 기준, 낙관) 신청자.
    levers[goal_achievement], levers[reward_payout_rate] = [보수, 기준, 낙관].
    같은 인덱스(보수↔보수)를 묶어 3개 대표 시나리오 예산 산출.
    """
    goals = levers.get("goal_achievement", [0.8, 1.0, 1.2])
    payouts = levers.get("reward_payout_rate", [0.6, 0.7, 0.8])
    names = ["보수", "기준", "낙관"]
    out = {}
    for i, nm in enumerate(names):
        a = applicants_by_band[i]
        res = compute_budget(a, goals[i], payouts[i], avg_reward, fixed_costs, tax_rate)
        out[nm] = {
            "applicants": a,
            "goal_achievement": goals[i],
            "reward_payout_rate": payouts[i],
            "avg_reward": avg_reward,
            "recipients": res.recipients,
            "total": res.total,
        }
    return out


def tornado(applicants: float, base_levers: dict, ranges: dict,
            fixed_costs: float = 0.0, tax_rate: float = 0.0) -> list[dict]:
    """각 레버를 (lo, hi)로 흔들 때 총예산 변화. 영향 큰 순 정렬.

    base_levers: {goal_achievement, reward_payout_rate, avg_reward, applicants_mult?}
    ranges: {변수: (lo, hi)}  변수는 base_levers 키 + 'applicants'(신청자 배수 가능).
    """
    g0 = base_levers["goal_achievement"]
    p0 = base_levers["reward_payout_rate"]
    r0 = base_levers["avg_reward"]
    base_total = compute_budget(applicants, g0, p0, r0, fixed_costs, tax_rate).total

    rows = []
    for var, (lo, hi) in ranges.items():
        def total_with(val):
            g, p, r, a = g0, p0, r0, applicants
            if var == "goal_achievement":
                g = val
            elif var == "reward_payout_rate":
                p = val
            elif var == "avg_reward":
                r = val
            elif var == "applicants":
                a = val
            return compute_budget(a, g, p, r, fixed_costs, tax_rate).total

        t_lo, t_hi = total_with(lo), total_with(hi)
        rows.append({
            "variable": var,
            "low": min(t_lo, t_hi),
            "high": max(t_lo, t_hi),
            "swing": abs(t_hi - t_lo),
        })
    rows.sort(key=lambda x: x["swing"], reverse=True)
    return {"base_total": base_total, "rows": rows}
