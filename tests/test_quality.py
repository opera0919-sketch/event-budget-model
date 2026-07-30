"""설계 품질 지표 및 추천 3안의 제약 준수 회귀 테스트."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import simulation_config as C  # noqa: E402
from src.cases import multiplier_keep_plan, recommended_plans  # noqa: E402
from src.data_generator import generate_all  # noqa: E402
from src.quality import (  # noqa: E402
    dead_tier_count,
    design_report,
    effective_rate_profile,
    max_tier_regression,
    min_ratio_vs_current,
    rate_dispersion,
    ratio_vs_current,
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


class TestRatioVsCurrent(unittest.TestCase):
    def test_identity_is_one(self):
        # 현행을 현행과 비교하면 모든 급간이 100%.
        transfers = [15_000_000, 60_000_000, 120_000_000]
        for _, r, _ in ratio_vs_current(CURRENT_STRUCTURE, transfers):
            self.assertAlmostEqual(r, 1.0, places=9)

    def test_skips_unqualified_brackets(self):
        # 현행 리워드가 0인 급간(5백만 미만)은 비율 계산에서 제외.
        rows = ratio_vs_current(CURRENT_STRUCTURE, [3_000_000] * 5)
        self.assertEqual(rows, [])

    def test_detects_drop(self):
        half = RewardStructure(((5_000_000, 20_000),), 1.0, 10_000_000, "half")
        # 1,500만 고객: 현행 4만(배수로 인정 2,250만) vs 2만 -> 50%
        worst = min_ratio_vs_current(half, [15_000_000] * 10)
        self.assertAlmostEqual(worst, 0.5, places=6)


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

    def test_cliff_improves_vs_current(self):
        """3안 모두 현행보다 경계 절벽이 완화되어야 한다."""
        q0 = design_report(CURRENT_STRUCTURE, self.transfers)
        for p in self.plans:
            q = design_report(p, self.transfers)
            self.assertLess(q["max_jump"], q0["max_jump"], f"{p.name} 절벽 미개선")

    def test_sd_not_materially_worse_than_current(self):
        """유효율 SD는 현행과 동등 수준이어야 한다(개선은 보장되지 않음).

        고객별 가중으로 편향을 제거한 뒤에는 현행 SD가 0.044로 낮아져,
        상위 티어를 방어하는 신규안이 현행보다 SD가 낮다고 단정할 수 없다.
        상한(MAX_RATE_SD)만 지키고, 현행 대비 크게 나빠지지 않는지만 본다.
        """
        q0 = design_report(CURRENT_STRUCTURE, self.transfers)
        for p in self.plans:
            q = design_report(p, self.transfers)
            self.assertLessEqual(q["sd"], C.MAX_RATE_SD + 1e-9, f"{p.name} SD 상한 초과")
            self.assertLess(q["sd"], q0["sd"] * 1.15,
                            f"{p.name} SD {q['sd']:.3f}가 현행 {q0['sd']:.3f} 대비 과도 악화")

    def test_no_excessive_reward_drop(self):
        """매력도 방어: 어떤 급간도 현행 대비 하한 미만으로 떨어지지 않아야 한다."""
        for p in self.plans:
            worst = min_ratio_vs_current(p, self.transfers)
            self.assertGreaterEqual(
                worst, C.MIN_RATIO_VS_CURRENT - 1e-9,
                f"{p.name} 현행 대비 {worst*100:.0f}%로 과도 하락")

    def test_top_tiers_protected(self):
        """최상위 티어도 현행 대비 하한을 지켜야 한다(최고액 고객 이탈 방지).

        절대 금액이 아니라 '현행 대비 비율'로 검증한다. 목표 보장률이 바뀌면
        절대 금액은 따라 바뀌지만 비율 하한은 유지돼야 한다.
        """
        from src.reward_engine import reward_for

        for p in self.plans:
            for amount in (150_000_000, 200_000_000, 230_000_000):
                cur = reward_for(amount, CURRENT_STRUCTURE)
                if cur <= 0:
                    continue
                ratio = reward_for(amount, p) / cur
                self.assertGreaterEqual(
                    ratio, C.MIN_RATIO_VS_CURRENT - 1e-9,
                    f"{p.name} {amount/1e8:.1f}억 고객 현행의 {ratio*100:.0f}%")

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


class TestBoundaryAlignment(unittest.TestCase):
    """경계 불일치 회귀 방지.

    현행은 배수(1.5배)로 실제 순입금 6,667만원에서 30만원으로 점프한다.
    신규안 경계를 7,000만원에 두면 그 사이 고객이 현행의 50%만 받는다.
    """

    @classmethod
    def setUpClass(cls):
        cls.transfers = [t for d in generate_all() for t in d.transfers]

    def test_boundary_below_current_effective_threshold(self):
        # 현행 인정 1억 = 실제 6,667만. 경계는 그보다 낮아야 한다.
        effective = 100_000_000 / C.CURRENT_MULTIPLIER
        boundaries = C.DIRECT_BRACKETS
        self.assertTrue(any(b <= effective for b in boundaries if b > 50_000_000),
                        "6,667만 이하 경계가 없어 하락 구간이 생긴다")

    def test_no_fifty_percent_drop_at_67m(self):
        """6,600만~7,000만 구간이 게이트 하한을 지켜야 한다.

        경계가 7,000만이던 시절 이 구간은 현행의 50%였다(30만 -> 15만).
        경계를 6,600만으로 내린 뒤 67%로 회복했다. 50%로 되돌아가면 실패한다.
        """
        from src.reward_engine import reward_for

        edges = [66_000_000, 67_000_000, 68_000_000, 69_000_000]
        for p in recommended_plans():
            for lo in edges:
                group = [t for t in self.transfers if lo <= t < lo + 1_000_000]
                if not group:
                    continue
                avg = sum(group) / len(group)
                cur = reward_for(avg, CURRENT_STRUCTURE)
                if cur <= 0:
                    continue
                ratio = reward_for(avg, p) / cur
                self.assertGreaterEqual(
                    ratio, C.MIN_RATIO_VS_CURRENT,
                    f"{p.name} {lo/1e4:,.0f}만 구간 현행의 {ratio*100:.0f}%")

    def test_precise_grid_is_default(self):
        """기본 격자가 100만원 단위여야 한다(1천만원 격자는 하락을 가린다)."""
        self.assertEqual(C.RATIO_CHECK_UNIT, 1_000_000)
        for p in recommended_plans():
            coarse = min_ratio_vs_current(p, self.transfers,
                                          [b * 10_000_000 for b in range(24)])
            precise = min_ratio_vs_current(p, self.transfers)
            self.assertLessEqual(precise, coarse + 1e-9,
                                 "정밀 격자가 더 관대하게 나오면 검증 의미가 없다")


class TestMultiplierKeepPlan(unittest.TestCase):
    """배수 유지안(A안')도 제약을 만족해야 한다."""

    @classmethod
    def setUpClass(cls):
        cls.transfers = [t for d in generate_all() for t in d.transfers]
        cls.plan = multiplier_keep_plan()

    def test_unit_and_monotonic(self):
        self.assertTrue(is_valid_structure(self.plan, strict_increase=True))

    def test_keeps_multiplier(self):
        self.assertEqual(self.plan.multiplier, C.CURRENT_MULTIPLIER)

    def test_ratio_floor(self):
        worst = min_ratio_vs_current(self.plan, self.transfers)
        self.assertGreaterEqual(worst, C.MIN_RATIO_VS_CURRENT - 1e-9,
                                f"A안' 현행 대비 {worst*100:.0f}%")

    def test_all_candidates_meet_floor(self):
        from src.cases import all_candidate_plans
        for p in all_candidate_plans():
            worst = min_ratio_vs_current(p, self.transfers)
            self.assertGreaterEqual(worst, C.MIN_RATIO_VS_CURRENT - 1e-9,
                                    f"{p.name} 현행 대비 {worst*100:.0f}%")


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
