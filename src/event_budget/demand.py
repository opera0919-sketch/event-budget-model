"""신청 고객 수 2단계 간접 추정.

Stage 1: 기준 고객수(종료연월 고객수)를 회차 전이별 평균 성장률로 투영.
Stage 2: 신청자 = 기준 × take_rate. take_rate = 시즌평균 × 최근추세 블렌드,
         보수/기준/낙관 = 기준 × (1 ± CV).

상위 퍼널(노출→도달→참여)의 단계 수치가 관측 불가하므로, 관측 가능한
기준 고객수 대비 신청 비율(take_rate)로 직접 추정하는 reduced-form 접근.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class ProductForecast:
    product: str
    label: str
    rounds: list          # 회차 코드 순서
    base: dict            # round -> 투영 기준 고객수
    take_rate: dict       # round -> (cons, base, opt)
    applicants: dict      # round -> (cons, base, opt)


def _observed(history: list[dict], col: str) -> list[dict]:
    """해당 컬럼이 채워진(실적) 행만, 입력 순서 유지."""
    return [r for r in history if r.get(col) is not None]


def _forecast_rows(history: list[dict], col: str, forecast_rounds: list[str]) -> list[dict]:
    want = set(forecast_rounds)
    return [r for r in history if r["round"] in want]


# ---------- Stage 1: 기준 고객수 투영 ----------
def season_growth_factors(history: list[dict], col: str) -> dict:
    """도착 시즌별 (직전 회차 대비) 평균 성장률."""
    obs = _observed(history, col)
    by_season: dict[int, list[float]] = {}
    for i in range(1, len(obs)):
        prev, cur = obs[i - 1], obs[i]
        ratio = cur[col] / prev[col]
        by_season.setdefault(cur["season"], []).append(ratio)
    return {s: sum(v) / len(v) for s, v in by_season.items()}


def project_base(history: list[dict], col: str, forecast_rounds: list[str] | None = None) -> dict:
    """마지막 실적 이후 기준 고객수가 공란인 모든 회차를 시즌 성장률로 순차 투영.

    중간 회차를 건너뛰지 않도록(예: 2026_04는 2026_03을 거쳐 도달) 마지막 실적
    이후의 공란 회차 전체를 순서대로 연쇄 투영한다. 반환 dict는 그 전부를 담는다.
    """
    obs = _observed(history, col)
    if not obs:
        raise ValueError(f"기준 고객수 투영 불가: '{col}' 실적이 없음")
    factors = season_growth_factors(history, col)
    avg_factor = sum(factors.values()) / len(factors)

    last_idx = max(i for i, r in enumerate(history) if r.get(col) is not None)
    cur = history[last_idx][col]
    out: dict = {}
    for r in history[last_idx + 1:]:
        s = r["season"]
        f = factors.get(s, avg_factor)   # 해당 시즌 실적 없으면 전체 평균
        cur = cur * f
        out[r["round"]] = cur
    return out


# ---------- Stage 2: 시즌 take-rate ----------
def season_take_rates(history: list[dict], base_col: str, appl_col: str,
                      exclude: set[str]) -> dict:
    """시즌별 take_rate 관측 리스트: {season: [(round, rate), ...]}."""
    out: dict[int, list] = {}
    for r in history:
        if r["round"] in exclude:
            continue
        base, appl = r.get(base_col), r.get(appl_col)
        if base is None or appl is None:
            continue
        out.setdefault(r["season"], []).append((r["round"], appl / base))
    return out


def recent_take_rate(history: list[dict], base_col: str, appl_col: str,
                     exclude: set[str], window: int) -> float:
    """최근 window개 실적 회차의 take_rate 평균(최근추세)."""
    series = []
    for r in history:
        if r["round"] in exclude:
            continue
        base, appl = r.get(base_col), r.get(appl_col)
        if base is None or appl is None:
            continue
        series.append(appl / base)
    if not series:
        raise ValueError("take_rate 실적이 없어 최근추세를 계산할 수 없음")
    return sum(series[-window:]) / len(series[-window:])


def _std(xs: list[float]) -> float:
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def blended_take_rate(season: int, seas_rates: dict, recent: float,
                      blend_recent_weight: float, min_cv: float,
                      benchmark_season: dict | None,
                      single_obs_cv: float = 0.25) -> tuple[float, float, float]:
    """(보수, 기준, 낙관) take_rate.

    기준 = blend_recent_weight*recent + (1-w)*시즌평균.
    밴드 = 기준 × (1 ± CV).
    """
    rates = [v for _, v in seas_rates.get(season, [])]
    if rates:
        smean = sum(rates) / len(rates)
    elif benchmark_season and season in benchmark_season:
        smean = float(benchmark_season[season])   # 실적 없으면 벤치마크
    else:
        smean = recent

    base_tr = blend_recent_weight * recent + (1 - blend_recent_weight) * smean

    if len(rates) > 1:
        cv = _std(rates) / smean if smean else min_cv
    else:
        cv = single_obs_cv
    cv = max(cv, min_cv)

    return base_tr * (1 - cv), base_tr, base_tr * (1 + cv)


def market_multiplier(market_row: dict | None) -> float:
    """시장 지표 → 수요 배수. 현재는 중립(1.0) 기본.

    시장 지표 컬럼(거래대금·지수)이 충분히 쌓이면 calibrate에서 탄력성을 추정해
    여기서 배수를 반환하도록 확장한다.
    """
    return 1.0


def predict_product(history: list[dict], product, benchmarks: dict,
                    forecast_rounds: list[str]) -> ProductForecast:
    exclude = set(benchmarks.get("exclude_rounds", []))
    w = float(benchmarks.get("blend_recent_weight", 0.5))
    window = int(benchmarks.get("recent_window", 4))
    min_cv = float(benchmarks.get("min_cv", 0.15))
    bench_tr = (benchmarks.get("take_rate", {}) or {}).get(product.name, {})

    base = project_base(history, product.base_col_end, forecast_rounds)
    seas_rates = season_take_rates(history, product.base_col_end,
                                   product.applicants_col, exclude)
    recent = recent_take_rate(history, product.base_col_end,
                              product.applicants_col, exclude, window)

    fc = _forecast_rows(history, product.base_col_end, forecast_rounds)
    take = {}
    appl = {}
    for r in fc:
        rnd, s = r["round"], r["season"]
        tr = blended_take_rate(s, seas_rates, recent, w, min_cv, bench_tr)
        take[rnd] = tr
        b = base[rnd]
        appl[rnd] = tuple(b * t for t in tr)

    return ProductForecast(
        product=product.name, label=product.label,
        rounds=[r["round"] for r in fc], base=base,
        take_rate=take, applicants=appl,
    )


# ---------- 진행 중 재추정(nowcast) ----------
def nowcast_applicants(applicants_to_date: float, elapsed_ratio: float,
                       curve: str = "logistic", k: float = 8.0) -> float:
    """진행률 대비 누적 신청자로 최종 신청자 외삽.

    linear: 단순 비례(포화 없음). logistic: S자 포화(초반이 빠름).
    k: 로지스틱 급경사(클수록 초반 집중). 반환값 = 최종 신청자 추정.
    """
    e = min(max(elapsed_ratio, 1e-6), 1.0)
    if curve == "linear":
        frac = e
    elif curve == "logistic":
        # 표준 로지스틱을 [0,1]로 정규화한 누적 진행 비율
        def L(x):
            return 1.0 / (1.0 + math.exp(-k * (x - 0.5)))
        frac = (L(e) - L(0.0)) / (L(1.0) - L(0.0))
    else:
        raise ValueError(f"알 수 없는 curve: {curve}")
    if frac <= 0:
        return applicants_to_date
    return applicants_to_date / frac
