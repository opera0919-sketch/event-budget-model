"""스트레스 테스트 — 리워드 현행 유지 시 신청자수·이전금액구간 믹스 2축 시나리오.

리워드 수준(구간별 금액)은 고정한 채 두 축만 흔든다.
  축1 신청 배수(mult):  0.8(감소) ~ 1.0(유지) ~ 1.8(증가, 연말효과)
  축2 믹스 시프트(shift): 당첨자 이전금액 분포의 중앙값 상향률
                          (연말 일시 대량입금 고객 유입 → 상위 구간 비중 확대)

예산 = 신청 × mult × 목표달성률 × 지급률 × Σ(구간비율 × 구간 예산반영액).
구간별 제세가 조건부라 일괄 세율로 표현되지 않으므로, 구간 리워드에 제세를 미리 반영한
값을 budget.compute_payout_tiered 에 넣고 tax_rate=0 으로 호출한다.
"""
from __future__ import annotations

import numpy as np

from .budget import compute_payout_tiered
from .simulate import _beta_from_mean_cv, _tri
from .tiers import TierModel

DEFAULT_MULTS = [0.8, 0.9, 1.0, 1.2, 1.5, 1.8]
DEFAULT_SHIFTS = [0.0, 0.25, 0.5, 1.0, 1.5]

SHIFT_LABELS = {
    0.0: "현행유지",
    0.25: "완만",
    0.5: "중간",
    1.0: "강함",
    1.5: "극단",
}

MULT_LABELS = {
    0.8: "감소(-20%)",
    0.9: "소폭감소",
    1.0: "유지",
    1.2: "소폭증가",
    1.5: "증가",
    1.8: "연말급증",
}


def shift_label(shift: float) -> str:
    return SHIFT_LABELS.get(round(shift, 4), f"+{shift:.0%}")


def mult_label(mult: float) -> str:
    return MULT_LABELS.get(round(mult, 4), f"x{mult:.2f}")


def cell(base_applicants: float, mult: float, shift: float, payout_rate: float,
         model: TierModel, goal: float = 1.0, fixed_costs: float = 0.0) -> dict:
    """단일 시나리오 셀 — 신청 배수 × 믹스 시프트 조합의 예산."""
    applicants = base_applicants * mult
    recipients = applicants * goal * payout_rate
    total = compute_payout_tiered(recipients, model.payout_tiers(shift),
                                  tax_rate=0.0, fixed_costs=fixed_costs)
    return {
        "mult": mult,
        "shift": shift,
        "mult_label": mult_label(mult),
        "shift_label": shift_label(shift),
        "applicants": applicants,
        "recipients": recipients,
        "avg_cost": model.avg_budget_cost(shift),
        "mean_deposit": model.mean_deposit(shift),
        "total": total,
    }


def stress_matrix(base_applicants: float, model: TierModel, payout_rate: float,
                  mults: list[float] | None = None,
                  shifts: list[float] | None = None,
                  goal: float = 1.0, fixed_costs: float = 0.0) -> list[dict]:
    """2축 시나리오 매트릭스(신청 배수 × 믹스 시프트)."""
    mults = DEFAULT_MULTS if mults is None else mults
    shifts = DEFAULT_SHIFTS if shifts is None else shifts
    return [cell(base_applicants, m, s, payout_rate, model, goal, fixed_costs)
            for m in mults for s in shifts]


def worst_case(cells: list[dict]) -> dict:
    """매트릭스 최악 셀(총예산 최대)."""
    return max(cells, key=lambda c: c["total"])


def _avg_cost_interpolator(model: TierModel, shift_lo: float, shift_hi: float,
                           knots: int = 201):
    """shift → 평균 예산반영 단가 보간기.

    몬테카를로 표본마다 구간 적분을 다시 돌리면 느리다. 평균 단가는 shift 에 대해
    매끄럽고 단조라, 격자에서 계산해 선형보간해도 오차가 무시할 수준이다.
    """
    grid = np.linspace(shift_lo, shift_hi, knots)
    vals = np.array([model.avg_budget_cost(float(s)) for s in grid])
    return grid, vals


def stress_montecarlo(base_applicants: float, model: TierModel,
                      payout_mean: float, payout_cv: float = 0.22,
                      mult_band: tuple[float, float, float] = (0.8, 1.0, 1.8),
                      shift_band: tuple[float, float, float] = (0.0, 0.0, 1.5),
                      goal_band: tuple[float, float, float] = (1.0, 1.0, 1.0),
                      fixed_costs: float = 0.0,
                      n: int = 40000, seed: int = 42) -> dict:
    """신청 배수·믹스 시프트·지급률을 동시에 흔든 총예산 분포.

    mult/shift/goal 은 삼각분포(lo, mode, hi), 지급률은 Beta(평균, CV).
    shift 의 mode 를 0 으로 두면 '상향은 상방 리스크'라는 비대칭을 표현한다.
    """
    rng = np.random.default_rng(seed)

    mult = _tri(mult_band[0], mult_band[1], mult_band[2], n, rng)
    shift = _tri(shift_band[0], shift_band[1], shift_band[2], n, rng)
    goal = _tri(goal_band[0], goal_band[1], goal_band[2], n, rng)
    payout = _beta_from_mean_cv(payout_mean, max(payout_cv, 1e-6), n, rng)

    grid, vals = _avg_cost_interpolator(model, shift_band[0], shift_band[2])
    avg_cost = np.interp(shift, grid, vals)

    recipients = base_applicants * mult * goal * payout
    total = recipients * avg_cost + fixed_costs

    q = np.percentile(total, [5, 50, 90, 95, 99])
    return {
        "mean": float(total.mean()),
        "p5": float(q[0]),
        "p50": float(q[1]),
        "p90": float(q[2]),
        "p95": float(q[3]),
        "p99": float(q[4]),
        "max": float(total.max()),
        "recipients_mean": float(recipients.mean()),
        "avg_cost_mean": float(avg_cost.mean()),
        "samples": total,
    }


def portfolio_montecarlo(per_round: dict[str, dict]) -> dict:
    """회차별 MC 표본을 합산한 포트폴리오 분포.

    회차마다 다른 시드로 뽑았으므로 이 합산은 '충격이 회차별로 독립'인 경우다 → 분산효과가
    최대로 작동하는 하한. 반대 극단은 모든 회차가 같은 방향으로 함께 움직이는 경우(공통충격)
    이고, 그때 합산 P90 은 회차별 P90 의 단순합과 같다(comonotonic 상한).

    실제 예산 편성은 두 값 사이에 있다. 연말 대량입금처럼 회차를 가로지르는 충격이 클수록
    상한에 가까워지므로, 스트레스 테스트에서는 두 값을 모두 제시한다.
    """
    keys = list(per_round)
    total = np.zeros_like(per_round[keys[0]]["samples"])
    for k in keys:
        total = total + per_round[k]["samples"]
    q = np.percentile(total, [5, 50, 90, 95, 99])
    return {
        "mean": float(total.mean()),
        "p5": float(q[0]),
        "p50": float(q[1]),
        "p90": float(q[2]),          # 독립 가정(분산효과 최대) — 하한
        "p95": float(q[3]),
        "p99": float(q[4]),
        "max": float(total.max()),
        "comonotonic_p90": float(sum(per_round[k]["p90"] for k in keys)),  # 공통충격 상한
        "comonotonic_p95": float(sum(per_round[k]["p95"] for k in keys)),
        "samples": total,
    }
