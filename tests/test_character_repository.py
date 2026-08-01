from __future__ import annotations

import copy
import socket
import unittest
from dataclasses import replace
from unittest.mock import patch

from src.optimizer.data import (
    HERO_PLACEHOLDER_IMAGE_REFERENCE,
    MAX_CHARACTER_SEARCH_LIMIT,
    CharacterAliasCollisionError,
    CharacterNotFoundError,
    CharacterRepository,
    CharacterRepositoryError,
    FrozenJsonArray,
    FrozenJsonObject,
    SchemaValidationError,
    load_bundled_character_catalog,
    load_bundled_character_repository,
    load_bundled_character_source_snapshot,
    load_character_catalog,
    load_character_source_snapshot,
    normalize_character_search_text,
    thaw_json,
)


class CharacterRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_bundled_character_catalog()
        cls.source = load_bundled_character_source_snapshot()
        cls.repository = CharacterRepository(cls.catalog, cls.source)

    def _source_with(self, mutate) -> object:
        payload = self.source.to_dict()
        mutate(payload)
        return load_character_source_snapshot(payload)

    def _catalog_with(self, mutate) -> object:
        payload = self.catalog.to_dict()
        mutate(payload)
        return load_character_catalog(payload)

    def test_bundled_repository_loads_every_hero_and_profile_without_network(self) -> None:
        with (
            patch.object(socket, "create_connection", side_effect=AssertionError("network attempted")),
            patch.object(socket.socket, "connect", side_effect=AssertionError("network attempted")),
        ):
            repository = load_bundled_character_repository()

        self.assertEqual(386, len(repository))
        self.assertEqual(386, len(repository.heroes))
        self.assertEqual(772, sum(len(hero.base_profiles) for hero in repository.heroes))
        self.assertEqual(
            {hero.hero_id for hero in self.catalog.heroes},
            {hero.hero_id for hero in repository.heroes},
        )

    def test_public_exports_and_exact_lookups_use_stable_evidence(self) -> None:
        expected = self.repository.get("hero.fribbels.aube")
        self.assertIs(expected, self.repository.get("HERO.FRIBBELS.AUBE"))
        for query in ("Aube", "aUbE", "c5190", "C5190", "hero.fribbels.aube"):
            self.assertIs(expected, self.repository.find_exact(query))
        self.assertIsNone(self.repository.find_exact(""))
        self.assertIsNone(self.repository.find_exact("does-not-exist"))
        with self.assertRaises(CharacterNotFoundError):
            self.repository.get("does-not-exist")

    def test_search_ranking_limits_and_blank_policy_are_deterministic(self) -> None:
        expected_ras = ("Ras", "Adventurer Ras", "Genesis Ras")
        self.assertEqual(expected_ras, tuple(hero.name for hero in self.repository.search("ras")))
        self.assertEqual(expected_ras, tuple(hero.name for hero in self.repository.search("RAS")))
        self.assertEqual(("Ras",), tuple(hero.name for hero in self.repository.search("ras", limit=1)))
        self.assertEqual(
            ("ae-GISELLE",),
            tuple(hero.name for hero in self.repository.search("  ae__giselle  ")),
        )
        self.assertEqual((), self.repository.search("not a real epic seven hero"))

        blank = self.repository.search("", limit=7)
        self.assertEqual(7, len(blank))
        self.assertEqual(blank, self.repository.search(" \t---___ ", limit=7))
        self.assertEqual(blank, self.repository.search("", limit=7))
        self.assertEqual(
            tuple(sorted(blank, key=lambda hero: (normalize_character_search_text(hero.name), hero.hero_id))),
            blank,
        )
        for invalid_limit in (0, MAX_CHARACTER_SEARCH_LIMIT + 1, True, 1.5):
            with self.subTest(limit=invalid_limit), self.assertRaises(ValueError):
                self.repository.search("ras", limit=invalid_limit)
        with self.assertRaises(ValueError):
            self.repository.search(None)

    def test_unicode_casefold_nfkc_and_punctuation_normalization(self) -> None:
        source_payload = self.source.to_dict()
        record = source_payload["records"]["heroes"].pop("Aube")
        record["name"] = "Élan—Prime"
        record["_id"] = "elan-prime-source"
        record["code"] = "CUNICODE"
        source_payload["records"]["heroes"]["Élan—Prime"] = record
        source = load_character_source_snapshot(source_payload)

        catalog_payload = self.catalog.to_dict()
        canonical = next(hero for hero in catalog_payload["heroes"] if hero["name"] == "Aube")
        canonical["name"] = "Élan—Prime"
        canonical["heroId"] = "hero.fribbels.elan-prime-source"
        for profile in canonical["baseProfiles"]:
            profile["profileId"] = profile["profileId"].replace(
                ".aube.", ".elan-prime-source."
            )
        catalog = load_character_catalog(catalog_payload)

        repository = CharacterRepository(catalog, source)
        expected = repository.get("hero.fribbels.elan-prime-source")
        self.assertEqual("e", normalize_character_search_text("Ｅ"))
        self.assertEqual("élan prime", normalize_character_search_text("E\u0301lan—Prime"))
        self.assertIs(expected, repository.find_exact("e\u0301LAN prime"))
        self.assertEqual((expected,), repository.search("élan___pr"))

    def test_provenance_count_mapping_and_profile_drift_are_actionable(self) -> None:
        mismatched_source = replace(
            self.source,
            source=replace(self.source.source, source_revision="0" * 40),
        )
        with self.assertRaises(CharacterRepositoryError) as provenance:
            CharacterRepository(self.catalog, mismatched_source)
        self.assertEqual("source-provenance-drift", provenance.exception.code)

        def remove_aube(payload) -> None:
            payload["records"]["heroes"].pop("Aube")
            next(item for item in payload["inputs"] if item["recordKind"] == "hero")[
                "recordCount"
            ] -= 1

        with self.assertRaises(CharacterRepositoryError) as missing:
            CharacterRepository(self.catalog, self._source_with(remove_aube))
        self.assertEqual("hero-count-drift", missing.exception.code)

        for field, replacement, expected_code in (
            ("name", "Not Aube", "name-drift"),
            ("_id", "not-aube", "missing-canonical-hero"),
        ):
            with self.subTest(field=field):
                source = self._source_with(
                    lambda payload, field=field, replacement=replacement: payload["records"][
                        "heroes"
                    ]["Aube"].__setitem__(field, replacement)
                )
                with self.assertRaises(CharacterRepositoryError) as drift:
                    CharacterRepository(self.catalog, source)
                self.assertEqual(expected_code, drift.exception.code)
                self.assertIn("Aube", drift.exception.path)

        def drift_profile(payload) -> None:
            hero = next(item for item in payload["heroes"] if item["name"] == "Aube")
            hero["baseProfiles"][0]["profileId"] = "profile.drifted"

        with self.assertRaises(CharacterRepositoryError) as profile:
            CharacterRepository(self._catalog_with(drift_profile), self.source)
        self.assertEqual("profile-drift", profile.exception.code)

    def test_normalized_alias_collision_reports_all_stable_ids(self) -> None:
        source = self._source_with(
            lambda payload: payload["records"]["heroes"]["Aube"].__setitem__("code", "RAS")
        )
        with self.assertRaises(CharacterAliasCollisionError) as caught:
            CharacterRepository(self.catalog, source)
        self.assertEqual("ras", caught.exception.normalized_alias)
        self.assertEqual(
            ("hero.fribbels.aube", "hero.fribbels.ras"),
            caught.exception.hero_ids,
        )

    def test_malformed_rich_fields_fail_with_paths(self) -> None:
        mutations = (
            ("skills", [], ".skills"),
            ("self_devotion", {"type": "acc", "grades": []}, ".self_devotion"),
            ("ex_equip", [{"stat": {"type": "speed", "value": "five"}}], ".ex_equip"),
        )
        for field, replacement, path_fragment in mutations:
            with self.subTest(field=field):
                source = self._source_with(
                    lambda payload, field=field, replacement=replacement: payload["records"][
                        "heroes"
                    ]["Aube"].__setitem__(field, copy.deepcopy(replacement))
                )
                with self.assertRaises(CharacterRepositoryError) as caught:
                    CharacterRepository(self.catalog, source)
                self.assertEqual("invalid-rich-field", caught.exception.code)
                self.assertIn(path_fragment, caught.exception.path)

    def test_unsupported_source_and_catalog_versions_fail_before_repository_use(self) -> None:
        for payload in (self.source.to_dict(), self.catalog.to_dict()):
            with self.subTest(schema=payload["schemaId"]):
                payload["schemaVersion"] = 999
                loader = (
                    load_character_source_snapshot
                    if "source-snapshot" in payload["schemaId"]
                    else load_character_catalog
                )
                with self.assertRaisesRegex(SchemaValidationError, "newer than supported"):
                    loader(payload)

    def test_portrait_failures_degrade_to_built_in_placeholder(self) -> None:
        def mutate(payload) -> None:
            assets = payload["records"]["heroes"]["Aube"]["assets"]
            assets.pop("icon")
            assets["image"] = "not a usable URL"

        source = self._source_with(mutate)
        repository = CharacterRepository(
            self.catalog,
            source,
            usable_asset_reference=lambda _reference: False,
        )
        hero = repository.get("hero.fribbels.aube")
        self.assertTrue(hero.portraits.uses_placeholder)
        self.assertEqual(HERO_PLACEHOLDER_IMAGE_REFERENCE, hero.portraits.icon)
        self.assertEqual(HERO_PLACEHOLDER_IMAGE_REFERENCE, hero.portraits.image)
        self.assertEqual(HERO_PLACEHOLDER_IMAGE_REFERENCE, hero.portraits.thumbnail)
        self.assertIsNone(hero.portraits.source_icon)
        self.assertEqual("not a usable URL", hero.portraits.source_image)
        self.assertTrue(hero.portraits.source_thumbnail.startswith("https://"))
        self.assertIs(hero, repository.find_exact("aube"))

    def test_representative_categories_preserve_exact_rich_source_data(self) -> None:
        representatives = {
            "Ras": ("ras", "c1001", "fire", "knight", 3, "scales"),
            "Seaside Bellona": (
                "seaside-bellona",
                "c5071",
                "ice",
                "ranger",
                5,
                "twins",
            ),
            "ae-GISELLE": ("ae-giselle", "c1138", "wind", "mage", 5, "scorpion"),
            "Adventurer Ras": (
                "adventurer-ras",
                "c5001",
                "fire",
                "knight",
                3,
                "scales",
            ),
            "Aube": ("aube", "c5190", "ice", "ranger", 5, "fish"),
            "Tidal Rift Elvira": (
                "tidal-rift-elvira",
                "c2148",
                "dark",
                "mage",
                5,
                "fish",
            ),
        }
        for name, expected in representatives.items():
            with self.subTest(name=name):
                hero = self.repository.find_exact(name)
                self.assertIsNotNone(hero)
                source = thaw_json(self.source.heroes[name])
                self.assertEqual(
                    expected,
                    (
                        hero.source_id,
                        hero.source_code,
                        hero.element,
                        hero.role,
                        hero.rarity,
                        hero.zodiac,
                    ),
                )
                self.assertEqual(source["skills"], thaw_json(hero.skills))
                self.assertEqual(source["self_devotion"], thaw_json(hero.self_devotion))
                self.assertEqual(source["ex_equip"], thaw_json(hero.exclusive_equipment))
                self.assertEqual(source["assets"], thaw_json(hero.portraits.source_assets))
                self.assertEqual(source, thaw_json(hero.raw_source))

    def test_records_rich_fields_unknown_fields_and_repository_are_immutable(self) -> None:
        arunka = self.repository.find_exact("Arunka")
        self.assertIsNotNone(arunka)
        self.assertIsInstance(arunka.skills, FrozenJsonObject)
        self.assertIsInstance(arunka.exclusive_equipment, FrozenJsonArray)
        self.assertIsInstance(arunka.raw_source, FrozenJsonObject)
        self.assertIn("S2", arunka.unknown_fields)
        self.assertEqual(
            thaw_json(arunka.raw_source)["S2"],
            thaw_json(arunka.unknown_fields)["S2"],
        )
        with self.assertRaises(TypeError):
            arunka.skills["S1"] = arunka.skills["S1"]
        with self.assertRaises(TypeError):
            arunka.portraits.source_assets["icon"] = "changed"
        with self.assertRaises((AttributeError, TypeError)):
            arunka.name = "Changed"
        with self.assertRaises(AttributeError):
            self.repository._heroes = ()


if __name__ == "__main__":
    unittest.main()
