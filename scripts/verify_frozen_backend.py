"""Exercise the frozen backend directly without a system Python dependency."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXECUTABLE = ROOT / "dist" / "backend" / "e7-core.exe"
PIECE = {
    "enhancement": "+9",
    "slot": "Weapon",
    "set": "Speed Set",
    "mainStat": "Flat Attack",
    "substats": [
        {"stat": "Attack", "value": "12"},
        {"stat": "Health", "value": "8"},
        {"stat": "Speed", "value": "4"},
        {"stat": "Critical Hit Chance", "value": "5"},
    ],
}
SYNTHETIC_INVENTORY = {
    "items": [
        {
            "id": f"frozen-smoke-{slot.lower()}",
            "gear": slot,
            "rank": "Epic",
            "set": "HealthSet" if index < 4 else "DefenseSet",
            "enhance": 15,
            "level": 85,
            "main": {"type": main_type, "value": main_value},
            "substats": [{"type": "Speed" if slot != "Boots" else "AttackPercent", "value": 5}],
        }
        for index, (slot, main_type, main_value) in enumerate((
            ("Weapon", "Attack", 500),
            ("Helmet", "Health", 2500),
            ("Armor", "Defense", 300),
            ("Necklace", "CriticalHitDamagePercent", 65),
            ("Ring", "EffectivenessPercent", 65),
            ("Boots", "Speed", 45),
        ))
    ],
    "heroes": [],
}


class FrozenBackend:
    def __init__(
        self,
        executable: Path,
        user_data: Path,
        resources: Path,
        *,
        command: list[str] | None = None,
        working_directory: Path | None = None,
        cuda_disabled: bool = True,
    ):
        environment = dict(os.environ)
        for key in (
            "E7_BACKEND_EXECUTABLE",
            "E7_PROJECT_ROOT",
            "E7_PYTHON",
            "E7_SETTINGS_PATH",
            "E7_USER_DATA_DIR",
            "E7_RESOURCES_PATH",
            "PYTHONHOME",
            "PYTHONPATH",
        ):
            environment.pop(key, None)
        environment.update({
            "E7_USER_DATA_DIR": str(user_data),
            "E7_RESOURCES_PATH": str(resources),
            "PATH": str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"),
            "ProgramFiles": str(resources / "missing-program-files"),
            "ProgramFiles(x86)": str(resources / "missing-program-files-x86"),
            "APPDATA": str(user_data.parent / "app-data"),
            "LOCALAPPDATA": str(user_data.parent / "local-app-data"),
            "USERPROFILE": str(user_data.parent / "user-profile"),
        })
        if cuda_disabled:
            environment["E7_DISABLE_CUDA"] = "1"
        else:
            environment.pop("E7_DISABLE_CUDA", None)
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen(
            [str(executable)] if command is None else command,
            cwd=user_data if working_directory is None else working_directory,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=creation_flags,
        )
        self.counter = 0
        self.events: list[dict[str, Any]] = []

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.counter += 1
        request_id = f"request-{self.counter}"
        payload = {"protocol": 1, "id": request_id, "method": method, "params": params or {}}
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        self.process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        while True:
            line = self.process.stdout.readline()
            if not line:
                stderr = self.process.stderr.read() if self.process.stderr is not None else ""
                raise AssertionError(f"Frozen backend exited before {method}: {stderr}")
            response = json.loads(line)
            if "event" in response:
                self.events.append(response)
                continue
            if response.get("id") == request_id:
                if response.get("ok") is not True:
                    raise AssertionError(f"Frozen backend rejected {method}: {response}")
                return response["result"]

    def stop(self) -> None:
        if self.process.poll() is None:
            self.request("system.shutdown")
        try:
            exit_code = self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            raise AssertionError("Frozen backend did not stop after shutdown acknowledgement.")
        if exit_code != 0:
            stderr = self.process.stderr.read() if self.process.stderr is not None else ""
            raise AssertionError(f"Frozen backend exited with {exit_code}: {stderr}")


def run(executable: Path) -> None:
    if not executable.is_file():
        raise AssertionError(f"Frozen backend is missing: {executable}")
    with tempfile.TemporaryDirectory(prefix="e7-frozen-") as temporary:
        temp_root = Path(temporary).resolve()
        user_data = temp_root / "user-data"
        resources = executable.parent.parent.resolve()
        user_data.mkdir()
        if not (resources / "cuda-installer" / "python.exe").is_file():
            raise AssertionError("Frozen resource root does not contain the trusted CUDA installer helper.")
        inventory_source = temp_root / "synthetic-gear.txt"
        inventory_source.write_text(json.dumps(SYNTHETIC_INVENTORY), encoding="utf-8")

        session = FrozenBackend(executable, user_data, resources)
        try:
            ping = session.request("system.ping")
            health = session.request("health.get")
            health_deadline = time.monotonic() + 20
            while health["overall"] == "checking":
                if time.monotonic() >= health_deadline:
                    raise AssertionError("Frozen health check did not finish within 20 seconds.")
                time.sleep(0.05)
                health = session.request("health.get")
            settings = session.request("settings.get")
            updated = session.request("settings.update", {
                "revision": settings["revision"],
                "patch": {"targetWindow": "Frozen Package Test"},
            })
            analyzer_options = session.request("analyzer.options")
            evaluation = session.request("analyzer.evaluate", {"piece": PIECE})
            analyzer_state = session.request("analyzer.scan.get")
            enhancement_options = session.request("enhancement.options")
            enhancement_state = session.request("enhancement.job.get")
            enhancement_debug = session.request("enhancement.debug.get")
            inventory = session.request("optimizer.inventory.get")
            if (user_data / "optimizer.db").exists():
                raise AssertionError("Frozen status-only inventory check created a database.")
            imported_inventory = session.request(
                "optimizer.inventory.import",
                {"sourcePath": str(inventory_source)},
            )
            hero = session.request("optimizer.hero.search", {"query": "Achates", "limit": 1})["results"][0]
            hero_details = session.request("optimizer.hero.details", {"heroId": hero["heroId"]})
            hero_draft = session.request("optimizer.profile.load", {"heroId": hero["heroId"]})
            if (user_data / "optimizer_profiles").exists():
                raise AssertionError("Frozen profile reads created draft storage.")
            artifact = session.request("optimizer.artifact.search", {"query": "", "limit": 1})["results"][0]
            configured_draft = hero_draft["draft"]
            configured_draft["baseProfileId"] = hero_details["profiles"][0]["profileId"]
            configured_draft["artifact"] = {
                "artifactId": artifact["artifactId"],
                "level": 30,
                "attackOverride": None,
                "healthOverride": None,
                "defenseOverride": None,
            }
            configured_draft["imprintGrade"] = hero_details["imprints"][-1]["grade"]
            equipment = hero_details["exclusiveEquipment"]
            configured_draft["exclusiveEquipment"] = {
                "equipmentId": equipment["equipmentId"],
                "statValue": equipment["rolls"][-1],
                "skillOptionId": equipment["skillOptions"][0]["optionId"],
            }
            configured_draft["customBonuses"]["attackPercent"] = 12.5
            configured_draft["primaryStats"] = {
                "attack": {"minimum": 1200, "maximum": 4200, "priority": -1},
                "health": {"minimum": 0, "maximum": None, "priority": 0},
                "defense": {"minimum": None, "maximum": 2400, "priority": 1},
                "speed": {"minimum": 180, "maximum": 260, "priority": 2},
                "criticalHitChancePercent": {"minimum": 85.5, "maximum": 100, "priority": 3},
                "criticalHitDamagePercent": {"minimum": 250.25, "maximum": 350, "priority": -1},
                "effectivenessPercent": {"minimum": 0, "maximum": None, "priority": 0},
                "effectResistancePercent": {"minimum": None, "maximum": 200.5, "priority": 3},
            }
            configured_draft["setPattern"] = {
                "kind": "flexible",
                "sets": ["set.health", None, None],
            }
            configured_draft["includeEquipped"] = True
            configured_draft["maximumReplacementDistance"] = 0
            configured_draft["nearSetTolerancePercent"] = 0
            configured_draft["itemProjectionMode"] = "projection.reforged"
            configured_draft["gearFilters"] = {
                "minimumEnhance": 15,
                "rightSideMainStats": {
                    "slot.necklace": ["item_stat.critical_hit_damage_percent"],
                    "slot.ring": ["item_stat.effectiveness_percent"],
                    "slot.boots": ["item_stat.speed"],
                },
            }
            configured_draft["skills"][0]["hitType"] = hero_details["skills"][0]["hitTypes"][0]
            configured_draft["skills"][0]["targetCountOverride"] = 1
            configured_draft["skills"][0]["penetrationPercent"] = 20
            configured_draft["skills"][0]["targetDefense"] = 1200
            saved_profile = session.request("optimizer.profile.save", {"draft": configured_draft})
            search_draft = json.loads(json.dumps(configured_draft))
            for bounds in search_draft["primaryStats"].values():
                bounds["minimum"] = None
                bounds["maximum"] = None
            search_started = session.request("optimizer.search.start", {"draft": search_draft})
            search_deadline = time.monotonic() + 20
            search_terminal = session.request("optimizer.search.get")
            while search_terminal["state"] in {"preparing", "running"}:
                if time.monotonic() >= search_deadline:
                    raise AssertionError("Frozen optimizer search did not finish within 20 seconds.")
                time.sleep(0.01)
                search_terminal = session.request("optimizer.search.get")
            result_options = session.request("optimizer.results.options")
            empty_range = {"minimum": None, "maximum": None}
            result_started = session.request("optimizer.results.query", {"query": {
                "runId": search_terminal["resultRunId"],
                "category": "all",
                "sortKey": "priority-score",
                "direction": "descending",
                "pageIndex": 0,
                "pageSize": 100,
                "primaryRanges": {
                    item["fieldId"]: dict(empty_range) for item in result_options["primaryFields"]
                },
                "derivedRanges": {
                    item["fieldId"]: dict(empty_range) for item in result_options["derivedFields"]
                },
                "priorityScore": dict(empty_range),
                "constraintDistance": dict(empty_range),
                "replacementCount": dict(empty_range),
                "equippedCount": dict(empty_range),
            }})
            result_deadline = time.monotonic() + 20
            result_terminal = session.request("optimizer.results.get")
            while result_terminal["state"] == "running":
                if time.monotonic() >= result_deadline:
                    raise AssertionError("Frozen optimizer result page did not finish within 20 seconds.")
                time.sleep(0.01)
                result_terminal = session.request("optimizer.results.get")
            result_row = result_terminal["rows"][0]
            detail_started = session.request("optimizer.results.detail", {
                "runId": search_terminal["resultRunId"],
                "queryId": result_terminal["queryId"],
                "rowKey": result_row["rowKey"],
            })
            detail_deadline = time.monotonic() + 20
            detail_terminal = None
            while detail_terminal is None:
                if time.monotonic() >= detail_deadline:
                    raise AssertionError("Frozen optimizer selected-build detail did not finish within 20 seconds.")
                time.sleep(0.01)
                session.request("optimizer.results.get")
                completed_details = [
                    event["payload"] for event in session.events
                    if event.get("event") == "optimizer.results.detail-updated"
                    and event.get("payload", {}).get("state") in {"completed", "failed"}
                ]
                if completed_details:
                    detail_terminal = completed_details[-1]
            export_path = temp_root / "frozen-results.csv"
            export_terminal = session.request("optimizer.results.export.start", {
                "runId": search_terminal["resultRunId"],
                "queryId": result_terminal["queryId"],
                "format": "csv",
                "destination": str(export_path),
            })
            export_deadline = time.monotonic() + 20
            while export_terminal["state"] == "running":
                if time.monotonic() >= export_deadline:
                    raise AssertionError("Frozen optimizer result export did not finish within 20 seconds.")
                time.sleep(0.01)
                export_terminal = session.request("optimizer.results.export.get")
        finally:
            session.stop()

        if ping["protocolVersion"] != 1 or ping["backendVersion"] != "0.6.0":
            raise AssertionError(f"Unexpected frozen backend identity: {ping}")
        if health["overall"] not in {"degraded", "ready"}:
            raise AssertionError(f"Frozen health checks are unusable: {health}")
        capability_states = {item["id"]: item["state"] for item in health["capabilities"]}
        for optional in ("tesseract", "ollama", "cuda", "packet", "adb"):
            if optional not in capability_states:
                raise AssertionError(f"Missing frozen capability result: {optional}")
        packet_capability = next(item for item in health["capabilities"] if item["id"] == "packet")
        if "bundled packet capture component is missing" in packet_capability.get("detail", "").lower():
            raise AssertionError("Frozen backend did not bundle the packet capture component.")
        cuda_capability = next(item for item in health["capabilities"] if item["id"] == "cuda")
        if not cuda_capability["metadata"]["component"]["installerAvailable"]:
            raise AssertionError("Frozen Health Center did not discover the packaged trusted helper.")
        if updated["settings"]["targetWindow"] != "Frozen Package Test":
            raise AssertionError("Frozen settings update did not persist in memory.")
        if analyzer_options["enhancements"][-1] != "+15" or evaluation["piece"] != PIECE:
            raise AssertionError("Frozen manual Analyzer contract failed.")
        if analyzer_state["state"] != "idle":
            raise AssertionError("Frozen Analyzer unexpectedly started capture.")
        if [mode["id"] for mode in enhancement_options["modes"]] != ["adb"]:
            raise AssertionError("Frozen enhancement mode is not ADB-only.")
        if enhancement_state["state"] != "idle" or enhancement_debug["available"]:
            raise AssertionError("Frozen enhancement automation unexpectedly ran.")
        if inventory["state"] != "empty" or inventory["totalItems"] != 0:
            raise AssertionError("Frozen optimizer inventory did not start in its empty aggregate state.")
        if imported_inventory["inventory"]["state"] != "ready":
            raise AssertionError("Frozen optimizer inventory import did not become ready.")
        if imported_inventory["inventory"]["totalItems"] != 6:
            raise AssertionError("Frozen optimizer inventory import returned the wrong aggregate count.")
        if not (user_data / "optimizer.db").is_file():
            raise AssertionError("Frozen optimizer inventory import did not create isolated storage.")
        if saved_profile["state"] != "saved" or saved_profile["draft"] != configured_draft:
            raise AssertionError("Frozen optimizer hero profile did not save exactly.")
        if saved_profile["draft"]["primaryStats"]["health"]["minimum"] != 0:
            raise AssertionError("Frozen optimizer profile lost a zero primary-stat bound.")
        if saved_profile["draft"]["setPattern"] != {
            "kind": "flexible", "sets": ["set.health", None, None],
        }:
            raise AssertionError("Frozen optimizer profile lost the optional set requirement.")
        if not saved_profile["draft"]["includeEquipped"]:
            raise AssertionError("Frozen optimizer profile lost the equipped-item policy.")
        if saved_profile["draft"]["maximumReplacementDistance"] != 0:
            raise AssertionError("Frozen optimizer profile re-enabled replacement distance.")
        if saved_profile["draft"]["nearSetTolerancePercent"] != 0:
            raise AssertionError("Frozen optimizer profile re-enabled near-set tolerance.")
        if saved_profile["draft"]["itemProjectionMode"] != "projection.reforged":
            raise AssertionError("Frozen optimizer profile lost the item projection mode.")
        if saved_profile["draft"]["gearFilters"]["minimumEnhance"] != 15:
            raise AssertionError("Frozen optimizer profile lost the gear filters.")
        if search_started["state"] != "preparing":
            raise AssertionError("Frozen optimizer search did not start asynchronously.")
        if search_terminal["state"] != "completed" or search_terminal["backend"] != "cpu":
            raise AssertionError(f"Frozen optimizer search did not complete on CPU: {search_terminal}")
        if search_terminal["searchedPermutations"] != "1":
            raise AssertionError("Frozen optimizer search lost exact permutation accounting.")
        if not search_terminal["resultAvailable"] or not search_terminal["resultRunId"]:
            raise AssertionError("Frozen optimizer search did not publish a completed result handle.")
        result_manifest = (
            user_data / "optimizer_results" / "runs"
            / search_terminal["resultRunId"] / "manifest.json"
        )
        if not result_manifest.is_file():
            raise AssertionError("Frozen optimizer search result handle is not durable.")
        if result_started["state"] != "running":
            raise AssertionError("Frozen optimizer result query did not start asynchronously.")
        if len(result_options["sortOptions"]) != 25 or result_options["maxPageSize"] != 1000:
            raise AssertionError("Frozen optimizer result options are incomplete.")
        if result_terminal["state"] != "completed" or len(result_terminal["rows"]) != 1:
            raise AssertionError(f"Frozen optimizer result page did not resolve one bounded row: {result_terminal}")
        if set(result_terminal["rows"][0]) != {
            "rowKey", "category", "replacementCount", "equippedCount", "priorityScore",
            "constraintDistance", "primaryStats", "derivedMetrics", "sets",
        }:
            raise AssertionError("Frozen optimizer result row exposed an unexpected field.")
        if detail_started["state"] != "loading":
            raise AssertionError("Frozen selected-build detail did not start asynchronously.")
        if detail_terminal["state"] != "completed":
            raise AssertionError(f"Frozen selected-build detail failed: {detail_terminal}")
        if len(detail_terminal["detail"]["gear"]) != 6:
            raise AssertionError("Frozen selected-build detail did not resolve exactly six owned pieces.")
        if detail_terminal["detail"]["guidance"]["kind"] != "set-complete":
            raise AssertionError("Frozen exact build fabricated future replacement guidance.")
        if any("itemId" in item or "denseId" in item for item in detail_terminal["detail"]["gear"]):
            raise AssertionError("Frozen selected-build detail exposed private gear identities.")
        if export_terminal["state"] != "completed" or export_terminal["rowCount"] != "1":
            raise AssertionError(f"Frozen optimizer result export failed: {export_terminal}")
        if not export_path.is_file() or export_path.stat().st_size != int(export_terminal["fileBytes"]):
            raise AssertionError("Frozen optimizer result export did not publish the reported file.")
        if str(export_path) in json.dumps(export_terminal):
            raise AssertionError("Frozen optimizer result export exposed its private destination.")
        reproducibility = result_manifest.parent / "reproducibility-v1.json"
        if not reproducibility.is_file():
            raise AssertionError("Frozen optimizer result export did not persist reproducibility evidence.")
        if not any(event.get("event") == "optimizer.results.export-updated" for event in session.events):
            raise AssertionError("Frozen optimizer result export emitted no completion event.")

        second = FrozenBackend(executable, user_data, resources)
        try:
            persisted = second.request("settings.get")
            persisted_inventory = second.request("optimizer.inventory.get")
            persisted_profile = second.request("optimizer.profile.load", {"heroId": hero["heroId"]})
            restarted_search = second.request("optimizer.search.get")
            restarted_results = second.request("optimizer.results.get")
        finally:
            second.stop()
        if persisted["settings"]["targetWindow"] != "Frozen Package Test":
            raise AssertionError("Frozen settings did not survive backend restart.")
        if persisted_inventory["state"] != "ready" or persisted_inventory["totalItems"] != 6:
            raise AssertionError("Frozen optimizer inventory did not survive backend restart.")
        if persisted_profile["state"] != "saved" or persisted_profile["draft"] != configured_draft:
            raise AssertionError("Frozen optimizer hero profile did not survive backend restart.")
        if restarted_search["state"] != "idle" or not result_manifest.is_file():
            raise AssertionError("Frozen optimizer search restart state or completed run is invalid.")
        if restarted_results["state"] != "idle" or restarted_results["rows"]:
            raise AssertionError("Frozen optimizer result context incorrectly survived restart.")

        settings_path = (user_data / "settings.json").resolve()
        if not settings_path.is_file() or temp_root not in settings_path.parents:
            raise AssertionError("Frozen backend wrote settings outside isolated user data.")
        if any(path.name.lower() in {"python.exe", "py.exe"} for path in executable.parent.rglob("*.exe")):
            raise AssertionError("Frozen backend bundle contains a system Python launcher.")

    print(
        "E7_FROZEN_BACKEND_OK "
        f"protocol={ping['protocolVersion']} backend={ping['backendVersion']} "
        f"python={ping['pythonVersion']} analyzer={len(analyzer_options['slots'])} "
        f"enhancement={len(enhancement_options['modes'])} inventory={persisted_inventory['totalItems']} "
        f"profile={persisted_profile['draft']['heroId']} search={search_terminal['backend']}:"
        f"{search_terminal['searchedPermutations']} results={len(result_terminal['rows'])} "
        f"detailGear={len(detail_terminal['detail']['gear'])} export=csv:{export_terminal['rowCount']}"
    )


if __name__ == "__main__":
    selected = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_EXECUTABLE
    run(selected)
