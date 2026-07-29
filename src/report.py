"""결과 CSV 저장 + 한글 마크다운 추천 리포트 생성."""
from __future__ import annotations

import csv
import os
from typing import Dict, List, Sequence, Tuple

from config import simulation_config as C
from src.reward_engine import RewardStructure, budget_amount
from src.simulation import AggregateMetrics, DatasetCache, evaluate_structure


# ---------------------------------------------------------------------------
# 포맷 헬퍼
# ---------------------------------------------------------------------------
def won(x: float) -> str:
    x = float(x)
    if abs(x) >= 1e8:
        return f"{x/1e8:.2f}억"
    if abs(x) >= 1e4:
        return f"{x/1e4:.0f}만"
    return f"{x:.0f}"


def pct(x: float) -> str:
    return f"{x*100:.1f}%"


def tier_lines(s: RewardStructure) -> str:
    """리워드 구조를 (인정금액 구간 → 리워드) 목록으로."""
    ths = list(s.thresholds()) + [None]
    rws = s.rewards()
    parts = []
    for i, r in enumerate(rws):
        lo = won(ths[i])
        hi = "이상" if ths[i + 1] is None else f"~{won(ths[i+1])} 미만"
        parts.append(f"{lo} {hi}: {won(r)}")
    mult = "미적용" if s.multiplier <= 1.0 else f"{s.multiplier}배(≥{won(s.multiplier_threshold)} 인정)"
    return " / ".join(parts) + f"  [배수 {mult}]"


# ---------------------------------------------------------------------------
# CSV 저장
# ---------------------------------------------------------------------------
def write_metrics_csv(rows: List[AggregateMetrics], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    dict_rows = [m.as_row() for m in rows]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(dict_rows[0].keys()))
        w.writeheader()
        w.writerows(dict_rows)


def write_pareto_csv(pareto: Sequence[Tuple[RewardStructure, AggregateMetrics]], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["예산_평균", "유치이전금액_평균", "효율_배", "매력도지수", "배수", "리워드벡터"])
        for s, m in sorted(pareto, key=lambda sm: sm[1].budget_mean):
            w.writerow([round(m.budget_mean), round(m.transfer_mean), round(m.efficiency, 1),
                        round(m.attractiveness_index, 4), s.multiplier,
                        "|".join(str(r) for r in s.rewards())])


# ---------------------------------------------------------------------------
# 민감도 (탄력성 변형)
# ---------------------------------------------------------------------------
def sensitivity(caches: Sequence[DatasetCache],
                named: List[Tuple[str, RewardStructure]]) -> Dict[str, Dict[str, AggregateMetrics]]:
    """탄력성 완만/기본/급격 3종에 대한 각 구조 지표."""
    out: Dict[str, Dict[str, AggregateMetrics]] = {}
    for variant, params in C.ELASTICITY.items():
        out[variant] = {name: evaluate_structure(caches, s, params) for name, s in named}
    return out


# ---------------------------------------------------------------------------
# 마크다운 리포트
# ---------------------------------------------------------------------------
def build_markdown(out, caches, case_metrics: List[AggregateMetrics]) -> str:
    b = out.baseline
    L: List[str] = []
    ap = b.applicants_mean

    L.append("# 연금 타사이전 리워드 최적화 — 시뮬레이션 결과 리포트\n")
    L.append(f"- 생성일: 자동 생성 | 시뮬레이션 반복: **{C.N_ROUNDS}회** "
             f"(흥행 {C.HIT_ROUNDS} / 비흥행 {C.N_ROUNDS - C.HIT_ROUNDS})\n")

    # 1. 배경/가정
    L.append("## 1. 목적과 가정\n")
    L.append("- **목적**: 실무 적용 가능한 최적의 타사이전금액 리워드 지급구조(구간·조건·수준) 도출.")
    L.append("- **예산 정의(제세 포함)**: 리워드 ≥ 5만원이면 예산 = `(리워드/0.78)*0.22 + 리워드` "
             "(기타소득 제세 22% 그로스업). 고객 수령액은 리워드 그대로.")
    L.append("- **수요반응(탄력성)**: 비선형 로지스틱 S-커브. 리워드 소폭 하향에는 **sticky(비탄력)**, "
             "대폭 하향 시 참여 급감. 현행 대비 정규화.")
    L.append("- **구간내 분포**: 순입금 구간 내부는 하한(최소 기준) 쪽으로 쏠린 triangular 분포 "
             "(최소 기준만 맞추려는 성향 반영).")
    L.append("- **배수 참여 보너스**: 배수 적용 시 수혜 고객 참여율 가산(1.5배 +3%, 2.0배 +5%).")
    L.append(f"- **제약(매력도 하한)**: 목표 A ≥ {pct(C.OBJ_A_ATTRACT_FLOOR)}, B·C ≥ {pct(C.OBJ_MIN_ATTRACTIVENESS)} "
             "(현행 대비 유치 이전금액).\n")

    # 2. 현행 기준
    L.append("## 2. 현행 구조 기준선\n")
    L.append(f"- 구조: {tier_lines_current()}")
    L.append(f"- 평균 신청자: **{ap:,.0f}명**, 리워드 수령자: {b.recipients_mean:,.0f}명")
    L.append(f"- 평균 예산(제세 포함): **{won(b.budget_mean)}원** (1인당 {won(b.budget_mean/ap)}원)")
    L.append(f"- 평균 유치 이전금액: {won(b.transfer_mean)}원 | 비용률 {b.cost_rate_pct:.3f}% | "
             f"효율 {b.efficiency:.0f}배 | CPA {won(b.cpa)}원")
    L.append(f"- worst-case 예산(최대/최소): {won(b.budget_worst)} / {won(b.budget_best)}\n")

    # 3. 케이스 비교표
    L.append("## 3. 리워드 케이스 비교 (30회 평균, 제세 포함 예산 기준)\n")
    L.append(_metrics_table(case_metrics, b))
    L.append("")

    # 4. 목표별 추천 3안
    L.append("## 4. 목표별 추천 구조 (2단계 탐색 결과)\n")
    titles = {
        "A_min_budget": "A. 예산 최소화 (매력도 유지)",
        "B_max_efficiency": "B. 비용효율(ROI) 최대화",
        "C_target_budget": "C. 목표예산 달성 (현행 ≈75%)",
    }
    for key in ["A_min_budget", "B_max_efficiency", "C_target_budget"]:
        res = out.objectives[key]
        m = res.metrics
        L.append(f"### {titles[key]}\n")
        L.append(f"- **구조**: {tier_lines(res.structure)}")
        L.append(f"- 예산 **{won(m.budget_mean)}** (현행 대비 {m.budget_mean/b.budget_mean*100:.0f}%, "
                 f"worst {won(m.budget_worst)} / best {won(m.budget_best)})")
        L.append(f"- 유치 이전금액 {won(m.transfer_mean)} (매력도 {pct(m.attractiveness_index)}) | "
                 f"효율 {m.efficiency:.0f}배 | ROI {m.roi_pct:.0f}% | CPA {won(m.cpa)} | 평균이전 {won(m.avg_transfer)}")
        L.append(f"- **근거**: {res.rationale}\n")

    # 5. Pareto
    L.append("## 5. 예산 ↔ 유치금액 Pareto Frontier\n")
    L.append(f"- 비지배 후보 {len(out.pareto)}개. 예산이 낮을수록 효율(원점 기울기)은 높아지나 "
             "유치금액(매력도)은 감소하는 상충 관계.")
    L.append("- sticky 수요 가정상 **예산 최소화와 효율 최대화는 정렬**되어, 실질 의사결정 상충은 "
             "'예산 vs 매력도'임. 차트: `results/pareto.png` (데이터 `results/pareto.csv`).\n")

    # 6. 민감도
    L.append("## 6. 탄력성 민감도\n")
    named = [("현행", _current()), ("A안", out.objectives["A_min_budget"].structure),
             ("B안", out.objectives["B_max_efficiency"].structure),
             ("C안", out.objectives["C_target_budget"].structure)]
    sens = sensitivity(caches, named)
    L.append("| 탄력성 | 구조 | 예산 | 매력도 |")
    L.append("|---|---|---|---|")
    for variant in ["gentle", "base", "steep"]:
        for name, _ in named:
            mm = sens[variant][name]
            L.append(f"| {variant} | {name} | {won(mm.budget_mean)} | {pct(mm.attractiveness_index)} |")
    L.append("")

    # 7. 권고
    L.append("## 7. 실무 권고\n")
    a = out.objectives["A_min_budget"]
    L.append(f"- **기본 권고: A안** — 매력도 {pct(a.metrics.attractiveness_index)}를 유지하며 "
             f"예산을 현행 {won(b.budget_mean)}→{won(a.metrics.budget_mean)}으로 "
             f"약 {(1-a.metrics.budget_mean/b.budget_mean)*100:.0f}% 절감.")
    L.append("- **배수 조건**: 배수는 최대 비용 레버이자 매력도 요인. 완전 제거보다 1.5배 유지 + "
             "상위 티어 리워드 소폭 조정이 예산·매력도 균형에 유리.")
    L.append("- 공격적 예산 절감이 필요하면 B안(효율 최대), 목표예산이 정해져 있으면 C안 채택.\n")

    L.append("## 부록. 실행 방법\n")
    L.append("```\npython scripts/run_generate_data.py\npython scripts/run_simulation.py\n"
             "python scripts/run_optimize.py\npython scripts/build_excel.py\n"
             "python -m pytest tests/ -q\n```\n")
    return "\n".join(L)


def _metrics_table(metrics: List[AggregateMetrics], baseline: AggregateMetrics) -> str:
    head = "| 케이스 | 예산 | 현행比 | 유치금액 | 매력도 | 효율(배) | ROI% | CPA | worst예산 |"
    sep = "|---|---|---|---|---|---|---|---|---|"
    lines = [head, sep]
    for m in metrics:
        lines.append(
            f"| {m.name} | {won(m.budget_mean)} | {m.budget_mean/baseline.budget_mean*100:.0f}% | "
            f"{won(m.transfer_mean)} | {pct(m.attractiveness_index)} | {m.efficiency:.0f} | "
            f"{m.roi_pct:.0f} | {won(m.cpa)} | {won(m.budget_worst)} |")
    return "\n".join(lines)


def _current() -> RewardStructure:
    from src.reward_engine import CURRENT_STRUCTURE
    return CURRENT_STRUCTURE


def tier_lines_current() -> str:
    return tier_lines(_current())
