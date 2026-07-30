"""30개 회차 베이스 데이터를 생성해 data/ 에 CSV로 저장."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import simulation_config as C  # noqa: E402
from src.data_generator import generate_all, save_all  # noqa: E402

# PR #1의 실적 데이터(data/history.csv 등)와 섞이지 않도록 하위 폴더로 분리한다.
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "simulation")


def main() -> None:
    datasets = generate_all()
    paths = save_all(datasets, DATA_DIR)
    n_hit = sum(1 for d in datasets if d.scenario == "hit")
    print(f"생성 완료: {len(paths)}개 데이터셋 (흥행 {n_hit} / 비흥행 {len(datasets)-n_hit}) -> {DATA_DIR}")
    for d in datasets:
        recipients = sum(1 for b in d.benefits if b > 0)
        print(f"  round {d.round_idx:02d} [{d.scenario:4s}] 신청 {d.n_customers:,}명, "
              f"수령 {recipients:,}명, 현행혜택합 {sum(d.benefits):,}원")


if __name__ == "__main__":
    main()
