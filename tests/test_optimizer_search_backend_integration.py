from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from src.desktop import PROTOCOL_VERSION
from src.optimizer.cuda.runtime import CUDA_DISABLE_ENV_VAR
from src.optimizer.domain import GEAR_SLOT_ORDER, GearSet
from tests.test_cpu_orchestration import _gear_row


ROOT = Path(__file__).resolve().parents[1]


class BackendSession:
    def __init__(self, user_data_dir: Path):
        environment = dict(os.environ)
        environment["E7_USER_DATA_DIR"] = str(user_data_dir)
        environment[CUDA_DISABLE_ENV_VAR] = "1"
        self.events: list[dict] = []
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
            if "event" in response:
                self.events.append(response)
            if response.get("id") == request_id:
                return response

    def wait_for_search(self, timeout: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout
        attempt = 0
        while time.monotonic() < deadline:
            response = self.request(f"search-get-{attempt}", "optimizer.search.get")
            if response["result"]["state"] in {"completed", "overflowed", "cancelled", "failed"}:
                return response
            attempt += 1
            time.sleep(0.01)
        raise AssertionError("optimizer search did not finish")

    def stop(self) -> str:
        if self.process.poll() is None:
            response = self.request("shutdown", "system.shutdown")
            if not response.get("ok"):
                raise AssertionError(response)
        self.process.wait(timeout=10)
        stderr = self.process.stderr.read()
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()
        return stderr


def _write_inventory(path: Path) -> None:
    rows = [
        _gear_row(
            f"private-result-item-{index}",
            slot,
            GearSet.SPEED if index < 4 else GearSet.HEALTH,
        )
        for index, slot in enumerate(GEAR_SLOT_ORDER)
    ]
    path.write_text(json.dumps({
        "items": rows,
        "heroes": [{"id": "inventory-hero-ras", "name": "Ras"}],
    }), encoding="utf-8")


class OptimizerSearchBackendIntegrationTests(unittest.TestCase):
    def test_async_cpu_search_recovers_current_state_and_restarts_privately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            user_data = root / "isolated-user-data"
            source = root / "private-player" / "gear.txt"
            source.parent.mkdir()
            _write_inventory(source)

            backend = BackendSession(user_data)
            try:
                imported = backend.request(
                    "import",
                    "optimizer.inventory.import",
                    {"sourcePath": str(source)},
                )
                hero = backend.request(
                    "hero",
                    "optimizer.hero.search",
                    {"query": "Ras", "limit": 1},
                )["result"]["results"][0]
                draft = backend.request(
                    "draft",
                    "optimizer.profile.load",
                    {"heroId": hero["heroId"]},
                )["result"]["draft"]
                started = backend.request(
                    "search-start",
                    "optimizer.search.start",
                    {"draft": draft},
                )
                terminal = backend.wait_for_search()
            finally:
                first_stderr = backend.stop()

            self.assertTrue(imported["ok"])
            self.assertEqual("preparing", started["result"]["state"])
            self.assertEqual("completed", terminal["result"]["state"])
            self.assertEqual("cpu", terminal["result"]["backend"])
            self.assertEqual("1", terminal["result"]["searchedPermutations"])
            self.assertEqual("1", terminal["result"]["categoryCounts"]["exact"])
            self.assertTrue(terminal["result"]["resultAvailable"])
            run_id = terminal["result"]["resultRunId"]
            self.assertTrue(
                (user_data / "optimizer_results" / "runs" / run_id / "manifest.json").is_file()
            )
            self.assertTrue(any(
                event.get("event") == "optimizer.search.updated"
                and event.get("payload", {}).get("state") == "completed"
                for event in backend.events
            ))

            restarted = BackendSession(user_data)
            try:
                current = restarted.request("search-after-restart", "optimizer.search.get")
            finally:
                second_stderr = restarted.stop()
            self.assertEqual("idle", current["result"]["state"])
            self.assertTrue(
                (user_data / "optimizer_results" / "runs" / run_id / "manifest.json").is_file()
            )

            public_output = json.dumps({
                "started": started,
                "terminal": terminal,
                "events": backend.events,
                "current": current,
            }) + first_stderr + second_stderr
            self.assertNotIn(str(source), public_output)
            self.assertNotIn("private-player", public_output)
            self.assertNotIn("private-result-item", public_output)
            self.assertNotIn('"rows"', public_output)
            self.assertNotIn('"itemIds"', public_output)


if __name__ == "__main__":
    unittest.main()
