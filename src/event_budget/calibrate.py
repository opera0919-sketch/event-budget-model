"""실적으로 시즌 take-rate 보정.

data/history.csv 의 실적 회차로 상품·시즌별 take_rate 평균을 계산해
benchmarks(사전값)를 덮어쓴다. 실적이 없는 시즌은 벤치마크 값을 유지한다.
회차가 쌓일수록 이 보정이 사전값보다 정확해진다.
"""
from __future__ import annotations

import copy

from .demand import season_take_rates


def calibrate_take_rate(history: list[dict], product, benchmarks: dict) -> dict:
    """product.name 의 시즌별 take_rate 를 실적 평균으로 보정한 dict 반환."""
    exclude = set(benchmarks.get("exclude_rounds", []))
    seas = season_take_rates(history, product.base_col_end,
                             product.applicants_col, exclude)
    calibrated = {}
    bench = (benchmarks.get("take_rate", {}) or {}).get(product.name, {})
    seasons = set(seas) | set(bench)
    for s in seasons:
        rates = [v for _, v in seas.get(s, [])]
        if rates:
            calibrated[s] = sum(rates) / len(rates)
        elif s in bench:
            calibrated[s] = float(bench[s])
    return calibrated


def calibrate_funnel_rates(reference_rows: list[dict]) -> dict:
    """참고 실적으로 순입금·조건충족 이벤트의 지급 퍼널 비율을 보정.

    전환율(순입금/신청) · 조건충족률(당첨/순입금) · 지급률(당첨/신청) · 당첨 1인당 리워드(원).
    """
    if not reference_rows:
        return {}
    app = sum(r["applicants"] for r in reference_rows)
    dep = sum(r["deposit_customers"] for r in reference_rows)
    win = sum(r["condition_customers"] for r in reference_rows)
    bud = sum(r["budget_eok"] for r in reference_rows) * 1e8
    return {
        "conversion_rate": dep / app if app else None,
        "condition_rate": win / dep if dep else None,
        "payout_rate": win / app if app else None,
        "reward_per_winner": bud / win if win else None,
    }


def apply_calibration(history: list[dict], products, benchmarks: dict) -> dict:
    """모든 product 를 보정해 benchmarks 사본에 반영."""
    out = copy.deepcopy(benchmarks)
    out.setdefault("take_rate", {})
    for p in products:
        out["take_rate"][p.name] = calibrate_take_rate(history, p, benchmarks)
    return out


def calibrate_payout_rate(history: list[dict], product, exclude: set[str] | None = None):
    """(#4 지급갭 실측) product.payout_rate_col 실적이 있으면 평균 실지급률 반환.

    실지급률 = 실제 지급자 / 신청자. 값이 없으면 None → 레버(가정값) 사용.
    회차가 쌓이면 이 실측값이 reward_payout_rate 기준값을 대체한다.
    """
    col = getattr(product, "payout_rate_col", None)
    if not col:
        return None
    exclude = exclude or set()
    vals = [r[col] for r in history
            if r["round"] not in exclude and r.get(col) is not None]
    return sum(vals) / len(vals) if vals else None
