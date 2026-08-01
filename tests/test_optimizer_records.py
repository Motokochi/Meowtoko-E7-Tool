import json
import math
import unittest
from dataclasses import FrozenInstanceError

from src.optimizer.domain import (
    MAX_RESULT_CAP,
    ArtifactDefinition,
    BuildMetrics,
    DomainValidationError,
    ExecutionPreference,
    FinalStat,
    GearItem,
    GearSearchFilters,
    GearSet,
    GearSlot,
    HeroBaseProfile,
    HeroDefinition,
    HeroModifierContribution,
    HeroModifierStatType,
    HeroModifiers,
    ItemProjectionMode,
    ItemStatType,
    OptimizationRequest,
    SearchSummary,
    SetPattern,
    SkillContext,
    SkillHitType,
    SkillSlot,
    StatRange,
    custom_bonus_projection,
)


def final_stats():
    return {
        FinalStat.ATTACK: 1200,
        FinalStat.HEALTH: 6000,
        FinalStat.DEFENSE: 700,
        FinalStat.SPEED: 110,
        FinalStat.CRITICAL_HIT_CHANCE: 15,
        FinalStat.CRITICAL_HIT_DAMAGE: 150,
        FinalStat.EFFECTIVENESS: 0,
        FinalStat.EFFECT_RESISTANCE: 0,
    }


def profile(profile_id="hero.profile.60"):
    return HeroBaseProfile(
        profile_id=profile_id,
        dense_id=3,
        label="Level 60 / 6★ awakened",
        level=60,
        stars=6,
        final_stats=final_stats(),
    )


def modifiers():
    return HeroModifiers(
        artifact_id="artifact.proof_of_valor",
        artifact_level=30,
        imprint_level="SSS",
        imprint_bonuses={FinalStat.HEALTH: 12},
        exclusive_equipment_id="ee.hero.option_2",
        exclusive_equipment_bonuses={FinalStat.SPEED: 10},
        custom_bonuses={FinalStat.ATTACK: 100},
        skill_options=("skill.s2.enabled", "skill.s3.full_focus"),
    )


def request():
    return OptimizationRequest(
        request_id="request.1",
        hero_id="hero.1",
        base_profile_id="hero.profile.60",
        modifiers=modifiers(),
        set_pattern=SetPattern((GearSet.SPEED, GearSet.HEALTH)),
        stat_ranges={
            FinalStat.ATTACK: StatRange(minimum=0, maximum=4000),
            FinalStat.SPEED: StatRange(minimum=200),
        },
        stat_priorities={FinalStat.ATTACK: 2, FinalStat.SPEED: 3, FinalStat.HEALTH: -1},
        derived_metric_ranges={"metric.ehp": StatRange(minimum=100_000)},
        include_equipped=True,
        near_set_tolerance=0,
        target_defense=1500,
        result_cap=MAX_RESULT_CAP,
        execution_preference=ExecutionPreference.GPU,
    )


def build_metrics():
    values = final_stats()
    values[FinalStat.SPEED] = 205
    return BuildMetrics(
        final_stats=values,
        derived_metrics={"metric.ehp": 110_000, "metric.damage": 4250.5},
        priority_score=87.25,
    )


class StatRangeTests(unittest.TestCase):
    def test_blank_and_zero_are_distinct_and_round_trip(self):
        blank = StatRange()
        zero = StatRange(minimum=0, maximum=0)
        self.assertEqual(blank.to_dict(), {"minimum": None, "maximum": None})
        self.assertEqual(zero.to_dict(), {"minimum": 0, "maximum": 0})
        self.assertEqual(StatRange.from_dict(blank.to_dict()), blank)
        self.assertEqual(StatRange.from_dict(zero.to_dict()), zero)
        self.assertTrue(zero.contains(0))

    def test_invalid_or_non_finite_ranges_are_rejected(self):
        for kwargs in (
            {"minimum": 2, "maximum": 1},
            {"minimum": math.nan},
            {"maximum": math.inf},
            {"minimum": True},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(DomainValidationError):
                    StatRange(**kwargs)


class SetPatternTests(unittest.TestCase):
    def test_four_plus_two_and_three_repeatable_sets_are_canonical(self):
        four_plus_two = SetPattern((GearSet.HEALTH, GearSet.SPEED))
        triple = SetPattern((GearSet.HEALTH, GearSet.HEALTH, GearSet.HEALTH))
        self.assertEqual(four_plus_two.sets, (GearSet.SPEED, GearSet.HEALTH))
        self.assertEqual(four_plus_two.kind, "4+2")
        self.assertEqual(triple.kind, "2+2+2")
        self.assertEqual(SetPattern.from_dict(triple.to_dict()), triple)

    def test_optional_requirements_are_valid_and_impossible_or_repeated_sets_are_rejected(self):
        for sets in (
            (),
            (GearSet.SPEED,),
            (GearSet.HEALTH, GearSet.DEFENSE),
        ):
            with self.subTest(valid_sets=sets):
                self.assertEqual("flexible", SetPattern(sets).kind)

        invalid = (
            (GearSet.SPEED, GearSet.ATTACK),
            (GearSet.IMMUNITY, GearSet.IMMUNITY, GearSet.HEALTH),
            (GearSet.PENETRATION, GearSet.PENETRATION, GearSet.TORRENT),
            (GearSet.HEALTH, GearSet.DEFENSE, GearSet.CRITICAL, GearSet.HIT),
        )
        for sets in invalid:
            with self.subTest(sets=sets):
                with self.assertRaises(DomainValidationError):
                    SetPattern(sets)


class InputRecordTests(unittest.TestCase):
    def test_gear_item_is_immutable_normalized_and_serializable(self):
        source_substats = [
            (ItemStatType.SPEED, 12),
            (ItemStatType.CRITICAL_HIT_CHANCE_PERCENT, 8),
        ]
        item = GearItem(
            item_id=" item.1 ",
            dense_id=9,
            slot=GearSlot.WEAPON,
            gear_set=GearSet.SPEED,
            item_level=90,
            enhance=15,
            main_stat=ItemStatType.FLAT_ATTACK,
            main_stat_value=525,
            substats=source_substats,
            equipped_hero_id="hero.1",
            locked=True,
        )
        source_substats.append((ItemStatType.HEALTH_PERCENT, 10))
        self.assertEqual(item.item_id, "item.1")
        self.assertEqual(len(item.substats), 2)
        self.assertEqual(GearItem.from_dict(item.to_dict()), item)
        self.assertIsInstance(hash(item), int)
        with self.assertRaises(FrozenInstanceError):
            item.item_id = "changed"

    def test_gear_item_rejects_invalid_stats_and_identifiers(self):
        valid = dict(
            item_id="item.1",
            slot=GearSlot.WEAPON,
            gear_set=GearSet.SPEED,
            main_stat=ItemStatType.FLAT_ATTACK,
            main_stat_value=500,
        )
        invalid_overrides = (
            {"item_id": " "},
            {"dense_id": -1},
            {"enhance": 16},
            {"main_stat_value": -1},
            {"substats": ((ItemStatType.FLAT_ATTACK, 10),)},
            {"substats": ((ItemStatType.SPEED, 1),) * 2},
            {"substats": tuple((stat, 1) for stat in list(ItemStatType)[:5])},
        )
        for override in invalid_overrides:
            with self.subTest(override=override):
                with self.assertRaises(DomainValidationError):
                    GearItem(**(valid | override))

    def test_hero_profile_definition_and_artifact_round_trip(self):
        base = profile()
        hero = HeroDefinition(hero_id="hero.1", dense_id=7, name="Test Hero", base_profiles=[base])
        artifact = ArtifactDefinition(
            artifact_id="artifact.1",
            dense_id=8,
            name="Test Artifact",
            max_level=30,
            base_attack=15,
            base_health=40,
            max_attack=195,
            max_health=702,
        )
        self.assertEqual(HeroBaseProfile.from_dict(base.to_dict()), base)
        self.assertEqual(HeroDefinition.from_dict(hero.to_dict()), hero)
        self.assertEqual(ArtifactDefinition.from_dict(artifact.to_dict()), artifact)
        self.assertIsInstance(hash(hero), int)
        self.assertIsInstance(hash(artifact), int)

    def test_hero_and_artifact_validation_is_actionable(self):
        with self.assertRaisesRegex(DomainValidationError, "missing"):
            HeroBaseProfile(
                profile_id="profile.1",
                label="Incomplete",
                level=60,
                stars=6,
                final_stats={FinalStat.ATTACK: 1000},
            )
        with self.assertRaisesRegex(DomainValidationError, "unique profile IDs"):
            HeroDefinition(hero_id="hero.1", name="Hero", base_profiles=(profile(), profile()))
        with self.assertRaisesRegex(DomainValidationError, "max_attack"):
            ArtifactDefinition("artifact.1", "Artifact", 30, 100, 100, 99, 200)

    def test_modifiers_are_deeply_immutable_and_round_trip(self):
        custom = {FinalStat.ATTACK: 100}
        value = HeroModifiers(
            artifact_id="artifact.1",
            artifact_level=15,
            artifact_attack_override=101.5,
            artifact_health_override=202,
            artifact_defense_override=3.5,
            custom_bonuses=custom,
            skill_options=["skill.b", "skill.a"],
        )
        custom[FinalStat.ATTACK] = 999
        self.assertEqual(dict(value.custom_bonuses)[FinalStat.ATTACK], 100)
        self.assertEqual(value.skill_options, ("skill.a", "skill.b"))
        self.assertEqual(HeroModifiers.from_dict(value.to_dict()), value)
        self.assertIsInstance(hash(value), int)

    def test_typed_hero_modifier_contributions_preserve_flat_percent_and_dual_attack(self):
        flat = HeroModifierContribution(HeroModifierStatType.FLAT_HEALTH, 920)
        rate = HeroModifierContribution(HeroModifierStatType.HEALTH_PERCENT, 0.14)
        dual = HeroModifierContribution(HeroModifierStatType.DUAL_ATTACK_CHANCE_PERCENT, 0.09)
        self.assertEqual(((FinalStat.HEALTH, 920),), flat.legacy_final_stat_bonus())
        self.assertEqual(((FinalStat.HEALTH, 14.0),), rate.legacy_final_stat_bonus())
        self.assertEqual((), dual.legacy_final_stat_bonus())
        self.assertEqual(rate, HeroModifierContribution.from_dict(rate.to_dict()))

        modifiers = HeroModifiers(
            imprint_level="A",
            imprint_bonuses=rate.legacy_final_stat_bonus(),
            imprint_contribution=rate,
        )
        self.assertEqual(modifiers, HeroModifiers.from_dict(modifiers.to_dict()))
        with self.assertRaisesRegex(DomainValidationError, "must match"):
            HeroModifiers(
                imprint_level="A",
                imprint_bonuses={FinalStat.HEALTH: 13},
                imprint_contribution=rate,
            )

    def test_typed_custom_contributions_are_ordered_projected_and_round_trip(self):
        contributions = (
            HeroModifierContribution(HeroModifierStatType.SPEED, 0),
            HeroModifierContribution(HeroModifierStatType.ATTACK_PERCENT, 0.15),
            HeroModifierContribution(HeroModifierStatType.FLAT_ATTACK, 100),
        )
        projection = custom_bonus_projection(contributions)
        self.assertEqual(
            ((FinalStat.ATTACK, 115.0), (FinalStat.SPEED, 0)),
            projection,
        )
        value = HeroModifiers(
            custom_bonuses=projection,
            custom_contributions=contributions,
        )
        self.assertEqual(
            (
                HeroModifierContribution(HeroModifierStatType.FLAT_ATTACK, 100),
                HeroModifierContribution(HeroModifierStatType.ATTACK_PERCENT, 0.15),
                HeroModifierContribution(HeroModifierStatType.SPEED, 0),
            ),
            value.custom_contributions,
        )
        self.assertEqual(value, HeroModifiers.from_dict(value.to_dict()))
        with self.assertRaisesRegex(DomainValidationError, "duplicate"):
            HeroModifiers(
                custom_contributions=(
                    HeroModifierContribution(HeroModifierStatType.SPEED, 1),
                    HeroModifierContribution(HeroModifierStatType.SPEED, 2),
                )
            )
        with self.assertRaisesRegex(DomainValidationError, "does not support dual"):
            HeroModifiers(
                custom_contributions=(
                    HeroModifierContribution(
                        HeroModifierStatType.DUAL_ATTACK_CHANCE_PERCENT,
                        0.01,
                    ),
                )
            )

    def test_skill_contexts_default_from_global_target_and_validate_all_overrides(self):
        value = request()
        self.assertEqual(tuple(SkillSlot), tuple(item.skill for item in value.skill_contexts))
        self.assertEqual((1500, 1500, 1500), tuple(item.target_defense for item in value.skill_contexts))
        explicit = SkillContext(
            SkillSlot.S1,
            1234,
            source_option_id="skill-option.fribbels.hero.s1.0.proof",
            hit_type=SkillHitType.CRITICAL,
            target_count_override=2,
            penetration_override=0.5,
        )
        self.assertEqual(explicit, SkillContext.from_dict(explicit.to_dict()))
        for override in (
            {"target_count_override": 0},
            {"target_count_override": True},
            {"penetration_override": -0.1},
            {"penetration_override": 1.1},
            {"penetration_override": math.inf},
            {"target_defense": True},
        ):
            with self.subTest(override=override), self.assertRaises(DomainValidationError):
                values = {"target_defense": 1000, **override}
                SkillContext(SkillSlot.S1, **values)

    def test_modifiers_require_complete_artifact_selection(self):
        with self.assertRaisesRegex(DomainValidationError, "supplied together"):
            HeroModifiers(artifact_id="artifact.1")
        for override in (
            {"artifact_level": 1},
            {"artifact_limit_breaks": 0},
            {"artifact_attack_override": 1},
        ):
            with self.subTest(override=override), self.assertRaisesRegex(
                DomainValidationError,
                "require artifact_id",
            ):
                HeroModifiers(**override)
        for override in (
            {"artifact_limit_breaks": 6},
            {"artifact_limit_breaks": True},
            {"artifact_attack_override": -1},
            {"artifact_health_override": math.inf},
            {"artifact_defense_override": True},
        ):
            with self.subTest(override=override), self.assertRaises(DomainValidationError):
                HeroModifiers(
                    artifact_id="artifact.1",
                    artifact_level=15,
                    **override,
                )
        with self.assertRaisesRegex(DomainValidationError, "duplicate"):
            HeroModifiers(skill_options=("skill.a", "skill.a"))


class OptimizationRequestTests(unittest.TestCase):
    def test_request_preserves_locked_options_and_round_trips(self):
        value = request()
        payload = value.to_dict()
        self.assertEqual(payload["nearSetTolerance"], 0)
        self.assertEqual(payload["maximumReplacementDistance"], 0)
        self.assertEqual(payload["resultCap"], 5_000_000)
        self.assertEqual(payload["executionPreference"], "execution.gpu")
        self.assertIsNone(payload["itemProjectionMode"])
        self.assertEqual(
            payload["gearFilters"],
            {
                "rightSideMainStats": {},
                "minimumEnhance": 0,
                "excludedItemIds": [],
            },
        )
        self.assertEqual(payload["statRanges"][FinalStat.ATTACK.value]["minimum"], 0)
        self.assertEqual(OptimizationRequest.from_dict(payload), value)
        self.assertIsInstance(hash(value), int)

    def test_request_defaults_match_product_contract(self):
        value = OptimizationRequest(
            request_id="request.1",
            hero_id="hero.1",
            base_profile_id="profile.1",
            modifiers=HeroModifiers(),
            set_pattern=SetPattern((GearSet.SPEED, GearSet.HEALTH)),
        )
        self.assertFalse(value.include_equipped)
        self.assertEqual(value.near_set_tolerance, 0)
        self.assertEqual(value.maximum_replacement_distance, 0)
        self.assertEqual(value.result_cap, MAX_RESULT_CAP)
        self.assertIs(value.execution_preference, ExecutionPreference.AUTO)
        self.assertIsNone(value.item_projection_mode)
        self.assertEqual(value.gear_filters, GearSearchFilters())

        explicit = request()
        explicit = OptimizationRequest.from_dict(
            explicit.to_dict() | {"itemProjectionMode": "projection.reforged"}
        )
        self.assertIs(explicit.item_projection_mode, ItemProjectionMode.REFORGED)
        self.assertEqual(explicit, OptimizationRequest.from_dict(explicit.to_dict()))

    def test_request_rejects_invalid_priority_tolerance_cap_and_numbers(self):
        base = dict(
            request_id="request.1",
            hero_id="hero.1",
            base_profile_id="profile.1",
            modifiers=HeroModifiers(),
            set_pattern=SetPattern((GearSet.SPEED, GearSet.HEALTH)),
        )
        invalid_overrides = (
            {"stat_priorities": {FinalStat.SPEED: -2}},
            {"stat_priorities": {FinalStat.SPEED: 4}},
            {"near_set_tolerance": -0.01},
            {"near_set_tolerance": 1.01},
            {"maximum_replacement_distance": -1},
            {"maximum_replacement_distance": 3},
            {"maximum_replacement_distance": True},
            {"target_defense": math.inf},
            {"result_cap": 0},
            {"result_cap": MAX_RESULT_CAP + 1},
            {"include_equipped": 1},
            {"execution_preference": "gpu"},
            {"item_projection_mode": "reforged"},
            {"gear_filters": {}},
        )
        for override in invalid_overrides:
            with self.subTest(override=override):
                with self.assertRaises(DomainValidationError):
                    OptimizationRequest(**(base | override))

    def test_deserialization_rejects_unknown_and_missing_fields(self):
        payload = request().to_dict()
        payload["surprise"] = True
        with self.assertRaisesRegex(DomainValidationError, "unknown field"):
            OptimizationRequest.from_dict(payload)
        with self.assertRaisesRegex(DomainValidationError, "missing required"):
            OptimizationRequest.from_dict({})


class GearSearchFiltersTests(unittest.TestCase):
    def test_filters_are_canonical_immutable_and_round_trip(self):
        filters = GearSearchFilters(
            right_side_main_stats=(
                (
                    GearSlot.BOOTS,
                    (
                        ItemStatType.SPEED,
                        ItemStatType.ATTACK_PERCENT,
                        ItemStatType.FLAT_ATTACK,
                    ),
                ),
                (
                    GearSlot.NECKLACE,
                    (
                        ItemStatType.CRITICAL_HIT_DAMAGE_PERCENT,
                        ItemStatType.CRITICAL_HIT_CHANCE_PERCENT,
                    ),
                ),
            ),
            minimum_enhance=12,
            excluded_item_ids=(" item.z ", "item.a"),
        )
        self.assertEqual(
            tuple(slot for slot, _ in filters.right_side_main_stats),
            (GearSlot.NECKLACE, GearSlot.BOOTS),
        )
        self.assertEqual(
            filters.allowed_main_stats_for(GearSlot.BOOTS),
            (
                ItemStatType.FLAT_ATTACK,
                ItemStatType.ATTACK_PERCENT,
                ItemStatType.SPEED,
            ),
        )
        self.assertIsNone(filters.allowed_main_stats_for(GearSlot.RING))
        self.assertEqual(filters.excluded_item_ids, ("item.a", "item.z"))
        self.assertEqual(filters, GearSearchFilters.from_dict(filters.to_dict()))
        self.assertIsInstance(hash(filters), int)
        with self.assertRaises(FrozenInstanceError):
            filters.minimum_enhance = 0  # type: ignore[misc]

    def test_filters_reject_ambiguous_or_illegal_values(self):
        invalid = (
            {"right_side_main_stats": ((GearSlot.WEAPON, (ItemStatType.FLAT_ATTACK,)),)},
            {"right_side_main_stats": ((GearSlot.RING, ()),)},
            {
                "right_side_main_stats": (
                    (GearSlot.RING, (ItemStatType.EFFECTIVENESS_PERCENT,)),
                    (GearSlot.RING, (ItemStatType.HEALTH_PERCENT,)),
                )
            },
            {
                "right_side_main_stats": (
                    (
                        GearSlot.NECKLACE,
                        (ItemStatType.EFFECTIVENESS_PERCENT,),
                    ),
                )
            },
            {
                "right_side_main_stats": (
                    (
                        GearSlot.BOOTS,
                        (ItemStatType.SPEED, ItemStatType.SPEED),
                    ),
                )
            },
            {"minimum_enhance": -1},
            {"minimum_enhance": 16},
            {"minimum_enhance": True},
            {"excluded_item_ids": ("item.a", " item.a ")},
        )
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs), self.assertRaises(DomainValidationError):
                GearSearchFilters(**kwargs)


class ResultRecordTests(unittest.TestCase):
    def test_metrics_require_all_final_stats_and_round_trip(self):
        value = build_metrics()
        self.assertEqual(BuildMetrics.from_dict(value.to_dict()), value)
        with self.assertRaisesRegex(DomainValidationError, "missing"):
            BuildMetrics(final_stats={FinalStat.ATTACK: 1000})
        with self.assertRaises(DomainValidationError):
            BuildMetrics(final_stats=final_stats(), derived_metrics={"metric.ehp": math.nan})

class SearchSummaryTests(unittest.TestCase):
    def test_completed_and_aborted_summaries_round_trip(self):
        complete = SearchSummary(
            request_id="request.1",
            evaluated_permutations=1_000_000,
            exact_count=10,
            one_away_count=20,
            two_away_count=30,
            duration_seconds=1.25,
            execution_preference=ExecutionPreference.GPU,
        )
        overflow = SearchSummary(
            request_id="request.2",
            evaluated_permutations=9_000_000,
            exact_count=0,
            one_away_count=0,
            two_away_count=0,
            duration_seconds=4,
            execution_preference=ExecutionPreference.CPU,
            overflowed=True,
        )
        self.assertEqual(complete.result_count, 60)
        self.assertEqual(SearchSummary.from_dict(complete.to_dict()), complete)
        self.assertEqual(SearchSummary.from_dict(overflow.to_dict()), overflow)

    def test_summary_rejects_partial_aborts_overflow_and_mismatched_totals(self):
        with self.assertRaisesRegex(DomainValidationError, "partial"):
            SearchSummary("request.1", 100, 1, 0, 0, 1, ExecutionPreference.CPU, overflowed=True)
        with self.assertRaisesRegex(DomainValidationError, "must not exceed"):
            SearchSummary(
                "request.1",
                100,
                MAX_RESULT_CAP,
                1,
                0,
                1,
                ExecutionPreference.CPU,
            )
        payload = SearchSummary(
            "request.1", 100, 1, 2, 3, 1, ExecutionPreference.CPU
        ).to_dict()
        payload["resultCount"] = 99
        with self.assertRaisesRegex(DomainValidationError, "does not match"):
            SearchSummary.from_dict(payload)


class SerializationContractTests(unittest.TestCase):
    def test_every_record_round_trips_through_json(self):
        base = profile()
        hero = HeroDefinition("hero.1", "Hero", (base,), dense_id=1)
        artifact = ArtifactDefinition("artifact.1", "Artifact", 30, 10, 20, 100, 200, dense_id=2)
        summary = SearchSummary("request.1", 100, 1, 2, 3, 0.5, ExecutionPreference.CPU)
        records = (
            StatRange(0, 10),
            SetPattern((GearSet.SPEED, GearSet.HEALTH)),
            GearItem(
                "item.1",
                GearSlot.WEAPON,
                GearSet.SPEED,
                ItemStatType.FLAT_ATTACK,
                500,
            ),
            base,
            hero,
            artifact,
            modifiers(),
            request(),
            build_metrics(),
            summary,
        )
        for record in records:
            with self.subTest(record=type(record).__name__):
                payload = json.loads(json.dumps(record.to_dict()))
                self.assertEqual(type(record).from_dict(payload), record)


if __name__ == "__main__":
    unittest.main()
