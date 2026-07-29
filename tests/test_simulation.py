import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data_generator import generate_all  # noqa: E402
from src.reward_engine import CURRENT_STRUCTURE, budget_amount, reward_for  # noqa: E402
from src.simulation import build_cache, evaluate_round, evaluate_structure  # noqa: E402


class TestSimulation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.datasets = generate_all()
        cls.caches = [build_cache(d) for d in cls.datasets]

    def test_baseline_attractiveness_unity(self):
        # 현행을 파이프라인에 통과시키면 가중=1 -> 매력도 1.0.
        agg = evaluate_structure(self.caches, CURRENT_STRUCTURE)
        self.assertAlmostEqual(agg.attractiveness_index, 1.0, places=6)

    def test_budget_equals_sum_of_grossup(self):
        # 현행(가중=1) 라운드 예산 = Σ budget_amount(reward).
        ds = self.datasets[0]
        cache = self.caches[0]
        m = evaluate_round(cache, CURRENT_STRUCTURE)
        expected = sum(budget_amount(reward_for(t, CURRENT_STRUCTURE)) for t in ds.transfers)
        self.assertAlmostEqual(m.total_budget, expected, delta=expected * 1e-9 + 1)

    def test_worst_case_is_max(self):
        agg = evaluate_structure(self.caches, CURRENT_STRUCTURE)
        rounds = [evaluate_round(c, CURRENT_STRUCTURE) for c in self.caches]
        self.assertAlmostEqual(agg.budget_worst, max(r.total_budget for r in rounds), places=3)
        self.assertAlmostEqual(agg.budget_best, min(r.total_budget for r in rounds), places=3)

    def test_kpi_relationships(self):
        agg = evaluate_structure(self.caches, CURRENT_STRUCTURE)
        self.assertAlmostEqual(agg.efficiency, agg.transfer_mean / agg.budget_mean, places=3)
        self.assertAlmostEqual(agg.cost_rate_pct, agg.budget_mean / agg.transfer_mean * 100, places=3)
        self.assertAlmostEqual(agg.cpa, agg.budget_mean / agg.recipients_mean, delta=1)

    def test_baseline_budget_magnitude(self):
        # 현행 1인당 예산 ~ 17~19만원 범위(제세 포함).
        agg = evaluate_structure(self.caches, CURRENT_STRUCTURE)
        per_head = agg.budget_mean / agg.applicants_mean
        self.assertTrue(160_000 < per_head < 200_000)


if __name__ == "__main__":
    unittest.main()
