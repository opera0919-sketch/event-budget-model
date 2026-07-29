"""대표 리워드 케이스들을 30회차 데이터에 시뮬레이션하고 결과 CSV 저장."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.cases import recommended_plans, reference_cases  # noqa: E402
from src.quality import design_report  # noqa: E402
from src.data_generator import generate_all  # noqa: E402
from src.report import write_metrics_csv, won  # noqa: E402
from src.simulation import build_cache, evaluate_structure  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(ROOT, "results")


def main() -> None:
    datasets = generate_all()
    caches = [build_cache(d) for d in datasets]
    transfers = [t for d in datasets for t in d.transfers]

    structures = reference_cases() + recommended_plans()
    metrics = [evaluate_structure(caches, s) for s in structures]
    path = os.path.join(RESULTS_DIR, "case_metrics.csv")
    write_metrics_csv(metrics, path)
    print(f"케이스 시뮬레이션 완료 -> {path}\n")

    base = metrics[0]
    print(f"{'케이스':<22} {'예산':>9} {'현행比':>7} {'매력도':>7} {'효율':>7} {'율SD':>7} {'절벽':>6}")
    for s, m in zip(structures, metrics):
        q = design_report(s, transfers)
        print(f"{m.name:<22} {won(m.budget_mean):>9} "
              f"{m.budget_mean/base.budget_mean*100:>6.0f}% "
              f"{m.attractiveness_index*100:>6.1f}% {m.efficiency:>6.0f}배 "
              f"{q['sd']:>6.3f} {q['max_jump']:>5.2f}x")


if __name__ == "__main__":
    main()
