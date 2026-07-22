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


def apply_calibration(history: list[dict], products, benchmarks: dict) -> dict:
    """모든 product 를 보정해 benchmarks 사본에 반영."""
    out = copy.deepcopy(benchmarks)
    out.setdefault("take_rate", {})
    for p in products:
        out["take_rate"][p.name] = calibrate_take_rate(history, p, benchmarks)
    return out
