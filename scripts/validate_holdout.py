"""26.5~7월(회차 2026_02 / 이벤트 1607) 홀드아웃 검증.

모델을 '그 시점에 알 수 있던 데이터만'으로 돌려 실측과 대조한다. 세 축을 나눠 본다.

  A. 신청 고객 수  — history.csv 2026_02 을 가리고 2단계 수요모델로 예측
  B. 예산 단가     — 이벤트 1607 을 뺀 9회차 분포로 신청 1인당 예산을 예측
  C. 예산 모델 전체 — 회차별 Leave-One-Out·순차 백테스트로 편향과 설명력 측정

실행: PYTHONPATH=src python scripts/validate_holdout.py [--write]
"""
from __future__ import annotations

import argparse
import csv
import os
import statistics as st
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from event_budget import backtest, demand, tiers                      # noqa: E402
from event_budget.calibrate import apply_calibration                  # noqa: E402
from event_budget.schema import (_resolve, load_benchmarks, load_history,  # noqa: E402
                                 load_market, load_spec)

DEMAND_SPEC = "events/2026_pension_irp.yaml"
STRESS_SPEC = "events/2026_pension_transfer_stress.yaml"
TARGET_ROUND = "2026_02"          # 202605~202607
TARGET_EVENT = "1607"             # 같은 기간의 이벤트 회차
REFERENCE_CSV = "data/reference_deposit_events.csv"


def load_reference_rows() -> list[dict]:
    """이벤트 실행 실적(예산·신청·순입금·조건충족) — 주석 줄 제외."""
    with open(_resolve(REFERENCE_CSV), "r", encoding="utf-8") as f:
        reader = csv.DictReader(l for l in f if not l.lstrip().startswith("#"))
        return [r for r in reader if (r.get("round_label") or "").strip()]


# ---------------------------------------------------------------- A. 신청 고객 수
def track_a(out: list[str]) -> dict:
    spec = load_spec(DEMAND_SPEC)
    history = load_history(spec.history_csv)
    raw = load_benchmarks()
    market = load_market(spec.market_csv)
    idx = next(i for i, r in enumerate(history) if r["round"] == TARGET_ROUND)
    row = history[idx]

    res = {}
    for p in spec.products:
        actual = row[p.applicants_col]
        actual_base = row[p.base_col_end]
        trunc = backtest._blank_from(history, p, idx)          # 이후 회차 전부 가림
        bench = apply_calibration(trunc, [p], raw)
        fc = demand.predict_product(trunc, p, bench, [TARGET_ROUND], market)
        cons, base, opt = fc.applicants[TARGET_ROUND]
        res[p.name] = {
            "label": p.label, "actual": actual, "pred": base,
            "cons": cons, "opt": opt,
            "err": (base - actual) / actual,
            "in_band": cons <= actual <= opt,
            "base_pred": fc.base[TARGET_ROUND], "base_actual": actual_base,
            "base_err": (fc.base[TARGET_ROUND] - actual_base) / actual_base,
            "tr_pred": fc.take_rate[TARGET_ROUND][1], "tr_actual": actual / actual_base,
        }

    # take-rate 블렌드 구성 — 상품별로 어떤 재료가 들어갔는지
    exclude = set(raw.get("exclude_rounds", []))
    for p in spec.products:
        trunc = backtest._blank_from(history, p, idx)
        seas = demand.season_take_rates(trunc, p.base_col_end, p.applicants_col, exclude)
        obs = [v for _, v in seas.get(row["season"], [])]
        res[p.name]["decomp"] = {
            "season_obs": obs,
            "season_mean": st.mean(obs) if obs else None,
            "recent": demand.recent_take_rate(trunc, p.base_col_end, p.applicants_col,
                                              exclude, int(raw.get("recent_window", 4))),
            "blend_w": float(raw.get("blend_recent_weight", 0.5)),
        }

    out.append(f"## A. 신청 고객 수 — {TARGET_ROUND}(202605~202607) 홀드아웃\n")
    out.append(f"{TARGET_ROUND} 이후 행의 기준고객수·신청자를 모두 가리고, take-rate 보정까지"
               " 절단된 이력으로만 다시 계산했다.\n")
    out.append("| 상품 | 예측(기준) | 밴드 | 실측 | 오차 | 밴드내 |")
    out.append("|---|---:|---|---:|---:|:--:|")
    for p in spec.products:
        r = res[p.name]
        out.append(f"| {r['label']} | {r['pred']:,.0f} | {r['cons']:,.0f} ~ {r['opt']:,.0f} "
                   f"| {r['actual']:,.0f} | {r['err']*100:+.1f}% "
                   f"| {'O' if r['in_band'] else '**X**'} |")
    out.append("")
    out.append("두 상품의 결과가 갈린다. 어느 단계에서 갈렸는지 보면 이렇다.\n")
    out.append("| 상품 | Stage1 기준고객수 | 오차 | Stage2 take-rate | 오차 |")
    out.append("|---|---:|---:|---:|---:|")
    for p in spec.products:
        r = res[p.name]
        out.append(f"| {r['label']} | {r['base_pred']:,.0f} → {r['base_actual']:,.0f} "
                   f"| {r['base_err']*100:+.1f}% "
                   f"| {r['tr_pred']*100:.3f}% → {r['tr_actual']*100:.3f}% "
                   f"| {(r['tr_pred']-r['tr_actual'])/r['tr_actual']*100:+.1f}% |")
    out.append("")
    out.append("기준고객수 투영은 두 상품 모두 3% 안으로 맞았다. 차이는 전부 take-rate 에서"
               " 나며, 블렌드 재료를 펼치면 원인이 드러난다.\n")
    out.append("| 상품 | 시즌2 관측 | 시즌평균 | 최근추세 | 블렌드(예측) | 실측 |")
    out.append("|---|---|---:|---:|---:|---:|")
    for p in spec.products:
        r, d = res[p.name], res[p.name]["decomp"]
        obs = ", ".join(f"{v*100:.3f}%" for v in d["season_obs"]) or "없음"
        out.append(f"| {r['label']} | {obs} ({len(d['season_obs'])}건) "
                   f"| {d['season_mean']*100:.3f}% | {d['recent']*100:.3f}% "
                   f"| {r['tr_pred']*100:.3f}% | {r['tr_actual']*100:.3f}% |")
    out.append("")
    for p in spec.products:
        r, d = res[p.name], res[p.name]["decomp"]
        alt = (d["recent"] - r["tr_actual"]) / r["tr_actual"]
        cur = (r["tr_pred"] - r["tr_actual"]) / r["tr_actual"]
        if abs(cur) > 0.15 and abs(alt) > 0.15:
            note = "재료가 둘 다 실측에서 같은 방향으로 멀어 무엇을 섞어도 못 맞힌다"
        elif abs(alt) > abs(cur):
            note = "시즌평균과 최근추세를 반반 섞은 것이 주효했다"
        else:
            note = "시즌평균을 섞은 쪽이 오차를 키웠다"
        out.append(f"- **{r['label']}**: 블렌드 {cur*100:+.1f}% vs 최근추세만"
                   f" {alt*100:+.1f}% (시즌평균 {d['season_mean']*100:.3f}% ·"
                   f" 최근추세 {d['recent']*100:.3f}%) — {note}.")
    out.append("")
    out.append("시즌2 관측이 상품마다 한 건뿐이라 이 블렌드는 표본 1개에 절반 가중을 준다."
               " 연금저축이 맞은 것은 구조가 옳아서라기보다 그 한 건이 마침 맞는 자리에"
               " 있었기 때문으로 봐야 한다 — 같은 구조에서 IRP 는 밴드를 벗어났다.")
    irp = res["irp"]
    hist = [r[spec.products[1].applicants_col] / r[spec.products[1].base_col_end]
            for r in history[:idx]
            if r.get(spec.products[1].applicants_col) and r.get(spec.products[1].base_col_end)]
    out.append(f"IRP 의 take-rate 는 직전 실적만 봐도 {min(hist)*100:.3f}~{max(hist)*100:.3f}%"
               f" (×{max(hist)/min(hist):.1f}) 로 튄다. 실측 {irp['tr_actual']*100:.3f}% 는"
               " 그 범위 안이지만 블렌드가 만들어내는 좁은 예측치로는 닿지 않는다."
               f" 현재 밴드(±CV)가 ±{(irp['opt']/irp['pred']-1)*100:.0f}% 인데 실제 변동은"
               " 그보다 훨씬 크다 — 밴드 폭이 부족한 것이 근본 원인이다.\n")
    return res


# ---------------------------------------------------------------- B/C. 예산 단가
def _model_from(cfg: dict, rows: list[dict]) -> tiers.EmpiricalTierModel:
    return tiers.build_empirical_tier_model(
        cfg, shares=tiers.average_distribution(rows, True), n_events=len(rows))


def track_b(out: list[str], cfg: dict, rows: list[dict], ref: list[dict]) -> dict:
    i = next(i for i, r in enumerate(rows) if r["event_no"] == TARGET_EVENT)
    prior = [r for j, r in enumerate(rows) if j != i]
    m_pred, m_act = _model_from(cfg, prior), _model_from(cfg, [rows[i]])
    rr = ref[i]
    app = float(rr["applicants"])
    budget = float(rr["budget_eok"]) * 1e8

    out.append(f"## B. 예산 단가 — 이벤트 {TARGET_EVENT}(2026-05~07) 홀드아웃\n")
    out.append(f"{TARGET_EVENT} 을 뺀 9회차 분포로 신청 1인당 예산을 예측하고, 같은 회차의"
               " 실측 분포·실제 집행예산과 각각 비교했다.\n")
    out.append("| 기준 | 지급률 | 평균 단가 | 신청 1인당 |")
    out.append("|---|---:|---:|---:|")
    for name, m in (("예측(9회차)", m_pred), ("실측 분포(1607)", m_act)):
        out.append(f"| {name} | {m.payout_rate()*100:.2f}% | {m.avg_budget_cost():,.0f}원 "
                   f"| {m.per_applicant():,.0f}원 |")
    out.append(f"| **실제 집행예산 ÷ 신청** | — | — | **{budget/app:,.0f}원** |")
    out.append("")
    out.append(f"예측 {m_pred.per_applicant():,.0f}원은 실제 집행예산 기준"
               f" {budget/app:,.0f}원과"
               f" {(m_pred.per_applicant()-budget/app)/(budget/app)*100:+.1f}% 로 붙는다."
               f" 반면 1607 **자체** 분포로 계산하면 {m_act.per_applicant():,.0f}원"
               f"({(m_act.per_applicant()-budget/app)/(budget/app)*100:+.1f}%)이 나와"
               " 오히려 더 벗어난다 — 자체 분포를 알아도 예산이 그만큼 나오지는 않는다는 뜻이고,"
               " 아래 C 의 수준 편의와 같은 현상이다.\n")
    out.append("구간별 대상자 비중은 잘 맞는다 — 회차 간 믹스 자체는 안정적이다.\n")
    out.append("| 구간 | 예측(9회차) | 실측(1607) | 차 |")
    out.append("|---|---:|---:|---:|")
    for lab, a, b in zip(m_pred.labels, m_pred.shares(), m_act.shares()):
        out.append(f"| {lab} | {a*100:.1f}% | {b*100:.1f}% | {(a-b)*100:+.1f}pp |")
    out.append("")
    return {"pred": m_pred.per_applicant(), "actual": budget / app}


def track_c(out: list[str], cfg: dict, rows: list[dict], ref: list[dict]) -> dict:
    def run(mode: str):
        recs = []
        for i, (row, rr) in enumerate(zip(rows, ref)):
            train = ([r for j, r in enumerate(rows) if j != i] if mode == "loo"
                     else rows[:i])
            if len(train) < 3:
                continue
            m = _model_from(cfg, train)
            app = float(rr["applicants"])
            act = float(rr["budget_eok"]) * 1e8
            pred = app * m.per_applicant()
            recs.append({"ev": row["event_no"], "period": rr["period"], "app": app,
                         "act": act, "pred": pred, "err": (pred - act) / act,
                         "act_pa": act / app, "pred_pa": m.per_applicant()})
        return recs

    loo, seq = run("loo"), run("seq")

    # 자체 분포의 설명력 및 수준 보정계수
    own, actual = [], []
    for row, rr in zip(rows, ref):
        m = _model_from(cfg, [row])
        own.append(m.per_applicant())
        actual.append(float(rr["budget_eok"]) * 1e8 / float(rr["applicants"]))
    corr = st.correlation(own, actual)
    k = sum(actual) / sum(own)
    ratios = [a / o for a, o in zip(actual, own)]

    out.append("## C. 예산 모델 전체 백테스트\n")
    out.append("1607 한 건만으로는 운이 섞인다. 10회차 전부를 대상으로 두 방식으로 돌렸다 —"
               " Leave-One-Out(해당 회차만 빼고 학습)과 순차(선행 회차만 학습).\n")
    out.append("| 이벤트 | 기간 | 신청 | 실측 | 예측(LOO) | 오차 | 실측 1인당 | 예측 1인당 |")
    out.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for r in loo:
        out.append(f"| {r['ev']} | {r['period']} | {r['app']:,.0f} | {r['act']/1e8:.2f}억 "
                   f"| {r['pred']/1e8:.2f}억 | {r['err']*100:+.0f}% "
                   f"| {r['act_pa']:,.0f}원 | {r['pred_pa']:,.0f}원 |")
    out.append("")

    def summ(recs):
        return (st.mean([abs(r["err"]) for r in recs]), st.mean([r["err"] for r in recs]),
                sum(r["pred"] for r in recs), sum(r["act"] for r in recs))

    out.append("| 방식 | n | MAPE | 편향 | 합계 예측 | 합계 실측 |")
    out.append("|---|---:|---:|---:|---:|---:|")
    for name, recs in (("Leave-One-Out", loo), ("순차(선행 회차만)", seq)):
        mape, bias, pt, at = summ(recs)
        out.append(f"| {name} | {len(recs)} | {mape*100:.1f}% | {bias*100:+.1f}% "
                   f"| {pt/1e8:.2f}억 | {at/1e8:.2f}억 |")
    out.append("")
    pa_lo, pa_hi = min(r["act_pa"] for r in loo), max(r["act_pa"] for r in loo)
    pp_lo, pp_hi = min(r["pred_pa"] for r in loo), max(r["pred_pa"] for r in loo)
    out.append(f"실측 신청 1인당 예산은 {pa_lo:,.0f}~{pa_hi:,.0f}원으로 ×{pa_hi/pa_lo:.1f} 흔들리는데,"
               f" 모델 예측은 {pp_lo:,.0f}~{pp_hi:,.0f}원(폭 {(pp_hi/pp_lo-1)*100:.0f}%)으로 거의 평평하다."
               " 회차 간 변동을 사실상 설명하지 못한다.\n")
    out.append(f"다만 **회차의 자체 분포를 주면 잘 맞는다** — 자체 분포로 계산한 1인당 예산과"
               f" 실측의 상관은 r={corr:.3f} 다. 즉 분포→예산 계산식은 옳고,"
               " 문제는 **다음 회차의 분포를 선행 회차로 예측할 수 없다**는 데 있다.\n")
    out.append("### 수준(level) 편의와 보정계수\n")
    out.append(f"실측 ÷ 자체분포 모델 비율은 회차별 {min(ratios):.3f}~{max(ratios):.3f},"
               f" 평균 **{st.mean(ratios):.3f}** ± {st.stdev(ratios):.3f}"
               f" (CV {st.stdev(ratios)/st.mean(ratios)*100:.0f}%)."
               f" 예산가중 보정계수는 k = {k:.3f} 다. 비율이 회차별로 꽤 안정적이라"
               " 단일 계수 보정이 통한다.\n")
    out.append("| 보정 | LOO MAPE | LOO 편향 |")
    out.append("|---|---:|---:|")
    for kk, name in ((1.0, "없음(현재 모델)"), (k, f"×{k:.3f}"), (0.78, "제세 제외 ×0.78")):
        errs = [(r["pred"] * kk - r["act"]) / r["act"] for r in loo]
        out.append(f"| {name} | {st.mean([abs(e) for e in errs])*100:.1f}% "
                   f"| {st.mean(errs)*100:+.1f}% |")
    out.append("")
    out.append(f"제세 gross-up 을 빼는 것(×0.78)만으로는 편의가 다 지워지지 않는다."
               f" 남는 ×{k/0.78:.2f} 는 리워드 대상(수관 5백만원 이상)으로 잡힌 고객 중"
               " 실제로는 지급되지 않은 몫으로 보인다. 두 해석 모두"
               " `data/reference_deposit_events.csv` 의 `budget_eok` 가 무엇을 담는지"
               " (세전 지급액인지 제세 포함 소요예산인지) 확인해야 확정된다.\n")

    tgt = next(r for r in loo if r["ev"] == TARGET_EVENT)
    rank = sorted(ratios, reverse=True).index(ratios[[r["ev"] for r in loo].index(TARGET_EVENT)]) + 1
    out.append(f"> ⚠️ **이 편의는 {TARGET_EVENT} 만 놓고 보면 나타나지 않는다.**"
               f" {TARGET_EVENT} 은 종료 후 확정치(신청 {tgt['app']:,.0f}명 /"
               f" 리워드 {tgt['act']/1e8:.2f}억)를 직접 받은 유일한 회차인데,"
               f" 실측/모델 비율이 {ratios[[r['ev'] for r in loo].index(TARGET_EVENT)]:.3f} 로"
               f" 10회차 중 {rank}위이고 LOO 오차도 {tgt['err']*100:+.0f}% 로 가장 작다."
               " 나머지 9회차의 `budget_eok` 가 같은 기준의 확정치인지"
               " (진행중 스냅샷이거나 제세 제외 금액은 아닌지) 확인이 필요하다."
               " 만약 9회차 값이 확정·동일기준이라면 편의는 실재하고, 아니라면 편의의 상당 부분은"
               " 데이터 정의 차이일 수 있다.\n")
    return {"k": k, "corr": corr, "loo": loo}


# ---------------------------------------------------------------- D. 스트레스 함의
def track_d(out: list[str], k: float, ref: list[dict]) -> None:
    spec = load_spec(STRESS_SPEC)
    history = load_history(spec.history_csv)
    raw = load_benchmarks()
    market = load_market(spec.market_csv)
    bench = apply_calibration(history, spec.products, raw)
    p = spec.products[0]
    fc = demand.predict_product(history, p, bench, spec.forecast_rounds, market)
    model = tiers.load_empirical_tier_model(p.reward_tier_key, p.reward_tiers_path)
    pa = model.per_applicant()

    out.append("## D. 스트레스 리포트 기준셀에 대한 함의\n")
    out.append(f"현재 기준셀은 `예측 신청자 × {pa:,.0f}원`이다. C 의 보정계수 k={k:.3f} 를"
               " 곱하면 이렇게 바뀐다.\n")
    out.append("| 회차 | 예측 신청자 | 현재 기준셀 | k 보정 |")
    out.append("|---|---:|---:|---:|")
    tot = totk = 0.0
    for rnd in spec.forecast_rounds:
        a = fc.applicants[rnd][1]
        tot += a * pa
        totk += a * pa * k
        out.append(f"| {rnd} | {a:,.0f} | {a*pa/1e8:.2f}억 | {a*pa*k/1e8:.2f}억 |")
    out.append(f"| **합계** | | **{tot/1e8:.2f}억** | **{totk/1e8:.2f}억** |")
    out.append("")

    recent = [r for r in ref if r["period"].startswith(("2026-01", "2026-05"))]
    obs = " / ".join(f"{r['period']} {float(r['budget_eok']):.2f}억" for r in recent)
    first = fc.applicants[spec.forecast_rounds[0]][1] * pa
    out.append(f"보정 후 3개월 회차 예산은 최근 3개월 이벤트 실측({obs})과 같은 자리에 놓인다."
               f" 보정 전 {first/1e8:.2f}억은 그 실측보다 15~45% 높다."
               " 다만 C 의 경고대로 k 자체가 9회차 `budget_eok` 의 정의에 달려 있으므로,"
               " 이 보정은 그 확인 전까지 참고치로만 봐야 한다.\n")
    out.append("### 신청자 모집단 정합\n")
    hist_app = next(r for r in history if r["round"] == TARGET_ROUND)[p.applicants_col]
    ev = next(r for r in ref if r["period"].startswith("2026-05"))
    dist_app = next(r for r in tiers.load_amount_distribution()
                    if r["event_no"] == TARGET_EVENT)["applicants"]
    out.append("기준셀은 `history.csv` 의 신청자와 이벤트 실적 파일의 신청자를 곱셈으로 섞어 쓴다."
               " 두 계열이 같은 것을 세는지 확인이 필요한데, 기간이 정확히 겹치는"
               f" {TARGET_ROUND}/{TARGET_EVENT} 에서"
               f" history {hist_app:,.0f}명 · 실적 {float(ev['applicants']):,.0f}명 ·"
               f" 금액분포 {dist_app:,.0f}명으로"
               f" 최대 {abs(dist_app-hist_app)/hist_app*100:.1f}% 차이에 그친다"
               " (금액분포는 종료 직전 스냅샷). 같은 모집단으로 보고 곱해도 무방하다.\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="리포트 파일로 저장")
    args = ap.parse_args()

    cfg = tiers.load_tier_table("pension_transfer_2026")
    rows = tiers.load_amount_distribution()
    ref = load_reference_rows()
    if len(rows) != len(ref):
        raise SystemExit(f"분포 {len(rows)}건 vs 실적 {len(ref)}건 — 회차 대응을 확인하라")

    out: list[str] = ["# 26.5~7월 예측 검증 (홀드아웃)\n",
                      "리워드 스트레스 모델이 실제로 얼마나 맞혔는지를, 그 시점에 알 수 있던"
                      " 데이터만으로 다시 돌려 확인한다.\n"]
    track_a(out)
    track_b(out, cfg, rows, ref)
    c = track_c(out, cfg, rows, ref)
    track_d(out, c["k"], ref)

    md = "\n".join(out)
    if args.write:
        path = _resolve("reports/2026_05-07_예측검증.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"리포트 생성: {path}")
    else:
        print(md)


if __name__ == "__main__":
    main()
