from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIRECTORY = REPOSITORY_ROOT / "tests" / "fixtures" / "fribbels"
MANIFEST_PATH = FIXTURE_DIRECTORY / "manifest.json"
CONTRACT_PATH = REPOSITORY_ROOT / "src" / "optimizer" / "data" / "FRIBBELS_GEAR_TXT.md"
SOURCE_REVISION = "f49b0676c27d893ae4aa1b69920e4c98f37eb3fb"
UTF8_BOM = b"\xef\xbb\xbf"

CORE_ITEM_FIELDS = {
    "gear",
    "rank",
    "set",
    "enhance",
    "level",
    "main",
    "substats",
}
SCANNER_NATIVE_FIELDS = {
    "code",
    "ct",
    "e",
    "f",
    "g",
    "l",
    "mg",
    "op",
    "s",
    "type",
}
STAT_TOTAL_FIELDS = {
    "Attack",
    "AttackPercent",
    "Defense",
    "DefensePercent",
    "Health",
    "HealthPercent",
    "Speed",
    "CriticalHitChancePercent",
    "CriticalHitDamagePercent",
    "EffectivenessPercent",
    "EffectResistancePercent",
}


def _manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _raw(entry: dict[str, Any]) -> bytes:
    return (FIXTURE_DIRECTORY / entry["name"]).read_bytes()


def _decoded(entry: dict[str, Any]) -> str:
    return _raw(entry).decode(entry["encoding"])


def _document(entry: dict[str, Any]) -> Any:
    return json.loads(_decoded(entry))


def _entry(name: str) -> dict[str, Any]:
    return next(entry for entry in _manifest()["files"] if entry["name"] == name)


def _classify_structural_error(document: Any) -> str | None:
    if not isinstance(document, dict):
        return "wrong-root"
    if "items" not in document:
        return "missing-items"
    if not isinstance(document["items"], list):
        return "invalid-items-container"
    if "heroes" in document and not isinstance(document["heroes"], list):
        return "invalid-heroes-container"
    return None


class FribbelsGearTxtFixtureContractTests(unittest.TestCase):
    def test_manifest_is_the_complete_txt_corpus(self) -> None:
        manifest = _manifest()
        entries = manifest["files"]
        listed_names = [entry["name"] for entry in entries]
        actual_names = sorted(path.name for path in FIXTURE_DIRECTORY.glob("*.txt"))

        self.assertEqual(manifest["sourceRevision"], SOURCE_REVISION)
        self.assertIn("synthetic", manifest["privacy"].lower())
        self.assertEqual(len(listed_names), len(set(listed_names)))
        self.assertEqual(sorted(listed_names), actual_names)
        self.assertEqual({entry["status"] for entry in entries}, {"valid", "invalid"})

    def test_every_fixture_is_strict_utf8_with_the_claimed_bom_state(self) -> None:
        for entry in _manifest()["files"]:
            with self.subTest(fixture=entry["name"]):
                raw = _raw(entry)
                self.assertNotIn(b"\x00", raw)
                self.assertEqual(raw.startswith(UTF8_BOM), entry["bom"])
                self.assertEqual(entry["encoding"], "utf-8-sig" if entry["bom"] else "utf-8")
                decoded = _decoded(entry)
                self.assertFalse(decoded.startswith("\ufeff"))

    def test_valid_documents_have_the_supported_root_and_item_core(self) -> None:
        entries = [entry for entry in _manifest()["files"] if entry["status"] == "valid"]

        for entry in entries:
            with self.subTest(fixture=entry["name"]):
                document = _document(entry)
                self.assertIsInstance(document, dict)
                self.assertEqual(_classify_structural_error(document), None)
                self.assertIsInstance(document["items"], list)
                self.assertGreater(len(document["items"]), 0)
                if entry["rootShape"] == "items-only":
                    self.assertNotIn("heroes", document)
                else:
                    self.assertIsInstance(document["heroes"], list)

                for item in document["items"]:
                    self.assertTrue(CORE_ITEM_FIELDS.issubset(item))
                    self.assertIsInstance(item["main"], dict)
                    self.assertIn("type", item["main"])
                    self.assertIn("value", item["main"])
                    self.assertIsInstance(item["substats"], list)
                    for substat in item["substats"]:
                        self.assertIn("type", substat)
                        self.assertIn("value", substat)

    def test_scanner_fixture_retains_raw_keys_identity_and_owner_sentinel(self) -> None:
        document = _document(_entry("valid-scanner-export-utf8.txt"))
        equipped, unequipped = document["items"]

        self.assertTrue(SCANNER_NATIVE_FIELDS.issubset(equipped))
        self.assertTrue(CORE_ITEM_FIELDS.issubset(equipped))
        self.assertEqual(equipped["id"], equipped["ingameId"])
        self.assertEqual(str(document["heroes"][0]["id"]), equipped["ingameEquippedId"])
        self.assertEqual(str(equipped["p"]), equipped["ingameEquippedId"])
        self.assertNotIn("locked", equipped)

        self.assertTrue(SCANNER_NATIVE_FIELDS.issubset(unequipped))
        self.assertEqual(unequipped["ingameEquippedId"], "undefined")
        self.assertNotIn("p", unequipped)
        self.assertIn("fixtureFutureRootField", document)
        self.assertIn("fixtureFutureHeroField", document["heroes"][0])

    def test_bom_fixture_is_real_bom_and_keeps_an_unknown_item_key(self) -> None:
        entry = _entry("valid-scanner-export-bom.txt")
        document = _document(entry)

        self.assertTrue(_raw(entry).startswith(UTF8_BOM))
        self.assertEqual(document["items"][0]["fixtureUnknownItemField"], "preserve-me")
        self.assertEqual(document["items"][0]["ingameEquippedId"], "undefined")

    def test_items_only_fixture_has_no_invented_identity_or_ownership(self) -> None:
        item = _document(_entry("valid-items-only-utf8.txt"))["items"][0]

        self.assertNotIn("id", item)
        self.assertNotIn("ingameId", item)
        self.assertNotIn("ingameEquippedId", item)
        self.assertNotIn("locked", item)
        self.assertIn("fixtureLegacyUnknown", item)

    def test_enriched_fixture_covers_projection_lock_ownership_and_nulls(self) -> None:
        document = _document(_entry("valid-enriched-export-utf8.txt"))
        equipped, unequipped = document["items"]

        self.assertTrue(equipped["locked"])
        self.assertFalse(unequipped["locked"])
        self.assertEqual(equipped["equippedById"], document["heroes"][0]["id"])
        self.assertIsNone(unequipped["ingameId"])
        self.assertIsNone(unequipped["ingameEquippedId"])
        self.assertIsNone(unequipped["equippedById"])
        self.assertIsNone(unequipped["equippedByName"])
        self.assertIsNone(unequipped["material"])
        self.assertIsNotNone(unequipped["modId"])

        for item in (equipped, unequipped):
            self.assertTrue(STAT_TOTAL_FIELDS.issubset(item["augmentedStats"]))
            self.assertTrue(STAT_TOTAL_FIELDS.issubset(item["reforgedStats"]))
            self.assertEqual(item["augmentedStats"]["mainType"], item["main"]["type"])
            self.assertEqual(item["reforgedStats"]["mainType"], item["main"]["type"])
            self.assertIn("mainValue", item["augmentedStats"])
            self.assertIn("mainValue", item["reforgedStats"])

        self.assertIn("fixtureEnrichedUnknown", equipped)

    def test_invalid_documents_match_the_manifest_error_categories(self) -> None:
        invalid_entries = [
            entry for entry in _manifest()["files"] if entry["status"] == "invalid"
        ]

        for entry in invalid_entries:
            with self.subTest(fixture=entry["name"]):
                if entry["errorCategory"] == "malformed-json":
                    with self.assertRaises(json.JSONDecodeError):
                        _document(entry)
                    continue

                document = _document(entry)
                self.assertEqual(_classify_structural_error(document), entry["errorCategory"])

    def test_names_and_string_identities_are_explicitly_synthetic(self) -> None:
        allowed_numeric_ids = {700000001, 700000002, 91001}

        for entry in _manifest()["files"]:
            if entry["status"] != "valid":
                continue
            document = _document(entry)
            for record in [*document["items"], *document.get("heroes", [])]:
                if "name" in record and record["name"] is not None:
                    self.assertTrue(record["name"].startswith("Fixture"))
                for key in ("id", "ingameId", "ingameEquippedId", "equippedById", "modId"):
                    value = record.get(key)
                    if value is None or value == "undefined":
                        continue
                    if isinstance(value, int):
                        self.assertIn(value, allowed_numeric_ids)
                    elif str(value).isdecimal():
                        self.assertIn(int(value), allowed_numeric_ids)
                    else:
                        self.assertTrue(str(value).startswith("fixture-"))

    def test_documentation_pins_provenance_and_contract_boundaries(self) -> None:
        contract = CONTRACT_PATH.read_text(encoding="utf-8")

        self.assertIn(SOURCE_REVISION, contract)
        for source_name in (
            "scanner.js",
            "importer.js",
            "files.js",
            "itemSerializer.js",
            "itemAugmenter.js",
            "Item.java",
            "Stat.java",
            "MergeHero.java",
            "ItemsRequestHandler.java",
            "saves.js",
        ):
            self.assertIn(source_name, contract)
        for boundary in (
            "UTF-8 with the byte sequence `EF BB BF`",
            "`items` is required",
            "`heroes` is optional",
            "raw `l` is **not** interpreted",
            "Unknown root, item, stat, and hero keys",
            "P01-T02",
        ):
            self.assertIn(boundary, contract)


if __name__ == "__main__":
    unittest.main()
