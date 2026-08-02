import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import simulation_config as C  # noqa: E402
from src import demand_model as dm  # noqa: E402


class TestParticipationScaling(unittest.TestCase):
    def test_baseline_unity(self):
        # r=1 (현행 동일)에서 정확히 1.0.
        self.assertAlmostEqual(dm.participation_scaling(1.0), 1.0, places=9)

    def test_monotonic(self):
        rs = [0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.5]
        vals = [dm.participation_scaling(r) for r in rs]
        for i in range(len(vals) - 1):
            self.assertLessEqual(vals[i], vals[i + 1] + 1e-9)

    def test_sticky_near_baseline(self):
        # 소폭 하향(r=0.9)에서는 참여 손실이 작아야 함(sticky).
        self.assertGreater(dm.participation_scaling(0.9), 0.95)

    def test_steep_drop_large_cut(self):
        # 대폭 하향(r=0.3)에서는 참여 급감.
        self.assertLess(dm.participation_scaling(0.3), 0.75)

    def test_steep_variant_more_reactive(self):
        # steep 곡선이 base보다 하향에 더 민감.
        base = dm.participation_scaling(0.5, C.ELASTICITY["base"])
        steep = dm.participation_scaling(0.5, C.ELASTICITY["steep"])
        self.assertLess(steep, base)


class TestRewardRatio(unittest.TestCase):
    def test_zero_boundaries(self):
        self.assertEqual(dm.reward_ratio(0, 0), 1.0)
        self.assertEqual(dm.reward_ratio(20_000, 0), 0.0)
        self.assertGreater(dm.reward_ratio(0, 20_000), 1.0)
        self.assertAlmostEqual(dm.reward_ratio(40_000, 20_000), 0.5)


class TestMultiplierBonus(unittest.TestCase):
    def test_anchors(self):
        self.assertAlmostEqual(dm.multiplier_bonus(1.5), 0.03, places=6)
        self.assertAlmostEqual(dm.multiplier_bonus(2.0), 0.05, places=6)
        self.assertEqual(dm.multiplier_bonus(1.0), 0.0)

    def test_interpolation(self):
        # 1.75배 -> 3%와 5% 사이.
        b = dm.multiplier_bonus(1.75)
        self.assertTrue(0.03 < b < 0.05)


if __name__ == "__main__":
    unittest.main()
