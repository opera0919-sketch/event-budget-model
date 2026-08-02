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
        datasets = generate_all()
        cls.caches = [build_cache(d) for d in datasets]
        cls.transfers = [t for d in datasets for t in d.transfers]
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

    def test_C_near_target_or_closest_feasible(self):
        """목표예산 밴드 안이거나, 하한 제약 하에서 가장 가까운 선택이어야 한다.

        '현행 대비 리워드 하한'이 하드 제약으로 들어간 뒤에는 목표예산(현행 75%)이
        달성 불가능할 수 있다. 그 경우 밴드를 못 맞추는 것이 정상이며, 대신
        제약을 지키면서 목표에 가장 가까운 안이 선정돼야 한다.
        """
        from src.quality import min_ratio_vs_current

        res = self.out.objectives["C_target_budget"]
        m = res.metrics
        target = self.out.baseline.budget_mean * C.OBJ_C_BUDGET_TARGET
        tol = self.out.baseline.budget_mean * C.OBJ_C_BUDGET_TOL
        if abs(m.budget_mean - target) <= tol + 1:
            return
        # 밴드 밖이면 하한을 지키는 후보 중 목표에 가장 가까워야 한다.
        feasible = [(s, mm) for s, mm in self.out.all_evaluated
                    if min_ratio_vs_current(s, self.transfers) >= C.MIN_RATIO_VS_CURRENT - 1e-9]
        self.assertTrue(feasible, "하한을 지키는 후보가 없다")
        best = min(feasible, key=lambda sm: abs(sm[1].budget_mean - target))
        self.assertAlmostEqual(m.budget_mean, best[1].budget_mean, delta=1)

    def test_selected_plans_respect_ratio_floor(self):
        """목표별 선정안은 현행 대비 하한(사용자 확정 제약)을 지켜야 한다."""
        from src.quality import min_ratio_vs_current

        for key in ["A_min_budget", "B_max_efficiency", "C_target_budget"]:
            s = self.out.objectives[key].structure
            worst = min_ratio_vs_current(s, self.transfers)
            self.assertGreaterEqual(worst, C.MIN_RATIO_VS_CURRENT - 1e-9,
                                    f"{key} 현행 대비 {worst*100:.0f}%")

    def test_gate_violations_recorded(self):
        """게이트 위반은 조용히 넘어가지 않고 기록돼야 한다."""
        for key in ["A_min_budget", "B_max_efficiency", "C_target_budget"]:
            res = self.out.objectives[key]
            # passes_gate 와 violations 목록이 서로 일관돼야 한다.
            self.assertEqual(res.passes_gate, not res.gate_violations)

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
