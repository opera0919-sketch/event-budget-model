"""수요반응(탄력성) 모델 - 비선형 로지스틱.

리워드 수준 변화가 참여(신청/이전금액 유지)에 미치는 영향을 모델링한다.

설계 원칙:
  - r = reward_candidate / reward_current (구간/고객별 리워드비)
  - 참여 스케일링은 r=1(현행 동일)에서 정확히 1.0이 되도록 정규화한다.
  - r ~ 1 부근(소폭 하향)에서는 기울기 완만 -> sticky(비탄력).
  - r 가 크게 하락하면 S-커브 하단으로 참여 급감.
  - f_min / f_max 로 반응 폭을 제한(현실성).

배수 참여 보너스: 구조에 배수가 적용되면(매력적 조건) 배수 수혜 고객의 참여율에 가산.
  1.5배 -> +3%, 2.0배 -> +5% (앵커 선형보간), 1.0배 -> 0%.
"""
from __future__ import annotations

import math
from typing import Dict

from config import simulation_config as C


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def active_params() -> Dict[str, float]:
    return C.ELASTICITY[C.ELASTICITY_ACTIVE]


def participation_scaling(r: float, params: Dict[str, float] | None = None) -> float:
    """리워드비 r 에 대한 참여 스케일링(현행=1.0 정규화, 로지스틱)."""
    p = params or active_params()
    k, r0, f_min, f_max = p["k"], p["r0"], p["f_min"], p["f_max"]

    def raw(x: float) -> float:
        return f_min + (f_max - f_min) * _sigmoid(k * (x - r0))

    base = raw(1.0)  # 현행 기준값
    return raw(r) / base


def reward_ratio(reward_current: int, reward_candidate: int) -> float:
    """리워드비 r. 0 리워드 경계 처리 포함.

    - 현행 0, 후보 0   -> 1.0 (비자격, 변화 없음)
    - 현행 0, 후보 > 0 -> 큰 값(신규 유인)  => 상한 부근 스케일링
    - 현행 > 0, 후보 0 -> 0.0 (완전 하락)
    """
    if reward_current <= 0:
        if reward_candidate <= 0:
            return 1.0
        return 2.0  # 충분히 큰 값(로지스틱 상단 포화)
    return reward_candidate / reward_current


def multiplier_bonus(multiplier: float) -> float:
    """배수 값에 대한 참여율 가산(앵커 선형보간)."""
    if multiplier <= 1.0:
        return 0.0
    anchors = sorted(C.MULT_BONUS_ANCHOR.items())
    # 앵커 범위 밖은 경계값으로 클램프, 사이는 선형보간.
    if multiplier <= anchors[0][0]:
        lo_m, lo_b = 1.0, 0.0
        hi_m, hi_b = anchors[0]
    else:
        lo_m, lo_b = anchors[0]
        hi_m, hi_b = anchors[-1]
        for i in range(len(anchors) - 1):
            if anchors[i][0] <= multiplier <= anchors[i + 1][0]:
                lo_m, lo_b = anchors[i]
                hi_m, hi_b = anchors[i + 1]
                break
        else:
            # 최대 앵커 초과: 마지막 기울기로 외삽.
            lo_m, lo_b = anchors[-2]
            hi_m, hi_b = anchors[-1]
    frac = (multiplier - lo_m) / (hi_m - lo_m)
    return lo_b + frac * (hi_b - lo_b)
