"""리워드 구조 최적화 - 2단계 탐색.

1단계 거친(coarse) 탐색: 세그먼트 배율 × 배수 격자에서 후보 생성·평가.
2단계 촘촘한(fine) 탐색: 목표별 상위 20개 주변을 티어 단위로 국소 탐색.

목표:
  (A) 예산 최소화 (매력도 하한 제약)
  (B) 비용효율 최대화 (예산 상한 제약)
  (C) 목표예산 달성 (현행 75% ± 허용, 매력도 최대)

부산물: 예산 vs 효율 Pareto frontier.
"""
from __future__ import annotations

from dataclasses import dataclass, field
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
def _accept(s: RewardStructure) -> bool:
    """설계 제약 필터.

    단위·단조에 더해 레거시용 완화 절벽 상한(OPTIMIZER_MAX_TIER_JUMP)을 적용한다.
    신규 설계 목표치(MAX_TIER_JUMP=2.2)를 여기 강제하면 현행 티어 배율(2.50x)을
    물려받은 탐색 공간이 전멸하므로, 레거시 탐색에서는 완화 상한만 걸고
    절벽·평탄구간·역진폭은 리포트의 설계 품질 지표로 드러낸다.
    """
    return is_valid_structure(s, strict_increase=False,
                              max_jump=C.OPTIMIZER_MAX_TIER_JUMP)


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
                    if not _accept(s):
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
    return [c for c in out if _accept(c)]


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
    gate_violations: List[str] = field(default_factory=list)

    @property
    def passes_gate(self) -> bool:
        return not self.gate_violations


def gate_violations(structure: RewardStructure, transfers) -> List[str]:
    """선정된 구조에 최종 설계 게이트를 적용해 위반 항목을 나열한다.

    탐색 단계(`_accept`)는 레거시 7단계 공간을 살리기 위해 제약을 완화하므로,
    **선정 결과에는 반드시 최종 게이트를 다시 적용해야 한다.** 그렇지 않으면
    리포트가 게이트 위반안을 최우선 권고로 제시하면서 동시에 '전항 PASS'라
    표시하는 모순이 생긴다.
    """
    from src.quality import design_report
    from src.reward_engine import is_strictly_increasing

    out: List[str] = []
    rewards = structure.rewards()
    if not is_strictly_increasing(rewards):
        out.append("평탄구간(엄격 증가 위반)")
    q = design_report(structure, transfers)
    if q["max_jump"] > C.MAX_TIER_JUMP + 1e-9:
        out.append(f"경계 절벽 {q['max_jump']:.2f}x > {C.MAX_TIER_JUMP}x")
    if q["sd"] > C.MAX_RATE_SD + 1e-9:
        out.append(f"유효율 SD {q['sd']:.3f} > {C.MAX_RATE_SD}")
    if q["min_ratio_vs_current"] < C.MIN_RATIO_VS_CURRENT - 1e-9:
        out.append(f"현행 대비 {q['min_ratio_vs_current']*100:.0f}% "
                   f"< {C.MIN_RATIO_VS_CURRENT*100:.0f}%")
    return out


def _feasible_attractive(m: AggregateMetrics, floor: float) -> bool:
    return m.attractiveness_index >= floor


def _ratio_ok(structure: RewardStructure, transfers) -> bool:
    """현행 대비 하락 하한(사용자 확정 제약)을 지키는지.

    '지나친 리워드 하락 금지'는 사용자가 확정한 제약이므로 목표별 선정에서
    **하드 feasibility**로 적용한다. 절벽·SD는 레거시 7단계 구조상 충족이
    불가능하므로 하드 제약으로 걸지 않고 위반 사실을 병기한다.
    """
    from src.quality import min_ratio_vs_current
    return min_ratio_vs_current(structure, transfers) >= C.MIN_RATIO_VS_CURRENT - 1e-9


def select_min_budget(cand, transfers) -> Tuple[RewardStructure, AggregateMetrics]:
    feas = [(s, m) for s, m in cand
            if _feasible_attractive(m, C.OBJ_A_ATTRACT_FLOOR) and _ratio_ok(s, transfers)]
    feas = feas or [(s, m) for s, m in cand if _ratio_ok(s, transfers)] or cand
    return min(feas, key=lambda sm: sm[1].budget_mean)


def select_max_efficiency(cand, current_budget, transfers) -> Tuple[RewardStructure, AggregateMetrics]:
    cap = current_budget * C.OBJ_B_BUDGET_CAP
    feas = [(s, m) for s, m in cand
            if m.budget_mean <= cap and _feasible_attractive(m, C.OBJ_MIN_ATTRACTIVENESS)
            and _ratio_ok(s, transfers)]
    feas = feas or [(s, m) for s, m in cand
                    if m.budget_mean <= cap and _ratio_ok(s, transfers)] or cand
    return max(feas, key=lambda sm: sm[1].efficiency)


def select_target_budget(cand, current_budget, transfers) -> Tuple[RewardStructure, AggregateMetrics]:
    target = current_budget * C.OBJ_C_BUDGET_TARGET
    tol = current_budget * C.OBJ_C_BUDGET_TOL
    band = [(s, m) for s, m in cand
            if abs(m.budget_mean - target) <= tol
            and _feasible_attractive(m, C.OBJ_MIN_ATTRACTIVENESS) and _ratio_ok(s, transfers)]
    if band:
        return max(band, key=lambda sm: sm[1].transfer_mean)
    ratio_ok = [(s, m) for s, m in cand if _ratio_ok(s, transfers)] or cand
    return min(ratio_ok, key=lambda sm: abs(sm[1].budget_mean - target))


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


def optimize(caches: Sequence[DatasetCache], top_k: int = 20, verbose: bool = False,
             transfers: Sequence[int] | None = None) -> OptimizationOutput:
    ev = Evaluator(caches)
    baseline = ev.eval(CURRENT_STRUCTURE)
    current_budget = baseline.budget_mean
    # 현행 대비 하락률 검증용 순입금 목록. 캐시에서 복원하면 데이터 재생성이 불필요.
    if transfers is None:
        transfers = [v for c in caches for v, cnt in zip(c.values, c.counts) for _ in range(cnt)]

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
        "A_min_budget": _wrap("A_min_budget",
                              *select_min_budget(all_results, transfers),
                              baseline, current_budget, transfers),
        "B_max_efficiency": _wrap("B_max_efficiency",
                                  *select_max_efficiency(all_results, current_budget, transfers),
                                  baseline, current_budget, transfers),
        "C_target_budget": _wrap("C_target_budget",
                                 *select_target_budget(all_results, current_budget, transfers),
                                 baseline, current_budget, transfers),
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
        # 무성 폴백을 두면 잘못된 구조에 남의 지표가 붙어 조용히 오답이 된다.
        if sig not in by_sig:
            raise KeyError(f"평가된 시그니처를 구조로 복원할 수 없음: {sig}")
        out.append((by_sig[sig], m))
    return out


def _rationale(obj: str, m: AggregateMetrics, baseline: AggregateMetrics) -> str:
    save = (1 - m.budget_mean / baseline.budget_mean) * 100
    if obj == "A_min_budget":
        return (f"매력도 {m.attractiveness_index*100:.1f}% 유지하며 예산 {save:.1f}% 절감 "
                f"(현행 {baseline.budget_mean/1e8:.2f}억→{m.budget_mean/1e8:.2f}억)")
    if obj == "B_max_efficiency":
        return (f"예산 상한 내 효율 {m.efficiency:.1f}배(현행 {baseline.efficiency:.1f}배), "
                f"예산 {save:.1f}% 절감")
    return (f"예산을 현행의 {m.budget_mean/baseline.budget_mean*100:.0f}%로 맞추며 "
            f"유치금액 {m.attractiveness_index*100:.1f}% 확보")


def _wrap(obj, s, m, baseline, current_budget, transfers) -> ObjectiveResult:
    return ObjectiveResult(obj, s, m, _rationale(obj, m, baseline),
                           gate_violations(s, transfers))
