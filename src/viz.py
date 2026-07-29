"""Pareto frontier 시각화.

예산(x) vs 유치 이전금액(y) 산점도에 Pareto frontier와 목표별 최적안·현행을
표시한다. matplotlib 가용 시 PNG를 저장하고, 미가용 시 None을 반환한다.
(엑셀 워크북은 별도로 native scatter chart 폴백을 제공.)

한글 폰트 부재 환경을 고려해 축/범례는 영문으로 표기한다.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

BUDGET_UNIT = 1e8      # 억원
TRANSFER_UNIT = 1e12   # 조원


def render_pareto_png(all_results, pareto, objectives, baseline, out_path: str) -> Optional[str]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None

    xs = [m.budget_mean / BUDGET_UNIT for _, m in all_results]
    ys = [m.transfer_mean / TRANSFER_UNIT for _, m in all_results]

    pf = sorted(pareto, key=lambda sm: sm[1].budget_mean)
    pfx = [m.budget_mean / BUDGET_UNIT for _, m in pf]
    pfy = [m.transfer_mean / TRANSFER_UNIT for _, m in pf]

    fig, ax = plt.subplots(figsize=(9, 6))
    ax.scatter(xs, ys, s=12, c="#c7d2e0", alpha=0.6, label="All candidates", zorder=1)
    ax.plot(pfx, pfy, "-o", color="#2c6fbb", ms=4, lw=1.6, label="Pareto frontier", zorder=2)

    # 현행
    ax.scatter([baseline.budget_mean / BUDGET_UNIT], [baseline.transfer_mean / TRANSFER_UNIT],
               s=120, marker="*", color="#111111", label="Current", zorder=4)

    # 목표별 최적안
    colors = {"A_min_budget": "#e4572e", "B_max_efficiency": "#17a398", "C_target_budget": "#f2a900"}
    labels = {"A_min_budget": "A: Min budget", "B_max_efficiency": "B: Max efficiency", "C_target_budget": "C: Target budget"}
    for key, res in objectives.items():
        m = res.metrics
        ax.scatter([m.budget_mean / BUDGET_UNIT], [m.transfer_mean / TRANSFER_UNIT],
                   s=90, marker="D", color=colors.get(key, "#888"), edgecolors="white",
                   label=labels.get(key, key), zorder=5)

    ax.set_xlabel("Budget (100M KRW, tax-grossed)")
    ax.set_ylabel("Attracted transfer (trillion KRW)")
    ax.set_title("Budget vs Attracted Transfer — Pareto Frontier\n(slope from origin = efficiency)")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="lower right", fontsize=9)
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)
    return out_path
