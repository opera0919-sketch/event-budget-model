import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import simulation_config as C  # noqa: E402
from src import distribution as dist  # noqa: E402
from src.data_generator import generate_all, scenario_for_round  # noqa: E402


class TestOscillation(unittest.TestCase):
    def test_sums_to_one(self):
        rng = random.Random(1)
        for _ in range(20):
            probs = dist.oscillate_distribution(rng, upper_tilt=0.0)
            self.assertAlmostEqual(sum(probs), 1.0, places=9)

    def test_zero_stays_zero(self):
        rng = random.Random(2)
        probs = dist.oscillate_distribution(rng)
        for b, p in enumerate(C.BASE_DISTRIBUTION):
            if p == 0.0:
                self.assertEqual(probs[b], 0.0)

    def test_within_noise_band(self):
        rng = random.Random(3)
        probs = dist.oscillate_distribution(rng, upper_tilt=0.0)
        total_base = sum(C.BASE_DISTRIBUTION)
        for b, p in enumerate(C.BASE_DISTRIBUTION):
            if p <= 0:
                continue
            delta = C.OSCILLATION_DELTA_SPARSE if p < C.SPARSE_THRESHOLD else C.OSCILLATION_DELTA
            # 재정규화 여유를 감안한 느슨한 상한.
            self.assertLessEqual(probs[b], p * (1 + delta) / total_base * 1.5 + 0.02)


class TestIntraBracketSkew(unittest.TestCase):
    def test_lower_skew(self):
        # triangular 하한 쏠림 -> 평균이 구간 중앙보다 낮아야 함.
        rng = random.Random(4)
        samples = [dist.sample_amount_in_bracket(rng, 5) for _ in range(5000)]
        low = 5 * C.BRACKET_WIDTH
        mid = low + C.BRACKET_WIDTH / 2
        self.assertLess(sum(samples) / len(samples), mid)

    def test_within_bounds(self):
        rng = random.Random(5)
        for b in range(24):
            for _ in range(50):
                a = dist.sample_amount_in_bracket(rng, b)
                self.assertGreaterEqual(a, b * C.BRACKET_WIDTH)
                self.assertLess(a, (b + 1) * C.BRACKET_WIDTH)


class TestDatasets(unittest.TestCase):
    def test_counts_and_scenarios(self):
        datasets = generate_all()
        self.assertEqual(len(datasets), C.N_ROUNDS)
        n_hit = sum(1 for d in datasets if d.scenario == "hit")
        self.assertEqual(n_hit, C.HIT_ROUNDS)
        for d in datasets:
            self.assertTrue(4000 <= d.n_customers <= 5000)
            self.assertEqual(len(d.transfers), d.n_customers)
            self.assertEqual(scenario_for_round(d.round_idx), d.scenario)

    def test_reproducible(self):
        a = generate_all()
        b = generate_all()
        self.assertEqual(a[0].transfers, b[0].transfers)


if __name__ == "__main__":
    unittest.main()
