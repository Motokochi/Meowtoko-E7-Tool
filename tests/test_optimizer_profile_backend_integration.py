import json
import copy
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src.desktop import PROTOCOL_VERSION


ROOT = Path(__file__).resolve().parents[1]
class BackendSession:
    def __init__(self, user_data_dir: Path):
        environment = dict(os.environ)
        environment["E7_USER_DATA_DIR"] = str(user_data_dir)
        self.process = subprocess.Popen(
            [sys.executable, "-u", "-m", "src.desktop.backend"],
            cwd=ROOT,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )

    def request(self, request_id: str, method: str, params: dict | None = None) -> dict:
        self.process.stdin.write(json.dumps({
            "protocol": PROTOCOL_VERSION,
            "id": request_id,
            "method": method,
            "params": params or {},
        }) + "\n")
        self.process.stdin.flush()
        while True:
            line = self.process.stdout.readline()
            if not line:
                raise AssertionError(f"backend exited before responding: {self.process.stderr.read()}")
            response = json.loads(line)
            if response.get("id") == request_id:
                return response

    def stop(self) -> str:
        if self.process.poll() is None:
            self.request("shutdown", "system.shutdown")
        self.process.wait(timeout=5)
        stderr = self.process.stderr.read()
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()
        return stderr


def _draft(backend: BackendSession, name: str, ordinal: int) -> dict:
    hero = backend.request(f"hero-{ordinal}", "optimizer.hero.search", {"query": name, "limit": 1})["result"]["results"][0]
    details = backend.request(f"details-{ordinal}", "optimizer.hero.details", {"heroId": hero["heroId"]})["result"]
    envelope = backend.request(f"load-{ordinal}", "optimizer.profile.load", {"heroId": hero["heroId"]})["result"]
    artifact = backend.request(f"artifact-{ordinal}", "optimizer.artifact.search", {"query": "", "limit": ordinal + 1})["result"]["results"][ordinal]
    draft = envelope["draft"]
    draft["baseProfileId"] = details["profiles"][0]["profileId"]
    draft["artifact"] = {
        "artifactId": artifact["artifactId"],
        "level": 29 - ordinal,
        "attackOverride": 100 + ordinal,
        "healthOverride": 200 + ordinal,
        "defenseOverride": ordinal,
    }
    draft["imprintGrade"] = details["imprints"][-1]["grade"]
    ee = details["exclusiveEquipment"]
    draft["exclusiveEquipment"] = {
        "equipmentId": ee["equipmentId"],
        "statValue": ee["rolls"][-1 - ordinal],
        "skillOptionId": ee["skillOptions"][ordinal]["optionId"],
    }
    draft["customBonuses"]["flatAttack"] = 75 + ordinal
    draft["customBonuses"]["attackPercent"] = 12.5 + ordinal
    for index, stat in enumerate(draft["primaryStats"].values()):
        stat["minimum"] = 0 if index == 0 else index * 25.25 + ordinal
        stat["maximum"] = None if index == 1 else index * 25.25 + 500 + ordinal
        stat["priority"] = (-1, 0, 1, 2, 3)[index % 5]
    if ordinal == 0:
        draft["setPattern"] = {"kind": "2+2+2", "sets": ["set.health", "set.health", "set.defense"]}
        draft["includeEquipped"] = True
        draft["maximumReplacementDistance"] = 0
        draft["nearSetTolerancePercent"] = 0
        draft["itemProjectionMode"] = "projection.reforged"
        draft["gearFilters"] = {
            "minimumEnhance": 15,
            "rightSideMainStats": {
                "slot.necklace": ["item_stat.critical_hit_damage_percent"],
                "slot.ring": ["item_stat.effectiveness_percent", "item_stat.effect_resistance_percent"],
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
            "rightSideMainStats": {"slot.necklace": [], "slot.ring": [], "slot.boots": []},
        }
    for index, context in enumerate(draft["skills"]):
        context["targetDefense"] = 1000 + ordinal * 200 + index * 100
        skill = details["skills"][index]
        if index == 1 and skill["sourceOptions"]:
            context["sourceOptionId"] = skill["sourceOptions"][0]["optionId"]
        elif skill["isDamaging"]:
            context["hitType"] = skill["hitTypes"][0]
            context["targetCountOverride"] = index + 1
            context["penetrationPercent"] = 20 + ordinal + index
    return draft


class OptimizerProfileBackendIntegrationTests(unittest.TestCase):
    def test_two_hero_profiles_restore_every_modifier_across_backend_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            user_data = Path(directory) / "isolated-user-data"
            first = BackendSession(user_data)
            try:
                achates = _draft(first, "Achates", 0)
                alencia = _draft(first, "Alencia", 1)
                invalid = copy.deepcopy(achates)
                invalid["primaryStats"]["attack"].update({"minimum": 501, "maximum": 500})
                rejected = first.request("save-invalid", "optimizer.profile.save", {"draft": invalid})
                invalid_derived = copy.deepcopy(achates)
                invalid_derived["derivedMetrics"] = {
                    "metric.ehp": {"minimum": 100_000, "maximum": None},
                }
                rejected_derived = first.request("save-invalid-derived", "optimizer.profile.save", {"draft": invalid_derived})
                invalid_set = copy.deepcopy(achates)
                invalid_set["setPattern"] = {"kind": "2+2+2", "sets": ["set.immunity", "set.immunity", "set.health"]}
                rejected_set = first.request("save-invalid-set", "optimizer.profile.save", {"draft": invalid_set})
                invalid_filter = copy.deepcopy(achates)
                invalid_filter["gearFilters"]["rightSideMainStats"]["slot.necklace"] = ["item_stat.speed"]
                rejected_filter = first.request("save-invalid-filter", "optimizer.profile.save", {"draft": invalid_filter})
                saved_a = first.request("save-a", "optimizer.profile.save", {"draft": achates})
                saved_b = first.request("save-b", "optimizer.profile.save", {"draft": alencia})
            finally:
                first_stderr = first.stop()
            self.assertTrue(saved_a["ok"])
            self.assertTrue(saved_b["ok"])
            self.assertFalse(rejected["ok"])
            self.assertEqual("draft.primaryStats.attack.maximum", rejected["error"]["data"]["fieldPath"])
            self.assertFalse(rejected_derived["ok"])
            self.assertEqual("draft", rejected_derived["error"]["data"]["fieldPath"])
            self.assertFalse(rejected_set["ok"])
            self.assertEqual("draft.setPattern.sets[1]", rejected_set["error"]["data"]["fieldPath"])
            self.assertFalse(rejected_filter["ok"])
            self.assertEqual("draft.gearFilters.rightSideMainStats.slot.necklace[0]", rejected_filter["error"]["data"]["fieldPath"])

            restarted = BackendSession(user_data)
            try:
                loaded_a = restarted.request("restart-a", "optimizer.profile.load", {"heroId": achates["heroId"]})
                loaded_b = restarted.request("restart-b", "optimizer.profile.load", {"heroId": alencia["heroId"]})
            finally:
                second_stderr = restarted.stop()
            self.assertEqual(achates, loaded_a["result"]["draft"])
            self.assertEqual(alencia, loaded_b["result"]["draft"])
            for draft in (loaded_a["result"]["draft"], loaded_b["result"]["draft"]):
                self.assertIsNotNone(draft["artifact"]["artifactId"])
                self.assertIsNotNone(draft["imprintGrade"])
                self.assertIsNotNone(draft["exclusiveEquipment"]["equipmentId"])
                self.assertIsNotNone(draft["customBonuses"]["attackPercent"])
                self.assertEqual(8, len(draft["primaryStats"]))
                self.assertEqual(0, draft["primaryStats"]["attack"]["minimum"])
                self.assertEqual({-1, 0, 1, 2, 3}, {stat["priority"] for stat in draft["primaryStats"].values()})
                self.assertNotIn("derivedMetrics", draft)
                self.assertIn(draft["setPattern"]["kind"], {"4+2", "2+2+2"})
                self.assertEqual(0, draft["maximumReplacementDistance"])
                self.assertEqual(0, draft["nearSetTolerancePercent"])
                self.assertIn(draft["itemProjectionMode"], {"projection.current", "projection.reforged"})
                self.assertEqual(15, draft["gearFilters"]["minimumEnhance"])
                self.assertEqual(3, len(draft["skills"]))
                self.assertEqual(3, len({skill["targetDefense"] for skill in draft["skills"]}))
            public_output = json.dumps([saved_a, saved_b, loaded_a, loaded_b]) + first_stderr + second_stderr
            self.assertNotIn(str(user_data), public_output)
            self.assertNotIn("rawSource", public_output)


if __name__ == "__main__":
    unittest.main()
