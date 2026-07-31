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
    model = tiers.load_tier_model(product.reward_tier_key, product.reward_tiers_path)
    fc = demand.predict_product(history, product, benchmarks, spec.forecast_rounds, market)

    cfg = spec.stress or {}
    mults = cfg.get("applicant_multipliers", stress.DEFAULT_MULTS)
    shifts = cfg.get("mix_shifts", stress.DEFAULT_SHIFTS)
    payout = product.conversion_rate * product.condition_rate
    payout_cv = float(cfg.get("payout_cv", 0.22))
    n_mc = int(cfg.get("montecarlo_n", 40000))

    per_round = {}
    for i, rnd in enumerate(fc.rounds):
        base = fc.applicants[rnd][1]
        per_round[rnd] = {
            "base": base,
            "cells": stress.stress_matrix(base, model, payout, mults, shifts),
            "mc": stress.stress_montecarlo(base, model, payout, payout_cv,
                                           n=n_mc, seed=42 + i),
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
    head(ws, r, ["회차", "기준 신청자", "기준셀(×1.0/+0%)", "worst case(×1.8/+150%)",
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
        ("기준셀 합", b_sum / EOK, "신청 현 수준 유지 + 구간 믹스 현행유지"),
        ("MC P50", portfolio["p50"] / EOK, "배수·시프트 상방 비대칭이 반영된 중앙값"),
        ("MC P90 (회차 독립)", portfolio["p90"] / EOK, "회차별 충격이 독립일 때 — 분산효과 최대, 하한"),
        ("MC P90 (공통충격)", portfolio["comonotonic_p90"] / EOK,
         "모든 회차가 함께 움직일 때 — 상한. 연말 대량입금처럼 회차를 가로지르는 충격이 크면 이쪽"),
        ("worst case 합", w_sum / EOK, "결정론 최악 셀(×1.8 신청 × +150% 믹스 상향)"),
    ]:
        put(ws, r, [label, val, note], fmt="#,##0.00")
        r += 1
    ws.cell(row=r - 3, column=1).fill = BASE_FILL
    ws.cell(row=r - 1, column=1).fill = WARN_FILL

    r += 1
    ws.cell(row=r, column=1,
            value=f"핵심: 리워드를 현재안대로 유지해도 신청자 1.8배 + 대량입금 믹스 상향이 "
                  f"겹치면 예산은 기준 대비 약 {w_sum/b_sum:.1f}배로 늘어난다. "
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
                 ("지급률(당첨/신청)", f"{payout:.1%} (전환 {product.conversion_rate:.1%}"
                                      f" × 조건충족 {product.condition_rate:.1%})")]:
        put(ws, r, [k, v])
        r += 1

    r += 1
    ws.cell(row=r, column=1, value="[ 흔든 것 — 2개 축 ]").font = SUB_FONT
    r += 1
    for k, v in [("축1 신청 고객 수", f"배수 {', '.join(f'×{m}' for m in mults)} "
                                    f"(0.8=감소, 1.0=유지, 1.8=연말효과 증가)"),
                 ("축2 이전금액 구간 비율", f"이전금액 분포 중앙값 상향률 "
                                        f"{', '.join(f'+{s:.0%}' for s in shifts)} "
                                        f"(연말 일시 대량입금 고객 유입 가정)")]:
        put(ws, r, [k, v])
        r += 1

    r += 1
    ws.cell(row=r, column=1, value="[ 당첨자 이전금액 분포 — 실적으로 역산 ]").font = SUB_FONT
    r += 1
    put(ws, r, ["분포 형태", "로그정규(우측 꼬리) — 소액이 다수, 대액이 소수인 입금 분포에 부합"])
    r += 1
    put(ws, r, ["앵커 (a) 조건충족률", "51% = P(이전금액 ≥ 5백만원). 최근 실측 당첨/순입금"])
    r += 1
    put(ws, r, ["앵커 (b) 평균 예산반영 단가", "127,665원. 최근 실측 예산/당첨"])
    r += 1
    put(ws, r, ["역산 결과 sigma", round(model.sigma, 4)], fmt="#,##0.0000")
    r += 1
    put(ws, r, ["당첨자 평균 이전금액", f"{model.mean_deposit()/1e4:,.0f}만원"])
    r += 1
    put(ws, r, ["검증", "구간표·1.5배 인정·제세 규칙을 모두 적용한 모델이 최근 실측 단가를 "
                       "그대로 재현 → 기준선 정합성 확인"], fill=BASE_FILL)
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
    ]:
        put(ws, r, ["", note])
        r += 1
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 100

    # ---------------- 구간별리워드 ----------------
    ws = wb.create_sheet("구간별리워드")
    title(ws, "타사이전금액 구간별 리워드 (현재안) — 고정")
    r = 3
    head(ws, r, ["인정실적 구간", "리워드(원)", "예산반영액(원)", "실제 이전금액 하한",
                 "실제 이전금액 상한", "기준 구간비율"],
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
    title(ws, "축2 — 타사이전금액 구간 비율 변동 (연말 대량입금 유입 가정)")
    r = 3
    head(ws, r, ["시프트", "시나리오"] + model.labels +
         ["평균 예산반영 단가(원)", "기준대비", "당첨자 평균 이전금액(만원)"],
         width=[10, 12] + [12] * len(model.labels) + [20, 10, 22])
    r += 1
    base_cost = model.avg_budget_cost(0.0)
    for s in shifts:
        sh = model.shares(s)
        cost = model.avg_budget_cost(s)
        put(ws, r, [f"+{s:.0%}", stress.shift_label(s)] + list(sh) +
            [round(cost), cost / base_cost, round(model.mean_deposit(s) / 1e4)])
        for ci in range(3, 3 + len(sh)):
            ws.cell(row=r, column=ci).number_format = "0.0%"
        ws.cell(row=r, column=3 + len(sh)).number_format = "#,##0"
        ws.cell(row=r, column=4 + len(sh)).number_format = '#,##0.00"배"'
        ws.cell(row=r, column=5 + len(sh)).number_format = "#,##0"
        if s == 0.0:
            put(ws, r, [f"+{s:.0%}", stress.shift_label(s)], fill=BASE_FILL)
        r += 1
    r += 1
    ws.cell(row=r, column=1,
            value="※ 시프트는 이전금액 분포의 중앙값 상향률. 상향된 분포를 구간 경계로 "
                  "다시 적분하므로 구간 비율 합계는 항상 100%다.")

    # ---------------- 스트레스매트릭스 ----------------
    ws = wb.create_sheet("스트레스매트릭스")
    title(ws, "스트레스 매트릭스 — 회차별 총예산 (단위: 억원)")
    r = 3
    for rnd, d in per_round.items():
        by = {(c["mult"], c["shift"]): c for c in d["cells"]}
        ws.cell(row=r, column=1,
                value=f"{rnd}  (기준 신청자 {d['base']:,.0f}명)").font = SUB_FONT
        r += 1
        head(ws, r, ["신청 배수"] + [f"+{s:.0%} {stress.shift_label(s)}" for s in shifts],
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
            value="※ 연두색=기준셀(신청 유지·믹스 현행유지), 주황색=worst case.")

    # ---------------- 몬테카를로 ----------------
    ws = wb.create_sheet("몬테카를로")
    title(ws, "몬테카를로 — 신청배수·믹스시프트·지급률 결합 분포 (단위: 억원)")
    r = 3
    ws.cell(row=r, column=1,
            value=f"방법: {n_mc:,}회 시뮬레이션. 신청배수 삼각(0.8/1.0/1.8), "
                  f"믹스 시프트 삼각(0/0/+150%), 지급률 Beta(평균 {payout:.1%}, CV {payout_cv}).")
    r += 1
    ws.cell(row=r, column=1,
            value="시프트 최빈값을 0으로 두어 '상향은 상방 리스크'라는 비대칭을 반영했다.")
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
    head(ws, r, ["회차", "단계", "신청자", "당첨자", "평균 단가(원)", "총예산(억)", "누적 배율"],
         width=[14, 30, 14, 14, 16, 14, 12])
    r += 1
    max_m, max_s = max(mults), max(shifts)
    for rnd, d in per_round.items():
        by = {(c["mult"], c["shift"]): c for c in d["cells"]}
        base = by[(1.0, 0.0)]
        steps = [
            ("① 기준 (신청 유지 · 믹스 현행유지)", by[(1.0, 0.0)]),
            (f"② 신청만 ×{max_m} (연말 유입 증가)", by[(max_m, 0.0)]),
            (f"③ 믹스만 +{max_s:.0%} (대량입금 유입)", by[(1.0, max_s)]),
            (f"④ worst — ×{max_m} + {max_s:.0%} 동시", by[(max_m, max_s)]),
        ]
        for label, c in steps:
            put(ws, r, [rnd, label, round(c["applicants"]), round(c["recipients"]),
                        round(c["avg_cost"]), c["total"] / EOK,
                        c["total"] / base["total"]])
            for ci in (3, 4, 5):
                ws.cell(row=r, column=ci).number_format = "#,##0"
            ws.cell(row=r, column=6).number_format = "#,##0.00"
            ws.cell(row=r, column=7).number_format = '#,##0.00"배"'
            if label.startswith("④"):
                for ci in range(1, 8):
                    ws.cell(row=r, column=ci).fill = WARN_FILL
            r += 1
        r += 1
    ws.cell(row=r, column=1,
            value="※ 두 축은 곱으로 작용한다. 신청 배수는 총예산에 정비례하고, 믹스 상향은 "
                  "평균 단가를 통해 곱해진다 → worst 배율 ≈ 신청배수 × 단가배율.")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    wb.save(OUT)
    print(f"엑셀 보고서 생성: {OUT}")
    print(f"  시트: {', '.join(wb.sheetnames)}")


if __name__ == "__main__":
    main()
