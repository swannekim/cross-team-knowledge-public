"""Benchmark smoke/regression test: safety metrics must be zero for every architecture."""
from __future__ import annotations

from bench.run_benchmark import PROBES, USERS, render_markdown, run_benchmark
from common.testing import OfflineTestCase


class BenchmarkSafety(OfflineTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.summary = run_benchmark(write=False)

    def test_zero_leaks_and_zero_unauthorised_results(self):
        """All 3 architectures x 20 probes x 3 users: 0 unauthorised results, 0 PII, 0 original URLs, 0 HC leaks."""
        self.assertEqual(set(self.summary["architectures"]), {"A", "B", "C"})
        for name, metrics in self.summary["architectures"].items():
            self.assertEqual(metrics["queries"], len(PROBES) * len(USERS), name)
            for key in ("unauthorisedResultsReturned", "piiLeaks", "originalUrlExposures", "highlyConfidentialLeaks",
                        "resultsForCarol", "resultsForDave"):
                self.assertEqual(metrics[key], 0, f"{name}.{key}")
            self.assertGreater(metrics["meanLatencyMs"], 0)

    def test_answer_rates_and_policy_signals(self):
        """bob gets useful answers everywhere; B shows its per-request controls (403s for carol/dave, guard withholds)."""
        archs = self.summary["architectures"]
        self.assertGreaterEqual(archs["A"]["authorisedAnswerRateBob"], 0.9)
        self.assertGreaterEqual(archs["B"]["authorisedAnswerRateBob"], 0.5)
        self.assertGreaterEqual(archs["C"]["authorisedAnswerRateBob"], 0.5)
        self.assertGreaterEqual(archs["B"]["policyDenials"], 2 * len(PROBES))
        self.assertGreater(archs["B"]["withheldByExfiltrationGuard"], 0)
        self.assertLessEqual(archs["B"]["maxSourceCharsPerResult"], 300)
        self.assertGreaterEqual(self.summary["diagnostics"]["bAnswerRateWithoutExfiltrationGuard"],
                                archs["B"]["authorisedAnswerRateBob"])
        self.assertIn("| Metric |", render_markdown(self.summary))
