"""가상 베이스 데이터 생성.

30개 회차(흥행 15 / 비흥행 15) 각각에 대해 고객ID / 타사이전금액 / 혜택금액(현행 기준)
데이터셋을 만들어 CSV로 저장한다.
"""
from __future__ import annotations

import csv
import os
import random
from dataclasses import dataclass
from typing import List

from config import simulation_config as C
from src import distribution as dist
from src.reward_engine import CURRENT_STRUCTURE, reward_for


@dataclass
class RoundDataset:
    round_idx: int
    scenario: str            # "hit" | "flop"
    customer_ids: List[str]
    transfers: List[int]
    benefits: List[int]      # 현행 구조 기준 혜택금액(리워드)

    @property
    def n_customers(self) -> int:
        return len(self.transfers)


def scenario_for_round(round_idx: int) -> str:
    """회차 인덱스 -> 시나리오 라벨(흥행 HIT_ROUNDS개, 나머지 비흥행)."""
    return "hit" if round_idx < C.HIT_ROUNDS else "flop"


def generate_round(round_idx: int) -> RoundDataset:
    rng = random.Random(C.round_seed(round_idx))
    scenario = scenario_for_round(round_idx)
    cfg = C.SCENARIOS[scenario]

    n_lo, n_hi = cfg["n_customers_range"]
    n_customers = rng.randint(n_lo, n_hi)

    probs = dist.oscillate_distribution(rng, upper_tilt=cfg["upper_tilt"])
    transfers = dist.generate_transfers(rng, probs, n_customers)

    benefits = [reward_for(t, CURRENT_STRUCTURE) for t in transfers]
    customer_ids = [f"R{round_idx:02d}-{i:05d}" for i in range(n_customers)]

    return RoundDataset(round_idx, scenario, customer_ids, transfers, benefits)


def generate_all() -> List[RoundDataset]:
    return [generate_round(i) for i in range(C.N_ROUNDS)]


def save_round_csv(ds: RoundDataset, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"round_{ds.round_idx:02d}_{ds.scenario}.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["고객ID", "타사이전금액", "혜택금액"])
        for cid, t, b in zip(ds.customer_ids, ds.transfers, ds.benefits):
            writer.writerow([cid, t, b])
    return path


def save_all(datasets: List[RoundDataset], out_dir: str) -> List[str]:
    return [save_round_csv(ds, out_dir) for ds in datasets]
