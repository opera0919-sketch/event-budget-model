"""예산 산출 — 조정 가능한 3대 레버.

지급대상자 = 신청자 × goal_achievement(목표 달성률)
예산      = 지급대상자 × reward_payout_rate(리워드 지급률) × avg_reward(평균 리워드)
총예산    = 예산 + 제세공과금 + 고정비
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BudgetResult:
    applicants: float
    recipients: float          # 지급대상자
    reward_cost: float         # 순수 리워드 지급액
    tax: float                 # 제세공과금
    fixed_costs: float
    total: float               # 총예산


def compute_budget(applicants: float, goal_achievement: float,
                   reward_payout_rate: float, avg_reward: float,
                   fixed_costs: float = 0.0, tax_rate: float = 0.0) -> BudgetResult:
    recipients = applicants * goal_achievement * reward_payout_rate
    reward_cost = recipients * avg_reward
    tax = reward_cost * tax_rate
    total = reward_cost + tax + fixed_costs
    return BudgetResult(
        applicants=applicants,
        recipients=recipients,
        reward_cost=reward_cost,
        tax=tax,
        fixed_costs=fixed_costs,
        total=total,
    )


def payout_from_funnel(conversion_rate: float, condition_rate: float) -> float:
    """리워드 지급률 = 전환율(순입금/신청) × 조건충족률(당첨/순입금)."""
    return conversion_rate * condition_rate


def compute_budget_funnel(applicants: float, conversion_rate: float,
                          condition_rate: float, avg_reward: float,
                          goal_achievement: float = 1.0,
                          fixed_costs: float = 0.0, tax_rate: float = 0.0) -> BudgetResult:
    """순입금·조건충족 '전원지급'형 이벤트 예산(연금저축 이벤트 실제 구조).

    지급대상자(당첨=조건충족자) = 신청 × goal × 전환율 × 조건충족률.
    리워드는 조건 충족자 전원에게 지급(추첨 없음). avg_reward는 제세공과금 포함 단가이면
    tax_rate=0. 반환 recipients는 '당첨(조건충족)고객수'에 해당.
    """
    payout = payout_from_funnel(conversion_rate, condition_rate)
    return compute_budget(applicants, goal_achievement, payout, avg_reward,
                          fixed_costs, tax_rate)


def compute_payout_tiered(recipients: float, tiers: list[dict],
                          cap_type: str = "all", cap_value: float | None = None,
                          tax_rate: float = 0.0, fixed_costs: float = 0.0) -> float:
    """(고급) 티어·캡형 리워드 총액.

    tiers: [{share, reward}, ...]  share 합≈1.
    cap_type: all | first_come(대상=min(recipients,N)) | draw(N명 고정) | budget_cap(원 상한)
    """
    if cap_type == "draw" and cap_value is not None:
        # 추첨: 당첨 N명 × (가중평균 리워드)
        avg = sum(t["share"] * t["reward"] for t in tiers)
        payout = cap_value * avg
    else:
        eff = recipients
        if cap_type == "first_come" and cap_value is not None:
            eff = min(recipients, cap_value)
        payout = sum(eff * t["share"] * t["reward"] for t in tiers)

    payout = payout * (1 + tax_rate) + fixed_costs
    if cap_type == "budget_cap" and cap_value is not None:
        payout = min(payout, cap_value)
    return payout
