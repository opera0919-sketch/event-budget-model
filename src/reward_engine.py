"""리워드 계산 엔진.

리워드 구조(티어 + 배수)를 파라미터로 받아 고객이 받는 '리워드'와
회사가 부담하는 '예산(제세 그로스업 포함)'을 계산한다.

핵심 구분:
  - reward : 고객이 실제 수령하는 금액
  - budget : 회사가 부담하는 금액. 리워드 >= 5만원이면 제세금(22%) 그로스업.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple

from config import simulation_config as C


@dataclass(frozen=True)
class RewardStructure:
    """하나의 리워드 케이스 정의.

    tiers: (하한금액, 리워드) 오름차순 목록. 인정금액이 하한 이상인 최고 티어의 리워드 지급.
    multiplier: 배수 값(1.0이면 미적용).
    multiplier_threshold: 이 금액 이상일 때만 배수 적용.
    name: 식별용 이름.
    """

    tiers: Tuple[Tuple[int, int], ...]
    multiplier: float = 1.0
    multiplier_threshold: int = C.CURRENT_MULTIPLIER_THRESHOLD
    name: str = "unnamed"

    def thresholds(self) -> Tuple[int, ...]:
        return tuple(t for t, _ in self.tiers)

    def rewards(self) -> Tuple[int, ...]:
        return tuple(r for _, r in self.tiers)


# 현행 구조 인스턴스 --------------------------------------------------------
CURRENT_STRUCTURE = RewardStructure(
    tiers=tuple(C.CURRENT_TIERS),
    multiplier=C.CURRENT_MULTIPLIER,
    multiplier_threshold=C.CURRENT_MULTIPLIER_THRESHOLD,
    name="현행",
)


def recognized_amount(transfer: float, structure: RewardStructure) -> float:
    """배수 적용 후 인정금액."""
    if structure.multiplier != 1.0 and transfer >= structure.multiplier_threshold:
        return transfer * structure.multiplier
    return transfer


def reward_for(transfer: float, structure: RewardStructure) -> int:
    """단일 고객의 리워드(고객 수령액). 자격 미달이면 0."""
    if transfer < C.MIN_QUALIFY_AMOUNT:
        return 0
    recog = recognized_amount(transfer, structure)
    reward = 0
    for threshold, r in structure.tiers:
        if recog >= threshold:
            reward = r
        else:
            break
    return reward


def budget_amount(reward: int) -> float:
    """리워드에 대한 회사 부담 예산(제세 그로스업 포함).

    reward < 5만원  -> 예산 = reward
    reward >= 5만원 -> 예산 = (reward/0.78)*0.22 + reward
    """
    if reward < C.TAX_THRESHOLD:
        return float(reward)
    return (reward / C.TAX_NET_RATE) * C.TAX_RATE + reward


def budget_for(transfer: float, structure: RewardStructure) -> float:
    """단일 고객의 예산(제세 포함)."""
    return budget_amount(reward_for(transfer, structure))


# --- 최적화용 리워드 단위/단조 제약 헬퍼 ----------------------------------
def is_valid_reward_unit(reward: int) -> bool:
    """리워드 단위 제약: <=5만원은 1만원 단위, >=5만원은 5만원 단위."""
    if reward == 0:
        return True
    if reward < 0:
        return False
    if reward <= C.TAX_THRESHOLD:  # <= 50,000
        return reward % 10_000 == 0
    return reward % 50_000 == 0


def allowed_reward_levels(max_reward: int) -> List[int]:
    """제약을 만족하는 허용 리워드 수준 목록(오름차순)."""
    levels = [v for v in range(10_000, C.TAX_THRESHOLD + 1, 10_000)]  # 1~5만
    v = C.TAX_THRESHOLD + 50_000  # 10만부터 5만 단위
    while v <= max_reward:
        levels.append(v)
        v += 50_000
    return levels


def snap_reward(x: float) -> int:
    """임의 금액을 허용 단위(≤5만원 1만 단위 / >5만원 5만 단위)로 스냅."""
    if x <= 0:
        return 0
    if x <= C.TAX_THRESHOLD:
        v = round(x / 10_000) * 10_000
        return int(max(10_000, min(v, C.TAX_THRESHOLD)))
    return int(round(x / 50_000) * 50_000)


def step_reward(reward: int, direction: int, max_reward: int) -> int:
    """허용 수준 목록에서 한 단계 위(+1)/아래(-1)로 이동. 경계 밖은 그대로."""
    levels = [0] + allowed_reward_levels(max_reward)
    if reward not in levels:
        reward = snap_reward(reward)
        if reward not in levels:
            levels = sorted(set(levels + [reward]))
    idx = levels.index(reward)
    j = idx + direction
    if 0 <= j < len(levels):
        return levels[j]
    return reward


def enforce_monotonic(rewards: Sequence[int]) -> Tuple[int, ...]:
    """앞에서부터 비감소가 되도록 상향 보정."""
    out = list(rewards)
    for i in range(1, len(out)):
        if out[i] < out[i - 1]:
            out[i] = out[i - 1]
    return tuple(out)


def is_monotonic(rewards: Sequence[int]) -> bool:
    """구간별 리워드가 단조 비감소인지."""
    return all(rewards[i] <= rewards[i + 1] for i in range(len(rewards) - 1))


def is_strictly_increasing(rewards: Sequence[int]) -> bool:
    """평탄구간(한계 리워드 0)이 없는지. 구간이 커지면 리워드도 반드시 증가."""
    return all(rewards[i] < rewards[i + 1] for i in range(len(rewards) - 1))


def max_tier_jump(rewards: Sequence[int]) -> float:
    """인접 티어 간 최대 리워드 배율(경계 절벽 크기). 0 리워드는 건너뛴다."""
    jumps = [rewards[i + 1] / rewards[i]
             for i in range(len(rewards) - 1) if rewards[i] > 0]
    return max(jumps) if jumps else 1.0


def is_valid_structure(structure: RewardStructure,
                       strict_increase: bool = False,
                       max_jump: float | None = None) -> bool:
    """설계 제약 충족 여부.

    기본(단위 + 단조 비감소)은 항상 검사한다. 추가 제약은 옵션으로 켠다.

    strict_increase: 평탄구간 금지(엄격 증가) 요구.
    max_jump: 인접 티어 배율 상한(예: 2.2). None이면 미적용.
    """
    rewards = structure.rewards()
    if not all(is_valid_reward_unit(r) for r in rewards):
        return False
    if not is_monotonic(rewards):
        return False
    if strict_increase and not is_strictly_increasing(rewards):
        return False
    if max_jump is not None and max_tier_jump(rewards) > max_jump + 1e-9:
        return False
    return True
