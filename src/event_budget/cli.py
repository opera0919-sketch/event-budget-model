"""CLI — estimate / scenario 서브커맨드.

사용:
  python -m event_budget.cli estimate events/2026_pension_irp.yaml
  python -m event_budget.cli scenario events/2026_pension_irp.yaml \
      --goal 0.8,1.0,1.2 --payout 0.6,0.7,0.8 --reward 40000,50000
"""
from __future__ import annotations

import argparse
import sys

from . import backtest, demand, report, stress, tiers
from .calibrate import apply_calibration
from .scenario import scenario_grid
from .schema import load_benchmarks, load_history, load_market, load_spec
from .report import fmt_n, fmt_won, fmt_won2


def _load(spec_path):
    spec = load_spec(spec_path)
    history = load_history(spec.history_csv)
    raw = load_benchmarks()
    market = load_market(spec.market_csv)
    # 실적으로 시즌 take-rate 보정
    benchmarks = apply_calibration(history, spec.products, raw)
    return spec, history, benchmarks, raw, market


def _forecasts(spec, history, benchmarks, market):
    return {p.name: demand.predict_product(history, p, benchmarks,
                                           spec.forecast_rounds, market)
            for p in spec.products}


def cmd_estimate(args):
    spec, history, benchmarks, raw, market = _load(args.spec)
    forecasts = _forecasts(spec, history, benchmarks, market)

    print(f"\n[{spec.title}]  회차: {', '.join(spec.forecast_rounds)}")
    for p in spec.products:
        fc = forecasts[p.name]
        print(f"\n=== {p.label} 신청고객수 예측 (보수/기준/낙관) ===")
        for rnd in fc.rounds:
            b = fc.base[rnd]
            a = fc.applicants[rnd]
            tr = fc.take_rate[rnd]
            print(f"  {rnd}: 기준고객수 {fmt_n(b):>12} | "
                  f"take {tr[0]*100:.2f}/{tr[1]*100:.2f}/{tr[2]*100:.2f}% | "
                  f"신청 {fmt_n(a[0]):>8} / {fmt_n(a[1]):>8} / {fmt_n(a[2]):>8}")

    md = report.build_report(spec, forecasts, benchmarks)
    path = report.write_report(spec, md)
    print(f"\n리포트 생성: {path}")


def _parse_floats(s):
    return [float(x) for x in s.split(",") if x.strip() != ""]


def cmd_scenario(args):
    spec, history, benchmarks, raw, market = _load(args.spec)
    forecasts = _forecasts(spec, history, benchmarks, market)

    levers = spec.levers or benchmarks.get("levers", {})
    goals = _parse_floats(args.goal) if args.goal else levers.get("goal_achievement", [0.8, 1.0, 1.2])
    payouts = _parse_floats(args.payout) if args.payout else levers.get("reward_payout_rate", [0.6, 0.7, 0.8])

    for p in spec.products:
        if args.product and p.name != args.product:
            continue
        fc = forecasts[p.name]
        rewards = _parse_floats(args.reward) if args.reward else [p.avg_reward]
        for rnd in fc.rounds:
            if args.round and rnd != args.round:
                continue
            a_mid = fc.applicants[rnd][1]   # 기준 신청자로 grid
            print(f"\n=== {p.label} {rnd} 예산 스윕 "
                  f"(기준 신청자 {fmt_n(a_mid)}) ===")
            print(f"{'목표달성':>8}{'지급률':>8}{'평균리워드':>12}{'지급대상':>12}{'총예산':>16}")
            grid = scenario_grid(a_mid, goals, payouts, rewards,
                                 p.fixed_costs, p.tax_rate)
            for row in grid:
                print(f"{row['goal_achievement']:>8.2f}{row['reward_payout_rate']:>8.2f}"
                      f"{fmt_won(row['avg_reward']):>12}{fmt_n(row['recipients']):>12}"
                      f"{fmt_won(row['total']):>16}")

    md = report.build_report(spec, forecasts, benchmarks)
    path = report.write_report(spec, md)
    print(f"\n리포트 생성: {path}")


def _stress_product(spec):
    """스트레스 대상 상품 = reward_tier_key 를 가진 첫 상품."""
    for p in spec.products:
        if p.reward_tier_key:
            return p
    raise SystemExit("명세에 reward_tier_key 를 지정한 상품이 없다 "
                     "(events/2026_pension_transfer_stress.yaml 참조).")


def cmd_stress(args):
    spec, history, benchmarks, raw, market = _load(args.spec)
    forecasts = _forecasts(spec, history, benchmarks, market)

    p = _stress_product(spec)
    model = tiers.load_empirical_tier_model(p.reward_tier_key, p.reward_tiers_path)
    events = tiers.event_tier_models(p.reward_tier_key, p.reward_tiers_path)
    legacy = tiers.load_tier_model(p.reward_tier_key, p.reward_tiers_path)

    cfg = spec.stress or {}
    mults = _parse_floats(args.mults) if args.mults else cfg.get(
        "applicant_multipliers", stress.DEFAULT_MULTS)
    shifts = _parse_floats(args.shifts) if args.shifts else cfg.get(
        "mix_shifts", stress.DEFAULT_SHIFTS)
    payout_cv = float(cfg.get("payout_cv", 0.22))
    n_mc = args.mc if args.mc else int(cfg.get("montecarlo_n", 40000))
    mc_shift = tuple(_parse_floats(args.mc_shift_band) if args.mc_shift_band
                     else cfg.get("mc_shift_band", stress.DEFAULT_MC_SHIFT_BAND))
    mc_mult = tuple(_parse_floats(args.mc_mult_band) if args.mc_mult_band
                    else cfg.get("mc_mult_band", stress.DEFAULT_MC_MULT_BAND))

    payout = model.payout_rate(0.0)
    spec_payout = (p.conversion_rate or 1.0) * (p.condition_rate or 1.0)

    print(f"\n[{spec.title}]")
    print(f"  금액 분포: 실측 {model.n_events}회차 고객 수 가중 ({model.source})")
    print(f"  리워드: 현재안 고정 · 평균 예산반영 단가 "
          f"{fmt_won2(model.avg_budget_cost())} (제세 제외 {fmt_won2(model.avg_reward())})")
    print(f"  지급률(수관 5백만원 이상) {payout:.2%} · 대상자 평균 수관금액 "
          f"{fmt_won(model.mean_deposit())}")
    print(f"  신청 1인당 예산 {payout*model.avg_budget_cost():,.0f}원 "
          f"(구 로그정규 가정 {spec_payout*legacy.avg_budget_cost():,.0f}원, "
          f"{payout*model.avg_budget_cost()/(spec_payout*legacy.avg_budget_cost())-1:+.1%})")

    print(f"\n=== 축2: 수관금액 구간 비율 변동 (실측 회차 변동 방향을 눈금으로) ===")
    print(f"  θ=0 실측 {model.n_events}회차 가중 · θ=+1 관측 최고({model.tilt_hi_event}) · "
          f"θ=-1 관측 최저({model.tilt_lo_event})")
    print(f"{'기울기':>8}{'구분':>10}{'지급률':>9}{'평균단가':>13}{'신청1인당':>11}"
          f"{'기준대비':>9}   " + " ".join(f"{lab:>10}" for lab in model.labels))
    base_per = payout * model.avg_budget_cost()
    for s in shifts:
        sh = model.shares(s)
        c = model.avg_budget_cost(s)
        pr = model.payout_rate(s)
        print(f"{stress.shift_tick(s):>8}{stress.shift_label(s):>10}{pr:>8.2%}"
              f"{fmt_won2(c):>13}{pr*c:>10,.0f}원{pr*c/base_per:>8.2f}x   "
              + " ".join(f"{x*100:>9.1f}%" for x in sh))

    print(f"\n=== 참고: 회차별 실측 분포 (가정 없이 관측치 그대로) ===")
    ev = stress.event_scenarios(1.0, events)
    print(f"{'회차':>8}{'지급률':>9}{'평균단가':>13}{'신청1인당':>11}{'평균대비':>9}"
          f"{'등가시프트':>11}")
    for row in ev:
        print(f"{row['event']:>8}{row['payout_rate']:>8.2%}"
              f"{fmt_won2(row['avg_cost']):>13}{row['per_applicant']:>10,.0f}원"
              f"{row['per_applicant']/base_per:>8.2f}x"
              f"{stress.shift_tick(stress.equivalent_shift(model, row['per_applicant'])):>11}")

    fc = forecasts[p.name]
    per_round = {}
    for i, rnd in enumerate(fc.rounds):
        base = fc.applicants[rnd][1]
        cells = stress.stress_matrix(base, model, None, mults, shifts)
        # 회차마다 시드를 달리해 독립 추출 → 합산 시 분산효과 하한을 얻는다.
        mc = stress.stress_montecarlo(base, model, None, payout_cv,
                                      mult_band=mc_mult, shift_band=mc_shift,
                                      n=n_mc, seed=42 + i)
        per_round[rnd] = {"base": base, "cells": cells, "mc": mc,
                          "events": stress.event_scenarios(base, events)}

        by = {(c["mult"], c["shift"]): c for c in cells}
        print(f"\n=== {rnd} 스트레스 매트릭스 (기준 신청자 {fmt_n(base)}) ===")
        print(f"{'배수':>8}" + "".join(f"{stress.shift_tick(s):>14}" for s in shifts))
        for m in mults:
            print(f"{m:>7.1f}x" + "".join(f"{fmt_won(by[(m, s)]['total']):>14}"
                                          for s in shifts))
        w = stress.worst_case(cells)
        b = by[(1.0, 0.0)]
        print(f"  기준셀 {fmt_won(b['total'])} → worst(×{w['mult']:.1f}/{stress.shift_tick(w['shift'])}) "
              f"{fmt_won(w['total'])} (×{w['total']/b['total']:.2f})")
        print(f"  MC: P50 {fmt_won(mc['p50'])} | P90 {fmt_won(mc['p90'])} | "
              f"P95 {fmt_won(mc['p95'])} | P99 {fmt_won(mc['p99'])}")

    portfolio = stress.portfolio_montecarlo({k: v["mc"] for k, v in per_round.items()})
    print(f"\n=== {len(per_round)}회차 합산 포트폴리오 "
          f"(MC 밴드: 배수 {mc_mult[0]}~{mc_mult[2]} · "
          f"θ {mc_shift[0]:+.1f}~{mc_shift[2]:+.1f}) ===")
    print(f"  P50 {fmt_won(portfolio['p50'])} | P90 {fmt_won(portfolio['p90'])} | "
          f"P95 {fmt_won(portfolio['p95'])} | P99 {fmt_won(portfolio['p99'])}")
    print(f"  편성 권고 P90 범위: {fmt_won(portfolio['p90'])}(회차 독립) ~ "
          f"{fmt_won(portfolio['comonotonic_p90'])}(공통충격)")

    md = report.build_stress_report(spec, model, payout, per_round, mults, shifts,
                                    portfolio, events=events, legacy=legacy,
                                    spec_payout=spec_payout,
                                    mc_mult_band=mc_mult, mc_shift_band=mc_shift)
    path = report.write_stress_report(spec, md)
    print(f"\n리포트 생성: {path}")


def cmd_backtest(args):
    spec, history, benchmarks, raw, market = _load(args.spec)
    min_train = args.min_train
    target = args.target
    min_cv = float(raw.get("min_cv", 0.15))
    print(f"\n[{spec.title}] 백테스트 (사후예측, min_train={min_train}, "
          f"목표 커버리지 {target:.0%})")
    per_product = {}
    for p in spec.products:
        res = backtest.backtest_product(history, p, raw, market, min_train=min_train)
        s = backtest.summarize(res)
        cv = backtest.suggested_cv(res, target)
        per_product[p.label] = (p.base_mode, res, s, cv)
        print(f"\n=== {p.label} (base_mode={p.base_mode}) ===")
        if not res:
            print("  평가 가능한 회차 없음(학습표본 부족)")
            continue
        print(f"{'회차':>8}{'실제':>10}{'예측(기준)':>12}{'오차%':>9}{'밴드내':>7}")
        for r in res:
            print(f"{r['round']:>8}{fmt_n(r['actual']):>10}{fmt_n(r['pred_base']):>12}"
                  f"{r['ape']*100:>8.1f}%{'  O' if r['in_band'] else '  X':>7}")
        print(f"  MAPE {s['mape']*100:.1f}% | 편향 {s['bias']*100:+.1f}% | "
              f"밴드 커버리지 {s['coverage']*100:.0f}% (n={s['n']})")
        print(f"  현재 min_cv={min_cv:.2f} → 목표 커버리지 위한 경험적 CV≈{cv:.2f}")

    md = report.build_backtest_report(spec, per_product, min_train, target, min_cv)
    path = report.write_backtest_report(spec, md)
    print(f"\n백테스트 리포트 생성: {path}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="event_budget", description="이벤트 신청고객수·예산 예측")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("estimate", help="신청고객수·기준예산 예측")
    pe.add_argument("spec", help="이벤트 명세 YAML 경로")
    pe.set_defaults(func=cmd_estimate)

    ps = sub.add_parser("scenario", help="레버 grid 스윕")
    ps.add_argument("spec", help="이벤트 명세 YAML 경로")
    ps.add_argument("--goal", help="목표달성률 리스트 (콤마구분)")
    ps.add_argument("--payout", help="리워드 지급률 리스트 (콤마구분)")
    ps.add_argument("--reward", help="평균 리워드 금액 리스트 (콤마구분)")
    ps.add_argument("--round", help="특정 회차만")
    ps.add_argument("--product", help="특정 상품(name)만")
    ps.set_defaults(func=cmd_scenario)

    pt = sub.add_parser("stress",
                        help="리워드 현행 유지 시 신청자수·이전금액구간 2축 스트레스 테스트")
    pt.add_argument("spec", help="이벤트 명세 YAML 경로")
    pt.add_argument("--mults", help="신청 고객수 배수 리스트 (콤마구분, 예 0.8,1.0,1.8)")
    pt.add_argument("--shifts", help="이전금액 분포 중앙값 상향률 리스트 (콤마구분, 예 0,0.5,1.5)")
    pt.add_argument("--mc", type=int, help="몬테카를로 시행 횟수 (기본 40000)")
    pt.add_argument("--mc-shift-band", dest="mc_shift_band",
                    help="MC 기울기 밴드 lo,mode,hi (기본 -1.0,0,1.0 = 실측 관측 범위)")
    pt.add_argument("--mc-mult-band", dest="mc_mult_band",
                    help="MC 신청배수 밴드 lo,mode,hi (기본 0.8,1.0,1.2)")
    pt.set_defaults(func=cmd_stress)

    pb = sub.add_parser("backtest", help="사후예측 정확도(MAPE·커버리지) 검증")
    pb.add_argument("spec", help="이벤트 명세 YAML 경로")
    pb.add_argument("--min-train", type=int, default=4, dest="min_train",
                    help="평가에 필요한 최소 학습 회차 수 (기본 4)")
    pb.add_argument("--target", type=float, default=0.8,
                    help="목표 밴드 커버리지 (기본 0.8)")
    pb.set_defaults(func=cmd_backtest)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
