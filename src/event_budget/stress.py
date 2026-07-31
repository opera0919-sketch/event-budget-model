"""스트레스 테스트 — 리워드 현행 유지 시 신청자수·이전금액구간 믹스 2축 시나리오.

리워드 수준(구간별 금액)은 고정한 채 두 축만 흔든다.
  축1 신청 배수(mult):   0.8(감소) ~ 1.0(유지) ~ 1.8(증가, 연말효과)
  축2 믹스 기울기(theta): 타사수관금액 구간 분포를 관측된 변동 방향으로 기울인 정도

축2의 눈금은 실측(data/transfer_amount_distribution.csv)에서 그대로 가져온다.
  theta=0  → 성숙 10회차 평균
  theta=1  → 관측 최고 회차(1470/202509)
  theta=-1 → 관측 최저 회차(1536/202512)
  |theta|>1 → 같은 방향 외삽(관측 범위 초과 스트레스)

예산 = 신청 × mult × 목표달성률 × 지급률(theta) × Σ(구간비율(theta) × 구간 예산반영액).
실측 분포 모델에서는 지급률도 theta 에 따라 움직인다 — 대량입금 고객이 늘면 5백만원
문턱을 넘는 고객이 함께 늘기 때문이며, 이것이 유입 증가의 실제 작동 방식이다.

구간별 제세가 조건부라 일괄 세율로 표현되지 않으므로, 구간 리워드에 제세를 미리 반영한
값을 budget.compute_payout_tiered 에 넣고 tax_rate=0 으로 호출한다.
"""
from __future__ import annotations

import numpy as np

from .budget import compute_payout_tiered
from .simulate import _beta_from_mean_cv, _tri

DEFAULT_MULTS = [0.8, 0.9, 1.0, 1.2, 1.5, 1.8]

# 실측 관측 범위가 곧 눈금(theta=±1) + 범위 초과 스트레스(1.5)
DEFAULT_SHIFTS = [-1.0, 0.0, 0.5, 1.0, 1.5]

SHIFT_LABELS = {
    -1.0: "관측최저",
    0.0: "실측평균",
    0.5: "중간상향",
    1.0: "관측최고",
    1.5: "범위초과",
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
    return SHIFT_LABELS.get(round(shift, 4), f"θ={shift:+.1f}")


def shift_tick(shift: float) -> str:
    """표 머리글용 눈금 표기."""
    return f"θ={shift:+.1f}"


def mult_label(mult: float) -> str:
    return MULT_LABELS.get(round(mult, 4), f"x{mult:.2f}")


def resolve_payout(model, shift: float, payout_rate: float | None) -> float:
    """지급률 — 명시값이 있으면 그것, 없으면 분포에서 유도(실측 모델만 가능)."""
    if payout_rate is not None:
        return payout_rate
    if hasattr(model, "payout_rate"):
        return model.payout_rate(shift)
    raise ValueError("payout_rate 를 지정하거나 payout_rate(shift) 를 가진 모델을 써라")


def cell(base_applicants: float, mult: float, shift: float, model,
         payout_rate: float | None = None, goal: float = 1.0,
         fixed_costs: float = 0.0) -> dict:
    """단일 시나리오 셀 — 신청 배수 × 믹스 시프트 조합의 예산."""
    payout = resolve_payout(model, shift, payout_rate)
    applicants = base_applicants * mult
    recipients = applicants * goal * payout
    total = compute_payout_tiered(recipients, model.payout_tiers(shift),
                                  tax_rate=0.0, fixed_costs=fixed_costs)
    return {
        "mult": mult,
        "shift": shift,
        "mult_label": mult_label(mult),
        "shift_label": shift_label(shift),
        "applicants": applicants,
        "payout_rate": payout,
        "recipients": recipients,
        "avg_cost": model.avg_budget_cost(shift),
        "mean_deposit": model.mean_deposit(shift),
        "total": total,
    }


def stress_matrix(base_applicants: float, model,
                  payout_rate: float | None = None,
                  mults: list[float] | None = None,
                  shifts: list[float] | None = None,
                  goal: float = 1.0, fixed_costs: float = 0.0) -> list[dict]:
    """2축 시나리오 매트릭스(신청 배수 × 믹스 시프트)."""
    mults = DEFAULT_MULTS if mults is None else mults
    shifts = DEFAULT_SHIFTS if shifts is None else shifts
    return [cell(base_applicants, m, s, model, payout_rate, goal, fixed_costs)
            for m in mults for s in shifts]


def worst_case(cells: list[dict]) -> dict:
    """매트릭스 최악 셀(총예산 최대)."""
    return max(cells, key=lambda c: c["total"])


def event_scenarios(base_applicants: float, models: dict, mult: float = 1.0,
                    goal: float = 1.0, fixed_costs: float = 0.0) -> list[dict]:
    """회차별 실측 분포를 그대로 쓴 시나리오 — 가정 없이 관측된 믹스만으로 예산 산출."""
    out = []
    for name, m in models.items():
        payout = m.payout_rate(0.0)
        recipients = base_applicants * mult * goal * payout
        out.append({
            "event": name,
            "payout_rate": payout,
            "avg_cost": m.avg_budget_cost(0.0),
            "per_applicant": payout * m.avg_budget_cost(0.0),
            "recipients": recipients,
            "total": compute_payout_tiered(recipients, m.payout_tiers(0.0),
                                           tax_rate=0.0, fixed_costs=fixed_costs),
        })
    out.sort(key=lambda r: r["total"])
    return out


def equivalent_shift(model, target_per_applicant: float,
                     lo: float = -3.0, hi: float = 3.0) -> float:
    """신청 1인당 예산이 target 이 되는 기울기 — 관측 회차를 theta 눈금으로 환산."""
    for _ in range(200):
        mid = (lo + hi) / 2
        if model.payout_rate(mid) * model.avg_budget_cost(mid) < target_per_applicant:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _shift_interpolators(model, shift_lo: float, shift_hi: float, knots: int = 201):
    """shift → (평균 단가, 지급률) 보간기.

    표본마다 구간 적분을 다시 돌리면 느리다. 두 값 모두 shift 에 대해 매끄러워
    격자 계산 후 선형보간해도 오차가 무시할 수준이다.
    """
    grid = np.linspace(shift_lo, shift_hi, knots)
    cost = np.array([model.avg_budget_cost(float(s)) for s in grid])
    has_rate = hasattr(model, "payout_rate")
    rate = (np.array([model.payout_rate(float(s)) for s in grid]) if has_rate
            else np.ones_like(grid))
    return grid, cost, rate


def _lognormal_noise(cv: float, n: int, rng) -> np.ndarray:
    """평균 1, 변동계수 cv 인 곱셈 잡음. 지급률처럼 양수 비율에 곱해 쓴다."""
    if cv <= 0:
        return np.ones(n)
    sigma = np.sqrt(np.log(1.0 + cv * cv))
    return rng.lognormal(-sigma * sigma / 2, sigma, n)


def stress_montecarlo(base_applicants: float, model,
                      payout_rate: float | None = None, payout_cv: float = 0.22,
                      mult_band: tuple[float, float, float] = (0.8, 1.0, 1.8),
                      shift_band: tuple[float, float, float] = (-1.0, 0.0, 1.5),
                      goal_band: tuple[float, float, float] = (1.0, 1.0, 1.0),
                      fixed_costs: float = 0.0,
                      n: int = 40000, seed: int = 42) -> dict:
    """신청 배수·믹스 시프트·지급률을 동시에 흔든 총예산 분포.

    mult/shift/goal 은 삼각분포(lo, mode, hi). 기울기 밴드 기본값은 관측 최저(-1)에서
    관측 범위를 5할 초과하는 상방(+1.5)까지이고 최빈값은 10회차 평균(0)이다 —
    임의 가정이 아니라 데이터가 눈금이다.

    지급률은 실측 모델이면 시프트에서 유도한 뒤 잔여 불확실성만 곱셈 잡음으로 얹고,
    고정 지급률이면 Beta(평균, CV)로 흔든다.
    """
    rng = np.random.default_rng(seed)

    mult = _tri(mult_band[0], mult_band[1], mult_band[2], n, rng)
    shift = _tri(shift_band[0], shift_band[1], shift_band[2], n, rng)
    goal = _tri(goal_band[0], goal_band[1], goal_band[2], n, rng)

    grid, cost_grid, rate_grid = _shift_interpolators(model, shift_band[0], shift_band[2])
    avg_cost = np.interp(shift, grid, cost_grid)

    if payout_rate is None:
        payout = np.interp(shift, grid, rate_grid) * _lognormal_noise(payout_cv, n, rng)
        payout = np.clip(payout, 1e-6, 1.0)
    else:
        payout = _beta_from_mean_cv(payout_rate, max(payout_cv, 1e-6), n, rng)

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
        "payout_mean": float(payout.mean()),
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
