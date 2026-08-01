import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src.desktop import PROTOCOL_VERSION


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "fribbels"


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
        message = {
            "protocol": PROTOCOL_VERSION,
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()
        while True:
            line = self.process.stdout.readline()
            if not line:
                raise AssertionError(
                    f"backend exited before responding: {self.process.stderr.read()}"
                )
            response = json.loads(line)
            if response.get("id") == request_id:
                return response

    def stop(self) -> str:
        if self.process.poll() is None:
            response = self.request("shutdown", "system.shutdown")
            if not response.get("ok"):
                raise AssertionError(response)
        self.process.wait(timeout=5)
        stderr = self.process.stderr.read()
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()
        return stderr


class OptimizerInventoryBackendIntegrationTests(unittest.TestCase):
    def test_empty_status_and_successful_import_cross_the_complete_backend_privately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            user_data = root / "user-data"
            source = root / "private-player-name" / "gear.txt"
            source.parent.mkdir()
            source.write_bytes((FIXTURES / "valid-enriched-export-utf8.txt").read_bytes())
            backend = BackendSession(user_data)
            try:
                empty = backend.request("empty", "optimizer.inventory.get")
                imported = backend.request(
                    "import",
                    "optimizer.inventory.import",
                    {"sourcePath": str(source)},
                )
                reloaded = backend.request("reloaded", "optimizer.inventory.get")
            finally:
                stderr = backend.stop()

            self.assertTrue(empty["ok"])
            self.assertEqual(empty["result"]["state"], "empty")
            self.assertTrue(imported["ok"])
            self.assertEqual(imported["result"]["inventory"]["state"], "ready")
            self.assertEqual(imported["result"]["inventory"]["totalItems"], 2)
            self.assertEqual(imported["result"]["report"]["warningCount"], 0)
            self.assertEqual(reloaded["result"], imported["result"]["inventory"])
            self.assertTrue((user_data / "optimizer.db").exists())
            public_output = json.dumps([empty, imported, reloaded]) + stderr
            self.assertNotIn(str(source), public_output)
            self.assertNotIn("private-player-name", public_output)

    def test_recoverable_warning_commits_and_returns_only_structural_issue_data(self) -> None:
        payload = json.loads((FIXTURES / "valid-enriched-export-utf8.txt").read_text(encoding="utf-8"))
        payload["items"][0]["locked"] = "private-invalid-value"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "warning-gear.txt"
            source.write_text(json.dumps(payload), encoding="utf-8")
            backend = BackendSession(root / "user-data")
            try:
                response = backend.request(
                    "warning",
                    "optimizer.inventory.import",
                    {"sourcePath": str(source)},
                )
            finally:
                stderr = backend.stop()

            self.assertTrue(response["ok"])
            self.assertEqual(response["result"]["report"]["warningCount"], 1)
            self.assertEqual(response["result"]["report"]["issues"][0]["kind"], "warning")
            public_output = json.dumps(response) + stderr
            self.assertNotIn("private-invalid-value", public_output)
            self.assertNotIn(str(source), public_output)

    def test_fatal_import_failure_returns_actionable_error_without_creating_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            user_data = root / "user-data"
            source = root / "private-player-name" / "gear.txt"
            source.parent.mkdir()
            source.write_text("{not-json", encoding="utf-8")
            backend = BackendSession(user_data)
            try:
                failed = backend.request(
                    "failed",
                    "optimizer.inventory.import",
                    {"sourcePath": str(source)},
                )
                status = backend.request("status", "optimizer.inventory.get")
            finally:
                stderr = backend.stop()

            self.assertFalse(failed["ok"])
            self.assertEqual(failed["error"]["code"], "optimizer_inventory_import_failed")
            self.assertEqual(failed["error"]["data"]["category"], "document")
            self.assertEqual(status["result"]["state"], "empty")
            self.assertFalse((user_data / "optimizer.db").exists())
            public_output = json.dumps([failed, status]) + stderr
            self.assertNotIn(str(source), public_output)
            self.assertNotIn("private-player-name", public_output)

    def test_full_reset_erases_inventory_profiles_results_and_keeps_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            user_data = root / "user-data"
            source = root / "gear.txt"
            source.write_bytes((FIXTURES / "valid-enriched-export-utf8.txt").read_bytes())
            backend = BackendSession(user_data)
            try:
                imported = backend.request(
                    "import",
                    "optimizer.inventory.import",
                    {"sourcePath": str(source)},
                )
                profile_dir = user_data / "optimizer_profiles"
                result_dir = user_data / "optimizer_results" / "old-run"
                cache_dir = user_data / "optimizer_result_sort_cache"
                profile_dir.mkdir()
                result_dir.mkdir(parents=True)
                cache_dir.mkdir()
                (profile_dir / "hero.json").write_text("{}", encoding="utf-8")
                (result_dir / "rows.bin").write_bytes(b"rows")
                (cache_dir / "sort.bin").write_bytes(b"sort")
                settings = user_data / "settings.json"
                settings.write_text('{"keep": true}', encoding="utf-8")

                reset = backend.request("reset", "optimizer.inventory.reset")
                reloaded = backend.request("reloaded", "optimizer.inventory.get")
            finally:
                stderr = backend.stop()

            self.assertTrue(imported["ok"])
            self.assertTrue(reset["ok"], reset)
            self.assertEqual("cleared", reset["result"]["state"])
            self.assertEqual("empty", reset["result"]["inventory"]["state"])
            self.assertEqual(1, reset["result"]["removed"]["profileFiles"])
            self.assertEqual(2, reset["result"]["removed"]["resultArtifacts"])
            self.assertEqual("empty", reloaded["result"]["state"])
            self.assertEqual('{"keep": true}', settings.read_text(encoding="utf-8"))
            self.assertFalse((user_data / "optimizer.db").exists())
            self.assertFalse(profile_dir.exists())
            self.assertFalse(result_dir.parent.exists())
            self.assertFalse(cache_dir.exists())
            self.assertNotIn(str(source), json.dumps([reset, reloaded]) + stderr)


if __name__ == "__main__":
    unittest.main()
