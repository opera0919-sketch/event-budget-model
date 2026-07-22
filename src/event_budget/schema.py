"""이벤트 명세(YAML)와 실적 CSV 로더·검증.

경로는 모두 레포 루트 기준 상대경로로 해석한다(명세 파일 위치와 무관하게).
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from typing import Optional

import yaml

# 레포 루트 = 이 파일(src/event_budget/schema.py) 기준 두 단계 위
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _resolve(path: str) -> str:
    """상대경로를 레포 루트 기준 절대경로로."""
    if os.path.isabs(path):
        return path
    return os.path.join(REPO_ROOT, path)


@dataclass
class Product:
    name: str
    label: str
    base_col_end: str
    applicants_col: str
    avg_reward: float
    fixed_costs: float = 0.0
    tax_rate: float = 0.0


@dataclass
class EventSpec:
    event_id: str
    title: str
    history_csv: str
    forecast_rounds: list[str]
    products: list[Product]
    market_csv: Optional[str] = None
    levers: dict = field(default_factory=dict)


def load_spec(path: str) -> EventSpec:
    with open(_resolve(path), "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    required = ["event_id", "title", "history_csv", "forecast_rounds", "products"]
    missing = [k for k in required if k not in raw]
    if missing:
        raise ValueError(f"명세 필수 필드 누락: {missing} ({path})")

    products = []
    for i, p in enumerate(raw["products"]):
        for k in ["name", "label", "base_col_end", "applicants_col", "avg_reward"]:
            if k not in p:
                raise ValueError(f"products[{i}] 필수 필드 누락: {k}")
        products.append(
            Product(
                name=p["name"],
                label=p["label"],
                base_col_end=p["base_col_end"],
                applicants_col=p["applicants_col"],
                avg_reward=float(p["avg_reward"]),
                fixed_costs=float(p.get("fixed_costs", 0.0)),
                tax_rate=float(p.get("tax_rate", 0.0)),
            )
        )

    return EventSpec(
        event_id=raw["event_id"],
        title=raw["title"],
        history_csv=raw["history_csv"],
        forecast_rounds=list(raw["forecast_rounds"]),
        products=products,
        market_csv=raw.get("market_csv"),
        levers=raw.get("levers", {}) or {},
    )


def _num(v: str) -> Optional[float]:
    v = (v or "").strip()
    if v == "":
        return None
    return float(v)


def load_history(path: str) -> list[dict]:
    """실적 CSV → 행 리스트. 숫자 컬럼은 float 또는 None(공란).

    주석 행(첫 셀이 '#'로 시작)은 건너뛴다.
    """
    rows: list[dict] = []
    with open(_resolve(path), "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rnd = (r.get("round") or "").strip()
            if rnd == "" or rnd.startswith("#"):
                continue
            row: dict = {"round": rnd}
            for k, v in r.items():
                if k == "round":
                    continue
                if k in ("start_ym", "end_ym"):
                    row[k] = (v or "").strip()
                else:
                    row[k] = _num(v)
            # season은 정수로
            if row.get("season") is not None:
                row["season"] = int(row["season"])
            rows.append(row)
    return rows


def load_market(path: Optional[str]) -> dict:
    """시장 지표 CSV → {round: {...}}. 없거나 비면 빈 dict."""
    if not path:
        return {}
    full = _resolve(path)
    if not os.path.exists(full):
        return {}
    out: dict = {}
    with open(full, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rnd = (r.get("round") or "").strip()
            if rnd == "" or rnd.startswith("#"):
                continue
            out[rnd] = {k: _num(v) for k, v in r.items() if k != "round"}
    return out


def load_benchmarks(path: str = "params/benchmarks.yaml") -> dict:
    full = _resolve(path)
    if not os.path.exists(full):
        return {}
    with open(full, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
