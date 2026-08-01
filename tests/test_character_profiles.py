from __future__ import annotations

import math
import socket
import unittest
from dataclasses import replace
from unittest.mock import patch

from src.optimizer.data import (
    DEFAULT_CHARACTER_PROFILE_LABEL,
    CharacterProfileSelectionError,
    CharacterProfileSelector,
    OptimizerConfiguration,
    OptimizerProfileDocument,
    SourceMetadata,
    load_bundled_character_catalog,
    load_bundled_character_profile_selector,
    load_bundled_character_repository,
    load_optimizer_profile_json,
    thaw_json,
)
from src.optimizer.domain import (
    FinalStat,
    GearSet,
    HeroModifiers,
    OptimizationRequest,
    SetPattern,
)


def _source_profile_stats(source_status: dict[str, object]) -> dict[FinalStat, int]:
    def percentage(field: str) -> int:
        return math.floor(float(source_status[field]) * 100 + 0.5)

    return {
        FinalStat.ATTACK: int(source_status["atk"]),
        FinalStat.HEALTH: int(source_status["hp"]),
        FinalStat.DEFENSE: int(source_status["def"]),
        FinalStat.SPEED: int(source_status["spd"]),
        FinalStat.CRITICAL_HIT_CHANCE: percentage("chc"),
        FinalStat.CRITICAL_HIT_DAMAGE: percentage("chd"),
        FinalStat.EFFECTIVENESS: percentage("eff"),
        FinalStat.EFFECT_RESISTANCE: percentage("efr"),
    }


class CharacterProfileSelectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_bundled_character_catalog()
        cls.repository = load_bundled_character_repository()
        cls.selector = CharacterProfileSelector(cls.repository)

    def test_bundled_selector_exposes_every_profile_without_network(self) -> None:
        with (
            patch.object(socket, "create_connection", side_effect=AssertionError("network attempted")),
            patch.object(socket.socket, "connect", side_effect=AssertionError("network attempted")),
        ):
            selector = load_bundled_character_profile_selector()

        self.assertEqual(386, len(selector))
        self.assertEqual(772, selector.profile_count)
        self.assertEqual(
            772,
            sum(len(selector.profiles_for(hero.hero_id)) for hero in selector.heroes),
        )

    def test_every_hero_has_one_explicit_60_6_fully_awakened_default(self) -> None:
        for hero in self.selector.heroes:
            with self.subTest(hero=hero.hero_id):
                selection = self.selector.create_default_selection(hero.hero_id)
                self.assertIs(hero, selection.hero)
                self.assertEqual(60, selection.profile.level)
                self.assertEqual(6, selection.profile.stars)
                self.assertEqual(DEFAULT_CHARACTER_PROFILE_LABEL, selection.profile.label)
                self.assertTrue(selection.is_default_for_new_selection)

    def test_profile_options_and_explicit_selection_are_stable_and_immutable(self) -> None:
        profiles = self.selector.profiles_for("hero.fribbels.ras")
        self.assertEqual(
            ("profile.fribbels.ras.50.5", "profile.fribbels.ras.60.6"),
            tuple(profile.profile_id for profile in profiles),
        )
        self.assertEqual(profiles, self.selector.profiles_for("HERO.FRIBBELS.RAS"))

        selection = self.selector.select("hero.fribbels.ras", profiles[0].profile_id)
        repeated = self.selector.select("hero.fribbels.ras", profiles[0].profile_id)
        self.assertEqual(selection, repeated)
        self.assertIs(profiles[0], selection.profile)
        self.assertFalse(selection.is_default_for_new_selection)
        with self.assertRaises((AttributeError, TypeError)):
            selection.profile = profiles[1]
        with self.assertRaises(AttributeError):
            self.selector._heroes = ()

    def test_explicit_resolution_never_falls_back(self) -> None:
        cases = (
            (
                lambda: self.selector.profiles_for("hero.fribbels.missing"),
                "hero-not-found",
            ),
            (
                lambda: self.selector.select("hero.fribbels.ras", "profile.fribbels.missing"),
                "profile-not-found",
            ),
            (
                lambda: self.selector.select(
                    "hero.fribbels.ras", "profile.fribbels.aube.60.6"
                ),
                "profile-hero-mismatch",
            ),
            (
                lambda: self.selector.select("hero.fribbels.ras", None),
                "profile-not-found",
            ),
        )
        for operation, expected_code in cases:
            with self.subTest(code=expected_code), self.assertRaises(
                CharacterProfileSelectionError
            ) as caught:
                operation()
            self.assertEqual(expected_code, caught.exception.code)
            self.assertTrue(caught.exception.path)

    def test_absent_and_ambiguous_defaults_fail_at_selector_construction(self) -> None:
        ras = self.repository.find_exact("Ras")
        self.assertIsNotNone(ras)
        fifty = next(profile for profile in ras.base_profiles if profile.level == 50)
        sixty = next(profile for profile in ras.base_profiles if profile.level == 60)

        without_default = replace(
            ras,
            definition=replace(ras.definition, base_profiles=(fifty,)),
        )
        with self.assertRaises(CharacterProfileSelectionError) as missing:
            CharacterProfileSelector((without_default,))
        self.assertEqual("default-profile-missing", missing.exception.code)

        duplicate_default = replace(
            sixty,
            profile_id="profile.fixture.ras.second-default",
            dense_id=None,
        )
        ambiguous = replace(
            ras,
            definition=replace(
                ras.definition,
                base_profiles=(*ras.base_profiles, duplicate_default),
            ),
        )
        with self.assertRaises(CharacterProfileSelectionError) as multiple:
            CharacterProfileSelector((ambiguous,))
        self.assertEqual("default-profile-ambiguous", multiple.exception.code)
        self.assertIn(sixty.profile_id, multiple.exception.message)
        self.assertIn(duplicate_default.profile_id, multiple.exception.message)

    def test_selected_stats_match_raw_source_before_modifiers(self) -> None:
        relationships = (
            (50, "lv50FiveStarFullyAwakened"),
            (60, "lv60SixStarFullyAwakened"),
        )
        for hero_name in ("Ras", "Aube"):
            hero = self.repository.find_exact(hero_name)
            self.assertIsNotNone(hero)
            calculated = thaw_json(hero.raw_source)["calculatedStatus"]
            for level, source_key in relationships:
                with self.subTest(hero=hero_name, level=level):
                    profile = next(item for item in hero.base_profiles if item.level == level)
                    selection = self.selector.select(hero.hero_id, profile.profile_id)
                    self.assertIs(profile, selection.profile)
                    self.assertEqual(
                        _source_profile_stats(calculated[source_key]),
                        dict(selection.profile.final_stats),
                    )

    def test_non_default_profile_id_survives_saved_configuration_round_trip(self) -> None:
        selection = self.selector.select(
            "hero.fribbels.ras",
            "profile.fribbels.ras.50.5",
        )
        request = OptimizationRequest(
            request_id="request.profile-selection-proof",
            hero_id=selection.hero_id,
            base_profile_id=selection.profile_id,
            modifiers=HeroModifiers(),
            set_pattern=SetPattern((GearSet.SPEED, GearSet.HEALTH)),
        )
        configuration = OptimizerConfiguration.from_request(request)
        document = OptimizerProfileDocument(
            profile_id="optimizer-profile.profile-selection-proof",
            name="Profile selection proof",
            saved_at="2026-07-20T12:00:00Z",
            source=SourceMetadata(source_name="Meowtoko E7 Tool"),
            configuration=configuration,
            character_catalog_id=self.catalog.catalog_id,
        )

        reloaded = load_optimizer_profile_json(
            document.to_json(),
            character_catalog=self.catalog,
        )
        restored_request = reloaded.create_request("request.restored-profile-selection")
        restored = self.selector.select(
            restored_request.hero_id,
            restored_request.base_profile_id,
        )
        self.assertEqual(selection, restored)
        self.assertEqual(selection.profile_id, reloaded.configuration.to_dict()["baseProfileId"])
        self.assertFalse(restored.is_default_for_new_selection)


if __name__ == "__main__":
    unittest.main()
