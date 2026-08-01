import io
import json
import unittest

from src.desktop import BACKEND_VERSION, PROTOCOL_VERSION
from src.desktop.protocol import dispatch_message, serve
from src.core.settings_service import SettingsConflictError, SettingsValidationError
from src.desktop.analyzer_controller import AnalyzerBusyError, AnalyzerJobNotFoundError
from src.desktop.analyzer_service import AnalyzerValidationError
from src.desktop.settings_preview import SettingsPreviewError


class FakeHealthController:
    def __init__(self):
        self.calls = []

    def get_snapshot(self):
        self.calls.append(("get", None))
        return {"overall": "degraded", "checkedAt": "now", "capabilities": []}

    def refresh(self):
        self.calls.append(("refresh", None))
        return {"overall": "checking", "checkedAt": "now", "capabilities": []}

    def run_action(self, action_id):
        if action_id not in {"ollama.start", "ollama.pull_model"}:
            raise ValueError(f"Unsupported health action: {action_id}")
        self.calls.append(("action", action_id))
        return {"overall": "checking", "checkedAt": "now", "capabilities": []}


class FakeSettingsController:
    def __init__(self):
        self.calls = []

    def get_snapshot(self):
        self.calls.append(("get", None))
        return {"schemaVersion": 1, "revision": "r1", "settings": {}}

    def update(self, revision, patch):
        self.calls.append(("update", revision, dict(patch)))
        if patch.get("targetWindow") == "invalid":
            raise SettingsValidationError("invalid", {"targetWindow": "bad"})
        if revision == "stale":
            raise SettingsConflictError("stale")
        return {"schemaVersion": 1, "revision": "r2", "settings": dict(patch)}

    def preview(self, settings, request):
        self.calls.append(("preview", dict(settings), dict(request)))
        if request.get("source") == "failed":
            raise SettingsPreviewError("capture failed")
        return {
            "source": request["source"],
            "kind": "region",
            "itemId": "slot",
            "label": "Capture region: slot",
            "width": 10,
            "height": 10,
            "dataUrl": "data:image/png;base64,AAAA",
        }


class FakeAnalyzerController:
    def __init__(self):
        self.calls = []
        self.busy = False

    def get_options(self):
        self.calls.append(("options", None))
        return {"slots": ["Weapon"]}

    def evaluate(self, piece):
        self.calls.append(("evaluate", dict(piece)))
        if piece.get("slot") == "invalid":
            raise AnalyzerValidationError("invalid", {"slot": "bad"})
        return {"piece": dict(piece), "archetypeText": "NO MATCH", "gearScoreText": "Final GS: 50"}

    def get_snapshot(self):
        self.calls.append(("get_scan", None))
        return {"state": "idle", "stage": "idle", "message": "Ready", "progress": 0.0}

    def start_scan(self):
        self.calls.append(("start", None))
        if self.busy:
            raise AnalyzerBusyError("busy")
        return {"jobId": "job-1", "state": "running", "stage": "capture", "message": "Capture", "progress": 0.1}

    def cancel_scan(self, job_id):
        self.calls.append(("cancel", job_id))
        if job_id == "missing":
            raise AnalyzerJobNotFoundError("missing")
        return {"jobId": job_id, "state": "cancelling", "stage": "capture", "message": "Cancelling", "progress": 0.1}

    def get_debug(self):
        self.calls.append(("debug", None))
        return {"available": False, "artifacts": []}

class DesktopProtocolTests(unittest.TestCase):
    def test_ping_returns_versioned_backend_details(self):
        response = dispatch_message(
            {
                "protocol": PROTOCOL_VERSION,
                "id": "ping-1",
                "method": "system.ping",
                "params": {},
            }
        )

        self.assertTrue(response["ok"])
        self.assertEqual(response["id"], "ping-1")
        self.assertEqual(response["protocol"], PROTOCOL_VERSION)
        self.assertEqual(response["result"]["protocolVersion"], PROTOCOL_VERSION)
        self.assertEqual(response["result"]["backendVersion"], BACKEND_VERSION)
        self.assertGreater(response["result"]["pid"], 0)
        self.assertRegex(response["result"]["pythonVersion"], r"^\d+\.\d+\.\d+")

    def test_rejects_incompatible_protocol(self):
        response = dispatch_message(
            {
                "protocol": PROTOCOL_VERSION + 1,
                "id": "old-client",
                "method": "system.ping",
                "params": {},
            }
        )

        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "incompatible_protocol")
        self.assertEqual(response["error"]["data"]["expected"], PROTOCOL_VERSION)

    def test_rejects_unknown_method_without_crashing(self):
        response = dispatch_message(
            {
                "protocol": PROTOCOL_VERSION,
                "id": "missing-method",
                "method": "unknown.action",
                "params": {},
            }
        )

        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "method_not_found")

    def test_stream_continues_after_unknown_method(self):
        unknown = json.dumps(
            {
                "protocol": PROTOCOL_VERSION,
                "id": "unknown-1",
                "method": "unknown.action",
                "params": {},
            }
        )
        ping = json.dumps(
            {
                "protocol": PROTOCOL_VERSION,
                "id": "ping-after-unknown",
                "method": "system.ping",
                "params": {},
            }
        )
        output_stream = io.StringIO()

        serve(io.StringIO(f"{unknown}\n{ping}\n"), output_stream)

        responses = [json.loads(line) for line in output_stream.getvalue().splitlines()]
        self.assertEqual(responses[0]["error"]["code"], "method_not_found")
        self.assertTrue(responses[1]["ok"])
        self.assertEqual(responses[1]["id"], "ping-after-unknown")

    def test_rejects_non_object_params(self):
        response = dispatch_message(
            {
                "protocol": PROTOCOL_VERSION,
                "id": "bad-params",
                "method": "system.ping",
                "params": [],
            }
        )

        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "invalid_params")

    def test_shutdown_returns_explicit_acknowledgement(self):
        response = dispatch_message(
            {
                "protocol": PROTOCOL_VERSION,
                "id": "shutdown-1",
                "method": "system.shutdown",
                "params": {},
            }
        )

        self.assertTrue(response["ok"])
        self.assertEqual(response["result"], {"accepted": True})

    def test_stream_stops_after_shutdown(self):
        shutdown = json.dumps(
            {
                "protocol": PROTOCOL_VERSION,
                "id": "shutdown-stream",
                "method": "system.shutdown",
                "params": {},
            }
        )
        ignored_ping = json.dumps(
            {
                "protocol": PROTOCOL_VERSION,
                "id": "must-not-run",
                "method": "system.ping",
                "params": {},
            }
        )
        output_stream = io.StringIO()

        serve(io.StringIO(f"{shutdown}\n{ignored_ping}\n"), output_stream)

        responses = [json.loads(line) for line in output_stream.getvalue().splitlines()]
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0]["id"], "shutdown-stream")
        self.assertTrue(responses[0]["result"]["accepted"])

    def test_health_methods_map_only_to_typed_controller_operations(self):
        controller = FakeHealthController()
        requests = [
            ("health.get", {}),
            ("health.refresh", {}),
            ("health.action", {"actionId": "ollama.start"}),
        ]

        responses = [
            dispatch_message(
                {
                    "protocol": PROTOCOL_VERSION,
                    "id": f"health-{index}",
                    "method": method,
                    "params": params,
                },
                controller,
            )
            for index, (method, params) in enumerate(requests)
        ]

        self.assertTrue(all(response["ok"] for response in responses))
        self.assertEqual(
            controller.calls,
            [("get", None), ("refresh", None), ("action", "ollama.start")],
        )

    def test_health_action_rejects_arbitrary_backend_method(self):
        response = dispatch_message(
            {
                "protocol": PROTOCOL_VERSION,
                "id": "health-invalid",
                "method": "health.action",
                "params": {"actionId": "shell.run_anything"},
            },
            FakeHealthController(),
        )

        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "invalid_params")

    def test_settings_methods_map_only_to_typed_controller_operations(self):
        controller = FakeSettingsController()
        get_response = dispatch_message(
            {"protocol": PROTOCOL_VERSION, "id": "settings-get", "method": "settings.get", "params": {}},
            settings_controller=controller,
        )
        update_response = dispatch_message(
            {
                "protocol": PROTOCOL_VERSION,
                "id": "settings-update",
                "method": "settings.update",
                "params": {"revision": "r1", "patch": {"targetWindow": "Test"}},
            },
            settings_controller=controller,
        )
        windows_response = dispatch_message(
            {"protocol": PROTOCOL_VERSION, "id": "settings-windows", "method": "settings.windows", "params": {}},
            settings_controller=controller,
        )
        preview_settings = {"targetWindow": "Epic Seven"}
        preview_request = {"source": "adb", "target": {"kind": "region", "id": "slot"}}
        preview_response = dispatch_message(
            {
                "protocol": PROTOCOL_VERSION,
                "id": "settings-preview",
                "method": "settings.preview",
                "params": {"settings": preview_settings, "request": preview_request},
            },
            settings_controller=controller,
        )

        self.assertTrue(get_response["ok"])
        self.assertTrue(update_response["ok"])
        self.assertFalse(windows_response["ok"])
        self.assertEqual(windows_response["error"]["code"], "method_not_found")
        self.assertTrue(preview_response["ok"])
        self.assertEqual(controller.calls, [
            ("get", None),
            ("update", "r1", {"targetWindow": "Test"}),
            ("preview", preview_settings, preview_request),
        ])

    def test_settings_validation_and_conflict_have_specific_error_codes(self):
        controller = FakeSettingsController()
        invalid = dispatch_message(
            {
                "protocol": PROTOCOL_VERSION,
                "id": "invalid-settings",
                "method": "settings.update",
                "params": {"revision": "r1", "patch": {"targetWindow": "invalid"}},
            },
            settings_controller=controller,
        )
        conflict = dispatch_message(
            {
                "protocol": PROTOCOL_VERSION,
                "id": "conflict-settings",
                "method": "settings.update",
                "params": {"revision": "stale", "patch": {"targetWindow": "Test"}},
            },
            settings_controller=controller,
        )

        self.assertEqual(invalid["error"]["code"], "settings_validation")
        self.assertEqual(invalid["error"]["data"]["issues"], {"targetWindow": "bad"})
        self.assertEqual(conflict["error"]["code"], "settings_conflict")

    def test_settings_update_rejects_untyped_params(self):
        response = dispatch_message(
            {
                "protocol": PROTOCOL_VERSION,
                "id": "bad-settings",
                "method": "settings.update",
                "params": {"revision": "r1", "patch": []},
            },
            settings_controller=FakeSettingsController(),
        )

        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "invalid_params")

        preview_response = dispatch_message(
            {
                "protocol": PROTOCOL_VERSION,
                "id": "bad-settings-preview",
                "method": "settings.preview",
                "params": {"settings": [], "request": {"source": "window"}},
            },
            settings_controller=FakeSettingsController(),
        )
        self.assertEqual(preview_response["error"]["code"], "invalid_params")

    def test_settings_preview_failure_has_specific_error_code(self):
        response = dispatch_message(
            {
                "protocol": PROTOCOL_VERSION,
                "id": "failed-settings-preview",
                "method": "settings.preview",
                "params": {
                    "settings": {"targetWindow": "Epic Seven"},
                    "request": {"source": "failed", "target": {"kind": "region", "id": "slot"}},
                },
            },
            settings_controller=FakeSettingsController(),
        )

        self.assertEqual(response["error"]["code"], "settings_preview_failed")

    def test_analyzer_methods_map_only_to_typed_controller_operations(self):
        controller = FakeAnalyzerController()
        requests = [
            ("analyzer.options", {}),
            ("analyzer.evaluate", {"piece": {"slot": "Weapon"}}),
            ("analyzer.scan.get", {}),
            ("analyzer.scan.start", {}),
            ("analyzer.scan.cancel", {"jobId": "job-1"}),
            ("analyzer.debug.get", {}),
        ]
        responses = [
            dispatch_message(
                {"protocol": PROTOCOL_VERSION, "id": f"analyzer-{index}", "method": method, "params": params},
                analyzer_controller=controller,
            )
            for index, (method, params) in enumerate(requests)
        ]

        self.assertTrue(all(response["ok"] for response in responses))
        self.assertEqual(controller.calls, [
            ("options", None),
            ("evaluate", {"slot": "Weapon"}),
            ("get_scan", None),
            ("start", None),
            ("cancel", "job-1"),
            ("debug", None),
        ])

    def test_analyzer_rejects_malformed_payloads_before_controller_code(self):
        controller = FakeAnalyzerController()
        malformed = [
            ("analyzer.evaluate", {"piece": []}),
            ("analyzer.scan.start", {"shellCommand": "anything"}),
            ("analyzer.scan.cancel", {"jobId": []}),
            ("analyzer.debug.get", {"path": "C:/private"}),
        ]
        responses = [
            dispatch_message(
                {"protocol": PROTOCOL_VERSION, "id": f"bad-analyzer-{index}", "method": method, "params": params},
                analyzer_controller=controller,
            )
            for index, (method, params) in enumerate(malformed)
        ]

        self.assertTrue(all(response["error"]["code"] == "invalid_params" for response in responses))
        self.assertEqual(controller.calls, [])

    def test_analyzer_validation_busy_and_missing_job_have_specific_codes(self):
        controller = FakeAnalyzerController()
        invalid = dispatch_message(
            {
                "protocol": PROTOCOL_VERSION,
                "id": "invalid-analyzer",
                "method": "analyzer.evaluate",
                "params": {"piece": {"slot": "invalid"}},
            },
            analyzer_controller=controller,
        )
        controller.busy = True
        busy = dispatch_message(
            {"protocol": PROTOCOL_VERSION, "id": "busy-analyzer", "method": "analyzer.scan.start", "params": {}},
            analyzer_controller=controller,
        )
        missing = dispatch_message(
            {
                "protocol": PROTOCOL_VERSION,
                "id": "missing-analyzer",
                "method": "analyzer.scan.cancel",
                "params": {"jobId": "missing"},
            },
            analyzer_controller=controller,
        )

        self.assertEqual(invalid["error"]["code"], "analyzer_validation")
        self.assertEqual(invalid["error"]["data"]["issues"], {"slot": "bad"})
        self.assertEqual(busy["error"]["code"], "analyzer_busy")
        self.assertEqual(missing["error"]["code"], "analyzer_job_not_found")

    def test_stream_recovers_after_malformed_json(self):
        valid = json.dumps(
            {
                "protocol": PROTOCOL_VERSION,
                "id": "after-error",
                "method": "system.ping",
                "params": {},
            }
        )
        input_stream = io.StringIO(f"not-json\n{valid}\n")
        output_stream = io.StringIO()

        serve(input_stream, output_stream)

        responses = [json.loads(line) for line in output_stream.getvalue().splitlines()]
        self.assertEqual(responses[0]["error"]["code"], "parse_error")
        self.assertTrue(responses[1]["ok"])
        self.assertEqual(responses[1]["id"], "after-error")


if __name__ == "__main__":
    unittest.main()
