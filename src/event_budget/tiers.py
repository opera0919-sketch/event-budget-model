"""타사이전금액 구간별 리워드 — 구간 환산·제세 gross-up·당첨자 금액분포.

현재안 리워드 구조는 두 규칙이 겹쳐 있다(params/reward_tiers.yaml 참조).
  규칙1(실적 인정): 이전금액 >= credit_threshold 면 인정실적 = 이전금액 × credit_multiplier.
  규칙2(제세금):    리워드 >= tax_threshold 면 예산반영액 = 리워드/(1-tax_rate).

구간 경계는 '인정실적' 기준이므로, 당첨자 금액분포에 적분하려면 '실제 이전금액' 축으로
되돌려야 한다(credited_to_actual). 분포는 로그정규로 두고 실적 2개 지표로 역산한다.

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
