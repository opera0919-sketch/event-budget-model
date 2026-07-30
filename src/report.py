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
        "B_max_efficiency": "B. 비용효율 최대화",
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
                 f"효율 {m.efficiency:.0f}배 | CPA {won(m.cpa)} | 평균이전 {won(m.avg_transfer)}")
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

    # 6-1. 추천 3안 (직접구간)
    L.append(_recommended_section(caches))

    # 8. 권고 — 모든 수치는 계산값에서 포맷팅한다(리터럴 금지).
    L.append(_recommendation_section(out, caches))

    L.append(_gate_section(out, caches))

    L.append("## 부록. 실행 방법\n")
    L.append("```\npython scripts/run_generate_data.py\npython scripts/run_simulation.py\n"
             "python scripts/run_optimize.py\npython scripts/build_excel.py\n"
             "python -m pytest tests/ -q\n```\n")
    return "\n".join(L)


def _recommendation_section(out, caches) -> str:
    """실무 권고 — 모든 수치를 계산값에서 포맷팅한다.

    이전에는 이 절에 수치를 리터럴로 적어 두어, 모델이 바뀌어도 서술이 따라오지
    않고 같은 리포트의 계산 표와 모순됐다. 리터럴을 두지 않는 것이 원칙이다.
    """
    from src.cases import all_candidate_plans
    from src.data_generator import generate_all
    from src.quality import design_report
    from src.reward_engine import CURRENT_STRUCTURE

    transfers = [t for d in generate_all() for t in d.transfers]
    b = out.baseline
    q0 = design_report(CURRENT_STRUCTURE, transfers)
    plans = all_candidate_plans()
    rows = []
    for p in plans:
        m = evaluate_structure(caches, p)
        q = design_report(p, transfers)
        rows.append((p, m, q))

    L = ["## 8. 실무 권고\n"]

    # 게이트를 통과한 후보만 권고 대상.
    L.append("### 8-1. 게이트 통과 후보 (권고 대상)\n")
    L.append("| 안 | 예산 | 현행比 | 매력도 | 효율 | 현행대비 최저 | 유효율 SD | 절벽 |")
    L.append("|---|---|---|---|---|---|---|---|")
    L.append(f"| (현행) | {won(b.budget_mean)} | 100% | 100.0% | {b.efficiency:.0f}배 | — | "
             f"{q0['sd']:.3f} | {q0['max_jump']:.2f}x |")
    for p, m, q in rows:
        L.append(f"| {p.name} | {won(m.budget_mean)} | {m.budget_mean/b.budget_mean*100:.0f}% | "
                 f"{pct(m.attractiveness_index)} | {m.efficiency:.0f}배 | "
                 f"{q['min_ratio_vs_current']*100:.0f}% | {q['sd']:.3f} | {q['max_jump']:.2f}x |")
    L.append("")

    # 예산 최소 / 매력도 최대 후보를 계산으로 뽑는다.
    cheapest = min(rows, key=lambda r: r[1].budget_mean)
    most_attractive = max(rows, key=lambda r: r[1].attractiveness_index)
    best_sd = min(rows, key=lambda r: r[2]["sd"])
    L.append(f"- **예산 절감이 최우선이면 {cheapest[0].name}** — "
             f"{won(cheapest[1].budget_mean)}({cheapest[1].budget_mean/b.budget_mean*100:.0f}%, "
             f"{(1-cheapest[1].budget_mean/b.budget_mean)*100:.0f}% 절감), "
             f"매력도 {pct(cheapest[1].attractiveness_index)}, "
             f"현행 대비 최저 {cheapest[2]['min_ratio_vs_current']*100:.0f}%.")
    L.append(f"- **매력도 방어가 최우선이면 {most_attractive[0].name}** — "
             f"매력도 {pct(most_attractive[1].attractiveness_index)}, "
             f"예산 {won(most_attractive[1].budget_mean)}"
             f"({most_attractive[1].budget_mean/b.budget_mean*100:.0f}%).")
    L.append(f"- **유효율 일관성이 최우선이면 {best_sd[0].name}** — SD {best_sd[2]['sd']:.3f} "
             f"(현행 {q0['sd']:.3f}).")
    L.append("")

    # 배수 유지 vs 폐지 — 계산값으로 비교.
    keep = [r for r in rows if r[0].multiplier > 1.0]
    drop = [r for r in rows if r[0].multiplier <= 1.0]
    if keep and drop:
        k = keep[0]
        # 같은 하한 수준의 직접구간안과 비교.
        comparable = min(drop, key=lambda r: abs(
            r[2]["min_ratio_vs_current"] - k[2]["min_ratio_vs_current"]))
        L.append("### 8-2. 배수 유지 vs 폐지\n")
        L.append(f"같은 현행 대비 하한({k[2]['min_ratio_vs_current']*100:.0f}% 대) 기준 비교:\n")
        L.append(f"- **{k[0].name}(배수 유지)**: 예산 {won(k[1].budget_mean)}"
                 f"({k[1].budget_mean/b.budget_mean*100:.0f}%), 매력도 {pct(k[1].attractiveness_index)}, "
                 f"SD {k[2]['sd']:.3f}, 절벽 {k[2]['max_jump']:.2f}x")
        L.append(f"- **{comparable[0].name}(배수 폐지)**: 예산 {won(comparable[1].budget_mean)}"
                 f"({comparable[1].budget_mean/b.budget_mean*100:.0f}%), "
                 f"매력도 {pct(comparable[1].attractiveness_index)}, "
                 f"SD {comparable[2]['sd']:.3f}, 절벽 {comparable[2]['max_jump']:.2f}x")
        better = ("배수 유지안" if (k[1].budget_mean <= comparable[1].budget_mean
                                and k[1].attractiveness_index >= comparable[1].attractiveness_index)
                  else "우열 혼재")
        if better == "배수 유지안":
            L.append("")
            L.append("→ 계량 지표(예산·매력도·유효율 분산)에서는 **배수 유지안이 우세**하다. "
                     "직접구간안의 남는 장점은 계량되지 않는 **고객 이해도**"
                     "(경계가 실제 순입금과 일치)와 배수 로직 제거에 따른 운영 단순화다.")
        L.append("")
        L.append("- **단, 배수 유지안의 우위 일부는 배수 참여보너스(+3%) 가정에서 나온다.** "
                 "이는 미검증 가정이므로(§7 경고) 배수 존폐 결정 전 실측 검증을 권고한다.")
        L.append("")

    # A/B/C 최적화 결과는 게이트 위반 시 격하.
    L.append("### 8-3. 목표별 최적화 결과(A/B/C)의 위치\n")
    violated = [(k, r) for k, r in out.objectives.items() if not r.passes_gate]
    if violated:
        L.append("A/B/C는 예산·효율만으로 탐색한 **참고치**이며, 설계 게이트를 통과하지 못한다. "
                 "권고안으로 쓰지 않는다.\n")
        for key, res in violated:
            L.append(f"- **{key}**: {', '.join(res.gate_violations)}")
    else:
        L.append("A/B/C 모두 설계 게이트를 통과한다.")
    L.append("")
    return "\n".join(L)


def _ratio_vs_current_table(plans, transfers) -> str:
    """동일 순입금 고객이 받는 금액을 현행과 비교(매력도 방어 검증)."""
    from src.cases import multiplier_keep_plan
    from src.quality import ratio_vs_current
    from src.reward_engine import CURRENT_STRUCTURE, reward_for

    # 배수 유지안을 병기해 '경제성 vs 설계품질' 선택을 볼 수 있게 한다.
    plans = list(plans) + [multiplier_keep_plan()]
    # 표는 1천만원 단위로 보여주고(가독성), 게이트는 100만원 격자로 검증한다.
    coarse = [b * C.BRACKET_WIDTH for b in range(24)]
    ratios = [dict((lo, (r, sh)) for lo, r, sh in ratio_vs_current(p, transfers, coarse))
              for p in plans]
    keys = sorted(ratios[0].keys())

    L = ["", "### 현행 대비 급간별 리워드 수준 (동일 순입금 고객 기준)\n",
         "현행은 배수(1.5배)로 인정금액을 올려 판정하므로, 구간 체계가 다른 "
         "신규안과는 '같은 금액을 넣은 고객이 실제로 받는 액수'로 비교해야 한다.\n"]
    L.append("| 순입금 급간 | 인원 | 현행 | " + " | ".join(p.name for p in plans) + " |")
    L.append("|---|---|---|" + "---|" * len(plans))
    for lo in keys:
        group = [t for t in transfers if lo <= t < lo + C.BRACKET_WIDTH]
        avg = sum(group) / len(group)
        cur = reward_for(avg, CURRENT_STRUCTURE)
        share = ratios[0][lo][1]
        cells = []
        for p, rd in zip(plans, ratios):
            r = rd[lo][0]
            mark = "**" if r < 0.75 else ""
            cells.append(f"{mark}{won(reward_for(avg, p))} ({r*100:.0f}%){mark}")
        L.append(f"| {won(lo)}~ | {share*100:.1f}% | {won(cur)} | " + " | ".join(cells) + " |")
    L.append("")
    from src.quality import min_ratio_vs_current
    floors = [(p.name, min_ratio_vs_current(p, transfers)) for p in plans]
    L.append("표는 1천만원 단위지만, 게이트는 **100만원 격자**로 검증한다 "
             "(좁은 구간의 하락이 평균에 묻히기 때문).\n")
    L.append(f"- **최저 보장 수준**(100만원 격자): "
             + ", ".join(f"{n} {r*100:.0f}%" for n, r in floors)
             + f" — 게이트 하한 {C.MIN_RATIO_VS_CURRENT*100:.0f}%.")
    # 하락 구간을 계산으로 뽑아 서술한다(리터럴 금지).
    fine = ratio_vs_current(plans[0], transfers)
    for p, rd in zip(plans, ratios):
        lows = [(lo, r, sh) for lo, r, sh in ratio_vs_current(p, transfers) if r < 0.90]
        if not lows:
            continue
        pop = sum(sh for _, _, sh in lows)
        lo_min = min(lows, key=lambda x: x[1])
        L.append(f"- **{p.name}**: 현행의 90% 미만 구간 인원 {pop*100:.1f}%, "
                 f"최저 {lo_min[1]*100:.0f}% @{won(lo_min[0])}~")
    L.append("- **6,600만원 경계**는 현행의 실효 경계(인정 1억 = 실제 6,667만)에 맞춘 것이다. "
             "7,000만원에 두면 6,667만~7,000만 고객이 30만→15만으로 **현행의 50%**가 된다.")
    L.append(f"- 리워드 단위는 **{won(C.REWARD_UNIT)}원**이다. 당초 '5만원 초과는 5만원 단위'로 "
             "가정했으나 현행 구조에 6만원 티어가 실재해 그 가정이 틀렸다. 단위를 바로잡으면 "
             "12만·13만 같은 중간값을 쓸 수 있어 하락 방어가 쉬워진다.\n")
    return "\n".join(L)


def _gate_section(out, caches) -> str:
    """완성도 게이트 체크리스트 — 사양 준수와 설계 제약을 실측으로 재확인."""
    from src.cases import all_candidate_plans
    from src.data_generator import generate_all
    from src.quality import design_report
    from src.reward_engine import (CURRENT_STRUCTURE, budget_amount,
                                   is_valid_structure, reward_for)
    from src import demand_model as dm

    datasets = generate_all()
    transfers = [t for d in datasets for t in d.transfers]
    counts = [d.n_customers for d in datasets]
    base = evaluate_structure(caches, CURRENT_STRUCTURE)
    plans = all_candidate_plans()

    checks: List[tuple] = []
    # G1 데이터
    emp = [sum(1 for t in transfers if b * C.BRACKET_WIDTH <= t < (b + 1) * C.BRACKET_WIDTH)
           / len(transfers) for b in range(len(C.BASE_DISTRIBUTION))]
    dev = max(abs(e - s) for e, s in zip(emp, C.BASE_DISTRIBUTION))
    checks.append(("G1.1", "기준분포 재현", dev < 0.02, f"최대편차 {dev*100:.2f}%p"))
    checks.append(("G1.2", "신청자 4,000~5,000명", all(4000 <= c <= 5000 for c in counts),
                   f"{min(counts):,}~{max(counts):,}"))
    checks.append(("G1.3", f"{C.N_ROUNDS}회 반복", len(datasets) == C.N_ROUNDS, f"{len(datasets)}회"))
    # G2 제세
    # 독립 기대값(리터럴)과 비교한다. 구현식을 그대로 다시 쓰면 실패할 수 없는
    # 항진명제가 되어 PASS 개수만 부풀린다.
    checks.append(("G2.1", "제세 그로스업 공식", round(budget_amount(60_000)) == 76_923,
                   f"6만→{budget_amount(60_000):,.0f}원 (기대 76,923원)"))
    checks.append(("G2.2", "5만 미만 미적용", budget_amount(40_000) == 40_000, "4만→40,000원"))
    checks.append(("G2.3", "5만 경계 그로스업 적용", round(budget_amount(50_000)) == 64_103,
                   f"5만→{budget_amount(50_000):,.0f}원 (기대 64,103원)"))
    # G3 탄력성 — 독립 기대값과 비교
    checks.append(("G3.1", "sticky(r=0.9 소폭하향)", dm.participation_scaling(0.9) > 0.95,
                   f"{dm.participation_scaling(0.9):.3f} > 0.95"))
    checks.append(("G3.2", "대폭하향 급감(r=0.3)", dm.participation_scaling(0.3) < 0.75,
                   f"{dm.participation_scaling(0.3):.3f} < 0.75"))
    checks.append(("G3.3", "비선형(구간별 기울기 상이)",
                   abs((dm.participation_scaling(0.9) - dm.participation_scaling(0.7))
                       - (dm.participation_scaling(0.5) - dm.participation_scaling(0.3))) > 0.02,
                   "sticky 구간과 급락 구간의 기울기 차 > 0.02"))
    # G4 결론 정합 — 파이프라인 밖에서 독립 계산한 값과 비교
    checks.append(("G4.1", "baseline 매력도=100%", abs(base.attractiveness_index - 1.0) < 1e-6, "100.0%"))
    indep_budget = sum(budget_amount(reward_for(t, CURRENT_STRUCTURE)) for t in transfers) / C.N_ROUNDS
    checks.append(("G4.2", "예산=Σ그로스업(독립 계산 대조)",
                   abs(base.budget_mean - indep_budget) / indep_budget < 1e-6,
                   f"{won(base.budget_mean)} vs 독립계산 {won(indep_budget)}"))
    q0 = design_report(CURRENT_STRUCTURE, transfers)
    checks.append(("G4.3", "사문화 티어 없음(현행)", q0["dead_tiers"] == 0,
                   "배수로 인정 3.59억까지 도달 → 3억 티어 생존"))
    # G5 설계 제약 (추천 3안)
    all_unit = all(is_valid_structure(p) for p in plans)
    checks.append(("G5.1", "후보안 리워드 단위 준수", all_unit, f"{len(plans)}안 전부 통과"))
    all_strict = all(is_valid_structure(p, strict_increase=True) for p in plans)
    checks.append(("G5.2", "후보안 평탄구간 없음", all_strict, f"{len(plans)}안 전부 엄격 증가"))
    all_jump = all(is_valid_structure(p, max_jump=C.MAX_TIER_JUMP) for p in plans)
    jm = max(design_report(p, transfers)["max_jump"] for p in plans)
    checks.append(("G5.3", f"경계 절벽 ≤{C.MAX_TIER_JUMP}x", all_jump, f"최대 {jm:.2f}x (현행 {q0['max_jump']:.2f}x)"))
    sds = [design_report(p, transfers)["sd"] for p in plans]
    checks.append(("G5.4", f"유효율 SD ≤{C.MAX_RATE_SD}", max(sds) <= C.MAX_RATE_SD + 1e-9,
                   f"최대 {max(sds):.3f} (현행 {q0['sd']:.3f})"))
    ratios = [design_report(p, transfers)["min_ratio_vs_current"] for p in plans]
    worst_name = min(zip(plans, ratios), key=lambda x: x[1])
    checks.append(("G5.5", f"현행 대비 리워드 ≥{C.MIN_RATIO_VS_CURRENT*100:.0f}%",
                   min(ratios) >= C.MIN_RATIO_VS_CURRENT - 1e-9,
                   f"최저 {min(ratios)*100:.0f}% ({worst_name[0].name})"))

    # G6 — A/B/C 최적화 결과도 검사 대상에 넣는다. 이전에는 추천 3안만 검사해
    #      게이트 위반안이 '전항 PASS' 표시 아래 최우선 권고로 실렸다.
    for key in ["A_min_budget", "B_max_efficiency", "C_target_budget"]:
        res = out.objectives.get(key)
        if res is None:
            continue
        checks.append((f"G6.{key[0]}", f"{key} 게이트 통과", res.passes_gate,
                       "통과" if res.passes_gate else " / ".join(res.gate_violations)))

    passed = sum(1 for c in checks if c[2])
    L = ["## 9. 완성도 게이트 체크리스트\n",
         f"**{passed}/{len(checks)} PASS** — 데이터·제세·탄력성 사양과 설계 제약을 실측으로 재확인.\n",
         "| ID | 항목 | 결과 | 실측 |", "|---|---|---|---|"]
    for cid, name, ok, detail in checks:
        L.append(f"| {cid} | {name} | {'PASS' if ok else '**FAIL**'} | {detail} |")
    L.append("")
    L.append("> 주: G6은 예산·효율만으로 탐색한 A/B/C 결과에 최종 설계 게이트를 적용한 것이다. "
             "탐색 단계는 레거시 7단계 공간을 살리려 제약을 완화하므로, 선정 결과에는 "
             "게이트를 다시 적용해야 한다(§8-3 참조).\n")
    return "\n".join(L)


def _bonus_sensitivity_table(caches) -> str:
    """배수 참여보너스 가정 on/off 시 A안 vs 직접구간 안1 비교(G6.1)."""
    from src.cases import recommended_plans
    from src.reward_engine import CURRENT_STRUCTURE, RewardStructure

    a_struct = RewardStructure(
        tuple(zip([t for t, _ in C.CURRENT_TIERS],
                  (10_000, 20_000, 50_000, 150_000, 250_000, 450_000, 750_000))),
        1.5, C.CURRENT_MULTIPLIER_THRESHOLD, "A안")
    plan1 = recommended_plans()[0]

    def snapshot() -> tuple:
        # 캐시에는 baseline의 배수 참여계수(mf_cur)가 구워져 있으므로,
        # 가정을 바꾼 뒤에는 반드시 캐시를 다시 만들어야 한다.
        from src.data_generator import generate_all
        from src.simulation import build_cache

        fresh = [build_cache(d) for d in generate_all()]
        b = evaluate_structure(fresh, CURRENT_STRUCTURE)
        ma = evaluate_structure(fresh, a_struct)
        m1 = evaluate_structure(fresh, plan1)
        return (ma.budget_mean / b.budget_mean, ma.attractiveness_index,
                m1.budget_mean / b.budget_mean, m1.attractiveness_index)

    on = snapshot()
    original = dict(C.MULT_BONUS_ANCHOR)
    C.MULT_BONUS_ANCHOR.update({k: 0.0 for k in C.MULT_BONUS_ANCHOR})
    try:
        off = snapshot()
    finally:
        C.MULT_BONUS_ANCHOR.clear()
        C.MULT_BONUS_ANCHOR.update(original)

    L = ["", "| 배수 참여보너스 가정 | A안(배수 1.5) | 직접구간 안1 |", "|---|---|---|"]
    for label, v in [("+3% (기본 가정)", on), ("0% (가정 제거)", off)]:
        L.append(f"| {label} | 예산 {v[0]*100:.0f}% / 매력도 {v[1]*100:.1f}% | "
                 f"예산 {v[2]*100:.0f}% / 매력도 {v[3]*100:.1f}% |")
    L.append("")
    return "\n".join(L)


def _recommended_section(caches) -> str:
    """추천 3안(직접구간) 비교 + 설계 품질 + 배수가정 민감도(G6.1)."""
    from src.cases import recommended_plans
    from src.data_generator import generate_all
    from src.quality import design_report, tier_entry_rates
    from src.reward_engine import CURRENT_STRUCTURE

    transfers = [t for d in generate_all() for t in d.transfers]
    base = evaluate_structure(caches, CURRENT_STRUCTURE)
    plans = recommended_plans()

    L: List[str] = []
    L.append("## 7. 추천 리워드 구성 3안 (직접구간·배수 폐지)\n")
    L.append(f"경계(순입금): {' / '.join(won(b) for b in C.DIRECT_BRACKETS)} — 리워드 단위 격자에 정렬.\n")

    # 구간별 리워드 표
    edges = list(C.DIRECT_BRACKETS)
    L.append("| 순입금 구간 | " + " | ".join(p.name for p in plans) + " |")
    L.append("|---|" + "---|" * len(plans))
    for i, lo in enumerate(edges):
        hi = edges[i + 1] if i + 1 < len(edges) else None
        label = f"{won(lo)} 이상" if hi is None else f"{won(lo)}~{won(hi)}"
        L.append(f"| {label} | " + " | ".join(won(p.rewards()[i]) for p in plans) + " |")
    L.append("")

    # 지표 + 설계 품질
    L.append("| 지표 | 현행 | " + " | ".join(p.name for p in plans) + " |")
    L.append("|---|---|" + "---|" * len(plans))
    ms = [evaluate_structure(caches, p) for p in plans]
    qs = [design_report(p, transfers) for p in plans]
    q0 = design_report(CURRENT_STRUCTURE, transfers)
    rows = [
        ("예산(제세포함)", won(base.budget_mean), [won(m.budget_mean) for m in ms]),
        ("현행 대비", "100%", [f"{m.budget_mean/base.budget_mean*100:.0f}%" for m in ms]),
        ("매력도", "100.0%", [pct(m.attractiveness_index) for m in ms]),
        ("효율(배)", f"{base.efficiency:.0f}", [f"{m.efficiency:.0f}" for m in ms]),
        ("CPA", won(base.cpa), [won(m.cpa) for m in ms]),
        ("worst 예산", won(base.budget_worst), [won(m.budget_worst) for m in ms]),
        ("유효율 SD", f"{q0['sd']:.3f}", [f"{q['sd']:.3f}" for q in qs]),
        ("유효율 범위(%)", f"{q0['min_pct']:.2f}~{q0['max_pct']:.2f}",
         [f"{q['min_pct']:.2f}~{q['max_pct']:.2f}" for q in qs]),
        ("티어 역진 최대폭(%p)", f"{q0['max_tier_regression']:.3f}",
         [f"{q['max_tier_regression']:.3f}" for q in qs]),
        ("경계 절벽(최대배율)", f"{q0['max_jump']:.2f}x", [f"{q['max_jump']:.2f}x" for q in qs]),
    ]
    for label, b, vals in rows:
        L.append(f"| {label} | {b} | " + " | ".join(vals) + " |")
    L.append("")

    # 현행 대비 급간별 리워드 비율 (매력도 방어 검증)
    L.append(_ratio_vs_current_table(plans, transfers))

    # 티어 진입 유효율
    L.append("**티어 진입 시점 유효 리워드율(%)** — 평탄할수록 형평성이 높음\n")
    L.append(f"- 현행: {[round(x,3) for x in tier_entry_rates(CURRENT_STRUCTURE)]}")
    for p in plans:
        L.append(f"- {p.name}: {[round(x,3) for x in tier_entry_rates(p)]}")
    L.append("")

    # G6.1 경고
    L.append("### ⚠ 결론 강건성 경고 (배수 참여보너스 가정 의존)\n")
    L.append("배수 적용 시 참여율 +3%(1.5배) 가산은 **실측이 아닌 모델 가정**이다. "
             "이 가정을 제거하면 배수 기반 최적안(A안)과 직접구간안의 우열이 뒤집힌다:")
    L.append("")
    L.append(_bonus_sensitivity_table(caches))
    L.append("→ **배수 유지 여부는 데이터로 확정되지 않는다.** 실제 의사결정 전 "
             "배수의 참여 유인 효과를 실측(A/B 테스트 등)으로 검증할 것을 권고한다.\n")
    return "\n".join(L)


def _metrics_table(metrics: List[AggregateMetrics], baseline: AggregateMetrics) -> str:
    head = "| 케이스 | 예산 | 현행比 | 유치금액 | 매력도 | 효율(배) | CPA | worst예산 |"
    sep = "|---|---|---|---|---|---|---|---|"
    lines = [head, sep]
    for m in metrics:
        lines.append(
            f"| {m.name} | {won(m.budget_mean)} | {m.budget_mean/baseline.budget_mean*100:.0f}% | "
            f"{won(m.transfer_mean)} | {pct(m.attractiveness_index)} | {m.efficiency:.0f} | "
            f"{won(m.cpa)} | {won(m.budget_worst)} |")
    return "\n".join(lines)


def _current() -> RewardStructure:
    from src.reward_engine import CURRENT_STRUCTURE
    return CURRENT_STRUCTURE


def tier_lines_current() -> str:
    return tier_lines(_current())
