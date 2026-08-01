from __future__ import annotations

import math
import unittest
from dataclasses import replace
from pathlib import Path

from src.optimizer.data import (
    ArtifactSelection,
    ArtifactStatOverrides,
    load_bundled_artifact_repository,
    load_bundled_character_profile_selector,
    merge_fribbels_inventory,
    parse_fribbels_gear_file,
)
from src.optimizer.domain import (
    FINAL_STAT_ORDER,
    FinalStat,
    GearItem,
    GearSet,
    GearSlot,
    HeroModifierContribution,
    HeroModifiers,
    HeroModifierStatType,
    ItemProjectionMode,
    ItemStatType,
    OptimizationRequest,
    SetPattern,
    custom_bonus_projection,
)
from src.optimizer.engine import (
    ItemProjectionEvidence,
    ProjectedGearItem,
    StatAggregationError,
    aggregate_pre_set_stats,
)


FIXTURES = Path(__file__).parent / "fixtures" / "fribbels"


def _request(selection, modifiers=HeroModifiers(), mode=ItemProjectionMode.CURRENT):
    return OptimizationRequest(
        request_id="request.stat-aggregation",
        hero_id=selection.hero_id,
        base_profile_id=selection.profile_id,
        modifiers=modifiers,
        set_pattern=SetPattern((GearSet.SPEED, GearSet.CRITICAL)),
        item_projection_mode=mode,
    )


def _gear_items() -> tuple[ProjectedGearItem, ...]:
    definitions = (
        (
            GearSlot.WEAPON,
            ItemStatType.FLAT_ATTACK,
            500,
            ((ItemStatType.ATTACK_PERCENT, 10.5), (ItemStatType.SPEED, 5.5)),
        ),
        (
            GearSlot.HELMET,
            ItemStatType.FLAT_HEALTH,
            2500,
            ((ItemStatType.HEALTH_PERCENT, 5.5),),
        ),
        (
            GearSlot.ARMOR,
            ItemStatType.FLAT_DEFENSE,
            300,
            ((ItemStatType.DEFENSE_PERCENT, 7.5),),
        ),
        (
            GearSlot.NECKLACE,
            ItemStatType.CRITICAL_HIT_DAMAGE_PERCENT,
            65,
            ((ItemStatType.CRITICAL_HIT_CHANCE_PERCENT, 5),),
        ),
        (
            GearSlot.RING,
            ItemStatType.EFFECTIVENESS_PERCENT,
            65,
            ((ItemStatType.EFFECT_RESISTANCE_PERCENT, 10),),
        ),
        (
            GearSlot.BOOTS,
            ItemStatType.SPEED,
            45,
            ((ItemStatType.ATTACK_PERCENT, 12),),
        ),
    )
    return tuple(
        ProjectedGearItem.from_gear_item(
            GearItem(
                item_id=f"item.{slot.name.lower()}",
                dense_id=index,
                slot=slot,
                gear_set=GearSet.ATTACK,
                main_stat=main_stat,
                main_stat_value=main_value,
                substats=substats,
            )
        )
        for index, (slot, main_stat, main_value, substats) in enumerate(definitions)
    )


def _zero_items() -> tuple[ProjectedGearItem, ...]:
    return tuple(
        ProjectedGearItem.from_gear_item(
            GearItem(
                item_id=f"zero.{slot.name.lower()}",
                dense_id=index,
                slot=slot,
                gear_set=GearSet.SPEED,
                main_stat=ItemStatType.FLAT_ATTACK,
                main_stat_value=0,
            )
        )
        for index, slot in enumerate(GearSlot)
    )


def _project(items: tuple[ProjectedGearItem, ...], changes=None):
    changes = {} if changes is None else changes
    projected = []
    for item in items:
        totals = dict(item.current_totals)
        for stat, amount in changes.get(item.slot, {}).items():
            totals[stat] += amount
        projected.append(
            replace(
                item,
                reforged_totals=totals,
                reforged_evidence=ItemProjectionEvidence.FRIBBELS_VALID,
            )
        )
    return tuple(projected)


class StatAggregationGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.selector = load_bundled_character_profile_selector()
        cls.selection = cls.selector.create_default_selection("hero.fribbels.ras")

    def test_hand_calculated_all_item_stats_main_and_substats(self) -> None:
        result = aggregate_pre_set_stats(
            _request(self.selection), self.selection, ArtifactSelection(), _gear_items()
        )
        self.assertEqual(
            {
                FinalStat.ATTACK: 1428,
                FinalStat.HEALTH: 8646,
                FinalStat.DEFENSE: 1022,
                FinalStat.SPEED: 145,
                FinalStat.CRITICAL_HIT_CHANCE: 20,
                FinalStat.CRITICAL_HIT_DAMAGE: 215,
                FinalStat.EFFECTIVENESS: 65,
                FinalStat.EFFECT_RESISTANCE: 22,
            },
            dict(result.final_stats),
        )
        self.assertEqual(FINAL_STAT_ORDER, tuple(stat for stat, _ in result.final_stats))
        self.assertEqual(tuple(ItemStatType), tuple(stat for stat, _ in result.diagnostics.item_totals))
        self.assertAlmostEqual(1428.55, dict(result.diagnostics.unrounded_final_stats)[FinalStat.ATTACK], places=3)
        self.assertEqual(
            dict(self.selection.profile.final_stats),
            dict(result.diagnostics.configured_naked_stats),
        )
        self.assertEqual(tuple(GearSlot), tuple(item.slot for item in result.diagnostics.items))

    def test_sanitized_fribbels_fixture_current_and_reforged_golden(self) -> None:
        parsed = parse_fribbels_gear_file(FIXTURES / "valid-enriched-export-utf8.txt")
        merged = merge_fribbels_inventory((), parsed)
        imported = {
            item.gear_item.slot: ProjectedGearItem.from_fribbels_inventory_item(
                item, dense_id=index
            )
            for index, item in enumerate(merged.items)
        }
        dense_id = len(imported)
        for slot in GearSlot:
            if slot not in imported:
                zero = ProjectedGearItem.from_gear_item(
                    GearItem(
                        item_id=f"fixture.zero.{slot.name.lower()}",
                        dense_id=dense_id,
                        slot=slot,
                        gear_set=GearSet.HEALTH,
                        main_stat=ItemStatType.FLAT_ATTACK,
                        main_stat_value=0,
                    )
                )
                imported[slot] = replace(
                    zero,
                    reforged_totals=zero.current_totals,
                    reforged_evidence=ItemProjectionEvidence.FRIBBELS_VALID,
                )
                dense_id += 1
        items = tuple(imported[slot] for slot in GearSlot)

        current = aggregate_pre_set_stats(
            _request(self.selection, mode=ItemProjectionMode.CURRENT),
            self.selection,
            ArtifactSelection(),
            items,
        )
        reforged = aggregate_pre_set_stats(
            _request(self.selection, mode=ItemProjectionMode.REFORGED),
            self.selection,
            ArtifactSelection(),
            items,
        )
        self.assertEqual((758, 7124, 982, 130, 15, 150, 0, 24), tuple(dict(current.final_stats).values()))
        self.assertEqual((758, 7407, 982, 135, 15, 150, 0, 27), tuple(dict(reforged.final_stats).values()))
        source_evidence = {
            item.slot: item.projection_evidence for item in reforged.diagnostics.items
        }
        self.assertIs(source_evidence[GearSlot.ARMOR], ItemProjectionEvidence.FRIBBELS_VALID)
        self.assertIs(source_evidence[GearSlot.BOOTS], ItemProjectionEvidence.FRIBBELS_VALID)

    def test_projection_choice_is_explicit_and_deterministic(self) -> None:
        items = _project(
            _gear_items(),
            {
                GearSlot.WEAPON: {ItemStatType.FLAT_ATTACK: 50},
                GearSlot.BOOTS: {ItemStatType.SPEED: 5},
            },
        )
        current = aggregate_pre_set_stats(
            _request(self.selection), self.selection, ArtifactSelection(), items
        )
        reforged = aggregate_pre_set_stats(
            _request(self.selection, mode=ItemProjectionMode.REFORGED),
            self.selection,
            ArtifactSelection(),
            items,
        )
        self.assertEqual(50, reforged.value(FinalStat.ATTACK) - current.value(FinalStat.ATTACK))
        self.assertEqual(5, reforged.value(FinalStat.SPEED) - current.value(FinalStat.SPEED))

    def test_set_bonuses_caps_and_metrics_are_absent(self) -> None:
        items = list(_gear_items())
        necklace = items[3]
        totals = dict(necklace.current_totals)
        totals[ItemStatType.CRITICAL_HIT_CHANCE_PERCENT] = 100
        totals[ItemStatType.CRITICAL_HIT_DAMAGE_PERCENT] = 300
        items[3] = replace(necklace, current_totals=totals)
        result = aggregate_pre_set_stats(
            _request(self.selection), self.selection, ArtifactSelection(), items
        )
        self.assertEqual(115, result.value(FinalStat.CRITICAL_HIT_CHANCE))
        self.assertEqual(450, result.value(FinalStat.CRITICAL_HIT_DAMAGE))
        self.assertFalse(hasattr(result, "metrics"))
        self.assertFalse(hasattr(result, "active_sets"))


class StatAggregationModifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.selection = load_bundled_character_profile_selector().create_default_selection(
            "hero.fribbels.ras"
        )
        cls.artifacts = load_bundled_artifact_repository()

    def _aggregate(self, modifiers: HeroModifiers, artifact=ArtifactSelection()):
        return aggregate_pre_set_stats(
            _request(self.selection, modifiers), self.selection, artifact, _zero_items()
        )

    def test_no_selection_calculated_artifact_and_overrides_apply_once(self) -> None:
        baseline = self._aggregate(HeroModifiers())
        self.assertEqual(dict(self.selection.profile.final_stats), dict(baseline.final_stats))

        record = self.artifacts.artifacts[0]
        calculated = self.artifacts.select(record.artifact_id, level=15)
        calculated_result = self._aggregate(
            calculated.to_artifact_only_modifiers(), calculated
        )
        for stat, amount in (
            (FinalStat.ATTACK, calculated.flat_stats.attack),
            (FinalStat.HEALTH, calculated.flat_stats.health),
            (FinalStat.DEFENSE, calculated.flat_stats.defense),
        ):
            self.assertEqual(
                math.trunc(dict(self.selection.profile.final_stats)[stat] + amount),
                calculated_result.value(stat),
            )

        overridden = self.artifacts.select(
            record.artifact_id,
            level=15,
            overrides=ArtifactStatOverrides(attack=101.5, health=202.25, defense=3.75),
        )
        overridden_result = self._aggregate(
            overridden.to_artifact_only_modifiers(), overridden
        )
        self.assertEqual((859, 6028, 675), tuple(overridden_result.value(stat) for stat in (FinalStat.ATTACK, FinalStat.HEALTH, FinalStat.DEFENSE)))

    def test_source_profile_final_multiplier_is_applied_after_additions(self) -> None:
        selection = load_bundled_character_profile_selector().create_default_selection(
            "hero.fribbels.aki"
        )
        base_attack = dict(selection.profile.final_stats)[FinalStat.ATTACK]
        items = list(_zero_items())
        first = items[0]
        totals = dict(first.current_totals)
        totals[ItemStatType.FLAT_ATTACK] = 100
        items[0] = replace(first, current_totals=totals)
        result = aggregate_pre_set_stats(
            _request(selection), selection, ArtifactSelection(), items
        )
        self.assertEqual(
            1.5,
            dict(result.diagnostics.final_stat_multipliers)[FinalStat.ATTACK],
        )
        self.assertEqual(math.trunc(base_attack * 1.5), math.trunc(dict(result.diagnostics.configured_naked_stats)[FinalStat.ATTACK]))
        self.assertEqual(math.trunc((base_attack + 100) * 1.5), result.value(FinalStat.ATTACK))

    def test_every_relevant_typed_modifier_kind_uses_canonical_units(self) -> None:
        base = dict(self.selection.profile.final_stats)
        cases = {
            HeroModifierStatType.FLAT_ATTACK: (FinalStat.ATTACK, 10, 10),
            HeroModifierStatType.ATTACK_PERCENT: (FinalStat.ATTACK, 0.1, math.trunc(base[FinalStat.ATTACK] * 1.1) - base[FinalStat.ATTACK]),
            HeroModifierStatType.FLAT_HEALTH: (FinalStat.HEALTH, 10, 10),
            HeroModifierStatType.HEALTH_PERCENT: (FinalStat.HEALTH, 0.1, math.trunc(base[FinalStat.HEALTH] * 1.1) - base[FinalStat.HEALTH]),
            HeroModifierStatType.FLAT_DEFENSE: (FinalStat.DEFENSE, 10, 10),
            HeroModifierStatType.DEFENSE_PERCENT: (FinalStat.DEFENSE, 0.1, math.trunc(base[FinalStat.DEFENSE] * 1.1) - base[FinalStat.DEFENSE]),
            HeroModifierStatType.SPEED: (FinalStat.SPEED, 10, 10),
            HeroModifierStatType.CRITICAL_HIT_CHANCE_PERCENT: (FinalStat.CRITICAL_HIT_CHANCE, 0.1, 10),
            HeroModifierStatType.EFFECTIVENESS_PERCENT: (FinalStat.EFFECTIVENESS, 0.1, 10),
            HeroModifierStatType.EFFECT_RESISTANCE_PERCENT: (FinalStat.EFFECT_RESISTANCE, 0.1, 10),
            HeroModifierStatType.FINAL_ATTACK_PERCENT: (FinalStat.ATTACK, 0.1, math.trunc(base[FinalStat.ATTACK] * 1.1) - base[FinalStat.ATTACK]),
            HeroModifierStatType.FINAL_HEALTH_PERCENT: (FinalStat.HEALTH, 0.1, math.trunc(base[FinalStat.HEALTH] * 1.1) - base[FinalStat.HEALTH]),
            HeroModifierStatType.FINAL_DEFENSE_PERCENT: (FinalStat.DEFENSE, 0.1, math.trunc(base[FinalStat.DEFENSE] * 1.1) - base[FinalStat.DEFENSE]),
        }
        for kind, (stat, amount, expected_delta) in cases.items():
            with self.subTest(kind=kind):
                contribution = HeroModifierContribution(kind, amount)
                modifiers = HeroModifiers(
                    custom_bonuses=custom_bonus_projection((contribution,)),
                    custom_contributions=(contribution,),
                )
                result = self._aggregate(modifiers)
                self.assertEqual(expected_delta, result.value(stat) - base[stat])

    def test_imprint_and_ee_typed_values_are_not_double_counted(self) -> None:
        imprint = HeroModifierContribution(HeroModifierStatType.ATTACK_PERCENT, 0.10)
        ee = HeroModifierContribution(HeroModifierStatType.FLAT_HEALTH, 100)
        modifiers = HeroModifiers(
            imprint_level="A",
            imprint_bonuses=imprint.legacy_final_stat_bonus(),
            imprint_contribution=imprint,
            exclusive_equipment_id="exclusive-equipment.synthetic",
            exclusive_equipment_bonuses=ee.legacy_final_stat_bonus(),
            exclusive_equipment_contribution=ee,
        )
        result = self._aggregate(modifiers)
        self.assertEqual(833, result.value(FinalStat.ATTACK))
        self.assertEqual(5926, result.value(FinalStat.HEALTH))

    def test_legacy_only_modifier_maps_fail_instead_of_guessing_units(self) -> None:
        cases = (
            HeroModifiers(imprint_level="A", imprint_bonuses={FinalStat.HEALTH: 9}),
            HeroModifiers(custom_bonuses={FinalStat.ATTACK: 75}),
        )
        for modifiers in cases:
            with self.subTest(modifiers=modifiers), self.assertRaises(StatAggregationError) as raised:
                self._aggregate(modifiers)
            self.assertEqual("legacy-modifier-untyped", raised.exception.code)


class StatAggregationValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.selector = load_bundled_character_profile_selector()
        cls.selection = cls.selector.create_default_selection("hero.fribbels.ras")

    def _call(self, items=None, request=None, artifact=ArtifactSelection()):
        return aggregate_pre_set_stats(
            _request(self.selection) if request is None else request,
            self.selection,
            artifact,
            _gear_items() if items is None else items,
        )

    def test_six_unique_slots_and_stable_dense_ids_are_required(self) -> None:
        items = list(_gear_items())
        cases = (
            (items[:-1], "six-items-required"),
            (items[:-1] + [replace(items[-1], slot=GearSlot.WEAPON)], "duplicate-slot"),
            (items[:-1] + [replace(items[-1], item_id=items[0].item_id)], "duplicate-item-id"),
            (items[:-1] + [replace(items[-1], dense_id=items[0].dense_id)], "duplicate-dense-id"),
        )
        for invalid, code in cases:
            with self.subTest(code=code), self.assertRaises(StatAggregationError) as raised:
                self._call(invalid)
            self.assertEqual(code, raised.exception.code)

    def test_explicit_projection_profile_and_artifact_resolution_are_required(self) -> None:
        no_mode = replace(_request(self.selection), item_projection_mode=None)
        wrong_hero = replace(_request(self.selection), hero_id="hero.fribbels.aither")
        selected_artifact = load_bundled_artifact_repository().select_none()
        for request, artifact, code in (
            (no_mode, ArtifactSelection(), "projection-mode-required"),
            (wrong_hero, ArtifactSelection(), "hero-selection-mismatch"),
            (
                replace(
                    _request(self.selection),
                    modifiers=HeroModifiers(
                        artifact_id="artifact.synthetic",
                        artifact_level=1,
                    ),
                ),
                selected_artifact,
                "artifact-selection-mismatch",
            ),
        ):
            with self.subTest(code=code), self.assertRaises(StatAggregationError) as raised:
                self._call(request=request, artifact=artifact)
            self.assertEqual(code, raised.exception.code)

        with self.assertRaises(StatAggregationError) as unavailable:
            self._call(request=_request(self.selection, mode=ItemProjectionMode.REFORGED))
        self.assertEqual("reforged-projection-unavailable", unavailable.exception.code)

    def test_partial_nonfinite_and_malformed_projection_evidence_fail_actionably(self) -> None:
        complete = {stat: 0 for stat in ItemStatType}
        cases = (
            ({ItemStatType.FLAT_ATTACK: 1}, ItemProjectionEvidence.DOMAIN_CURRENT, "partial-stat-totals"),
            (complete | {ItemStatType.SPEED: math.nan}, ItemProjectionEvidence.DOMAIN_CURRENT, "invalid-number"),
            (complete, "unknown", "invalid-projection-evidence"),
        )
        for totals, evidence, code in cases:
            with self.subTest(code=code), self.assertRaises(StatAggregationError) as raised:
                ProjectedGearItem(
                    item_id="bad.item",
                    slot=GearSlot.WEAPON,
                    gear_set=GearSet.SPEED,
                    current_totals=totals,
                    current_evidence=evidence,
                )
            self.assertEqual(code, raised.exception.code)

    def test_complete_fallback_evidence_is_retained_but_not_reinterpreted(self) -> None:
        items = list(_project(_zero_items()))
        items[0] = replace(
            items[0],
            reforged_evidence=ItemProjectionEvidence.FRIBBELS_MISSING,
        )
        result = self._call(
            items,
            _request(self.selection, mode=ItemProjectionMode.REFORGED),
        )
        self.assertIs(
            ItemProjectionEvidence.FRIBBELS_MISSING,
            result.diagnostics.items[0].projection_evidence,
        )


if __name__ == "__main__":
    unittest.main()
