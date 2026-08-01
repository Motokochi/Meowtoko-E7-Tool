from __future__ import annotations

import copy
import json
import math
import socket
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from src.optimizer.data import (
    ARTIFACT_MAX_LEVEL,
    FRIBBELS_ARTIFACT_BACKEND_LOGIC_GIT_BLOB_SHA1,
    FRIBBELS_ARTIFACT_LOGIC_GIT_BLOB_SHA1,
    FRIBBELS_CHARACTER_SOURCE_REVISION,
    FRIBBELS_ROUNDING_LOGIC_GIT_BLOB_SHA1,
    OPTIMIZER_PROFILE_CURRENT_VERSION,
    RUN_MANIFEST_CURRENT_VERSION,
    ArtifactEffectDataState,
    ArtifactFlatStats,
    ArtifactRepository,
    ArtifactRepositoryError,
    ArtifactStatOverrides,
    FrozenJsonObject,
    OptimizerConfiguration,
    OptimizerProfileDocument,
    SchemaValidationError,
    SourceMetadata,
    artifact_stable_id,
    calculate_artifact_flat_stat,
    load_bundled_artifact_repository,
    load_bundled_character_catalog,
    load_bundled_character_source_snapshot,
    load_character_catalog,
    load_character_source_snapshot,
    load_optimizer_profile,
    load_optimizer_profile_json,
    load_run_manifest,
    normalize_character_search_text,
    thaw_json,
)
from src.optimizer.domain import (
    GearSet,
    HeroModifiers,
    OptimizationRequest,
    SetPattern,
)


FIXTURES = Path(__file__).parent / "fixtures" / "optimizer"


class ArtifactRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_bundled_character_catalog()
        cls.source = load_bundled_character_source_snapshot()
        cls.repository = ArtifactRepository(cls.catalog, cls.source)

    def _artifact(self, name: str):
        return next(record for record in self.repository.artifacts if record.name == name)

    def _source_with(self, mutate):
        payload = self.source.to_dict()
        mutate(payload)
        return load_character_source_snapshot(payload)

    def _catalog_with(self, mutate):
        payload = self.catalog.to_dict()
        mutate(payload)
        return load_character_catalog(payload)

    def _request(self, modifiers: HeroModifiers) -> OptimizationRequest:
        return OptimizationRequest(
            request_id="request.artifact-proof",
            hero_id="hero.fribbels.ras",
            base_profile_id="profile.fribbels.ras.60.6",
            modifiers=modifiers,
            set_pattern=SetPattern((GearSet.SPEED, GearSet.HEALTH)),
        )

    def _profile_document(self, modifiers: HeroModifiers) -> OptimizerProfileDocument:
        return OptimizerProfileDocument(
            profile_id="optimizer-profile.artifact-proof",
            name="Artifact proof",
            saved_at="2026-07-20T13:00:00Z",
            source=SourceMetadata(source_name="Meowtoko E7 Tool"),
            configuration=OptimizerConfiguration.from_request(self._request(modifiers)),
            character_catalog_id=self.catalog.catalog_id,
        )

    def test_bundled_repository_maps_every_artifact_without_network(self) -> None:
        with (
            patch.object(socket, "create_connection", side_effect=AssertionError("network attempted")),
            patch.object(socket.socket, "connect", side_effect=AssertionError("network attempted")),
        ):
            repository = load_bundled_artifact_repository()

        self.assertEqual(283, len(repository))
        self.assertEqual(283, len(repository.artifacts))
        self.assertEqual(
            {artifact.artifact_id for artifact in self.catalog.artifacts},
            {artifact.artifact_id for artifact in repository.artifacts},
        )
        self.assertEqual(
            tuple(sorted(
                repository.artifacts,
                key=lambda artifact: (
                    normalize_character_search_text(artifact.name),
                    artifact.artifact_id,
                ),
            )),
            repository.artifacts,
        )
        for artifact in repository.artifacts:
            with self.subTest(artifact=artifact.name):
                self.assertEqual(
                    artifact.artifact_id,
                    artifact_stable_id(artifact.source_code, artifact.name),
                )
                self.assertIs(artifact, repository.get(artifact.artifact_id.upper()))

    def test_pinned_formula_evidence_and_level_calculation_are_exact(self) -> None:
        self.assertEqual(40, len(FRIBBELS_CHARACTER_SOURCE_REVISION))
        self.assertEqual("34dfb714ef97d6bc05da79048f1ee8e14b1c342a", FRIBBELS_ARTIFACT_LOGIC_GIT_BLOB_SHA1)
        self.assertEqual("18acebcc88380f9f3a86d98e0693000486830f1b", FRIBBELS_ROUNDING_LOGIC_GIT_BLOB_SHA1)
        self.assertEqual("f1e9d6cedbc4915f6ad2dd82e1d89bf17bca9c98", FRIBBELS_ARTIFACT_BACKEND_LOGIC_GIT_BLOB_SHA1)

        artifact = self._artifact("3F")
        expected = {
            0: ArtifactFlatStats(attack=9, health=76, defense=0),
            1: ArtifactFlatStats(attack=12.6, health=106.4, defense=0),
            15: ArtifactFlatStats(attack=63, health=532, defense=0),
            29: ArtifactFlatStats(attack=113.4, health=957.6, defense=0),
            30: ArtifactFlatStats(attack=117, health=988, defense=0),
        }
        for level, stats in expected.items():
            with self.subTest(level=level):
                selection = self.repository.select(artifact.artifact_id, level=level)
                self.assertEqual(stats, selection.calculated_flat_stats)
                self.assertEqual(stats, selection.flat_stats)
        self.assertEqual(29.4, calculate_artifact_flat_stat(21, 1))
        self.assertEqual(0, calculate_artifact_flat_stat(0, 30))

    def test_no_artifact_is_explicit_zero_and_distinct(self) -> None:
        selection = self.repository.select_none()
        self.assertIsNone(selection.artifact_id)
        self.assertIsNone(selection.level)
        self.assertEqual(ArtifactFlatStats(), selection.calculated_flat_stats)
        self.assertEqual(ArtifactFlatStats(), selection.flat_stats)
        self.assertIs(selection.effect_data_state, ArtifactEffectDataState.NOT_APPLICABLE)
        self.assertIsNone(selection.effect_value)
        self.assertEqual(HeroModifiers(), selection.to_artifact_only_modifiers())
        with self.assertRaises(ArtifactRepositoryError) as caught:
            type(selection)(level=0)
        self.assertEqual("no-artifact-configuration", caught.exception.code)

    def test_limit_break_effect_data_is_explicitly_unavailable(self) -> None:
        artifact = self._artifact("3F")
        selection = self.repository.select(artifact.artifact_id, level=30)
        self.assertIs(
            selection.effect_data_state,
            ArtifactEffectDataState.UNAVAILABLE_IN_SNAPSHOT,
        )
        self.assertIsNone(selection.limit_breaks)
        self.assertIsNone(selection.effect_value)
        for invalid in (-1, 6, True, 1.5):
            with self.subTest(invalid=invalid), self.assertRaises(ArtifactRepositoryError) as caught:
                self.repository.select(artifact.artifact_id, level=30, limit_breaks=invalid)
            self.assertEqual("invalid-limit-breaks", caught.exception.code)
        with self.assertRaises(ArtifactRepositoryError) as unavailable:
            self.repository.select(artifact.artifact_id, level=30, limit_breaks=0)
        self.assertEqual("limit-break-data-unavailable", unavailable.exception.code)

    def test_custom_overrides_replace_final_flat_stats_only(self) -> None:
        artifact = self._artifact("3F")
        selection = self.repository.select(
            artifact.artifact_id,
            level=1,
            overrides=ArtifactStatOverrides(attack=0, defense=99),
        )
        self.assertEqual(
            ArtifactFlatStats(attack=12.6, health=106.4, defense=0),
            selection.calculated_flat_stats,
        )
        self.assertEqual(
            ArtifactFlatStats(attack=0, health=106.4, defense=99),
            selection.flat_stats,
        )
        modifiers = selection.to_artifact_only_modifiers()
        self.assertEqual(0, modifiers.artifact_attack_override)
        self.assertEqual(99, modifiers.artifact_defense_override)
        self.assertEqual((), modifiers.custom_bonuses)
        self.assertEqual(selection, self.repository.select_from_modifiers(modifiers))

    def test_zero_attack_health_and_nonzero_defense_records_remain_lossless(self) -> None:
        expected_defense = {
            "Refracted Desire": 5,
            "Ritual of Sealing Flames": 5,
            "Shadow Winds 7": 5,
            "Summer Photogenic": 5,
            "Thorn of the Blue Rose": 11,
            "Veritas": 5,
        }
        actual = {
            record.name: record.base_defense
            for record in self.repository.artifacts
            if record.base_defense
        }
        self.assertEqual(expected_defense, actual)
        for name, base_defense in expected_defense.items():
            with self.subTest(name=name):
                record = self._artifact(name)
                source = thaw_json(record.raw_source)
                self.assertEqual(base_defense, source["stats"]["defense"])
                self.assertEqual(base_defense * 13, record.max_defense)
                self.assertEqual(
                    base_defense * 13,
                    self.repository.select(record.artifact_id, level=30).flat_stats.defense,
                )
        self.assertEqual(0, self._artifact("Veritas").definition.base_attack)
        self.assertEqual(0, self._artifact("Summer Photogenic").definition.base_health)

    def test_reused_source_codes_return_all_matches_without_collision(self) -> None:
        expected = {
            "Ascending Axe",
            "Axe of Heavenly Mandate",
        }
        matches = self.repository.source_code_matches("EF315")
        self.assertEqual(expected, {record.name for record in matches})
        self.assertEqual(2, len(matches))
        self.assertNotEqual(matches[0].artifact_id, matches[1].artifact_id)
        self.assertEqual((), self.repository.source_code_matches("missing"))

    def test_repository_records_and_source_data_are_immutable(self) -> None:
        record = self._artifact("3F")
        self.assertIsInstance(record.raw_source, FrozenJsonObject)
        self.assertIsInstance(record.raw_stats, FrozenJsonObject)
        with self.assertRaises(TypeError):
            record.raw_stats["attack"] = 999
        with self.assertRaises((AttributeError, TypeError)):
            record.base_defense = 999
        with self.assertRaises(AttributeError):
            self.repository._artifacts = ()

    def test_invalid_levels_numbers_and_overrides_are_actionable(self) -> None:
        artifact = self._artifact("3F")
        for level in (-1, ARTIFACT_MAX_LEVEL + 1, True, 1.5, None):
            with self.subTest(level=level), self.assertRaises(ArtifactRepositoryError) as caught:
                self.repository.select(artifact.artifact_id, level=level)
            self.assertEqual("invalid-level", caught.exception.code)
        for value in (-1, math.nan, math.inf, True):
            with self.subTest(base=value), self.assertRaises(ArtifactRepositoryError):
                calculate_artifact_flat_stat(value, 0)
            with self.subTest(override=value), self.assertRaises(ArtifactRepositoryError):
                ArtifactStatOverrides(attack=value)
        with self.assertRaises(ArtifactRepositoryError) as missing:
            self.repository.get("artifact.missing")
        self.assertEqual("artifact-not-found", missing.exception.code)

    def test_catalog_sidecar_and_rich_field_drift_are_actionable(self) -> None:
        mismatched = replace(
            self.source,
            source=replace(self.source.source, source_revision="0" * 40),
        )
        with self.assertRaises(ArtifactRepositoryError) as provenance:
            ArtifactRepository(self.catalog, mismatched)
        self.assertEqual("source-provenance-drift", provenance.exception.code)

        def remove_artifact(payload) -> None:
            payload["records"]["artifacts"].pop("3F")
            next(item for item in payload["inputs"] if item["recordKind"] == "artifact")[
                "recordCount"
            ] -= 1

        with self.assertRaises(ArtifactRepositoryError) as count:
            ArtifactRepository(self.catalog, self._source_with(remove_artifact))
        self.assertEqual("artifact-count-drift", count.exception.code)

        def rename_key(payload) -> None:
            records = payload["records"]["artifacts"]
            records["Wrong 3F key"] = records.pop("3F")

        with self.assertRaises(ArtifactRepositoryError) as name:
            ArtifactRepository(self.catalog, self._source_with(rename_key))
        self.assertEqual("name-drift", name.exception.code)

        def drift_stat(payload) -> None:
            artifact = next(item for item in payload["artifacts"] if item["name"] == "3F")
            artifact["baseAttack"] += 1

        with self.assertRaises(ArtifactRepositoryError) as stat:
            ArtifactRepository(self._catalog_with(drift_stat), self.source)
        self.assertEqual("stat-drift", stat.exception.code)

        for replacement in ("bad", -1, True):
            with self.subTest(replacement=replacement):
                source = self._source_with(
                    lambda payload, replacement=replacement: payload["records"]["artifacts"][
                        "3F"
                    ]["stats"].__setitem__("attack", replacement)
                )
                with self.assertRaises(ArtifactRepositoryError) as malformed:
                    ArtifactRepository(self.catalog, source)
                self.assertEqual("invalid-number", malformed.exception.code)

    def test_artifact_configuration_persists_and_v1_documents_migrate(self) -> None:
        artifact = self._artifact("3F")
        selection = self.repository.select(
            artifact.artifact_id,
            level=15,
            overrides=ArtifactStatOverrides(attack=101.5, health=202, defense=3.5),
        )
        document = self._profile_document(selection.to_artifact_only_modifiers())
        reloaded = load_optimizer_profile_json(
            document.to_json(),
            character_catalog=self.catalog,
        )
        self.assertEqual(OPTIMIZER_PROFILE_CURRENT_VERSION, reloaded.to_dict()["schemaVersion"])
        restored = self.repository.select_from_modifiers(
            reloaded.create_request("request.artifact-restored").modifiers
        )
        self.assertEqual(selection, restored)
        self.assertEqual(ArtifactFlatStats(101.5, 202, 3.5), restored.flat_stats)

        no_artifact = self._profile_document(self.repository.select_none().to_artifact_only_modifiers())
        no_artifact_reloaded = load_optimizer_profile_json(
            no_artifact.to_json(),
            character_catalog=self.catalog,
        )
        self.assertEqual(
            self.repository.select_none(),
            self.repository.select_from_modifiers(
                no_artifact_reloaded.create_request("request.no-artifact-restored").modifiers
            ),
        )

        profile_v1 = json.loads((FIXTURES / "optimizer-profile-v1.json").read_text(encoding="utf-8"))
        profile_v1_original = copy.deepcopy(profile_v1)
        migrated_profile = load_optimizer_profile(profile_v1)
        self.assertEqual(profile_v1_original, profile_v1)
        self.assertEqual(OPTIMIZER_PROFILE_CURRENT_VERSION, migrated_profile.to_dict()["schemaVersion"])
        migrated_modifiers = migrated_profile.configuration.to_dict()["modifiers"]
        self.assertIsNone(migrated_modifiers["artifactLimitBreaks"])
        self.assertIsNone(migrated_modifiers["artifactAttackOverride"])
        self.assertIsNone(migrated_modifiers["artifactHealthOverride"])
        self.assertIsNone(migrated_modifiers["artifactDefenseOverride"])

        run_v1 = json.loads((FIXTURES / "run-manifest-v1.json").read_text(encoding="utf-8"))
        migrated_run = load_run_manifest(run_v1)
        self.assertEqual(RUN_MANIFEST_CURRENT_VERSION, migrated_run.to_dict()["schemaVersion"])
        self.assertIsNone(migrated_run.request_snapshot.modifiers.artifact_attack_override)

    def test_persisted_level_and_limit_break_validation_uses_catalog_context(self) -> None:
        artifact = self._artifact("3F")
        payload = self._profile_document(
            HeroModifiers(artifact_id=artifact.artifact_id, artifact_level=31)
        ).to_dict()
        with self.assertRaisesRegex(SchemaValidationError, "exceeds maximum level 30"):
            load_optimizer_profile(payload, character_catalog=self.catalog)

        payload = self._profile_document(
            HeroModifiers(
                artifact_id=artifact.artifact_id,
                artifact_level=30,
                artifact_limit_breaks=0,
            )
        ).to_dict()
        with self.assertRaisesRegex(SchemaValidationError, "no limit-break effect data"):
            load_optimizer_profile(payload, character_catalog=self.catalog)


if __name__ == "__main__":
    unittest.main()
