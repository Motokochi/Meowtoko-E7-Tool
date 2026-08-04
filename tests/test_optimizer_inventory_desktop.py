import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src.desktop import PROTOCOL_VERSION
from src.desktop.optimizer_inventory_controller import OptimizerInventoryController
from src.desktop.optimizer_inventory_service import (
    OptimizerInventoryService,
    OptimizerInventoryServiceError,
)
from src.desktop.protocol import dispatch_message
from src.core.packet_inventory import normalize_account_inventory


FIXTURES = Path(__file__).parent / "fixtures" / "fribbels"
IMPORTED_AT = datetime(2026, 7, 22, 12, 34, 56, tzinfo=timezone.utc)


class OptimizerInventoryDesktopServiceTests(unittest.TestCase):
    def _service(self, user_data: Path) -> OptimizerInventoryService:
        return OptimizerInventoryService(
            user_data,
            clock=lambda: IMPORTED_AT,
            import_id_factory=lambda: "desktop-import-1",
        )

    def test_absent_database_is_an_empty_aggregate_without_creating_storage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            user_data = Path(directory) / "not-created"
            service = self._service(user_data)

            snapshot = service.get_snapshot()

            self.assertEqual(snapshot["state"], "empty")
            self.assertEqual(snapshot["totalItems"], 0)
            self.assertEqual(snapshot["gear"], [])
            self.assertEqual(snapshot["lastImport"], None)
            self.assertEqual(
                [item["label"] for item in snapshot["itemsBySlot"]],
                ["Weapon", "Helmet", "Armor", "Necklace", "Ring", "Boots"],
            )
            self.assertFalse(user_data.exists())
            self.assertFalse(service.database_path.exists())

    def test_valid_import_returns_only_aggregate_state_and_persists_last_import(self) -> None:
        source = FIXTURES / "valid-enriched-export-utf8.txt"
        with tempfile.TemporaryDirectory() as directory:
            service = self._service(Path(directory))

            result = service.import_file(source)
            reloaded = service.get_snapshot()

            self.assertTrue(service.database_path.exists())
            self.assertEqual(result["inventory"], reloaded)
            self.assertEqual(reloaded["state"], "ready")
            self.assertEqual(reloaded["totalItems"], 2)
            self.assertEqual(reloaded["equippedItems"], 1)
            self.assertEqual(reloaded["lockedItems"], 1)
            self.assertEqual(len(reloaded["gear"]), 1)
            self.assertEqual(reloaded["gear"][0]["enhance"], 15)
            self.assertEqual(reloaded["gear"][0]["reforgedGearScore"], 37)
            self.assertEqual(reloaded["gear"][0]["combatGearScore"], 22)
            self.assertEqual(reloaded["gear"][0]["supportGearScore"], 37)
            self.assertEqual(reloaded["gear"][0]["archetypeAnalysis"]["verdict"], "destroy")
            self.assertIn("No archetype", reloaded["gear"][0]["archetypeAnalysis"]["reason"])
            self.assertEqual(reloaded["gear"][0]["equippedHeroName"], "Fixture Guardian")
            self.assertEqual(reloaded["lastImport"]["importedAt"], "2026-07-22T12:34:56.000Z")
            self.assertEqual(result["report"]["insertedCount"], 2)
            self.assertEqual(result["report"]["resultingInventoryCount"], 2)
            self.assertEqual(result["report"]["issues"], [])
            encoded = json.dumps(result, sort_keys=True)
            self.assertNotIn(str(source), encoded)
            self.assertNotIn(source.name, encoded)
            self.assertNotIn("fixture-item-enriched", encoded)

    def test_recoverable_warning_is_sanitized_and_successfully_committed(self) -> None:
        payload = json.loads((FIXTURES / "valid-enriched-export-utf8.txt").read_text(encoding="utf-8"))
        payload["items"][0]["locked"] = "private-invalid-value"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "warning-gear.txt"
            source.write_text(json.dumps(payload), encoding="utf-8")
            service = self._service(root / "data")

            result = service.import_file(source)

            self.assertEqual(result["inventory"]["state"], "ready")
            self.assertEqual(result["report"]["warningCount"], 1)
            self.assertEqual(len(result["report"]["issues"]), 1)
            self.assertEqual(result["report"]["issues"][0]["kind"], "warning")
            encoded = json.dumps(result, sort_keys=True)
            self.assertNotIn("private-invalid-value", encoded)
            self.assertNotIn(str(source), encoded)

    def test_substats_keep_game_order_and_rolls_follow_their_stat_type(self) -> None:
        item = {
            "ingameId": "roll-order-regression",
            "gear": "Necklace",
            "rank": "Epic",
            "set": "SpeedSet",
            "enhance": 15,
            "level": 90,
            "main": {"type": "HealthPercent", "value": 65},
            "substats": [
                {"type": "EffectivenessPercent", "value": 8, "rolls": 1, "ingameRolls": 1},
                {"type": "CriticalHitChancePercent", "value": 9, "rolls": 2, "ingameRolls": 2},
                {"type": "DefensePercent", "value": 17, "rolls": 2, "ingameRolls": 2},
                {"type": "Speed", "value": 16, "rolls": 4, "ingameRolls": 4},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "gear.txt"
            source.write_text(json.dumps({"items": [item]}), encoding="utf-8")

            gear = self._service(root / "data").import_file(source)["inventory"]["gear"][0]

        self.assertEqual(
            [
                "item_stat.effectiveness_percent",
                "item_stat.critical_hit_chance_percent",
                "item_stat.defense_percent",
                "item_stat.speed",
            ],
            [stat["statId"] for stat in gear["substats"]],
        )
        match = next(
            match for match in gear["archetypeAnalysis"]["matches"]
            if match["id"] == "health-bruiser-critical-hit-chance-critical-hit-damage-defense-health-speed"
        )
        self.assertEqual(
            [{
                "statId": "item_stat.effectiveness_percent",
                "label": "Effectiveness",
                "rolls": 1,
            }],
            match["offStats"],
        )
        self.assertEqual("eligible", match["status"])

    def test_reforged_scores_are_predicted_from_packet_roll_counts(self) -> None:
        item = {
            "ingameId": "reforge-score-regression",
            "gear": "Ring",
            "rank": "Epic",
            "set": "DefenseSet",
            "enhance": 15,
            "level": 85,
            "main": {"type": "EffectResistancePercent", "value": 60},
            "substats": [
                {"type": "AttackPercent", "value": 20, "rolls": 3, "ingameRolls": 3},
                {"type": "CriticalHitDamagePercent", "value": 12, "rolls": 2, "ingameRolls": 2},
                {"type": "DefensePercent", "value": 17, "rolls": 3, "ingameRolls": 3},
                {"type": "HealthPercent", "value": 7, "rolls": 1, "ingameRolls": 1},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "gear.txt"
            source.write_text(json.dumps({"items": [item]}), encoding="utf-8")

            gear = self._service(root / "data").import_file(source)["inventory"]["gear"][0]

        self.assertEqual(69, gear["reforgedGearScore"])
        self.assertEqual(69, gear["combatGearScore"])
        self.assertEqual(29, gear["supportGearScore"])
        self.assertEqual([24, 14, 21, 8], [stat["reforgedValue"] for stat in gear["substats"]])

    def test_packet_capture_imports_all_supported_gear_and_only_five_star_heroes(self) -> None:
        account_data = {
            "equips": {
                "piece": {
                    "id": 101,
                    "code": "ecw6n",
                    "f": "set_speed",
                    "g": 5,
                    "op": [
                        ["cri_dmg", 0.13],
                        ["speed", 3],
                        ["cri", 0.04],
                        ["max_hp_rate", 0.07],
                        ["def_rate", 0.06],
                        ["speed", 2],
                    ],
                },
            },
            "units": {
                "five": {"id": 201, "code": "c5001", "g": 5, "z": 4},
                "four": {"id": 202, "code": "c1017", "g": 4, "z": 3},
            },
        }

        class PacketSource:
            def __init__(self) -> None:
                self.started = False
                self.stopped = False

            def start(self) -> None:
                self.started = True

            def stop(self) -> None:
                self.stopped = True

            def captured_payloads(self):
                return [b"opaque-account-response"]

        def normalize(payloads):
            self.assertEqual(payloads, [b"opaque-account-response"])
            return normalize_account_inventory(
                account_data,
                hero_names={"c5001": "Adventurer Ras"},
            )[0]

        packet_source = PacketSource()
        with tempfile.TemporaryDirectory() as directory:
            service = OptimizerInventoryService(
                Path(directory),
                clock=lambda: IMPORTED_AT,
                import_id_factory=lambda: "packet-import-1",
                packet_source_factory=lambda: packet_source,
                inventory_normalizer=normalize,
                capture_directory=Path(directory) / "Documents",
            )

            state = service.start_game_inventory_capture()
            self.assertEqual(state, {"state": "capturing"})
            self.assertTrue(packet_source.started)
            self.assertFalse(packet_source.stopped)

            result = service.finish_game_inventory_capture()
            repository = service.database_path
            saved = Path(directory) / "Documents" / "MeowtokoE7Hub" / "gear.txt"

            self.assertEqual(result["inventory"]["totalItems"], 1)
            self.assertEqual(result["report"]["importedHeroCount"], 1)
            self.assertTrue(repository.exists())
            self.assertTrue(packet_source.stopped)
            self.assertEqual(json.loads(saved.read_text(encoding="utf-8"))["items"][0]["id"], "101")

    def test_packet_capture_uses_injected_inventory_normalizer(self) -> None:
        payloads = [b"opaque-captured-packet"]
        received = []

        class PacketSource:
            def start(self):
                return None

            def stop(self):
                return None

            def captured_payloads(self):
                return payloads

        def normalize(value):
            received.append(value)
            return {"items": [], "heroes": []}

        with tempfile.TemporaryDirectory() as directory:
            service = OptimizerInventoryService(
                Path(directory),
                clock=lambda: IMPORTED_AT,
                import_id_factory=lambda: "remote-packet-import",
                packet_source_factory=PacketSource,
                inventory_normalizer=normalize,
                capture_directory=Path(directory) / "Documents",
            )

            service.start_game_inventory_capture()
            result = service.finish_game_inventory_capture()

        self.assertEqual(received, [payloads])
        self.assertEqual(result["inventory"]["totalItems"], 0)

    def test_done_keeps_capture_open_for_late_packets(self) -> None:
        payloads = []

        class PacketSource:
            def start(self):
                return None

            def stop(self):
                return None

            def captured_payloads(self):
                return payloads

        def finish_grace(seconds):
            self.assertEqual(seconds, 1.0)
            payloads.append(b"late-account-response")

        with tempfile.TemporaryDirectory() as directory, patch(
            "src.desktop.optimizer_inventory_service.time.sleep",
            side_effect=finish_grace,
        ):
            service = OptimizerInventoryService(
                Path(directory),
                packet_source_factory=PacketSource,
                inventory_normalizer=lambda captured: {"items": [], "heroes": []}
                if captured == payloads else None,
                capture_directory=Path(directory) / "Documents",
            )
            service.start_game_inventory_capture()
            result = service.finish_game_inventory_capture()

        self.assertEqual(payloads, [b"late-account-response"])
        self.assertEqual(result["inventory"]["totalItems"], 0)

    def test_done_without_account_packet_closes_capture_for_a_fresh_retry(self) -> None:
        class PacketSource:
            def __init__(self) -> None:
                self.stopped = False

            def start(self) -> None:
                return None

            def stop(self) -> None:
                self.stopped = True

            def captured_payloads(self):
                return []

        source = PacketSource()
        with tempfile.TemporaryDirectory() as directory:
            service = OptimizerInventoryService(
                Path(directory) / "data",
                packet_source_factory=lambda: source,
                capture_directory=Path(directory) / "Documents",
            )
            service.start_game_inventory_capture()

            with self.assertRaisesRegex(
                OptimizerInventoryServiceError,
                "start a new capture",
            ) as raised:
                service.finish_game_inventory_capture()

            self.assertEqual(raised.exception.code, "account-packet-missing")
            self.assertTrue(source.stopped)
            self.assertEqual(service.start_game_inventory_capture(), {"state": "capturing"})

    def test_missing_account_message_distinguishes_capture_failures(self) -> None:
        class Source:
            def __init__(self, status):
                self.status = status

            def capture_status(self):
                return self.status

        cases = (
            (
                {"running": False, "packetsSeen": 0, "gamePacketsSeen": 0, "decodedMessages": 0},
                "stopped unexpectedly",
            ),
            (
                {
                    "running": True,
                    "packetsSeen": 50,
                    "gamePacketsSeen": 0,
                    "decodedMessages": 0,
                    "activeAdapters": 2,
                    "observedTcpSourcePorts": [
                        {"port": 443, "packets": 30},
                        {"port": 853, "packets": 5},
                    ],
                },
                "TCP payload source ports observed: 443 (30), 853 (5)",
            ),
            (
                {
                    "running": True,
                    "packetsSeen": 50,
                    "gamePacketsSeen": 3,
                    "decodedMessages": 0,
                },
                "no complete account snapshot",
            ),
        )
        for status, expected in cases:
            with self.subTest(expected=expected):
                self.assertIn(
                    expected,
                    OptimizerInventoryService._missing_account_message(Source(status)),
                )

    def test_fatal_document_and_wrong_extension_do_not_create_database(self) -> None:
        cases = (
            FIXTURES / "invalid-malformed-json.txt",
            FIXTURES / "valid-enriched-export-utf8.txt.json",
        )
        with tempfile.TemporaryDirectory() as directory:
            for index, source in enumerate(cases):
                service = self._service(Path(directory) / str(index))
                with self.assertRaises(OptimizerInventoryServiceError) as raised:
                    service.import_file(source)
                self.assertIn(raised.exception.category, {"document", "source-selection"})
                self.assertNotIn(str(source), str(raised.exception))
                self.assertFalse(service.database_path.exists())

    def test_reset_erases_only_optimizer_storage_and_returns_bounded_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            user_data = Path(directory)
            service = self._service(user_data)
            for name in ("optimizer.db", "optimizer.db-wal", "optimizer.db-shm"):
                (user_data / name).write_bytes(b"optimizer")
            for name, files in (
                ("optimizer_profiles", ("a.json", "b.json")),
                ("optimizer_results", ("run/manifest.json", "run/rows.bin")),
                ("optimizer_result_sort_cache", ("sort/index.bin",)),
            ):
                for relative in files:
                    path = user_data / name / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"optimizer")
            settings = user_data / "settings.json"
            settings.write_text('{"keep": true}', encoding="utf-8")

            result = service.reset_all_optimizer_data()

            self.assertEqual("cleared", result["state"])
            self.assertEqual("empty", result["inventory"]["state"])
            self.assertEqual({
                "databaseFiles": 3,
                "profileFiles": 2,
                "resultArtifacts": 3,
            }, result["removed"])
            self.assertEqual('{"keep": true}', settings.read_text(encoding="utf-8"))
            self.assertFalse(any(user_data.glob(".optimizer-reset-*.tmp")))
            for name in (
                "optimizer.db", "optimizer.db-wal", "optimizer.db-shm",
                "optimizer_profiles", "optimizer_results", "optimizer_result_sort_cache",
            ):
                self.assertFalse((user_data / name).exists())


class FakeOptimizerInventoryController:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.fail = False

    def get_snapshot(self):
        self.calls.append(("get", None))
        return {"state": "empty", "totalItems": 0}

    def import_file(self, source_path):
        self.calls.append(("import", source_path))
        if self.fail:
            raise OptimizerInventoryServiceError(
                "document",
                code="malformed-json",
                message="The selected document is not valid JSON.",
                document_path="$",
            )
        return {"inventory": {"state": "ready", "totalItems": 2}, "report": {"warningCount": 0}}

    def start_game_inventory_capture(self):
        self.calls.append(("capture-start", None))
        return {"state": "capturing"}

    def finish_game_inventory_capture(self):
        self.calls.append(("capture-finish", None))
        return {"inventory": {"state": "ready", "totalItems": 2}, "report": {"warningCount": 0}}

    def reset_all_optimizer_data(self):
        self.calls.append(("reset", None))
        return {
            "state": "cleared",
            "inventory": {"state": "empty", "totalItems": 0},
            "removed": {"databaseFiles": 1, "profileFiles": 2, "resultArtifacts": 3},
        }


class FakeResetController:
    def __init__(self, label: str, calls: list[str]) -> None:
        self.label = label
        self.calls = calls

    def reset_for_data_erasure(self) -> None:
        self.calls.append(self.label)


class OptimizerInventoryDesktopProtocolTests(unittest.TestCase):
    def test_status_and_import_map_to_only_the_typed_controller_operations(self) -> None:
        controller = FakeOptimizerInventoryController()
        status = dispatch_message(
            {
                "protocol": PROTOCOL_VERSION,
                "id": "inventory-get",
                "method": "optimizer.inventory.get",
                "params": {},
            },
            optimizer_inventory_controller=controller,
        )
        imported = dispatch_message(
            {
                "protocol": PROTOCOL_VERSION,
                "id": "inventory-import",
                "method": "optimizer.inventory.import",
                "params": {"sourcePath": "C:/private/gear.txt"},
            },
            optimizer_inventory_controller=controller,
        )
        captured = dispatch_message(
            {
                "protocol": PROTOCOL_VERSION,
                "id": "inventory-capture",
                "method": "optimizer.inventory.capture.start",
                "params": {},
            },
            optimizer_inventory_controller=controller,
        )
        finished = dispatch_message(
            {
                "protocol": PROTOCOL_VERSION,
                "id": "inventory-capture-finish",
                "method": "optimizer.inventory.capture.finish",
                "params": {},
            },
            optimizer_inventory_controller=controller,
        )

        self.assertTrue(status["ok"])
        self.assertTrue(imported["ok"])
        self.assertTrue(captured["ok"])
        self.assertTrue(finished["ok"])
        self.assertEqual(controller.calls, [
            ("get", None),
            ("import", "C:/private/gear.txt"),
            ("capture-start", None),
            ("capture-finish", None),
        ])

    def test_reset_coordinates_workers_before_erasing_storage(self) -> None:
        controller = FakeOptimizerInventoryController()
        coordination: list[str] = []
        response = dispatch_message(
            {
                "protocol": PROTOCOL_VERSION,
                "id": "inventory-reset",
                "method": "optimizer.inventory.reset",
                "params": {},
            },
            optimizer_inventory_controller=controller,
            optimizer_search_controller=FakeResetController("search", coordination),
            optimizer_result_controller=FakeResetController("results", coordination),
        )

        self.assertTrue(response["ok"])
        self.assertEqual(["search", "results"], coordination)
        self.assertEqual([("reset", None)], controller.calls)
        self.assertEqual("cleared", response["result"]["state"])

    def test_malformed_params_and_missing_service_are_rejected_before_work(self) -> None:
        controller = FakeOptimizerInventoryController()
        malformed = (
            {},
            {"sourcePath": ""},
            {"sourcePath": []},
            {"sourcePath": "gear.txt", "shellCommand": "anything"},
        )
        for index, params in enumerate(malformed):
            response = dispatch_message(
                {
                    "protocol": PROTOCOL_VERSION,
                    "id": f"bad-import-{index}",
                    "method": "optimizer.inventory.import",
                    "params": params,
                },
                optimizer_inventory_controller=controller,
            )
            self.assertEqual(response["error"]["code"], "invalid_params")
        unavailable = dispatch_message(
            {
                "protocol": PROTOCOL_VERSION,
                "id": "missing-service",
                "method": "optimizer.inventory.get",
                "params": {},
            }
        )
        self.assertEqual(unavailable["error"]["code"], "service_unavailable")
        self.assertEqual(controller.calls, [])

    def test_import_failure_is_structured_and_does_not_echo_the_source_path(self) -> None:
        controller = FakeOptimizerInventoryController()
        controller.fail = True
        source_path = "C:/private/player-name/gear.txt"

        response = dispatch_message(
            {
                "protocol": PROTOCOL_VERSION,
                "id": "failed-import",
                "method": "optimizer.inventory.import",
                "params": {"sourcePath": source_path},
            },
            optimizer_inventory_controller=controller,
        )

        self.assertEqual(response["error"]["code"], "optimizer_inventory_import_failed")
        self.assertEqual(response["error"]["data"], {
            "category": "document",
            "issueCode": "malformed-json",
            "documentPath": "$",
        })
        self.assertNotIn(source_path, json.dumps(response))

    def test_real_controller_keeps_its_service_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = OptimizerInventoryController(self._service_for_controller(Path(directory)))
            self.assertEqual(controller.get_snapshot()["state"], "empty")

    @staticmethod
    def _service_for_controller(path: Path) -> OptimizerInventoryService:
        return OptimizerInventoryService(
            path,
            clock=lambda: IMPORTED_AT,
            import_id_factory=lambda: "controller-import",
        )


if __name__ == "__main__":
    unittest.main()
