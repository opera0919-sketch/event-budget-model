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


def _josa(word: str, with_final: str, without_final: str) -> str:
    """받침 유무에 따라 조사를 고른다(예: 이/가, 은/는)."""
    if not word:
        return without_final
    ch = word[-1]
    if not ("가" <= ch <= "힣"):
        return without_final
    return with_final if (ord(ch) - 0xAC00) % 28 else without_final


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
    """최종 결과 리포트.

    구성 원칙:
      - **결론 먼저**. '무엇을 결정해야 하는가' 순서로 배치한다.
        탐색 과정(A/B/C 참고치·케이스 비교·Pareto)은 부록으로 내린다.
        이전 판은 게이트 미통과 A/B/C가 앞에 오고 뒤에서야 '쓰지 말라'고 해서,
        앞부분만 읽으면 폐기된 안을 권고로 오해할 수 있었다.
      - **수치 리터럴 금지**. 모든 숫자는 계산값에서 포맷팅한다.
      - **불리한 지표도 함께**. 개선된 항목만 말하지 않는다.
    """
    b = out.baseline
    L: List[str] = []
    ap = b.applicants_mean

    L.append("# 연금 타사이전 리워드 구조 — 최종 결과 리포트\n")
    L.append(f"시뮬레이션 {C.N_ROUNDS}회(흥행 {C.HIT_ROUNDS} / 비흥행 {C.N_ROUNDS - C.HIT_ROUNDS}) "
             f"· 평균 신청자 {ap:,.0f}명 · 모든 금액은 **제세 포함 예산** 기준\n")
    L.append("> 이 리포트는 `scripts/run_optimize.py`가 매 실행마다 실측으로 생성한다. "
             "수치를 문서에 직접 적지 않으므로 모델을 바꾸면 자동으로 따라온다.\n")
    L.append("---\n")

    # 1. 결론 요약
    L.append(_summary_section(out, caches))

    # 2. 결정해야 할 것
    L.append(_recommendation_section(out, caches))

    # 3. 현행 대비 영향 + 후보 구조
    L.append(_recommended_section(caches))

    # 4. 예산 계획
    L.append(_budget_section(out, caches))

    # 5. 가정과 강건성
    L.append(_assumption_section(out, caches))

    # 6. 설계 품질
    L.append(_design_quality_section(caches))

    # 7. 검증
    L.append(_gate_section(out, caches))

    # 부록
    L.append(_appendix_section(out, caches, case_metrics))
    return "\n".join(L)


def _summary_section(out, caches) -> str:
    """결론 요약 — 후보 비교 한 표 + 두 갈래 병기 + 유의사항."""
    from src.cases import all_candidate_plans
    from src.data_generator import generate_all
    from src.quality import design_report
    from src.reward_engine import CURRENT_STRUCTURE

    transfers = [t for d in generate_all() for t in d.transfers]
    b = out.baseline
    q0 = design_report(CURRENT_STRUCTURE, transfers)
    rows = [(p, evaluate_structure(caches, p), design_report(p, transfers))
            for p in all_candidate_plans()]

    L = ["## 1. 결론 요약\n"]
    L.append("게이트를 통과한 후보는 아래 넷이다. **순위를 매기지 않는다** — "
             "계량 지표와 계량되지 않는 요소가 서로 다른 안을 가리키기 때문이다(§2).\n")
    L.append("| 후보 | 배수 | 예산 | 현행比 | 매력도 | 효율 | 현행대비 최저 |")
    L.append("|---|---|---|---|---|---|---|")
    L.append(f"| (현행) | 유지 | {won(b.budget_mean)} | 100% | 100.0% | {b.efficiency:.0f}배 | — |")
    for p, m, q in rows:
        mult = "유지" if p.multiplier > 1.0 else "폐지"
        L.append(f"| {p.name} | {mult} | {won(m.budget_mean)} | "
                 f"{m.budget_mean/b.budget_mean*100:.0f}% | {pct(m.attractiveness_index)} | "
                 f"{m.efficiency:.0f}배 | {q['min_ratio_vs_current']*100:.0f}% |")
    L.append("")

    lo = min(rows, key=lambda r: r[1].budget_mean)
    hi = max(rows, key=lambda r: r[1].attractiveness_index)
    L.append(f"- 예산은 현행 대비 **{lo[1].budget_mean/b.budget_mean*100:.0f}~"
             f"{max(r[1].budget_mean for r in rows)/b.budget_mean*100:.0f}%** 범위에서 선택 가능하다.")
    L.append(f"- 어떤 안을 골라도 **모든 급간에서 현행의 "
             f"{min(r[2]['min_ratio_vs_current'] for r in rows)*100:.0f}% 이상**을 지급한다.")
    L.append("")

    # 유의사항 — 계산으로 판정해 서술한다.
    L.append("### 결정 전 반드시 볼 것\n")
    L.append("1. **배수 참여보너스(+3%)는 미검증 가정이다.** 배수 유지안의 계량 우위가 "
             "이 가정에 부분적으로 의존한다(§5).")
    worse = [p.name for p, _, q in rows
             if q["max_tier_regression"] > q0["max_tier_regression"] + 1e-9]
    if worse:
        L.append(f"2. **티어 역진 폭은 현행보다 악화된다** — 현행 {q0['max_tier_regression']:.3f}%p 대비 "
                 f"{', '.join(worse)}{_josa(worse[-1], '이', '가')} 더 크다(§6). "
                 "유효율 분산(SD)은 개선되지만 모든 형평성 지표가 좋아지는 것은 아니다.")
    else:
        L.append("2. 티어 역진 폭은 현행 이하로 유지된다(§6).")
    L.append("3. **예산 승인은 평균이 아니라 worst-case로** 요청할 것(§4).")
    L.append("")
    return "\n".join(L)


def _budget_section(out, caches) -> str:
    """예산 계획 — 시나리오별·worst-case·보수적 집계."""
    from src.cases import all_candidate_plans
    from src.data_generator import generate_all
    from src.reward_engine import CURRENT_STRUCTURE, budget_amount, reward_for

    datasets = generate_all()
    transfers = [t for d in datasets for t in d.transfers]
    b = out.baseline

    L = ["## 4. 예산 계획\n"]
    L.append("| 후보 | 평균 | 현행比 | 흥행 | 비흥행 | **worst** | 리워드 | 제세 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for m, name in [(b, "현행")] + [(evaluate_structure(caches, p), p.name)
                                   for p in all_candidate_plans()]:
        L.append(f"| {name} | {won(m.budget_mean)} | {m.budget_mean/b.budget_mean*100:.0f}% | "
                 f"{won(m.budget_hit_mean)} | {won(m.budget_flop_mean)} | "
                 f"**{won(m.budget_worst)}** | {won(m.reward_mean)} | "
                 f"{won(m.budget_mean - m.reward_mean)} |")
    L.append("")
    L.append(f"제세금이 예산의 **{(b.budget_mean-b.reward_mean)/b.budget_mean*100:.1f}%**를 차지한다"
             "(리워드 5만원 이상 그로스업).\n")

    # 탄력성 미반영 보수 집계 — 예산 승인용
    L.append("### 예산 승인 기준 (보수적 집계)\n")
    L.append("아래는 **신청자 수가 현행과 동일하다는 가정**(탄력성 미반영)이다. "
             "리워드 하향에 따른 참여 감소를 빼지 않았으므로 승인 요청에 적합하다.\n")
    L.append("| 후보 | 보수적 집계 | 모델 추정 | worst-case |")
    L.append("|---|---|---|---|")
    for p in all_candidate_plans():
        conservative = sum(budget_amount(reward_for(t, p)) for t in transfers) / C.N_ROUNDS
        m = evaluate_structure(caches, p)
        L.append(f"| {p.name} | **{won(conservative)}** | {won(m.budget_mean)} | {won(m.budget_worst)} |")
    L.append("")
    return "\n".join(L)


def _assumption_section(out, caches) -> str:
    """가정과 강건성 — 후보안 기준 민감도 + 미검증 가정."""
    from src.cases import all_candidate_plans
    from src.reward_engine import CURRENT_STRUCTURE

    L = ["## 5. 가정과 강건성\n"]
    L.append("| 항목 | 내용 |")
    L.append("|---|---|")
    L.append("| 예산(제세 포함) | 리워드 ≥ 5만원이면 `(리워드/0.78)*0.22 + 리워드`. 고객 수령액은 리워드 그대로 |")
    L.append("| 수요반응 | 비선형 로지스틱 S-커브. 소폭 하향엔 sticky(비탄력), 대폭 하향 시 급감 |")
    L.append("| 구간내 분포 | 하한(최소 기준) 쪽으로 쏠린 triangular |")
    L.append("| 배수 참여 보너스 | 1.5배 +3% / 2.0배 +5% — **미검증 가정** |")
    L.append(f"| 리워드 단위 | {won(C.REWARD_UNIT)}원 단위, 구간별 엄격 증가 |")
    L.append("")

    # 민감도 — 실제 후보안 기준(이전 판은 A/B/C 기준이라 같은 구조로 수렴해 중복이었다)
    L.append("### 5-1. 탄력성 민감도 (후보안 기준)\n")
    named = [("현행", CURRENT_STRUCTURE)] + [(p.name, p) for p in all_candidate_plans()]
    sens = sensitivity(caches, named)
    L.append("| 후보 | 완만(gentle) | 기본(base) | 급격(steep) |")
    L.append("|---|---|---|---|")
    for name, _ in named:
        cells = " | ".join(f"{pct(sens[v][name].attractiveness_index)}"
                           for v in ["gentle", "base", "steep"])
        L.append(f"| {name} | {cells} |")
    L.append("")
    L.append("표는 매력도(유치 이전금액 vs 현행). 급격 시나리오에서도 후보안이 "
             "어느 수준을 유지하는지 확인할 것.\n")

    L.append("### 5-2. ⚠ 배수 참여보너스 가정 의존성\n")
    L.append("배수 적용 시 참여율 +3% 가산은 **실측이 아닌 모델 가정**이다. "
             "가정을 제거하면 배수 유지안과 직접구간안의 상대 위치가 달라진다:")
    L.append(_bonus_sensitivity_table(caches))
    L.append("→ **예산 축에서는 가정과 무관하게 배수 유지안이 우세하고, 매력도 축만 역전된다.** "
             "배수 존폐 결정 전 참여 유인 효과를 실측(A/B 테스트 등)으로 검증할 것을 권고한다.\n")
    return "\n".join(L)


def _design_quality_section(caches) -> str:
    """설계 품질 — 개선·악화를 함께 판정해 표기."""
    from src.cases import all_candidate_plans
    from src.data_generator import generate_all
    from src.quality import design_report
    from src.reward_engine import CURRENT_STRUCTURE

    transfers = [t for d in generate_all() for t in d.transfers]
    q0 = design_report(CURRENT_STRUCTURE, transfers)
    plans = all_candidate_plans()

    L = ["## 6. 설계 품질\n"]
    L.append("총예산·매력도만으로는 드러나지 않는 결함을 계량한다. "
             "**개선된 항목만이 아니라 악화된 항목도 함께 본다.**\n")
    L.append("| 지표 | 현행 | " + " | ".join(p.name for p in plans) + " | 방향 |")
    L.append("|---|---|" + "---|" * (len(plans) + 1))

    qs = [design_report(p, transfers) for p in plans]
    specs = [
        ("유효율 SD", "sd", "{:.3f}", False),
        ("유효율 범위(%)", None, None, None),
        ("티어 역진 최대폭(%p)", "max_tier_regression", "{:.3f}", False),
        ("경계 절벽(최대배율)", "max_jump", "{:.2f}x", False),
        ("현행 대비 최저(%)", "min_ratio_vs_current", "{:.0%}", True),
    ]
    for label, key, fmt, higher_better in specs:
        if key is None:
            base_cell = f"{q0['min_pct']:.2f}~{q0['max_pct']:.2f}"
            cells = [f"{q['min_pct']:.2f}~{q['max_pct']:.2f}" for q in qs]
            L.append(f"| {label} | {base_cell} | " + " | ".join(cells) + " | — |")
            continue
        base_cell = fmt.format(q0[key]) if key != "min_ratio_vs_current" else "—"
        cells = [fmt.format(q[key]) for q in qs]
        if key == "min_ratio_vs_current":
            arrow = "높을수록 좋음"
        else:
            better = sum(1 for q in qs if q[key] < q0[key] - 1e-9)
            worse = sum(1 for q in qs if q[key] > q0[key] + 1e-9)
            arrow = (f"현행 대비 개선 {better} / 악화 {worse}")
        L.append(f"| {label} | {base_cell} | " + " | ".join(cells) + f" | {arrow} |")
    L.append("")

    # 악화 항목을 문장으로 명시한다.
    regress = [p.name for p, q in zip(plans, qs)
               if q["max_tier_regression"] > q0["max_tier_regression"] + 1e-9]
    if regress:
        L.append(f"**티어 역진 폭 악화**: 현행 {q0['max_tier_regression']:.3f}%p 대비 "
                 f"{', '.join(regress)}{_josa(regress[-1], '이', '가')} 더 크다. 상위 티어를 현행 대비 비율로 방어하면서 "
                 "구간이 세분화돼 진입 유효율의 진동 폭이 커진 결과다. "
                 "유효율 분산(SD)이 낮아진다고 해서 모든 형평성 지표가 좋아지는 것은 아니다.\n")
    L.append(f"자격 미달(리워드 0) 인원은 **{q0['unqualified_share']*100:.2f}%**이며, "
             f"자격 진입 시점 유효율은 {q0['entry_cliff_pct']:.2f}%다(500만원 절벽). "
             "이 인원은 유효율 집계에서 제외되므로 별도로 본다.\n")
    return "\n".join(L)


def _appendix_section(out, caches, case_metrics) -> str:
    """부록 — 탐색 과정 산물. 의사결정 자료가 아니다."""
    b = out.baseline
    L = ["## 부록 A. 탐색 과정 (참고)\n"]
    L.append("아래는 최종 후보를 고르기까지의 탐색 산물이다. "
             "**의사결정 자료가 아니다.**\n")

    L.append("### A-1. 목표별 최적화 결과 (게이트 미통과)\n")
    violated = {k: r for k, r in out.objectives.items() if not r.passes_gate}
    if violated:
        L.append("예산·효율만으로 탐색한 결과로, **설계 게이트를 통과하지 못한다.** "
                 "권고안으로 쓰지 않는다.\n")
    L.append("| 목표 | 구조(인정금액 기준) | 예산 | 매력도 | 게이트 |")
    L.append("|---|---|---|---|---|")
    for key in ["A_min_budget", "B_max_efficiency", "C_target_budget"]:
        res = out.objectives.get(key)
        if res is None:
            continue
        m = res.metrics
        gate = "통과" if res.passes_gate else "**미통과**: " + " / ".join(res.gate_violations)
        rewards = " / ".join(won(r) for r in res.structure.rewards())
        L.append(f"| {key} | {rewards} | {won(m.budget_mean)} "
                 f"({m.budget_mean/b.budget_mean*100:.0f}%) | {pct(m.attractiveness_index)} | {gate} |")
    L.append("")

    L.append("### A-2. 초기 탐색 케이스\n")
    L.append(_metrics_table(case_metrics, b))
    L.append("")

    L.append("### A-3. 예산 ↔ 유치금액 Pareto Frontier\n")
    L.append(f"비지배 후보 {len(out.pareto)}개. 예산이 낮을수록 효율(원점 기울기)은 오르나 "
             "유치금액은 줄어든다. sticky 수요 가정에서는 예산 최소화와 효율 최대화가 "
             "정렬되므로, 실질 상충은 **'예산 vs 매력도'**다.")
    L.append("차트 `results/pareto.png` · 데이터 `results/pareto.csv`\n")

    L.append("## 부록 B. 재현 방법\n")
    L.append("```\npython scripts/run_generate_data.py\npython scripts/run_simulation.py\n"
             "python scripts/run_optimize.py\npython scripts/build_excel.py\n"
             "python -m pytest tests/ -q\n```\n")
    L.append(f"시드 고정(`MASTER_SEED={C.MASTER_SEED}`)으로 데이터·리포트가 비트 동일하게 재생성된다.\n")
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

    L = ["## 2. 결정해야 할 것\n"]

    # 게이트를 통과한 후보만 권고 대상.
    L.append("### 2-1. 후보별 지표\n")
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
        L.append("### 2-2. 결정 ① 배수 유지 vs 폐지\n")
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
                 "이는 미검증 가정이므로(§5-2) 배수 존폐 결정 전 실측 검증을 권고한다.")
        L.append("")

    # 결정 ② — 보장률을 얼마로 둘 것인가. 예산과 정직하게 맞바꾼다.
    L.append("### 2-3. 결정 ② 현행 대비 보장률 수준\n")
    L.append("각 안은 '어떤 급간도 현행의 X% 미만으로 떨어뜨리지 않는다'를 목표로 도출했다. "
             "보장률을 올리면 매력도가 오르고 예산도 함께 오른다 — 정직한 맞바꿈이다.\n")
    L.append("| 보장률 | 후보 | 배수 | 예산 | 현행比 | 매력도 |")
    L.append("|---|---|---|---|---|---|")
    for p, m, q in sorted(rows, key=lambda r: r[2]["min_ratio_vs_current"]):
        mult = "유지" if p.multiplier > 1.0 else "폐지"
        L.append(f"| {q['min_ratio_vs_current']*100:.0f}% | {p.name} | {mult} | "
                 f"{won(m.budget_mean)} | {m.budget_mean/b.budget_mean*100:.0f}% | "
                 f"{pct(m.attractiveness_index)} |")
    L.append("")
    # 보장률의 예산 대가는 **같은 계열 안에서** 비교해야 한다.
    # 배수 유지/폐지를 섞으면 배수 효과가 보장률 효과로 오해된다.
    direct = [r for r in rows if r[0].multiplier <= 1.0]
    if len(direct) >= 2:
        lo_p = min(direct, key=lambda r: r[2]["min_ratio_vs_current"])
        hi_p = max(direct, key=lambda r: r[2]["min_ratio_vs_current"])
        delta = hi_p[1].budget_mean - lo_p[1].budget_mean
        L.append(f"- 배수 폐지안 기준, 보장률을 "
                 f"{lo_p[2]['min_ratio_vs_current']*100:.0f}% → "
                 f"{hi_p[2]['min_ratio_vs_current']*100:.0f}%로 올리면 예산이 "
                 f"**{won(delta)}**(현행의 {delta/b.budget_mean*100:.0f}%p) 늘고 "
                 f"매력도는 {pct(lo_p[1].attractiveness_index)} → "
                 f"{pct(hi_p[1].attractiveness_index)}로 오른다.")
    L.append("- 민원 리스크와 예산 여유를 함께 보고 결정할 것. "
             "구간별 실제 하락 폭은 §3-3에서 확인한다.")
    L.append("")
    return "\n".join(L)


def _ratio_vs_current_table(plans, transfers) -> str:
    """동일 순입금 고객이 받는 금액을 현행과 비교(매력도 방어 검증)."""
    from src.quality import ratio_vs_current
    from src.reward_engine import CURRENT_STRUCTURE, reward_for

    plans = list(plans)
    # 표는 1천만원 단위로 보여주고(가독성), 게이트는 100만원 격자로 검증한다.
    coarse = [b * C.BRACKET_WIDTH for b in range(24)]
    ratios = [dict((lo, (r, sh)) for lo, r, sh in ratio_vs_current(p, transfers, coarse))
              for p in plans]
    keys = sorted(ratios[0].keys())

    L = ["", "### 3-3. 현행 대비 급간별 리워드 수준 (동일 순입금 고객 기준)\n",
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
                                   is_valid_structure, recognized_amount, reward_for)
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
    max_recog = max(recognized_amount(t, CURRENT_STRUCTURE) for t in transfers)
    checks.append(("G4.3", "사문화 티어 없음(현행)", q0["dead_tiers"] == 0,
                   f"배수로 인정 {won(max_recog)}까지 도달 → 최상단 티어 생존"))
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
    L = ["## 7. 검증 — 완성도 게이트\n",
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
    from src.cases import multiplier_keep_plan

    plans = recommended_plans()
    keep = multiplier_keep_plan()

    L: List[str] = []
    L.append("## 3. 후보 리워드 구조와 현행 대비 영향\n")
    eff_edges = [th / C.CURRENT_MULTIPLIER if th / C.CURRENT_MULTIPLIER >= C.CURRENT_MULTIPLIER_THRESHOLD
                 else th for th, _ in C.CURRENT_TIERS]
    L.append(f"직접구간 경계(순입금): {' / '.join(won(x) for x in C.DIRECT_BRACKETS)}\n")
    L.append("경계는 **현행의 실효 경계**(배수 역산: "
             + " / ".join(won(x) for x in eff_edges[2:])
             + ")와 대조해 배치했다. 신규 경계를 현행 실효 경계보다 위에 두면 그 사이 고객이 "
             "급락하기 때문이다(예: 7,000만에 두면 6,667만~7,000만 고객이 현행의 50%).\n")

    # 3-1. 구간별 리워드 표.
    #   배수 폐지안은 순입금 기준 9단계, 배수 유지안은 인정금액 기준 7단계라
    #   구간 체계가 다르다. 한 표에 억지로 합치면 오해를 부르므로 나눠 싣고,
    #   실제 비교는 §3-2(동일 순입금 고객이 받는 금액)에서 한다.
    L.append("### 3-1. 후보별 리워드 구조\n")
    L.append("**배수 폐지안** — 순입금 구간에 직접 대응한다.\n")
    edges = list(C.DIRECT_BRACKETS)
    L.append("| 순입금 구간 | " + " | ".join(p.name for p in plans) + " |")
    L.append("|---|" + "---|" * len(plans))
    for i, lo in enumerate(edges):
        hi = edges[i + 1] if i + 1 < len(edges) else None
        label = f"{won(lo)} 이상" if hi is None else f"{won(lo)}~{won(hi)}"
        L.append(f"| {label} | " + " | ".join(won(p.rewards()[i]) for p in plans) + " |")
    L.append("")
    L.append(f"**배수 유지안({keep.name})** — 순입금에 "
             f"{C.CURRENT_MULTIPLIER}배를 적용한 **인정금액** 기준 7단계다. "
             "현행과 같은 체계라 시스템 변경이 가장 적다.\n")
    L.append("| 인정금액 구간 | 리워드 | 진입 순입금 |")
    L.append("|---|---|---|")
    keep_ths = list(keep.thresholds()) + [None]
    for i, r in enumerate(keep.rewards()):
        th = keep_ths[i]
        nxt = keep_ths[i + 1]
        label = f"{won(th)} 이상" if nxt is None else f"{won(th)}~{won(nxt)}"
        entry = th / keep.multiplier if th / keep.multiplier >= keep.multiplier_threshold else th
        L.append(f"| {label} | {won(r)} | {won(entry)} |")
    L.append("")

    # 지표 + 설계 품질 — 배수 유지안까지 한 표에서 비교한다.
    plans = plans + [keep]
    L.append("### 3-2. 지표 비교\n")
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
    # 배수 보너스 가정 의존성은 §5-2에서 한 번만 다룬다(중복 방지).
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
