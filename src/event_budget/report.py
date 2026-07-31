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


def fmt_won2(x: float) -> str:
    """원 → 억/만 단위(소수 2자리). 리워드 단가처럼 만원 단위 반올림이 아까운 값에 쓴다."""
    if abs(x) >= 1e8:
        return f"{x/1e8:,.2f}억원"
    if abs(x) >= 1e4:
        return f"{x/1e4:,.2f}만원"
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
        funnel = p.conversion_rate is not None and p.condition_rate is not None
        recip_label = "지급대상자(당첨)" if funnel else "지급대상자"
        note = (f", 지급퍼널=전환율 {p.conversion_rate:.0%}×조건충족 {p.condition_rate:.0%}"
                f"→지급률 {p.conversion_rate*p.condition_rate:.1%}") if funnel else ""
        L.append(f"### {p.label}  (avg_reward={fmt_won(p.avg_reward)}"
                 + (f", tax={p.tax_rate:.0%}" if p.tax_rate else "")
                 + (f", 고정비={fmt_won(p.fixed_costs)}" if p.fixed_costs else "")
                 + note + ")")
        L.append("")
        L.append(f"| 회차 | 시나리오 | 신청자 | 목표달성률 | 지급률 | {recip_label} | 총예산 |")
        L.append("|---|---|---|---|---|---|---|")
        for rnd in fc.rounds:
            band = fc.applicants[rnd]
            sc = three_scenario(band, levers, p.avg_reward, p.fixed_costs, p.tax_rate,
                                p.conversion_rate, p.condition_rate)
            for nm in ["보수", "기준", "낙관"]:
                s = sc[nm]
                L.append(f"| {rnd} | {nm} | {fmt_n(s['applicants'])} | "
                         f"{s['goal_achievement']:.2f} | {s['reward_payout_rate']:.3f} | "
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
        funnel = p.conversion_rate is not None and p.condition_rate is not None
        payout_base = (p.conversion_rate * p.condition_rate) if funnel else payouts[1]
        payout_lo, payout_hi = ((payout_base * 0.8, payout_base * 1.2) if funnel
                                else (payouts[0], payouts[-1]))
        for rnd in fc.rounds:
            a_lo, a_mid, a_hi = fc.applicants[rnd]
            base_levers = {"goal_achievement": goals[1],
                           "reward_payout_rate": payout_base,
                           "avg_reward": p.avg_reward}
            ranges = {
                "applicants": (a_lo, a_hi),                 # 신청자 보수↔낙관 밴드
                "goal_achievement": (goals[0], goals[-1]),  # 목표달성률 보수↔낙관
                "reward_payout_rate": (payout_lo, payout_hi),
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


def build_backtest_report(spec, per_product: dict, min_train: int, target: float,
                          min_cv: float) -> str:
    """per_product: {label: (base_mode, results, summary, suggested_cv)}."""
    L = [f"# {spec.title} — 백테스트(사후예측) 검증", "",
         f"- 생성일: {date.today().isoformat()}",
         f"- 방식: 각 실적 회차를 그 이전 데이터만으로 2단계 예측 → 실제와 비교(누수 방지)",
         f"- min_train={min_train}, 목표 밴드 커버리지={target:.0%}, 현재 min_cv={min_cv:.2f}", ""]
    for label, (base_mode, res, s, cv) in per_product.items():
        L.append(f"## {label}  (base_mode={base_mode})")
        L.append("")
        if not res:
            L.append("평가 가능한 회차 없음(학습표본 부족).")
            L.append("")
            continue
        L.append("| 회차 | 실제 | 예측(기준) | 오차% | 밴드내 |")
        L.append("|---|---|---|---|---|")
        for r in res:
            L.append(f"| {r['round']} | {fmt_n(r['actual'])} | {fmt_n(r['pred_base'])} | "
                     f"{r['ape']*100:.1f}% | {'O' if r['in_band'] else 'X'} |")
        L.append("")
        L.append(f"- **MAPE {s['mape']*100:.1f}%** · 편향 {s['bias']*100:+.1f}% · "
                 f"밴드 커버리지 {s['coverage']*100:.0f}% (n={s['n']})")
        L.append(f"- 목표 커버리지 위한 경험적 CV ≈ **{cv:.2f}** "
                 f"(현재 min_cv {min_cv:.2f} 대비)")
        L.append("")
    L.append("---")
    L.append("> 편향이 양(+)이면 과소예측(실제가 더 큼). 커버리지가 목표보다 낮으면 "
             "min_cv를 경험적 CV 수준으로 넓혀 밴드 신뢰도를 맞춘다.")
    L.append("")
    return "\n".join(L)


def write_backtest_report(spec, markdown: str) -> str:
    out_dir = os.path.join(REPO_ROOT, "reports")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{spec.event_id}_backtest.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(markdown)
    return path


def build_stress_report(spec, model, payout_rate: float, per_round: dict,
                        mults: list[float], shifts: list[float],
                        portfolio: dict | None = None, events: dict | None = None,
                        legacy=None, spec_payout: float | None = None) -> str:
    """리워드 현행 유지 시 신청자수·수관금액구간 2축 스트레스 테스트 리포트.

    per_round: {회차: {"base": 기준신청자, "cells": [...], "mc": {...}, "events": [...]}}
    """
    from .stress import (equivalent_shift, event_scenarios, mult_label,
                         shift_label, shift_tick)

    base_per = payout_rate * model.avg_budget_cost(0.0)

    L = [f"# {spec.title}", "",
         f"- 이벤트 ID: `{spec.event_id}`",
         f"- 생성일: {date.today().isoformat()}",
         f"- 리워드: **현재안 고정**(구간별 금액 불변). 흔드는 축은 신청 고객 수와 "
         f"타사수관금액 구간 비율 2개.",
         f"- 금액 구간 분포: **실측 {model.n_events}회차 고객 수 가중** "
         f"(`{model.source}`) — 가정 분포가 아니라 관측 데이터",
         f"- 기준 신청자는 take-rate 모델 예측(기준선)", ""]

    # 1. 리워드 구조
    L.append("## 1. 리워드 구조 (현재안)")
    L.append("")
    L.append("- 실적 인정: 타사수관금액 **1천만원 이상이면 ×1.5배**를 실적으로 인정")
    L.append("- 제세금: 리워드 **5만원 이상**이면 예산반영액 = 리워드 ÷ 0.78")
    L.append("")
    L.append("| 인정실적 구간 | 리워드 | 예산반영액 | 실제 수관금액 구간 | 대상자 중 비율 |")
    L.append("|---|---|---|---|---|")
    base_shares = model.shares(0.0)
    for i, lab in enumerate(model.labels):
        lo = model.edges[i]
        hi = model.edges[i + 1]
        rng = (f"{fmt_won(lo)} 이상" if hi == float("inf")
               else f"{fmt_won(lo)} ~ {fmt_won(hi)}")
        L.append(f"| {lab} | {fmt_won2(model.rewards[i])} | {fmt_won2(model.gross[i])} | "
                 f"{rng} | {base_shares[i]*100:.1f}% |")
    L.append("")
    L.append(f"- 평균 예산반영 단가 **{fmt_won2(model.avg_budget_cost())}** "
             f"(제세 제외 평균 리워드 {fmt_won2(model.avg_reward())})")
    L.append(f"- 지급률(수관금액 5백만원 이상) **{payout_rate:.2%}** · "
             f"대상자 평균 수관금액 {fmt_won(model.mean_deposit())}")
    L.append(f"- **신청 1인당 예산 {base_per:,.0f}원** = 지급률 × 평균 단가")
    L.append("")

    # 1-1. 실측 분포 치환 효과
    if legacy is not None and spec_payout:
        old_per = spec_payout * legacy.avg_budget_cost(0.0)
        L.append("### 실측 분포 치환 효과")
        L.append("")
        L.append("금액 구간 분포를 가정(로그정규)에서 실측 고객 수로 바꾼 결과. 지급률과 "
                 "단가가 크게 달라지지만 **곱인 신청 1인당 예산은 비슷하게 수렴**한다 — "
                 "두 방식이 같은 총예산을 다르게 분해하고 있었다는 뜻이다.")
        L.append("")
        L.append("| 구분 | 지급률 | 평균 단가 | 신청 1인당 예산 |")
        L.append("|---|---|---|---|")
        L.append(f"| 이전(로그정규 가정) | {spec_payout:.2%} | "
                 f"{fmt_won2(legacy.avg_budget_cost(0.0))} | {old_per:,.0f}원 |")
        L.append(f"| **현재(실측 분포)** | **{payout_rate:.2%}** | "
                 f"**{fmt_won2(model.avg_budget_cost(0.0))}** | **{base_per:,.0f}원** |")
        L.append(f"| 차이 | {payout_rate/spec_payout-1:+.1%} | "
                 f"{model.avg_budget_cost(0.0)/legacy.avg_budget_cost(0.0)-1:+.1%} | "
                 f"{base_per/old_per-1:+.1%} |")
        L.append("")
        L.append(f"로그정규 가정은 저액 구간을 크게 과대평가했다"
                 f"(최저 구간 비중 {legacy.shares(0.0)[0]*100:.1f}% vs "
                 f"실측 {model.shares(0.0)[0]*100:.1f}%). "
                 f"실측 분포는 대상자가 적고 1인당 단가는 높은 구조다.")
        L.append("")

    # 2. 회차별 실측 분포
    if events:
        L.append("## 2. 실측 분포 — 회차별 관측치")
        L.append("")
        L.append("가정 없이 관측된 고객 수만으로 계산한 값이다. 이 변동 폭이 축2 시나리오의 "
                 "눈금이 된다.")
        L.append("")
        L.append("| 회차 | 지급률 | 평균 단가 | 신청 1인당 | 평균 대비 | 등가 시프트 |")
        L.append("|---|---|---|---|---|---|")
        for row in event_scenarios(1.0, events):
            L.append(f"| {row['event']} | {row['payout_rate']:.2%} | "
                     f"{fmt_won2(row['avg_cost'])} | {row['per_applicant']:,.0f}원 | "
                     f"×{row['per_applicant']/base_per:.2f} | "
                     f"{shift_tick(equivalent_shift(model, row['per_applicant']))} |")
        L.append("")
        L.append("> **연말효과 가정과 실측이 어긋난다.** 데이터상 유일한 12월 단독 회차인 "
                 "1536(202512)이 10회차 중 **최저**(신청 1인당 평균 대비 ×0.58)이고, "
                 "최고는 9월 회차 1470(202509, ×1.59)이다. "
                 "'연말에 대량입금 고객이 몰린다'는 가정은 이 데이터로는 지지되지 않으므로, "
                 "축2의 상향 시나리오는 계절 효과가 아니라 **일반적인 상방 리스크**로 읽어야 한다.")
        L.append("")

    # 3. 믹스 시프트
    L.append("## 3. 축2 — 수관금액 구간 비율 변동")
    L.append("")
    L.append("실측 분포를 금액축에서 통째로 이동시킨다(중앙값 상향률). 구간 비율은 이동한 "
             "분포를 리워드 구간 경계로 다시 적분해 산출하므로 합계는 항상 100%다. "
             "**지급률도 함께 움직인다** — 분포가 오르면 5백만원 문턱을 넘는 고객이 늘기 "
             "때문이며, 이것이 대량입금 유입의 실제 작동 방식이다.")
    L.append("")
    L.append(f"눈금은 실측 회차 변동 방향 그 자체다: **θ=0 은 성숙 10회차 평균, "
             f"θ=+1 은 관측 최고 회차({model.tilt_hi_event}), "
             f"θ=-1 은 관측 최저 회차({model.tilt_lo_event})**. "
             f"|θ|>1 은 관측 범위를 벗어난 외삽 구간이다.")
    L.append("")
    header = ("| 기울기 | 구분 | 지급률 | " + " | ".join(model.labels) +
              " | 평균 단가 | 신청 1인당 | 기준대비 |")
    L.append(header)
    L.append("|---" * (len(model.labels) + 6) + "|")
    for s in shifts:
        sh = model.shares(s)
        cost = model.avg_budget_cost(s)
        pr = model.payout_rate(s)
        L.append(f"| {shift_tick(s)} | {shift_label(s)} | {pr:.2%} | "
                 + " | ".join(f"{x*100:.1f}%" for x in sh)
                 + f" | {fmt_won2(cost)} | {pr*cost:,.0f}원 | ×{pr*cost/base_per:.2f} |")
    L.append("")

    # 4. 회차별 매트릭스
    L.append("## 4. 스트레스 매트릭스 — 회차별 총예산")
    L.append("")
    for rnd, d in per_round.items():
        L.append(f"### {rnd}  (기준 신청자 {fmt_n(d['base'])}명)")
        L.append("")
        L.append("| 신청 배수 | " + " | ".join(f"{shift_tick(s)}<br>{shift_label(s)}" for s in shifts) + " |")
        L.append("|---" * (len(shifts) + 1) + "|")
        by = {(c["mult"], c["shift"]): c for c in d["cells"]}
        for m in mults:
            row = [f"**×{m:.1f}** {mult_label(m)}"]
            for s in shifts:
                row.append(fmt_won(by[(m, s)]["total"]))
            L.append("| " + " | ".join(row) + " |")
        L.append("")
        w = max(d["cells"], key=lambda c: c["total"])
        b = by[(1.0, 0.0)]
        L.append(f"- 기준셀(×1.0 / θ=0, 실측평균): **{fmt_won(b['total'])}** "
                 f"(당첨 {fmt_n(b['recipients'])}명)")
        L.append(f"- worst case(×{w['mult']:.1f} / {shift_tick(w['shift'])}): "
                 f"**{fmt_won(w['total'])}** — 기준 대비 **×{w['total']/b['total']:.2f}**")
        if d.get("events"):
            lo_e, hi_e = d["events"][0], d["events"][-1]
            L.append(f"- 실측 회차 범위만으로도(신청 배수 ×1.0): "
                     f"{fmt_won(lo_e['total'])}({lo_e['event']}) ~ "
                     f"{fmt_won(hi_e['total'])}({hi_e['event']})")
        L.append("")

    # 5. 몬테카를로
    L.append("## 5. 몬테카를로 — 불확실성 결합")
    L.append("")
    L.append(f"신청 배수(삼각 0.8/1.0/1.8) · 믹스 기울기(삼각 {shift_tick(min(shifts))}/"
             f"θ=0/{shift_tick(max(shifts))}) · 지급률 잔여 잡음을 동시에 흔들어 총예산 "
             "분포를 얻는다. 기울기 밴드는 실측 관측 범위에서 왔고 최빈값은 10회차 "
             "평균이다 — 임의 가정이 아니라 데이터가 눈금이다.")
    L.append("")
    L.append("| 회차 | 기대값 | P50 | P90(편성 권고) | P95 | P99 |")
    L.append("|---|---|---|---|---|---|")
    for rnd, d in per_round.items():
        mc = d["mc"]
        L.append(f"| {rnd} | {fmt_won(mc['mean'])} | {fmt_won(mc['p50'])} | "
                 f"{fmt_won(mc['p90'])} | {fmt_won(mc['p95'])} | {fmt_won(mc['p99'])} |")
    if portfolio:
        L.append(f"| **3회차 합** | {fmt_won(portfolio['mean'])} | {fmt_won(portfolio['p50'])} | "
                 f"**{fmt_won(portfolio['p90'])}** | {fmt_won(portfolio['p95'])} | "
                 f"{fmt_won(portfolio['p99'])} |")
        L.append("")
        L.append(f"> 위 3회차 합 행은 회차별 충격이 **독립**이라는 가정(분산효과 최대)이다. "
                 f"반대로 모든 회차가 함께 움직이는 **공통충격** 가정에서는 합산 P90이 "
                 f"{fmt_won(portfolio['comonotonic_p90'])}, P95가 "
                 f"{fmt_won(portfolio['comonotonic_p95'])}로 올라간다. "
                 f"실제 편성치는 **{fmt_won(portfolio['p90'])} ~ "
                 f"{fmt_won(portfolio['comonotonic_p90'])}** 사이다.")
    L.append("")

    # 6. 가정
    L.append("## 6. 가정과 한계")
    L.append("")
    L.append("1. 금액 구간 분포는 **실측 10회차의 고객 수 가중 통합**이다(총 신청 100,058명, "
             "리워드 대상 19,920명). 회차 규모가 4,063~19,831명으로 5배 차이나 "
             "가중이 맞다. 참고로 회차 단순평균을 쓰면 지급률이 21.22%로 올라간다.")
    L.append("2. 구간 안쪽 분포는 **로그축 균등**으로 보간했다. 1.5배 인정 규칙 때문에 "
             "리워드 구간 경계가 실측 구간 안쪽(예: 실제 2,000만원)에 떨어져 쪼개 적분해야 "
             "하기 때문이다. 구간 경계에서는 실측치를 정확히 재현한다.")
    L.append("3. 최저 구간 `[0, 5백만)`의 하한은 10만원으로 가정했다(로그축 보간용). "
             "이 값은 시프트가 클 때 지급률 상승폭에만 영향을 준다.")
    L.append("4. 2026_04 회차는 시즌4 take-rate에 연말효과가 이미 일부 반영돼 있다. "
             "따라서 배수 1.8은 모델 기준선 **대비 추가** 변동으로 해석해야 하며, "
             "연말효과를 이중으로 계상하지 않도록 주의한다.")
    L.append("5. 분포의 모집단이 '신청 고객'임은 교차 검증됐다 — 회차별 총 고객 수가 "
             "`data/reference_deposit_events.csv`의 신청자수와 10건 중 9건 정확히 일치한다"
             "(1607만 진행 경과로 16,356 → 17,399 갱신). 따라서 지급률 19.91%는 "
             "리워드 지급고객 ÷ 신청고객이다.")
    L.append("")
    return "\n".join(L)


def write_stress_report(spec, markdown: str) -> str:
    out_dir = os.path.join(REPO_ROOT, "reports")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{spec.event_id}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(markdown)
    return path
