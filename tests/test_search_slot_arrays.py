from __future__ import annotations

import ast
import inspect
import json
import unittest
from dataclasses import FrozenInstanceError, replace

from src.optimizer.data import (
    load_bundled_character_profile_selector,
    merge_fribbels_inventory,
    parse_fribbels_gear_bytes,
)
from src.optimizer.domain import (
    ALLOWED_MAIN_STATS_BY_SLOT,
    FINAL_STAT_ORDER,
    FRIBBELS_ITEM_STAT_ORDER,
    GEAR_SLOT_ORDER,
    RIGHT_SIDE_GEAR_SLOTS,
    SET_CATALOG,
    EquipmentEligibilityReason,
    FinalStat,
    GearSearchFilters,
    GearSet,
    GearSlot,
    HeroModifiers,
    ItemProjectionMode,
    ItemStatType,
    OptimizationRequest,
    SetPattern,
    gear_set_fribbels_name,
    gear_slot_fribbels_name,
    item_stat_fribbels_name,
)
from src.optimizer.engine import ItemProjectionEvidence
from src.optimizer.search import (
    SEARCH_PREPARATION_EXCLUSION_ORDER,
    SearchPreparationError,
    SearchPreparationExclusionReason,
    prepare_search_slot_arrays,
)
from src.optimizer.search import slot_arrays as slot_arrays_module


DEFAULT_MAIN_STATS = {
    GearSlot.WEAPON: ItemStatType.FLAT_ATTACK,
    GearSlot.HELMET: ItemStatType.FLAT_HEALTH,
    GearSlot.ARMOR: ItemStatType.FLAT_DEFENSE,
    GearSlot.NECKLACE: ItemStatType.CRITICAL_HIT_DAMAGE_PERCENT,
    GearSlot.RING: ItemStatType.EFFECTIVENESS_PERCENT,
    GearSlot.BOOTS: ItemStatType.SPEED,
}
DEFAULT_MAIN_VALUES = {
    GearSlot.WEAPON: 500,
    GearSlot.HELMET: 2500,
    GearSlot.ARMOR: 300,
    GearSlot.NECKLACE: 65,
    GearSlot.RING: 65,
    GearSlot.BOOTS: 45,
}


def _row(
    item_id: str,
    slot: GearSlot,
    *,
    main_stat: ItemStatType | None = None,
    main_value: int | float | None = None,
    gear_set: GearSet = GearSet.SPEED,
    enhance: int = 15,
    owner_id: str | None = None,
    locked: bool = False,
    substat: ItemStatType | None = None,
    substat_value: int | float = 4,
    reforged_substat_value: int | float = 6,
) -> dict[str, object]:
    selected_main = DEFAULT_MAIN_STATS[slot] if main_stat is None else main_stat
    selected_value = DEFAULT_MAIN_VALUES[slot] if main_value is None else main_value
    selected_substat = (
        ItemStatType.ATTACK_PERCENT
        if selected_main is ItemStatType.SPEED
        else ItemStatType.SPEED
        if substat is None
        else substat
    )
    result: dict[str, object] = {
        "ingameId": item_id,
        "gear": gear_slot_fribbels_name(slot),
        "rank": "Epic",
        "set": gear_set_fribbels_name(gear_set),
        "enhance": enhance,
        "level": 85,
        "main": {
            "type": item_stat_fribbels_name(selected_main),
            "value": selected_value,
            "reforgedValue": selected_value,
        },
        "substats": [
            {
                "type": item_stat_fribbels_name(selected_substat),
                "value": substat_value,
                "reforgedValue": reforged_substat_value,
            }
        ],
        "locked": locked,
    }
    if owner_id is not None:
        result["ingameEquippedId"] = owner_id
    return result


def _six_rows(prefix: str = "base", **overrides: object) -> list[dict[str, object]]:
    return [
        _row(f"{prefix}.{slot.name.lower()}", slot, **overrides)
        for slot in GEAR_SLOT_ORDER
    ]


def _inventory(rows: list[dict[str, object]]):
    payload = json.dumps({"items": rows}).encode("utf-8")
    parsed = parse_fribbels_gear_bytes(payload)
    if parsed.rejections:
        raise AssertionError(parsed.rejections)
    return merge_fribbels_inventory((), parsed).items


def _stable_id(inventory, source_id: str) -> str:
    return next(item.stable_item_id for item in inventory if item.current_ingame_id == source_id)


def _request(selection, **overrides: object) -> OptimizationRequest:
    values: dict[str, object] = {
        "request_id": "request.search-slots",
        "hero_id": selection.hero_id,
        "base_profile_id": selection.profile_id,
        "modifiers": HeroModifiers(),
        "set_pattern": SetPattern((GearSet.SPEED, GearSet.CRITICAL)),
        "item_projection_mode": ItemProjectionMode.CURRENT,
    }
    values.update(overrides)
    return OptimizationRequest(**values)


class SearchSlotArrayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.selection = load_bundled_character_profile_selector().create_default_selection(
            "hero.fribbels.ras"
        )

    def test_arrays_are_canonical_compact_and_stable_across_input_order(self) -> None:
        rows = _six_rows()
        rows.extend(
            (
                _row("extra.weapon", GearSlot.WEAPON, gear_set=GearSet.HEALTH),
                _row(
                    "extra.ring",
                    GearSlot.RING,
                    main_stat=ItemStatType.HEALTH_PERCENT,
                    main_value=60,
                    gear_set=GearSet.CRITICAL,
                ),
            )
        )
        inventory = _inventory(rows)
        request = _request(self.selection)

        first = prepare_search_slot_arrays(request, self.selection, inventory)
        second = prepare_search_slot_arrays(request, self.selection, reversed(inventory))

        self.assertEqual(first, second)
        self.assertEqual(request.request_id, first.request_id)
        self.assertEqual(request.hero_id, first.hero_id)
        self.assertEqual(request.base_profile_id, first.base_profile_id)
        self.assertEqual(
            tuple(value for _, value in self.selection.profile.final_stats),
            first.base_stats,
        )
        self.assertEqual(GEAR_SLOT_ORDER, tuple(slot.slot for slot in first.slots))
        self.assertEqual(tuple(range(first.total_items)), tuple(item[0] for item in first.dense_id_to_stable_id))
        self.assertEqual(
            tuple(range(first.total_items)),
            tuple(dense_id for slot in first.slots for dense_id in slot.dense_ids),
        )
        self.assertEqual(8, len(first.for_slot(GearSlot.WEAPON).final_stat_contributions[0]))
        self.assertEqual(len(FINAL_STAT_ORDER), 8)
        expected_stable = tuple(
            item.stable_item_id
            for item in sorted(
                inventory,
                key=lambda item: (GEAR_SLOT_ORDER.index(item.gear_item.slot), item.stable_item_id),
            )
        )
        self.assertEqual(expected_stable, tuple(item[1] for item in first.dense_id_to_stable_id))
        for slot_array in first.slots:
            expected_sets = tuple(
                SET_CATALOG[item.gear_item.gear_set].fribbels_index
                for item in sorted(
                    (item for item in inventory if item.gear_item.slot is slot_array.slot),
                    key=lambda item: item.stable_item_id,
                )
            )
            self.assertEqual(expected_sets, slot_array.set_indices)
        self.assertTrue(
            all(
                decision.projection_evidence is ItemProjectionEvidence.FRIBBELS_MISSING
                for decision in first.diagnostics.decisions
            )
        )
        self.assertIsInstance(hash(first), int)
        with self.assertRaises(FrozenInstanceError):
            first.slots = ()  # type: ignore[misc]

    def test_current_and_reforged_vectors_and_gear_scores_use_selected_projection(self) -> None:
        inventory = _inventory(_six_rows())
        current = prepare_search_slot_arrays(
            _request(self.selection, item_projection_mode=ItemProjectionMode.CURRENT),
            self.selection,
            inventory,
        )
        reforged = prepare_search_slot_arrays(
            _request(self.selection, item_projection_mode=ItemProjectionMode.REFORGED),
            self.selection,
            inventory,
        )

        weapon_current = current.for_slot(GearSlot.WEAPON)
        weapon_reforged = reforged.for_slot(GearSlot.WEAPON)
        speed_index = FINAL_STAT_ORDER.index(FinalStat.SPEED)
        self.assertEqual(4.0, weapon_current.final_stat_contributions[0][speed_index])
        self.assertEqual(6.0, weapon_reforged.final_stat_contributions[0][speed_index])
        self.assertEqual((8,), weapon_current.gear_scores)
        self.assertEqual((12,), weapon_reforged.gear_scores)
        self.assertIs(
            ItemProjectionEvidence.FRIBBELS_MISSING,
            reforged.diagnostics.decisions[0].projection_evidence,
        )

    def test_equipment_policy_preserves_selected_hero_and_lock_semantics(self) -> None:
        selected = self.selection.hero_id
        rows = _six_rows()
        rows.extend(
            (
                _row("weapon.selected", GearSlot.WEAPON, owner_id=selected, locked=True),
                _row("weapon.other", GearSlot.WEAPON, owner_id="hero.other"),
                _row("weapon.stale", GearSlot.WEAPON, owner_id="hero.deleted"),
            )
        )
        inventory = _inventory(rows)
        excluded = prepare_search_slot_arrays(
            _request(self.selection, include_equipped=False),
            self.selection,
            inventory,
        )
        included = prepare_search_slot_arrays(
            _request(self.selection, include_equipped=True),
            self.selection,
            inventory,
        )

        self.assertEqual(2, len(excluded.for_slot(GearSlot.WEAPON).dense_ids))
        self.assertEqual(4, len(included.for_slot(GearSlot.WEAPON).dense_ids))
        by_source = {
            item.current_ingame_id: next(
                decision
                for decision in excluded.diagnostics.decisions
                if decision.stable_item_id == item.stable_item_id
            )
            for item in inventory
        }
        self.assertTrue(by_source["weapon.selected"].included)
        self.assertIs(
            EquipmentEligibilityReason.SELECTED_HERO,
            by_source["weapon.selected"].eligibility_reason,
        )
        self.assertIs(
            SearchPreparationExclusionReason.OTHER_HERO,
            by_source["weapon.other"].exclusion_reason,
        )
        self.assertIs(
            SearchPreparationExclusionReason.OTHER_HERO,
            by_source["weapon.stale"].exclusion_reason,
        )

    def test_imported_selected_hero_alias_keeps_current_equipment_eligible(self) -> None:
        rows = _six_rows()
        rows.append(
            _row(
                "weapon.imported-owner",
                GearSlot.WEAPON,
                owner_id="5405594543",
            )
        )
        result = prepare_search_slot_arrays(
            _request(self.selection, include_equipped=False),
            self.selection,
            _inventory(rows),
            selected_hero_alias_ids=("5405594543",),
        )
        decision = next(
            item
            for item in result.diagnostics.decisions
            if item.stable_item_id.endswith("weapon.imported-owner")
        )
        self.assertTrue(decision.included)
        self.assertIs(
            EquipmentEligibilityReason.SELECTED_HERO,
            decision.eligibility_reason,
        )

    def test_fully_constrained_production_prefilter_removes_only_unselected_sets(self) -> None:
        rows = []
        for index, slot in enumerate(GEAR_SLOT_ORDER):
            required_set = GearSet.SPEED if index < 4 else GearSet.CRITICAL
            rows.append(_row(f"target.{slot.name.lower()}", slot, gear_set=required_set))
            rows.append(_row(f"other.{slot.name.lower()}", slot, gear_set=GearSet.HEALTH))
        result = prepare_search_slot_arrays(
            _request(self.selection),
            self.selection,
            _inventory(rows),
            prefilter_fully_constrained_sets=True,
        )
        self.assertEqual((1, 1, 1, 1, 1, 1), tuple(len(slot.dense_ids) for slot in result.slots))
        self.assertEqual(
            6,
            sum(
                slot.excluded_for(SearchPreparationExclusionReason.SET_REQUIREMENT)
                for slot in result.diagnostics.slots
            ),
        )

    def test_all_legal_right_side_main_stats_are_filterable(self) -> None:
        rows = _six_rows()
        expected_counts: dict[GearSlot, int] = {}
        for slot in RIGHT_SIDE_GEAR_SLOTS:
            rows = [row for row in rows if row["gear"] != gear_slot_fribbels_name(slot)]
            allowed = tuple(
                stat for stat in FRIBBELS_ITEM_STAT_ORDER if stat in ALLOWED_MAIN_STATS_BY_SLOT[slot]
            )
            expected_counts[slot] = len(allowed)
            for index, stat in enumerate(allowed):
                rows.append(
                    _row(
                        f"legal.{slot.name.lower()}.{index}",
                        slot,
                        main_stat=stat,
                        main_value=10,
                    )
                )
        filters = GearSearchFilters(
            right_side_main_stats=tuple(
                (slot, tuple(reversed(tuple(ALLOWED_MAIN_STATS_BY_SLOT[slot]))))
                for slot in reversed(RIGHT_SIDE_GEAR_SLOTS)
            )
        )

        result = prepare_search_slot_arrays(
            _request(self.selection, gear_filters=filters),
            self.selection,
            _inventory(rows),
        )

        for slot, count in expected_counts.items():
            self.assertEqual(count, len(result.for_slot(slot).dense_ids))
        self.assertEqual(
            RIGHT_SIDE_GEAR_SLOTS,
            tuple(slot for slot, _ in filters.right_side_main_stats),
        )
        for slot, stats in filters.right_side_main_stats:
            self.assertEqual(
                tuple(
                    stat
                    for stat in FRIBBELS_ITEM_STAT_ORDER
                    if stat in ALLOWED_MAIN_STATS_BY_SLOT[slot]
                ),
                stats,
            )

    def test_minimum_main_stat_and_explicit_filters_have_stable_diagnostics(self) -> None:
        rows = _six_rows()
        rows.extend(
            (
                _row("ring.hp", GearSlot.RING, main_stat=ItemStatType.HEALTH_PERCENT, main_value=60),
                _row("boots.low", GearSlot.BOOTS, enhance=14),
                _row("helmet.exclude", GearSlot.HELMET),
            )
        )
        inventory = _inventory(rows)
        excluded_id = _stable_id(inventory, "helmet.exclude")
        filters = GearSearchFilters(
            right_side_main_stats=((GearSlot.RING, (ItemStatType.EFFECTIVENESS_PERCENT,)),),
            minimum_enhance=15,
            excluded_item_ids=(excluded_id, "stale.item.id"),
        )

        result = prepare_search_slot_arrays(
            _request(self.selection, gear_filters=filters),
            self.selection,
            inventory,
        )

        self.assertEqual(("stale.item.id",), result.diagnostics.unmatched_excluded_item_ids)
        self.assertEqual(
            1,
            result.diagnostics.slot(GearSlot.RING).excluded_for(
                SearchPreparationExclusionReason.MAIN_STAT
            ),
        )
        self.assertEqual(
            1,
            result.diagnostics.slot(GearSlot.BOOTS).excluded_for(
                SearchPreparationExclusionReason.BELOW_MINIMUM_ENHANCE
            ),
        )
        self.assertEqual(
            1,
            result.diagnostics.slot(GearSlot.HELMET).excluded_for(
                SearchPreparationExclusionReason.EXPLICIT_ITEM
            ),
        )
        self.assertEqual(
            SEARCH_PREPARATION_EXCLUSION_ORDER,
            tuple(
                reason
                for reason, _ in result.diagnostics.slot(GearSlot.RING).exclusion_counts
            ),
        )

    def test_empty_slots_report_the_filter_that_removed_the_last_candidate(self) -> None:
        base_inventory = _inventory(_six_rows())
        cases = []
        weapon_id = _stable_id(base_inventory, "base.weapon")
        cases.append(
            (
                base_inventory,
                GearSearchFilters(excluded_item_ids=(weapon_id,)),
                GearSlot.WEAPON,
                SearchPreparationExclusionReason.EXPLICIT_ITEM,
            )
        )
        cases.append(
            (
                _inventory(_six_rows(enhance=14)),
                GearSearchFilters(minimum_enhance=15),
                GearSlot.WEAPON,
                SearchPreparationExclusionReason.BELOW_MINIMUM_ENHANCE,
            )
        )
        cases.append(
            (
                base_inventory,
                GearSearchFilters(
                    right_side_main_stats=((GearSlot.RING, (ItemStatType.HEALTH_PERCENT,)),)
                ),
                GearSlot.RING,
                SearchPreparationExclusionReason.MAIN_STAT,
            )
        )
        for inventory, filters, slot, reason in cases:
            with self.subTest(reason=reason), self.assertRaises(SearchPreparationError) as raised:
                prepare_search_slot_arrays(
                    _request(self.selection, gear_filters=filters),
                    self.selection,
                    inventory,
                )
            self.assertEqual("empty-search-slots", raised.exception.code)
            assert raised.exception.diagnostics is not None
            self.assertIn(slot, raised.exception.diagnostics.empty_slots)
            self.assertGreater(
                raised.exception.diagnostics.slot(slot).excluded_for(reason),
                0,
            )

    def test_no_inventory_reports_all_six_empty_slots(self) -> None:
        with self.assertRaises(SearchPreparationError) as raised:
            prepare_search_slot_arrays(_request(self.selection), self.selection, ())
        self.assertEqual("empty-search-slots", raised.exception.code)
        assert raised.exception.diagnostics is not None
        self.assertEqual(GEAR_SLOT_ORDER, raised.exception.diagnostics.empty_slots)
        self.assertEqual(0, raised.exception.diagnostics.input_count)

    def test_projection_request_profile_and_inventory_validation_precede_search(self) -> None:
        inventory = _inventory(_six_rows())
        cases = (
            (
                replace(_request(self.selection), item_projection_mode=None),
                self.selection,
                inventory,
                "projection-mode-required",
            ),
            (
                replace(_request(self.selection), hero_id="hero.other"),
                self.selection,
                inventory,
                "hero-selection-mismatch",
            ),
            (_request(self.selection), object(), inventory, "invalid-profile-selection"),
            (_request(self.selection), self.selection, (object(),), "invalid-inventory"),
            (
                _request(self.selection),
                self.selection,
                (inventory[0], inventory[0]),
                "duplicate-item-id",
            ),
        )
        for request, profile, items, code in cases:
            with self.subTest(code=code), self.assertRaises(SearchPreparationError) as raised:
                prepare_search_slot_arrays(request, profile, items)  # type: ignore[arg-type]
            self.assertEqual(code, raised.exception.code)

    def test_module_has_no_repository_io_enumeration_cuda_storage_or_ui_dependency(self) -> None:
        source = inspect.getsource(slot_arrays_module)
        for forbidden in (
            "sqlite3",
            "pathlib",
            "InventoryRepository",
            "src.desktop",
            "src.ui",
            "cartesian",
            "itertools.product",
            "cuda",
            "result_store",
            "logging",
        ):
            self.assertNotIn(forbidden, source)
        calls = {
            node.func.id
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertTrue({"open", "print"}.isdisjoint(calls))


if __name__ == "__main__":
    unittest.main()
