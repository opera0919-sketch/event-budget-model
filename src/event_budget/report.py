"""한국어 마크다운 리포트 생성."""
from __future__ import annotations

import os
from datetime import date

from .scenario import three_scenario, tornado
from .schema import REPO_ROOT


def fmt_won(x: float) -> str:
    """원 → 읽기 쉬운 억/만 단위."""
    if abs(x) >= 1e8:
        return f"{x/1e8:,.2f}억원"
    if abs(x) >= 1e4:
        return f"{x/1e4:,.0f}만원"
    return f"{x:,.0f}원"


def fmt_n(x: float) -> str:
    return f"{x:,.0f}"


def build_report(spec, forecasts: dict, benchmarks: dict) -> str:
    L = []
    L.append(f"# {spec.title}")
    L.append("")
    L.append(f"- 이벤트 ID: `{spec.event_id}`")
    L.append(f"- 생성일: {date.today().isoformat()}")
    L.append(f"- 예측 회차: {', '.join(spec.forecast_rounds)}")
    L.append(f"- 방법: 2단계 시즌 take-rate 모델(기준 고객수 투영 → 시즌 take-rate) "
             f"+ 3대 레버 예산 시나리오")
    L.append("")

    # 1. 신청고객수 예측
    L.append("## 1. 신청 고객 수 예측")
    for p in spec.products:
        fc = forecasts[p.name]
        L.append("")
        L.append(f"### {p.label}")
        L.append("")
        L.append("| 회차 | 투영 기준고객수 | take-rate(보수/기준/낙관) | 보수 | 기준 | 낙관 |")
        L.append("|---|---|---|---|---|---|")
        for rnd in fc.rounds:
            b = fc.base[rnd]
            tr = fc.take_rate[rnd]
            a = fc.applicants[rnd]
            L.append(f"| {rnd} | {fmt_n(b)} | "
                     f"{tr[0]*100:.2f}% / {tr[1]*100:.2f}% / {tr[2]*100:.2f}% | "
                     f"{fmt_n(a[0])} | {fmt_n(a[1])} | {fmt_n(a[2])} |")
    L.append("")

    # 2. 예산 시나리오
    L.append("## 2. 예산 시나리오 (3대 레버)")
    L.append("")
    L.append("레버: `goal_achievement`(목표 달성률) · `reward_payout_rate`(리워드 지급률) · "
             "`avg_reward`(평균 리워드 금액).")
    levers = spec.levers or benchmarks.get("levers", {})
    for p in spec.products:
        fc = forecasts[p.name]
        L.append("")
        L.append(f"### {p.label}  (avg_reward={fmt_won(p.avg_reward)}"
                 + (f", tax={p.tax_rate:.0%}" if p.tax_rate else "")
                 + (f", 고정비={fmt_won(p.fixed_costs)}" if p.fixed_costs else "")
                 + ")")
        L.append("")
        L.append("| 회차 | 시나리오 | 신청자 | 목표달성률 | 지급률 | 지급대상자 | 총예산 |")
        L.append("|---|---|---|---|---|---|---|")
        for rnd in fc.rounds:
            band = fc.applicants[rnd]
            sc = three_scenario(band, levers, p.avg_reward, p.fixed_costs, p.tax_rate)
            for nm in ["보수", "기준", "낙관"]:
                s = sc[nm]
                L.append(f"| {rnd} | {nm} | {fmt_n(s['applicants'])} | "
                         f"{s['goal_achievement']:.2f} | {s['reward_payout_rate']:.2f} | "
                         f"{fmt_n(s['recipients'])} | {fmt_won(s['total'])} |")
    L.append("")

    # 3. 민감도(기준 시나리오, 각 변수 실제 시나리오 범위로)
    L.append("## 3. 민감도 (기준 시나리오 대비, 각 변수 보수↔낙관 범위)")
    L.append("")
    L.append("각 변수를 자신의 시나리오 범위로 흔들 때 총예산 변동폭. 변동폭 큰 변수가 예산 리스크의 핵심.")
    goals = levers.get("goal_achievement", [0.8, 1.0, 1.2])
    payouts = levers.get("reward_payout_rate", [0.6, 0.7, 0.8])
    label_ko = {"applicants": "신청자수", "goal_achievement": "목표달성률",
                "reward_payout_rate": "리워드지급률", "avg_reward": "평균리워드"}
    for p in spec.products:
        fc = forecasts[p.name]
        L.append("")
        L.append(f"### {p.label}")
        for rnd in fc.rounds:
            a_lo, a_mid, a_hi = fc.applicants[rnd]
            base_levers = {"goal_achievement": goals[1],
                           "reward_payout_rate": payouts[1],
                           "avg_reward": p.avg_reward}
            ranges = {
                "applicants": (a_lo, a_hi),                 # 신청자 보수↔낙관 밴드
                "goal_achievement": (goals[0], goals[-1]),  # 목표달성률 보수↔낙관
                "reward_payout_rate": (payouts[0], payouts[-1]),
                "avg_reward": (p.avg_reward * 0.8, p.avg_reward * 1.2),  # 리워드 ±20%
            }
            t = tornado(a_mid, base_levers, ranges, p.fixed_costs, p.tax_rate)
            L.append("")
            L.append(f"- **{rnd}** (기준 총예산 {fmt_won(t['base_total'])})")
            for row in t["rows"]:
                nm = label_ko.get(row["variable"], row["variable"])
                L.append(f"  - {nm}: {fmt_won(row['low'])} ~ "
                         f"{fmt_won(row['high'])} (변동폭 {fmt_won(row['swing'])})")
    L.append("")

    L.append("---")
    L.append("> take-rate는 파일럿 회차를 제외하고 시즌평균×최근추세로 산정했으며, "
             "`avg_reward`·`reward_payout_rate`는 실제 이벤트 조건으로 대체 입력한다.")
    L.append("")
    return "\n".join(L)


def write_report(spec, markdown: str) -> str:
    out_dir = os.path.join(REPO_ROOT, "reports")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{spec.event_id}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(markdown)
    return path
