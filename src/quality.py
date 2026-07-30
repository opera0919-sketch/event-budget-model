"""리워드 스케줄의 '설계 품질' 지표.

총예산·매력도 같은 경제성 지표만으로는 드러나지 않는 결함을 계량한다.

- 유효 리워드율(reward / 순입금): 급간별로 얼마나 일관되게 보상하는가.
  현행처럼 배수를 쓰면 티어 내부에서 율이 하락(역진)하고 경계에서 급등(톱니)한다.
- 사문화 티어: 실제 표본이 없어 예산에 기여하지 못하는 티어.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from config import simulation_config as C
from src.reward_engine import RewardStructure, reward_for


@dataclass
class RateProfile:
    """급간별 유효 리워드율 프로파일."""
    brackets: List[Tuple[int, int]]     # (하한, 상한) — 상한 None 대신 큰 값
    avg_transfer: List[float]           # 급간 평균 순입금
    share: List[float]                  # 급간 인원 비중(0~1)
    rate_pct: List[float]               # 유효 리워드율(%)

    def weighted_mean(self) -> float:
        pairs = [(r, s) for r, s in zip(self.rate_pct, self.share) if r > 0]
        if not pairs:
            return 0.0
        tot = sum(s for _, s in pairs)
        return sum(r * s for r, s in pairs) / tot

    def weighted_sd(self) -> float:
        pairs = [(r, s) for r, s in zip(self.rate_pct, self.share) if r > 0]
        if not pairs:
            return 0.0
        tot = sum(s for _, s in pairs)
        m = sum(r * s for r, s in pairs) / tot
        return (sum(s * (r - m) ** 2 for r, s in pairs) / tot) ** 0.5

    def rate_range(self) -> Tuple[float, float]:
        vals = [r for r in self.rate_pct if r > 0]
        return (min(vals), max(vals)) if vals else (0.0, 0.0)

    def intra_regressive_steps(self) -> int:
        """급간 단위로 율이 하락하는 지점 수.

        주의: 정액-구간 방식은 티어 '내부'에서 율 하락이 불가피하다
        (같은 리워드를 더 큰 금액으로 나누므로). 따라서 이 값 자체는
        결함이 아니며, 티어 간 역진(tier_regressive_steps)과 구분해야 한다.
        """
        vals = [r for r in self.rate_pct if r > 0]
        return sum(1 for i in range(len(vals) - 1) if vals[i + 1] < vals[i] - 1e-9)


def _default_edges(width: int = C.BRACKET_WIDTH, n: int = 24) -> List[int]:
    return [b * width for b in range(n)]


def effective_rate_profile(structure: RewardStructure,
                           transfers: Sequence[int],
                           edges: Sequence[int] | None = None) -> RateProfile:
    """급간별 평균 순입금에 대한 유효 리워드율 프로파일.

    edges: 급간 하한 목록(오름차순). 생략 시 1천만원 단위 24구간.
    """
    edges = list(edges) if edges is not None else _default_edges()
    total = len(transfers)
    brackets: List[Tuple[int, int]] = []
    avg: List[float] = []
    share: List[float] = []
    rates: List[float] = []

    uppers = list(edges[1:]) + [10 ** 15]
    for lo, hi in zip(edges, uppers):
        group = [t for t in transfers if lo <= t < hi]
        brackets.append((lo, hi))
        if not group:
            avg.append(0.0)
            share.append(0.0)
            rates.append(0.0)
            continue
        a = sum(group) / len(group)
        r = reward_for(a, structure)
        avg.append(a)
        share.append(len(group) / total if total else 0.0)
        rates.append(r / a * 100 if a else 0.0)

    return RateProfile(brackets, avg, share, rates)


def tier_entry_rates(structure: RewardStructure) -> List[float]:
    """각 티어에 '막 진입한' 시점의 유효 리워드율(%).

    배수가 있으면 인정금액 기준 하한을 실제 순입금으로 환산해서 계산한다.
    (예: 배수 1.5, 인정 3천만 티어 → 실제 순입금 2천만에서 진입)
    """
    rates: List[float] = []
    for threshold, reward in structure.tiers:
        entry = threshold
        if structure.multiplier > 1.0:
            candidate = threshold / structure.multiplier
            # 환산액이 배수 적용 임계 이상일 때만 배수로 진입 가능.
            if candidate >= structure.multiplier_threshold:
                entry = candidate
        rates.append(reward / entry * 100 if entry else 0.0)
    return rates


def tier_regressive_steps(structure: RewardStructure) -> int:
    """티어 간 역진 지점 '수'.

    주의: 구간을 세분할수록 작은 진동도 횟수로 잡히므로, 구조 간 비교에는
    폭 지표(max_tier_regression)와 유효율 SD를 함께 봐야 한다.
    """
    r = tier_entry_rates(structure)
    return sum(1 for i in range(len(r) - 1) if r[i + 1] < r[i] - 1e-9)


def max_tier_regression(structure: RewardStructure) -> float:
    """티어 간 역진의 최대 '폭'(%p). 클수록 형평성 훼손이 크다."""
    r = tier_entry_rates(structure)
    drops = [r[i] - r[i + 1] for i in range(len(r) - 1) if r[i + 1] < r[i]]
    return max(drops) if drops else 0.0


def rate_dispersion(structure: RewardStructure,
                    transfers: Sequence[int],
                    edges: Sequence[int] | None = None) -> Dict[str, float]:
    """유효율 일관성 요약(가중 평균/표준편차/최소/최대)."""
    p = effective_rate_profile(structure, transfers, edges)
    lo, hi = p.rate_range()
    return {
        "mean_pct": p.weighted_mean(),
        "sd": p.weighted_sd(),
        "min_pct": lo,
        "max_pct": hi,
        "intra_regressive": float(p.intra_regressive_steps()),
        "tier_regressive": float(tier_regressive_steps(structure)),
        "max_tier_regression": max_tier_regression(structure),
    }


def dead_tier_count(structure: RewardStructure, transfers: Sequence[int]) -> int:
    """실제 표본이 도달하지 못하는 '사문화' 티어 수.

    인정금액(배수 적용 후) 기준으로 어떤 고객도 도달하지 못하는 티어를 센다.
    """
    from src.reward_engine import recognized_amount

    if not transfers:
        return len(structure.tiers)
    max_recog = max(recognized_amount(t, structure) for t in transfers)
    return sum(1 for th, _ in structure.tiers if th > max_recog)


def ratio_vs_current(structure: RewardStructure,
                     transfers: Sequence[int],
                     edges: Sequence[int] | None = None) -> List[Tuple[int, float, float]]:
    """급간별 '현행 대비 리워드 비율'.

    동일 순입금 고객이 실제로 받는 금액을 비교한다. 현행은 배수(1.5배)로
    인정금액을 올려 판정하므로, 구간 체계가 다른 신규안과는 이 방식으로만
    공정하게 비교할 수 있다.

    반환: (급간 하한, 현행 대비 비율, 인원 비중) 목록. 현행 리워드가 0인
    급간(자격 미달)은 제외한다.
    """
    from src.reward_engine import CURRENT_STRUCTURE

    edges = list(edges) if edges is not None else _default_edges()
    total = len(transfers)
    uppers = list(edges[1:]) + [10 ** 15]
    out: List[Tuple[int, float, float]] = []
    for lo, hi in zip(edges, uppers):
        group = [t for t in transfers if lo <= t < hi]
        if not group:
            continue
        avg = sum(group) / len(group)
        cur = reward_for(avg, CURRENT_STRUCTURE)
        if cur <= 0:
            continue
        out.append((lo, reward_for(avg, structure) / cur, len(group) / total if total else 0.0))
    return out


def min_ratio_vs_current(structure: RewardStructure,
                         transfers: Sequence[int],
                         edges: Sequence[int] | None = None) -> float:
    """현행 대비 리워드 비율의 최솟값(매력도 방어 게이트용)."""
    ratios = ratio_vs_current(structure, transfers, edges)
    return min((r for _, r, _ in ratios), default=1.0)


def design_report(structure: RewardStructure,
                  transfers: Sequence[int],
                  edges: Sequence[int] | None = None) -> Dict[str, float]:
    """설계 품질 종합(유효율 + 사문화 티어 + 절벽)."""
    from src.reward_engine import max_tier_jump

    out = rate_dispersion(structure, transfers, edges)
    out["dead_tiers"] = float(dead_tier_count(structure, transfers))
    out["max_jump"] = max_tier_jump(structure.rewards())
    out["min_ratio_vs_current"] = min_ratio_vs_current(structure, transfers, edges)
    return out
