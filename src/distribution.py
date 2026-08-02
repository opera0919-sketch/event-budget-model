"""타사이전금액 분포 생성.

실측 24구간 분포에 회차별 진동(노이즈)과 흥행/비흥행 시나리오 틸트를 적용하고,
구간 내부는 하한 쪽으로 쏠린 triangular/beta 분포로 개별 금액을 샘플링한다.
"""
from __future__ import annotations

import random
from typing import Dict, List

from config import simulation_config as C


def oscillate_distribution(rng: random.Random, upper_tilt: float = 0.0) -> List[float]:
    """기준 분포에 진동 + 시나리오 틸트를 적용한 회차 분포(합계 1)."""
    probs: List[float] = []
    n = len(C.BASE_DISTRIBUTION)
    for b, p in enumerate(C.BASE_DISTRIBUTION):
        if p <= 0.0:
            probs.append(0.0)
            continue
        delta = C.OSCILLATION_DELTA_SPARSE if p < C.SPARSE_THRESHOLD else C.OSCILLATION_DELTA
        noise = rng.uniform(-delta, delta)
        # 상위 구간(인덱스가 클수록)으로 갈수록 틸트 강도 증가.
        tilt = upper_tilt * (b / (n - 1))
        val = p * (1.0 + noise) * (1.0 + tilt)
        probs.append(max(val, 0.0))
    total = sum(probs)
    return [v / total for v in probs]


def allocate_counts(rng: random.Random, probs: List[float], n_customers: int) -> List[int]:
    """확률 분포에 따라 정수 인원을 배분(합계 = n_customers)."""
    # 기대값 기반 + 잔여를 최대소수부 순으로 배분.
    raw = [p * n_customers for p in probs]
    counts = [int(x) for x in raw]
    remainder = n_customers - sum(counts)
    fracs = sorted(range(len(raw)), key=lambda i: raw[i] - counts[i], reverse=True)
    for i in range(remainder):
        counts[fracs[i % len(fracs)]] += 1
    return counts


def sample_amount_in_bracket(rng: random.Random, bracket_idx: int) -> int:
    """구간 내부에서 하한 쪽으로 쏠린 금액 1건 샘플링(만원 단위)."""
    low = bracket_idx * C.BRACKET_WIDTH
    high = low + C.BRACKET_WIDTH
    if C.INTRA_BRACKET_MODE == "beta":
        frac = rng.betavariate(C.BETA_ALPHA, C.BETA_BETA)
        amount = low + frac * C.BRACKET_WIDTH
    else:  # triangular (기본)
        mode = low + C.TRIANGULAR_MODE_RATIO * C.BRACKET_WIDTH
        amount = rng.triangular(low, high, mode)
    # 만원 단위 반올림, 상한 미만 보정.
    amount = round(amount / C.ROUND_UNIT) * C.ROUND_UNIT
    amount = min(max(amount, low), high - C.ROUND_UNIT)
    return int(amount)


def generate_transfers(rng: random.Random, probs: List[float], n_customers: int) -> List[int]:
    """회차 전체 고객의 타사이전금액 리스트."""
    counts = allocate_counts(rng, probs, n_customers)
    transfers: List[int] = []
    for b, cnt in enumerate(counts):
        for _ in range(cnt):
            transfers.append(sample_amount_in_bracket(rng, b))
    rng.shuffle(transfers)
    return transfers


def bracket_of(amount: float) -> int:
    """금액이 속한 1천만원 단위 구간 인덱스(상한 초과는 마지막 구간)."""
    idx = int(amount // C.BRACKET_WIDTH)
    return min(idx, len(C.BASE_DISTRIBUTION) - 1)
