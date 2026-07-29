"""설계 품질 지표 및 추천 3안의 제약 준수 회귀 테스트."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import simulation_config as C  # noqa: E402
from src.cases import recommended_plans  # noqa: E402
from src.data_generator import generate_all  # noqa: E402
from src.quality import (  # noqa: E402
    dead_tier_count,
    design_report,
    effective_rate_profile,
    max_tier_regression,
    rate_dispersion,
    tier_entry_rates,
    tier_regressive_steps,
)
from src.reward_engine import (  # noqa: E402
    CURRENT_STRUCTURE,
    RewardStructure,
    is_strictly_increasing,
    is_valid_structure,
    max_tier_jump,
)
from src.simulation import build_cache, evaluate_structure  # noqa: E402


class TestEngineConstraintHelpers(unittest.TestCase):
    def test_strictly_increasing(self):
        self.assertTrue(is_strictly_increasing([1, 2, 3]))
        self.assertFalse(is_strictly_increasing([1, 2, 2]))   # 평탄구간
        self.assertFalse(is_strictly_increasing([3, 2]))

    def test_max_tier_jump(self):
        self.assertAlmostEqual(max_tier_jump([20_000, 40_000, 60_000]), 2.0)
        self.assertAlmostEqual(max_tier_jump([60_000, 150_000]), 2.5)

    def test_is_valid_structure_options(self):
        flat = RewardStructure(((5_000_000, 20_000), (10_000_000, 20_000)), 1.0, 10_000_000, "flat")
        # 기본(단조 비감소)은 통과, 엄격 증가는 불통과.
        self.assertTrue(is_valid_structure(flat))
        self.assertFalse(is_valid_structure(flat, strict_increase=True))
        cliff = RewardStructure(((5_000_000, 20_000), (10_000_000, 100_000)), 1.0, 10_000_000, "cliff")
        self.assertTrue(is_valid_structure(cliff))
        self.assertFalse(is_valid_structure(cliff, max_jump=2.2))


class TestRateProfile(unittest.TestCase):
    def test_rate_computation(self):
        # 1천만 구간에 리워드 4만 -> 평균 1,500만이면 율 0.2667%
        s = RewardStructure(((5_000_000, 40_000),), 1.0, 10_000_000, "s")
        p = effective_rate_profile(s, [15_000_000] * 10, edges=[10_000_000])
        self.assertAlmostEqual(p.rate_pct[0], 40_000 / 15_000_000 * 100, places=6)

    def test_dispersion_keys(self):
        d = rate_dispersion(CURRENT_STRUCTURE, [10_000_000, 50_000_000, 120_000_000])
        for k in ["mean_pct", "sd", "min_pct", "max_pct", "tier_regressive"]:
            self.assertIn(k, d)

    def test_tier_entry_rates_multiplier_aware(self):
        # 배수 1.5: 인정 3천만 티어는 실제 순입금 2천만에서 진입.
        rates = tier_entry_rates(CURRENT_STRUCTURE)
        self.assertAlmostEqual(rates[2], 60_000 / 20_000_000 * 100, places=6)

    def test_regression_metrics_consistent(self):
        # 역진 폭이 0이면 역진 지점도 0이어야 한다.
        s = RewardStructure(((5_000_000, 20_000), (10_000_000, 40_000)), 1.0, 10_000_000, "s")
        if max_tier_regression(s) == 0.0:
            self.assertEqual(tier_regressive_steps(s), 0)


class TestDeadTier(unittest.TestCase):
    def test_current_top_tier_alive_via_multiplier(self):
        # 최대 순입금 2.39억 * 1.5 = 3.59억 >= 3억 -> 3억 티어 생존.
        transfers = [239_150_000]
        self.assertEqual(dead_tier_count(CURRENT_STRUCTURE, transfers), 0)

    def test_dead_when_multiplier_removed(self):
        no_mult = RewardStructure(CURRENT_STRUCTURE.tiers, 1.0, 10_000_000, "nm")
        # 배수 없으면 2.39억은 2억 티어까지만 도달 -> 3억 티어 사문화(1개).
        self.assertEqual(dead_tier_count(no_mult, [239_150_000]), 1)


class TestRecommendedPlans(unittest.TestCase):
    """추천 3안이 모든 설계 제약을 만족하는지 (회귀 방지)."""

    @classmethod
    def setUpClass(cls):
        cls.datasets = generate_all()
        cls.caches = [build_cache(d) for d in cls.datasets]
        cls.transfers = [t for d in cls.datasets for t in d.transfers]
        cls.plans = recommended_plans()

    def test_three_plans(self):
        self.assertEqual(len(self.plans), 3)

    def test_unit_and_strict_increase(self):
        for p in self.plans:
            self.assertTrue(is_valid_structure(p, strict_increase=True),
                            f"{p.name} 단위/엄격증가 위반: {p.rewards()}")

    def test_cliff_within_limit(self):
        for p in self.plans:
            self.assertLessEqual(max_tier_jump(p.rewards()), C.MAX_TIER_JUMP + 1e-9,
                                 f"{p.name} 절벽 초과")

    def test_rate_sd_within_limit(self):
        for p in self.plans:
            sd = design_report(p, self.transfers)["sd"]
            self.assertLessEqual(sd, C.MAX_RATE_SD + 1e-9, f"{p.name} 유효율 SD {sd:.4f} 초과")

    def test_better_than_current_on_quality(self):
        """3안 모두 현행보다 유효율 일관성과 절벽이 개선되어야 한다."""
        q0 = design_report(CURRENT_STRUCTURE, self.transfers)
        for p in self.plans:
            q = design_report(p, self.transfers)
            self.assertLess(q["sd"], q0["sd"], f"{p.name} 유효율 SD 미개선")
            self.assertLess(q["max_jump"], q0["max_jump"], f"{p.name} 절벽 미개선")

    def test_no_dead_tiers(self):
        for p in self.plans:
            self.assertEqual(dead_tier_count(p, self.transfers), 0, f"{p.name} 사문화 티어 존재")

    def test_budget_savings_and_ordering(self):
        base = evaluate_structure(self.caches, CURRENT_STRUCTURE)
        ms = [evaluate_structure(self.caches, p) for p in self.plans]
        # 모든 안이 현행보다 저예산.
        for p, m in zip(self.plans, ms):
            self.assertLess(m.budget_mean, base.budget_mean, f"{p.name} 예산 미절감")
        # 안1 > 안2 > 안3 순으로 예산·매력도가 낮아져야 한다(포지션 정합).
        self.assertGreater(ms[0].budget_mean, ms[1].budget_mean)
        self.assertGreater(ms[1].budget_mean, ms[2].budget_mean)
        self.assertGreater(ms[0].attractiveness_index, ms[1].attractiveness_index)
        self.assertGreater(ms[1].attractiveness_index, ms[2].attractiveness_index)


class TestBonusAssumptionSensitivity(unittest.TestCase):
    """G6.1 회귀 고정: 배수 참여보너스 가정이 결론을 뒤집는다는 사실."""

    def test_ranking_flips_without_bonus(self):
        a = RewardStructure(
            tuple(zip([t for t, _ in C.CURRENT_TIERS],
                      (10_000, 20_000, 50_000, 150_000, 250_000, 450_000, 750_000))),
            1.5, C.CURRENT_MULTIPLIER_THRESHOLD, "A")
        plan1 = recommended_plans()[0]

        def attract():
            caches = [build_cache(d) for d in generate_all()]
            return (evaluate_structure(caches, a).attractiveness_index,
                    evaluate_structure(caches, plan1).attractiveness_index)

        a_on, p_on = attract()
        original = dict(C.MULT_BONUS_ANCHOR)
        C.MULT_BONUS_ANCHOR.update({k: 0.0 for k in C.MULT_BONUS_ANCHOR})
        try:
            a_off, p_off = attract()
        finally:
            C.MULT_BONUS_ANCHOR.clear()
            C.MULT_BONUS_ANCHOR.update(original)

        # 보너스 있으면 A안이 앞서고, 없으면 안1이 앞선다.
        self.assertGreater(a_on, p_on, "기본 가정에서는 A안 매력도가 높아야 함")
        self.assertGreater(p_off, a_off, "보너스 제거 시 안1이 A안을 추월해야 함")


if __name__ == "__main__":
    unittest.main()
