from __future__ import annotations

import ast
import inspect
import io
import json
import math
import unittest
from contextlib import redirect_stdout
from dataclasses import FrozenInstanceError, replace

from src.optimizer.search import CpuSearchTerminalState
from scripts import benchmark_cpu_optimizer as benchmark_module
from scripts.benchmark_cpu_optimizer import (
    BENCHMARK_ID,
    BENCHMARK_SCHEMA_VERSION,
    CPU_BENCHMARK_SCENARIOS,
    CpuBenchmarkError,
    CpuBenchmarkRecord,
    CpuBenchmarkWorkload,
    build_synthetic_benchmark_inputs,
    run_benchmark_scenario,
    select_scenarios,
)


def _clock(*values: float):
    supplied = iter(values)

    def read() -> float:
        try:
            return next(supplied)
        except StopIteration:
            raise AssertionError("benchmark clock read beyond a batch boundary") from None

    return read


class CpuBenchmarkTests(unittest.TestCase):
    def test_catalog_pins_named_radices_totals_batches_and_exact_density(self) -> None:
        self.assertEqual(
            ("small", "medium", "broad"),
            tuple(scenario.scenario_id for scenario in CPU_BENCHMARK_SCENARIOS),
        )
        self.assertEqual(
            ((2, 2, 2, 2, 2, 2), (5, 5, 5, 5, 5, 5), (10, 10, 10, 10, 10, 10)),
            tuple(scenario.radices for scenario in CPU_BENCHMARK_SCENARIOS),
        )
        self.assertEqual(
            (64, 15_625, 1_000_000),
            tuple(scenario.total_permutations for scenario in CPU_BENCHMARK_SCENARIOS),
        )
        self.assertEqual(
            (2, 31, 123),
            tuple(scenario.expected_batch_count for scenario in CPU_BENCHMARK_SCENARIOS),
        )
        self.assertEqual(
            (64, 15_625, 1),
            tuple(scenario.expected_exact_candidates for scenario in CPU_BENCHMARK_SCENARIOS),
        )
        self.assertEqual(
            (
                CpuBenchmarkWorkload.FULL_EXACT_REJECTED,
                CpuBenchmarkWorkload.FULL_EXACT_REJECTED,
                CpuBenchmarkWorkload.SPARSE_EXACT_REJECTED,
            ),
            tuple(scenario.workload for scenario in CPU_BENCHMARK_SCENARIOS),
        )

    def test_synthetic_preparation_produces_each_explicit_six_slot_radix(self) -> None:
        for scenario in CPU_BENCHMARK_SCENARIOS:
            with self.subTest(scenario=scenario.scenario_id):
                arrays, evaluation, counting = build_synthetic_benchmark_inputs(scenario)
                self.assertEqual(
                    scenario.radices,
                    tuple(len(slot.dense_ids) for slot in arrays.slots),
                )
                self.assertEqual(evaluation.request_id, arrays.request_id)
                self.assertEqual(counting.request_id, arrays.request_id)
                self.assertEqual(
                    f"benchmark.cpu-exact.{scenario.scenario_id}",
                    arrays.request_id,
                )

    def test_small_run_is_complete_and_repeated_nontiming_evidence_is_identical(self) -> None:
        scenario = CPU_BENCHMARK_SCENARIOS[0]
        first = run_benchmark_scenario(scenario, clock=_clock(0, 1, 2))
        second = run_benchmark_scenario(scenario, clock=_clock(10, 12, 14))
        self.assertEqual(first.deterministic_evidence(), second.deterministic_evidence())
        self.assertEqual(2.0, first.elapsed_seconds)
        self.assertEqual(4.0, second.elapsed_seconds)
        self.assertEqual(32.0, first.permutations_per_second)
        self.assertEqual(16.0, second.permutations_per_second)
        self.assertEqual(CpuSearchTerminalState.COMPLETED, first.state)
        self.assertEqual(first.scenario.total_permutations, first.searched_permutations)
        self.assertEqual(0, first.match_count)

    def test_cli_emits_one_environment_and_one_stable_json_scenario_record(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = benchmark_module.main(["--scenario", "small"])
        self.assertEqual(0, exit_code)
        lines = output.getvalue().splitlines()
        self.assertEqual(2, len(lines))
        parsed = tuple(json.loads(line) for line in lines)
        self.assertEqual(("environment", "scenario"), tuple(item["recordType"] for item in parsed))
        for line, record in zip(lines, parsed, strict=True):
            self.assertEqual(
                json.dumps(record, sort_keys=True, separators=(",", ":")),
                line,
            )
            self.assertEqual(BENCHMARK_ID, record["benchmarkId"])
            self.assertEqual(BENCHMARK_SCHEMA_VERSION, record["schemaVersion"])
        scenario = parsed[1]
        self.assertEqual("small", scenario["scenario"])
        self.assertEqual(scenario["totalPermutations"], scenario["searchedPermutations"])
        self.assertEqual("completed", scenario["state"])
        self.assertEqual(0, scenario["matchCount"])
        self.assertTrue(math.isfinite(scenario["elapsedSeconds"]))
        self.assertGreaterEqual(scenario["elapsedSeconds"], 0)
        self.assertTrue(math.isfinite(scenario["permutationsPerSecond"]))
        self.assertGreaterEqual(scenario["permutationsPerSecond"], 0)

    def test_cli_selection_retains_order_and_rejects_unknowns_duplicates(self) -> None:
        self.assertEqual(CPU_BENCHMARK_SCENARIOS, select_scenarios(None))
        self.assertEqual(
            ("broad", "small"),
            tuple(item.scenario_id for item in select_scenarios(("broad", "small"))),
        )
        for supplied in (("small", "small"), ("unknown",)):
            with self.subTest(supplied=supplied):
                with self.assertRaises(CpuBenchmarkError):
                    select_scenarios(supplied)
        with self.assertRaises(SystemExit) as caught:
            benchmark_module.parse_args(["--scenario", "unknown"])
        self.assertEqual(2, caught.exception.code)

    def test_records_validate_impossible_evidence_and_are_frozen_hashable(self) -> None:
        valid = run_benchmark_scenario(
            CPU_BENCHMARK_SCENARIOS[0],
            clock=_clock(0, 1, 2),
        )
        for changes in (
            {"searched_permutations": True},
            {"searched_permutations": 63},
            {"completed_batch_count": 1},
            {"exact_set_candidates": 63},
            {"hard_bound_rejected_count": 63},
            {"match_count": 1},
            {"state": CpuSearchTerminalState.CANCELLED},
            {"elapsed_seconds": float("nan")},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(CpuBenchmarkError):
                    replace(valid, **changes)
        zero_elapsed = replace(valid, elapsed_seconds=0)
        self.assertEqual(0, zero_elapsed.permutations_per_second)
        self.assertIsInstance(hash(valid), int)
        with self.assertRaises(FrozenInstanceError):
            valid.match_count = 1  # type: ignore[misc]

    def test_benchmark_is_developer_only_and_has_no_live_or_future_phase_dependencies(self) -> None:
        source = inspect.getsource(benchmark_module)
        tree = ast.parse(source)
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )
        self.assertNotIn("user_data", source)
        self.assertNotIn("gear.txt", source)
        forbidden = (
            "src.desktop",
            "src.ui",
            "src.optimizer.persistence",
            "src.optimizer.search.cuda",
            "src.optimizer.search.near",
        )
        self.assertFalse(
            any(name == root or name.startswith(f"{root}.") for name in imports for root in forbidden),
            imports,
        )
        spec = (benchmark_module.ROOT / "packaging" / "e7-core.spec").read_text(encoding="utf-8")
        self.assertNotIn("benchmark_cpu_optimizer", spec)


if __name__ == "__main__":
    unittest.main()
