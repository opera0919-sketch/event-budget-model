import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.reward_engine import (  # noqa: E402
    CURRENT_STRUCTURE,
    RewardStructure,
    allowed_reward_levels,
    budget_amount,
    enforce_monotonic,
    is_valid_reward_unit,
    is_valid_structure,
    recognized_amount,
    reward_for,
    snap_reward,
    step_reward,
)


class TestRewardBoundaries(unittest.TestCase):
    def test_below_min_qualify_zero(self):
        self.assertEqual(reward_for(4_990_000, CURRENT_STRUCTURE), 0)

    def test_min_qualify_first_tier(self):
        self.assertEqual(reward_for(5_000_000, CURRENT_STRUCTURE), 20_000)
        self.assertEqual(reward_for(9_990_000, CURRENT_STRUCTURE), 20_000)

    def test_multiplier_mapping(self):
        # 배수 미적용 구간 (10M 미만): 인정=전환금액.
        self.assertEqual(recognized_amount(8_000_000, CURRENT_STRUCTURE), 8_000_000)
        # 10M 전환 -> 인정 15M -> 10~30M 티어(4만).
        self.assertEqual(recognized_amount(10_000_000, CURRENT_STRUCTURE), 15_000_000)
        self.assertEqual(reward_for(10_000_000, CURRENT_STRUCTURE), 40_000)
        # 20M 전환 -> 인정 30M -> 30~50M 티어(6만).
        self.assertEqual(reward_for(20_000_000, CURRENT_STRUCTURE), 60_000)
        # 200M 전환 -> 인정 300M -> 3억이상(100만).
        self.assertEqual(reward_for(200_000_000, CURRENT_STRUCTURE), 1_000_000)

    def test_reward_monotonic_in_transfer(self):
        prev = -1
        for t in range(0, 350_000_000, 5_000_000):
            r = reward_for(t, CURRENT_STRUCTURE)
            self.assertGreaterEqual(r, prev if t >= 10_000_000 else 0)
            prev = r


class TestBudgetGrossup(unittest.TestCase):
    def test_below_threshold_no_tax(self):
        self.assertEqual(budget_amount(20_000), 20_000)
        self.assertEqual(budget_amount(40_000), 40_000)

    def test_at_and_above_threshold(self):
        # 6만원 -> (60000/0.78)*0.22 + 60000 = 76,923.08...
        self.assertAlmostEqual(budget_amount(60_000), (60_000 / 0.78) * 0.22 + 60_000, places=4)
        self.assertAlmostEqual(budget_amount(60_000), 60_000 / 0.78, places=4)
        # 경계 5만원은 그로스업 대상(>=).
        self.assertAlmostEqual(budget_amount(50_000), 50_000 / 0.78, places=4)


class TestUnitAndMonotonic(unittest.TestCase):
    def test_unit_validity(self):
        self.assertTrue(is_valid_reward_unit(10_000))
        self.assertTrue(is_valid_reward_unit(50_000))
        self.assertTrue(is_valid_reward_unit(150_000))
        self.assertFalse(is_valid_reward_unit(60_000))   # >5만 이면서 5만 단위 아님
        self.assertFalse(is_valid_reward_unit(15_000))   # <=5만 이면서 1만 단위 아님
        self.assertTrue(is_valid_reward_unit(0))

    def test_snap(self):
        self.assertEqual(snap_reward(25_000), 20_000)
        self.assertEqual(snap_reward(60_000), 50_000)
        self.assertEqual(snap_reward(120_000), 100_000)
        self.assertTrue(is_valid_reward_unit(snap_reward(137_000)))

    def test_step_reward(self):
        levels = allowed_reward_levels(1_000_000)
        self.assertEqual(step_reward(50_000, +1, 1_000_000), 100_000)
        self.assertEqual(step_reward(100_000, -1, 1_000_000), 50_000)
        self.assertIn(step_reward(20_000, +1, 1_000_000), levels)

    def test_enforce_monotonic(self):
        self.assertEqual(enforce_monotonic((10, 5, 8, 3)), (10, 10, 10, 10))
        self.assertEqual(enforce_monotonic((1, 2, 3)), (1, 2, 3))

    def test_valid_structure(self):
        good = RewardStructure(((5_000_000, 20_000), (10_000_000, 50_000)), 1.5, 10_000_000, "g")
        bad_unit = RewardStructure(((5_000_000, 20_000), (10_000_000, 60_000)), 1.5, 10_000_000, "b")
        bad_mono = RewardStructure(((5_000_000, 50_000), (10_000_000, 20_000)), 1.5, 10_000_000, "b")
        self.assertTrue(is_valid_structure(good))
        self.assertFalse(is_valid_structure(bad_unit))
        self.assertFalse(is_valid_structure(bad_mono))


if __name__ == "__main__":
    unittest.main()
