import tempfile
import time
import unittest
from pathlib import Path

from src.desktop.health_controller import HealthController
from src.desktop.health_models import (
    CapabilityState,
    OperationState,
    OverallHealthState,
    aggregate_overall,
    checking_snapshot,
)
from src.desktop.health_service import (
    CommandResult,
    HealthService,
    HealthSystem,
    OperationCancelled,
    ProbeTimeout,
    ProbeUnavailable,
    _parse_nvidia_smi_rows,
)
from src.desktop.cuda_setup import CudaComponentCancelled, CudaComponentStatus
from src.optimizer.cuda import CUDA_DISABLE_ENV_VAR


class FakeHealthSystem(HealthSystem):
    def __init__(self):
        self.tesseract_path = "C:/fake/tesseract.exe"
        self.ollama_path = "C:/fake/ollama.exe"
        self.adb_path = "C:/fake/adb.exe"
        self.cli_ollama_version = "0.13.2"
        self.server_ollama_version = "0.13.2"
        self.models = {"qwen3-vl:8b-instruct"}
        self.tesseract_timeout = False
        self.storage_error = None
        self.cuda = {
            "status": "ready",
            "mode": "cuda",
            "available": True,
            "disabled": False,
            "summary": "CUDA acceleration is ready on Test GPU.",
            "deviceCount": 1,
            "runtimeVersion": 13000,
            "driverVersion": 13000,
            "deviceName": "Test GPU",
            "cupyVersion": "14.0.0",
            "selectedDeviceIndex": 0,
            "freeVramBytes": 24 << 30,
            "totalVramBytes": 32 << 30,
            "allocationProbeBytes": 1 << 20,
            "allocationProbeSucceeded": True,
        }
        self.cuda_error = None
        self.nvidia = {
            "status": "detected",
            "detected": True,
            "rtxDetected": True,
            "adapters": ({"name": "NVIDIA GeForce RTX Test", "driverVersion": "591.44"},),
        }
        self.nvidia_error = None
        self.adb_devices = [["emulator-5554", "device"]]
        self.adb_timeout = False
        self.configured_serial = ""
        self.started_commands = []
        self.pull_progress = [(0.25, "downloading"), (0.75, "verifying")]
        self.packet_capture = {"version": "2.7.0", "adapterCount": 2, "libpcap": True}

    def resolve_tesseract(self):
        return self.tesseract_path

    def resolve_ollama(self):
        return self.ollama_path

    def resolve_adb(self, configured):
        return self.adb_path

    def run(self, command, timeout):
        executable = command[0]
        if executable == self.tesseract_path:
            if self.tesseract_timeout:
                raise ProbeTimeout("tesseract timeout")
            return CommandResult(0, "tesseract 5.5.0", "")
        if executable == self.ollama_path:
            return CommandResult(0, f"ollama version {self.cli_ollama_version}", "")
        if executable == self.adb_path:
            if self.adb_timeout:
                raise ProbeTimeout("adb timeout")
            if command[-1] == "version":
                return CommandResult(0, "Android Debug Bridge version 1.0.41", "")
            lines = ["List of devices attached", *["\t".join(device) for device in self.adb_devices]]
            return CommandResult(0, "\n".join(lines), "")
        raise ProbeUnavailable(f"unexpected command: {command}")

    def get_json(self, url, timeout):
        if url.endswith("/api/version"):
            if self.server_ollama_version is None:
                raise ProbeUnavailable("server offline")
            return {"version": self.server_ollama_version}
        if url.endswith("/api/tags"):
            return {"models": [{"model": model} for model in sorted(self.models)]}
        raise ProbeUnavailable(f"unexpected URL: {url}")

    def start_hidden(self, command):
        self.started_commands.append(command)
        self.server_ollama_version = self.cli_ollama_version

    def sleep(self, seconds):
        return None

    def probe_writable(self, directory):
        if self.storage_error:
            raise self.storage_error

    def settings(self):
        return {
            "adb": {
                "adb_path": self.adb_path or "adb",
                "device_serial": self.configured_serial,
            }
        }

    def cuda_info(self):
        if self.cuda_error:
            raise self.cuda_error
        return self.cuda

    def nvidia_info(self, timeout):
        if self.nvidia_error:
            raise self.nvidia_error
        return self.nvidia

    def packet_capture_info(self):
        if isinstance(self.packet_capture, Exception):
            raise self.packet_capture
        return self.packet_capture

    def pull_ollama_model(self, base_url, model, progress, cancelled):
        for fraction, message in self.pull_progress:
            if cancelled():
                raise RuntimeError("cancelled")
            progress(fraction, message)
        self.models.add(model)


def capability(capabilities, capability_id):
    return next(item for item in capabilities if item.id == capability_id)


def wait_for_controller(controller, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = controller.get_snapshot(ensure_refresh=False)
        operation = snapshot.get("operation")
        if operation and operation["state"] != OperationState.RUNNING.value:
            return snapshot
        time.sleep(0.01)
    raise AssertionError("health operation did not finish")


class AvailableCudaComponentManager:
    def status(self):
        return CudaComponentStatus(False, True, None, "cp312")


class DesktopHealthTests(unittest.TestCase):
    def setUp(self):
        self.system = FakeHealthSystem()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.service = HealthService(
            system=self.system,
            user_data_dir=Path(self.temp_dir.name) / "user_data",
            ollama_start_attempts=2,
            ollama_start_interval=0,
            cuda_components=AvailableCudaComponentManager(),
        )

    def test_healthy_snapshot_reports_every_capability_ready(self):
        capabilities = self.service.check_all(auto_start_ollama=False)

        self.assertEqual(len(capabilities), 7)
        self.assertTrue(all(item.state is CapabilityState.READY for item in capabilities))
        self.assertEqual(aggregate_overall(capabilities), OverallHealthState.READY)

    def test_packet_capture_requires_npcap_and_offers_its_installer(self):
        self.system.packet_capture["libpcap"] = False

        result = self.service.check_packet_capture()

        self.assertEqual(result.state, CapabilityState.UNAVAILABLE)
        self.assertEqual(result.actions[0].id, "packet.install")

    def test_missing_executables_are_optional_degraded_capabilities(self):
        self.system.tesseract_path = None
        self.system.ollama_path = None
        self.system.server_ollama_version = None
        self.system.adb_path = None

        capabilities = self.service.check_all(auto_start_ollama=False)

        self.assertEqual(capability(capabilities, "tesseract").state, CapabilityState.UNAVAILABLE)
        self.assertEqual(capability(capabilities, "ollama").state, CapabilityState.UNAVAILABLE)
        self.assertEqual(capability(capabilities, "adb").state, CapabilityState.UNAVAILABLE)
        self.assertIn("select adb.exe", capability(capabilities, "adb").detail)
        self.assertEqual(capability(capabilities, "adb").actions[0].label, "Download platform tools")
        self.assertEqual(aggregate_overall(capabilities), OverallHealthState.DEGRADED)
        self.assertTrue(capability(capabilities, "ollama").actions)

    def test_required_storage_error_makes_overall_health_error(self):
        self.system.storage_error = PermissionError("read only")

        capabilities = self.service.check_all(auto_start_ollama=False)

        self.assertEqual(capability(capabilities, "storage").state, CapabilityState.ERROR)
        self.assertEqual(aggregate_overall(capabilities), OverallHealthState.ERROR)

    def test_probe_timeout_does_not_block_other_capabilities(self):
        self.system.tesseract_timeout = True
        self.system.adb_timeout = True

        capabilities = self.service.check_all(auto_start_ollama=False)

        self.assertEqual(capability(capabilities, "tesseract").state, CapabilityState.ERROR)
        self.assertEqual(capability(capabilities, "adb").state, CapabilityState.DEGRADED)
        self.assertEqual(capability(capabilities, "backend").state, CapabilityState.READY)

    def test_installed_stopped_ollama_is_started_without_replacing_processes(self):
        self.system.server_ollama_version = None

        result = self.service.check_ollama(auto_start=True)

        self.assertEqual(result.state, CapabilityState.READY)
        self.assertEqual(self.system.started_commands, [[self.system.ollama_path, "serve"]])

    def test_stale_ollama_server_is_degraded_and_not_restarted(self):
        self.system.server_ollama_version = "0.12.8"

        result = self.service.check_ollama(auto_start=True)

        self.assertEqual(result.state, CapabilityState.DEGRADED)
        self.assertIn("does not match", result.summary)
        self.assertEqual(self.system.started_commands, [])

    def test_missing_model_is_repairable_by_download_action(self):
        self.system.models.clear()

        result = self.service.check_ollama(auto_start=False)

        self.assertEqual(result.state, CapabilityState.DEGRADED)
        self.assertEqual([action.id for action in result.actions], ["ollama.pull_model"])

    def test_cuda_failure_selects_usable_cpu_fallback(self):
        self.system.cuda_error = ProbeUnavailable("CuPy missing")

        result = self.service.check_cuda()

        self.assertEqual(result.state, CapabilityState.DEGRADED)
        self.assertEqual(result.metadata["mode"], "cpu")
        self.assertEqual([action.id for action in result.actions], ["cuda.install"])

    def test_nvidia_probe_is_bounded_and_does_not_claim_cuda_readiness(self):
        rows = _parse_nvidia_smi_rows('NVIDIA GeForce RTX 5090, 591.44\n"NVIDIA RTX, Test", 591.44')

        self.assertEqual(rows[0]["name"], "NVIDIA GeForce RTX 5090")
        self.assertEqual(rows[1]["name"], "NVIDIA RTX, Test")
        with self.assertRaisesRegex(ProbeUnavailable, "malformed"):
            _parse_nvidia_smi_rows("GPU without driver")
        with self.assertRaisesRegex(ProbeUnavailable, "bounded"):
            _parse_nvidia_smi_rows("x" * (16 * 1024 + 1))

    def test_non_nvidia_pc_keeps_cpu_mode_without_setup_action(self):
        self.system.nvidia = {"status": "not-found", "detected": False, "adapters": ()}
        self.system.cuda = {
            "status": "cupy-unavailable",
            "mode": "cpu",
            "available": False,
            "disabled": False,
            "summary": "CPU fallback is active because CuPy is missing.",
            "deviceCount": 0,
        }

        result = self.service.check_cuda()

        self.assertEqual(result.metadata["mode"], "cpu")
        self.assertEqual(result.actions, ())

    def test_failed_nvidia_probe_is_nonfatal_and_does_not_offer_an_installer(self):
        self.system.nvidia_error = ProbeTimeout("nvidia-smi timed out")
        self.system.cuda = {
            "status": "cupy-unavailable",
            "mode": "cpu",
            "available": False,
            "disabled": False,
            "summary": "CPU fallback is active because CuPy is missing.",
            "deviceCount": 0,
        }

        result = self.service.check_cuda()

        self.assertEqual(result.state, CapabilityState.DEGRADED)
        self.assertEqual(result.actions, ())
        self.assertEqual(result.metadata["nvidia"]["status"], "probe-failed")
        self.assertIn("CPU mode remains available", result.detail)

    def test_cuda_diagnostic_status_and_evidence_drive_desktop_health(self):
        ready = self.service.check_cuda()
        self.assertEqual(ready.state, CapabilityState.READY)
        self.assertEqual(ready.metadata["status"], "ready")
        self.assertEqual(ready.metadata["totalVramBytes"], 32 << 30)
        self.assertTrue(ready.metadata["allocationProbeSucceeded"])

        for status, summary in (
            ("disabled", "CPU fallback is active because CUDA was deliberately disabled."),
            ("cupy-unavailable", "CPU fallback is active because CuPy is missing."),
            ("no-device", "CPU fallback is active because no CUDA device was detected."),
            ("incompatible", "CPU fallback is active because CUDA is incompatible."),
            ("query-failed", "CPU fallback is active because CUDA discovery failed."),
            ("allocation-failed", "CPU fallback is active because allocation failed."),
        ):
            with self.subTest(status=status):
                self.system.cuda = {
                    "status": status,
                    "mode": "cpu",
                    "available": False,
                    "disabled": status == "disabled",
                    "summary": summary,
                    "detail": None if status in {"disabled", "no-device"} else "diagnostic detail",
                    "cupyVersion": None if status in {"disabled", "cupy-unavailable"} else "14.1.1",
                    "deviceCount": 0,
                }
                result = self.service.check_cuda()
                self.assertEqual(result.state, CapabilityState.DEGRADED)
                if status == "cupy-unavailable":
                    self.assertIn("NVIDIA GeForce RTX Test", result.summary)
                    self.assertIn("optional GPU components", result.summary)
                else:
                    self.assertEqual(result.summary, summary)
                self.assertEqual(result.metadata["mode"], "cpu")
                self.assertEqual(result.metadata["status"], status)

    def test_real_health_system_honors_cuda_disable_without_importing_cupy(self):
        info = HealthSystem(environment={CUDA_DISABLE_ENV_VAR: "1"}).cuda_info()

        self.assertEqual(info["status"], "disabled")
        self.assertEqual(info["mode"], "cpu")
        self.assertTrue(info["disabled"])

    def test_optional_adb_distinguishes_no_device_and_configured_offline_device(self):
        self.system.adb_devices = []
        no_device = self.service.check_adb()
        self.system.configured_serial = "emulator-5554"
        self.system.adb_devices = [["emulator-5554", "offline"]]
        offline = self.service.check_adb()

        self.assertEqual(no_device.state, CapabilityState.DEGRADED)
        self.assertIn("no ready device", no_device.summary)
        self.assertEqual(offline.state, CapabilityState.DEGRADED)
        self.assertIn("configured", offline.summary)

    def test_checking_snapshot_is_explicit_and_nonfatal(self):
        snapshot = checking_snapshot()

        self.assertEqual(snapshot.overall, OverallHealthState.CHECKING)
        self.assertTrue(all(item.state is CapabilityState.CHECKING for item in snapshot.capabilities))

    def test_controller_emits_in_progress_and_completed_download_snapshots(self):
        self.system.models.clear()
        events = []
        controller = HealthController(self.service, events.append)
        controller.refresh()
        wait_for_controller(controller)

        started = controller.run_action("ollama.pull_model")
        completed = wait_for_controller(controller)

        self.assertEqual(started["operation"]["state"], OperationState.RUNNING.value)
        self.assertEqual(
            next(item for item in started["capabilities"] if item["id"] == "ollama")["state"],
            CapabilityState.IN_PROGRESS.value,
        )
        self.assertTrue(any(event.get("operation", {}).get("progress") == 0.75 for event in events))
        self.assertEqual(completed["operation"]["state"], OperationState.SUCCEEDED.value)
        self.assertEqual(
            next(item for item in completed["capabilities"] if item["id"] == "ollama")["state"],
            CapabilityState.READY.value,
        )

    def test_controller_rejects_arbitrary_actions(self):
        controller = HealthController(self.service)

        with self.assertRaisesRegex(ValueError, "Unsupported health action"):
            controller.run_action("shell.run_anything")

    def test_controller_installs_gpu_components_with_progress(self):
        class FakeComponents:
            def status(inner_self):
                return CudaComponentStatus(False, True, None, "cp312")

            def install_or_repair(inner_self, progress, cancelled):
                progress(0.25, "downloading fixed component")
                progress(0.85, "verifying fixed component")
                self.system.cuda = {
                    **self.system.cuda,
                    "status": "ready",
                    "mode": "cuda",
                    "available": True,
                    "summary": "CUDA acceleration is ready on Test GPU.",
                }

        service = HealthService(
            system=self.system,
            user_data_dir=Path(self.temp_dir.name) / "gpu-action",
            cuda_components=FakeComponents(),
            ollama_start_attempts=2,
            ollama_start_interval=0,
        )
        events = []
        controller = HealthController(service, events.append)
        controller.refresh()
        wait_for_controller(controller)

        started = controller.run_action("cuda.install")
        completed = wait_for_controller(controller)

        self.assertEqual(started["operation"]["actionId"], "cuda.install")
        self.assertTrue(any(event.get("operation", {}).get("progress") == 0.85 for event in events))
        self.assertEqual(completed["operation"]["state"], OperationState.SUCCEEDED.value)
        cuda = next(item for item in completed["capabilities"] if item["id"] == "cuda")
        self.assertEqual(cuda["state"], CapabilityState.READY.value)

    def test_controller_cancels_gpu_setup_and_preserves_cpu_mode(self):
        class SlowComponents:
            def status(inner_self):
                return CudaComponentStatus(False, True, None, "cp312")

            def install_or_repair(inner_self, progress, cancelled):
                progress(0.1, "waiting")
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline:
                    if cancelled():
                        raise CudaComponentCancelled("cancelled")
                    time.sleep(0.005)
                raise AssertionError("cancellation was not observed")

        self.system.cuda = {
            "status": "cupy-unavailable",
            "mode": "cpu",
            "available": False,
            "disabled": False,
            "summary": "CPU mode is ready.",
            "deviceCount": 0,
        }
        service = HealthService(
            system=self.system,
            user_data_dir=Path(self.temp_dir.name) / "gpu-cancel",
            cuda_components=SlowComponents(),
            ollama_start_attempts=2,
            ollama_start_interval=0,
        )
        controller = HealthController(service)
        controller.refresh()
        wait_for_controller(controller)
        controller.run_action("cuda.install")
        controller.run_action("health.cancel")

        cancelled = wait_for_controller(controller)

        self.assertEqual(cancelled["operation"]["state"], OperationState.CANCELLED.value)
        self.assertEqual(cancelled["overall"], OverallHealthState.DEGRADED.value)
        self.assertIn("CPU mode remains available", cancelled["operation"]["message"])

    def test_failed_action_leaves_a_usable_degraded_snapshot(self):
        self.system.models.clear()

        def fail_pull(base_url, model, progress, cancelled):
            raise ProbeUnavailable("download unavailable")

        self.system.pull_ollama_model = fail_pull
        controller = HealthController(self.service)
        controller.refresh()
        wait_for_controller(controller)

        controller.run_action("ollama.pull_model")
        failed = wait_for_controller(controller)
        ollama = next(item for item in failed["capabilities"] if item["id"] == "ollama")

        self.assertEqual(failed["operation"]["state"], OperationState.FAILED.value)
        self.assertEqual(failed["overall"], OverallHealthState.DEGRADED.value)
        self.assertEqual(ollama["state"], CapabilityState.DEGRADED.value)
        self.assertIn("download unavailable", ollama["detail"])


if __name__ == "__main__":
    unittest.main()
