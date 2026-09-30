"""CLI — estimate / scenario 서브커맨드.

사용:
  python -m event_budget.cli estimate events/2026_pension_irp.yaml
  python -m event_budget.cli scenario events/2026_pension_irp.yaml \
      --goal 0.8,1.0,1.2 --payout 0.6,0.7,0.8 --reward 40000,50000
"""
from __future__ import annotations

import argparse
import sys

from . import backtest, demand, report
from .calibrate import apply_calibration
from .scenario import scenario_grid
from .schema import load_benchmarks, load_history, load_market, load_spec
from .report import fmt_n, fmt_won


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
