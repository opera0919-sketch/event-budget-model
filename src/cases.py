"""리워드 케이스(후보 구조) 정의 - 비교/설명용 대표 케이스.

최적화 탐색과 별개로, 실무적으로 자연스러운 대표 케이스들을 명시해
'리워드 케이스에 따른 시뮬레이션' 비교표를 만든다.
"""
from __future__ import annotations

from typing import List

from config import simulation_config as C
from src.reward_engine import RewardStructure, enforce_monotonic, snap_reward

_THRESHOLDS = tuple(t for t, _ in C.CURRENT_TIERS)
_BASE = tuple(r for _, r in C.CURRENT_TIERS)


def _make(rewards, multiplier, name) -> RewardStructure:
    rewards = enforce_monotonic(tuple(rewards))
    return RewardStructure(tuple(zip(_THRESHOLDS, rewards)), multiplier,
                           C.CURRENT_MULTIPLIER_THRESHOLD, name)


def _scaled(factor: float) -> List[int]:
    return [snap_reward(r * factor) for r in _BASE]


# ---------------------------------------------------------------------------
# 재검토로 확정한 추천 3안 (직접구간·배수 미적용)
#   - 경계: config.DIRECT_BRACKETS (9단계)
#   - 전 제약 통과: 리워드 단위 / 엄격 증가 / 절벽 <= MAX_TIER_JUMP / 유효율 SD
# ---------------------------------------------------------------------------
#   상위 2개 티어는 현행 수준(1.3억 60만 / 3억+ 100만)을 방어한다.
#   방어하지 않으면 최상위 고객의 리워드가 현행의 45~65%까지 떨어져
#   '지나친 리워드 하락 금지' 제약을 위반한다(인원은 적지만 최고액 고객).
PLAN1_ATTRACT = [20_000, 40_000, 50_000, 100_000, 200_000, 250_000, 350_000, 600_000, 1_000_000]
PLAN2_BALANCED = [20_000, 30_000, 50_000, 100_000, 150_000, 250_000, 300_000, 600_000, 1_000_000]
PLAN3_EFFICIENT = [20_000, 30_000, 50_000, 100_000, 150_000, 200_000, 300_000, 550_000, 1_000_000]


def _direct(rewards: List[int], name: str) -> RewardStructure:
    """직접구간(배수 미적용) 구조 생성."""
    return RewardStructure(
        tuple(zip(C.DIRECT_BRACKETS, rewards)),
        1.0,
        C.CURRENT_MULTIPLIER_THRESHOLD,
        name,
    )


def recommended_plans() -> List[RewardStructure]:
    """추천 3안 (매력도우선 / 균형 / 효율우선)."""
    return [
        _direct(PLAN1_ATTRACT, "안1_매력도우선"),
        _direct(PLAN2_BALANCED, "안2_균형"),
        _direct(PLAN3_EFFICIENT, "안3_효율우선"),
    ]


def reference_cases() -> List[RewardStructure]:
    """대표 케이스 목록(현행 포함)."""
    from src.reward_engine import CURRENT_STRUCTURE

    cases = [CURRENT_STRUCTURE]

    # 배수 조건 변형
    cases.append(_make(_BASE, 1.0, "배수제거"))
    cases.append(_make(_scaled(1.0), 2.0, "배수2.0배(단위정합)"))

    # 균등 하향
    cases.append(_make(_scaled(0.8), 1.5, "균등하향80%"))
    cases.append(_make(_scaled(0.7), 1.5, "균등하향70%"))

    # 상위 축소(5천만 이상 티어 30% 절감), 배수 유지
    high_cut = [snap_reward(_BASE[i] * (1.0 if i < 3 else 0.7)) for i in range(7)]
    cases.append(_make(high_cut, 1.5, "상위축소"))

    # 하위 강화(저구간 상향), 배수 제거로 상쇄
    low_boost = [snap_reward(_BASE[i] * (1.5 if i < 3 else 0.9)) for i in range(7)]
    cases.append(_make(low_boost, 1.0, "하위강화·배수제거"))

    return cases
