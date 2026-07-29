"""2단계 최적화 실행 -> 목표별 최적안, Pareto, 리포트 생성."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.cases import reference_cases  # noqa: E402
from src.data_generator import generate_all  # noqa: E402
from src.optimizer import optimize  # noqa: E402
from src.report import build_markdown, write_metrics_csv, write_pareto_csv  # noqa: E402
from src.simulation import build_cache, evaluate_structure  # noqa: E402
from src.viz import render_pareto_png  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(ROOT, "results")
REPORT_DIR = os.path.join(ROOT, "report")


def main() -> None:
    caches = [build_cache(d) for d in generate_all()]
    print("최적화 탐색 중...")
    out = optimize(caches, verbose=True)

    # 케이스 지표(리포트 표용)
    case_metrics = [evaluate_structure(caches, s) for s in reference_cases()]

    os.makedirs(RESULTS_DIR, exist_ok=True)
    os.makedirs(REPORT_DIR, exist_ok=True)

    write_metrics_csv(case_metrics, os.path.join(RESULTS_DIR, "case_metrics.csv"))
    write_pareto_csv(out.pareto, os.path.join(RESULTS_DIR, "pareto.csv"))

    # 목표별 최적안 지표 CSV
    obj_metrics = [out.baseline] + [out.objectives[k].metrics for k in
                                    ["A_min_budget", "B_max_efficiency", "C_target_budget"]]
    for name, key in zip(["현행", "A_예산최소", "B_효율최대", "C_목표예산"],
                         [None, "A_min_budget", "B_max_efficiency", "C_target_budget"]):
        if key:
            out.objectives[key].metrics.name = name
    out.baseline.name = "현행"
    write_metrics_csv(obj_metrics, os.path.join(RESULTS_DIR, "objective_optima.csv"))

    png = render_pareto_png(out.all_evaluated, out.pareto, out.objectives, out.baseline,
                            os.path.join(RESULTS_DIR, "pareto.png"))
    print("Pareto 차트:", png or "(matplotlib 미가용 - 엑셀 차트로 대체)")

    md = build_markdown(out, caches, case_metrics)
    report_path = os.path.join(REPORT_DIR, "recommendation_ko.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(md)
    print("리포트 생성:", report_path)

    print("\n=== 목표별 최적안 요약 ===")
    for key in ["A_min_budget", "B_max_efficiency", "C_target_budget"]:
        r = out.objectives[key]
        print(f"[{key}] {r.rationale}")


if __name__ == "__main__":
    main()
