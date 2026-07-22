"""CLI — estimate / scenario 서브커맨드.

사용:
  python -m event_budget.cli estimate events/2026_pension_irp.yaml
  python -m event_budget.cli scenario events/2026_pension_irp.yaml \
      --goal 0.8,1.0,1.2 --payout 0.6,0.7,0.8 --reward 40000,50000
"""
from __future__ import annotations

import argparse
import sys

from . import demand, report
from .calibrate import apply_calibration
from .scenario import scenario_grid
from .schema import load_benchmarks, load_history, load_spec
from .report import fmt_n, fmt_won


def _load(spec_path):
    spec = load_spec(spec_path)
    history = load_history(spec.history_csv)
    benchmarks = load_benchmarks()
    # 실적으로 시즌 take-rate 보정
    benchmarks = apply_calibration(history, spec.products, benchmarks)
    return spec, history, benchmarks


def _forecasts(spec, history, benchmarks):
    return {p.name: demand.predict_product(history, p, benchmarks, spec.forecast_rounds)
            for p in spec.products}


def cmd_estimate(args):
    spec, history, benchmarks = _load(args.spec)
    forecasts = _forecasts(spec, history, benchmarks)

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
    spec, history, benchmarks = _load(args.spec)
    forecasts = _forecasts(spec, history, benchmarks)

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

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
