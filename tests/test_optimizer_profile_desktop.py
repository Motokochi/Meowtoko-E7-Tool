import json
import tempfile
import unittest
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src.desktop import PROTOCOL_VERSION
from src.desktop.optimizer_profile_controller import OptimizerProfileController
from src.desktop.optimizer_profile_service import (
    DESKTOP_PROFILE_DIRECTORY,
    OptimizerProfileService,
    OptimizerProfileServiceError,
)
from src.desktop.protocol import dispatch_message


SAVED_AT = datetime(2026, 7, 22, 18, 30, tzinfo=timezone.utc)

PRIMARY_VALUES = {
    "attack": {"minimum": 1000, "maximum": 3000, "priority": -1},
    "health": {"minimum": 0, "maximum": None, "priority": 0},
    "defense": {"minimum": None, "maximum": 2000, "priority": 1},
    "speed": {"minimum": 200, "maximum": 200, "priority": 2},
    "criticalHitChancePercent": {"minimum": 85.5, "maximum": 100, "priority": 3},
    "criticalHitDamagePercent": {"minimum": 250.25, "maximum": None, "priority": -1},
    "effectivenessPercent": {"minimum": None, "maximum": 100.5, "priority": 0},
    "effectResistancePercent": {"minimum": 100, "maximum": 200, "priority": 3},
}

def _configured_draft(service: OptimizerProfileService, hero_name: str, artifact_index: int) -> dict:
    hero_id = service.search_heroes(hero_name, 1)["results"][0]["heroId"]
    details = service.get_hero_details(hero_id)
    draft = service.load_draft(hero_id)["draft"]
    artifact = service.search_artifacts("", artifact_index + 1)["results"][artifact_index]
    draft["baseProfileId"] = details["profiles"][0]["profileId"]
    draft["artifact"] = {
        "artifactId": artifact["artifactId"],
        "level": 25 + artifact_index,
        "attackOverride": 111 + artifact_index,
        "healthOverride": 222 + artifact_index,
        "defenseOverride": 3 + artifact_index,
    }
    draft["imprintGrade"] = details["imprints"][-1 - artifact_index]["grade"]
    equipment = details["exclusiveEquipment"]
    assert equipment is not None
    draft["exclusiveEquipment"] = {
        "equipmentId": equipment["equipmentId"],
        "statValue": equipment["rolls"][-1 - artifact_index],
        "skillOptionId": equipment["skillOptions"][artifact_index]["optionId"],
    }
    for index, key in enumerate(draft["customBonuses"]):
        draft["customBonuses"][key] = (index + 1) * (artifact_index + 1) / 2
    draft["primaryStats"] = {
        key: {
            "minimum": None if value["minimum"] is None else value["minimum"] + artifact_index,
            "maximum": None if value["maximum"] is None else value["maximum"] + artifact_index,
            "priority": value["priority"],
        }
        for key, value in PRIMARY_VALUES.items()
    }
    if artifact_index == 0:
        draft["setPattern"] = {
            "kind": "2+2+2",
            "sets": ["set.health", "set.health", "set.defense"],
        }
        draft["includeEquipped"] = True
        draft["maximumReplacementDistance"] = 0
        draft["nearSetTolerancePercent"] = 0
        draft["itemProjectionMode"] = "projection.reforged"
        draft["gearFilters"] = {
            "minimumEnhance": 15,
            "rightSideMainStats": {
                "slot.necklace": [
                    "item_stat.critical_hit_chance_percent",
                    "item_stat.critical_hit_damage_percent",
                ],
                "slot.ring": [
                    "item_stat.effectiveness_percent",
                    "item_stat.effect_resistance_percent",
                ],
                "slot.boots": ["item_stat.speed"],
            },
        }
    else:
        draft["setPattern"] = {"kind": "4+2", "sets": ["set.rage", "set.penetration"]}
        draft["includeEquipped"] = False
        draft["maximumReplacementDistance"] = 0
        draft["nearSetTolerancePercent"] = 0
        draft["itemProjectionMode"] = "projection.current"
        draft["gearFilters"] = {
            "minimumEnhance": 15,
            "rightSideMainStats": {
                "slot.necklace": [],
                "slot.ring": ["item_stat.health_percent"],
                "slot.boots": [],
            },
        }
    for index, skill in enumerate(details["skills"]):
        context = draft["skills"][index]
        context["targetDefense"] = 900 + artifact_index * 100 + index * 111
        if index == 1 and skill["sourceOptions"]:
            context["sourceOptionId"] = skill["sourceOptions"][0]["optionId"]
            context["hitType"] = None
            context["targetCountOverride"] = None
            context["penetrationPercent"] = None
        elif skill["isDamaging"]:
            context["hitType"] = skill["hitTypes"][0] if skill["hitTypes"] else None
            context["targetCountOverride"] = index + 1
            context["penetrationPercent"] = 10.5 + artifact_index + index
    return draft


class OptimizerProfileDesktopServiceTests(unittest.TestCase):
    def _service(self, user_data: Path) -> OptimizerProfileService:
        return OptimizerProfileService(user_data, clock=lambda: SAVED_AT)

    def test_bounded_catalog_projection_exposes_every_choice_without_raw_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = self._service(Path(directory))
            self.assertEqual(386, len(service.characters.heroes))
            self.assertEqual(283, len(service.artifacts.artifacts))
            self.assertEqual(50, len(service.search_heroes("", 50)["results"]))
            self.assertEqual(50, len(service.search_artifacts("", 50)["results"]))
            for hero in service.characters.heroes:
                self.assertIn(hero.hero_id, {item["heroId"] for item in service.search_heroes(hero.name, 50)["results"]})
            for artifact in service.artifacts.artifacts:
                self.assertIn(artifact.artifact_id, {item["artifactId"] for item in service.search_artifacts(artifact.name, 50)["results"]})

            duplicate_code = next(code for code, count in Counter(item.source_code for item in service.artifacts.artifacts).items() if count > 1)
            matches = service.search_artifacts(duplicate_code, 50)["results"]
            self.assertGreater(len(matches), 1)
            self.assertEqual(len(matches), len({item["artifactId"] for item in matches}))
            encoded = json.dumps({"heroes": service.search_heroes("Ras", 10), "artifacts": matches})
            for forbidden in ("rawSource", "portraits", "skills", "selfDevotion", "sourcePath"):
                self.assertNotIn(forbidden, encoded)

    def test_representative_heroes_and_complete_modifier_options_are_projected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = self._service(Path(directory))
            for name in ("Ras", "Seaside Bellona", "ae-GISELLE", "Adventurer Ras", "Aube", "Tidal Rift Elvira"):
                hero_id = service.search_heroes(name, 1)["results"][0]["heroId"]
                details = service.get_hero_details(hero_id)
                self.assertEqual(name, details["hero"]["name"])
                self.assertEqual(2, len(details["profiles"]))
                self.assertIn(details["defaultProfileId"], {item["profileId"] for item in details["profiles"]})
                self.assertTrue(details["imprints"])
                self.assertEqual(13, len(details["customBonusFields"]))
                self.assertEqual(24, len(details["sets"]))
                self.assertEqual(3, len(details["rightSideMainStats"]))
                self.assertEqual(["S1", "S2", "S3"], [item["label"] for item in details["skills"]])
                self.assertEqual(
                    ["slot.necklace", "slot.ring", "slot.boots"],
                    [item["slotId"] for item in details["rightSideMainStats"]],
                )
                self.assertEqual(24, len({item["setId"] for item in details["sets"]}))
                health = next(item for item in details["sets"] if item["setId"] == "set.health")
                immunity = next(item for item in details["sets"] if item["setId"] == "set.immunity")
                speed = next(item for item in details["sets"] if item["setId"] == "set.speed")
                self.assertEqual((2, True), (health["piecesRequired"], health["stackable"]))
                self.assertEqual((2, False), (immunity["piecesRequired"], immunity["stackable"]))
                self.assertEqual((4, False), (speed["piecesRequired"], speed["stackable"]))
            no_ee = next(hero for hero in service.characters.heroes if service.hero_modifiers.exclusive_equipment_for(hero.hero_id) is None)
            with_ee = next(hero for hero in service.characters.heroes if service.hero_modifiers.exclusive_equipment_for(hero.hero_id) is not None)
            self.assertIsNone(service.get_hero_details(no_ee.hero_id)["exclusiveEquipment"])
            self.assertEqual(3, len(service.get_hero_details(with_ee.hero_id)["exclusiveEquipment"]["skillOptions"]))

    def test_view_and_default_load_do_not_create_storage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            user_data = Path(directory) / "not-created"
            service = self._service(user_data)
            hero_id = service.search_heroes("Ras", 1)["results"][0]["heroId"]
            envelope = service.load_draft(hero_id)
            self.assertEqual("default", envelope["state"])
            self.assertEqual(service.get_hero_details(hero_id)["defaultProfileId"], envelope["draft"]["baseProfileId"])
            self.assertEqual(
                {key: {"minimum": None, "maximum": None, "priority": 0} for key in PRIMARY_VALUES},
                envelope["draft"]["primaryStats"],
            )
            self.assertNotIn("derivedMetrics", envelope["draft"])
            self.assertEqual(
                {"kind": "flexible", "sets": [None, None, None]},
                envelope["draft"]["setPattern"],
            )
            self.assertFalse(envelope["draft"]["includeEquipped"])
            self.assertEqual(0, envelope["draft"]["maximumReplacementDistance"])
            self.assertEqual(0, envelope["draft"]["nearSetTolerancePercent"])
            self.assertEqual("projection.current", envelope["draft"]["itemProjectionMode"])
            self.assertEqual({
                "minimumEnhance": 15,
                "rightSideMainStats": {
                    "slot.necklace": [], "slot.ring": [], "slot.boots": [],
                },
            }, envelope["draft"]["gearFilters"])
            self.assertFalse(user_data.exists())
            self.assertFalse((user_data / DESKTOP_PROFILE_DIRECTORY).exists())

    def test_two_complete_hero_drafts_survive_service_restart_independently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            user_data = Path(directory)
            first = self._service(user_data)
            achates = _configured_draft(first, "Achates", 0)
            alencia = _configured_draft(first, "Alencia", 1)
            saved_achates = first.save_draft(achates)
            saved_alencia = first.save_draft(alencia)
            self.assertEqual("saved", saved_achates["state"])
            self.assertEqual("saved", saved_alencia["state"])
            self.assertEqual("2026-07-22T18:30:00.000Z", saved_achates["savedAt"])

            restarted = self._service(user_data)
            loaded_achates = restarted.load_draft(achates["heroId"])["draft"]
            loaded_alencia = restarted.load_draft(alencia["heroId"])["draft"]
            self.assertEqual(achates, loaded_achates)
            self.assertEqual(alencia, loaded_alencia)
            self.assertNotEqual(loaded_achates["baseProfileId"], loaded_alencia["baseProfileId"])
            for key in ("artifact", "imprintGrade", "exclusiveEquipment", "customBonuses", "primaryStats", "setPattern", "includeEquipped", "itemProjectionMode", "gearFilters", "skills"):
                self.assertNotEqual(loaded_achates[key], loaded_alencia[key])
            self.assertEqual(0, loaded_achates["maximumReplacementDistance"])
            self.assertEqual(0, loaded_alencia["maximumReplacementDistance"])
            self.assertEqual(0, loaded_achates["nearSetTolerancePercent"])
            self.assertEqual(0, loaded_alencia["nearSetTolerancePercent"])
            files = list((user_data / DESKTOP_PROFILE_DIRECTORY).glob("*.json"))
            self.assertEqual(2, len(files))
            self.assertEqual([], list((user_data / DESKTOP_PROFILE_DIRECTORY).glob("*.tmp")))
            for path in files:
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual("e7.optimizer.optimizer-profile", payload["schemaId"])
                self.assertEqual(7, payload["schemaVersion"])

            achates_payload = json.loads(first._profile_path(achates["heroId"]).read_text(encoding="utf-8"))
            saved_ranges = achates_payload["configuration"]["statRanges"]
            self.assertEqual(0, saved_ranges["final_stat.health"]["minimum"])
            self.assertEqual(2000, saved_ranges["final_stat.defense"]["maximum"])
            self.assertEqual(85.5, saved_ranges["final_stat.critical_hit_chance"]["minimum"])
            self.assertEqual(250.25, saved_ranges["final_stat.critical_hit_damage"]["minimum"])
            self.assertEqual({
                "final_stat.attack": -1,
                "final_stat.health": 0,
                "final_stat.defense": 1,
                "final_stat.speed": 2,
                "final_stat.critical_hit_chance": 3,
                "final_stat.critical_hit_damage": -1,
                "final_stat.effectiveness": 0,
                "final_stat.effect_resistance": 3,
            }, achates_payload["configuration"]["statPriorities"])
            self.assertEqual({}, achates_payload["configuration"]["derivedMetricRanges"])
            self.assertEqual(["set.health", "set.health", "set.defense"], achates_payload["configuration"]["setPattern"]["sets"])
            self.assertTrue(achates_payload["configuration"]["includeEquipped"])
            self.assertEqual(0, achates_payload["configuration"]["maximumReplacementDistance"])
            self.assertEqual(0, achates_payload["configuration"]["nearSetTolerance"])
            self.assertEqual("projection.reforged", achates_payload["configuration"]["itemProjectionMode"])
            self.assertEqual(15, achates_payload["configuration"]["gearFilters"]["minimumEnhance"])

    def test_blank_zero_one_sided_equal_and_percentage_primary_values_map_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = self._service(Path(directory))
            draft = _configured_draft(service, "Achates", 0)
            request = service._draft_to_request(draft)
            ranges = {stat.value: value.to_dict() for stat, value in request.stat_ranges}
            self.assertEqual({"minimum": 0, "maximum": None}, ranges["final_stat.health"])
            self.assertEqual({"minimum": None, "maximum": 2000}, ranges["final_stat.defense"])
            self.assertEqual({"minimum": 200, "maximum": 200}, ranges["final_stat.speed"])
            self.assertEqual({"minimum": 85.5, "maximum": 100}, ranges["final_stat.critical_hit_chance"])
            self.assertEqual({"minimum": 250.25, "maximum": None}, ranges["final_stat.critical_hit_damage"])
            self.assertEqual({"minimum": None, "maximum": 100.5}, ranges["final_stat.effectiveness"])
            self.assertEqual(tuple(range(-1, 4)), tuple(sorted(set(dict(request.stat_priorities).values()))))
            self.assertEqual(draft, service._request_to_draft(request))

            draft["primaryStats"]["defense"] = {"minimum": None, "maximum": None, "priority": 1}
            blank_ranges = dict(service._draft_to_request(draft).stat_ranges)
            self.assertNotIn(next(stat for stat in dict(request.stat_ranges) if stat.value == "final_stat.defense"), blank_ranges)

    def test_invalid_primary_range_and_priority_block_save_without_replacing_valid_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = self._service(Path(directory))
            draft = _configured_draft(service, "Achates", 0)
            saved = service.save_draft(draft)
            path = service._profile_path(draft["heroId"])
            valid_bytes = path.read_bytes()

            invalid_range = json.loads(json.dumps(draft))
            invalid_range["primaryStats"]["attack"].update({"minimum": 3001, "maximum": 3000})
            with self.assertRaises(OptimizerProfileServiceError) as raised:
                service.save_draft(invalid_range)
            self.assertEqual("range-order", raised.exception.code)
            self.assertEqual("draft.primaryStats.attack.maximum", raised.exception.field_path)
            self.assertEqual(valid_bytes, path.read_bytes())

            invalid_priority = json.loads(json.dumps(draft))
            invalid_priority["primaryStats"]["speed"]["priority"] = 4
            with self.assertRaises(OptimizerProfileServiceError) as raised:
                service.save_draft(invalid_priority)
            self.assertEqual("draft.primaryStats.speed.priority", raised.exception.field_path)
            self.assertEqual(valid_bytes, path.read_bytes())

    def test_derived_metric_targets_are_rejected_at_the_desktop_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = self._service(Path(directory))
            draft = _configured_draft(service, "Achates", 0)
            draft["derivedMetrics"] = {
                "metric.ehp": {"minimum": 50_000, "maximum": None},
            }
            with self.assertRaises(OptimizerProfileServiceError) as raised:
                service.save_draft(draft)
            self.assertEqual("invalid-fields", raised.exception.code)
            self.assertEqual("draft", raised.exception.field_path)
            self.assertFalse((Path(directory) / DESKTOP_PROFILE_DIRECTORY).exists())

    def test_desktop_requests_always_clear_legacy_derived_target_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = self._service(Path(directory))
            draft = _configured_draft(service, "Achates", 0)
            request = service._draft_to_request(draft)
            self.assertEqual((), request.derived_metric_ranges)
            self.assertNotIn("derivedMetrics", service._request_to_draft(request))

    def test_set_inventory_projection_and_main_stat_filters_map_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = self._service(Path(directory))
            draft = _configured_draft(service, "Achates", 0)
            request = service._draft_to_request(draft)
            self.assertEqual("2+2+2", request.set_pattern.kind)
            self.assertEqual(
                ("set.health", "set.health", "set.defense"),
                tuple(gear_set.value for gear_set in request.set_pattern.sets),
            )
            self.assertTrue(request.include_equipped)
            self.assertEqual(0, request.maximum_replacement_distance)
            self.assertEqual(0, request.near_set_tolerance)
            self.assertEqual("projection.reforged", request.item_projection_mode.value)
            self.assertEqual(15, request.gear_filters.minimum_enhance)
            self.assertEqual(
                ("slot.necklace", "slot.ring", "slot.boots"),
                tuple(slot.value for slot, _stats in request.gear_filters.right_side_main_stats),
            )
            self.assertEqual(
                ("item_stat.critical_hit_chance_percent", "item_stat.critical_hit_damage_percent"),
                tuple(stat.value for stat in request.gear_filters.allowed_main_stats_for("slot.necklace")),
            )
            self.assertEqual(draft, service._request_to_draft(request))

            draft["gearFilters"]["rightSideMainStats"]["slot.necklace"] = []
            unrestricted = service._draft_to_request(draft)
            self.assertIsNone(unrestricted.gear_filters.allowed_main_stats_for("slot.necklace"))

            four_plus_two = _configured_draft(service, "Alencia", 1)
            request = service._draft_to_request(four_plus_two)
            self.assertEqual("4+2", request.set_pattern.kind)
            self.assertEqual(("set.rage", "set.penetration"), tuple(item.value for item in request.set_pattern.sets))
            self.assertFalse(request.include_equipped)
            self.assertEqual(0, request.maximum_replacement_distance)
            self.assertEqual(0, request.near_set_tolerance)
            self.assertEqual("projection.current", request.item_projection_mode.value)

            flexible = _configured_draft(service, "Achates", 0)
            flexible["setPattern"] = {
                "kind": "flexible",
                "sets": ["set.riposte", None, None],
            }
            request = service._draft_to_request(flexible)
            self.assertEqual("flexible", request.set_pattern.kind)
            self.assertEqual(("set.riposte",), tuple(item.value for item in request.set_pattern.sets))
            self.assertEqual(flexible, service._request_to_draft(request))

            unrestricted_sets = json.loads(json.dumps(flexible))
            unrestricted_sets["setPattern"]["sets"] = [None, None, None]
            request = service._draft_to_request(unrestricted_sets)
            self.assertEqual((), request.set_pattern.sets)
            self.assertEqual(unrestricted_sets, service._request_to_draft(request))

    def test_invalid_set_and_inventory_controls_leave_saved_profile_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = self._service(Path(directory))
            draft = _configured_draft(service, "Achates", 0)
            service.save_draft(draft)
            path = service._profile_path(draft["heroId"])
            valid_bytes = path.read_bytes()
            mutations = (
                ("nonstackable-set-repeat", "draft.setPattern.sets[1]", lambda value: value.update({"setPattern": {"kind": "2+2+2", "sets": ["set.immunity", "set.immunity", "set.health"]}})),
                ("wrong-set-size", "draft.setPattern.sets[0]", lambda value: value.update({"setPattern": {"kind": "4+2", "sets": ["set.health", "set.defense"]}})),
                ("too-many-set-pieces", "draft.setPattern.sets", lambda value: value.update({"setPattern": {"kind": "flexible", "sets": ["set.speed", "set.rage", None]}})),
                ("number-out-of-range", "draft.maximumReplacementDistance", lambda value: value.update({"maximumReplacementDistance": 3})),
                ("number-out-of-range", "draft.nearSetTolerancePercent", lambda value: value.update({"nearSetTolerancePercent": 100.1})),
                ("invalid-projection-mode", "draft.itemProjectionMode", lambda value: value.update({"itemProjectionMode": "projection.future"})),
                ("number-out-of-range", "draft.gearFilters.minimumEnhance", lambda value: value["gearFilters"].update({"minimumEnhance": 16})),
                ("illegal-main-stat", "draft.gearFilters.rightSideMainStats.slot.necklace[0]", lambda value: value["gearFilters"]["rightSideMainStats"].update({"slot.necklace": ["item_stat.speed"]})),
                ("duplicate-main-stat", "draft.gearFilters.rightSideMainStats.slot.boots[1]", lambda value: value["gearFilters"]["rightSideMainStats"].update({"slot.boots": ["item_stat.speed", "item_stat.speed"]})),
            )
            for code, field_path, mutate in mutations:
                with self.subTest(field_path=field_path):
                    invalid = json.loads(json.dumps(draft))
                    mutate(invalid)
                    with self.assertRaises(OptimizerProfileServiceError) as raised:
                        service.save_draft(invalid)
                    self.assertEqual(code, raised.exception.code)
                    self.assertEqual(field_path, raised.exception.field_path)
                    self.assertEqual(valid_bytes, path.read_bytes())

            for mutation in ("missing", "extra"):
                invalid = json.loads(json.dumps(draft))
                if mutation == "missing":
                    del invalid["gearFilters"]["rightSideMainStats"]["slot.boots"]
                else:
                    invalid["gearFilters"]["rightSideMainStats"]["slot.weapon"] = ["item_stat.flat_attack"]
                with self.assertRaises(OptimizerProfileServiceError) as raised:
                    service.save_draft(invalid)
                self.assertEqual("invalid-fields", raised.exception.code)
                self.assertEqual("draft.gearFilters.rightSideMainStats", raised.exception.field_path)
                self.assertEqual(valid_bytes, path.read_bytes())

    def test_existing_item_exclusions_remain_server_side_and_survive_resave(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = self._service(Path(directory))
            draft = _configured_draft(service, "Achates", 0)
            service.save_draft(draft)
            path = service._profile_path(draft["heroId"])
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["configuration"]["gearFilters"]["excludedItemIds"] = ["item.private-preserved"]
            path.write_text(json.dumps(payload), encoding="utf-8")

            loaded = service.load_draft(draft["heroId"])
            self.assertNotIn("item.private-preserved", json.dumps(loaded))
            service.save_draft(loaded["draft"])
            resaved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                ["item.private-preserved"],
                resaved["configuration"]["gearFilters"]["excludedItemIds"],
            )
            self.assertNotIn("item.private-preserved", json.dumps(service.load_draft(draft["heroId"])))

    def test_older_schema_v7_null_projection_loads_as_current_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = self._service(Path(directory))
            draft = _configured_draft(service, "Achates", 0)
            service.save_draft(draft)
            path = service._profile_path(draft["heroId"])
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["configuration"]["itemProjectionMode"] = None
            legacy_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
            path.write_bytes(legacy_bytes)

            loaded = service.load_draft(draft["heroId"])
            self.assertEqual("projection.current", loaded["draft"]["itemProjectionMode"])
            self.assertEqual(legacy_bytes, path.read_bytes())
            service.save_draft(loaded["draft"])
            normalized = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("projection.current", normalized["configuration"]["itemProjectionMode"])

    def test_malformed_and_future_documents_are_read_only_and_never_overwritten(self) -> None:
        for payload, expected_code in (("{not-json", "profile-malformed"), (json.dumps({"schemaId": "e7.optimizer.optimizer-profile", "schemaVersion": 999}), "profile-future-version")):
            with self.subTest(expected_code=expected_code), tempfile.TemporaryDirectory() as directory:
                service = self._service(Path(directory))
                draft = _configured_draft(service, "Achates", 0)
                path = service._profile_path(draft["heroId"])
                path.parent.mkdir(parents=True)
                path.write_text(payload, encoding="utf-8")
                for operation in (lambda: service.load_draft(draft["heroId"]), lambda: service.save_draft(draft)):
                    with self.assertRaises(OptimizerProfileServiceError) as raised:
                        operation()
                    self.assertTrue(raised.exception.read_only)
                    self.assertEqual(expected_code, raised.exception.code)
                self.assertEqual(payload, path.read_text(encoding="utf-8"))

    def test_atomic_profile_replace_failure_retains_last_good_primary_and_removes_owned_temp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = self._service(Path(directory))
            draft = _configured_draft(service, "Achates", 0)
            service.save_draft(draft)
            path = service._profile_path(draft["heroId"])
            original = path.read_bytes()
            changed = json.loads(json.dumps(draft))
            changed["includeEquipped"] = not changed["includeEquipped"]

            with patch(
                "src.desktop.optimizer_profile_service.os.replace",
                side_effect=OSError("injected profile replace interruption"),
            ), self.assertRaises(OptimizerProfileServiceError) as raised:
                service.save_draft(changed)

            self.assertEqual("profile-write-failed", raised.exception.code)
            self.assertEqual(original, path.read_bytes())
            self.assertEqual([], list(path.parent.glob("*.tmp")))

    def test_saved_legacy_derived_targets_are_ignored_and_cleared_on_next_save(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = self._service(Path(directory))
            draft = _configured_draft(service, "Achates", 0)
            service.save_draft(draft)
            path = service._profile_path(draft["heroId"])
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["configuration"]["derivedMetricRanges"]["metric.private"] = {
                "minimum": 1,
                "maximum": 2,
            }
            altered = json.dumps(payload, sort_keys=True)
            path.write_text(altered, encoding="utf-8")

            loaded = service.load_draft(draft["heroId"])
            self.assertNotIn("derivedMetrics", loaded["draft"])
            service.save_draft(loaded["draft"])

            rewritten = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual({}, rewritten["configuration"]["derivedMetricRanges"])

    def test_cross_hero_profile_and_option_namespaces_fail_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = self._service(Path(directory))
            draft = _configured_draft(service, "Achates", 0)
            other = service.get_hero_details(service.search_heroes("Alencia", 1)["results"][0]["heroId"])
            draft["baseProfileId"] = other["profiles"][0]["profileId"]
            with self.assertRaises(OptimizerProfileServiceError) as raised:
                service.save_draft(draft)
            self.assertEqual("draft.baseProfileId", raised.exception.field_path)
            self.assertFalse((Path(directory) / DESKTOP_PROFILE_DIRECTORY).exists())


class FakeOptimizerProfileController:
    def __init__(self) -> None:
        self.calls = []
        self.fail = False

    def search_heroes(self, query, limit):
        self.calls.append(("heroes", query, limit))
        return {"query": query, "results": []}

    def hero_details(self, hero_id):
        self.calls.append(("details", hero_id))
        return {"hero": {"heroId": hero_id}}

    def search_artifacts(self, query, limit):
        self.calls.append(("artifacts", query, limit))
        return {"query": query, "results": []}

    def load_draft(self, hero_id):
        self.calls.append(("load", hero_id))
        if self.fail:
            raise OptimizerProfileServiceError("storage", code="profile-future-version", message="Newer version.", read_only=True)
        return {"state": "default"}

    def save_draft(self, draft):
        self.calls.append(("save", draft["heroId"]))
        return {"state": "saved"}


class OptimizerProfileDesktopProtocolTests(unittest.TestCase):
    def test_exact_profile_methods_reach_only_the_narrow_controller(self) -> None:
        controller = FakeOptimizerProfileController()
        messages = (
            ("optimizer.hero.search", {"query": "ras", "limit": 20}),
            ("optimizer.hero.details", {"heroId": "hero.ras"}),
            ("optimizer.artifact.search", {"query": "sword", "limit": 20}),
            ("optimizer.profile.load", {"heroId": "hero.ras"}),
            ("optimizer.profile.save", {"draft": {"heroId": "hero.ras"}}),
        )
        for index, (method, params) in enumerate(messages):
            response = dispatch_message(
                {"protocol": PROTOCOL_VERSION, "id": str(index), "method": method, "params": params},
                optimizer_profile_controller=controller,
            )
            self.assertTrue(response["ok"])
        self.assertEqual(["heroes", "details", "artifacts", "load", "save"], [item[0] for item in controller.calls])

    def test_invalid_params_and_future_profile_error_are_structured(self) -> None:
        controller = FakeOptimizerProfileController()
        for method, params in (
            ("optimizer.hero.search", {"query": "ras", "limit": 20, "unbounded": True}),
            ("optimizer.hero.search", {"query": "ras", "limit": True}),
            ("optimizer.hero.details", {"heroId": ""}),
            ("optimizer.profile.save", {"draft": [], "sourcePath": "private"}),
        ):
            response = dispatch_message(
                {"protocol": PROTOCOL_VERSION, "id": method, "method": method, "params": params},
                optimizer_profile_controller=controller,
            )
            self.assertEqual("invalid_params", response["error"]["code"])
        controller.fail = True
        response = dispatch_message(
            {"protocol": PROTOCOL_VERSION, "id": "future", "method": "optimizer.profile.load", "params": {"heroId": "hero.ras"}},
            optimizer_profile_controller=controller,
        )
        self.assertEqual("optimizer_profile_failed", response["error"]["code"])
        self.assertEqual(True, response["error"]["data"]["readOnly"])
        self.assertNotIn("sourcePath", json.dumps(response))

    def test_real_controller_preserves_service_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = OptimizerProfileController(OptimizerProfileService(Path(directory)))
            self.assertEqual(1, len(controller.search_heroes("Ras", 1)["results"]))


if __name__ == "__main__":
    unittest.main()
