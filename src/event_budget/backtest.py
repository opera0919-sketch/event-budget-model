"""백테스트·밴드 검증 (#7).

각 실적 회차 t를 '그 이전 데이터만'으로 예측(2단계 전체 파이프라인)해 실제와 비교한다.
누수(leakage) 방지를 위해 t 이후 행의 기준고객수·신청자를 공란 처리하고, take-rate
보정도 절단된 이력으로만 수행한다. → MAPE·편향(bias)·밴드 커버리지 산출.
"""
from __future__ import annotations

from . import demand
from .calibrate import apply_calibration


def _blank_from(history: list[dict], product, cutoff_idx: int) -> list[dict]:
    """cutoff_idx(포함) 이후 행의 기준·신청자 컬럼을 공란 처리한 이력 사본."""
    cols = {product.base_col_end, product.applicants_col}
    if getattr(product, "base_col_start", None):
        cols.add(product.base_col_start)
    out = []
    for j, r in enumerate(history):
        rc = dict(r)
        if j >= cutoff_idx:
            for c in cols:
                if c in rc:
                    rc[c] = None
        out.append(rc)
    return out


def backtest_product(history: list[dict], product, base_benchmarks: dict,
                     market: dict | None = None, min_train: int = 4) -> list[dict]:
    """product의 회차별 사후예측 결과 리스트."""
    exclude = set(base_benchmarks.get("exclude_rounds", []))
    results = []
    for i, row in enumerate(history):
        rnd = row["round"]
        actual = row.get(product.applicants_col)
        if actual is None or actual <= 0 or rnd in exclude:
            continue
        prior = [h for h in history[:i]
                 if h.get(product.applicants_col) is not None and h["round"] not in exclude]
        if len(prior) < min_train:
            continue
        trunc = _blank_from(history, product, i)
        bench_t = apply_calibration(trunc, [product], base_benchmarks)
        try:
            fc = demand.predict_product(trunc, product, bench_t, [rnd], market)
        except (ValueError, KeyError):
            continue
        if rnd not in fc.applicants:
            continue
        cons, base, opt = fc.applicants[rnd]
        err_rel = (actual - base) / base          # 밴드 기준 상대오차
        results.append({
            "round": rnd, "actual": actual,
            "pred_cons": cons, "pred_base": base, "pred_opt": opt,
            "ape": abs(actual - base) / actual,    # 절대백분율오차
            "err_rel": err_rel,
            "in_band": cons <= actual <= opt,
        })
    return results


def summarize(results: list[dict]) -> dict:
    if not results:
        return {"n": 0}
    n = len(results)
    mape = sum(r["ape"] for r in results) / n
    bias = sum(r["err_rel"] for r in results) / n
    coverage = sum(1 for r in results if r["in_band"]) / n
    return {"n": n, "mape": mape, "bias": bias, "coverage": coverage}


def suggested_cv(results: list[dict], target_coverage: float = 0.8) -> float | None:
    """목표 커버리지를 맞추는 데 필요한 경험적 CV(밴드 반폭).

    |상대오차|의 target 분위수. 현재 min_cv와 비교해 밴드 조정 근거로 사용.
    """
    if not results:
        return None
    devs = sorted(abs(r["err_rel"]) for r in results)
    k = min(len(devs) - 1, int(round(target_coverage * (len(devs) - 1))))
    return devs[k]
