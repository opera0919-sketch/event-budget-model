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
#   각 안은 '현행 대비 최저 보장률' 목표로 도출했다(안1 85% / 안2 80% / 안3 75%).
#   리워드 단위가 1만원으로 바로잡히면서 12만·13만 같은 중간값을 쓸 수 있게 되어,
#   5만원 단위 시절의 67% 하한을 75~85%로 끌어올렸다. 상위 티어도 같은 비율로
#   방어되므로 최고액 고객이 반토막 나는 문제가 사라진다.
#
#   도출 방식: 구간별 '현행 최대 리워드 x 목표비율'을 1만원 단위로 올림한 뒤,
#   엄격 증가와 절벽(<=MAX_TIER_JUMP)을 만족시킨다. 절벽 위반 시 후행 티어를
#   낮추지 않고 **선행 티어를 올린다** (낮추면 목표 보장률이 깨지기 때문).
PLAN1_ATTRACT = [20_000, 40_000, 60_000, 130_000, 140_000, 260_000, 270_000, 510_000, 850_000]
PLAN2_BALANCED = [20_000, 40_000, 60_000, 120_000, 130_000, 240_000, 250_000, 480_000, 800_000]
PLAN3_EFFICIENT = [20_000, 30_000, 60_000, 120_000, 130_000, 230_000, 240_000, 450_000, 750_000]


def _direct(rewards: List[int], name: str) -> RewardStructure:
    """직접구간(배수 미적용) 구조 생성."""
    return RewardStructure(
        tuple(zip(C.DIRECT_BRACKETS, rewards)),
        1.0,
        C.CURRENT_MULTIPLIER_THRESHOLD,
        name,
    )


# 배수 유지안(A안'): 현행 7단계 구조를 그대로 두고 리워드만 조정.
#   안2와 동일한 목표 보장률(80%)로 도출해 직접구간안과 공정 비교가 되게 했다.
#   1만원 단위가 허용되면서 절벽도 2.00x로 내려가, 예산·매력도·유효율 분산 전 항목에서
#   직접구간안보다 낫다(6.15억/77% · 매력도 94.2% · SD 0.034). 남는 차이는 계량되지
#   않는 '고객 이해도'(경계가 실제 순입금과 일치하는지)와 배수 로직의 복잡성이다.
PLAN_MULT_KEEP = [20_000, 40_000, 60_000, 120_000, 240_000, 480_000, 800_000]


def multiplier_keep_plan() -> RewardStructure:
    """배수 유지안 (A안')."""
    return RewardStructure(
        tuple(zip([t for t, _ in C.CURRENT_TIERS], PLAN_MULT_KEEP)),
        C.CURRENT_MULTIPLIER,
        C.CURRENT_MULTIPLIER_THRESHOLD,
        "A안_배수유지",
    )


def recommended_plans() -> List[RewardStructure]:
    """추천 3안 (매력도우선 / 균형 / 효율우선). 모두 직접구간·배수 폐지."""
    return [
        _direct(PLAN1_ATTRACT, "안1_매력도우선"),
        _direct(PLAN2_BALANCED, "안2_균형"),
        _direct(PLAN3_EFFICIENT, "안3_효율우선"),
    ]


def all_candidate_plans() -> List[RewardStructure]:
    """의사결정용 전체 후보: 배수 폐지 3안 + 배수 유지안."""
    return recommended_plans() + [multiplier_keep_plan()]


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
