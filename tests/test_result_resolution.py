from __future__ import annotations

import ast
import inspect
import tempfile
import unittest
from dataclasses import FrozenInstanceError, dataclass, replace
from pathlib import Path
from unittest.mock import patch

import numpy as np

from src.optimizer.data import (
    ArtifactSelection,
    DenseInventorySnapshot,
    load_bundled_character_profile_selector,
    load_bundled_skill_context_repository,
)
from src.optimizer.domain import (
    FINAL_STAT_ORDER,
    FRIBBELS_SET_ORDER,
    GEAR_SLOT_ORDER,
    SET_CATALOG,
    FinalStat,
    GearItem,
    GearSet,
    GearSlot,
    HeroModifiers,
    ItemProjectionMode,
    ItemStatType,
    OptimizationRequest,
    ResultCategory,
    SetPattern,
    SkillContext,
    SkillSlot,
)
from src.optimizer.result_store import (
    MAX_PAGE_SIZE,
    RESULT_RESOLUTION_ID,
    RESULT_RESOLUTION_VERSION,
    CompletedResultRun,
    DenseItemEquippedLookup,
    ResultBuildDetailRequest,
    ResultPage,
    ResultPageRequest,
    ResultPageRowsRequest,
    ResultResolutionError,
    ResultResolverContext,
    ResultRunStore,
    ResultSortRequest,
    build_result_sort_index,
    page_result_sort_index,
    project_result_resolution,
    resolve_result_build_detail,
    resolve_result_page,
    result_columns_from_cpu_rows,
)
from src.optimizer.search import (
    compile_exact_build_context,
    compile_set_pattern,
    create_cartesian_search_space,
    evaluate_exact_build_batch,
    iter_cartesian_batches,
)
from src.optimizer.result_store import resolution as resolution_module
from tests.test_exact_build_evaluation import _arrays, _items


_MAIN_STATS = (
    ItemStatType.FLAT_ATTACK,
    ItemStatType.FLAT_HEALTH,
    ItemStatType.FLAT_DEFENSE,
    ItemStatType.CRITICAL_HIT_DAMAGE_PERCENT,
    ItemStatType.EFFECTIVENESS_PERCENT,
    ItemStatType.SPEED,
)


@dataclass(frozen=True)
class _Fixture:
    temporary: tempfile.TemporaryDirectory[str]
    run: CompletedResultRun
    index: object
    context: ResultResolverContext
    columns: dict[str, np.ndarray]

    def page(self, page_index: int = 0, page_size: int = 100) -> ResultPage:
        return page_result_sort_index(
            self.index,  # type: ignore[arg-type]
            ResultPageRequest(page_index=page_index, page_size=page_size),
        )

    def page_request(self, page: ResultPage) -> ResultPageRowsRequest:
        return ResultPageRowsRequest(
            session_id=self.context.session_id,
            run_id=self.run.run_id,
            index_cache_key=self.index.cache_key,  # type: ignore[attr-defined]
            page=page,
        )

    def detail_request(self, page: ResultPage, ordinal: int | None = None) -> ResultBuildDetailRequest:
        selected = int(page.row_ordinals[0]) if ordinal is None else ordinal
        return ResultBuildDetailRequest(
            session_id=self.context.session_id,
            run_id=self.run.run_id,
            index_cache_key=self.index.cache_key,  # type: ignore[attr-defined]
            page=page,
            row_ordinal=selected,
        )


class ResultResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.profile = load_bundled_character_profile_selector().create_default_selection(
            "hero.fribbels.ras"
        )
        cls.skills = load_bundled_skill_context_repository()
        cls.skill_contexts = tuple(SkillContext(slot, 1500) for slot in SkillSlot)

    def _request(
        self,
        requested_sets: tuple[GearSet, ...] = (GearSet.SPEED, GearSet.HEALTH),
        *,
        request_id: str = "request.result-resolution",
    ) -> OptimizationRequest:
        return OptimizationRequest(
            request_id=request_id,
            hero_id=self.profile.hero_id,
            base_profile_id=self.profile.profile_id,
            modifiers=HeroModifiers(),
            set_pattern=SetPattern(requested_sets),
            stat_priorities=((FinalStat.SPEED, 3), (FinalStat.ATTACK, 1)),
            target_defense=1500,
            skill_contexts=self.skill_contexts,
            item_projection_mode=ItemProjectionMode.CURRENT,
        )

    def _fixture(
        self,
        actual_sets: tuple[GearSet, ...],
        *,
        requested_sets: tuple[GearSet, ...] = (GearSet.SPEED, GearSet.HEALTH),
        row_count: int = 1,
        equipped_positions: tuple[int, ...] = (),
        request_id: str = "request.result-resolution",
        run_id: str = "resolution-fixture",
        mutate_columns=None,
    ) -> _Fixture:
        request = self._request(requested_sets, request_id=request_id)
        arrays, _ = _arrays(request, self.profile, _items(actual_sets))
        pattern = compile_set_pattern(request.set_pattern)
        exact = compile_exact_build_context(
            request,
            self.profile,
            ArtifactSelection(),
            self.skills.select(request.hero_id, request.skill_contexts),
            pattern,
        )
        batch = next(iter_cartesian_batches(create_cartesian_search_space(arrays), 1))
        evaluated = evaluate_exact_build_batch(exact, arrays, batch)
        self.assertEqual(1, evaluated.emitted_count)
        row = evaluated.rows[0]

        equipped = set(equipped_positions)
        by_slot: list[tuple[GearSlot, tuple[GearItem, ...]]] = []
        reverse: list[tuple[int, str]] = []
        snapshot_dense = 0
        for position, (slot, prepared) in enumerate(zip(GEAR_SLOT_ORDER, arrays.slots, strict=True)):
            search_dense = prepared.dense_ids[0]
            stable_id = arrays.stable_item_id_for_dense_id(search_dense)
            gear_set = FRIBBELS_SET_ORDER[prepared.set_indices[0]]
            extra = GearItem(
                item_id=f"unused.{position}",
                dense_id=snapshot_dense,
                slot=slot,
                gear_set=GearSet.HEALTH,
                main_stat=_MAIN_STATS[position],
                main_stat_value=0,
            )
            reverse.append((snapshot_dense, extra.item_id))
            snapshot_dense += 1
            owned = GearItem(
                item_id=stable_id,
                dense_id=snapshot_dense,
                slot=slot,
                gear_set=gear_set,
                main_stat=_MAIN_STATS[position],
                main_stat_value=0,
                equipped_hero_id=f"hero.owner.{position}" if position in equipped else None,
            )
            reverse.append((snapshot_dense, owned.item_id))
            snapshot_dense += 1
            by_slot.append((slot, (extra, owned)))
        snapshot = DenseInventorySnapshot(tuple(by_slot), tuple(reverse))
        context = ResultResolverContext(
            session_id="session.result-resolution",
            run_id=run_id,
            selected_hero_id=request.hero_id,
            inventory_snapshot=snapshot,
            slot_arrays=arrays,
            evaluation_context=exact,
            target_pattern=pattern,
        )
        lookup = DenseItemEquippedLookup(
            tuple(index in equipped for index in range(arrays.total_items))
        )
        columns = result_columns_from_cpu_rows((row,) * row_count, arrays, lookup)
        if mutate_columns is not None:
            mutate_columns(columns)
        temporary = tempfile.TemporaryDirectory(prefix="e7-result-resolution-")
        root = Path(temporary.name)
        writer = ResultRunStore(root / "store").begin_run(run_id)
        writer.append(0, columns)
        run = writer.complete()
        index = build_result_sort_index(run, ResultSortRequest())
        fixture = _Fixture(temporary, run, index, context, columns)
        self.addCleanup(temporary.cleanup)
        return fixture

    def test_contracts_are_versioned_bounded_and_actionable(self) -> None:
        projection = project_result_resolution(MAX_PAGE_SIZE)
        self.assertEqual(RESULT_RESOLUTION_ID, ResultResolverContext.__dataclass_fields__["resolution_id"].default)
        self.assertEqual(RESULT_RESOLUTION_VERSION, ResultResolverContext.__dataclass_fields__["version"].default)
        self.assertEqual(10, projection.stored_column_count)
        self.assertEqual(233_000, projection.stored_column_copy_bytes)
        self.assertEqual(6_000, projection.owned_item_reference_count)
        with self.assertRaisesRegex(ResultResolutionError, "integer-out-of-range"):
            project_result_resolution(MAX_PAGE_SIZE + 1)

        fixture = self._fixture((GearSet.SPEED,) * 4 + (GearSet.HEALTH,) * 2)
        page = fixture.page()
        invalid = (
            lambda: ResultPageRowsRequest("session", fixture.run.run_id, fixture.index.cache_key, page, resolution_id="wrong"),  # type: ignore[attr-defined]
            lambda: ResultPageRowsRequest("session", fixture.run.run_id, fixture.index.cache_key, page, version=2),  # type: ignore[attr-defined]
            lambda: ResultPageRowsRequest("session", fixture.run.run_id, "bad", page),
            lambda: ResultBuildDetailRequest("session", fixture.run.run_id, fixture.index.cache_key, page, -1),  # type: ignore[attr-defined]
        )
        for operation in invalid:
            with self.subTest(operation=operation), self.assertRaises(ResultResolutionError) as raised:
                operation()
            self.assertTrue(raised.exception.code)
            self.assertTrue(raised.exception.path)

    def test_page_preserves_every_stored_value_and_full_equipped_gear(self) -> None:
        expected_stats = (9_000_000_001, 20_002, 3_003, 304, 100, 350, 107, 208)
        expected_metrics = tuple(-9_000_000_000 + index * 123_456_789 for index in range(15))
        priority_bits = 0x3F800001

        def mutate(columns: dict[str, np.ndarray]) -> None:
            columns["effective_final_stats"][0] = expected_stats
            columns["raw_critical_hit_chances"][0] = 127
            columns["derived_metrics"][0] = expected_metrics
            columns["priority_scores"][0] = np.asarray([priority_bits], dtype="<u4").view("<f4")[0]

        fixture = self._fixture(
            (GearSet.SPEED,) * 4 + (GearSet.HEALTH,) * 2,
            equipped_positions=(0, 3, 5),
            mutate_columns=mutate,
        )
        page = fixture.page()
        resolved = resolve_result_page(fixture.run, fixture.index, fixture.page_request(page), fixture.context)  # type: ignore[arg-type]
        row = resolved.rows[0]
        self.assertEqual(expected_stats, row.effective_final_stats)
        self.assertEqual(127, row.raw_critical_hit_chance)
        self.assertEqual(expected_metrics, row.derived_metrics)
        self.assertEqual(priority_bits, np.asarray([row.priority_score], dtype="<f4").view("<u4")[0])
        self.assertEqual(0.0, row.constraint_distance)
        self.assertEqual(3, row.equipped_item_count)
        self.assertEqual((2, 4), tuple(value for value in row.set_piece_counts if value))
        self.assertEqual((1, 1), tuple(value for value in row.set_activation_counts if value))
        self.assertEqual(GEAR_SLOT_ORDER, tuple(item.slot for item in row.owned_items))
        self.assertEqual(
            tuple(
                fixture.context.slot_arrays.stable_item_id_for_dense_id(item.search_dense_id)
                for item in row.owned_items
            ),
            tuple(item.stable_item_id for item in row.owned_items),
        )
        self.assertEqual((1, 3, 5, 7, 9, 11), tuple(item.gear.dense_id for item in row.owned_items))
        self.assertIsNone(row.replacement_reference)
        self.assertEqual(233, resolved.stats.projection.stored_column_copy_bytes)

    def test_repeated_items_are_resolved_once_and_shared_between_page_rows(self) -> None:
        fixture = self._fixture(
            (GearSet.SPEED,) * 4 + (GearSet.HEALTH,) * 2,
            row_count=3,
        )
        resolved = resolve_result_page(
            fixture.run,
            fixture.index,  # type: ignore[arg-type]
            fixture.page_request(fixture.page(page_size=3)),
            fixture.context,
        )
        self.assertEqual(6, resolved.stats.unique_full_gear_records)
        self.assertIs(resolved.rows[0].owned_items[0], resolved.rows[1].owned_items[0])
        self.assertIs(resolved.rows[1].owned_items[5].gear, resolved.rows[2].owned_items[5].gear)

    def test_first_middle_last_and_empty_pages_never_resolve_off_page_rows(self) -> None:
        fixture = self._fixture(
            (GearSet.SPEED,) * 4 + (GearSet.HEALTH,) * 2,
            row_count=2_305,
        )
        original = CompletedResultRun.open_column
        for page_index, expected_count in ((0, 1_000), (1, 1_000), (2, 305)):
            calls: list[str] = []

            def tracked(instance: CompletedResultRun, name: str):
                calls.append(name)
                return original(instance, name)

            page = fixture.page(page_index, 1_000)
            with patch.object(CompletedResultRun, "open_column", tracked):
                resolved = resolve_result_page(
                    fixture.run,
                    fixture.index,  # type: ignore[arg-type]
                    fixture.page_request(page),
                    fixture.context,
                )
            self.assertEqual(expected_count, len(resolved.rows))
            self.assertEqual(tuple(int(item) for item in page.row_ordinals), tuple(item.row_ordinal for item in resolved.rows))
            self.assertEqual(10, len(calls))
            self.assertEqual(10, resolved.stats.projection.stored_column_count)

        # An invalid physical row outside the last page remains untouched. If
        # resolution scanned the run instead of gathering the page ordinals,
        # the schema validator would reject this sentinel.
        dense_path = fixture.run.column_spec("dense_item_ids").path
        writable = np.memmap(dense_path, dtype="<i4", mode="r+", shape=(2_305, 6))
        writable[0, 0] = -1
        writable.flush()
        writable._mmap.close()
        last_page = fixture.page(2, 1_000)
        last = resolve_result_page(
            fixture.run,
            fixture.index,  # type: ignore[arg-type]
            fixture.page_request(last_page),
            fixture.context,
        )
        self.assertEqual(305, len(last.rows))

        empty = self._fixture(
            (GearSet.SPEED,) * 4 + (GearSet.HEALTH,) * 2,
            row_count=0,
            run_id="resolution-empty",
            request_id="request.result-resolution.empty",
        )
        calls = []

        def tracked_empty(instance: CompletedResultRun, name: str):
            calls.append(name)
            return original(instance, name)

        with patch.object(CompletedResultRun, "open_column", tracked_empty):
            resolved_empty = resolve_result_page(
                empty.run,
                empty.index,  # type: ignore[arg-type]
                empty.page_request(empty.page()),
                empty.context,
            )
        self.assertEqual((), resolved_empty.rows)
        self.assertEqual([], calls)
        self.assertEqual(0, resolved_empty.stats.projection.stored_column_count)

    def test_selected_exact_detail_keeps_no_replacement_sentinel(self) -> None:
        fixture = self._fixture((GearSet.SPEED,) * 4 + (GearSet.HEALTH,) * 2)
        page = fixture.page()
        detail = resolve_result_build_detail(
            fixture.run,
            fixture.index,  # type: ignore[arg-type]
            fixture.detail_request(page),
            fixture.context,
        )
        self.assertIs(detail.row.category, ResultCategory.EXACT)
        self.assertIsNone(detail.row.replacement_reference)

    def test_active_identity_checked_page_and_selected_membership_are_enforced(self) -> None:
        fixture = self._fixture(
            (GearSet.SPEED,) * 4 + (GearSet.HEALTH,) * 2,
            row_count=3,
        )
        page = fixture.page(page_size=2)
        wrong_session = replace(fixture.page_request(page), session_id="session.other")
        with self.assertRaisesRegex(ResultResolutionError, "active-session-mismatch"):
            resolve_result_page(fixture.run, fixture.index, wrong_session, fixture.context)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ResultResolutionError, "selected-row-outside-page"):
            resolve_result_build_detail(
                fixture.run,
                fixture.index,  # type: ignore[arg-type]
                fixture.detail_request(page, ordinal=2),
                fixture.context,
            )
        forged_ordinals = np.asarray([2, int(page.row_ordinals[1])], dtype="<u4")
        forged = replace(page, row_ordinals=forged_ordinals)
        with self.assertRaisesRegex(ResultResolutionError, "unchecked-result-page"):
            resolve_result_page(
                fixture.run,
                fixture.index,  # type: ignore[arg-type]
                fixture.page_request(forged),
                fixture.context,
            )

    def test_missing_inventory_and_stale_search_identity_fail_before_row_lookup(self) -> None:
        fixture = self._fixture((GearSet.SPEED,) * 4 + (GearSet.HEALTH,) * 2)
        snapshot = fixture.context.inventory_snapshot
        groups = list(snapshot.items_by_slot)  # type: ignore[attr-defined]
        first_slot, first_items = groups[0]
        groups[0] = (first_slot, first_items[:1])
        flattened = tuple(item for _, items in groups for item in items)
        renumbered = tuple(replace(item, dense_id=index) for index, item in enumerate(flattened))
        rebuilt_groups = []
        offset = 0
        for slot, items in groups:
            rebuilt_groups.append((slot, renumbered[offset : offset + len(items)]))
            offset += len(items)
        missing = DenseInventorySnapshot(
            tuple(rebuilt_groups),
            tuple((index, item.item_id) for index, item in enumerate(renumbered)),
        )
        with self.assertRaisesRegex(ResultResolutionError, "search-item-missing-from-snapshot"):
            replace(fixture.context, inventory_snapshot=missing)
        with self.assertRaisesRegex(ResultResolutionError, "selected-hero-mismatch"):
            replace(fixture.context, selected_hero_id="hero.other")

    def test_corrupt_visible_columns_and_ordinals_fail_explicitly(self) -> None:
        fixture = self._fixture((GearSet.SPEED,) * 4 + (GearSet.HEALTH,) * 2)
        dense_column = fixture.run.open_column("dense_item_ids")
        path = fixture.run.column_spec("dense_item_ids").path
        mapping = getattr(dense_column, "_mmap", None)
        if mapping is not None:
            mapping.close()
        writable = np.memmap(path, dtype="<i4", mode="r+", shape=(1, 6))
        writable[0, 0] = -1
        writable.flush()
        writable._mmap.close()
        with self.assertRaisesRegex(ResultResolutionError, "dense-item-id-out-of-range"):
            resolve_result_page(
                fixture.run,
                fixture.index,  # type: ignore[arg-type]
                fixture.page_request(fixture.page()),
                fixture.context,
            )

        clean = self._fixture(
            (GearSet.SPEED,) * 4 + (GearSet.HEALTH,) * 2,
            run_id="resolution-ordinal",
            request_id="request.result-resolution.ordinal",
        )
        bad_index = replace(clean.index, row_ordinals=np.asarray([99], dtype="<u4"))  # type: ignore[arg-type]
        bad_page = page_result_sort_index(bad_index, ResultPageRequest())
        with self.assertRaisesRegex(ResultResolutionError, "page-ordinal-out-of-range"):
            resolve_result_page(
                clean.run,
                bad_index,
                ResultPageRowsRequest(
                    clean.context.session_id,
                    clean.run.run_id,
                    bad_index.cache_key,
                    bad_page,
                ),
                clean.context,
            )

    def test_outputs_are_immutable_hashable_and_resolver_has_no_database_or_gpu_access(self) -> None:
        fixture = self._fixture((GearSet.SPEED,) * 4 + (GearSet.HEALTH,) * 2)
        resolved = resolve_result_page(
            fixture.run,
            fixture.index,  # type: ignore[arg-type]
            fixture.page_request(fixture.page()),
            fixture.context,
        )
        with self.assertRaises(FrozenInstanceError):
            resolved.rows = ()  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            resolved.rows[0].owned_items[0].stable_item_id = "changed"  # type: ignore[misc]
        request = fixture.page_request(fixture.page())
        with self.assertRaises(FrozenInstanceError):
            request.session_id = "changed"  # type: ignore[misc]
        self.assertIsInstance(hash(resolved.rows[0]), int)
        self.assertIsInstance(hash(resolved.page), int)

        source = inspect.getsource(resolution_module)
        tree = ast.parse(source)
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        imports.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        self.assertFalse(any(name.startswith(("cupy", "sqlite3")) for name in imports))
        self.assertNotIn("InventoryRepository", source)
        self.assertNotIn("optimizer.db", source)
        self.assertNotIn("user_data", source)
        spec = Path("packaging/e7-core.spec").read_text(encoding="utf-8")
        self.assertIn('"src.optimizer.result_store.resolution"', spec)


if __name__ == "__main__":
    unittest.main()
