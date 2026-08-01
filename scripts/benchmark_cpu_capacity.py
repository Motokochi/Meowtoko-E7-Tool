"""Run repeated CPU optimizer samples and the production cap+1 counter."""

from __future__ import annotations

import argparse
import ctypes
import gc
import hashlib
import importlib.metadata
import json
import os
import platform
import statistics
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.benchmark_cpu_optimizer import (  # noqa: E402
    CPU_BENCHMARK_SCENARIOS,
    CPU_BENCHMARK_SCENARIOS_BY_ID,
    CpuBenchmarkScenario,
    build_synthetic_benchmark_inputs,
    run_benchmark_scenario,
)
from src.optimizer.domain import (  # noqa: E402
    MAX_RESULT_CAP,
    RESULT_CATEGORY_ORDER,
    ExecutionPreference,
)
from src.optimizer.search import MatchEvent, count_match_events  # noqa: E402


BENCHMARK_ID = "e7.optimizer.cpu-capacity-validation"
BENCHMARK_SCHEMA_VERSION = 1
DEFAULT_WARMUPS = 1
DEFAULT_SAMPLES = 5


class CpuCapacityBenchmarkError(ValueError):
    """Raised when benchmark configuration or deterministic evidence drifts."""


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("page_fault_count", ctypes.c_ulong),
        ("peak_working_set_size", ctypes.c_size_t),
        ("working_set_size", ctypes.c_size_t),
        ("quota_peak_paged_pool_usage", ctypes.c_size_t),
        ("quota_paged_pool_usage", ctypes.c_size_t),
        ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
        ("quota_non_paged_pool_usage", ctypes.c_size_t),
        ("pagefile_usage", ctypes.c_size_t),
        ("peak_pagefile_usage", ctypes.c_size_t),
        ("private_usage", ctypes.c_size_t),
    ]


if os.name == "nt":
    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _PSAPI = ctypes.WinDLL("psapi", use_last_error=True)
    _KERNEL32.GetCurrentProcess.restype = ctypes.c_void_p
    _PSAPI.GetProcessMemoryInfo.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_ProcessMemoryCounters),
        ctypes.c_ulong,
    ]
    _PSAPI.GetProcessMemoryInfo.restype = ctypes.c_int


def _process_rss_bytes() -> int:
    if os.name != "nt":
        return 0
    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    if not _PSAPI.GetProcessMemoryInfo(
        _KERNEL32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    ):
        raise CpuCapacityBenchmarkError(
            f"GetProcessMemoryInfo failed with Windows error {ctypes.get_last_error()}."
        )
    return int(counters.working_set_size)


def _system_memory_bytes() -> int:
    if os.name != "nt":
        return 0

    class _MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong),
            ("memory_load", ctypes.c_ulong),
            ("total_physical", ctypes.c_ulonglong),
            ("available_physical", ctypes.c_ulonglong),
            ("total_page_file", ctypes.c_ulonglong),
            ("available_page_file", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong),
            ("available_virtual", ctypes.c_ulonglong),
            ("available_extended_virtual", ctypes.c_ulonglong),
        ]

    status = _MemoryStatus()
    status.length = ctypes.sizeof(status)
    return int(status.total_physical) if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)) else 0


class _RssSampler:
    def __init__(self) -> None:
        self.start_bytes = _process_rss_bytes()
        self.peak_bytes = self.start_bytes
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self) -> None:
        while not self._stop.wait(0.005):
            self.peak_bytes = max(self.peak_bytes, _process_rss_bytes())

    def __enter__(self) -> "_RssSampler":
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join()
        self.peak_bytes = max(self.peak_bytes, _process_rss_bytes())


def _integer(value: object, path: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CpuCapacityBenchmarkError(f"{path} must be an integer.")
    if value < minimum or value > maximum:
        raise CpuCapacityBenchmarkError(
            f"{path} must be between {minimum:,} and {maximum:,}; found {value:,}."
        )
    return value


def run_cpu_sample(scenario: CpuBenchmarkScenario, sample_index: int) -> dict[str, object]:
    if not isinstance(scenario, CpuBenchmarkScenario):
        raise CpuCapacityBenchmarkError("scenario must be a CpuBenchmarkScenario.")
    index = _integer(sample_index, "sample_index", 0, 99)
    gc.collect()
    started = time.perf_counter()
    with _RssSampler() as memory:
        record = run_benchmark_scenario(scenario)
    wall_seconds = time.perf_counter() - started
    return {
        **record.deterministic_evidence(),
        "sampleIndex": index,
        "searchSeconds": record.elapsed_seconds,
        "wallSeconds": wall_seconds,
        "permutationsPerSecond": record.permutations_per_second,
        "peakRssBytes": memory.peak_bytes,
    }


def _metric(samples: Sequence[Mapping[str, object]], field: str) -> dict[str, float]:
    values = [float(sample[field]) for sample in samples]
    return {
        "median": statistics.median(values),
        "minimum": min(values),
        "maximum": max(values),
    }


def summarize_cpu_samples(
    scenario: CpuBenchmarkScenario,
    samples: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    if not samples:
        raise CpuCapacityBenchmarkError("CPU summary requires samples.")
    deterministic_keys = (
        "scenario",
        "workload",
        "radices",
        "totalPermutations",
        "searchedPermutations",
        "batchSize",
        "completedBatchCount",
        "exactSetCandidates",
        "hardBoundRejectedCount",
        "matchCount",
        "state",
    )
    first = {key: samples[0][key] for key in deterministic_keys}
    if any({key: sample[key] for key in deterministic_keys} != first for sample in samples):
        raise CpuCapacityBenchmarkError("CPU deterministic evidence changed between samples.")
    if first["scenario"] != scenario.scenario_id:
        raise CpuCapacityBenchmarkError("CPU sample scenario identity drifted.")
    return {
        **first,
        "sampleCount": len(samples),
        "searchSeconds": _metric(samples, "searchSeconds"),
        "wallSeconds": _metric(samples, "wallSeconds"),
        "permutationsPerSecond": _metric(samples, "permutationsPerSecond"),
        "peakRssBytes": max(int(sample["peakRssBytes"]) for sample in samples),
    }


def run_overflow_probe(cap: int = MAX_RESULT_CAP) -> dict[str, object]:
    selected_cap = _integer(cap, "cap", 1, MAX_RESULT_CAP)
    _, _, default_context = build_synthetic_benchmark_inputs(CPU_BENCHMARK_SCENARIOS[0])
    context = replace(default_context, result_cap=selected_cap)
    requested = selected_cap + 1
    consumed = 0
    resumed_after_sentinel = False

    def events():
        nonlocal consumed, resumed_after_sentinel
        for flat_index in range(requested):
            consumed += 1
            yield MatchEvent(
                RESULT_CATEGORY_ORDER[flat_index % len(RESULT_CATEGORY_ORDER)],
                flat_index,
                (0, 1, 2, 3, 4, 5),
            )
        resumed_after_sentinel = True
        raise AssertionError("overflow event source resumed after cap+1")

    gc.collect()
    started = time.perf_counter()
    with _RssSampler() as memory:
        result = count_match_events(context, events())
    elapsed = time.perf_counter() - started
    quotient, remainder = divmod(requested, len(RESULT_CATEGORY_ORDER))
    expected_counts = tuple(
        quotient + (1 if index < remainder else 0)
        for index in range(len(RESULT_CATEGORY_ORDER))
    )
    summary = result.to_search_summary(
        evaluated_permutations=requested,
        duration_seconds=elapsed,
        execution_preference=ExecutionPreference.CPU,
    )
    if (
        not result.overflowed
        or result.detected_count != requested
        or result.category_counts != expected_counts
        or result.retained_count != 0
        or summary.result_count != 0
        or consumed != requested
        or resumed_after_sentinel
    ):
        raise CpuCapacityBenchmarkError("production cap+1 overflow evidence drifted.")
    return {
        "resultCap": selected_cap,
        "detectedCount": result.detected_count,
        "categoryCounts": list(result.category_counts),
        "overflowed": result.overflowed,
        "retainedCount": result.retained_count,
        "publishedResultCount": summary.result_count,
        "sourceEventsConsumed": consumed,
        "sourceResumedAfterSentinel": resumed_after_sentinel,
        "partialRowsExposed": False,
        "elapsedSeconds": elapsed,
        "eventsPerSecond": requested / elapsed,
        "peakRssBytes": memory.peak_bytes,
        "memoryModel": "constant-state-no-retained-row-collection",
    }


def run_campaign(
    *,
    warmups: int = DEFAULT_WARMUPS,
    samples: int = DEFAULT_SAMPLES,
    scenarios: Sequence[CpuBenchmarkScenario] = CPU_BENCHMARK_SCENARIOS,
    include_overflow: bool = True,
) -> dict[str, object]:
    warmup_count = _integer(warmups, "warmups", 0, 10)
    sample_count = _integer(samples, "samples", 1, 20)
    selected = tuple(scenarios)
    if (
        not selected
        or len({item.scenario_id for item in selected}) != len(selected)
        or not all(isinstance(item, CpuBenchmarkScenario) for item in selected)
        or not isinstance(include_overflow, bool)
    ):
        raise CpuCapacityBenchmarkError("scenario/overflow selection is invalid.")
    raw: list[dict[str, object]] = []
    summaries: list[dict[str, object]] = []
    for scenario in selected:
        for index in range(warmup_count):
            run_cpu_sample(scenario, 50 + index)
        measured = [run_cpu_sample(scenario, index) for index in range(sample_count)]
        raw.extend(measured)
        summaries.append(summarize_cpu_samples(scenario, measured))
    document = {
        "benchmarkId": BENCHMARK_ID,
        "schemaVersion": BENCHMARK_SCHEMA_VERSION,
        "measuredUtc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "privacy": "synthetic-in-memory-only",
        "environment": {
            "system": platform.system(),
            "release": platform.release(),
            "architecture": platform.machine(),
            "processor": platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER", "unknown"),
            "logicalCpuCount": os.cpu_count(),
            "totalRamBytes": _system_memory_bytes(),
            "pythonImplementation": platform.python_implementation(),
            "pythonVersion": platform.python_version(),
            "numpyVersion": importlib.metadata.version("numpy"),
        },
        "configuration": {
            "warmups": warmup_count,
            "samples": sample_count,
            "scenarios": [item.scenario_id for item in selected],
            "overflowProbe": include_overflow,
        },
        "samples": raw,
        "summaries": summaries,
        "overflow": run_overflow_probe() if include_overflow else None,
        "sourceSha256": {
            "cpuBenchmark": hashlib.sha256(
                (ROOT / "scripts" / "benchmark_cpu_optimizer.py").read_bytes()
            ).hexdigest(),
            "matchCounting": hashlib.sha256(
                (ROOT / "src" / "optimizer" / "search" / "match_counting.py").read_bytes()
            ).hexdigest(),
        },
    }
    validate_document(document, require_overflow=include_overflow)
    return document


def validate_document(document: object, *, require_overflow: bool = True) -> None:
    if not isinstance(document, dict):
        raise CpuCapacityBenchmarkError("benchmark document must be an object.")
    if document.get("benchmarkId") != BENCHMARK_ID or document.get("schemaVersion") != 1:
        raise CpuCapacityBenchmarkError("benchmark identity/version is invalid.")
    environment = document.get("environment")
    configuration = document.get("configuration")
    samples = document.get("samples")
    summaries = document.get("summaries")
    if not isinstance(environment, dict) or "machine" in environment:
        raise CpuCapacityBenchmarkError("environment evidence is missing or contains host identity.")
    if not isinstance(configuration, dict) or not isinstance(samples, list) or not isinstance(summaries, list):
        raise CpuCapacityBenchmarkError("configuration/raw sample/summary evidence is missing.")
    if len(samples) != int(configuration["samples"]) * len(configuration["scenarios"]):
        raise CpuCapacityBenchmarkError("raw CPU sample count drifted.")
    overflow = document.get("overflow")
    if require_overflow:
        if not isinstance(overflow, dict):
            raise CpuCapacityBenchmarkError("production overflow evidence is required.")
        if (
            overflow.get("resultCap") != MAX_RESULT_CAP
            or overflow.get("detectedCount") != MAX_RESULT_CAP + 1
            or overflow.get("retainedCount") != 0
            or overflow.get("publishedResultCount") != 0
            or overflow.get("partialRowsExposed") is not False
        ):
            raise CpuCapacityBenchmarkError("production overflow evidence is invalid.")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warmups", type=int, default=DEFAULT_WARMUPS)
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    parser.add_argument(
        "--scenario",
        action="append",
        choices=tuple(CPU_BENCHMARK_SCENARIOS_BY_ID),
        dest="scenario_ids",
    )
    parser.add_argument("--skip-overflow", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        selected = (
            CPU_BENCHMARK_SCENARIOS
            if not args.scenario_ids
            else tuple(CPU_BENCHMARK_SCENARIOS_BY_ID[item] for item in args.scenario_ids)
        )
        document = run_campaign(
            warmups=args.warmups,
            samples=args.samples,
            scenarios=selected,
            include_overflow=not args.skip_overflow,
        )
        serialized = json.dumps(document, indent=2, sort_keys=True) + "\n"
        if args.output is None:
            print(serialized, end="")
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized, encoding="utf-8")
        return 0
    except (OSError, CpuCapacityBenchmarkError, ValueError) as error:
        print(f"CPU capacity benchmark error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
