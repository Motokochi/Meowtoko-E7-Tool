from __future__ import annotations

import ast
import inspect
import json
import unittest
from dataclasses import FrozenInstanceError, replace
from unittest.mock import patch

from src.optimizer.data import (
    ArtifactSelection,
    load_bundled_character_profile_selector,
    load_bundled_skill_context_repository,
    merge_fribbels_inventory,
    parse_fribbels_gear_bytes,
)
from src.optimizer.domain import (
    GEAR_SLOT_ORDER,
    ExecutionPreference,
    GearSet,
    GearSlot,
    HeroModifiers,
    ItemProjectionMode,
    ItemStatType,
    OptimizationRequest,
    SetPattern,
    SkillContext,
    SkillSlot,
    gear_set_fribbels_name,
    gear_slot_fribbels_name,
    item_stat_fribbels_name,
)
from src.optimizer.search import (
    CartesianEnumerationSummary,
    CpuExactSearchProgress,
    CpuSearchOrchestrationError,
    CpuSearchTerminalState,
    ExactBuildEvaluationError,
    collect_exact_build_matches,
    compile_exact_build_context,
    compile_match_counting_context,
    compile_set_pattern,
    create_cartesian_search_space,
    evaluate_exact_build_batch,
    iter_cartesian_batches,
    prepare_search_slot_arrays,
    run_exact_cpu_search,
)
from src.optimizer.search import cpu_orchestration as orchestration_module


_MAIN_STATS = {
    GearSlot.WEAPON: (ItemStatType.FLAT_ATTACK, 500),
    GearSlot.HELMET: (ItemStatType.FLAT_HEALTH, 2500),
    GearSlot.ARMOR: (ItemStatType.FLAT_DEFENSE, 300),
    GearSlot.NECKLACE: (ItemStatType.CRITICAL_HIT_DAMAGE_PERCENT, 65),
    GearSlot.RING: (ItemStatType.EFFECTIVENESS_PERCENT, 65),
    GearSlot.BOOTS: (ItemStatType.SPEED, 45),
}


def _gear_row(item_id: str, slot: GearSlot, gear_set: GearSet) -> dict[str, object]:
    main_stat, main_value = _MAIN_STATS[slot]
    substat = (
        ItemStatType.ATTACK_PERCENT
        if main_stat is ItemStatType.SPEED
        else ItemStatType.SPEED
    )
    return {
        "ingameId": item_id,
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
                "value": 4,
                "reforgedValue": 6,
            }
        ],
        "locked": False,
    }


def _clock(*values: float):
    supplied = iter(values)

    def read() -> float:
        try:
            return next(supplied)
        except StopIteration:
            raise AssertionError("clock read beyond the documented boundary") from None

    return read


class CpuOrchestrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.profile = load_bundled_character_profile_selector().create_default_selection(
            "hero.fribbels.ras"
        )
        cls.skills = load_bundled_skill_context_repository()

    def _fixture(self, *, cap: int = 10):
        request = OptimizationRequest(
            request_id=f"request.cpu-orchestration.{cap}",
            hero_id=self.profile.hero_id,
            base_profile_id=self.profile.profile_id,
            modifiers=HeroModifiers(),
            set_pattern=SetPattern((GearSet.SPEED, GearSet.HEALTH)),
            target_defense=1500,
            skill_contexts=tuple(SkillContext(skill, 1500) for skill in SkillSlot),
            result_cap=cap,
            item_projection_mode=ItemProjectionMode.CURRENT,
        )
        sets = {
            GearSlot.WEAPON: GearSet.SPEED,
            GearSlot.HELMET: GearSet.SPEED,
            GearSlot.ARMOR: GearSet.SPEED,
            GearSlot.NECKLACE: GearSet.SPEED,
            GearSlot.RING: GearSet.HEALTH,
            GearSlot.BOOTS: GearSet.HEALTH,
        }
        rows = [
            _gear_row(f"base.{slot.name.lower()}", slot, sets[slot])
            for slot in GEAR_SLOT_ORDER
        ]
        rows.append(_gear_row("extra.weapon", GearSlot.WEAPON, GearSet.SPEED))
        rows.extend(
            _gear_row(f"extra.boots.{index}", GearSlot.BOOTS, GearSet.HEALTH)
            for index in range(2)
        )
        parsed = parse_fribbels_gear_bytes(json.dumps({"items": rows}).encode("utf-8"))
        self.assertEqual((), parsed.rejections)
        inventory = merge_fribbels_inventory((), parsed).items
        arrays = prepare_search_slot_arrays(request, self.profile, inventory)
        evaluation = compile_exact_build_context(
            request,
            self.profile,
            ArtifactSelection(),
            self.skills.select(request.hero_id, request.skill_contexts),
            compile_set_pattern(request.set_pattern),
        )
        return request, arrays, evaluation, compile_match_counting_context(request)

    def _run(self, *, cap: int = 10, **overrides):
        _, arrays, evaluation, counting = self._fixture(cap=cap)
        values = {
            "batch_size": 4,
            "should_cancel": lambda: False,
            "clock": _clock(0, 1, 2),
        }
        values.update(overrides)
        return run_exact_cpu_search(arrays, evaluation, counting, **values)

    def test_cancellation_before_first_batch_never_requests_enumerator(self) -> None:
        _, arrays, evaluation, counting = self._fixture()

        class NeverRequested:
            requests = 0

            def __iter__(self):
                return self

            def __next__(self):
                self.requests += 1
                raise AssertionError("enumerator batch requested after cancellation")

            def snapshot(self, *, cancelled: bool = False):
                return CartesianEnumerationSummary(6, 0, 0.5, False, cancelled)

        enumerator = NeverRequested()
        progress: list[CpuExactSearchProgress] = []
        with patch.object(orchestration_module, "iter_cartesian_batches", return_value=enumerator):
            result = run_exact_cpu_search(
                arrays,
                evaluation,
                counting,
                batch_size=4,
                should_cancel=lambda: True,
                on_progress=progress.append,
            )
        self.assertTrue(result.cancelled)
        self.assertEqual((0, 0, (), 0), (
            result.searched_permutations,
            result.completed_batch_count,
            result.rows,
            enumerator.requests,
        ))
        self.assertEqual([], progress)
        summary = result.to_search_summary()
        self.assertTrue(summary.cancelled)
        self.assertFalse(summary.overflowed)
        self.assertEqual(0, summary.result_count)

    def test_cancellation_after_first_batch_stops_at_exact_boundary(self) -> None:
        checks = iter((False, True))
        progress: list[CpuExactSearchProgress] = []
        result = self._run(
            should_cancel=lambda: next(checks),
            on_progress=progress.append,
            clock=_clock(0, 1, 2),
        )
        self.assertTrue(result.cancelled)
        self.assertEqual((4, 1, 4, 0, ()), (
            result.searched_permutations,
            result.completed_batch_count,
            result.exact_set_candidates,
            result.hard_bound_rejected_count,
            result.rows,
        ))
        self.assertEqual([4], [item.searched_permutations for item in progress])
        summary = result.to_search_summary()
        self.assertEqual(ExecutionPreference.CPU, summary.execution_preference)
        self.assertEqual(4, summary.evaluated_permutations)
        self.assertEqual(0, summary.result_count)
        self.assertTrue(summary.cancelled)

    def test_full_execution_matches_lower_exact_layers(self) -> None:
        _, arrays, evaluation, counting = self._fixture()
        space = create_cartesian_search_space(arrays)
        lower_batches = tuple(
            evaluate_exact_build_batch(evaluation, arrays, batch)
            for batch in iter_cartesian_batches(space, 4, clock=_clock(0, 2))
        )
        lower = collect_exact_build_matches(counting, evaluation, lower_batches)
        result = run_exact_cpu_search(
            arrays,
            evaluation,
            counting,
            batch_size=4,
            should_cancel=lambda: False,
            clock=_clock(0, 1, 2),
        )
        self.assertTrue(result.completed)
        self.assertEqual(space.total_permutations, result.searched_permutations)
        self.assertEqual(lower.rows, result.rows)
        self.assertEqual(lower.counting, result.counting)
        self.assertEqual(lower.exact_set_candidates, result.exact_set_candidates)
        summary = result.to_search_summary()
        self.assertFalse(summary.cancelled)
        self.assertFalse(summary.overflowed)
        self.assertEqual(6, summary.exact_count)

    def test_progress_is_deterministic_for_uneven_final_batch_and_has_no_rows(self) -> None:
        observed: list[CpuExactSearchProgress] = []
        cancellation_checks: list[int] = []

        def continue_search() -> bool:
            cancellation_checks.append(len(cancellation_checks))
            return False

        first = self._run(
            should_cancel=continue_search,
            on_progress=observed.append,
            clock=_clock(0, 1, 2),
        )
        repeated: list[CpuExactSearchProgress] = []
        second = self._run(on_progress=repeated.append, clock=_clock(0, 1, 2))
        self.assertEqual(first, second)
        self.assertEqual(observed, repeated)
        self.assertEqual([4, 6], [item.searched_permutations for item in observed])
        self.assertEqual([1, 2], [item.completed_batch_count for item in observed])
        self.assertEqual([4, 6], [item.exact_set_candidates for item in observed])
        self.assertEqual([4, 6], [item.emitted_match_count for item in observed])
        self.assertEqual([1.0, 2.0], [item.elapsed_seconds for item in observed])
        self.assertEqual([0, 1], cancellation_checks)
        self.assertTrue(all(not hasattr(item, "rows") for item in observed))

    def test_callback_and_predicate_failures_are_actionable(self) -> None:
        def broken_predicate() -> bool:
            raise RuntimeError("predicate boom")

        with self.assertRaises(CpuSearchOrchestrationError) as predicate_error:
            self._run(should_cancel=broken_predicate, clock=_clock(0))
        self.assertEqual("cancellation-predicate-failed", predicate_error.exception.code)
        self.assertIsInstance(predicate_error.exception.__cause__, RuntimeError)

        def broken_callback(_progress: CpuExactSearchProgress) -> None:
            raise RuntimeError("callback boom")

        with self.assertRaises(CpuSearchOrchestrationError) as callback_error:
            self._run(on_progress=broken_callback, clock=_clock(0, 1))
        self.assertEqual("progress-callback-failed", callback_error.exception.code)
        self.assertIsInstance(callback_error.exception.__cause__, RuntimeError)

    def test_overflow_precedes_later_callbacks_and_cancellation(self) -> None:
        checks = 0
        progress: list[CpuExactSearchProgress] = []

        def cancel_later() -> bool:
            nonlocal checks
            checks += 1
            return checks > 1

        result = self._run(
            cap=1,
            should_cancel=cancel_later,
            on_progress=progress.append,
            clock=_clock(0, 1),
        )
        self.assertTrue(result.overflowed)
        self.assertEqual((4, 1, (), 1), (
            result.searched_permutations,
            result.completed_batch_count,
            result.rows,
            checks,
        ))
        self.assertEqual([], progress)
        summary = result.to_search_summary()
        self.assertTrue(summary.overflowed)
        self.assertFalse(summary.cancelled)
        self.assertEqual(0, summary.result_count)

    def test_exactly_cap_at_final_permutation_completes(self) -> None:
        result = self._run(cap=6, clock=_clock(0, 1, 2))
        self.assertTrue(result.completed)
        self.assertEqual(6, result.counting.detected_count)
        self.assertEqual(6, len(result.rows))
        self.assertEqual(6, result.to_search_summary().result_count)

    def test_arguments_context_identities_and_callback_results_are_validated(self) -> None:
        _, arrays, evaluation, counting = self._fixture()
        for batch_size in (True, 0, -1):
            with self.subTest(batch_size=batch_size):
                with self.assertRaises(CpuSearchOrchestrationError):
                    run_exact_cpu_search(
                        arrays,
                        evaluation,
                        counting,
                        batch_size=batch_size,
                        should_cancel=lambda: False,
                    )
        for field, value in (
            ("should_cancel", None),
            ("on_progress", 3),
            ("clock", 3),
        ):
            values = {"batch_size": 4, "should_cancel": lambda: False, field: value}
            with self.subTest(field=field):
                with self.assertRaises(CpuSearchOrchestrationError):
                    run_exact_cpu_search(arrays, evaluation, counting, **values)
        with self.assertRaises(CpuSearchOrchestrationError) as return_error:
            run_exact_cpu_search(
                arrays,
                evaluation,
                counting,
                batch_size=4,
                should_cancel=lambda: 1,  # type: ignore[return-value]
                clock=_clock(0),
            )
        self.assertEqual("invalid-cancellation-result", return_error.exception.code)

        with self.assertRaises(CpuSearchOrchestrationError) as counting_error:
            run_exact_cpu_search(
                arrays,
                evaluation,
                replace(counting, request_id="request.wrong"),
                batch_size=4,
                should_cancel=lambda: False,
            )
        self.assertEqual("request-context-mismatch", counting_error.exception.code)
        for field in ("request_id", "hero_id", "base_profile_id"):
            with self.subTest(array_field=field):
                with self.assertRaises(ExactBuildEvaluationError):
                    run_exact_cpu_search(
                        replace(arrays, **{field: f"wrong.{field}"}),
                        evaluation,
                        counting,
                        batch_size=4,
                        should_cancel=lambda: False,
                    )

    def test_direct_records_reject_impossible_state_and_numeric_evidence(self) -> None:
        valid_progress = CpuExactSearchProgress(
            request_id="request.progress",
            total_permutations=6,
            searched_permutations=4,
            elapsed_seconds=1,
            completed_batch_count=1,
            exact_set_candidates=4,
            hard_bound_rejected_count=1,
            emitted_match_count=3,
        )
        for changes in (
            {"total_permutations": True},
            {"searched_permutations": 0},
            {"elapsed_seconds": float("nan")},
            {"completed_batch_count": False},
            {"exact_set_candidates": 3},
        ):
            with self.subTest(progress_changes=changes):
                with self.assertRaises(CpuSearchOrchestrationError):
                    replace(valid_progress, **changes)

        completed = self._run(clock=_clock(0, 1, 2))
        cancelled = self._run(
            should_cancel=lambda: True,
            clock=_clock(0, 1),
        )
        for source, changes in (
            (completed, {"state": CpuSearchTerminalState.CANCELLED}),
            (completed, {"state": CpuSearchTerminalState.OVERFLOWED, "rows": ()}),
            (completed, {"elapsed_seconds": float("inf")}),
            (completed, {"searched_permutations": True}),
            (cancelled, {"rows": completed.rows}),
            (cancelled, {"searched_permutations": cancelled.total_permutations}),
        ):
            with self.subTest(result_changes=changes):
                with self.assertRaises(CpuSearchOrchestrationError):
                    replace(source, **changes)

    def test_records_are_frozen_hashable_and_module_has_no_forbidden_dependencies(self) -> None:
        progress: list[CpuExactSearchProgress] = []
        result = self._run(on_progress=progress.append, clock=_clock(0, 1, 2))
        self.assertIsInstance(hash(progress[0]), int)
        self.assertIsInstance(hash(result), int)
        with self.assertRaises(FrozenInstanceError):
            progress[0].searched_permutations = 0  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            result.rows = ()  # type: ignore[misc]

        tree = ast.parse(inspect.getsource(orchestration_module))
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
        forbidden = (
            "asyncio",
            "threading",
            "src.desktop",
            "src.ui",
            "src.optimizer.data.repository",
            "src.optimizer.search.cuda",
            "src.optimizer.search.near",
        )
        self.assertFalse(
            any(name == root or name.startswith(f"{root}.") for name in imports for root in forbidden),
            imports,
        )


if __name__ == "__main__":
    unittest.main()
