"""Run reproducible synthetic benchmarks for the exact CPU optimizer."""

from __future__ import annotations

import argparse
import json
import math
import platform
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from numbers import Real
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.optimizer.data import (  # noqa: E402
    ArtifactSelection,
    load_bundled_character_profile_selector,
    load_bundled_skill_context_repository,
    merge_fribbels_inventory,
    parse_fribbels_gear_bytes,
)
from src.optimizer.domain import (  # noqa: E402
    GEAR_SLOT_ORDER,
    MAX_RESULT_CAP,
    FinalStat,
    GearSet,
    GearSlot,
    HeroModifiers,
    ItemProjectionMode,
    ItemStatType,
    OptimizationRequest,
    SetPattern,
    SkillContext,
    SkillSlot,
    StatRange,
    gear_set_fribbels_name,
    gear_slot_fribbels_name,
    item_stat_fribbels_name,
)
from src.optimizer.search import (  # noqa: E402
    CpuSearchTerminalState,
    compile_exact_build_context,
    compile_match_counting_context,
    compile_set_pattern,
    prepare_search_slot_arrays,
    run_exact_cpu_search,
)
from src.optimizer.search.cartesian import Clock  # noqa: E402


BENCHMARK_ID = "e7.optimizer.cpu-exact"
BENCHMARK_SCHEMA_VERSION = 1
IMPOSSIBLE_ATTACK_MINIMUM = 1_000_000_000


class CpuBenchmarkError(ValueError):
    """Actionable benchmark catalog, fixture, or result failure."""


class CpuBenchmarkWorkload(StrEnum):
    """Synthetic exact-evaluation density used by one scenario."""

    FULL_EXACT_REJECTED = "full-exact-rejected"
    SPARSE_EXACT_REJECTED = "sparse-exact-rejected"


def _integer(value: object, path: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CpuBenchmarkError(f"{path} must be an integer; booleans are not accepted.")
    if value < minimum:
        raise CpuBenchmarkError(f"{path} must be at least {minimum}; found {value}.")
    return value


def _number(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise CpuBenchmarkError(f"{path} must be a finite nonnegative number.")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        raise CpuBenchmarkError(f"{path} must be a finite nonnegative number.")
    return numeric


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CpuBenchmarkError(f"{path} must be nonempty text.")
    return value.strip()


@dataclass(frozen=True, slots=True)
class CpuBenchmarkScenario:
    """One deterministic six-slot synthetic inventory shape."""

    scenario_id: str
    radices: tuple[int, ...]
    batch_size: int
    workload: CpuBenchmarkWorkload

    def __post_init__(self) -> None:
        scenario_id = _text(self.scenario_id, "CpuBenchmarkScenario.scenario_id")
        if isinstance(self.radices, (str, bytes, bytearray)):
            raise CpuBenchmarkError("CpuBenchmarkScenario.radices must contain six integers.")
        try:
            radices = tuple(
                _integer(value, f"CpuBenchmarkScenario.radices[{index}]", minimum=1)
                for index, value in enumerate(self.radices)
            )
        except TypeError:
            raise CpuBenchmarkError(
                "CpuBenchmarkScenario.radices must contain six integers."
            ) from None
        if len(radices) != len(GEAR_SLOT_ORDER):
            raise CpuBenchmarkError("CpuBenchmarkScenario.radices must contain six integers.")
        batch_size = _integer(
            self.batch_size,
            "CpuBenchmarkScenario.batch_size",
            minimum=1,
        )
        try:
            workload = (
                self.workload
                if isinstance(self.workload, CpuBenchmarkWorkload)
                else CpuBenchmarkWorkload(self.workload)
            )
        except (TypeError, ValueError):
            raise CpuBenchmarkError(
                "CpuBenchmarkScenario.workload must be a supported benchmark workload."
            ) from None
        object.__setattr__(self, "scenario_id", scenario_id)
        object.__setattr__(self, "radices", radices)
        object.__setattr__(self, "batch_size", batch_size)
        object.__setattr__(self, "workload", workload)

    @property
    def total_permutations(self) -> int:
        return math.prod(self.radices)

    @property
    def expected_batch_count(self) -> int:
        return math.ceil(self.total_permutations / self.batch_size)

    @property
    def expected_exact_candidates(self) -> int:
        if self.workload is CpuBenchmarkWorkload.FULL_EXACT_REJECTED:
            return self.total_permutations
        return 1


CPU_BENCHMARK_SCENARIOS = (
    CpuBenchmarkScenario(
        scenario_id="small",
        radices=(2, 2, 2, 2, 2, 2),
        batch_size=32,
        workload=CpuBenchmarkWorkload.FULL_EXACT_REJECTED,
    ),
    CpuBenchmarkScenario(
        scenario_id="medium",
        radices=(5, 5, 5, 5, 5, 5),
        batch_size=512,
        workload=CpuBenchmarkWorkload.FULL_EXACT_REJECTED,
    ),
    CpuBenchmarkScenario(
        scenario_id="broad",
        radices=(10, 10, 10, 10, 10, 10),
        batch_size=8192,
        workload=CpuBenchmarkWorkload.SPARSE_EXACT_REJECTED,
    ),
)
CPU_BENCHMARK_SCENARIOS_BY_ID = {
    scenario.scenario_id: scenario for scenario in CPU_BENCHMARK_SCENARIOS
}


@dataclass(frozen=True, slots=True)
class CpuBenchmarkRecord:
    """Validated scenario result with timing isolated from deterministic evidence."""

    scenario: CpuBenchmarkScenario
    searched_permutations: int
    completed_batch_count: int
    exact_set_candidates: int
    hard_bound_rejected_count: int
    match_count: int
    state: CpuSearchTerminalState
    elapsed_seconds: float

    def __post_init__(self) -> None:
        if not isinstance(self.scenario, CpuBenchmarkScenario):
            raise CpuBenchmarkError("CpuBenchmarkRecord.scenario must be a benchmark scenario.")
        searched = _integer(
            self.searched_permutations,
            "CpuBenchmarkRecord.searched_permutations",
        )
        batches = _integer(
            self.completed_batch_count,
            "CpuBenchmarkRecord.completed_batch_count",
        )
        exact = _integer(
            self.exact_set_candidates,
            "CpuBenchmarkRecord.exact_set_candidates",
        )
        rejected = _integer(
            self.hard_bound_rejected_count,
            "CpuBenchmarkRecord.hard_bound_rejected_count",
        )
        matches = _integer(self.match_count, "CpuBenchmarkRecord.match_count")
        try:
            state = (
                self.state
                if isinstance(self.state, CpuSearchTerminalState)
                else CpuSearchTerminalState(self.state)
            )
        except (TypeError, ValueError):
            raise CpuBenchmarkError(
                "CpuBenchmarkRecord.state must be a CPU terminal state."
            ) from None
        elapsed = _number(self.elapsed_seconds, "CpuBenchmarkRecord.elapsed_seconds")
        expected = self.scenario
        if (
            searched != expected.total_permutations
            or batches != expected.expected_batch_count
            or exact != expected.expected_exact_candidates
            or rejected != exact
            or matches != 0
            or state is not CpuSearchTerminalState.COMPLETED
        ):
            raise CpuBenchmarkError(
                "CpuBenchmarkRecord does not match the scenario's deterministic completed evidence."
            )
        object.__setattr__(self, "searched_permutations", searched)
        object.__setattr__(self, "completed_batch_count", batches)
        object.__setattr__(self, "exact_set_candidates", exact)
        object.__setattr__(self, "hard_bound_rejected_count", rejected)
        object.__setattr__(self, "match_count", matches)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "elapsed_seconds", elapsed)

    @property
    def permutations_per_second(self) -> float:
        if self.elapsed_seconds == 0:
            return 0.0
        return self.searched_permutations / self.elapsed_seconds

    def deterministic_evidence(self) -> dict[str, object]:
        return {
            "benchmarkId": BENCHMARK_ID,
            "schemaVersion": BENCHMARK_SCHEMA_VERSION,
            "recordType": "scenario",
            "scenario": self.scenario.scenario_id,
            "workload": self.scenario.workload.value,
            "radices": list(self.scenario.radices),
            "totalPermutations": self.scenario.total_permutations,
            "searchedPermutations": self.searched_permutations,
            "batchSize": self.scenario.batch_size,
            "completedBatchCount": self.completed_batch_count,
            "exactSetCandidates": self.exact_set_candidates,
            "hardBoundRejectedCount": self.hard_bound_rejected_count,
            "matchCount": self.match_count,
            "state": self.state.value,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self.deterministic_evidence(),
            "elapsedSeconds": self.elapsed_seconds,
            "permutationsPerSecond": self.permutations_per_second,
        }


_MAIN_STATS = {
    GearSlot.WEAPON: (ItemStatType.FLAT_ATTACK, 500),
    GearSlot.HELMET: (ItemStatType.FLAT_HEALTH, 2500),
    GearSlot.ARMOR: (ItemStatType.FLAT_DEFENSE, 300),
    GearSlot.NECKLACE: (ItemStatType.CRITICAL_HIT_DAMAGE_PERCENT, 65),
    GearSlot.RING: (ItemStatType.EFFECTIVENESS_PERCENT, 65),
    GearSlot.BOOTS: (ItemStatType.SPEED, 45),
}
_REQUIRED_SET_BY_SLOT = {
    GearSlot.WEAPON: GearSet.SPEED,
    GearSlot.HELMET: GearSet.SPEED,
    GearSlot.ARMOR: GearSet.SPEED,
    GearSlot.NECKLACE: GearSet.SPEED,
    GearSlot.RING: GearSet.HEALTH,
    GearSlot.BOOTS: GearSet.HEALTH,
}


def _synthetic_gear_row(
    scenario: CpuBenchmarkScenario,
    slot: GearSlot,
    item_index: int,
) -> dict[str, object]:
    main_stat, main_value = _MAIN_STATS[slot]
    correct_set = _REQUIRED_SET_BY_SLOT[slot]
    gear_set = (
        correct_set
        if scenario.workload is CpuBenchmarkWorkload.FULL_EXACT_REJECTED
        or item_index == 0
        else GearSet.ATTACK
    )
    substat = (
        ItemStatType.ATTACK_PERCENT
        if main_stat is ItemStatType.SPEED
        else ItemStatType.SPEED
    )
    return {
        "ingameId": f"benchmark.{scenario.scenario_id}.{slot.name.lower()}.{item_index:03d}",
        "gear": gear_slot_fribbels_name(slot),
        "rank": "Epic",
        "set": gear_set_fribbels_name(gear_set),
        "enhance": 15,
        "level": 85,
        "main": {
            "type": item_stat_fribbels_name(main_stat),
            "value": main_value,
            "reforgedValue": main_value,
        },
        "substats": [
            {
                "type": item_stat_fribbels_name(substat),
                "value": 4 + item_index % 4,
                "reforgedValue": 6 + item_index % 4,
            }
        ],
        "locked": False,
    }


def build_synthetic_benchmark_inputs(scenario: CpuBenchmarkScenario):
    """Build cold public P04 inputs from an in-memory Fribbels-shaped inventory."""

    if not isinstance(scenario, CpuBenchmarkScenario):
        raise CpuBenchmarkError("scenario must be a CpuBenchmarkScenario.")
    profile = load_bundled_character_profile_selector().create_default_selection(
        "hero.fribbels.ras"
    )
    request = OptimizationRequest(
        request_id=f"benchmark.cpu-exact.{scenario.scenario_id}",
        hero_id=profile.hero_id,
        base_profile_id=profile.profile_id,
        modifiers=HeroModifiers(),
        set_pattern=SetPattern((GearSet.SPEED, GearSet.HEALTH)),
        stat_ranges=((FinalStat.ATTACK, StatRange(minimum=IMPOSSIBLE_ATTACK_MINIMUM)),),
        target_defense=1500,
        skill_contexts=tuple(SkillContext(skill, 1500) for skill in SkillSlot),
        result_cap=MAX_RESULT_CAP,
        item_projection_mode=ItemProjectionMode.CURRENT,
    )
    rows = [
        _synthetic_gear_row(scenario, slot, item_index)
        for slot, radix in zip(GEAR_SLOT_ORDER, scenario.radices, strict=True)
        for item_index in range(radix)
    ]
    parsed = parse_fribbels_gear_bytes(
        json.dumps({"items": rows}, separators=(",", ":")).encode("utf-8")
    )
    if parsed.rejections:
        raise CpuBenchmarkError(
            f"synthetic Fribbels inventory was rejected: {parsed.rejections!r}"
        )
    inventory = merge_fribbels_inventory((), parsed).items
    arrays = prepare_search_slot_arrays(request, profile, inventory)
    actual_radices = tuple(len(slot.dense_ids) for slot in arrays.slots)
    if actual_radices != scenario.radices:
        raise CpuBenchmarkError(
            f"prepared radices must be {scenario.radices!r}; found {actual_radices!r}."
        )
    skills = load_bundled_skill_context_repository().select(
        request.hero_id,
        request.skill_contexts,
    )
    evaluation = compile_exact_build_context(
        request,
        profile,
        ArtifactSelection(),
        skills,
        compile_set_pattern(request.set_pattern),
    )
    return arrays, evaluation, compile_match_counting_context(request)


def run_benchmark_scenario(
    scenario: CpuBenchmarkScenario,
    *,
    clock: Clock | None = None,
) -> CpuBenchmarkRecord:
    """Run one complete synthetic search; production CLI calls use the real clock."""

    arrays, evaluation, counting = build_synthetic_benchmark_inputs(scenario)
    if clock is None:
        result = run_exact_cpu_search(
            arrays,
            evaluation,
            counting,
            batch_size=scenario.batch_size,
            should_cancel=lambda: False,
        )
    else:
        result = run_exact_cpu_search(
            arrays,
            evaluation,
            counting,
            batch_size=scenario.batch_size,
            should_cancel=lambda: False,
            clock=clock,
        )
    return CpuBenchmarkRecord(
        scenario=scenario,
        searched_permutations=result.searched_permutations,
        completed_batch_count=result.completed_batch_count,
        exact_set_candidates=result.exact_set_candidates,
        hard_bound_rejected_count=result.hard_bound_rejected_count,
        match_count=result.counting.detected_count,
        state=result.state,
        elapsed_seconds=result.elapsed_seconds,
    )


def environment_record() -> dict[str, object]:
    """Return interpreter/platform evidence once per benchmark invocation."""

    return {
        "benchmarkId": BENCHMARK_ID,
        "schemaVersion": BENCHMARK_SCHEMA_VERSION,
        "recordType": "environment",
        "pythonImplementation": platform.python_implementation(),
        "pythonVersion": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor() or "unknown",
    }


def select_scenarios(scenario_ids: Sequence[str] | None) -> tuple[CpuBenchmarkScenario, ...]:
    """Resolve requested scenarios while rejecting unknown or duplicate IDs."""

    if not scenario_ids:
        return CPU_BENCHMARK_SCENARIOS
    selected: list[CpuBenchmarkScenario] = []
    seen: set[str] = set()
    for index, supplied in enumerate(scenario_ids):
        scenario_id = _text(supplied, f"scenario_ids[{index}]")
        if scenario_id in seen:
            raise CpuBenchmarkError(f"duplicate benchmark scenario: {scenario_id}.")
        try:
            scenario = CPU_BENCHMARK_SCENARIOS_BY_ID[scenario_id]
        except KeyError:
            raise CpuBenchmarkError(f"unknown benchmark scenario: {scenario_id}.") from None
        seen.add(scenario_id)
        selected.append(scenario)
    return tuple(selected)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark the exact CPU optimizer with synthetic in-memory inventories."
    )
    parser.add_argument(
        "--scenario",
        action="append",
        choices=tuple(CPU_BENCHMARK_SCENARIOS_BY_ID),
        dest="scenario_ids",
        help="Run one scenario; repeat to run multiple. Defaults to small, medium, broad.",
    )
    return parser.parse_args(argv)


def emit_json_lines(records: Sequence[dict[str, object]]) -> None:
    for record in records:
        print(json.dumps(record, sort_keys=True, separators=(",", ":")))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    scenarios = select_scenarios(args.scenario_ids)
    records = [environment_record()]
    records.extend(run_benchmark_scenario(scenario).to_dict() for scenario in scenarios)
    emit_json_lines(records)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
