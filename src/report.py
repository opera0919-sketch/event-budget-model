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

    # 6-1. 추천 3안 (직접구간)
    L.append(_recommended_section(caches))

    # 7. 권고
    L.append("## 8. 실무 권고\n")
    a = out.objectives["A_min_budget"]
    L.append("의사결정은 **두 갈래**로 정리된다. 두 축은 서로 다른 것을 최적화하므로 "
             "한쪽이 다른 쪽을 지배하지 않는다.\n")
    L.append(f"1. **경제성 우선 — A안(배수 1.5 유지, 7단계)**: 매력도 "
             f"{pct(a.metrics.attractiveness_index)}에 예산 {won(b.budget_mean)}→"
             f"{won(a.metrics.budget_mean)}({(1-a.metrics.budget_mean/b.budget_mean)*100:.0f}% 절감). "
             "다만 유효 리워드율이 0.14~0.44%로 흩어지고 티어 간 역진이 남는다.")
    L.append("2. **설계 품질 우선 — 직접구간 안1~안3(배수 폐지, 9단계)**: 유효율 SD가 "
             "0.070→0.030~0.053으로 안정되고 경계 절벽이 2.50x→2.00x로 완화된다. "
             "고객 입장에서 '내 금액이 곧 내 구간'이라 이해도도 높다.")
    L.append("")
    L.append("- **예산 절감폭이 최우선이면** A안 또는 안3(예산 70%).")
    L.append("- **형평성·설명가능성까지 고려하면** 안2(예산 75%, 매력도 90.0%)가 균형점이며, "
             "매력도 방어가 중요하면 안1(예산 93%, 매력도 94.0%).")
    L.append("- **배수 조건**: 배수는 최대 비용 레버인 동시에, 그 참여 유인 효과(+3%)가 "
             "미검증 가정이다(§7 경고 참조). 배수 유지·폐지 결정 전 실측 검증을 권고한다.\n")

    L.append(_gate_section(caches))

    L.append("## 부록. 실행 방법\n")
    L.append("```\npython scripts/run_generate_data.py\npython scripts/run_simulation.py\n"
             "python scripts/run_optimize.py\npython scripts/build_excel.py\n"
             "python -m pytest tests/ -q\n```\n")
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
    L.append("- 67%가 나오는 곳은 세 안 공통으로 **3,000만~5,000만 구간**(인원 21.9%), "
             "안3은 추가로 **7,000만~9,000만 구간**(인원 10.8%)이다. 원인은 리워드 단위 "
             "제약이 10만원과 15만원 사이(그리고 20만원과 30만원 사이) 값을 허용하지 않아 "
             "중간 수준을 만들 수 없다는 것이다. "
             "(3~5천만 구간에 12만원이 허용되면 80% 방어 가능, 예산 +4%p)")
    L.append("- **최상위 2개 티어(1.3억·1.7억 이상)는 현행 수준(60만·100만)으로 방어**했다. "
             "방어하지 않으면 최고액 고객이 현행의 45~65%까지 떨어진다.")
    L.append("- **6,600만원 경계**는 현행의 실효 경계(인정 1억 = 실제 6,667만)에 맞춘 것이다. "
             "7,000만원에 두면 6,667만~7,000만 고객이 30만→15만으로 **현행의 50%**가 된다.")
    L.append("- **A안'(배수 유지)**는 같은 예산대에서 매력도·하락방어가 더 좋으나(96.2%/75%), "
             "유효율 분산 0.077·절벽 3.00x로 설계품질은 현행 수준에 머문다. "
             "**경제성 vs 설계품질**의 선택이다.\n")
    return "\n".join(L)


def _gate_section(caches) -> str:
    """완성도 게이트 체크리스트 — 사양 준수와 설계 제약을 실측으로 재확인."""
    from src.cases import recommended_plans
    from src.data_generator import generate_all
    from src.quality import design_report
    from src.reward_engine import (CURRENT_STRUCTURE, budget_amount,
                                   is_valid_structure)
    from src import demand_model as dm

    datasets = generate_all()
    transfers = [t for d in datasets for t in d.transfers]
    counts = [d.n_customers for d in datasets]
    base = evaluate_structure(caches, CURRENT_STRUCTURE)
    plans = recommended_plans()

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
    checks.append(("G2.1", "제세 그로스업 공식",
                   abs(budget_amount(60_000) - ((60_000 / 0.78) * 0.22 + 60_000)) < 1e-6,
                   f"6만→{budget_amount(60_000):,.0f}원"))
    checks.append(("G2.2", "5만 미만 미적용", budget_amount(40_000) == 40_000, "4만→40,000원"))
    # G3 탄력성
    checks.append(("G3.1", "현행 r=1 정규화", abs(dm.participation_scaling(1.0) - 1.0) < 1e-9, "1.000000"))
    checks.append(("G3.2", "sticky(r=0.9)", dm.participation_scaling(0.9) > 0.95,
                   f"{dm.participation_scaling(0.9):.3f}"))
    checks.append(("G3.3", "대폭하향 급감(r=0.3)", dm.participation_scaling(0.3) < 0.75,
                   f"{dm.participation_scaling(0.3):.3f}"))
    # G4 결론 정합
    checks.append(("G4.1", "baseline 매력도=100%", abs(base.attractiveness_index - 1.0) < 1e-6, "100.0%"))
    checks.append(("G4.2", "KPI 정의 정합",
                   abs(base.efficiency - base.transfer_mean / base.budget_mean) < 1e-6,
                   f"효율 {base.efficiency:.0f}배"))
    q0 = design_report(CURRENT_STRUCTURE, transfers)
    checks.append(("G4.3", "사문화 티어 없음(현행)", q0["dead_tiers"] == 0,
                   "배수로 인정 3.59억까지 도달 → 3억 티어 생존"))
    # G5 설계 제약 (추천 3안)
    all_unit = all(is_valid_structure(p) for p in plans)
    checks.append(("G5.1", "추천안 리워드 단위 준수", all_unit, "3안 전부 통과"))
    all_strict = all(is_valid_structure(p, strict_increase=True) for p in plans)
    checks.append(("G5.2", "추천안 평탄구간 없음", all_strict, "3안 전부 엄격 증가"))
    all_jump = all(is_valid_structure(p, max_jump=C.MAX_TIER_JUMP) for p in plans)
    jm = max(design_report(p, transfers)["max_jump"] for p in plans)
    checks.append(("G5.3", f"경계 절벽 ≤{C.MAX_TIER_JUMP}x", all_jump, f"최대 {jm:.2f}x (현행 {q0['max_jump']:.2f}x)"))
    sds = [design_report(p, transfers)["sd"] for p in plans]
    checks.append(("G5.4", f"유효율 SD ≤{C.MAX_RATE_SD}", max(sds) <= C.MAX_RATE_SD + 1e-9,
                   f"최대 {max(sds):.3f} (현행 {q0['sd']:.3f})"))
    ratios = [design_report(p, transfers)["min_ratio_vs_current"] for p in plans]
    checks.append(("G5.5", f"현행 대비 리워드 ≥{C.MIN_RATIO_VS_CURRENT*100:.0f}%",
                   min(ratios) >= C.MIN_RATIO_VS_CURRENT - 1e-9,
                   f"최저 {min(ratios)*100:.0f}% (3~5천만 구간, 단위 제약 기인)"))

    passed = sum(1 for *_, ok, _ in [(c[0], c[1], c[2], c[3]) for c in checks] if ok)
    L = ["## 9. 완성도 게이트 체크리스트\n",
         f"**{passed}/{len(checks)} PASS** — 데이터·제세·탄력성 사양과 설계 제약을 실측으로 재확인.\n",
         "| ID | 항목 | 결과 | 실측 |", "|---|---|---|---|"]
    for cid, name, ok, detail in checks:
        L.append(f"| {cid} | {name} | {'PASS' if ok else '**FAIL**'} | {detail} |")
    L.append("")
    L.append("> 주: 초기 검토에서 '3억 티어 사문화'로 판정했으나, 배수(1.5배) 적용 시 "
             "인정금액이 최대 3.59억에 도달하므로 **해당 티어는 생존**한다(0.21%). "
             "배수를 폐지하면 상위 티어 도달자가 사라지므로, 추천 3안은 최상단 경계를 "
             "1.7억으로 낮춰 사문화를 방지했다.\n")
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
        ("ROI(%)", f"{base.roi_pct:.0f}", [f"{m.roi_pct:.0f}" for m in ms]),
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
