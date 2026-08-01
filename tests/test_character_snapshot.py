from __future__ import annotations

import copy
import hashlib
import json
import socket
import unittest
from pathlib import Path
from unittest.mock import patch

from src.optimizer.data import (
    BUNDLED_ARTIFACT_SOURCE_PATH,
    BUNDLED_CATALOG_FILENAME,
    BUNDLED_HERO_SOURCE_PATH,
    BUNDLED_MANIFEST_FILENAME,
    BUNDLED_SOURCE_FILENAME,
    BUNDLED_VALIDATION_FILENAME,
    CHARACTER_SNAPSHOT_GENERATOR_ID,
    CHARACTER_SNAPSHOT_GENERATOR_VERSION,
    FRIBBELS_ARTIFACT_SHA256,
    FRIBBELS_ARTIFACT_SOURCE_PATH,
    FRIBBELS_CHARACTER_REPOSITORY_URL,
    FRIBBELS_CHARACTER_SOURCE_REVISION,
    FRIBBELS_HERO_SHA256,
    FRIBBELS_HERO_SOURCE_PATH,
    FrozenJsonObject,
    SchemaValidationError,
    build_character_snapshot,
    bundled_character_data_path,
    create_character_snapshot_manifest,
    load_bundled_character_catalog,
    load_bundled_character_normalization_report,
    load_bundled_character_snapshot_manifest,
    load_bundled_character_source_snapshot,
    load_character_normalization_report,
    thaw_json,
)
from src.optimizer.domain import FinalStat


GENERATED_AT = "2026-07-20T00:00:00Z"


class CharacterSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.hero_bytes = bundled_character_data_path(BUNDLED_HERO_SOURCE_PATH).read_bytes()
        cls.artifact_bytes = bundled_character_data_path(BUNDLED_ARTIFACT_SOURCE_PATH).read_bytes()
        cls.catalog = load_bundled_character_catalog()
        cls.source = load_bundled_character_source_snapshot()
        cls.report = load_bundled_character_normalization_report()
        cls.manifest = load_bundled_character_snapshot_manifest()

    def test_pinned_provenance_is_complete_and_non_floating(self) -> None:
        self.assertEqual(40, len(FRIBBELS_CHARACTER_SOURCE_REVISION))
        self.assertEqual(FRIBBELS_CHARACTER_SOURCE_REVISION, self.catalog.source.source_revision)
        self.assertEqual("patch-20260716", self.catalog.source.source_version)
        self.assertEqual(GENERATED_AT, self.catalog.generated_at)
        provenance = thaw_json(self.catalog.source.unknown_fields)
        self.assertEqual(FRIBBELS_CHARACTER_REPOSITORY_URL, provenance["repositoryUrl"])
        self.assertTrue(provenance["revisionUrl"].endswith(FRIBBELS_CHARACTER_SOURCE_REVISION))
        self.assertEqual(GENERATED_AT, provenance["generatedAt"])
        self.assertEqual(GENERATED_AT, provenance["fetchedAt"])
        self.assertEqual(CHARACTER_SNAPSHOT_GENERATOR_ID, provenance["generator"]["id"])
        self.assertEqual(CHARACTER_SNAPSHOT_GENERATOR_VERSION, provenance["generator"]["version"])
        self.assertEqual("MIT", provenance["attribution"]["licenseDeclared"])
        inputs = {item["recordKind"]: item for item in provenance["inputs"]}
        self.assertEqual(FRIBBELS_HERO_SOURCE_PATH, inputs["hero"]["sourcePath"])
        self.assertEqual(FRIBBELS_ARTIFACT_SOURCE_PATH, inputs["artifact"]["sourcePath"])
        self.assertEqual(FRIBBELS_HERO_SHA256, inputs["hero"]["sha256"])
        self.assertEqual(FRIBBELS_ARTIFACT_SHA256, inputs["artifact"]["sha256"])

    def test_every_source_record_is_accounted_for_exactly_once(self) -> None:
        self.assertEqual(386, len(self.source.heroes))
        self.assertEqual(283, len(self.source.artifacts))
        self.assertEqual(669, len(self.report.outcomes))
        identities = {(item.record_kind, item.source_key) for item in self.report.outcomes}
        self.assertEqual(669, len(identities))
        self.assertEqual(
            {
                "sourceRecords": 669,
                "sourceHeroRecords": 386,
                "sourceArtifactRecords": 283,
                "normalizedRecords": 669,
                "normalizedHeroRecords": 386,
                "normalizedArtifactRecords": 283,
                "rejectedRecords": 0,
                "rejectedHeroRecords": 0,
                "rejectedArtifactRecords": 0,
                "canonicalHeroes": 386,
                "canonicalProfiles": 772,
                "canonicalArtifacts": 283,
                "warningCount": 404,
                "warningRecords": 404,
            },
            dict(self.report.summary),
        )
        hero_keys = set(thaw_json(self.source.heroes))
        artifact_keys = set(thaw_json(self.source.artifacts))
        self.assertEqual(hero_keys, {item.source_key for item in self.report.outcomes if item.record_kind == "hero"})
        self.assertEqual(artifact_keys, {item.source_key for item in self.report.outcomes if item.record_kind == "artifact"})

    def test_duplicate_or_missing_accounting_is_rejected(self) -> None:
        payload = self.report.to_dict()
        missing = copy.deepcopy(payload)
        missing["outcomes"].pop()
        with self.assertRaisesRegex(SchemaValidationError, "account for every input record"):
            load_character_normalization_report(missing)

        duplicate = copy.deepcopy(payload)
        duplicate["outcomes"].append(copy.deepcopy(duplicate["outcomes"][0]))
        with self.assertRaisesRegex(SchemaValidationError, "source records"):
            load_character_normalization_report(duplicate)

    def test_canonical_catalog_loads_and_round_trips_exact_bytes(self) -> None:
        catalog_text = bundled_character_data_path(BUNDLED_CATALOG_FILENAME).read_text(encoding="utf-8")
        self.assertEqual(catalog_text, self.catalog.to_json())
        self.assertEqual(386, len(self.catalog.heroes))
        self.assertEqual(283, len(self.catalog.artifacts))
        self.assertEqual(sorted(hero.hero_id for hero in self.catalog.heroes), [hero.hero_id for hero in self.catalog.heroes])
        self.assertEqual(sorted(artifact.artifact_id for artifact in self.catalog.artifacts), [artifact.artifact_id for artifact in self.catalog.artifacts])

    def test_stable_ids_and_exact_hero_stats_match_source_evidence(self) -> None:
        abigail = next(hero for hero in self.catalog.heroes if hero.name == "Abigail")
        self.assertEqual("hero.fribbels.abigail", abigail.hero_id)
        self.assertEqual(
            ["profile.fribbels.abigail.50.5", "profile.fribbels.abigail.60.6"],
            [profile.profile_id for profile in abigail.base_profiles],
        )
        profile = next(profile for profile in abigail.base_profiles if profile.level == 60)
        self.assertEqual(
            {
                FinalStat.ATTACK: 984,
                FinalStat.HEALTH: 6266,
                FinalStat.DEFENSE: 637,
                FinalStat.SPEED: 117,
                FinalStat.CRITICAL_HIT_CHANCE: 15,
                FinalStat.CRITICAL_HIT_DAMAGE: 150,
                FinalStat.EFFECTIVENESS: 0,
                FinalStat.EFFECT_RESISTANCE: 0,
            },
            dict(profile.final_stats),
        )
        ras = next(hero for hero in self.catalog.heroes if hero.name == "Adventurer Ras")
        ras_profile = next(profile for profile in ras.base_profiles if profile.level == 60)
        self.assertEqual(25, dict(ras_profile.final_stats)[FinalStat.EFFECTIVENESS])
        self.assertEqual(12, dict(ras_profile.final_stats)[FinalStat.EFFECT_RESISTANCE])
        for hero in self.catalog.heroes:
            self.assertEqual(2, len(hero.base_profiles))
            for base_profile in hero.base_profiles:
                self.assertEqual(set(FinalStat), set(dict(base_profile.final_stats)))

    def test_artifact_stats_and_collision_safe_id_match_fribbels_rules(self) -> None:
        artifact = next(
            item for item in self.catalog.artifacts if item.name == "A Little Queen's Huge Crown"
        )
        self.assertEqual(
            "artifact.fribbels.efw21.a-little-queen-s-huge-crown.9c41ef4c",
            artifact.artifact_id,
        )
        self.assertEqual((30, 21, 32, 273, 416), (
            artifact.max_level,
            artifact.base_attack,
            artifact.base_health,
            artifact.max_attack,
            artifact.max_health,
        ))
        self.assertEqual(283, len({item.artifact_id for item in self.catalog.artifacts}))
        for item in self.catalog.artifacts:
            self.assertEqual(30, item.max_level)
            self.assertEqual(item.base_attack * 13, item.max_attack)
            self.assertEqual(item.base_health * 13, item.max_health)

    def test_source_rich_sidecar_is_lossless_and_immutable(self) -> None:
        self.assertEqual(
            json.loads(self.hero_bytes.decode("utf-8")),
            thaw_json(self.source.heroes),
        )
        self.assertEqual(
            json.loads(self.artifact_bytes.decode("utf-8")),
            thaw_json(self.source.artifacts),
        )
        self.assertIsInstance(self.source.heroes, FrozenJsonObject)
        with self.assertRaises(TypeError):
            self.source.heroes["Abigail"] = self.source.heroes["Abigail"]
        arunka = thaw_json(self.source.heroes["Arunka"])
        self.assertIn("S2", arunka)
        summer_photogenic = thaw_json(self.source.artifacts["Summer Photogenic"])
        self.assertEqual(5, summer_photogenic["stats"]["defense"])

    def test_missing_required_source_field_becomes_structured_rejection(self) -> None:
        heroes = json.loads(self.hero_bytes.decode("utf-8"))
        del heroes["Abigail"]["calculatedStatus"]["lv60SixStarFullyAwakened"]["atk"]
        altered_hero_bytes = json.dumps(heroes, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        bundle = build_character_snapshot(
            altered_hero_bytes,
            self.artifact_bytes,
            generated_at=GENERATED_AT,
            fetched_at=GENERATED_AT,
            require_pinned_hashes=False,
        )
        rejection = next(
            outcome for outcome in bundle.validation_report.outcomes
            if outcome.record_kind == "hero" and outcome.source_key == "Abigail"
        )
        self.assertEqual("rejected", rejection.status)
        self.assertEqual("missing-required-number", rejection.rejection.code)
        self.assertTrue(rejection.rejection.path.endswith("lv60SixStarFullyAwakened.atk"))
        self.assertEqual(1, bundle.validation_report.summary["rejectedRecords"])
        self.assertEqual(385, len(bundle.catalog.heroes))
        self.assertIn("Abigail", bundle.source_snapshot.heroes)

    def test_regeneration_is_byte_deterministic_and_manifest_hashes_match(self) -> None:
        first = build_character_snapshot(
            self.hero_bytes,
            self.artifact_bytes,
            generated_at=GENERATED_AT,
            fetched_at=GENERATED_AT,
        )
        second = build_character_snapshot(
            self.hero_bytes,
            self.artifact_bytes,
            generated_at=GENERATED_AT,
            fetched_at=GENERATED_AT,
        )
        self.assertEqual(first.generated_bytes(), second.generated_bytes())
        for filename, content in first.generated_bytes().items():
            self.assertEqual(content, bundled_character_data_path(filename).read_bytes())
        regenerated_manifest = create_character_snapshot_manifest(first)
        self.assertEqual(
            regenerated_manifest.to_json(),
            bundled_character_data_path(BUNDLED_MANIFEST_FILENAME).read_text(encoding="utf-8"),
        )
        for entry in self.manifest.files:
            content = bundled_character_data_path(entry.relative_path).read_bytes()
            self.assertEqual(entry.byte_length, len(content))
            self.assertEqual(entry.sha256, hashlib.sha256(content).hexdigest())

    def test_runtime_loaders_are_offline_and_outputs_contain_no_local_paths(self) -> None:
        with patch.object(socket, "create_connection", side_effect=AssertionError("network disabled")):
            self.assertEqual(386, len(load_bundled_character_catalog().heroes))
            self.assertEqual(386, len(load_bundled_character_source_snapshot().heroes))
            self.assertEqual(669, len(load_bundled_character_normalization_report().outcomes))
            self.assertEqual(5, len(load_bundled_character_snapshot_manifest().files))

        forbidden = (b"C:\\Users", b"Meowtoko", b"user_data", b"optimizer.db", b"gear.txt")
        for filename in (
            BUNDLED_CATALOG_FILENAME,
            BUNDLED_SOURCE_FILENAME,
            BUNDLED_VALIDATION_FILENAME,
            BUNDLED_MANIFEST_FILENAME,
        ):
            content = bundled_character_data_path(filename).read_bytes()
            for marker in forbidden:
                with self.subTest(filename=filename, marker=marker):
                    self.assertNotIn(marker, content)

    def test_public_user_documentation_pins_snapshot_and_complete_workflow_links(self) -> None:
        root = Path(__file__).resolve().parents[1]
        readme = (root / "README.md").read_text(encoding="utf-8")
        guide = (root / "docs" / "USER_GUIDE.md").read_text(encoding="utf-8")
        metrics = (root / "docs" / "METRICS.md").read_text(encoding="utf-8")
        attribution = (root / "docs" / "legal" / "ATTRIBUTION.md").read_text(encoding="utf-8")
        notices = (root / "docs" / "legal" / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        runtime_export = (root / "scripts" / "export_runtime_metadata.py").read_text(encoding="utf-8")
        self.assertIn("docs/USER_GUIDE.md", readme)
        self.assertIn('ROOT / "docs" / "legal" / "THIRD_PARTY_NOTICES.md"', runtime_export)
        self.assertIn('destination / "THIRD_PARTY_NOTICES.md"', runtime_export)
        for label in (
            "Select gear.txt", "Include equipped", "4+2", "2+2+2", "Export full view",
            "5,000,000", "Retry with CPU", "Repair GPU components", "Windows protected your PC",
        ):
            with self.subTest(label=label):
                self.assertIn(label, guide)
        for label in (
            "Combat Power", "Effective Health (EHP)", "Max Critical Damage", "Gear Score",
            "Build Score", "Priority score", "Normalized constraint distance", "S1", "S2", "S3",
        ):
            with self.subTest(metric=label):
                self.assertIn(label, metrics)
        for document in (attribution, notices):
            self.assertIn(FRIBBELS_CHARACTER_SOURCE_REVISION, document)
            self.assertIn(FRIBBELS_HERO_SHA256, document)
            self.assertIn(FRIBBELS_ARTIFACT_SHA256, document)
            self.assertIn("free, unofficial", document)


if __name__ == "__main__":
    unittest.main()
