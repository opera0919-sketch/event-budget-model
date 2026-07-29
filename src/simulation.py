"""시뮬레이션 엔진.

리워드 케이스(RewardStructure)를 데이터셋에 적용하고, 수요반응(탄력성)을
반영해 예산·유치금액·KPI를 산출한다.

성능: 데이터셋을 (타사이전금액 값 -> 인원수) 히스토그램으로 캐시하고,
현행 대비 값을 미리 계산해 수백 개 후보 구조를 빠르게 평가한다.
"""
from __future__ import annotations

import statistics
from bisect import bisect_right
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from config import simulation_config as C
from src import demand_model as dm
from src.data_generator import RoundDataset
from src.reward_engine import (
    CURRENT_STRUCTURE,
    RewardStructure,
    budget_amount,
    recognized_amount,
    reward_for,
)


# ---------------------------------------------------------------------------
# 데이터셋 캐시
# ---------------------------------------------------------------------------
@dataclass
class DatasetCache:
    round_idx: int
    scenario: str
    values: List[int]          # 고유 타사이전금액(오름차순)
    counts: List[int]          # 각 값의 인원수
    reward_cur: List[int]      # 현행 구조 리워드
    mf_cur: List[float]        # 현행 구조 배수 참여계수
    n_customers: int
    current_transfer: float    # 현행 기준 유치 이전금액(가중=1)


def build_cache(ds: RoundDataset) -> DatasetCache:
    hist: Dict[int, int] = {}
    for t in ds.transfers:
        hist[t] = hist.get(t, 0) + 1
    values = sorted(hist)
    counts = [hist[v] for v in values]
    reward_cur = [reward_for(v, CURRENT_STRUCTURE) for v in values]
    mf_cur = [_mult_factor(CURRENT_STRUCTURE, v) for v in values]
    current_transfer = float(sum(v * c for v, c in zip(values, counts)))
    return DatasetCache(
        round_idx=ds.round_idx,
        scenario=ds.scenario,
        values=values,
        counts=counts,
        reward_cur=reward_cur,
        mf_cur=mf_cur,
        n_customers=ds.n_customers,
        current_transfer=current_transfer,
    )


def _mult_factor(structure: RewardStructure, transfer: float) -> float:
    """배수 참여계수: 배수 수혜 고객이면 (1 + 보너스)."""
    if structure.multiplier > 1.0 and transfer >= structure.multiplier_threshold:
        return 1.0 + dm.multiplier_bonus(structure.multiplier)
    return 1.0


def _reward_lookup(structure: RewardStructure):
    thresholds = structure.thresholds()
    rewards = structure.rewards()

    def fn(transfer: float) -> int:
        if transfer < C.MIN_QUALIFY_AMOUNT:
            return 0
        recog = recognized_amount(transfer, structure)
        idx = bisect_right(thresholds, recog) - 1
        if idx < 0:
            return 0
        return rewards[idx]

    return fn


# ---------------------------------------------------------------------------
# 지표
# ---------------------------------------------------------------------------
@dataclass
class RoundMetrics:
    round_idx: int
    scenario: str
    n_applicants: float
    n_recipients: float
    total_transfer: float
    total_reward: float
    total_budget: float
    attractiveness_index: float   # 유치금액 vs 현행 (배)


def evaluate_round(cache: DatasetCache, structure: RewardStructure,
                   params: Dict[str, float] | None = None) -> RoundMetrics:
    reward_fn = _reward_lookup(structure)
    scaling_cache: Dict[Tuple[int, int], float] = {}

    n_app = n_rec = tot_t = tot_r = tot_b = 0.0
    for v, cnt, rc, mfc in zip(cache.values, cache.counts, cache.reward_cur, cache.mf_cur):
        r_cand = reward_fn(v)
        key = (rc, r_cand)
        scale = scaling_cache.get(key)
        if scale is None:
            r = dm.reward_ratio(rc, r_cand)
            scale = dm.participation_scaling(r, params)
            scaling_cache[key] = scale
        mf_cand = _mult_factor(structure, v)
        weight = scale * (mf_cand / mfc) * cnt

        n_app += weight
        tot_t += weight * v
        if r_cand > 0:
            n_rec += weight
            tot_r += weight * r_cand
            tot_b += weight * budget_amount(r_cand)

    attractiveness = tot_t / cache.current_transfer if cache.current_transfer else 0.0
    return RoundMetrics(
        round_idx=cache.round_idx,
        scenario=cache.scenario,
        n_applicants=n_app,
        n_recipients=n_rec,
        total_transfer=tot_t,
        total_reward=tot_r,
        total_budget=tot_b,
        attractiveness_index=attractiveness,
    )


@dataclass
class AggregateMetrics:
    """30회차 집계 + 파생 KPI (예산·효율·ROI·CPA는 제세 포함 예산 기준)."""
    name: str
    # 예산
    budget_mean: float
    budget_std: float
    budget_worst: float      # 최대(worst-case)
    budget_best: float       # 최소(best-case)
    budget_hit_mean: float
    budget_flop_mean: float
    # 규모
    transfer_mean: float
    reward_mean: float
    applicants_mean: float
    recipients_mean: float
    # KPI
    cost_rate_pct: float          # 예산/이전금액 * 100
    efficiency: float             # 이전금액/예산 (배)
    roi_pct: float                # (이전금액-예산)/예산 * 100
    cpa: float                    # 예산/수령자
    avg_transfer: float           # 이전금액/신청자
    avg_reward_recipient: float   # 리워드/수령자
    attractiveness_index: float   # 유치금액 vs 현행 (배)

    def as_row(self) -> Dict[str, float]:
        return {
            "구조": self.name,
            "예산_평균": round(self.budget_mean),
            "예산_표준편차": round(self.budget_std),
            "예산_worst(최대)": round(self.budget_worst),
            "예산_best(최소)": round(self.budget_best),
            "예산_흥행평균": round(self.budget_hit_mean),
            "예산_비흥행평균": round(self.budget_flop_mean),
            "유치이전금액_평균": round(self.transfer_mean),
            "리워드지급_평균": round(self.reward_mean),
            "신청자수_평균": round(self.applicants_mean),
            "수령자수_평균": round(self.recipients_mean),
            "비용률_%": round(self.cost_rate_pct, 4),
            "효율_배": round(self.efficiency, 2),
            "ROI_%": round(self.roi_pct, 1),
            "CPA": round(self.cpa),
            "평균이전금액": round(self.avg_transfer),
            "수령자당평균리워드": round(self.avg_reward_recipient),
            "매력도지수_배": round(self.attractiveness_index, 4),
        }


def aggregate(rounds: Sequence[RoundMetrics], name: str) -> AggregateMetrics:
    budgets = [m.total_budget for m in rounds]
    transfers = [m.total_transfer for m in rounds]
    rewards = [m.total_reward for m in rounds]
    apps = [m.n_applicants for m in rounds]
    recs = [m.n_recipients for m in rounds]
    attr = [m.attractiveness_index for m in rounds]

    hit_b = [m.total_budget for m in rounds if m.scenario == "hit"]
    flop_b = [m.total_budget for m in rounds if m.scenario == "flop"]

    budget_mean = statistics.fmean(budgets)
    transfer_mean = statistics.fmean(transfers)
    reward_mean = statistics.fmean(rewards)
    recipients_mean = statistics.fmean(recs)
    applicants_mean = statistics.fmean(apps)

    cost_rate = budget_mean / transfer_mean * 100 if transfer_mean else 0.0
    efficiency = transfer_mean / budget_mean if budget_mean else 0.0
    roi = (transfer_mean - budget_mean) / budget_mean * 100 if budget_mean else 0.0
    cpa = budget_mean / recipients_mean if recipients_mean else 0.0
    avg_transfer = transfer_mean / applicants_mean if applicants_mean else 0.0
    avg_reward_rec = reward_mean / recipients_mean if recipients_mean else 0.0

    return AggregateMetrics(
        name=name,
        budget_mean=budget_mean,
        budget_std=statistics.pstdev(budgets) if len(budgets) > 1 else 0.0,
        budget_worst=max(budgets),
        budget_best=min(budgets),
        budget_hit_mean=statistics.fmean(hit_b) if hit_b else 0.0,
        budget_flop_mean=statistics.fmean(flop_b) if flop_b else 0.0,
        transfer_mean=transfer_mean,
        reward_mean=reward_mean,
        applicants_mean=applicants_mean,
        recipients_mean=recipients_mean,
        cost_rate_pct=cost_rate,
        efficiency=efficiency,
        roi_pct=roi,
        cpa=cpa,
        avg_transfer=avg_transfer,
        avg_reward_recipient=avg_reward_rec,
        attractiveness_index=statistics.fmean(attr),
    )


def evaluate_structure(caches: Sequence[DatasetCache], structure: RewardStructure,
                       params: Dict[str, float] | None = None) -> AggregateMetrics:
    rounds = [evaluate_round(c, structure, params) for c in caches]
    return aggregate(rounds, structure.name)
