"""대표 리워드 케이스들을 30회차 데이터에 시뮬레이션하고 결과 CSV 저장."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.cases import reference_cases  # noqa: E402
from src.data_generator import generate_all  # noqa: E402
from src.report import write_metrics_csv, won  # noqa: E402
from src.simulation import build_cache, evaluate_structure  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(ROOT, "results")


def main() -> None:
    caches = [build_cache(d) for d in generate_all()]
    metrics = [evaluate_structure(caches, s) for s in reference_cases()]
    path = os.path.join(RESULTS_DIR, "case_metrics.csv")
    write_metrics_csv(metrics, path)
    print(f"케이스 시뮬레이션 완료 -> {path}\n")
    base = metrics[0]
    print(f"{'케이스':<20} {'예산':>10} {'현행比':>7} {'매력도':>7} {'효율':>7}")
    for m in metrics:
        print(f"{m.name:<20} {won(m.budget_mean):>10} "
              f"{m.budget_mean/base.budget_mean*100:>6.0f}% "
              f"{m.attractiveness_index*100:>6.1f}% {m.efficiency:>6.0f}배")


if __name__ == "__main__":
    main()
