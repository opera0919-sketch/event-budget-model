import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import simulation_config as C  # noqa: E402
from src.data_generator import generate_all  # noqa: E402
from src.optimizer import coarse_candidates, fine_neighbors, optimize, pareto_front  # noqa: E402
from src.reward_engine import is_valid_structure  # noqa: E402
from src.simulation import build_cache  # noqa: E402


class TestCandidates(unittest.TestCase):
    def test_coarse_all_valid(self):
        for s in coarse_candidates():
            self.assertTrue(is_valid_structure(s), f"invalid: {s.rewards()}")

    def test_fine_all_valid(self):
        seed = coarse_candidates()[0]
        for nb in fine_neighbors(seed):
            self.assertTrue(is_valid_structure(nb))


class TestOptimize(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.caches = [build_cache(d) for d in generate_all()]
        cls.out = optimize(cls.caches, top_k=10)

    def test_objectives_present(self):
        for key in ["A_min_budget", "B_max_efficiency", "C_target_budget"]:
            self.assertIn(key, self.out.objectives)

    def test_A_respects_attractiveness_floor(self):
        m = self.out.objectives["A_min_budget"].metrics
        self.assertGreaterEqual(m.attractiveness_index, C.OBJ_A_ATTRACT_FLOOR - 1e-6)

    def test_B_within_budget_cap(self):
        m = self.out.objectives["B_max_efficiency"].metrics
        cap = self.out.baseline.budget_mean * C.OBJ_B_BUDGET_CAP
        self.assertLessEqual(m.budget_mean, cap + 1)

    def test_C_near_target(self):
        m = self.out.objectives["C_target_budget"].metrics
        target = self.out.baseline.budget_mean * C.OBJ_C_BUDGET_TARGET
        tol = self.out.baseline.budget_mean * C.OBJ_C_BUDGET_TOL
        self.assertLessEqual(abs(m.budget_mean - target), tol + 1)

    def test_all_recommendations_valid(self):
        for key in ["A_min_budget", "B_max_efficiency", "C_target_budget"]:
            self.assertTrue(is_valid_structure(self.out.objectives[key].structure))

    def test_pareto_non_dominated(self):
        pf = self.out.pareto
        for s1, m1 in pf:
            for s2, m2 in pf:
                # 어떤 점도 다른 점에 (예산↓ & 유치↑)로 완전 지배되지 않아야 함.
                dominated = (m2.budget_mean <= m1.budget_mean - 1 and
                             m2.transfer_mean >= m1.transfer_mean + 1)
                self.assertFalse(dominated)

    def test_reproducible(self):
        out2 = optimize(self.caches, top_k=10)
        self.assertAlmostEqual(self.out.objectives["A_min_budget"].metrics.budget_mean,
                               out2.objectives["A_min_budget"].metrics.budget_mean, places=3)


if __name__ == "__main__":
    unittest.main()
