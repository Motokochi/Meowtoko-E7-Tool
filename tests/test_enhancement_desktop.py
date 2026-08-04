import copy
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.core.enhancement_packets import EnhancementPacket
from src.core.settings_service import default_settings
from src.desktop.enhancement_controller import (
    EnhancementBusyError,
    EnhancementController,
    EnhancementJobNotFoundError,
)
from src.desktop.enhancement_service import (
    EnhancementCancelledError,
    EnhancementService,
    EnhancementValidationError,
    validate_start_options,
)
from src.desktop.protocol import dispatch_message
from src.optimizer.data import (
    FribbelsImportRequest,
    FribbelsImportService,
    InventoryRepository,
)


def configured_settings():
    settings = default_settings()
    for key in settings["automation"]:
        settings["automation"][key] = 0
    return settings


class FakeSettingsService:
    def __init__(self, document):
        self.document = document
        self.path = Path("unused/settings.json")

    def load(self):
        return SimpleNamespace(document=copy.deepcopy(self.document))


class FakeBackend:
    def __init__(self, name="ADB", cancel=None):
        self.name = name
        self.clicks = []
        self.cancel = cancel

    def capture_regions(self, regions):
        if self.cancel:
            self.cancel.set()
        return {name: object() for name in regions}

    def click_point(self, point):
        self.clicks.append(copy.deepcopy(point))
        if self.cancel:
            self.cancel.set()


def packet(rolls=("speed",), item_id="gear-1"):
    operations = [
        ["att_rate", 0.10],
        ["speed", 8],
        ["cri", 0.10],
        ["cri_dmg", 0.14],
        ["att_rate", 0.18],
    ]
    operations.extend([
        [roll, 1 if roll == "speed" else 0.01]
        for roll in rolls
    ])
    return EnhancementPacket.from_message({
        "equip": item_id,
        "op": operations,
    })


class FakePacketSource:
    def __init__(self, events, cancel=None):
        self.events = list(events)
        self.cancel = cancel
        self.stopped = False

    def start(self):
        return None

    def stop(self):
        self.stopped = True

    def wait_for_enhancement(self, **_kwargs):
        if self.cancel:
            self.cancel.set()
        return self.events.pop(0)


class EnhancementValidationTests(unittest.TestCase):
    def test_validates_modes_boolean_limits_and_unknown_fields(self):
        self.assertEqual(
            validate_start_options({"mode": "adb", "allowDestroy": False, "maxPieces": None}),
            {"mode": "adb", "allowDestroy": False, "maxPieces": None},
        )
        for payload in (
            {"mode": "window", "allowDestroy": False, "maxPieces": 1},
            {"mode": "shell", "allowDestroy": False, "maxPieces": 1},
            {"mode": "adb", "allowDestroy": 1, "maxPieces": 1},
            {"mode": "adb", "allowDestroy": False, "maxPieces": 0},
            {"mode": "adb", "allowDestroy": False, "maxPieces": True},
            {"mode": "adb", "allowDestroy": False, "maxPieces": 1, "command": "tap"},
        ):
            with self.subTest(payload=payload), self.assertRaises(EnhancementValidationError):
                validate_start_options(payload)


class EnhancementServiceTests(unittest.TestCase):
    def make_service(self, directory, backend, events, cancel=None):
        def normalize_packet(captured, _enhancement, initial_substats):
            rolls = captured.enhancement_rolls(initial_substats)
            return {
                "enhancementRollStats": [roll.stat_code for roll in rolls],
                "parsedCheckpoints": [
                    captured.parsed_gear_at(index * 3, initial_substats)
                    for index in range(1, len(rolls) + 1)
                ],
            }

        return EnhancementService(
            FakeSettingsService(configured_settings()),
            user_data_dir=directory,
            backend_factories={"adb": lambda _settings: backend},
            packet_source_factory=lambda: FakePacketSource(events, cancel),
            item_metadata_resolver=lambda _item_id: {
                "set": "Speed Set",
                "setId": "set.speed",
                "slotId": "slot.weapon",
                "mainStatId": "item_stat.flat_attack",
                "enhance": 0,
                "initialSubstats": 4,
            },
            enhancement_normalizer=normalize_packet,
        )

    @staticmethod
    def full_events():
        return [
            packet(("speed",)),
            packet(("speed", "speed")),
            packet(("speed", "speed", "speed")),
            packet(("speed", "speed", "speed", "cri")),
            packet(("speed", "speed", "speed", "cri", "speed")),
        ]

    def test_adb_lock_uses_exact_configured_point_after_five_packet_events(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend()
            service = self.make_service(directory, backend, self.full_events())
            options, settings = service.prepare({"mode": "adb", "allowDestroy": False, "maxPieces": 1})
            result, debug = service.run("job", options, settings, lambda: False, lambda *_args: None, lambda _msg: None)
            self.assertEqual(backend.clicks[-1], settings["click_points"]["lock"])
            self.assertEqual(result["processedPieces"], 1)
            self.assertTrue(debug["available"])
            self.assertEqual(debug["artifacts"], ["latest_enhancement_packet.json"])
            self.assertNotIn(str(Path(directory).resolve()), json.dumps(debug))

    def test_destroy_disabled_never_clicks_destroy(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend()
            service = self.make_service(
                directory,
                backend,
                [
                    packet(("speed",)),
                    packet(("speed", "cri")),
                    packet(("speed", "cri", "acc")),
                    packet(("speed", "cri", "acc", "cri_dmg")),
                    packet(("speed", "cri", "acc", "cri_dmg", "att_rate")),
                ],
            )
            options, settings = service.prepare({"mode": "adb", "allowDestroy": False, "maxPieces": 1})
            result, _debug = service.run("job", options, settings, lambda: False, lambda *_args: None, lambda _msg: None)
            self.assertEqual(result["outcome"], "completed")
            self.assertNotIn(settings["click_points"]["destroy"], backend.clicks)

    def test_packet_driven_lock_never_clicks_destroy(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend()
            service = self.make_service(directory, backend, self.full_events())
            options, settings = service.prepare({"mode": "adb", "allowDestroy": True, "maxPieces": 1})
            service.run("job", options, settings, lambda: False, lambda *_args: None, lambda _msg: None)
            self.assertNotIn(settings["click_points"]["destroy"], backend.clicks)

    def test_final_enhancement_reward_popup_is_closed_before_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend()
            service = self.make_service(directory, backend, self.full_events())
            options, settings = service.prepare({"mode": "adb", "allowDestroy": False, "maxPieces": 1})
            service.run("job", options, settings, lambda: False, lambda *_args: None, lambda _msg: None)
            lock = settings["click_points"]["lock"]
            self.assertEqual(backend.clicks[-2:], [
                {"x": lock["x"], "y": lock["y"] - 100},
                lock,
            ])

    def test_piece_limit_advances_once_and_stops_after_second_piece(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend()
            service = self.make_service(directory, backend, self.full_events() * 2)
            options, settings = service.prepare({"mode": "adb", "allowDestroy": False, "maxPieces": 2})
            result, _debug = service.run("job", options, settings, lambda: False, lambda *_args: None, lambda _msg: None)
            points = settings["click_points"]
            first_lock = backend.clicks.index(points["lock"])
            self.assertEqual(
                backend.clicks[first_lock:first_lock + 4],
                [points["lock"], points["back"], points["next_piece"], points["open_enhance"]],
            )
            self.assertEqual(backend.clicks[-1], points["lock"])
            self.assertEqual(result["processedPieces"], 2)

    def test_cancellation_before_capture_prevents_every_click(self):
        with tempfile.TemporaryDirectory() as directory:
            cancelled = threading.Event()
            backend = FakeBackend(cancel=cancelled)
            service = self.make_service(directory, backend, self.full_events())
            options, settings = service.prepare({"mode": "adb", "allowDestroy": True, "maxPieces": 1})
            cancelled.set()
            with self.assertRaises(EnhancementCancelledError):
                service.run("job", options, settings, cancelled.is_set, lambda *_args: None, lambda _msg: None)
            self.assertEqual(backend.clicks, [])

    def test_cancellation_between_clicks_stops_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            cancelled = threading.Event()
            backend = FakeBackend(cancel=cancelled)
            service = self.make_service(directory, backend, self.full_events())
            options, settings = service.prepare({"mode": "adb", "allowDestroy": True, "maxPieces": 1})
            with self.assertRaises(EnhancementCancelledError):
                service.run("job", options, settings, cancelled.is_set, lambda *_args: None, lambda _msg: None)
            self.assertEqual(backend.clicks, [settings["click_points"]["probe_ingredient"]])

    def test_prepare_snapshots_settings(self):
        source = configured_settings()
        service = EnhancementService(FakeSettingsService(source), backend_factories={})
        _options, snapshot = service.prepare({"mode": "adb", "allowDestroy": False, "maxPieces": 1})
        source["target_window"] = "Changed"
        self.assertEqual(snapshot["target_window"], "Epic Seven")

    def test_real_inventory_lookup_returns_imported_enhancement_rank_and_set(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "gear.txt"
            source.write_text(
                json.dumps({
                    "items": [{
                        "ingameId": "packet-item-42",
                        "gear": "Weapon",
                        "rank": "Heroic",
                        "set": "SpeedSet",
                        "enhance": 6,
                        "level": 85,
                        "main": {"type": "Attack", "value": 500},
                        "substats": [
                            {"type": "Speed", "value": 7},
                            {"type": "CriticalHitChancePercent", "value": 5},
                            {"type": "HealthPercent", "value": 8},
                            {"type": "EffectivenessPercent", "value": 6},
                        ],
                    }],
                    "heroes": [],
                }),
                encoding="utf-8",
            )
            FribbelsImportService(
                InventoryRepository(root / "optimizer.db")
            ).import_file(
                FribbelsImportRequest(
                    source_path=source,
                    import_id="enhancer-lookup-test",
                    imported_at="2026-07-30T00:00:00Z",
                    privacy_safe_source_metadata={"sourceKind": "test"},
                )
            )
            service = EnhancementService(
                FakeSettingsService(configured_settings()),
                user_data_dir=root,
            )

            self.assertEqual(
                service._resolve_item_metadata("packet-item-42"),
                {
                    "set": "Speed Set",
                    "setId": "set.speed",
                    "slotId": "slot.weapon",
                    "mainStatId": "item_stat.flat_attack",
                    "enhance": 6,
                    "initialSubstats": 3,
                },
            )


class BlockingService:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def get_options(self):
        return {"modes": [], "maxRetainedLogs": 20}

    def prepare(self, options):
        return dict(options), {"snapshot": True}

    def run(self, job_id, options, settings, cancel_check, on_progress, on_log):
        self.started.set()
        on_log("one")
        on_progress("capture", "Capturing…", 0.2, 1, None)
        while not self.release.wait(0.005):
            if cancel_check():
                raise EnhancementCancelledError("cancelled")
        if cancel_check():
            raise EnhancementCancelledError("cancelled")
        return ({"outcome": "completed", "processedPieces": 1, "currentPiece": 1,
                 "lastDecision": None, "debugAvailable": False},
                {"available": False, "artifacts": []})


class EnhancementControllerTests(unittest.TestCase):
    def test_progress_success_and_one_job_ownership(self):
        service = BlockingService()
        events = []
        controller = EnhancementController(service, events.append)
        started = controller.start({"mode": "adb", "allowDestroy": False, "maxPieces": 1})
        self.assertTrue(service.started.wait(1))
        with self.assertRaises(EnhancementBusyError):
            controller.start({"mode": "adb", "allowDestroy": False, "maxPieces": 1})
        service.release.set()
        deadline = time.time() + 1
        while controller.get_snapshot()["state"] == "running" and time.time() < deadline:
            time.sleep(0.005)
        snapshot = controller.get_snapshot()
        self.assertEqual(snapshot["state"], "succeeded")
        self.assertEqual(snapshot["logs"], ["one"])
        self.assertEqual(events[0]["jobId"], started["jobId"])
        self.assertEqual(events[0]["stage"], "starting")

    def test_cancel_is_idempotent_and_suppresses_late_success(self):
        service = BlockingService()
        controller = EnhancementController(service)
        started = controller.start({"mode": "adb", "allowDestroy": False, "maxPieces": None})
        self.assertTrue(service.started.wait(1))
        first = controller.cancel(started["jobId"])
        second = controller.cancel(started["jobId"])
        self.assertEqual(first["state"], "cancelling")
        self.assertEqual(second["state"], "cancelling")
        deadline = time.time() + 1
        while controller.get_snapshot()["state"] == "cancelling" and time.time() < deadline:
            time.sleep(0.005)
        self.assertEqual(controller.get_snapshot()["state"], "cancelled")
        service.release.set()
        time.sleep(0.02)
        self.assertEqual(controller.get_snapshot()["state"], "cancelled")
        with self.assertRaises(EnhancementJobNotFoundError):
            controller.cancel("wrong")

    def test_retained_logs_are_bounded(self):
        service = BlockingService()
        controller = EnhancementController(service, max_logs=10)
        started = controller.start({"mode": "adb", "allowDestroy": False, "maxPieces": None})
        self.assertTrue(service.started.wait(1))
        for index in range(15):
            controller._log(started["jobId"], f"line-{index}")
        logs = controller.get_snapshot()["logs"]
        self.assertEqual(len(logs), 10)
        self.assertEqual(logs[0], "line-5")
        controller.cancel(started["jobId"])


class FakeProtocolEnhancementController:
    def get_options(self): return {"modes": [], "maxRetainedLogs": 200}
    def get_snapshot(self): return {"state": "idle"}
    def get_debug(self): return {"available": False, "artifacts": []}
    def start(self, options):
        validate_start_options(options)
        return {"state": "running", "jobId": "job"}
    def cancel(self, job_id): return {"state": "cancelling", "jobId": job_id}


class EnhancementProtocolTests(unittest.TestCase):
    def request(self, method, params):
        return dispatch_message(
            {"protocol": 1, "id": "request", "method": method, "params": params},
            enhancement_controller=FakeProtocolEnhancementController(),
        )

    def test_typed_methods_and_validation_error(self):
        self.assertTrue(self.request("enhancement.options", {})["ok"])
        self.assertTrue(self.request("enhancement.job.get", {})["ok"])
        response = self.request("enhancement.job.start", {
            "options": {"mode": "adb", "allowDestroy": False, "maxPieces": 1},
        })
        self.assertTrue(response["ok"])
        invalid = self.request("enhancement.job.start", {
            "options": {"mode": "shell", "allowDestroy": False, "maxPieces": 1},
        })
        self.assertEqual(invalid["error"]["code"], "enhancement_validation")

    def test_arbitrary_fields_and_operations_are_rejected(self):
        extra = self.request("enhancement.job.start", {
            "options": {"mode": "adb", "allowDestroy": False, "maxPieces": 1},
            "command": "click",
        })
        self.assertEqual(extra["error"]["code"], "invalid_params")
        unknown = self.request("enhancement.click", {"x": 1, "y": 2})
        self.assertEqual(unknown["error"]["code"], "method_not_found")


if __name__ == "__main__":
    unittest.main()
