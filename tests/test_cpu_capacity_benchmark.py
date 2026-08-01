from __future__ import annotations

import ast
import inspect
import json
import tempfile
import unittest
from pathlib import Path

from scripts import benchmark_cpu_capacity as benchmark_module
from scripts.benchmark_cpu_capacity import (
    BENCHMARK_ID,
    CpuCapacityBenchmarkError,
    run_campaign,
    run_cpu_sample,
    run_overflow_probe,
    summarize_cpu_samples,
    validate_document,
)
from scripts.benchmark_cpu_optimizer import CPU_BENCHMARK_SCENARIOS
from src.optimizer.domain import MAX_RESULT_CAP


class CpuCapacityBenchmarkTests(unittest.TestCase):
    def test_repeated_small_samples_preserve_deterministic_search_evidence(self) -> None:
        scenario = CPU_BENCHMARK_SCENARIOS[0]
        samples = [run_cpu_sample(scenario, index) for index in range(2)]
        summary = summarize_cpu_samples(scenario, samples)
        self.assertEqual("small", summary["scenario"])
        self.assertEqual(2, summary["sampleCount"])
        self.assertEqual(64, summary["searchedPermutations"])
        self.assertEqual(64, summary["hardBoundRejectedCount"])
        self.assertEqual(0, summary["matchCount"])
        self.assertEqual("completed", summary["state"])
        self.assertGreater(summary["peakRssBytes"], 0)
        self.assertGreaterEqual(summary["searchSeconds"]["minimum"], 0)

    def test_low_cap_probe_consumes_exact_sentinel_and_exposes_no_partial(self) -> None:
        evidence = run_overflow_probe(5)
        self.assertEqual(6, evidence["detectedCount"])
        self.assertEqual([2, 2, 2], evidence["categoryCounts"])
        self.assertTrue(evidence["overflowed"])
        self.assertEqual(0, evidence["retainedCount"])
        self.assertEqual(0, evidence["publishedResultCount"])
        self.assertFalse(evidence["sourceResumedAfterSentinel"])
        self.assertFalse(evidence["partialRowsExposed"])

    def test_small_campaign_keeps_raw_samples_and_omits_host_identity(self) -> None:
        document = run_campaign(
            warmups=0,
            samples=1,
            scenarios=(CPU_BENCHMARK_SCENARIOS[0],),
            include_overflow=False,
        )
        validate_document(document, require_overflow=False)
        self.assertEqual(BENCHMARK_ID, document["benchmarkId"])
        self.assertEqual(1, len(document["samples"]))
        self.assertEqual(1, len(document["summaries"]))
        self.assertIsNone(document["overflow"])
        self.assertNotIn("machine", document["environment"])

    def test_invalid_configuration_and_summary_fail_closed(self) -> None:
        scenario = CPU_BENCHMARK_SCENARIOS[0]
        with self.assertRaises(CpuCapacityBenchmarkError):
            run_campaign(samples=0, include_overflow=False)
        with self.assertRaises(CpuCapacityBenchmarkError):
            run_campaign(scenarios=(scenario, scenario), include_overflow=False)
        with self.assertRaises(CpuCapacityBenchmarkError):
            summarize_cpu_samples(scenario, ())
        with self.assertRaises(CpuCapacityBenchmarkError):
            run_overflow_probe(MAX_RESULT_CAP + 1)

    def test_cli_can_run_one_small_sample_without_the_expensive_probe(self) -> None:
        with tempfile.TemporaryDirectory(prefix="e7-cpu-capacity-cli-") as temporary:
            output = Path(temporary) / "evidence.json"
            self.assertEqual(
                0,
                benchmark_module.main(
                    [
                        "--scenario",
                        "small",
                        "--warmups",
                        "0",
                        "--samples",
                        "1",
                        "--skip-overflow",
                        "--output",
                        str(output),
                    ]
                ),
            )
            document = json.loads(output.read_text(encoding="utf-8"))
            validate_document(document, require_overflow=False)

    def test_harness_is_developer_only_and_has_no_live_or_cuda_dependency(self) -> None:
        source = inspect.getsource(benchmark_module)
        tree = ast.parse(source)
        imports = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        self.assertFalse(any(name.startswith("src.desktop") for name in imports))
        self.assertFalse(any(name.startswith("src.optimizer.cuda") for name in imports))
        self.assertNotIn("user_data", source)
        self.assertNotIn("gear.txt", source)
        spec = (benchmark_module.ROOT / "packaging" / "e7-core.spec").read_text(encoding="utf-8")
        self.assertNotIn("benchmark_cpu_capacity", spec)


if __name__ == "__main__":
    unittest.main()
