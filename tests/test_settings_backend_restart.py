import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src.desktop import PROTOCOL_VERSION


class BackendSession:
    def __init__(self, user_data_dir):
        environment = dict(os.environ)
        environment["E7_USER_DATA_DIR"] = str(user_data_dir)
        self.process = subprocess.Popen(
            [sys.executable, "-u", "-m", "src.desktop.backend"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
        )

    def request(self, request_id, method, params=None):
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
                raise AssertionError(f"backend exited before responding: {self.process.stderr.read()}")
            response = json.loads(line)
            if response.get("id") == request_id:
                return response

    def stop(self):
        if self.process.poll() is None:
            response = self.request("shutdown", "system.shutdown")
            if not response.get("ok"):
                raise AssertionError(response)
        self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()


class SettingsBackendRestartTests(unittest.TestCase):
    def test_settings_survive_a_complete_backend_restart_in_isolated_user_data(self):
        with tempfile.TemporaryDirectory() as directory:
            isolated_user_data = Path(directory) / "user_data"
            live_path = Path(".local/user-data/settings.json")
            live_before = live_path.read_bytes() if live_path.exists() else None

            first = BackendSession(isolated_user_data)
            initial = first.request("get-1", "settings.get")["result"]
            saved = first.request(
                "save-1",
                "settings.update",
                {
                    "revision": initial["revision"],
                    "patch": {
                        "targetWindow": "Isolated Restart Window",
                        "appearance": {"theme": "dark"},
                    },
                },
            )["result"]
            first.stop()

            second = BackendSession(isolated_user_data)
            restarted = second.request("get-2", "settings.get")["result"]
            second.stop()

            self.assertNotEqual(saved["revision"], initial["revision"])
            self.assertEqual(restarted["revision"], saved["revision"])
            self.assertEqual(restarted["settings"]["targetWindow"], "Isolated Restart Window")
            self.assertEqual(restarted["settings"]["appearance"]["theme"], "dark")
            self.assertEqual(
                live_path.read_bytes() if live_path.exists() else None,
                live_before,
                "isolated restart test must not modify live user settings",
            )


if __name__ == "__main__":
    unittest.main()
