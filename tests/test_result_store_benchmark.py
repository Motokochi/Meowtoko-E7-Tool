from __future__ import annotations

import ast
import inspect
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

import numpy as np

from scripts import benchmark_result_store as benchmark_module
from scripts.benchmark_result_store import (
    BENCHMARK_GATE,
    BENCHMARK_ID,
    CAP_ROW_COUNT,
    ResultStoreBenchmarkConfig,
    ResultStoreBenchmarkError,
    benchmark_gate_enabled,
    build_synthetic_fixture,
    build_synthetic_result_batch,
    cleanup_owned_workspace,
    create_owned_workspace,
    render_markdown_report,
    summarize_samples,
    validate_benchmark_document,
)
from src.optimizer.result_store import RESULT_COLUMN_NAMES, RESULT_ROW_BYTES, validate_result_columns


class ResultStoreBenchmarkTests(unittest.TestCase):
    def test_gate_is_exact_and_refuses_before_touching_workspace(self) -> None:
        self.assertFalse(benchmark_gate_enabled({}))
        self.assertFalse(benchmark_gate_enabled({BENCHMARK_GATE: "true"}))
        self.assertTrue(benchmark_gate_enabled({BENCHMARK_GATE: " 1 "}))
        with tempfile.TemporaryDirectory(prefix="e7-result-benchmark-gate-") as temporary:
            workspace = Path(temporary) / "must-not-exist"
            output = io.StringIO()
            with patch.dict(benchmark_module.os.environ, {}, clear=True), redirect_stderr(output):
                self.assertEqual(2, benchmark_module.main(["--workspace", str(workspace)]))
            self.assertFalse(workspace.exists())
            self.assertIn(BENCHMARK_GATE, output.getvalue())

    def test_configuration_is_bounded_and_full_export_is_explicit(self) -> None:
        config = ResultStoreBenchmarkConfig(
            Path(".build/benchmarks/bench"),
            row_count=10,
            export_rows=4,
        )
        self.assertEqual(10, config.row_count)
        self.assertEqual(4, config.export_rows)
        full = ResultStoreBenchmarkConfig(
            Path(".build/benchmarks/full"),
            row_count=10,
            export_rows=4,
            full_export=True,
        )
        self.assertEqual(10, full.export_rows)
        for values in (
            {"row_count": 0},
            {"row_count": CAP_ROW_COUNT + 1},
            {"row_count": 10, "export_rows": 11},
            {"row_count": 10, "repetitions": 0},
        ):
            with self.subTest(values=values), self.assertRaises(ResultStoreBenchmarkError):
                ResultStoreBenchmarkConfig(Path(".build/benchmarks/invalid"), **values)

    def test_fixture_and_vector_batches_are_deterministic_schema_rows(self) -> None:
        fixture = build_synthetic_fixture("result-store-test-fixture")
        first = build_synthetic_result_batch(fixture, 0, 2_005)
        second = build_synthetic_result_batch(fixture, 0, 2_005)
        self.assertEqual(tuple(RESULT_COLUMN_NAMES), tuple(first))
        self.assertEqual(2_005, validate_result_columns(first))
        for name in RESULT_COLUMN_NAMES:
            self.assertTrue(np.array_equal(first[name], second[name]), name)
        attack = first["effective_final_stats"][:, fixture.attack_axis]
        self.assertEqual(fixture.base_attack, int(attack[0]))
        self.assertEqual(fixture.base_attack + 999, int(attack[999]))
        self.assertEqual(fixture.base_attack, int(attack[1_000]))
        self.assertEqual([0, 1, 2], first["derived_metrics"][:3, 0].tolist())
        self.assertEqual(0, int(first["category_codes"].max()))

    def test_summary_math_separates_first_and_repeat_tail(self) -> None:
        samples = [
            {
                "operationId": "filter",
                "condition": condition,
                "elapsedSeconds": elapsed,
                "peakRssDeltaBytes": rss,
                "pythonAllocationPeakBytes": python,
            }
            for condition, elapsed, rss, python in (
                ("first", 9.0, 10, 1),
                ("repeat", 1.0, 20, 2),
                ("repeat", 3.0, 30, 3),
                ("repeat", 2.0, 40, 4),
            )
        ]
        summary = summarize_samples(samples)[0]
        self.assertEqual(9.0, summary["firstSeconds"])
        self.assertEqual(2.0, summary["repeatMedianSeconds"])
        self.assertEqual(3.0, summary["repeatP95Seconds"])
        self.assertEqual(40, summary["maximumPeakRssDeltaBytes"])
        self.assertEqual(4, summary["maximumPythonAllocationPeakBytes"])

    def test_workspace_cleanup_requires_exact_marker_and_preserves_foreign_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="e7-result-benchmark-cleanup-") as temporary:
            parent = Path(temporary)
            owned = parent / "owned"
            create_owned_workspace(owned)
            (owned / "artifact.bin").write_bytes(b"owned")
            cleanup_owned_workspace(owned)
            self.assertFalse(owned.exists())

            foreign = parent / "foreign"
            foreign.mkdir()
            (foreign / "keep.txt").write_text("keep", encoding="utf-8")
            with self.assertRaises(ResultStoreBenchmarkError):
                cleanup_owned_workspace(foreign)
            self.assertEqual("keep", (foreign / "keep.txt").read_text(encoding="utf-8"))

    def test_small_gated_run_covers_schema_and_cleans_workspace(self) -> None:
        with tempfile.TemporaryDirectory(prefix="e7-result-benchmark-small-") as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            output = root / "evidence.json"
            report = root / "report.md"
            with patch.dict(benchmark_module.os.environ, {BENCHMARK_GATE: "1"}, clear=True):
                code = benchmark_module.main(
                    [
                        "--workspace",
                        str(workspace),
                        "--rows",
                        "2000",
                        "--batch-rows",
                        "1000",
                        "--export-rows",
                        "10",
                        "--repetitions",
                        "1",
                        "--output",
                        str(output),
                        "--report",
                        str(report),
                    ]
                )
            self.assertEqual(0, code)
            self.assertFalse(workspace.exists())
            document = json.loads(output.read_text(encoding="utf-8"))
            validate_benchmark_document(document, require_cap=False)
            self.assertNotIn("machine", document["environment"])
            self.assertEqual(2_000 * RESULT_ROW_BYTES, document["storage"]["payloadBytes"])
            self.assertIn("Practical P08 requirements", report.read_text(encoding="utf-8"))

    def test_small_full_export_measures_every_row_instead_of_the_filtered_view(self) -> None:
        with tempfile.TemporaryDirectory(prefix="e7-result-benchmark-full-") as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            output = root / "evidence.json"
            with patch.dict(benchmark_module.os.environ, {BENCHMARK_GATE: "1"}, clear=True):
                code = benchmark_module.main(
                    [
                        "--workspace",
                        str(workspace),
                        "--rows",
                        "2000",
                        "--batch-rows",
                        "1000",
                        "--export-rows",
                        "10",
                        "--repetitions",
                        "1",
                        "--full-export",
                        "--output",
                        str(output),
                    ]
                )
            self.assertEqual(0, code)
            self.assertFalse(workspace.exists())
            document = json.loads(output.read_text(encoding="utf-8"))
            for evidence in document["validations"]["exports"].values():
                self.assertEqual(2_000, evidence["measuredRows"])
                self.assertFalse(evidence["fullCapTimingIsProjected"])

    def test_harness_is_developer_only_and_has_no_later_runtime_dependencies(self) -> None:
        source = inspect.getsource(benchmark_module)
        tree = ast.parse(source)
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        self.assertFalse(any(name.startswith("src.optimizer.cuda") for name in imports))
        self.assertFalse(any(name.startswith("src.desktop") for name in imports))
        self.assertNotIn("InventoryRepository", source)
        self.assertNotIn("tests.", source)
        spec = (benchmark_module.ROOT / "packaging" / "e7-core.spec").read_text(encoding="utf-8")
        self.assertNotIn("benchmark_result_store", spec)


if __name__ == "__main__":
    unittest.main()
