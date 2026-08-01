import tempfile
import threading
import time
import unittest
from pathlib import Path

from src.core.gear_evaluator import build_gs_string, calculate_gear_score, evaluate_archetypes
from src.core.settings_service import SettingsService
from src.desktop.analyzer_controller import AnalyzerBusyError, AnalyzerController
from src.desktop.analyzer_service import (
    AnalyzerCancelledError,
    AnalyzerCaptureError,
    AnalyzerService,
    AnalyzerValidationError,
    validate_piece,
)


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


class AnalyzerServiceTests(unittest.TestCase):
    def test_options_expose_every_form_constraint(self):
        service = AnalyzerService(archetype_loader=lambda: [])
        options = service.get_options()

        self.assertEqual(options["enhancements"][0], "+0")
        self.assertEqual(options["enhancements"][-1], "+15")
        self.assertEqual(options["slotMainStats"]["Weapon"], ["Flat Attack"])
        self.assertIn("Defense", options["restrictedSubstats"]["Weapon"])
        self.assertEqual(options["autoDetectCapabilities"], ["tesseract", "ollama", "adb"])

    def test_manual_evaluation_matches_the_legacy_functions(self):
        archetypes = [{
            "name": "Fast bruiser",
            "needed_stats": ["Attack", "Health", "Speed", "Critical Hit Chance"],
            "priority_sets": ["Speed Set"],
        }]
        service = AnalyzerService(archetype_loader=lambda: archetypes)

        result = service.evaluate(PIECE)
        expected_archetypes = evaluate_archetypes(
            PIECE["slot"], PIECE["set"], PIECE["mainStat"],
            [item["stat"] for item in PIECE["substats"]], archetypes,
        )
        score_input = build_gs_string([
            {"stat": item["stat"], "val": item["value"]}
            for item in PIECE["substats"]
        ])

        self.assertEqual(result["archetypeText"], expected_archetypes)
        self.assertEqual(result["gearScoreText"], calculate_gear_score(score_input, "+9"))
        self.assertEqual(result["gearScore"]["rolls"], 3)

    def test_zero_values_preserve_the_legacy_numerical_error(self):
        piece = {**PIECE, "substats": [{**item, "value": "0"} for item in PIECE["substats"]]}
        result = AnalyzerService(archetype_loader=lambda: []).evaluate(piece)

        self.assertIsNone(result["gearScore"])
        self.assertEqual(result["gearScoreText"], "❌ Error: Could not extract numerical stats.")

    def test_validation_rejects_malformed_restricted_duplicate_and_non_numeric_fields(self):
        invalid_cases = [
            ({**PIECE, "shellCommand": "anything"}, "piece"),
            ({**PIECE, "mainStat": "Health"}, "mainStat"),
            ({**PIECE, "substats": [
                {"stat": "Defense", "value": "4"}, *PIECE["substats"][1:]
            ]}, "substats.0.stat"),
            ({**PIECE, "substats": [
                PIECE["substats"][0], {"stat": "Attack", "value": "4"}, *PIECE["substats"][2:]
            ]}, "substats.1.stat"),
            ({**PIECE, "substats": [
                {"stat": "Attack", "value": "4.5"}, *PIECE["substats"][1:]
            ]}, "substats.0.value"),
        ]
        for piece, issue in invalid_cases:
            with self.subTest(issue=issue), self.assertRaises(AnalyzerValidationError) as raised:
                validate_piece(piece)
            self.assertIn(issue, raised.exception.issues)

    def test_controlled_scan_uses_settings_and_isolated_artifact_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = SettingsService(root / "settings.json")
            captures = []
            progress = []

            def capture_regions(settings_document, regions, _cancel_check):
                captures.append((dict(settings_document["adb"]), {name: dict(region) for name, region in regions.items()}))
                return {name: object() for name in regions}

            def scan_engine(images, **kwargs):
                debug_dir = Path(kwargs["debug_dir"])
                debug_dir.mkdir(parents=True)
                (debug_dir / "raw_crop_slot.png").write_bytes(b"fixture")
                kwargs["on_progress"]("verification", "Fixture verification", 0.5)
                return ({
                    "enhance": "+6",
                    "slot": "Weapon",
                    "set": "Speed Set",
                    "main_stat": "Flat Attack",
                    "subs": [
                        {"stat": "Attack", "val": "8"},
                        {"stat": "Health", "val": "7"},
                        {"stat": "Speed", "val": "4"},
                        {"stat": "Critical Hit Chance", "val": "5"},
                    ],
                }, "structured fixture debug")

            service = AnalyzerService(
                settings,
                user_data_dir=root,
                capture_regions=capture_regions,
                scan_engine=scan_engine,
                archetype_loader=lambda: [],
            )
            result, debug = service.scan("job-fixture", lambda: False, lambda *args: progress.append(args))

            self.assertEqual(len(captures), 1)
            self.assertEqual(set(captures[0][1]), {"enhance", "slot", "main_stat", "set", "subs"})
            self.assertEqual(captures[0][0]["coordinate_width"], 1280)
            self.assertEqual(result["piece"]["enhancement"], "+6")
            self.assertEqual(debug["text"], "structured fixture debug")
            self.assertEqual(debug["artifacts"], ["raw_crop_slot.png"])
            self.assertTrue((root / "debug_images" / "analyzer" / "job-fixture").is_dir())
            self.assertTrue(any(stage == "capture" for stage, _message, _value in progress))

    def test_capture_failure_stops_before_ocr(self):
        with tempfile.TemporaryDirectory() as temporary:
            scan_called = False

            def scan_engine(*_args, **_kwargs):
                nonlocal scan_called
                scan_called = True
                return {}, ""

            service = AnalyzerService(
                SettingsService(Path(temporary) / "settings.json"),
                user_data_dir=temporary,
                capture_regions=lambda _settings, _regions, _cancel_check: {},
                scan_engine=scan_engine,
            )
            with self.assertRaises(AnalyzerCaptureError):
                service.scan("capture-failure", lambda: False, lambda *_args: None)
            self.assertFalse(scan_called)


class FakeSuccessfulAnalyzerService:
    def get_options(self):
        return {"slots": ["Weapon"]}

    def evaluate(self, piece):
        return {"piece": dict(piece)}

    def scan(self, job_id, _cancel_check, progress):
        progress("capture", "Captured fixture", 0.25)
        return ({"piece": PIECE, "evaluation": {"piece": PIECE}, "debugAvailable": True}, {
            "available": True,
            "jobId": job_id,
            "text": "debug",
            "artifacts": [],
        })


class BlockingAnalyzerService(FakeSuccessfulAnalyzerService):
    def __init__(self, *, ignore_cancel=False):
        self.started = threading.Event()
        self.release = threading.Event()
        self.ignore_cancel = ignore_cancel

    def scan(self, job_id, cancel_check, progress):
        self.started.set()
        progress("ocr", "Waiting fixture", 0.5)
        while not self.release.wait(0.005):
            if cancel_check() and not self.ignore_cancel:
                raise AnalyzerCancelledError("cancelled")
        return super().scan(job_id, cancel_check, progress)


class AnalyzerControllerTests(unittest.TestCase):
    def wait_for_state(self, controller, state):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            snapshot = controller.get_snapshot()
            if snapshot["state"] == state:
                return snapshot
            time.sleep(0.005)
        self.fail(f"Analyzer did not reach {state}: {controller.get_snapshot()}")

    def test_success_emits_progress_result_and_structured_debug(self):
        events = []
        controller = AnalyzerController(FakeSuccessfulAnalyzerService(), events.append)
        started = controller.start_scan()
        completed = self.wait_for_state(controller, "succeeded")

        self.assertEqual(events[0]["state"], "running")
        self.assertTrue(any(event["stage"] == "capture" for event in events))
        self.assertEqual(completed["result"]["piece"], PIECE)
        self.assertEqual(controller.get_debug()["jobId"], started["jobId"])

    def test_cancel_is_cooperative_and_late_success_is_suppressed(self):
        service = BlockingAnalyzerService(ignore_cancel=True)
        events = []
        controller = AnalyzerController(service, events.append)
        started = controller.start_scan()
        self.assertTrue(service.started.wait(1))

        cancelling = controller.cancel_scan(started["jobId"])
        self.assertEqual(cancelling["state"], "cancelling")
        service.release.set()
        final = self.wait_for_state(controller, "cancelled")

        self.assertEqual(final["state"], "cancelled")
        self.assertFalse(any(event["state"] == "succeeded" for event in events))
        self.assertFalse(controller.get_debug()["available"])

    def test_only_one_scan_can_run_at_a_time(self):
        service = BlockingAnalyzerService()
        controller = AnalyzerController(service)
        started = controller.start_scan()
        self.assertTrue(service.started.wait(1))
        with self.assertRaises(AnalyzerBusyError):
            controller.start_scan()
        controller.cancel_scan(started["jobId"])
        service.release.set()
        self.wait_for_state(controller, "cancelled")


if __name__ == "__main__":
    unittest.main()
