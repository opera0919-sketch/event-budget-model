"""리워드 구조 최적화 - 2단계 탐색.

1단계 거친(coarse) 탐색: 세그먼트 배율 × 배수 격자에서 후보 생성·평가.
2단계 촘촘한(fine) 탐색: 목표별 상위 20개 주변을 티어 단위로 국소 탐색.

목표:
  (A) 예산 최소화 (매력도 하한 제약)
  (B) 비용효율(ROI) 최대화 (예산 상한 제약)
  (C) 목표예산 달성 (현행 75% ± 허용, 매력도 최대)

부산물: 예산 vs 효율 Pareto frontier.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from config import simulation_config as C
from src.reward_engine import (
    CURRENT_STRUCTURE,
    RewardStructure,
    allowed_reward_levels,
    enforce_monotonic,
    is_valid_structure,
    snap_reward,
    step_reward,
)
from src.simulation import AggregateMetrics, DatasetCache, evaluate_structure

# 현행 티어 하한(인정금액 기준) 고정. 리워드 수준·배수만 탐색.
TIER_THRESHOLDS = tuple(t for t, _ in C.CURRENT_TIERS)
BASE_REWARDS = tuple(r for _, r in C.CURRENT_TIERS)
MAX_REWARD = 1_000_000

# 세그먼트: 저(0-1) / 중(2-3) / 고(4-6)
SEGMENTS = [(0, 2), (2, 4), (4, 7)]
COARSE_FACTORS = [0.5, 0.75, 1.0, 1.25]
MULTIPLIERS = [1.0, 1.5, 2.0]

Signature = Tuple[Tuple[int, ...], float, int]


def _signature(s: RewardStructure) -> Signature:
    return (s.rewards(), s.multiplier, s.multiplier_threshold)


def _make_structure(rewards: Sequence[int], multiplier: float, name: str) -> RewardStructure:
    rewards = enforce_monotonic(tuple(rewards))
    return RewardStructure(
        tiers=tuple(zip(TIER_THRESHOLDS, rewards)),
        multiplier=multiplier,
        multiplier_threshold=C.CURRENT_MULTIPLIER_THRESHOLD,
        name=name,
    )


# ---------------------------------------------------------------------------
# 후보 생성
# ---------------------------------------------------------------------------
def coarse_candidates() -> List[RewardStructure]:
    seen: set[Signature] = set()
    out: List[RewardStructure] = []
    for fl in COARSE_FACTORS:
        for fm in COARSE_FACTORS:
            for fh in COARSE_FACTORS:
                factors = {0: fl, 1: fl, 2: fm, 3: fm, 4: fh, 5: fh, 6: fh}
                rewards = [snap_reward(BASE_REWARDS[i] * factors[i]) for i in range(7)]
                for m in MULTIPLIERS:
                    s = _make_structure(rewards, m, f"C_{fl}_{fm}_{fh}_m{m}")
                    if not is_valid_structure(s):
                        continue
                    sig = _signature(s)
                    if sig in seen:
                        continue
                    seen.add(sig)
                    out.append(s)
    return out


def fine_neighbors(s: RewardStructure) -> List[RewardStructure]:
    """티어별 ±1 단위, 배수 이웃을 생성."""
    out: List[RewardStructure] = []
    rewards = list(s.rewards())
    for i in range(len(rewards)):
        for d in (-1, +1):
            nr = list(rewards)
            nr[i] = step_reward(nr[i], d, MAX_REWARD)
            out.append(_make_structure(nr, s.multiplier, f"F_{i}_{d}"))
    for m in MULTIPLIERS:
        if m != s.multiplier:
            out.append(_make_structure(rewards, m, f"F_m{m}"))
    return [c for c in out if is_valid_structure(c)]


# ---------------------------------------------------------------------------
# 평가 (메모이제이션)
# ---------------------------------------------------------------------------
class Evaluator:
    def __init__(self, caches: Sequence[DatasetCache]):
        self.caches = caches
        self.cache: Dict[Signature, AggregateMetrics] = {}

    def eval(self, s: RewardStructure) -> AggregateMetrics:
        sig = _signature(s)
        m = self.cache.get(sig)
        if m is None:
            m = evaluate_structure(self.caches, s)
            self.cache[sig] = m
        return m

    def all_results(self) -> List[Tuple[Signature, AggregateMetrics]]:
        return list(self.cache.items())


# ---------------------------------------------------------------------------
# 목표별 선정
# ---------------------------------------------------------------------------
@dataclass
class ObjectiveResult:
    objective: str
    structure: RewardStructure
    metrics: AggregateMetrics
    rationale: str


def _feasible_attractive(m: AggregateMetrics, floor: float) -> bool:
    return m.attractiveness_index >= floor


def select_min_budget(cand: List[Tuple[RewardStructure, AggregateMetrics]]) -> Tuple[RewardStructure, AggregateMetrics]:
    feas = [(s, m) for s, m in cand if _feasible_attractive(m, C.OBJ_A_ATTRACT_FLOOR)]
    feas = feas or cand  # 제약 만족 없으면 전체에서
    return min(feas, key=lambda sm: sm[1].budget_mean)


def select_max_efficiency(cand, current_budget) -> Tuple[RewardStructure, AggregateMetrics]:
    cap = current_budget * C.OBJ_B_BUDGET_CAP
    feas = [(s, m) for s, m in cand
            if m.budget_mean <= cap and _feasible_attractive(m, C.OBJ_MIN_ATTRACTIVENESS)]
    feas = feas or [(s, m) for s, m in cand if m.budget_mean <= cap] or cand
    return max(feas, key=lambda sm: sm[1].efficiency)


def select_target_budget(cand, current_budget) -> Tuple[RewardStructure, AggregateMetrics]:
    target = current_budget * C.OBJ_C_BUDGET_TARGET
    tol = current_budget * C.OBJ_C_BUDGET_TOL
    band = [(s, m) for s, m in cand
            if abs(m.budget_mean - target) <= tol and _feasible_attractive(m, C.OBJ_MIN_ATTRACTIVENESS)]
    if band:
        return max(band, key=lambda sm: sm[1].transfer_mean)
    # 밴드 밖이면 목표에 가장 가까운 것.
    return min(cand, key=lambda sm: abs(sm[1].budget_mean - target))


# ---------------------------------------------------------------------------
# Pareto frontier: 예산(최소) vs 유치 이전금액(최대)의 상충 관계.
#   (sticky 수요 하에서 예산-효율은 정렬되어 무의미하므로, 실제 의사결정 상충인
#    '지출 vs 유치'를 프론티어로 삼는다. 효율=이전금액/예산은 원점 대비 기울기.)
# ---------------------------------------------------------------------------
def pareto_front(results: List[Tuple[RewardStructure, AggregateMetrics]]) -> List[Tuple[RewardStructure, AggregateMetrics]]:
    # 예산 오름차순, 동일 예산이면 유치금액 큰 것 우선.
    pts = sorted(results, key=lambda sm: (sm[1].budget_mean, -sm[1].transfer_mean))
    front: List[Tuple[RewardStructure, AggregateMetrics]] = []
    best_transfer = -1.0
    for s, m in pts:
        # 더 낮은(또는 같은) 예산에서 더 큰 유치금액이면 비지배.
        if m.transfer_mean > best_transfer + 1e-6:
            front.append((s, m))
            best_transfer = m.transfer_mean
    return front


# ---------------------------------------------------------------------------
# 전체 최적화 실행
# ---------------------------------------------------------------------------
@dataclass
class OptimizationOutput:
    baseline: AggregateMetrics
    objectives: Dict[str, ObjectiveResult]
    pareto: List[Tuple[RewardStructure, AggregateMetrics]]
    all_evaluated: List[Tuple[RewardStructure, AggregateMetrics]]


def optimize(caches: Sequence[DatasetCache], top_k: int = 20, verbose: bool = False) -> OptimizationOutput:
    ev = Evaluator(caches)
    baseline = ev.eval(CURRENT_STRUCTURE)
    current_budget = baseline.budget_mean

    # 1단계: 거친 탐색
    coarse = coarse_candidates()
    coarse_eval = [(s, ev.eval(s)) for s in coarse]
    if verbose:
        print(f"[coarse] {len(coarse_eval)} structures evaluated")

    # 목표별 상위 top_k 추출 후 fine 탐색
    def top_by(key, reverse=False):
        return [s for s, _ in sorted(coarse_eval, key=key, reverse=reverse)[:top_k]]

    seeds: List[RewardStructure] = []
    seeds += top_by(lambda sm: sm[1].budget_mean)                       # A: 저예산
    seeds += top_by(lambda sm: sm[1].efficiency, reverse=True)          # B: 고효율
    seeds += top_by(lambda sm: abs(sm[1].budget_mean - current_budget * C.OBJ_C_BUDGET_TARGET))  # C

    # 2단계: fine 이웃 평가
    seen = {(_signature(s)) for s in coarse}
    for seed in seeds:
        for nb in fine_neighbors(seed):
            sig = _signature(nb)
            if sig in seen:
                continue
            seen.add(sig)
            ev.eval(nb)
    if verbose:
        print(f"[fine] total {len(ev.cache)} unique structures evaluated")

    # 전체 결과(구조 복원)
    all_results = _restore(ev, coarse, seeds)

    obj_results = {
        "A_min_budget": _wrap("A_min_budget", *select_min_budget(all_results), baseline, current_budget),
        "B_max_efficiency": _wrap("B_max_efficiency", *select_max_efficiency(all_results, current_budget), baseline, current_budget),
        "C_target_budget": _wrap("C_target_budget", *select_target_budget(all_results, current_budget), baseline, current_budget),
    }

    pareto = pareto_front(all_results)
    return OptimizationOutput(baseline, obj_results, pareto, all_results)


def _restore(ev: Evaluator, coarse, seeds) -> List[Tuple[RewardStructure, AggregateMetrics]]:
    """평가된 모든 시그니처에 대해 (구조, 지표) 복원."""
    by_sig: Dict[Signature, RewardStructure] = {}
    for s in coarse:
        by_sig[_signature(s)] = s
    for seed in seeds:
        for nb in fine_neighbors(seed):
            by_sig.setdefault(_signature(nb), nb)
    by_sig.setdefault(_signature(CURRENT_STRUCTURE), CURRENT_STRUCTURE)
    out = []
    for sig, m in ev.all_results():
        s = by_sig.get(sig, CURRENT_STRUCTURE)
        out.append((s, m))
    return out


def _rationale(obj: str, m: AggregateMetrics, baseline: AggregateMetrics) -> str:
    save = (1 - m.budget_mean / baseline.budget_mean) * 100
    if obj == "A_min_budget":
        return (f"매력도 {m.attractiveness_index*100:.1f}% 유지하며 예산 {save:.1f}% 절감 "
                f"(현행 {baseline.budget_mean/1e8:.2f}억→{m.budget_mean/1e8:.2f}억)")
    if obj == "B_max_efficiency":
        return (f"예산 상한 내 효율 {m.efficiency:.1f}배(현행 {baseline.efficiency:.1f}배), "
                f"ROI {m.roi_pct:.0f}%, 예산 {save:.1f}% 절감")
    return (f"예산을 현행의 {m.budget_mean/baseline.budget_mean*100:.0f}%로 맞추며 "
            f"유치금액 {m.attractiveness_index*100:.1f}% 확보")


def _wrap(obj, s, m, baseline, current_budget) -> ObjectiveResult:
    return ObjectiveResult(obj, s, m, _rationale(obj, m, baseline))
