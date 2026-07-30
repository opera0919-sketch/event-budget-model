"""리워드 스케줄의 '설계 품질' 지표.

총예산·매력도 같은 경제성 지표만으로는 드러나지 않는 결함을 계량한다.

- 유효 리워드율(reward / 순입금): 급간별로 얼마나 일관되게 보상하는가.
  현행처럼 배수를 쓰면 티어 내부에서 율이 하락(역진)하고 경계에서 급등(톱니)한다.
- 사문화 티어: 실제 표본이 없어 예산에 기여하지 못하는 티어.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from config import simulation_config as C
from src.reward_engine import RewardStructure, reward_for


@dataclass
class RateProfile:
    """급간별 유효 리워드율 프로파일.

    rate_pct는 급간 **평균 금액에 리워드를 적용한 값이 아니라**, 급간에 속한
    고객 각자의 유효율을 평균한 값이다. 계단함수를 평균에 적용하면
    `reward(mean) != mean(reward)` 편향이 생기고, 자격 미달자가 섞인 급간이
    통째로 0으로 처리돼 집계에서 빠지기 때문이다.
    """
    brackets: List[Tuple[int, int]]     # (하한, 상한)
    avg_transfer: List[float]           # 급간 평균 순입금
    share: List[float]                  # 급간 인원 비중(0~1) — 자격자만
    rate_pct: List[float]               # 급간 내 자격자의 평균 유효 리워드율(%)
    unqualified_share: float = 0.0      # 자격 미달(리워드 0) 인원 비중
    entry_cliff_pct: float = 0.0        # 자격 진입 시점 유효율(%) — 0 -> 이 값으로 점프

    def weighted_mean(self) -> float:
        pairs = [(r, s) for r, s in zip(self.rate_pct, self.share) if s > 0]
        if not pairs:
            return 0.0
        tot = sum(s for _, s in pairs)
        return sum(r * s for r, s in pairs) / tot

    def weighted_sd(self) -> float:
        pairs = [(r, s) for r, s in zip(self.rate_pct, self.share) if s > 0]
        if not pairs:
            return 0.0
        tot = sum(s for _, s in pairs)
        m = sum(r * s for r, s in pairs) / tot
        return (sum(s * (r - m) ** 2 for r, s in pairs) / tot) ** 0.5

    def rate_range(self) -> Tuple[float, float]:
        vals = [r for r, s in zip(self.rate_pct, self.share) if s > 0]
        return (min(vals), max(vals)) if vals else (0.0, 0.0)

    def intra_regressive_steps(self) -> int:
        """급간 단위로 율이 하락하는 지점 수.

        주의: 정액-구간 방식은 티어 '내부'에서 율 하락이 불가피하다
        (같은 리워드를 더 큰 금액으로 나누므로). 따라서 이 값 자체는
        결함이 아니며, 티어 간 역진(tier_regressive_steps)과 구분해야 한다.
        """
        vals = [r for r, s in zip(self.rate_pct, self.share) if s > 0]
        return sum(1 for i in range(len(vals) - 1) if vals[i + 1] < vals[i] - 1e-9)


def _default_edges(width: int = C.BRACKET_WIDTH, n: int = 24) -> List[int]:
    """유효율 프로파일용 기본 격자(1천만원 단위 24구간)."""
    return [b * width for b in range(n)]


def _ratio_edges() -> List[int]:
    """현행 대비 비율 검증용 정밀 격자(100만원 단위).

    좁은 구간의 하락이 평균에 묻히지 않도록 프로파일용보다 촘촘하게 본다.
    """
    step = C.RATIO_CHECK_UNIT
    return [i * step for i in range(1, C.RATIO_CHECK_MAX // step + 1)]


# 급간 그룹핑 캐시.
#   ratio_vs_current 는 후보 구조마다 호출되는데, 순입금 목록과 격자는 매번 같다.
#   격자마다 전체 목록을 선형 스캔하면 245 x 135k = 33M 회 비교가 호출당 발생한다.
#   정렬 + bisect 로 한 번만 그룹핑하고 결과를 캐시한다.
_GROUP_CACHE: Dict[Tuple[int, int, int, int], List[Tuple[int, float, int]]] = {}


_HIST_CACHE: Dict[Tuple[int, int], Tuple[List[int], List[int], List[int]]] = {}


def _histogram(transfers: Sequence[int]) -> Tuple[List[int], List[int], List[int]]:
    """(고유값 오름차순, 각 값의 인원수, 금액 누적합) — 캐시된다.

    고객별 유효율을 구하려면 전 고객을 훑어야 하지만, 같은 금액은 같은 유효율이므로
    고유값 단위로 계산하고 인원수로 가중하면 결과가 동일하면서 훨씬 빠르다.
    """
    key = (len(transfers), sum(transfers))
    cached = _HIST_CACHE.get(key)
    if cached is not None:
        return cached

    hist: Dict[int, int] = {}
    for t in transfers:
        hist[t] = hist.get(t, 0) + 1
    values = sorted(hist)
    counts = [hist[v] for v in values]
    cum = [0]
    for v, c in zip(values, counts):
        cum.append(cum[-1] + v * c)
    _HIST_CACHE[key] = (values, counts, cum)
    return values, counts, cum


def _grouped(transfers: Sequence[int], edges: Sequence[int]) -> List[Tuple[int, float, int]]:
    """(급간 하한, 급간 평균 순입금, 인원수) 목록. 빈 급간은 제외."""
    key = (len(transfers), sum(transfers), len(edges), hash(tuple(edges)))
    cached = _GROUP_CACHE.get(key)
    if cached is not None:
        return cached

    ordered = sorted(transfers)
    prefix = [0]
    for t in ordered:
        prefix.append(prefix[-1] + t)
    uppers = list(edges[1:]) + [10 ** 15]
    out: List[Tuple[int, float, int]] = []
    for lo, hi in zip(edges, uppers):
        i = bisect_left(ordered, lo)
        j = bisect_left(ordered, hi)
        if j <= i:
            continue
        out.append((lo, (prefix[j] - prefix[i]) / (j - i), j - i))
    _GROUP_CACHE[key] = out
    return out


def effective_rate_profile(structure: RewardStructure,
                           transfers: Sequence[int],
                           edges: Sequence[int] | None = None) -> RateProfile:
    """급간별 유효 리워드율 프로파일 (고객별 유효율의 급간 평균).

    급간 평균 금액에 리워드를 적용하지 않는다. 그렇게 하면
    `reward(mean) != mean(reward)` 편향이 생기고, 자격 미달자가 섞인 급간
    (0~1천만: 평균 398만 -> 리워드 0)이 통째로 집계에서 빠진다.

    자격 미달자(리워드 0)는 급간 집계에서 제외하고 `unqualified_share`로
    별도 보고한다. 자격 진입 시점의 유효율(500만원 -> 2만원 = 0.40%)은
    `entry_cliff_pct`로 노출해 자격 절벽이 지표에서 사라지지 않게 한다.

    edges: 급간 하한 목록(오름차순). 생략 시 1천만원 단위 24구간.
    """
    edges = list(edges) if edges is not None else _default_edges()
    total = len(transfers)
    brackets: List[Tuple[int, int]] = []
    avg: List[float] = []
    share: List[float] = []
    rates: List[float] = []
    n_unqualified = 0

    values, counts, cum_amount = _histogram(transfers)
    uppers = list(edges[1:]) + [10 ** 15]
    for lo, hi in zip(edges, uppers):
        i = bisect_left(values, lo)
        j = bisect_left(values, hi)
        brackets.append((lo, hi))
        if j <= i:
            avg.append(0.0)
            share.append(0.0)
            rates.append(0.0)
            continue
        # 고객별 유효율(고유값 단위로 인원 가중). 자격 미달자는 따로 센다.
        n_group = 0
        amount = cum_amount[j] - cum_amount[i]
        rate_sum = 0.0
        n_qualified = 0
        for k in range(i, j):
            t = values[k]
            c = counts[k]
            n_group += c
            r = reward_for(t, structure)
            if r <= 0:
                n_unqualified += c
            elif t > 0:
                rate_sum += r / t * 100 * c
                n_qualified += c
        avg.append(amount / n_group if n_group else 0.0)
        share.append(n_qualified / total if total else 0.0)
        rates.append(rate_sum / n_qualified if n_qualified else 0.0)

    entry = reward_for(C.MIN_QUALIFY_AMOUNT, structure)
    return RateProfile(
        brackets, avg, share, rates,
        unqualified_share=n_unqualified / total if total else 0.0,
        entry_cliff_pct=entry / C.MIN_QUALIFY_AMOUNT * 100 if entry else 0.0,
    )


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
        "unqualified_share": p.unqualified_share,
        "entry_cliff_pct": p.entry_cliff_pct,
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

    격자는 기본적으로 100만원 단위(`_ratio_edges`)를 쓴다. 1천만원 단위로 보면
    좁은 구간의 하락이 묻힌다.
    """
    from src.reward_engine import CURRENT_STRUCTURE

    edges = list(edges) if edges is not None else _ratio_edges()
    total = len(transfers)
    out: List[Tuple[int, float, float]] = []
    for lo, avg, cnt in _grouped(transfers, edges):
        cur = reward_for(avg, CURRENT_STRUCTURE)
        if cur <= 0:
            continue
        out.append((lo, reward_for(avg, structure) / cur, cnt / total if total else 0.0))
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
