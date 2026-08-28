from __future__ import annotations

import copy
import json
import socket
import unittest
from collections import Counter
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest.mock import patch

from src.optimizer.data import (
    EXCLUSIVE_EQUIPMENT_SKILL_OPTION_COUNT,
    FRIBBELS_CHARACTER_SOURCE_REVISION,
    FRIBBELS_IMPRINT_EE_APPLICATION_GIT_BLOB_SHA1,
    FRIBBELS_IMPRINT_EE_DIALOG_GIT_BLOB_SHA1,
    IMPRINT_GRADE_ORDER,
    OPTIMIZER_PROFILE_CURRENT_VERSION,
    RUN_MANIFEST_CURRENT_VERSION,
    ExclusiveEquipmentEffectDataState,
    HeroModifierRepository,
    HeroModifierRepositoryError,
    OptimizerConfiguration,
    OptimizerProfileDocument,
    SchemaValidationError,
    SourceMetadata,
    freeze_json,
    load_bundled_character_catalog,
    load_bundled_character_repository,
    load_bundled_hero_modifier_repository,
    load_optimizer_profile,
    load_optimizer_profile_json,
    load_run_manifest,
)
from src.optimizer.domain import (
    FinalStat,
    GearSet,
    HeroModifierContribution,
    HeroModifiers,
    HeroModifierStatType,
    OptimizationRequest,
    SetPattern,
)


FIXTURES = Path(__file__).parent / "fixtures" / "optimizer"


class HeroModifierRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.characters = load_bundled_character_repository()
        cls.catalog = load_bundled_character_catalog()
        cls.repository = HeroModifierRepository(cls.characters)

    def _hero_id(self, name: str) -> str:
        return next(hero.hero_id for hero in self.characters.heroes if hero.name == name)

    def _profile_document(self, hero_id: str, modifiers: HeroModifiers) -> OptimizerProfileDocument:
        hero = self.characters.get(hero_id)
        profile = next(item for item in hero.base_profiles if item.level == 60 and item.stars == 6)
        request = OptimizationRequest(
            request_id="request.hero-modifier",
            hero_id=hero_id,
            base_profile_id=profile.profile_id,
            modifiers=modifiers,
            set_pattern=SetPattern((GearSet.SPEED, GearSet.HEALTH)),
        )
        return OptimizerProfileDocument(
            profile_id="profile.hero-modifier",
            name="Hero modifier selection",
            saved_at="2026-07-20T14:00:00Z",
            source=SourceMetadata(source_name="E7 Optimizer", source_version="0.6.0"),
            character_catalog_id=self.catalog.catalog_id,
            configuration=OptimizerConfiguration.from_request(request),
        )

    def test_all_bundled_records_map_offline_with_stable_identity_and_complete_type_coverage(self) -> None:
        with patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")):
            repository = load_bundled_hero_modifier_repository()
        self.assertEqual(387, len(repository.character_repository.heroes))
        self.assertEqual(133, len(repository.exclusive_equipment))
        self.assertEqual(
            tuple(sorted(item.equipment_id for item in repository.exclusive_equipment)),
            tuple(item.equipment_id for item in repository.exclusive_equipment),
        )
        self.assertEqual(
            133,
            len({item.equipment_id.casefold() for item in repository.exclusive_equipment}),
        )

        devotion_types = Counter()
        grade_shapes = Counter()
        ee_types = Counter()
        for hero in repository.character_repository.heroes:
            options = repository.imprint_options_for(hero.hero_id)
            self.assertTrue(options)
            self.assertEqual(hero.hero_id, options[0].hero_id)
            devotion_types[options[0].source_stat_type] += 1
            grade_shapes[tuple(item.grade for item in options)] += 1
            equipment = repository.exclusive_equipment_for(hero.hero_id)
            if equipment is not None:
                ee_types[equipment.source_stat_type] += 1
                self.assertEqual(hero.hero_id, equipment.hero_id)
                self.assertEqual(EXCLUSIVE_EQUIPMENT_SKILL_OPTION_COUNT, len(equipment.skill_options))
        self.assertEqual(
            {
                "acc": 82,
                "att": 37,
                "att_rate": 72,
                "coop": 2,
                "cri": 68,
                "def": 9,
                "def_rate": 13,
                "max_hp": 22,
                "max_hp_rate": 56,
                "res": 26,
            },
            dict(devotion_types),
        )
        self.assertEqual(
            {
                ("B", "A", "S", "SS", "SSS"): 209,
                ("C", "B", "A", "S", "SS", "SSS"): 71,
                ("D", "C", "B", "A", "S", "SS", "SSS"): 107,
            },
            dict(grade_shapes),
        )
        self.assertEqual(
            {"acc": 25, "att_rate": 31, "cri": 21, "def_rate": 3, "max_hp_rate": 18, "res": 5, "speed": 30},
            dict(ee_types),
        )

    def test_primary_source_evidence_and_canonical_grade_order_are_pinned(self) -> None:
        self.assertEqual("f49b0676c27d893ae4aa1b69920e4c98f37eb3fb", FRIBBELS_CHARACTER_SOURCE_REVISION)
        self.assertEqual("d82799ef58fcbe1e8ae19d72a0d8dd256630835e", FRIBBELS_IMPRINT_EE_DIALOG_GIT_BLOB_SHA1)
        self.assertEqual("532f826eeb4cea1a1345c70f0878f9d4038717c2", FRIBBELS_IMPRINT_EE_APPLICATION_GIT_BLOB_SHA1)
        self.assertEqual(("D", "C", "B", "A", "S", "SS", "SSS"), IMPRINT_GRADE_ORDER)

    def test_no_imprint_and_flat_rate_and_dual_attack_imprints_remain_distinct(self) -> None:
        ras_id = self._hero_id("Ras")
        self.assertIsNone(self.repository.select_imprint(ras_id).grade)
        ras = self.repository.select_imprint(ras_id, "sss")
        self.assertEqual(HeroModifierStatType.FLAT_HEALTH, ras.contribution.stat_type)
        self.assertEqual(920, ras.contribution.value)
        self.assertEqual(((FinalStat.HEALTH, 920),), ras.contribution.legacy_final_stat_bonus())

        seaside = self.repository.select_imprint(self._hero_id("Seaside Bellona"), "A")
        self.assertEqual(HeroModifierStatType.EFFECTIVENESS_PERCENT, seaside.contribution.stat_type)
        self.assertEqual(0.14, seaside.contribution.value)
        self.assertEqual(14, seaside.option.display_value)

        giselle = self.repository.select_imprint(self._hero_id("ae-GISELLE"), "S")
        self.assertEqual(HeroModifierStatType.ATTACK_PERCENT, giselle.contribution.stat_type)
        self.assertEqual(0.15, giselle.contribution.value)
        self.assertNotEqual(ras.contribution.stat_type, giselle.contribution.stat_type)

        clarissa = self.repository.select_imprint(self._hero_id("Clarissa"), "SSS")
        self.assertEqual(HeroModifierStatType.DUAL_ATTACK_CHANCE_PERCENT, clarissa.contribution.stat_type)
        self.assertEqual(0.09, clarissa.contribution.value)
        self.assertEqual((), clarissa.contribution.legacy_final_stat_bonus())

    def test_ee_rolls_use_exact_inclusive_range_and_keep_skill_choice_independent(self) -> None:
        arunka_id = self._hero_id("Arunka")
        arunka = self.repository.exclusive_equipment_for(arunka_id)
        self.assertIsNotNone(arunka)
        self.assertEqual(tuple(range(5, 11)), arunka.roll_display_values)
        self.assertEqual(HeroModifierStatType.SPEED, arunka.base_contribution.stat_type)
        self.assertEqual(
            (1, 2, 3),
            tuple(option.ordinal for option in arunka.skill_options),
        )
        self.assertTrue(
            all(
                option.effect_data_state is ExclusiveEquipmentEffectDataState.UNAVAILABLE_IN_SNAPSHOT
                and option.description is None
                and option.effect_value is None
                for option in arunka.skill_options
            )
        )
        selection = self.repository.select_exclusive_equipment(
            arunka_id,
            arunka.equipment_id,
            stat_display_value=7,
            skill_option_id=arunka.skill_options[2].option_id,
        )
        self.assertEqual(7, selection.contribution.value)
        self.assertEqual(arunka.skill_options[2].option_id, selection.skill_option_id)

        seaside_id = self._hero_id("Seaside Bellona")
        seaside = self.repository.exclusive_equipment_for(seaside_id)
        self.assertEqual(tuple(range(7, 15)), seaside.roll_display_values)
        percentage = self.repository.select_exclusive_equipment(
            seaside_id,
            seaside.equipment_id,
            stat_display_value=10,
        )
        self.assertEqual(HeroModifierStatType.ATTACK_PERCENT, percentage.contribution.stat_type)
        self.assertEqual(0.10, percentage.contribution.value)
        self.assertIsNone(percentage.skill_option_id)

    def test_combined_selection_produces_typed_modifier_record_and_preserves_other_fields(self) -> None:
        hero_id = self._hero_id("Arunka")
        equipment = self.repository.exclusive_equipment_for(hero_id)
        selection = self.repository.select(
            hero_id,
            imprint_grade="S",
            equipment_id=equipment.equipment_id,
            ee_stat_display_value=8,
            ee_skill_option_id=equipment.skill_options[1].option_id,
        )
        base = HeroModifiers(
            artifact_id="artifact.synthetic",
            artifact_level=15,
            custom_bonuses={FinalStat.HEALTH: 100},
            skill_options=("skill.damage.s2",),
        )
        modifiers = selection.apply_to_modifiers(base)
        self.assertEqual("artifact.synthetic", modifiers.artifact_id)
        self.assertEqual(((FinalStat.HEALTH, 100),), modifiers.custom_bonuses)
        self.assertEqual(("skill.damage.s2",), modifiers.skill_options)
        self.assertEqual(HeroModifierContribution(HeroModifierStatType.ATTACK_PERCENT, 0.15), modifiers.imprint_contribution)
        self.assertEqual(((FinalStat.ATTACK, 15.0),), modifiers.imprint_bonuses)
        self.assertEqual(HeroModifierContribution(HeroModifierStatType.SPEED, 8), modifiers.exclusive_equipment_contribution)
        self.assertEqual(((FinalStat.SPEED, 8),), modifiers.exclusive_equipment_bonuses)
        self.assertEqual(equipment.skill_options[1].option_id, modifiers.exclusive_equipment_skill_option_id)
        self.assertEqual(modifiers, HeroModifiers.from_dict(modifiers.to_dict()))

    def test_no_ee_is_distinct_and_incompatible_configuration_fails(self) -> None:
        ras_id = self._hero_id("Ras")
        selection = self.repository.select(ras_id)
        modifiers = selection.apply_to_modifiers()
        self.assertIsNone(modifiers.imprint_level)
        self.assertIsNone(modifiers.exclusive_equipment_id)
        self.assertIsNone(modifiers.exclusive_equipment_contribution)
        with self.assertRaisesRegex(HeroModifierRepositoryError, "require selected equipment"):
            self.repository.select_exclusive_equipment(ras_id, stat_display_value=5)
        arunka = self.repository.exclusive_equipment_for(self._hero_id("Arunka"))
        with self.assertRaisesRegex(HeroModifierRepositoryError, "belongs to"):
            self.repository.select_exclusive_equipment(
                ras_id,
                arunka.equipment_id,
                stat_display_value=5,
            )

    def test_invalid_grade_roll_skill_identity_and_hero_fail_actionably(self) -> None:
        arunka_id = self._hero_id("Arunka")
        arunka = self.repository.exclusive_equipment_for(arunka_id)
        cases = (
            (lambda: self.repository.select_imprint("hero.missing", "S"), "unknown-hero-id"),
            (lambda: self.repository.select_imprint(arunka_id, "D"), "unknown-imprint-grade"),
            (
                lambda: self.repository.select_exclusive_equipment(
                    arunka_id,
                    arunka.equipment_id,
                    stat_display_value=4,
                ),
                "invalid-ee-stat-value",
            ),
            (
                lambda: self.repository.select_exclusive_equipment(
                    arunka_id,
                    arunka.equipment_id,
                    stat_display_value=5.5,
                ),
                "invalid-integer",
            ),
            (
                lambda: self.repository.select_exclusive_equipment(
                    arunka_id,
                    arunka.equipment_id,
                    stat_display_value=5,
                    skill_option_id=f"{arunka.equipment_id}.skill-option.9",
                ),
                "unknown-ee-skill-option-id",
            ),
        )
        for operation, code in cases:
            with self.subTest(code=code), self.assertRaises(HeroModifierRepositoryError) as raised:
                operation()
            self.assertEqual(code, raised.exception.code)

    def test_malformed_rich_source_fields_fail_without_mutating_canonical_records(self) -> None:
        ras = self.characters.get(self._hero_id("Ras"))
        cases = (
            ({"type": "team_att_rate", "grades": {"S": 0.1}}, "unsupported-stat-type"),
            ({"type": "att_rate", "grades": {"X": 0.1}}, "unsupported-imprint-grade"),
            ({"type": "att_rate", "grades": {"S": True}}, "invalid-source-number"),
        )
        for devotion, code in cases:
            with self.subTest(code=code):
                malformed = replace(ras, self_devotion=freeze_json(devotion))
                with self.assertRaises(HeroModifierRepositoryError) as raised:
                    HeroModifierRepository._build_imprints(malformed)
                self.assertEqual(code, raised.exception.code)
        self.assertEqual("max_hp", ras.self_devotion["type"])

    def test_records_and_repository_are_immutable(self) -> None:
        arunka = self.repository.exclusive_equipment_for(self._hero_id("Arunka"))
        with self.assertRaises(FrozenInstanceError):
            arunka.hero_id = "changed"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            arunka.raw_source["new"] = 1  # type: ignore[index]
        with self.assertRaises(AttributeError):
            self.repository._ee_by_id = {}  # type: ignore[misc]

    def test_selected_and_empty_states_round_trip_and_resolve_against_catalog(self) -> None:
        hero_id = self._hero_id("Arunka")
        equipment = self.repository.exclusive_equipment_for(hero_id)
        selected = self.repository.select(
            hero_id,
            imprint_grade="SS",
            equipment_id=equipment.equipment_id,
            ee_stat_display_value=9,
            ee_skill_option_id=equipment.skill_options[0].option_id,
        )
        document = self._profile_document(hero_id, selected.apply_to_modifiers())
        reloaded = load_optimizer_profile_json(document.to_json(), character_catalog=self.catalog)
        self.assertEqual(OPTIMIZER_PROFILE_CURRENT_VERSION, reloaded.to_dict()["schemaVersion"])
        restored_modifiers = reloaded.create_request("request.restored").modifiers
        self.assertEqual(selected, self.repository.select_from_modifiers(hero_id, restored_modifiers))

        empty = self.repository.select(hero_id)
        empty_document = self._profile_document(hero_id, empty.apply_to_modifiers())
        empty_reloaded = load_optimizer_profile_json(empty_document.to_json(), character_catalog=self.catalog)
        self.assertEqual(empty, self.repository.select_from_modifiers(hero_id, empty_reloaded.create_request("request.empty").modifiers))

    def test_v1_and_v2_documents_migrate_without_inventing_typed_selection(self) -> None:
        profile_v1 = json.loads((FIXTURES / "optimizer-profile-v1.json").read_text(encoding="utf-8"))
        original = copy.deepcopy(profile_v1)
        migrated_v1 = load_optimizer_profile(profile_v1)
        self.assertEqual(original, profile_v1)
        self.assertEqual(OPTIMIZER_PROFILE_CURRENT_VERSION, migrated_v1.to_dict()["schemaVersion"])
        migrated_modifiers = migrated_v1.configuration.to_dict()["modifiers"]
        self.assertIsNone(migrated_modifiers["imprintContribution"])
        self.assertIsNone(migrated_modifiers["exclusiveEquipmentContribution"])
        self.assertIsNone(migrated_modifiers["exclusiveEquipmentSkillOptionId"])

        hero_id = self._hero_id("Ras")
        profile_v2 = self._profile_document(hero_id, HeroModifiers()).to_dict()
        profile_v2["schemaVersion"] = 2
        del profile_v2["configuration"]["itemProjectionMode"]
        del profile_v2["configuration"]["gearFilters"]
        del profile_v2["configuration"]["maximumReplacementDistance"]
        modifiers = profile_v2["configuration"]["modifiers"]
        for field in (
            "imprintContribution",
            "exclusiveEquipmentContribution",
            "exclusiveEquipmentSkillOptionId",
            "customContributions",
        ):
            del modifiers[field]
        del profile_v2["configuration"]["skillContexts"]
        migrated_v2 = load_optimizer_profile(profile_v2)
        self.assertEqual(OPTIMIZER_PROFILE_CURRENT_VERSION, migrated_v2.to_dict()["schemaVersion"])
        with self.assertRaisesRegex(SchemaValidationError, "must not already contain version-3 field"):
            smuggled = copy.deepcopy(profile_v2)
            smuggled["configuration"]["modifiers"]["imprintContribution"] = None
            load_optimizer_profile(smuggled)

        run_v1 = json.loads((FIXTURES / "run-manifest-v1.json").read_text(encoding="utf-8"))
        migrated_run = load_run_manifest(run_v1)
        self.assertEqual(RUN_MANIFEST_CURRENT_VERSION, migrated_run.to_dict()["schemaVersion"])
        self.assertIsNone(migrated_run.request_snapshot.modifiers.imprint_contribution)

    def test_catalog_validation_rejects_tampered_imprint_ee_roll_and_skill_option(self) -> None:
        hero_id = self._hero_id("Arunka")
        equipment = self.repository.exclusive_equipment_for(hero_id)
        selected = self.repository.select(
            hero_id,
            imprint_grade="S",
            equipment_id=equipment.equipment_id,
            ee_stat_display_value=8,
            ee_skill_option_id=equipment.skill_options[0].option_id,
        )
        base = self._profile_document(hero_id, selected.apply_to_modifiers()).to_dict()

        imprint_drift = copy.deepcopy(base)
        modifiers = imprint_drift["configuration"]["modifiers"]
        modifiers["imprintContribution"]["value"] = 0.16
        modifiers["imprintBonuses"]["final_stat.attack"] = 16
        with self.assertRaisesRegex(SchemaValidationError, "imprint-contribution-drift"):
            load_optimizer_profile(imprint_drift, character_catalog=self.catalog)

        ee_drift = copy.deepcopy(base)
        modifiers = ee_drift["configuration"]["modifiers"]
        modifiers["exclusiveEquipmentContribution"]["value"] = 11
        modifiers["exclusiveEquipmentBonuses"]["final_stat.speed"] = 11
        with self.assertRaisesRegex(SchemaValidationError, "invalid-ee-stat-value"):
            load_optimizer_profile(ee_drift, character_catalog=self.catalog)

        option_drift = copy.deepcopy(base)
        modifiers = option_drift["configuration"]["modifiers"]
        modifiers["exclusiveEquipmentSkillOptionId"] = f"{equipment.equipment_id}.skill-option.9"
        with self.assertRaisesRegex(SchemaValidationError, "unknown-ee-skill-option-id"):
            load_optimizer_profile(option_drift, character_catalog=self.catalog)


if __name__ == "__main__":
    unittest.main()
