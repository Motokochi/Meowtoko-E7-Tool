from __future__ import annotations

import copy
import json
import math
import socket
import unittest
from collections import Counter
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest.mock import patch

from src.optimizer.data import (
    OPTIMIZER_PROFILE_CURRENT_VERSION,
    RUN_MANIFEST_CURRENT_VERSION,
    SOURCE_SKILL_OPTION_ID_PREFIX,
    CustomBonusSelection,
    OptimizerConfiguration,
    OptimizerProfileDocument,
    RunCompletionState,
    RunManifestDocument,
    SchemaValidationError,
    SkillContextRepository,
    SkillContextRepositoryError,
    SourceMetadata,
    load_bundled_character_catalog,
    load_bundled_character_repository,
    load_bundled_skill_context_repository,
    load_optimizer_profile,
    load_optimizer_profile_json,
    load_run_manifest,
    load_run_manifest_json,
)
from src.optimizer.domain import (
    ExecutionPreference,
    FinalStat,
    GearSet,
    HeroModifierContribution,
    HeroModifierStatType,
    HeroModifiers,
    OptimizationRequest,
    SearchSummary,
    SetPattern,
    SkillContext,
    SkillHitType,
    SkillSlot,
)


FIXTURES = Path(__file__).parent / "fixtures" / "optimizer"


class SkillContextRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.characters = load_bundled_character_repository()
        cls.catalog = load_bundled_character_catalog()
        cls.repository = SkillContextRepository(cls.characters)

    def _hero_id(self, name: str) -> str:
        return next(hero.hero_id for hero in self.characters.heroes if hero.name == name)

    def _request(
        self,
        hero_id: str,
        *,
        modifiers: HeroModifiers | None = None,
        target_defense: int = 1000,
    ) -> OptimizationRequest:
        hero = self.characters.get(hero_id)
        profile = next(item for item in hero.base_profiles if item.level == 60 and item.stars == 6)
        return OptimizationRequest(
            request_id="request.skill-context",
            hero_id=hero_id,
            base_profile_id=profile.profile_id,
            modifiers=HeroModifiers() if modifiers is None else modifiers,
            set_pattern=SetPattern((GearSet.SPEED, GearSet.HEALTH)),
            target_defense=target_defense,
        )

    def _profile(self, request: OptimizationRequest) -> OptimizerProfileDocument:
        return OptimizerProfileDocument(
            profile_id="profile.skill-context",
            name="Skill context",
            saved_at="2026-07-20T15:00:00Z",
            source=SourceMetadata(source_name="E7 Optimizer", source_version="0.6.0"),
            character_catalog_id=self.catalog.catalog_id,
            configuration=OptimizerConfiguration.from_request(request),
        )

    def _run(self, request: OptimizationRequest) -> RunManifestDocument:
        return RunManifestDocument(
            run_id="run.skill-context",
            created_at="2026-07-20T15:00:00Z",
            completed_at="2026-07-20T15:00:01Z",
            completion_state=RunCompletionState.CANCELLED,
            source=SourceMetadata(source_name="E7 Optimizer", source_version="0.6.0"),
            request_snapshot=request,
            summary=SearchSummary(
                request_id=request.request_id,
                evaluated_permutations=12,
                exact_count=0,
                one_away_count=0,
                two_away_count=0,
                duration_seconds=1,
                execution_preference=ExecutionPreference.CPU,
                cancelled=True,
            ),
            result_store=None,
        )

    def test_all_skill_records_and_source_options_map_offline_with_stable_identity(self) -> None:
        with patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")):
            repository = load_bundled_skill_context_repository()
        self.assertEqual(1161, len(repository.records))
        self.assertEqual(211, len(repository.source_options))
        self.assertEqual(
            211,
            len({option.option_id.casefold() for option in repository.source_options}),
        )
        self.assertEqual(
            tuple(sorted(option.option_id for option in repository.source_options)),
            tuple(option.option_id for option in repository.source_options),
        )
        for hero in repository.character_repository.heroes:
            skills = repository.skills_for(hero.hero_id)
            self.assertEqual(tuple(SkillSlot), tuple(skill.skill for skill in skills))
            self.assertTrue(all(skill.hero_id == hero.hero_id for skill in skills))

    def test_direct_and_option_source_shapes_are_lossless_and_complete(self) -> None:
        field_counts = Counter()
        hit_counts = Counter()
        target_counts = Counter()
        penetration_values = Counter()
        option_field_counts = Counter()
        heroes_with_options = set()
        maximum_options = 0
        for record in self.repository.records:
            field_counts.update(record.raw_source.keys())
            hit_counts.update(item.value for item in record.hit_types)
            if record.target_count is not None:
                target_counts[record.target_count] += 1
            if record.penetration is not None:
                penetration_values[record.penetration] += 1
            maximum_options = max(maximum_options, len(record.options))
            for option in record.options:
                heroes_with_options.add(option.hero_id)
                option_field_counts.update(option.raw_source.keys())
        self.assertEqual(1161, field_counts["hitTypes"])
        self.assertEqual(1161, field_counts["options"])
        self.assertEqual(840, field_counts["rate"])
        self.assertEqual(840, field_counts["pow"])
        self.assertEqual(836, field_counts["targets"])
        self.assertEqual(61, field_counts["penetration"])
        self.assertEqual(75, field_counts["note"])
        self.assertEqual(141, field_counts["selfHpScaling"])
        self.assertEqual(39, field_counts["selfDefScaling"])
        self.assertEqual(47, field_counts["selfSpdScaling"])
        self.assertEqual(2, field_counts["extraSelfAtkScaling"])
        self.assertEqual(1, field_counts["extraSelfDefScaling"])
        self.assertEqual(2, field_counts["increasedValue"])
        self.assertEqual(1, field_counts["cdmgIncrease"])
        self.assertEqual({1: 633, 2: 9, 3: 194}, dict(target_counts))
        self.assertEqual({0.2, 0.3, 0.5, 0.6, 0.7, 0.8, 1}, set(penetration_values))
        self.assertEqual(
            {"hit.critical", "hit.crushing", "hit.normal", "hit.miss"},
            set(hit_counts),
        )
        self.assertEqual(144, len(heroes_with_options))
        self.assertEqual(2, maximum_options)
        self.assertEqual(211, option_field_counts["name"])
        self.assertEqual(211, option_field_counts["rate"])
        self.assertEqual(211, option_field_counts["pow"])
        self.assertEqual(122, option_field_counts["targets"])
        self.assertEqual(170, option_field_counts["selfHpScaling"])
        self.assertEqual(39, option_field_counts["selfAtkScaling"])
        self.assertEqual(8, option_field_counts["selfDefScaling"])
        self.assertTrue(all(not option.is_damaging for option in self.repository.source_options))

    def test_sparse_non_damaging_and_missing_target_records_remain_explicit(self) -> None:
        achates_s2 = self.repository.get(self._hero_id("Achates"), SkillSlot.S2)
        self.assertFalse(achates_s2.is_damaging)
        self.assertEqual((), achates_s2.hit_types)
        self.assertIsNone(achates_s2.rate)
        self.assertIsNone(achates_s2.power)
        self.assertIsNone(achates_s2.target_count)
        self.assertEqual(1, len(achates_s2.options))

        scout_s1 = self.repository.get(self._hero_id("Mighty Scout"), SkillSlot.S1)
        self.assertTrue(scout_s1.is_damaging)
        self.assertEqual(0, scout_s1.rate)
        self.assertEqual(0, scout_s1.power)
        self.assertIsNone(scout_s1.target_count)

    def test_duplicate_option_names_do_not_collide_and_raw_records_are_immutable(self) -> None:
        brieg_s2 = self.repository.get(self._hero_id("Brieg"), SkillSlot.S2)
        self.assertEqual(("S2 barrier", "S2 barrier"), tuple(item.name for item in brieg_s2.options))
        self.assertEqual(2, len({item.option_id for item in brieg_s2.options}))
        self.assertTrue(all(item.option_id.startswith(SOURCE_SKILL_OPTION_ID_PREFIX) for item in brieg_s2.options))
        with self.assertRaises(FrozenInstanceError):
            brieg_s2.hero_id = "changed"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            brieg_s2.raw_source["new"] = 1  # type: ignore[index]
        with self.assertRaises(AttributeError):
            self.repository._records = ()  # type: ignore[misc]

    def test_custom_bonuses_preserve_every_required_flat_and_percentage_axis(self) -> None:
        contributions = (
            HeroModifierContribution(HeroModifierStatType.EFFECT_RESISTANCE_PERCENT, 0.2),
            HeroModifierContribution(HeroModifierStatType.FLAT_ATTACK, 100),
            HeroModifierContribution(HeroModifierStatType.ATTACK_PERCENT, 0.15),
            HeroModifierContribution(HeroModifierStatType.FLAT_HEALTH, 500),
            HeroModifierContribution(HeroModifierStatType.HEALTH_PERCENT, 0.1),
            HeroModifierContribution(HeroModifierStatType.FLAT_DEFENSE, 50),
            HeroModifierContribution(HeroModifierStatType.DEFENSE_PERCENT, 0.08),
            HeroModifierContribution(HeroModifierStatType.SPEED, 0),
            HeroModifierContribution(HeroModifierStatType.CRITICAL_HIT_CHANCE_PERCENT, 0.05),
            HeroModifierContribution(HeroModifierStatType.EFFECTIVENESS_PERCENT, 0.12),
        )
        selection = CustomBonusSelection(contributions)
        modifiers = selection.apply_to_modifiers(
            HeroModifiers(artifact_id="artifact.proof", artifact_level=15)
        )
        self.assertEqual("artifact.proof", modifiers.artifact_id)
        self.assertEqual(
            tuple(
                stat_type
                for stat_type in HeroModifierStatType
                if stat_type in {item.stat_type for item in contributions}
            ),
            tuple(item.stat_type for item in modifiers.custom_contributions),
        )
        self.assertEqual(115.0, dict(modifiers.custom_bonuses)[FinalStat.ATTACK])
        self.assertEqual(510.0, dict(modifiers.custom_bonuses)[FinalStat.HEALTH])
        self.assertEqual(58.0, dict(modifiers.custom_bonuses)[FinalStat.DEFENSE])
        self.assertEqual(0, dict(modifiers.custom_bonuses)[FinalStat.SPEED])
        self.assertEqual(modifiers, HeroModifiers.from_dict(modifiers.to_dict()))

    def test_custom_bonus_invalid_duplicates_dual_and_numbers_fail(self) -> None:
        with self.assertRaisesRegex(SkillContextRepositoryError, "duplicate-custom-bonus"):
            CustomBonusSelection(
                (
                    HeroModifierContribution(HeroModifierStatType.SPEED, 1),
                    HeroModifierContribution(HeroModifierStatType.SPEED, 2),
                )
            )
        with self.assertRaisesRegex(SkillContextRepositoryError, "unsupported-custom-bonus"):
            CustomBonusSelection(
                (
                    HeroModifierContribution(HeroModifierStatType.DUAL_ATTACK_CHANCE_PERCENT, 0.01),
                )
            )
        for value in (-1, True, math.inf, math.nan):
            with self.subTest(value=value), self.assertRaises(ValueError):
                CustomBonusSelection(((HeroModifierStatType.SPEED, value),))

    def test_context_resolution_preserves_hit_targets_penetration_and_defense_per_skill(self) -> None:
        hero_id = self._hero_id("Amiki")
        contexts = (
            SkillContext(
                SkillSlot.S1,
                900,
                hit_type=SkillHitType.NORMAL,
                target_count_override=2,
                penetration_override=0.8,
            ),
            SkillContext(SkillSlot.S2, 1200),
            SkillContext(SkillSlot.S3, 1500, hit_type=SkillHitType.MISS),
        )
        selection = self.repository.select(hero_id, contexts)
        s1, s2, s3 = selection.skills
        self.assertEqual(2, s1.effective_target_count)
        self.assertEqual(0.8, s1.effective_penetration)
        self.assertTrue(s1.uses_target_count_override)
        self.assertTrue(s1.uses_penetration_override)
        self.assertIsNone(s2.effective_target_count)
        self.assertIsNone(s2.effective_penetration)
        self.assertEqual(1, s3.effective_target_count)
        self.assertEqual(0.7, s3.effective_penetration)
        self.assertEqual((900, 1200, 1500), tuple(item.context.target_defense for item in selection.skills))

    def test_source_targets_cover_one_two_three_and_unset_without_fabrication(self) -> None:
        arbiter_id = self._hero_id("Arbiter Vildred")
        arbiter = self.repository.select(
            arbiter_id,
            (
                SkillContext(SkillSlot.S1, 1000, hit_type=SkillHitType.CRITICAL),
                SkillContext(SkillSlot.S2, 1000),
                SkillContext(SkillSlot.S3, 1000, hit_type=SkillHitType.CRUSHING),
            ),
        )
        self.assertEqual((2, None, 3), tuple(item.effective_target_count for item in arbiter.skills))
        amiki = self.repository.get(self._hero_id("Amiki"), SkillSlot.S1)
        self.assertEqual(1, amiki.target_count)
        scout = self.repository.select_context(
            self._hero_id("Mighty Scout"),
            SkillContext(SkillSlot.S1, 1000, hit_type=SkillHitType.NORMAL),
        )
        self.assertIsNone(scout.effective_target_count)

    def test_non_damaging_option_is_selectable_without_fake_hit_or_penetration(self) -> None:
        hero_id = self._hero_id("Achates")
        s2 = self.repository.get(hero_id, SkillSlot.S2)
        context = SkillContext(
            SkillSlot.S2,
            777,
            source_option_id=s2.options[0].option_id,
        )
        selected = self.repository.select_context(hero_id, context)
        self.assertFalse(selected.is_damaging)
        self.assertIsNone(selected.effective_target_count)
        self.assertIsNone(selected.effective_penetration)
        self.assertIsNone(selected.context.hit_type)
        with self.assertRaisesRegex(SkillContextRepositoryError, "penetration-not-applicable"):
            self.repository.select_context(
                hero_id,
                replace(context, penetration_override=0.2),
            )
        with self.assertRaisesRegex(SkillContextRepositoryError, "hit-type-not-applicable"):
            self.repository.select_context(
                hero_id,
                replace(context, hit_type=SkillHitType.NORMAL),
            )
        with self.assertRaisesRegex(SkillContextRepositoryError, "target-count-not-applicable"):
            self.repository.select_context(
                hero_id,
                replace(context, target_count_override=1),
            )

        ras_id = self._hero_id("Adventurer Ras")
        ras_option = self.repository.get(ras_id, SkillSlot.S3).options[0]
        mixed = self.repository.select_context(
            ras_id,
            SkillContext(SkillSlot.S3, 1100, source_option_id=ras_option.option_id),
        )
        self.assertTrue(mixed.record.is_damaging)
        self.assertFalse(mixed.source_option.is_damaging)
        self.assertFalse(mixed.is_damaging)
        self.assertIsNone(mixed.effective_target_count)
        self.assertIsNone(mixed.effective_penetration)
        for field, value in (
            ("hit_type", SkillHitType.NORMAL),
            ("target_count_override", 1),
            ("penetration_override", 0.2),
        ):
            with self.subTest(field=field), self.assertRaises(SkillContextRepositoryError):
                self.repository.select_context(
                    ras_id,
                    SkillContext(
                        SkillSlot.S3,
                        1100,
                        source_option_id=ras_option.option_id,
                        **{field: value},
                    ),
                )

    def test_option_identity_rejects_unknown_cross_hero_cross_skill_and_ee_namespaces(self) -> None:
        achates_id = self._hero_id("Achates")
        achates_s3 = self.repository.get(achates_id, SkillSlot.S3)
        option_id = achates_s3.options[0].option_id
        cases = (
            (SkillContext(SkillSlot.S1, 1000, source_option_id=option_id), "source-option-skill-mismatch"),
            (
                SkillContext(
                    SkillSlot.S3,
                    1000,
                    source_option_id="skill-option.fribbels.missing.s3.0.deadbeef",
                ),
                "unknown-source-option-id",
            ),
            (
                SkillContext(
                    SkillSlot.S3,
                    1000,
                    source_option_id="exclusive-equipment.fribbels.achates.0.deadbeef.skill-option.1",
                ),
                "ee-option-namespace",
            ),
        )
        for context, code in cases:
            with self.subTest(code=code), self.assertRaises(SkillContextRepositoryError) as raised:
                self.repository.select_context(achates_id, context)
            self.assertEqual(code, raised.exception.code)
        with self.assertRaisesRegex(SkillContextRepositoryError, "source-option-hero-mismatch"):
            self.repository.select_context(self._hero_id("Ras"), SkillContext(SkillSlot.S3, 1000, source_option_id=option_id))

    def test_profile_and_run_round_trip_custom_and_three_independent_contexts(self) -> None:
        hero_id = self._hero_id("Achates")
        skills = self.repository.skills_for(hero_id)
        contexts = (
            SkillContext(
                SkillSlot.S1,
                800,
                hit_type=SkillHitType.CRITICAL,
                target_count_override=2,
                penetration_override=0.5,
            ),
            SkillContext(SkillSlot.S2, 1200, source_option_id=skills[1].options[0].option_id),
            SkillContext(SkillSlot.S3, 1600, source_option_id=skills[2].options[1].option_id),
        )
        request = self.repository.select(hero_id, contexts).apply_to_request(self._request(hero_id))
        modifiers = CustomBonusSelection(
            {
                HeroModifierStatType.FLAT_ATTACK: 100,
                HeroModifierStatType.ATTACK_PERCENT: 0.15,
                HeroModifierStatType.SPEED: 5,
            }
        ).apply_to_modifiers(request.modifiers)
        request = replace(request, modifiers=modifiers)

        profile = load_optimizer_profile_json(self._profile(request).to_json(), character_catalog=self.catalog)
        restored = profile.create_request("request.profile-restored")
        self.assertEqual(request.modifiers, restored.modifiers)
        self.assertEqual(request.skill_contexts, restored.skill_contexts)
        self.repository.validate_request(restored)

        run = load_run_manifest_json(self._run(request).to_json(), character_catalog=self.catalog)
        self.assertEqual(request.modifiers, run.request_snapshot.modifiers)
        self.assertEqual(request.skill_contexts, run.request_snapshot.skill_contexts)

    def test_v1_v2_v3_migrate_target_defense_without_inventing_options_or_custom_types(self) -> None:
        profile_v1 = json.loads((FIXTURES / "optimizer-profile-v1.json").read_text(encoding="utf-8"))
        migrated_v1 = load_optimizer_profile(profile_v1)
        self.assertEqual(OPTIMIZER_PROFILE_CURRENT_VERSION, migrated_v1.to_dict()["schemaVersion"])
        request_v1 = migrated_v1.create_request("request.v1")
        self.assertEqual((), request_v1.modifiers.custom_contributions)
        self.assertEqual((1200, 1200, 1200), tuple(item.target_defense for item in request_v1.skill_contexts))
        self.assertTrue(all(item.source_option_id is None for item in request_v1.skill_contexts))

        current = self._profile(self._request(self._hero_id("Ras"), target_defense=1400)).to_dict()
        for old_version, fields_to_remove in (
            (3, ("customContributions",)),
            (2, ("imprintContribution", "exclusiveEquipmentContribution", "exclusiveEquipmentSkillOptionId", "customContributions")),
        ):
            with self.subTest(version=old_version):
                payload = copy.deepcopy(current)
                payload["schemaVersion"] = old_version
                del payload["configuration"]["itemProjectionMode"]
                del payload["configuration"]["gearFilters"]
                del payload["configuration"]["maximumReplacementDistance"]
                del payload["configuration"]["skillContexts"]
                for field in fields_to_remove:
                    del payload["configuration"]["modifiers"][field]
                migrated = load_optimizer_profile(payload)
                self.assertEqual(OPTIMIZER_PROFILE_CURRENT_VERSION, migrated.to_dict()["schemaVersion"])
                self.assertEqual((1400, 1400, 1400), tuple(item.target_defense for item in migrated.create_request("request.old").skill_contexts))

        profile_v3 = copy.deepcopy(current)
        profile_v3["schemaVersion"] = 3
        del profile_v3["configuration"]["itemProjectionMode"]
        del profile_v3["configuration"]["gearFilters"]
        del profile_v3["configuration"]["maximumReplacementDistance"]
        del profile_v3["configuration"]["modifiers"]["customContributions"]
        del profile_v3["configuration"]["skillContexts"]
        smuggled = copy.deepcopy(profile_v3)
        smuggled["configuration"]["skillContexts"] = []
        with self.assertRaisesRegex(SchemaValidationError, "must not already contain version-4 field"):
            load_optimizer_profile(smuggled)

        run_v1 = json.loads((FIXTURES / "run-manifest-v1.json").read_text(encoding="utf-8"))
        migrated_run = load_run_manifest(run_v1)
        self.assertEqual(RUN_MANIFEST_CURRENT_VERSION, migrated_run.to_dict()["schemaVersion"])
        self.assertEqual((1200, 1200, 1200), tuple(item.target_defense for item in migrated_run.request_snapshot.skill_contexts))

    def test_catalog_validation_rejects_hit_option_projection_and_context_drift(self) -> None:
        hero_id = self._hero_id("Achates")
        skills = self.repository.skills_for(hero_id)
        contexts = (
            SkillContext(SkillSlot.S1, 1000, hit_type=SkillHitType.CRITICAL),
            SkillContext(SkillSlot.S2, 1000, source_option_id=skills[1].options[0].option_id),
            SkillContext(SkillSlot.S3, 1000),
        )
        request = self.repository.select(hero_id, contexts).apply_to_request(self._request(hero_id))
        base = self._profile(request).to_dict()

        bad_hit = copy.deepcopy(base)
        bad_hit["configuration"]["skillContexts"][1]["hitType"] = "hit.normal"
        with self.assertRaisesRegex(SchemaValidationError, "hit-type-not-applicable"):
            load_optimizer_profile(bad_hit, character_catalog=self.catalog)

        missing_projection = copy.deepcopy(base)
        missing_projection["configuration"]["modifiers"]["skillOptions"] = []
        with self.assertRaisesRegex(SchemaValidationError, "source-option-projection-drift"):
            load_optimizer_profile(missing_projection, character_catalog=self.catalog)

        wrong_skill = copy.deepcopy(base)
        wrong_skill["configuration"]["skillContexts"][0]["sourceOptionId"] = skills[1].options[0].option_id
        wrong_skill["configuration"]["modifiers"]["skillOptions"] = [skills[1].options[0].option_id]
        with self.assertRaisesRegex(SchemaValidationError, "source-option-skill-mismatch"):
            load_optimizer_profile(wrong_skill, character_catalog=self.catalog)


if __name__ == "__main__":
    unittest.main()
