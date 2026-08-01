from __future__ import annotations

import ast
import inspect
import json
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

from src.optimizer.data import merge_fribbels_inventory, parse_fribbels_gear_bytes, parse_fribbels_gear_file
from src.optimizer.domain import (
    EquipmentEligibilityDecision,
    EquipmentEligibilityInputError,
    EquipmentEligibilityPolicy,
    EquipmentEligibilityReason,
    GearItem,
    GearSet,
    GearSlot,
    ItemStatType,
    decide_equipment_eligibility,
    evaluate_equipment_eligibility,
    filter_eligible_gear,
)
from src.optimizer.domain import eligibility as eligibility_module


FIXTURES = Path(__file__).parent / "fixtures" / "fribbels"
SELECTED_HERO = "hero.selected"


def _gear(
    item_id: str,
    *,
    owner_id: str | None = None,
    locked: bool = False,
    dense_id: int | None = None,
) -> GearItem:
    return GearItem(
        item_id=item_id,
        dense_id=dense_id,
        slot=GearSlot.RING,
        gear_set=GearSet.HEALTH,
        item_level=85,
        enhance=15,
        main_stat=ItemStatType.HEALTH_PERCENT,
        main_stat_value=60,
        substats=((ItemStatType.SPEED, 12),),
        equipped_hero_id=owner_id,
        locked=locked,
    )


def _ownership_cases() -> tuple[GearItem, ...]:
    return (
        _gear("item.unequipped"),
        _gear("item.selected", owner_id=SELECTED_HERO),
        _gear("item.other", owner_id="hero.other"),
        _gear("item.stale", owner_id="hero.deleted"),
    )


class EquipmentEligibilitySemanticsTests(unittest.TestCase):
    def test_include_equipped_on_includes_every_ownership_case(self) -> None:
        policy = EquipmentEligibilityPolicy(SELECTED_HERO, True)

        decisions = evaluate_equipment_eligibility(_ownership_cases(), policy)

        self.assertEqual(tuple(decision.eligible for decision in decisions), (True,) * 4)
        self.assertEqual(
            tuple(decision.reason for decision in decisions),
            (
                EquipmentEligibilityReason.UNEQUIPPED,
                EquipmentEligibilityReason.SELECTED_HERO,
                EquipmentEligibilityReason.INCLUDED_EQUIPPED,
                EquipmentEligibilityReason.INCLUDED_EQUIPPED,
            ),
        )

    def test_include_equipped_off_keeps_unowned_and_selected_hero_only(self) -> None:
        items = _ownership_cases()
        policy = EquipmentEligibilityPolicy(SELECTED_HERO, False)

        decisions = evaluate_equipment_eligibility(items, policy)
        eligible = filter_eligible_gear(items, policy)

        self.assertEqual(tuple(decision.eligible for decision in decisions), (True, True, False, False))
        self.assertEqual(
            tuple(decision.reason for decision in decisions),
            (
                EquipmentEligibilityReason.UNEQUIPPED,
                EquipmentEligibilityReason.SELECTED_HERO,
                EquipmentEligibilityReason.OTHER_HERO,
                EquipmentEligibilityReason.OTHER_HERO,
            ),
        )
        self.assertEqual(eligible, items[:2])

    def test_stale_owner_is_conservatively_other_without_a_hero_catalog(self) -> None:
        stale = _gear("item.stale", owner_id="hero.no-longer-imported")

        excluded = decide_equipment_eligibility(
            stale,
            EquipmentEligibilityPolicy(SELECTED_HERO, False),
        )
        included = decide_equipment_eligibility(
            stale,
            EquipmentEligibilityPolicy(SELECTED_HERO, True),
        )

        self.assertFalse(excluded.eligible)
        self.assertIs(excluded.reason, EquipmentEligibilityReason.OTHER_HERO)
        self.assertTrue(included.eligible)
        self.assertIs(included.reason, EquipmentEligibilityReason.INCLUDED_EQUIPPED)

    def test_locked_state_never_changes_the_ownership_decision(self) -> None:
        unlocked = _gear("item.unlocked", owner_id="hero.other", locked=False)
        locked = _gear("item.locked", owner_id="hero.other", locked=True)

        for include_equipped in (False, True):
            with self.subTest(include_equipped=include_equipped):
                policy = EquipmentEligibilityPolicy(SELECTED_HERO, include_equipped)
                first = decide_equipment_eligibility(unlocked, policy)
                second = decide_equipment_eligibility(locked, policy)
                self.assertEqual(first.eligible, second.eligible)
                self.assertIs(first.reason, second.reason)

    def test_owner_display_name_changes_do_not_cross_the_policy_boundary(self) -> None:
        parsed = parse_fribbels_gear_file(FIXTURES / "valid-enriched-export-utf8.txt")
        equipped = next(
            item
            for item in merge_fribbels_inventory((), parsed).items
            if item.gear_item.equipped_hero_id is not None
        )
        renamed = replace(equipped, equipped_by_name="A different display name")
        policy = EquipmentEligibilityPolicy(equipped.gear_item.equipped_hero_id, False)

        original_decision = decide_equipment_eligibility(equipped.gear_item, policy)
        renamed_decision = decide_equipment_eligibility(renamed.gear_item, policy)

        self.assertIs(renamed.gear_item, equipped.gear_item)
        self.assertEqual(original_decision, renamed_decision)
        self.assertIs(original_decision.reason, EquipmentEligibilityReason.SELECTED_HERO)

    def test_blank_source_owner_normalizes_to_unequipped(self) -> None:
        payload = {
            "items": [
                {
                    "ingameId": "blank-owner-item",
                    "ingameEquippedId": "   ",
                    "gear": "Ring",
                    "rank": "Epic",
                    "set": "HealthSet",
                    "enhance": 15,
                    "level": 85,
                    "main": {"type": "HealthPercent", "value": 60},
                    "substats": [{"type": "Speed", "value": 12}],
                }
            ],
            "heroes": [],
        }
        parsed = parse_fribbels_gear_bytes(json.dumps(payload).encode())
        state = merge_fribbels_inventory((), parsed).items[0]

        decision = decide_equipment_eligibility(
            state.gear_item,
            EquipmentEligibilityPolicy(SELECTED_HERO, False),
        )

        self.assertIsNone(state.gear_item.equipped_hero_id)
        self.assertTrue(decision.eligible)
        self.assertIs(decision.reason, EquipmentEligibilityReason.UNEQUIPPED)

    def test_stable_hero_ids_compare_exactly_after_policy_whitespace_normalization(self) -> None:
        matching = _gear("item.matching", owner_id=SELECTED_HERO)
        different_case = _gear("item.case", owner_id="Hero.Selected")
        policy = EquipmentEligibilityPolicy(f"  {SELECTED_HERO}  ", False)

        decisions = evaluate_equipment_eligibility((matching, different_case), policy)

        self.assertEqual(policy.selected_hero_id, SELECTED_HERO)
        self.assertTrue(decisions[0].eligible)
        self.assertFalse(decisions[1].eligible)


class EquipmentEligibilityValidationTests(unittest.TestCase):
    def test_blank_or_invalid_selected_hero_and_non_boolean_toggle_are_rejected(self) -> None:
        for selected_hero_id in (None, "", "   ", 42):
            with self.subTest(selected_hero_id=selected_hero_id):
                with self.assertRaises(EquipmentEligibilityInputError):
                    EquipmentEligibilityPolicy(selected_hero_id, False)  # type: ignore[arg-type]
        for include_equipped in (None, 0, 1, "true"):
            with self.subTest(include_equipped=include_equipped):
                with self.assertRaisesRegex(EquipmentEligibilityInputError, "boolean"):
                    EquipmentEligibilityPolicy(
                        SELECTED_HERO,
                        include_equipped,  # type: ignore[arg-type]
                    )

    def test_policy_is_validated_before_the_inventory_is_iterated(self) -> None:
        class ExplodingItems:
            def __iter__(self):
                raise AssertionError("inventory should not have been iterated")

        with self.assertRaisesRegex(EquipmentEligibilityInputError, "policy"):
            evaluate_equipment_eligibility(
                ExplodingItems(),  # type: ignore[arg-type]
                object(),  # type: ignore[arg-type]
            )

    def test_invalid_item_types_and_duplicate_stable_ids_are_rejected(self) -> None:
        policy = EquipmentEligibilityPolicy(SELECTED_HERO, False)
        item = _gear("item.duplicate")

        for invalid in (None, "item", object()):
            with self.subTest(invalid=type(invalid).__name__):
                with self.assertRaises(EquipmentEligibilityInputError):
                    evaluate_equipment_eligibility((item, invalid), policy)  # type: ignore[arg-type]
        with self.assertRaisesRegex(EquipmentEligibilityInputError, "duplicate"):
            evaluate_equipment_eligibility((item, replace(item, locked=True)), policy)
        with self.assertRaises(EquipmentEligibilityInputError):
            decide_equipment_eligibility(object(), policy)  # type: ignore[arg-type]

    def test_decision_state_must_agree_with_its_stable_reason(self) -> None:
        item = _gear("item.decision")

        self.assertEqual(
            EquipmentEligibilityReason.UNEQUIPPED.value,
            "eligibility.unequipped",
        )
        self.assertEqual(
            EquipmentEligibilityDecision(
                item=item,
                eligible=True,
                reason="eligibility.unequipped",  # type: ignore[arg-type]
            ).reason,
            EquipmentEligibilityReason.UNEQUIPPED,
        )
        with self.assertRaisesRegex(EquipmentEligibilityInputError, "does not agree"):
            EquipmentEligibilityDecision(
                item=item,
                eligible=False,
                reason=EquipmentEligibilityReason.SELECTED_HERO,
            )


class EquipmentEligibilityPurityTests(unittest.TestCase):
    def test_filter_preserves_order_identity_immutability_and_empty_input(self) -> None:
        first = _gear("item.first", dense_id=9)
        excluded = _gear("item.excluded", owner_id="hero.other", locked=True, dense_id=3)
        last = _gear("item.last", owner_id=SELECTED_HERO, dense_id=7)
        items = (first, excluded, last)
        policy = EquipmentEligibilityPolicy(SELECTED_HERO, False)

        decisions = evaluate_equipment_eligibility((item for item in items), policy)
        filtered = filter_eligible_gear(items, policy)

        self.assertEqual(tuple(decision.stable_item_id for decision in decisions), tuple(item.item_id for item in items))
        self.assertIs(filtered[0], first)
        self.assertIs(filtered[1], last)
        self.assertEqual((filtered[0].dense_id, filtered[1].dense_id), (9, 7))
        self.assertEqual(evaluate_equipment_eligibility((), policy), ())
        self.assertEqual(filter_eligible_gear((), policy), ())
        with self.assertRaises(FrozenInstanceError):
            policy.include_equipped = True  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            decisions[0].eligible = False  # type: ignore[misc]

    def test_module_has_no_persistence_ui_logging_io_or_dense_assignment_dependency(self) -> None:
        source = inspect.getsource(eligibility_module)
        for forbidden in (
            "sqlite3",
            "pathlib",
            "src.optimizer.data",
            "src.desktop",
            "src.ui",
            "logging",
            "dense_id",
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
