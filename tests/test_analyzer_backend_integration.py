import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src.desktop import PROTOCOL_VERSION


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


class BackendSession:
    def __init__(self, user_data_dir):
        environment = dict(os.environ)
        environment["E7_USER_DATA_DIR"] = str(user_data_dir)
        self.process = subprocess.Popen(
            [sys.executable, "-u", "-m", "src.desktop.backend"],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
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


class AnalyzerBackendIntegrationTests(unittest.TestCase):
    def test_manual_analyzer_works_through_complete_backend_without_ocr_dependencies(self):
        with tempfile.TemporaryDirectory() as temporary:
            session = BackendSession(temporary)
            try:
                options = session.request("options", "analyzer.options")
                evaluation = session.request("evaluate", "analyzer.evaluate", {"piece": PIECE})
                scan = session.request("scan", "analyzer.scan.get")
                debug = session.request("debug", "analyzer.debug.get")
                enhancement_options = session.request("enhancement-options", "enhancement.options")
                enhancement_job = session.request("enhancement-job", "enhancement.job.get")
                enhancement_debug = session.request("enhancement-debug", "enhancement.debug.get")
            finally:
                session.stop()

        self.assertTrue(options["ok"])
        self.assertEqual(options["result"]["enhancements"][-1], "+15")
        self.assertTrue(evaluation["ok"])
        self.assertEqual(evaluation["result"]["piece"], PIECE)
        self.assertIn("Current GS", evaluation["result"]["gearScoreText"])
        self.assertEqual(scan["result"]["state"], "idle")
        self.assertEqual(debug["result"], {"available": False, "artifacts": []})
        self.assertEqual([mode["id"] for mode in enhancement_options["result"]["modes"]], ["adb"])
        self.assertEqual(enhancement_job["result"]["state"], "idle")
        self.assertEqual(enhancement_debug["result"], {"available": False, "artifacts": []})


if __name__ == "__main__":
    unittest.main()
