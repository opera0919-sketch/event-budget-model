"""타사이전금액 구간별 리워드 — 구간 환산·제세 gross-up·당첨자 금액분포.

현재안 리워드 구조는 두 규칙이 겹쳐 있다(params/reward_tiers.yaml 참조).
  규칙1(실적 인정): 이전금액 >= credit_threshold 면 인정실적 = 이전금액 × credit_multiplier.
  규칙2(제세금):    리워드 >= tax_threshold 면 예산반영액 = 리워드/(1-tax_rate).

구간 경계는 '인정실적' 기준이므로, 금액분포에 적분하려면 '실제 수관금액' 축으로
되돌려야 한다(credited_to_actual).

금액 분포는 실측을 쓴다 — data/transfer_amount_distribution.csv 의 회차별 구간 고객 수를
고객 수로 가중해 통합한다(EmpiricalTierModel). 로그정규 가정판(TierModel)은 실측 데이터를
확보하기 전 쓰던 것으로, 지금은 대조군으로만 남겨 둔다.

의존성은 numpy + 표준 statistics 만 사용한다(scipy 미도입).
"""
from __future__ import annotations

from dataclasses import dataclass
from math import exp, inf, log
from statistics import NormalDist

import yaml

from .schema import _resolve

_N = NormalDist()


def gross_cost(reward: float, tax_threshold: float, tax_rate: float) -> float:
    """예산반영액 — 리워드가 임계 이상이면 제세 gross-up(리워드/(1-세율))."""
    if reward < tax_threshold:
        return float(reward)
    return reward / (1.0 - tax_rate)


def credited_to_actual(credited: float, credit_threshold: float,
                       credit_multiplier: float) -> float:
    """'인정실적' 경계값 → 대응하는 '실제 이전금액' 경계값.

    실제금액 x 는 x < threshold 면 인정 c=x, x >= threshold 면 c=x×m 이다.
    따라서 c ∈ [threshold, threshold×m) 은 어떤 x 로도 도달할 수 없는 공백 구간이며,
    이때는 배수가 처음 적용되는 지점(threshold)으로 클램프한다.
    """
    if credited == inf:
        return inf
    if credited < credit_threshold:
        return float(credited)
    if credited < credit_threshold * credit_multiplier:
        return float(credit_threshold)
    return credited / credit_multiplier


def load_tier_table(key: str, path: str = "params/reward_tiers.yaml") -> dict:
    """구간 리워드 테이블 1건 로드. key 는 YAML 최상위 키."""
    with open(_resolve(path), "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if key not in raw:
        raise ValueError(f"reward tier key '{key}' 없음 ({path}). 사용 가능: {list(raw)}")
    return raw[key]


@dataclass
class TierModel:
    """구간표 + 당첨자 이전금액 로그정규 분포(mu·sigma)를 묶은 계산 모델."""
    label: str
    labels: list[str]            # 구간 표시명
    edges: list[float]           # 실제 이전금액 경계(하한 n개 + inf), 길이 = n+1
    credited_edges: list[float]  # 인정실적 경계(참고·표기용), 길이 = n+1
    rewards: list[float]         # 구간별 리워드(원)
    gross: list[float]           # 구간별 예산반영액(제세 포함, 원)
    mu: float                    # 로그정규 mu (원 단위 로그)
    sigma: float

    @property
    def floor(self) -> float:
        """조건충족 최저 금액 = 최저 구간의 실제 하한."""
        return self.edges[0]

    def shares(self, shift: float = 0.0) -> list[float]:
        """구간별 당첨자 비율(합=1). shift = 이전금액 중앙값 상향률(0.25 → +25%)."""
        mu = self.mu + log(1.0 + shift)

        def cdf(x: float) -> float:
            return 1.0 if x == inf else _N.cdf((log(x) - mu) / self.sigma)

        tail = 1.0 - cdf(self.floor)
        if tail <= 0:
            raise ValueError("절단 확률이 0 — sigma/mu 설정을 확인하라")
        return [(cdf(self.edges[i + 1]) - cdf(self.edges[i])) / tail
                for i in range(len(self.rewards))]

    def avg_budget_cost(self, shift: float = 0.0) -> float:
        """당첨 1인당 평균 예산반영액(원)."""
        return sum(s * g for s, g in zip(self.shares(shift), self.gross))

    def avg_reward(self, shift: float = 0.0) -> float:
        """당첨 1인당 평균 리워드(제세 제외, 원)."""
        return sum(s * r for s, r in zip(self.shares(shift), self.rewards))

    def payout_tiers(self, shift: float = 0.0) -> list[dict]:
        """budget.compute_payout_tiered 입력 형식.

        reward 에 제세 포함 예산반영액을 넣으므로 호출부는 tax_rate=0 으로 쓴다
        (제세가 구간별 조건부라 일괄 세율로는 표현되지 않기 때문).
        """
        return [{"share": s, "reward": g}
                for s, g in zip(self.shares(shift), self.gross)]

    def mean_deposit(self, shift: float = 0.0) -> float:
        """당첨자 평균 이전금액(원) — 절단 로그정규의 조건부 기대값. 참고 지표."""
        mu = self.mu + log(1.0 + shift)
        s = self.sigma
        z = (log(self.floor) - mu) / s
        num = exp(mu + s * s / 2) * (1.0 - _N.cdf(z - s))
        return num / (1.0 - _N.cdf(z))


def build_tier_model(cfg: dict) -> TierModel:
    """구간표 dict → TierModel. 로그정규 mu·sigma 는 실적 앵커 2개로 역산한다.

    (a) P(X >= floor) = anchor_condition_rate  → mu = ln(floor) - z·sigma  (z = Φ⁻¹(1-p))
    (b) 평균 예산반영 단가 = anchor_avg_budget_cost → 남은 자유도 sigma 를 이분탐색

    평균 단가는 sigma 에 대해 단조 증가(꼬리가 두꺼울수록 상위 구간 비중↑)라 이분탐색이 유효.
    """
    tiers = cfg["tiers"]
    ct = float(cfg["credit_threshold"])
    cm = float(cfg["credit_multiplier"])
    tax_th = float(cfg["tax_threshold"])
    tax_rate = float(cfg["tax_rate"])

    labels = [t["label"] for t in tiers]
    rewards = [float(t["reward"]) for t in tiers]
    gross = [gross_cost(r, tax_th, tax_rate) for r in rewards]

    credited_edges = [float(t["lower"]) for t in tiers]
    credited_edges.append(inf if tiers[-1].get("upper") is None
                          else float(tiers[-1]["upper"]))
    edges = [credited_to_actual(c, ct, cm) for c in credited_edges]

    dist = cfg["deposit_dist"]
    p = float(dist["anchor_condition_rate"])
    target = float(dist["anchor_avg_budget_cost"])
    floor = edges[0]
    z = _N.inv_cdf(1.0 - p)

    def avg_at(sigma: float) -> float:
        mu = log(floor) - z * sigma
        m = TierModel(cfg.get("label", ""), labels, edges, credited_edges,
                      rewards, gross, mu, sigma)
        return m.avg_budget_cost()

    lo, hi = 0.05, 5.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if avg_at(mid) < target:
            lo = mid
        else:
            hi = mid
    sigma = (lo + hi) / 2
    mu = log(floor) - z * sigma
    return TierModel(cfg.get("label", ""), labels, edges, credited_edges,
                     rewards, gross, mu, sigma)


def load_tier_model(key: str, path: str = "params/reward_tiers.yaml") -> TierModel:
    return build_tier_model(load_tier_table(key, path))


# ---------------------------------------------------------------------------
# 실측 금액 분포 (data/transfer_amount_distribution.csv)
# ---------------------------------------------------------------------------

# CSV 컬럼 순서 = 아래 구간 순서. 실제 타사수관금액(원) 기준 경계.
DIST_COLUMNS = ["lt_5m", "m5_10", "m10_30", "m30_50", "m50_100",
                "m100_200", "m200_300", "m300_plus"]
DIST_EDGES = [0.0, 5e6, 1e7, 3e7, 5e7, 1e8, 2e8, 3e8, inf]


def load_amount_distribution(path: str = "data/transfer_amount_distribution.csv") -> list[dict]:
    """회차별 타사수관금액 구간별 고객 수 CSV → 행 리스트.

    counts 는 명 수 그대로, shares 는 회차 내 비율(합=1)로 함께 담는다.
    각 행에서 구간 합계가 applicants 와 맞는지 검증한다 — 실측 표의 전사 오류를 잡기 위함.
    """
    import csv

    rows: list[dict] = []
    with open(_resolve(path), "r", encoding="utf-8") as f:
        reader = csv.DictReader(line for line in f if not line.lstrip().startswith("#"))
        for r in reader:
            if not (r.get("event_no") or "").strip():
                continue
            counts = [float(r[c]) for c in DIST_COLUMNS]
            total = sum(counts)
            declared = float(r["applicants"])
            if abs(total - declared) > 0.5:
                raise ValueError(
                    f"회차 {r['event_no']}: 구간 합계 {total:,.0f} != applicants "
                    f"{declared:,.0f} — 실측 표 전사를 확인하라")
            rows.append({
                "event_no": r["event_no"].strip(),
                "start_ym": (r.get("start_ym") or "").strip(),
                "end_ym": (r.get("end_ym") or "").strip(),
                "applicants": declared,
                "counts": counts,
                "shares": [c / total for c in counts],
            })
    return rows


def average_distribution(rows: list[dict], weighted: bool = True) -> list[float]:
    """회차 통합 분포(합=1).

    weighted=True(기본)면 고객 수로 가중 — 회차별 신청 규모가 4천~2만명으로 5배 차이나므로,
    실제 모집단 분포는 가중이 맞다. False 면 회차 단순평균(회차를 동등 취급).
    """
    if not rows:
        raise ValueError("평균을 낼 회차가 없다")
    if weighted:
        tot = [sum(r["counts"][i] for r in rows) for i in range(len(DIST_COLUMNS))]
    else:
        n = len(rows)
        tot = [sum(r["shares"][i] for r in rows) / n for i in range(len(DIST_COLUMNS))]
    s = sum(tot)
    return [t / s for t in tot]


@dataclass
class PiecewiseLogUniform:
    """구간별 질량이 주어진 분포. 구간 내부는 로그축에서 균등하다고 본다.

    실측 데이터는 구간 집계라 구간 안쪽 모양을 모른다. 금액처럼 자릿수로 퍼지는 변수는
    로그축 균등이 선형 균등보다 훨씬 나은 근사이고, 무엇보다 구간 경계에서 관측치를
    정확히 재현한다(구간 합이 실측과 일치).

    1.5배 인정 규칙 때문에 리워드 구간 경계가 실측 구간 안쪽(예: 실제 2,000만원)에
    떨어지므로, 구간을 쪼개 적분할 수단이 반드시 필요하다.
    """
    edges: list[float]        # 길이 n+1. edges[0] 은 0 가능(min_amount 로 치환)
    masses: list[float]       # 길이 n, 합=1
    min_amount: float = 1e5   # 최저 구간 하한 대체값(0은 로그를 못 취함)
    max_amount: float = 1e9   # 최상위 열린 구간 상한 대체값

    def _bounds(self, i: int, shift: float) -> tuple[float, float]:
        k = 1.0 + shift
        lo = self.edges[i] if self.edges[i] > 0 else self.min_amount
        hi = self.edges[i + 1] if self.edges[i + 1] != inf else self.max_amount
        return lo * k, hi * k

    def cdf(self, x: float, shift: float = 0.0) -> float:
        acc = 0.0
        for i, m in enumerate(self.masses):
            lo, hi = self._bounds(i, shift)
            if x >= hi:
                acc += m
            elif x > lo:
                acc += m * log(x / lo) / log(hi / lo)
                break
            else:
                break
        return acc

    def mass_between(self, a: float, b: float, shift: float = 0.0) -> float:
        hi = self.max_amount * (1.0 + shift) if b == inf else b
        return max(self.cdf(hi, shift) - self.cdf(a, shift), 0.0)

    def prob_above(self, x: float, shift: float = 0.0) -> float:
        return max(1.0 - self.cdf(x, shift), 0.0)

    def mean_above(self, x: float, shift: float = 0.0) -> float:
        """E[금액 | 금액 >= x]. 로그균등 구간의 1차 적률은 (hi-lo)/ln(hi/lo)."""
        mass = tot = 0.0
        for i, m in enumerate(self.masses):
            lo, hi = self._bounds(i, shift)
            a = max(lo, x)
            if a >= hi:
                continue
            frac = m / log(hi / lo)
            mass += frac * log(hi / a)
            tot += frac * (hi - a)
        return tot / mass if mass > 0 else 0.0


_EPS = 1e-9


def _tilt(base: list[float], target: list[float], theta: float) -> list[float]:
    """base 에서 target 방향으로 theta 만큼 기울인 분포(로그축 보간, 합=1).

    theta=0 → base, theta=1 → target, theta>1 → 같은 방향으로 외삽.
    구간별 비(target/base)를 지수 가중하므로 항상 양수이고 외삽이 매끄럽다.
    """
    w = [max(b, _EPS) * (max(t, _EPS) / max(b, _EPS)) ** theta
         for b, t in zip(base, target)]
    s = sum(w)
    return [x / s for x in w]


@dataclass
class EmpiricalTierModel:
    """실측 금액 분포 + 구간 리워드표. TierModel 과 같은 인터페이스를 제공한다.

    축2(믹스 변동)는 **관측된 회차 간 변동 방향**을 그대로 기울기로 쓴다(theta).
      theta=0  → 성숙 회차 평균
      theta=1  → 관측 최고 회차(대량입금이 가장 많이 들어온 회차)
      theta=-1 → 관측 최저 회차
      |theta|>1 → 같은 방향 외삽(관측 범위 초과 스트레스)

    금액축을 통째로 미는 방식도 가능하지만(PiecewiseLogUniform.shift), 그러면 결과가
    '5백만원 미만' 구간(전체의 78.8%)의 내부 모양 가정에 크게 휘둘린다. 실측은 회차 간
    변동이 거의 전부 '리워드 대상 비율'에서 나오고 대상자 내부 믹스는 안정적임을 보여주므로,
    관측된 변동 방향을 직접 쓰는 편이 가정을 덜 넣고 더 정확하다.

    리워드 대상 비율(지급률)도 분포에서 나온다 — 대량입금 고객이 늘면 5백만원 문턱을
    넘는 고객이 함께 늘기 때문이며, 이것이 유입 증가의 실제 작동 방식이다.
    """
    label: str
    labels: list[str]
    edges: list[float]           # 리워드 구간의 실제금액 경계(1.5배 환산 완료)
    credited_edges: list[float]
    rewards: list[float]
    gross: list[float]
    dist: PiecewiseLogUniform
    source: str = ""
    n_events: int = 0
    tilt_hi: list[float] | None = None   # 관측 최고 회차 분포
    tilt_lo: list[float] | None = None   # 관측 최저 회차 분포
    tilt_hi_event: str = ""
    tilt_lo_event: str = ""

    @property
    def floor(self) -> float:
        return self.edges[0]

    def _dist_at(self, theta: float) -> PiecewiseLogUniform:
        """theta 만큼 기울인 분포. 기준점(0)에서는 실측 평균 그대로."""
        if theta == 0 or not (self.tilt_hi and self.tilt_lo):
            return self.dist
        target = self.tilt_hi if theta > 0 else self.tilt_lo
        masses = _tilt(self.dist.masses, target, abs(theta))
        return PiecewiseLogUniform(self.dist.edges, masses,
                                   self.dist.min_amount, self.dist.max_amount)

    def payout_rate(self, theta: float = 0.0) -> float:
        """리워드 지급 대상 비율 = P(수관금액 >= 최저 구간 하한). 신청 대비 당첨률."""
        return self._dist_at(theta).prob_above(self.floor)

    def shares(self, theta: float = 0.0) -> list[float]:
        d = self._dist_at(theta)
        tail = d.prob_above(self.floor)
        if tail <= 0:
            raise ValueError("리워드 대상이 0 — 분포/문턱 설정을 확인하라")
        return [d.mass_between(self.edges[i], self.edges[i + 1]) / tail
                for i in range(len(self.rewards))]

    def avg_budget_cost(self, theta: float = 0.0) -> float:
        return sum(s * g for s, g in zip(self.shares(theta), self.gross))

    def avg_reward(self, theta: float = 0.0) -> float:
        return sum(s * r for s, r in zip(self.shares(theta), self.rewards))

    def payout_tiers(self, theta: float = 0.0) -> list[dict]:
        return [{"share": s, "reward": g}
                for s, g in zip(self.shares(theta), self.gross)]

    def mean_deposit(self, theta: float = 0.0) -> float:
        return self._dist_at(theta).mean_above(self.floor)

    def per_applicant(self, theta: float = 0.0) -> float:
        """신청 1인당 예산 = 지급률 × 평균 예산반영 단가. 축2의 요약 지표."""
        return self.payout_rate(theta) * self.avg_budget_cost(theta)


def build_empirical_tier_model(cfg: dict, shares: list[float] | None = None,
                               n_events: int = 0) -> EmpiricalTierModel:
    """구간표 + 실측 금액 분포 → EmpiricalTierModel.

    shares 를 주면 그 분포를, 없으면 cfg['amount_distribution'] 의 CSV 평균을 쓴다.
    """
    tiers_ = cfg["tiers"]
    ct = float(cfg["credit_threshold"])
    cm = float(cfg["credit_multiplier"])
    tax_th = float(cfg["tax_threshold"])
    tax_rate = float(cfg["tax_rate"])

    labels = [t["label"] for t in tiers_]
    rewards = [float(t["reward"]) for t in tiers_]
    gross = [gross_cost(r, tax_th, tax_rate) for r in rewards]

    credited_edges = [float(t["lower"]) for t in tiers_]
    credited_edges.append(inf if tiers_[-1].get("upper") is None
                          else float(tiers_[-1]["upper"]))
    edges = [credited_to_actual(c, ct, cm) for c in credited_edges]

    dcfg = cfg.get("amount_distribution", {}) or {}
    if shares is None:
        rows = load_amount_distribution(dcfg.get("source",
                                                 "data/transfer_amount_distribution.csv"))
        shares = average_distribution(rows, dcfg.get("weighted", True))
        n_events = len(rows)

    dist = PiecewiseLogUniform(
        edges=list(DIST_EDGES),
        masses=list(shares),
        min_amount=float(dcfg.get("min_amount", 1e5)),
        max_amount=float(dcfg.get("max_amount", 1e9)),
    )
    return EmpiricalTierModel(cfg.get("label", ""), labels, edges, credited_edges,
                              rewards, gross, dist,
                              source=dcfg.get("source", ""), n_events=n_events)


def _per_applicant_of(model: EmpiricalTierModel) -> float:
    return model.payout_rate(0.0) * model.avg_budget_cost(0.0)


def attach_observed_range(model: EmpiricalTierModel,
                          events: dict[str, EmpiricalTierModel]) -> EmpiricalTierModel:
    """관측 최고·최저 회차를 기울기 기준점으로 붙인다 — 축2 눈금을 데이터에서 가져온다."""
    if not events:
        return model
    ranked = sorted(events.items(), key=lambda kv: _per_applicant_of(kv[1]))
    lo_name, lo_m = ranked[0]
    hi_name, hi_m = ranked[-1]
    model.tilt_lo = list(lo_m.dist.masses)
    model.tilt_hi = list(hi_m.dist.masses)
    model.tilt_lo_event = lo_name
    model.tilt_hi_event = hi_name
    return model


def load_empirical_tier_model(key: str,
                              path: str = "params/reward_tiers.yaml",
                              with_observed_range: bool = True) -> EmpiricalTierModel:
    model = build_empirical_tier_model(load_tier_table(key, path))
    if with_observed_range:
        attach_observed_range(model, event_tier_models(key, path))
    return model


def event_tier_models(key: str,
                      path: str = "params/reward_tiers.yaml") -> dict[str, EmpiricalTierModel]:
    """회차별 실측 분포로 만든 모델들 — 관측된 믹스 변동 폭을 그대로 쓰기 위한 것."""
    cfg = load_tier_table(key, path)
    dcfg = cfg.get("amount_distribution", {}) or {}
    rows = load_amount_distribution(dcfg.get("source",
                                             "data/transfer_amount_distribution.csv"))
    return {r["event_no"]: build_empirical_tier_model(cfg, shares=r["shares"], n_events=1)
            for r in rows}
