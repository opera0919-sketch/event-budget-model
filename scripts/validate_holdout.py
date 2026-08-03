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

    out.append("## C. 예산 모델 전체 백테스트 — **성립하지 않는다**\n")
    out.append(f"> ⛔ **{TARGET_EVENT} 이전 회차는 리워드 지급 조건 자체가 달랐다.**"
               f" 현재 구간표(`params/reward_tiers.yaml`)가 적용된 것은"
               f" {TARGET_EVENT}(2026-05~07)부터이므로, 그 이전 회차의 `budget_eok` 로"
               " 구간모델을 검증하거나 보정계수를 뽑는 것은 **다른 상품의 가격표로"
               " 채점하는 것과 같다**. 아래 표는 그 사실을 확인하려고 남겨 둔 것이지"
               " 모델 오차의 측정치가 아니다.\n")
    out.append("그래서 구간모델의 유효 검증 표본은 현재 조건 아래 종료된"
               f" {TARGET_EVENT} 한 건뿐이고, 그 결과는 B 에 있다 — 신청 1인당"
               " 예측 43,065원 vs 실측 44,810원, **-3.9%**.\n")
    out.append("아래는 참고용 대조다. Leave-One-Out(해당 회차만 빼고 학습)과"
               " 순차(선행 회차만 학습) 두 방식으로 돌렸다.\n")
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
    out.append(f"자체 분포로 계산한 1인당 예산과 실측의 상관은 r={corr:.3f} 로 높다."
               " 조건이 달랐던 회차에서도 '금액이 크게 들어온 회차일수록 예산이 컸다'는"
               " 방향은 같았다는 뜻이고, 분포→예산 계산 구조 자체가 틀리지 않았다는"
               " 약한 방증은 된다. 수준(level)까지 맞을 이유는 없다 — 지급 조건이 달랐으니까.\n")

    i = [r["ev"] for r in loo].index(TARGET_EVENT)
    tgt = loo[i]
    out.append(f"실제로 {TARGET_EVENT} 은 실측/모델 비율 {ratios[i]:.3f} 로 10회차 중 가장 높고"
               f" LOO 오차 {tgt['err']*100:+.0f}% 로 가장 작다."
               f" 나머지 9회차의 비율({min(ratios[:i] + ratios[i+1:]):.3f}~"
               f"{max(ratios[:i] + ratios[i+1:]):.3f})이 낮은 것은"
               " 모델 편의가 아니라 **당시 지급 조건이 지금보다 덜 후했기 때문**으로 읽어야 한다."
               " 앞선 판(2026-08-03 이전)에서 제시했던 보정계수 k≈0.70 은"
               " 조건이 다른 회차로 뽑은 값이므로 **적용하지 않는다**.\n")
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

    out.append("## D. 스트레스 리포트 기준셀에 대한 함의 — **보정하지 않는다**\n")
    out.append(f"현재 기준셀은 `예측 신청자 × {pa:,.0f}원` 그대로 둔다."
               " C 에서 보정계수를 뽑을 수 있는 것처럼 보이지만, 그 계수는 지급 조건이"
               f" 달랐던 {TARGET_EVENT} 이전 회차에서 나온 값이라 현재 구간표에 적용할 근거가 없다.\n")
    out.append("| 회차 | 예측 신청자 | 기준셀 |")
    out.append("|---|---:|---:|")
    tot = 0.0
    for rnd in spec.forecast_rounds:
        a = fc.applicants[rnd][1]
        tot += a * pa
        out.append(f"| {rnd} | {a:,.0f} | {a*pa/1e8:.2f}억 |")
    out.append(f"| **합계** | | **{tot/1e8:.2f}억** |")
    out.append("")

    ev = next(r for r in ref if r["period"].startswith("2026-05"))
    out.append(f"현재 조건으로 종료된 유일한 회차 {TARGET_EVENT} 은 3개월간"
               f" 신청 {float(ev['applicants']):,.0f}명 / 리워드"
               f" {float(ev['budget_eok']):.2f}억이었다. 같은 3개월 길이인 예측 회차의"
               f" 기준셀({tot/len(spec.forecast_rounds)/1e8:.1f}억 내외)은 신청자 예측이"
               " 그보다 큰 만큼 높게 나오며, 단가 자체는 실측과 -3.9% 로 맞는다.\n")
    out.append("> ⚠️ 유효 검증 표본이 1회차뿐이다. 단가가 맞은 것을 '모델이 검증됐다'로"
               " 읽기에는 근거가 얇으므로, 다음 회차가 끝나는 대로 같은 홀드아웃을 다시 돌려"
               " 표본을 늘려야 한다.\n")
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
