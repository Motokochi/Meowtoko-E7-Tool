from __future__ import annotations

import ast
import inspect
import json
import subprocess
import sys
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from src.optimizer.cuda import (
    CUDA_INPUT_FIELD_NAMES,
    CUDA_INPUT_LAYOUT,
    CUDA_SIGNED_INT32_MAX,
    CudaDeviceBufferCache,
    CudaHostInputs,
    CudaInputError,
    compile_cuda_host_inputs,
    transfer_cuda_inputs,
    validate_cuda_search_dimensions,
)
from src.optimizer.cuda import inputs as inputs_module
from src.optimizer.cuda.runtime import (
    CudaDiagnosticStatus,
    CudaExecutionMode,
    CudaRuntimeDiagnostic,
)
from src.optimizer.data import (
    ArtifactSelection,
    load_bundled_character_profile_selector,
    load_bundled_skill_context_repository,
    merge_fribbels_inventory,
    parse_fribbels_gear_bytes,
)
from src.optimizer.domain import (
    FINAL_STAT_ORDER,
    GEAR_SLOT_ORDER,
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
from src.optimizer.engine import calculate_item_priority_score
from src.optimizer.search import (
    compile_exact_build_context,
    compile_set_pattern,
    prepare_search_slot_arrays,
)


ROOT = Path(__file__).resolve().parents[1]

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
    substat = ItemStatType.ATTACK_PERCENT if slot is GearSlot.BOOTS else ItemStatType.SPEED
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


def _ready_diagnostic() -> CudaRuntimeDiagnostic:
    return CudaRuntimeDiagnostic(
        status=CudaDiagnosticStatus.READY,
        mode=CudaExecutionMode.CUDA,
        available=True,
        disabled=False,
        summary="CUDA device is ready.",
        cupy_version="14.1.1",
        device_count=1,
        selected_device_index=0,
        device_name="Fake RTX 5090",
        free_vram_bytes=30 << 30,
        total_vram_bytes=32 << 30,
        driver_version=13030,
        runtime_version=13020,
        allocation_probe_bytes=1 << 20,
        allocation_probe_succeeded=True,
    )


def _disabled_diagnostic() -> CudaRuntimeDiagnostic:
    return CudaRuntimeDiagnostic(
        status=CudaDiagnosticStatus.DISABLED,
        mode=CudaExecutionMode.CPU,
        available=False,
        disabled=True,
        summary="CUDA was disabled.",
    )


class _FakeDevice:
    def __init__(self, api: "FakeArrayApi", index: int) -> None:
        self.api = api
        self.index = index

    def __enter__(self):
        self.api.device_entries.append(self.index)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.api.device_exits.append(self.index)


class FakeArrayApi:
    """The small CuPy-compatible surface consumed by the transfer adapter."""

    def __init__(self, *, fail_empty_at: int | None = None, fail_copy_at: int | None = None) -> None:
        self.fail_empty_at = fail_empty_at
        self.fail_copy_at = fail_copy_at
        self.empty_calls = 0
        self.copy_calls = 0
        self.released: list[int] = []
        self.device_entries: list[int] = []
        self.device_exits: list[int] = []
        self.cuda = SimpleNamespace(Device=lambda index: _FakeDevice(self, index))

    def empty(self, shape, *, dtype):
        self.empty_calls += 1
        if self.empty_calls == self.fail_empty_at:
            raise MemoryError("forced allocation failure")
        return np.empty(shape, dtype=dtype)

    def copyto(self, destination, source) -> None:
        self.copy_calls += 1
        if self.copy_calls == self.fail_copy_at:
            raise RuntimeError("forced copy failure")
        np.copyto(destination, source)

    def asnumpy(self, value):
        return np.array(value, copy=True)

    def release(self, value) -> None:
        self.released.append(id(value))


class _DirectSetArray:
    """Small stand-in for the CuPy ndarray host-transfer surface."""

    def __init__(self, api: "DirectSetArrayApi", shape, dtype) -> None:
        self._api = api
        self.values = np.empty(shape, dtype=dtype)
        self.shape = self.values.shape
        self.dtype = self.values.dtype

    def set(self, source) -> None:
        self._api.set_calls += 1
        np.copyto(self.values, source)


class DirectSetArrayApi(FakeArrayApi):
    def __init__(self) -> None:
        super().__init__()
        self.set_calls = 0

    def empty(self, shape, *, dtype):
        self.empty_calls += 1
        return _DirectSetArray(self, shape, dtype)

    def copyto(self, destination, source) -> None:
        raise AssertionError("direct-set destinations must not use array-api copyto")

    def asnumpy(self, value):
        return np.array(value.values, copy=True)


class CudaInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.profile = load_bundled_character_profile_selector().create_default_selection(
            "hero.fribbels.ras"
        )
        cls.skills = load_bundled_skill_context_repository()

    def _fixture(self, *, extra_boots: bool = False):
        request = OptimizationRequest(
            request_id="request.cuda.inputs.extra" if extra_boots else "request.cuda.inputs",
            hero_id=self.profile.hero_id,
            base_profile_id=self.profile.profile_id,
            modifiers=HeroModifiers(),
            set_pattern=SetPattern((GearSet.SPEED, GearSet.HEALTH)),
            stat_ranges=(
                (FinalStat.ATTACK, StatRange(minimum=1000)),
                (FinalStat.SPEED, StatRange(maximum=999)),
            ),
            stat_priorities=((FinalStat.ATTACK, 3), (FinalStat.SPEED, -1)),
            derived_metric_ranges=(("metric.ehp", StatRange(minimum=1)),),
            target_defense=1500,
            skill_contexts=tuple(SkillContext(skill, 1500) for skill in SkillSlot),
            near_set_tolerance=0.1,
            maximum_replacement_distance=2,
            item_projection_mode=ItemProjectionMode.CURRENT,
        )
        sets = (
            GearSet.SPEED,
            GearSet.SPEED,
            GearSet.HEALTH,
            GearSet.SPEED,
            GearSet.SPEED,
            GearSet.HEALTH,
        )
        rows = [
            _gear_row(f"cuda.{slot.value}", slot, gear_set)
            for slot, gear_set in zip(GEAR_SLOT_ORDER, sets, strict=True)
        ]
        if extra_boots:
            rows.append(_gear_row("cuda.boots.extra", GearSlot.BOOTS, GearSet.ATTACK))
        parsed = parse_fribbels_gear_bytes(json.dumps({"items": rows}).encode("utf-8"))
        self.assertEqual((), parsed.rejections)
        inventory = merge_fribbels_inventory((), parsed).items
        arrays = prepare_search_slot_arrays(request, self.profile, inventory)
        exact = compile_exact_build_context(
            request,
            self.profile,
            ArtifactSelection(),
            self.skills.select(request.hero_id, request.skill_contexts),
            compile_set_pattern(request.set_pattern),
        )
        return arrays, exact, compile_cuda_host_inputs(arrays, exact)

    def test_compiles_every_numeric_field_with_canonical_shapes_dtypes_and_masks(self) -> None:
        arrays, context, host = self._fixture()

        self.assertEqual(CUDA_INPUT_FIELD_NAMES, tuple(item.name for item in host.arrays))
        self.assertEqual(len(CUDA_INPUT_LAYOUT), len(host.arrays))
        self.assertEqual(6, host.item_count)
        self.assertEqual(1, host.total_permutations)
        self.assertEqual((0, 1, 2, 3, 4, 5), tuple(host.array("slot_offsets")))
        self.assertEqual((1,) * 6, tuple(host.array("slot_radices")))
        self.assertEqual(tuple(range(6)), tuple(host.array("dense_item_ids")))
        self.assertEqual((6, len(FINAL_STAT_ORDER)), host.array("item_stat_contributions").shape)
        self.assertEqual(np.dtype("<f4"), host.array("item_stat_contributions").dtype)
        self.assertEqual(np.dtype("<i4"), host.array("dense_item_ids").dtype)
        self.assertEqual(np.dtype("<i8"), host.array("total_permutations").dtype)
        self.assertEqual(np.dtype("u1"), host.array("item_set_indices").dtype)
        self.assertEqual((1, 0, 0, 0, 0, 0, 0, 0), tuple(host.array("primary_minimum_present")))
        self.assertEqual((0, 0, 0, 1, 0, 0, 0, 0), tuple(host.array("primary_maximum_present")))
        self.assertEqual(0, host.array("primary_minimum_values")[1])
        self.assertEqual(1, sum(host.array("derived_minimum_present")))
        self.assertEqual(0, sum(host.array("derived_maximum_present")))
        expected_priorities = tuple(
            calculate_item_priority_score(
                context.base_stats,
                context.priorities,
                row,
            )[1]
            for slot in arrays.slots
            for row in slot.final_stat_contributions
        )
        self.assertEqual(expected_priorities, tuple(host.array("item_priority_scores")))
        self.assertEqual(24, len(host.array("set_pieces_required")))
        self.assertEqual(24, len(host.array("set_stackable_flags")))
        self.assertEqual((24, 8), host.array("set_unit_numeric_contributions").shape)
        self.assertGreater(host.byte_count, 0)
        self.assertFalse(any("id" in item.name and item.name != "dense_item_ids" for item in host.arrays))
        self.assertEqual(arrays.total_items, host.item_count)

    def test_host_record_is_deeply_immutable_hashable_and_repeatable(self) -> None:
        arrays, context, first = self._fixture()
        second = compile_cuda_host_inputs(arrays, context)

        self.assertEqual(first, second)
        self.assertEqual(hash(first), hash(second))
        with self.assertRaises(FrozenInstanceError):
            first.arrays = ()  # type: ignore[misc]
        selected = first.array("base_stats")
        with self.assertRaises(ValueError):
            selected[0] = 0
        with self.assertRaises(ValueError):
            selected.setflags(write=True)

    def test_slot_boundaries_and_fixed_width_overflow_are_actionable(self) -> None:
        dimensions = validate_cuda_search_dimensions((CUDA_SIGNED_INT32_MAX - 4, 1, 1, 1, 1, 1))
        self.assertEqual(CUDA_SIGNED_INT32_MAX + 1, dimensions.total_items)
        self.assertEqual(CUDA_SIGNED_INT32_MAX, dimensions.slot_offsets[-1])
        with self.assertRaises(CudaInputError) as empty:
            validate_cuda_search_dimensions((1, 1, 1, 1, 1, 0))
        self.assertEqual("empty-cuda-slot", empty.exception.code)
        with self.assertRaises(CudaInputError) as permutations:
            validate_cuda_search_dimensions((100_000,) * 6)
        self.assertEqual("permutation-total-overflow", permutations.exception.code)
        with self.assertRaises(CudaInputError) as dense:
            validate_cuda_search_dimensions((CUDA_SIGNED_INT32_MAX - 3, 1, 1, 1, 1, 1))
        self.assertEqual("dense-id-overflow", dense.exception.code)

    def test_compiler_rejects_identity_mismatch_and_integer_or_binary32_narrowing(self) -> None:
        arrays, context, _ = self._fixture()
        mismatched_exact = replace(context, request_id="request.other")
        with self.assertRaises(CudaInputError) as mismatch:
            compile_cuda_host_inputs(arrays, mismatched_exact)
        self.assertEqual("search-context-mismatch", mismatch.exception.code)

        bad_score_slot = replace(arrays.slots[0], gear_scores=(CUDA_SIGNED_INT32_MAX + 1,))
        bad_scores = replace(arrays, slots=(bad_score_slot,) + arrays.slots[1:])
        with self.assertRaises(CudaInputError) as score:
            compile_cuda_host_inputs(bad_scores, context)
        self.assertEqual("integer-overflow", score.exception.code)

        contribution = (1e100,) + arrays.slots[0].final_stat_contributions[0][1:]
        bad_float_slot = replace(arrays.slots[0], final_stat_contributions=(contribution,))
        bad_floats = replace(arrays, slots=(bad_float_slot,) + arrays.slots[1:])
        with self.assertRaises(CudaInputError) as binary32:
            compile_cuda_host_inputs(bad_floats, context)
        self.assertEqual("binary32-overflow", binary32.exception.code)

    def test_every_field_round_trips_without_shape_dtype_or_value_changes(self) -> None:
        _, _, host = self._fixture()
        api = FakeArrayApi()
        cache = CudaDeviceBufferCache(array_api_loader=lambda: api)

        with cache.transfer(host, _ready_diagnostic()) as device:
            round_trip = device.to_host()
            self.assertEqual(host.byte_count, device.byte_count)
            self.assertEqual(host.layout_signature, round_trip.layout_signature)
            for expected, actual in zip(host.arrays, round_trip.arrays, strict=True):
                self.assertEqual(expected.dtype, actual.dtype, expected.name)
                self.assertEqual(expected.shape, actual.shape, expected.name)
                self.assertTrue(np.array_equal(expected.values, actual.values), expected.name)
        cache.close()
        self.assertEqual(len(CUDA_INPUT_FIELD_NAMES), len(api.released))
        self.assertEqual(api.device_entries, api.device_exits)

    def test_cupy_style_direct_set_accepts_numpy_sources_and_reuse(self) -> None:
        _, _, host = self._fixture()
        api = DirectSetArrayApi()
        cache = CudaDeviceBufferCache(array_api_loader=lambda: api)

        first = cache.transfer(host, _ready_diagnostic())
        self.assertEqual(host, first.to_host())
        first.release()
        second = cache.transfer(host, _ready_diagnostic())
        self.assertEqual(host, second.to_host())
        second.release()

        self.assertEqual(2 * len(CUDA_INPUT_FIELD_NAMES), api.set_calls)
        self.assertEqual(1, cache.reuse_count)
        cache.close()

    def test_exact_layout_reuses_allocations_and_changed_layout_replaces_them(self) -> None:
        arrays, context, host = self._fixture()
        _, _, changed = self._fixture(extra_boots=True)
        api = FakeArrayApi()
        cache = CudaDeviceBufferCache(array_api_loader=lambda: api)

        first = cache.transfer(host, _ready_diagnostic())
        first_ids = tuple(id(item.value) for item in first.arrays)
        first.release()
        first_row = arrays.slots[0].final_stat_contributions[0]
        updated_slot = replace(
            arrays.slots[0],
            final_stat_contributions=((first_row[0] + 1,) + first_row[1:],),
        )
        updated_host = compile_cuda_host_inputs(
            replace(arrays, slots=(updated_slot,) + arrays.slots[1:]),
            context,
        )
        second = cache.transfer(updated_host, _ready_diagnostic())
        self.assertEqual(first_ids, tuple(id(item.value) for item in second.arrays))
        self.assertEqual(updated_host, second.to_host())
        second.release()
        self.assertEqual(len(CUDA_INPUT_FIELD_NAMES), cache.allocation_count)
        self.assertEqual(1, cache.reuse_count)

        third = cache.transfer(changed, _ready_diagnostic())
        self.assertNotEqual(first_ids, tuple(id(item.value) for item in third.arrays))
        self.assertEqual(1, cache.replacement_count)
        self.assertEqual(len(CUDA_INPUT_FIELD_NAMES), len(api.released))
        third.release()
        cache.close()
        self.assertEqual(2 * len(CUDA_INPUT_FIELD_NAMES), len(api.released))

    def test_live_lease_blocks_reuse_replacement_and_close(self) -> None:
        _, _, host = self._fixture()
        _, _, changed = self._fixture(extra_boots=True)
        cache = CudaDeviceBufferCache(array_api_loader=lambda: FakeArrayApi())
        lease = cache.transfer(host, _ready_diagnostic())
        for candidate in (host, changed):
            with self.subTest(item_count=candidate.item_count):
                with self.assertRaises(CudaInputError) as blocked:
                    cache.transfer(candidate, _ready_diagnostic())
                self.assertEqual("device-buffers-in-use", blocked.exception.code)
        with self.assertRaises(CudaInputError):
            cache.close()
        lease.release()
        with self.assertRaises(CudaInputError) as released:
            lease.to_host()
        self.assertEqual("device-lease-released", released.exception.code)
        cache.close()

    def test_nonready_evidence_never_loads_or_allocates_array_api(self) -> None:
        _, _, host = self._fixture()
        loaded: list[bool] = []
        cache = CudaDeviceBufferCache(
            array_api_loader=lambda: loaded.append(True) or FakeArrayApi()
        )
        with self.assertRaises(CudaInputError) as error:
            cache.transfer(host, _disabled_diagnostic())
        self.assertEqual("cuda-not-ready", error.exception.code)
        self.assertEqual([], loaded)
        cache.close()

    def test_allocation_and_copy_failures_release_partial_new_state(self) -> None:
        _, _, host = self._fixture()
        allocation_api = FakeArrayApi(fail_empty_at=3)
        allocation_cache = CudaDeviceBufferCache(array_api_loader=lambda: allocation_api)
        with self.assertRaises(CudaInputError) as allocation:
            allocation_cache.transfer(host, _ready_diagnostic())
        self.assertEqual("device-allocation-failed", allocation.exception.code)
        self.assertEqual(2, len(allocation_api.released))
        self.assertFalse(allocation_cache.has_active_lease)
        allocation_cache.close()

        copy_api = FakeArrayApi(fail_copy_at=3)
        copy_cache = CudaDeviceBufferCache(array_api_loader=lambda: copy_api)
        with self.assertRaises(CudaInputError) as transfer:
            copy_cache.transfer(host, _ready_diagnostic())
        self.assertEqual("device-transfer-failed", transfer.exception.code)
        self.assertEqual(len(CUDA_INPUT_FIELD_NAMES), len(copy_api.released))
        self.assertFalse(copy_cache.has_active_lease)
        copy_cache.close()

        reuse_api = FakeArrayApi()
        reuse_cache = CudaDeviceBufferCache(array_api_loader=lambda: reuse_api)
        seeded = reuse_cache.transfer(host, _ready_diagnostic())
        seeded.release()
        reuse_api.fail_copy_at = reuse_api.copy_calls + 3
        with self.assertRaises(CudaInputError) as reused_copy:
            reuse_cache.transfer(host, _ready_diagnostic())
        self.assertEqual("device-transfer-failed", reused_copy.exception.code)
        self.assertEqual(len(CUDA_INPUT_FIELD_NAMES), len(reuse_api.released))
        self.assertFalse(reuse_cache.has_active_lease)
        reuse_cache.close()

    def test_standalone_transfer_owns_and_releases_its_cache(self) -> None:
        _, _, host = self._fixture()
        api = FakeArrayApi()
        lease = transfer_cuda_inputs(
            host,
            _ready_diagnostic(),
            array_api_loader=lambda: api,
        )
        self.assertFalse(lease.released)
        lease.release()
        self.assertTrue(lease.released)
        self.assertEqual(len(CUDA_INPUT_FIELD_NAMES), len(api.released))

    def test_module_is_lazy_public_cpu_isolated_and_frozen_package_visible(self) -> None:
        source = inspect.getsource(inputs_module)
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
        self.assertNotIn("cupy", imports)
        isolated = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; import src.optimizer.cuda.runtime; "
                    "print(any(name.startswith('src.optimizer.search') for name in sys.modules))"
                ),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual("False", isolated.stdout.strip())
        for search_module in (
            "src.optimizer.search.cartesian",
            "src.optimizer.search.exact_evaluation",
            "src.optimizer.search.slot_arrays",
        ):
            module = __import__(search_module, fromlist=["unused"])
            self.assertNotIn("src.optimizer.cuda", inspect.getsource(module))

        from src.optimizer import cuda

        for name in (
            "CudaHostInputs",
            "CudaDeviceBufferCache",
            "compile_cuda_host_inputs",
            "transfer_cuda_inputs",
        ):
            self.assertIn(name, cuda.__all__)
            self.assertIs(getattr(cuda, name), getattr(inputs_module, name))
        spec = (ROOT / "packaging" / "e7-core.spec").read_text(encoding="utf-8")
        self.assertIn('"src.optimizer.cuda.inputs"', spec)
        self.assertIn('"cupy"', spec)


if __name__ == "__main__":
    unittest.main()
