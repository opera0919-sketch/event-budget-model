"""스트레스 테스트 결과 → 엑셀 보고서.

기존 reports/연금저축_이벤트_시뮬레이션_보고서.xlsx 와 같은 톤으로, 리워드 현행 유지 시
신청자수·타사이전금액구간 2축 시나리오 결과를 시트별로 정리한다.

    python scripts/build_stress_xlsx.py [명세경로]
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from event_budget import demand, stress, tiers
from event_budget.calibrate import apply_calibration
from event_budget.schema import (REPO_ROOT, load_benchmarks, load_history,
                                 load_market, load_spec)

SPEC = sys.argv[1] if len(sys.argv) > 1 else "events/2026_pension_transfer_stress.yaml"
OUT = os.path.join(REPO_ROOT, "reports", "리워드유지_신청자·이전금액구간_스트레스테스트.xlsx")

TITLE_FONT = Font(bold=True, size=14)
HEAD_FONT = Font(bold=True, color="FFFFFF")
HEAD_FILL = PatternFill("solid", fgColor="2F5597")
SUB_FONT = Font(bold=True)
SUB_FILL = PatternFill("solid", fgColor="D9E2F3")
WARN_FILL = PatternFill("solid", fgColor="FCE4D6")
BASE_FILL = PatternFill("solid", fgColor="E2EFDA")
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

EOK = 1e8   # 억원


def head(ws, row, values, width=None):
    for i, v in enumerate(values, start=1):
        c = ws.cell(row=row, column=i, value=v)
        c.font = HEAD_FONT
        c.fill = HEAD_FILL
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = BORDER
    if width:
        for i, w in enumerate(width, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w


def put(ws, row, values, fmt=None, fill=None, bold=False):
    for i, v in enumerate(values, start=1):
        c = ws.cell(row=row, column=i, value=v)
        c.border = BORDER
        if fmt and isinstance(v, (int, float)):
            c.number_format = fmt
        if fill:
            c.fill = fill
        if bold:
            c.font = SUB_FONT


def title(ws, text, row=1):
    c = ws.cell(row=row, column=1, value=text)
    c.font = TITLE_FONT


def main():
    spec = load_spec(SPEC)
    history = load_history(spec.history_csv)
    raw = load_benchmarks()
    market = load_market(spec.market_csv)
    benchmarks = apply_calibration(history, spec.products, raw)

    product = next(p for p in spec.products if p.reward_tier_key)
    model = tiers.load_empirical_tier_model(product.reward_tier_key,
                                            product.reward_tiers_path)
    events = tiers.event_tier_models(product.reward_tier_key, product.reward_tiers_path)
    dist_rows = tiers.load_amount_distribution()
    legacy = tiers.load_tier_model(product.reward_tier_key, product.reward_tiers_path)
    fc = demand.predict_product(history, product, benchmarks, spec.forecast_rounds, market)

    cfg = spec.stress or {}
    mults = cfg.get("applicant_multipliers", stress.DEFAULT_MULTS)
    shifts = cfg.get("mix_shifts", stress.DEFAULT_SHIFTS)
    payout = model.payout_rate(0.0)
    spec_payout = product.conversion_rate * product.condition_rate
    payout_cv = float(cfg.get("payout_cv", 0.22))
    n_mc = int(cfg.get("montecarlo_n", 40000))
    base_per = model.per_applicant(0.0)

    per_round = {}
    for i, rnd in enumerate(fc.rounds):
        base = fc.applicants[rnd][1]
        per_round[rnd] = {
            "base": base,
            "cells": stress.stress_matrix(base, model, None, mults, shifts),
            "mc": stress.stress_montecarlo(base, model, None, payout_cv,
                                           shift_band=(min(shifts), 0.0, max(shifts)),
                                           n=n_mc, seed=42 + i),
            "events": stress.event_scenarios(base, events),
        }
    portfolio = stress.portfolio_montecarlo({k: v["mc"] for k, v in per_round.items()})

    wb = Workbook()

    # ---------------- 요약 ----------------
    ws = wb.active
    ws.title = "요약"
    title(ws, "리워드 현행 유지 시 신청자수·타사이전금액구간 스트레스 테스트 — 종합 요약")
    r = 3
    ws.cell(row=r, column=1, value="A. 회차별 예산 (단위: 억원)").font = SUB_FONT
    r += 1
    head(ws, r, ["회차", "기준 신청자", "기준셀(×1.0/θ=0)",
                 f"worst case(×{max(mults):.1f}/θ={max(shifts):+.1f})",
                 "worst 배율", "MC P90"],
         width=[16, 14, 20, 22, 12, 14])
    r += 1
    for rnd, d in per_round.items():
        by = {(c["mult"], c["shift"]): c for c in d["cells"]}
        b = by[(1.0, 0.0)]
        w = stress.worst_case(d["cells"])
        put(ws, r, [rnd, round(d["base"]), b["total"] / EOK, w["total"] / EOK,
                    w["total"] / b["total"], d["mc"]["p90"] / EOK],
            fmt="#,##0.00")
        ws.cell(row=r, column=2).number_format = "#,##0"
        ws.cell(row=r, column=5).number_format = '#,##0.00"배"'
        r += 1
    b_sum = sum(by_[(1.0, 0.0)]["total"] for by_ in
                [{(c["mult"], c["shift"]): c for c in d["cells"]}
                 for d in per_round.values()])
    w_sum = sum(stress.worst_case(d["cells"])["total"] for d in per_round.values())
    put(ws, r, ["3회차 합", "", b_sum / EOK, w_sum / EOK, w_sum / b_sum,
                portfolio["p90"] / EOK], fmt="#,##0.00", fill=SUB_FILL, bold=True)
    ws.cell(row=r, column=5).number_format = '#,##0.00"배"'

    r += 2
    ws.cell(row=r, column=1, value="B. 예산 편성 권고 (3회차 합, 억원)").font = SUB_FONT
    r += 1
    head(ws, r, ["구분", "금액(억)", "설명"], width=[16, 14, 62])
    r += 1
    for label, val, note in [
        ("기준셀 합", b_sum / EOK,
         f"신청 현 수준 유지 + 금액 구간 믹스 = 실측 {model.n_events}회차 고객 수 가중"),
        ("MC P50", portfolio["p50"] / EOK, "배수·기울기 상방 비대칭이 반영된 중앙값"),
        ("MC P90 (회차 독립)", portfolio["p90"] / EOK, "회차별 충격이 독립일 때 — 분산효과 최대, 하한"),
        ("MC P90 (공통충격)", portfolio["comonotonic_p90"] / EOK,
         "모든 회차가 함께 움직일 때 — 상한. 연말 대량입금처럼 회차를 가로지르는 충격이 크면 이쪽"),
        ("worst case 합", w_sum / EOK,
         f"결정론 최악 셀(신청 ×{max(mults):.1f} × 믹스 θ={max(shifts):+.1f} = 관측 범위 초과)"),
    ]:
        put(ws, r, [label, val, note], fmt="#,##0.00")
        r += 1
    ws.cell(row=r - 3, column=1).fill = BASE_FILL
    ws.cell(row=r - 1, column=1).fill = WARN_FILL

    r += 1
    ws.cell(row=r, column=1,
            value=f"핵심: 리워드를 현재안대로 유지해도 신청자 {max(mults):.1f}배 + 대량입금 "
                  f"믹스 상향이 겹치면 예산은 기준 대비 약 {w_sum/b_sum:.1f}배로 늘어난다. "
                  f"편성 권고는 3회차 합 {portfolio['p90']/EOK:.1f}~"
                  f"{portfolio['comonotonic_p90']/EOK:.1f}억원(P90 범위).").font = SUB_FONT

    # ---------------- 가정및방법론 ----------------
    ws = wb.create_sheet("가정및방법론")
    title(ws, "가정 및 방법론")
    r = 3
    ws.cell(row=r, column=1, value="[ 고정한 것 — 리워드 수준 ]").font = SUB_FONT
    r += 1
    for k, v in [("리워드 구조", "현재안 그대로(구간별 금액 불변)"),
                 ("실적 인정 규칙", "타사이전금액 1천만원 이상이면 ×1.5배를 실적으로 인정"),
                 ("제세금 규칙", "리워드 5만원 이상이면 예산반영액 = 리워드 ÷ 0.78"),
                 ("지급률(당첨/신청)", f"{payout:.2%} — 실측 금액 분포에서 유도"
                                      f"(수관 5백만원 이상 비중)")]:
        put(ws, r, [k, v])
        r += 1

    r += 1
    ws.cell(row=r, column=1, value="[ 흔든 것 — 2개 축 ]").font = SUB_FONT
    r += 1
    for k, v in [("축1 신청 고객 수", f"배수 {', '.join(f'×{m}' for m in mults)} "
                                    f"(0.8=감소, 1.0=유지, 1.8=연말효과 증가)"),
                 ("축2 수관금액 구간 비율", f"기울기 θ = "
                                        f"{', '.join(f'{s:+.1f}' for s in shifts)}. "
                                        f"θ=0 실측 {model.n_events}회차 가중, "
                                        f"θ=+1 관측 최고({model.tilt_hi_event}), "
                                        f"θ=-1 관측 최저({model.tilt_lo_event}), "
                                        f"|θ|>1 은 관측 범위 외삽")]:
        put(ws, r, [k, v])
        r += 1

    r += 1
    ws.cell(row=r, column=1, value="[ 수관금액 구간 분포 — 실측 데이터 ]").font = SUB_FONT
    r += 1
    put(ws, r, ["출처", f"{model.source} — 이벤트 회차별 타사수관금액 구간별 고객 수(실측)"])
    r += 1
    put(ws, r, ["교차 검증", "회차별 총 고객 수가 reference_deposit_events.csv 의 신청자수와 "
                          "10건 중 9건 정확히 일치(1607 은 진행 경과로 16,356 → 17,399). "
                          "분포의 모집단이 '신청 고객'임이 확인된다"], fill=BASE_FILL)
    r += 1
    put(ws, r, ["사용 회차", f"{model.n_events}회차, 고객 수 가중 통합 "
                          f"(총 신청 {int(sum(rr['applicants'] for rr in dist_rows)):,}명, "
                          f"리워드 대상 {int(sum(sum(rr['counts'][1:]) for rr in dist_rows)):,}명). "
                          f"회차 규모가 4,063~19,831명으로 5배 차이나 가중이 맞다"])
    r += 1
    put(ws, r, ["구간 내부 보간", "로그축 균등. 1.5배 인정 때문에 리워드 구간 경계가 실측 구간 "
                             "안쪽(예: 실제 2,000만원)에 떨어져 쪼개 적분해야 한다. "
                             "구간 경계에서는 실측치를 정확히 재현"])
    r += 1
    put(ws, r, ["대상자 평균 수관금액", f"{model.mean_deposit()/1e4:,.0f}만원"])
    r += 1
    put(ws, r, ["치환 효과", f"이전(로그정규 가정) 지급률 {spec_payout:.1%}×단가 "
                          f"{legacy.avg_budget_cost()/1e4:,.1f}만원 = "
                          f"{spec_payout*legacy.avg_budget_cost():,.0f}원/신청 → "
                          f"실측 {payout:.2%}×{model.avg_budget_cost()/1e4:,.1f}만원 = "
                          f"{base_per:,.0f}원/신청 "
                          f"({base_per/(spec_payout*legacy.avg_budget_cost())-1:+.1%})"],
        fill=BASE_FILL)
    r += 1
    put(ws, r, ["해석", "분해는 크게 달라졌지만(대상자 적고 단가 높음) 곱인 신청 1인당 예산은 "
                      "수렴한다 — 두 방식이 같은 총예산을 다르게 쪼개고 있었다는 뜻"])
    r += 1

    r += 1
    ws.cell(row=r, column=1, value="[ 한계 ]").font = SUB_FONT
    r += 1
    for note in [
        "지급률은 신청자 수와 독립이라고 가정. 유입 급증 시 질이 희석돼 지급률이 낮아질 수 "
        "있어 이 가정은 예산을 보수적(과대)으로 만든다.",
        "2026_04는 시즌4 take-rate에 연말효과가 이미 일부 반영돼 있다. 배수 1.8은 모델 "
        "기준선 대비 '추가' 변동으로 해석해야 하며 연말효과를 이중 계상하지 않도록 주의.",
        "믹스 시프트는 분포 전체의 중앙값 상향으로 모델링. 대량입금 고객 신규 유입 시 "
        "조건충족률도 함께 오를 수 있으나 지급률은 고정했다.",
        "몬테카를로 P50이 기준셀보다 높은 것은 정상이다. 두 축 모두 최빈값에서 위쪽으로 "
        "더 멀리 뻗은 삼각분포라 중앙값이 최빈값 위에 형성된다(상방 비대칭 설정의 귀결).",
        "연말효과 가정은 실측과 어긋난다. 유일한 12월 단독 회차 1536(202512)이 10회차 중 "
        "최저이고 최고는 9월 회차 1470(202509)이다. 축2 상향은 계절 효과가 아니라 "
        "일반적인 상방 리스크로 읽어야 한다.",
        "최저 구간 [0, 5백만)의 하한은 10만원으로 가정했다(로그축 보간용). 대상자 평균 "
        "수관금액 계산에만 영향을 준다.",
    ]:
        put(ws, r, ["", note])
        r += 1
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 100

    # ---------------- 구간별리워드 ----------------
    ws = wb.create_sheet("구간별리워드")
    title(ws, "타사이전금액 구간별 리워드 (현재안) — 고정")
    r = 3
    head(ws, r, ["인정실적 구간", "리워드(원)", "예산반영액(원)", "실제 수관금액 하한",
                 "실제 수관금액 상한", "대상자 중 비율(실측)"],
         width=[18, 14, 16, 20, 20, 14])
    r += 1
    base_shares = model.shares(0.0)
    for i, lab in enumerate(model.labels):
        hi = model.edges[i + 1]
        put(ws, r, [lab, model.rewards[i], round(model.gross[i]),
                    model.edges[i], None if hi == float("inf") else hi,
                    base_shares[i]], fmt="#,##0")
        ws.cell(row=r, column=6).number_format = "0.0%"
        if hi == float("inf"):
            ws.cell(row=r, column=5, value="—")
        r += 1
    put(ws, r, ["가중평균", round(model.avg_reward()), round(model.avg_budget_cost()),
                "", "", 1.0], fmt="#,##0", fill=SUB_FILL, bold=True)
    ws.cell(row=r, column=6).number_format = "0.0%"
    r += 2
    ws.cell(row=r, column=1,
            value="※ 예산반영액 = 리워드 5만원 미만이면 그대로, 5만원 이상이면 리워드÷0.78(제세 포함).")
    r += 1
    ws.cell(row=r, column=1,
            value="※ 실제 이전금액 구간 = 인정실적 구간을 1.5배 인정 규칙으로 되돌린 값. "
                  "예) 인정 3천만원 → 실제 2천만원 입금이면 6만원 지급.")

    # ---------------- 믹스시프트 ----------------
    ws = wb.create_sheet("믹스시프트")
    title(ws, "축2 — 수관금액 구간 비율 변동 (실측 회차 변동 방향을 눈금으로)")
    r = 3
    head(ws, r, ["기울기 θ", "시나리오", "지급률"] + model.labels +
         ["평균 예산반영 단가(원)", "신청 1인당(원)", "기준대비", "대상자 평균 수관금액(만원)"],
         width=[10, 12, 10] + [12] * len(model.labels) + [20, 16, 10, 24])
    r += 1
    for sv in shifts:
        sh = model.shares(sv)
        cost = model.avg_budget_cost(sv)
        pr = model.payout_rate(sv)
        put(ws, r, [sv, stress.shift_label(sv), pr] + list(sh) +
            [round(cost), round(pr * cost), pr * cost / base_per,
             round(model.mean_deposit(sv) / 1e4)])
        ws.cell(row=r, column=1).number_format = "+0.0;-0.0"
        ws.cell(row=r, column=3).number_format = "0.00%"
        for ci in range(4, 4 + len(sh)):
            ws.cell(row=r, column=ci).number_format = "0.0%"
        ws.cell(row=r, column=4 + len(sh)).number_format = "#,##0"
        ws.cell(row=r, column=5 + len(sh)).number_format = "#,##0"
        ws.cell(row=r, column=6 + len(sh)).number_format = '#,##0.00"배"'
        ws.cell(row=r, column=7 + len(sh)).number_format = "#,##0"
        if sv == 0.0:
            put(ws, r, [sv, stress.shift_label(sv)], fill=BASE_FILL)
            ws.cell(row=r, column=1).number_format = "+0.0;-0.0"
        r += 1
    r += 2
    ws.cell(row=r, column=1,
            value=f"※ θ=0 은 실측 {model.n_events}회차 가중 통합, θ=+1 은 관측 최고 회차"
                  f"({model.tilt_hi_event}), θ=-1 은 관측 최저 회차({model.tilt_lo_event}). "
                  f"|θ|>1 은 관측 범위를 벗어난 외삽 구간이다.").font = SUB_FONT
    r += 1
    ws.cell(row=r, column=1,
            value="※ 회차 간 변동은 대상자 내부 믹스가 아니라 '지급률'에 거의 전부 몰려 있다"
                  "(단가 ±9% vs 지급률 13.7~31.7% ±40%). 그래서 축2는 금액축을 "
                  "미는 대신 관측된 변동 방향을 그대로 기울기로 쓴다.")
    r += 2

    ws.cell(row=r, column=1, value="[ 회차별 실측 분포 — 가정 없이 관측치 그대로 ]").font = SUB_FONT
    r += 1
    head(ws, r, ["회차", "지급률", "평균 단가(원)", "신청 1인당(원)", "평균 대비", "등가 θ"],
         width=[12, 12, 16, 16, 12, 12])
    r += 1
    for row in stress.event_scenarios(1.0, events):
        put(ws, r, [row["event"], row["payout_rate"], round(row["avg_cost"]),
                    round(row["per_applicant"]), row["per_applicant"] / base_per,
                    stress.equivalent_shift(model, row["per_applicant"])])
        ws.cell(row=r, column=2).number_format = "0.00%"
        ws.cell(row=r, column=3).number_format = "#,##0"
        ws.cell(row=r, column=4).number_format = "#,##0"
        ws.cell(row=r, column=5).number_format = '#,##0.00"배"'
        ws.cell(row=r, column=6).number_format = "+0.00;-0.00"
        if row["event"] in (model.tilt_lo_event, model.tilt_hi_event):
            for ci in range(1, 7):
                ws.cell(row=r, column=ci).fill = WARN_FILL
        r += 1
    r += 1
    ws.cell(row=r, column=1,
            value="※ 연말효과 가정과 실측이 어긋난다. 유일한 12월 단독 회차 1536(202512)이 "
                  "최저이고, 최고는 9월 회차 1470(202509)이다.")

    # ---------------- 스트레스매트릭스 ----------------
    ws = wb.create_sheet("스트레스매트릭스")
    title(ws, "스트레스 매트릭스 — 회차별 총예산 (단위: 억원)")
    r = 3
    for rnd, d in per_round.items():
        by = {(c["mult"], c["shift"]): c for c in d["cells"]}
        ws.cell(row=r, column=1,
                value=f"{rnd}  (기준 신청자 {d['base']:,.0f}명)").font = SUB_FONT
        r += 1
        head(ws, r, ["신청 배수"] + [f"θ={s:+.1f} {stress.shift_label(s)}" for s in shifts],
             width=[18] + [16] * len(shifts))
        r += 1
        for m in mults:
            put(ws, r, [f"×{m:.1f} {stress.mult_label(m)}"] +
                [by[(m, s)]["total"] / EOK for s in shifts], fmt="#,##0.00")
            if m == 1.0:
                ws.cell(row=r, column=2).fill = BASE_FILL
            if m == max(mults):
                ws.cell(row=r, column=1 + len(shifts)).fill = WARN_FILL
            r += 1
        b = by[(1.0, 0.0)]
        w = stress.worst_case(d["cells"])
        ws.cell(row=r, column=1,
                value=f"기준셀 {b['total']/EOK:.2f}억 → worst {w['total']/EOK:.2f}억 "
                      f"(×{w['total']/b['total']:.2f}) · 기준 당첨 {b['recipients']:,.0f}명")
        r += 3
    ws.cell(row=r, column=1,
            value="※ 연두색=기준셀(신청 유지 · 믹스 = 실측 10회차 가중 통합), 주황색=worst case.")

    # ---------------- 몬테카를로 ----------------
    ws = wb.create_sheet("몬테카를로")
    title(ws, "몬테카를로 — 신청배수·믹스시프트·지급률 결합 분포 (단위: 억원)")
    r = 3
    ws.cell(row=r, column=1,
            value=f"방법: {n_mc:,}회 시뮬레이션. 신청배수 삼각(0.8/1.0/1.8), "
                  f"믹스 기울기 삼각(θ={min(shifts):+.1f}/0/{max(shifts):+.1f}), "
                  f"지급률은 기울기에서 유도 후 잔여 잡음 CV {payout_cv}.")
    r += 1
    ws.cell(row=r, column=1,
            value=f"기울기 밴드는 실측 관측 범위에서 왔고 최빈값은 {model.n_events}회차 고객 수 가중 통합(θ=0)이다 — "
                  "임의 가정이 아니라 데이터가 눈금이다.")
    r += 2
    head(ws, r, ["회차", "기대값", "P50", "P90(편성 권고)", "P95", "P99"],
         width=[22, 14, 14, 18, 14, 14])
    r += 1
    for rnd, d in per_round.items():
        mc = d["mc"]
        put(ws, r, [rnd, mc["mean"] / EOK, mc["p50"] / EOK, mc["p90"] / EOK,
                    mc["p95"] / EOK, mc["p99"] / EOK], fmt="#,##0.00")
        r += 1
    put(ws, r, ["3회차 합 (회차 독립)", portfolio["mean"] / EOK, portfolio["p50"] / EOK,
                portfolio["p90"] / EOK, portfolio["p95"] / EOK, portfolio["p99"] / EOK],
        fmt="#,##0.00", fill=SUB_FILL, bold=True)
    r += 1
    put(ws, r, ["3회차 합 (공통충격 상한)", "", "", portfolio["comonotonic_p90"] / EOK,
                portfolio["comonotonic_p95"] / EOK, ""], fmt="#,##0.00", fill=WARN_FILL)
    r += 2
    ws.cell(row=r, column=1,
            value=f"※ 합산 P90은 회차별 충격이 독립이면 {portfolio['p90']/EOK:.1f}억(분산효과 최대), "
                  f"모든 회차가 함께 움직이면 {portfolio['comonotonic_p90']/EOK:.1f}억이다. "
                  f"실제 편성치는 그 사이이며, 연말 대량입금처럼 회차를 가로지르는 충격을 "
                  f"크게 볼수록 상단에 가깝다.")
    r += 1
    ws.cell(row=r, column=1,
            value="※ P50이 매트릭스 기준셀보다 높은 것은 정상이다(상방 비대칭 설정의 귀결).")

    # ---------------- WorstCase ----------------
    ws = wb.create_sheet("WorstCase")
    title(ws, "Worst Case 분해 — 무엇이 예산을 늘리는가")
    r = 3
    head(ws, r, ["회차", "단계", "신청자", "지급률", "당첨자", "평균 단가(원)",
                 "총예산(억)", "누적 배율"],
         width=[14, 34, 14, 10, 14, 16, 14, 12])
    r += 1
    max_m, max_s = max(mults), max(shifts)
    for rnd, d in per_round.items():
        by = {(c["mult"], c["shift"]): c for c in d["cells"]}
        base = by[(1.0, 0.0)]
        steps = [
            ("① 기준 (신청 유지 · 믹스 = 실측 10회차 가중)", by[(1.0, 0.0)]),
            (f"② 신청만 ×{max_m} (유입 증가)", by[(max_m, 0.0)]),
            (f"③ 믹스만 θ={max_s:+.1f} (대량입금 유입)", by[(1.0, max_s)]),
            (f"④ worst — ×{max_m} + θ={max_s:+.1f} 동시", by[(max_m, max_s)]),
        ]
        for label, c in steps:
            put(ws, r, [rnd, label, round(c["applicants"]), c["payout_rate"],
                        round(c["recipients"]), round(c["avg_cost"]),
                        c["total"] / EOK, c["total"] / base["total"]])
            for ci in (3, 5, 6):
                ws.cell(row=r, column=ci).number_format = "#,##0"
            ws.cell(row=r, column=4).number_format = "0.00%"
            ws.cell(row=r, column=7).number_format = "#,##0.00"
            ws.cell(row=r, column=8).number_format = '#,##0.00"배"'
            if label.startswith("④"):
                for ci in range(1, 9):
                    ws.cell(row=r, column=ci).fill = WARN_FILL
            r += 1
        r += 1
    ws.cell(row=r, column=1,
            value="※ 두 축은 곱으로 작용한다. 신청 배수는 총예산에 정비례하고, 믹스 상향은 "
                  "지급률과 평균 단가를 함께 올린다 → worst 배율 ≈ 신청배수 × 신청1인당 배율.")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    wb.save(OUT)
    print(f"엑셀 보고서 생성: {OUT}")
    print(f"  시트: {', '.join(wb.sheetnames)}")


if __name__ == "__main__":
    main()
