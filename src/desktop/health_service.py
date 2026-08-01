"""Bounded local dependency probes and narrowly scoped health actions."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
import urllib.error
import urllib.request
import csv
import io
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core.config_manager import load_settings
from src.core.settings_service import SettingsService
from src.core.workspace_paths import DEFAULT_DEVELOPMENT_USER_DATA
from src.desktop import BACKEND_VERSION, PROTOCOL_VERSION
from src.desktop.health_models import (
    CAPABILITY_SPECS,
    CapabilityState,
    HealthAction,
    HealthCapability,
)
from src.desktop.cuda_setup import (
    CUDA_COMPONENT_DOWNLOAD_NOTE,
    CUDA_COMPONENT_SOURCE,
    CudaComponentCancelled,
    CudaComponentManager,
)
from src.desktop.runtime_paths import (
    resolve_adb_path,
    resolve_nvidia_smi_path,
    resolve_ollama_path,
    resolve_tesseract_path,
)
from src.optimizer.cuda import cuda_disabled_from_environment, diagnose_cuda_runtime

OLLAMA_BASE_URL = "http://127.0.0.1:11434"
OLLAMA_MODEL = "qwen3-vl:8b-instruct"
MINIMUM_OLLAMA_VERSION = (0, 12, 7)
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
MAX_NVIDIA_ADAPTERS = 16
MAX_NVIDIA_SMI_OUTPUT = 16 * 1024


class ProbeUnavailable(RuntimeError):
    pass


class ProbeTimeout(RuntimeError):
    pass


class OperationCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class HealthSystem:
    """OS/network boundary isolated so health logic can be tested without live dependencies."""

    def __init__(self, environment: Mapping[str, str] | None = None):
        self.environment = dict(os.environ if environment is None else environment)

    def resolve_tesseract(self) -> str | None:
        return resolve_tesseract_path(environment=self.environment)

    def resolve_ollama(self) -> str | None:
        return resolve_ollama_path(environment=self.environment)

    def resolve_adb(self, configured: str) -> str | None:
        return resolve_adb_path(configured)

    def resolve_nvidia_smi(self) -> str | None:
        return resolve_nvidia_smi_path(environment=getattr(self, "environment", {}))

    def run(self, command: list[str], timeout: float) -> CommandResult:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
                creationflags=CREATE_NO_WINDOW,
            )
        except FileNotFoundError as error:
            raise ProbeUnavailable(str(error)) from error
        except subprocess.TimeoutExpired as error:
            raise ProbeTimeout(f"Command timed out after {timeout:g}s.") from error
        return CommandResult(result.returncode, result.stdout.strip(), result.stderr.strip())

    def get_json(self, url: str, timeout: float) -> Mapping[str, Any]:
        try:
            request = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                value = json.loads(response.read().decode("utf-8"))
        except TimeoutError as error:
            raise ProbeTimeout(f"Request timed out after {timeout:g}s.") from error
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise ProbeUnavailable(str(error)) from error
        if not isinstance(value, Mapping):
            raise ProbeUnavailable("Endpoint returned a non-object response.")
        return value

    def start_hidden(self, command: list[str]) -> None:
        try:
            subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )
        except OSError as error:
            raise ProbeUnavailable(str(error)) from error

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def probe_writable(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / f".health-{uuid.uuid4().hex}.tmp"
        try:
            with probe.open("wb") as stream:
                stream.write(b"e7-health")
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            probe.unlink(missing_ok=True)

    def settings(self) -> Mapping[str, Any]:
        user_data = self.environment.get("E7_USER_DATA_DIR")
        settings_file = self.environment.get("E7_SETTINGS_PATH")
        if settings_file or user_data:
            path = Path(settings_file) if settings_file else Path(user_data) / "settings.json"
            return SettingsService(path).load().document
        return load_settings()

    def cuda_info(self) -> Mapping[str, Any]:
        return diagnose_cuda_runtime(
            disabled=cuda_disabled_from_environment(self.environment)
        ).to_dict()

    def nvidia_info(self, timeout: float) -> Mapping[str, Any]:
        executable = self.resolve_nvidia_smi()
        if not executable:
            return {"status": "not-found", "detected": False, "adapters": ()}
        result = self.run(
            [
                executable,
                "--query-gpu=name,driver_version",
                "--format=csv,noheader,nounits",
            ],
            timeout,
        )
        if result.returncode != 0:
            raise ProbeUnavailable("NVIDIA diagnostics returned an error.")
        adapters = _parse_nvidia_smi_rows(result.stdout)
        return {
            "status": "detected",
            "detected": True,
            "rtxDetected": any("RTX" in adapter["name"].upper() for adapter in adapters),
            "adapters": adapters,
        }

    def packet_capture_info(self) -> Mapping[str, Any]:
        try:
            import scapy
            from scapy.all import conf, get_if_list
        except ImportError as error:
            raise ProbeUnavailable("The bundled packet capture component is missing.") from error
        try:
            interfaces = tuple(str(name) for name in get_if_list() if str(name).strip())
        except Exception as error:
            raise ProbeUnavailable(f"Npcap could not enumerate network adapters: {error}") from error
        if not interfaces:
            raise ProbeUnavailable("Npcap did not expose any network adapters.")
        return {
            "version": str(getattr(scapy, "__version__", "unknown")),
            "adapterCount": len(interfaces),
            "libpcap": bool(getattr(conf, "use_pcap", False)),
        }

    def pull_ollama_model(
        self,
        base_url: str,
        model: str,
        progress: Callable[[float | None, str], None],
        cancelled: Callable[[], bool],
    ) -> None:
        import requests

        try:
            with requests.post(
                f"{base_url}/api/pull",
                json={"model": model, "stream": True},
                stream=True,
                timeout=(5, 30),
            ) as response:
                response.raise_for_status()
                for raw_line in response.iter_lines(decode_unicode=True):
                    if cancelled():
                        raise OperationCancelled("Model download was cancelled.")
                    if not raw_line:
                        continue
                    payload = json.loads(raw_line)
                    total = payload.get("total")
                    completed = payload.get("completed")
                    fraction = None
                    if isinstance(total, (int, float)) and total > 0 and isinstance(completed, (int, float)):
                        fraction = float(completed) / float(total)
                    progress(fraction, str(payload.get("status") or "Downloading model…"))
        except requests.Timeout as error:
            raise ProbeTimeout("Ollama model download stopped responding.") from error
        except requests.RequestException as error:
            raise ProbeUnavailable(str(error)) from error


INSTALL_OLLAMA = HealthAction("ollama.install", "Install or update", "install")
START_OLLAMA = HealthAction("ollama.start", "Start Ollama", "start")
PULL_OLLAMA = HealthAction("ollama.pull_model", "Download vision model", "download")
INSTALL_TESSERACT = HealthAction("tesseract.install", "Install Tesseract", "install")
INSTALL_PACKET_CAPTURE = HealthAction("packet.install", "Install Npcap", "install")
INSTALL_ADB = HealthAction("adb.install", "Download platform tools", "install")
INSTALL_CUDA = HealthAction("cuda.install", "Install GPU components", "install")
REPAIR_CUDA = HealthAction("cuda.repair", "Repair GPU components", "repair")


class HealthService:
    def __init__(
        self,
        *,
        system: HealthSystem | None = None,
        user_data_dir: str | Path = DEFAULT_DEVELOPMENT_USER_DATA,
        ollama_base_url: str = OLLAMA_BASE_URL,
        ollama_model: str = OLLAMA_MODEL,
        command_timeout: float = 5.0,
        http_timeout: float = 2.0,
        ollama_start_attempts: int = 12,
        ollama_start_interval: float = 0.25,
        cuda_components: CudaComponentManager | None = None,
    ):
        self.system = system or HealthSystem()
        self.user_data_dir = Path(user_data_dir)
        self.ollama_base_url = ollama_base_url.rstrip("/")
        self.ollama_model = ollama_model
        self.command_timeout = command_timeout
        self.http_timeout = http_timeout
        self.ollama_start_attempts = ollama_start_attempts
        self.ollama_start_interval = ollama_start_interval
        self.cuda_components = cuda_components or CudaComponentManager(
            self.user_data_dir,
            environment=getattr(self.system, "environment", {}),
        )

    def check_all(
        self,
        *,
        auto_start_ollama: bool,
        on_capability: Callable[[HealthCapability, int, int], None] | None = None,
    ) -> tuple[HealthCapability, ...]:
        probes: tuple[Callable[[], HealthCapability], ...] = (
            self.check_backend,
            self.check_storage,
            self.check_tesseract,
            lambda: self.check_ollama(auto_start=auto_start_ollama),
            self.check_cuda,
            self.check_packet_capture,
            self.check_adb,
        )
        capabilities: list[HealthCapability] = []
        total = len(probes)
        for index, probe in enumerate(probes, start=1):
            try:
                capability = probe()
            except Exception as error:
                capability_id, title, required = CAPABILITY_SPECS[index - 1]
                capability = HealthCapability(
                    id=capability_id,
                    title=title,
                    state=CapabilityState.ERROR,
                    summary="Health check failed.",
                    required=required,
                    detail=str(error),
                )
            capabilities.append(capability)
            if on_capability:
                on_capability(capability, index, total)
        return tuple(capabilities)

    def check_backend(self) -> HealthCapability:
        return HealthCapability(
            id="backend",
            title="Application backend",
            state=CapabilityState.READY,
            summary="Python backend and desktop protocol are compatible.",
            required=True,
            version=BACKEND_VERSION,
            metadata={"protocolVersion": PROTOCOL_VERSION},
        )

    def check_storage(self) -> HealthCapability:
        try:
            self.system.probe_writable(self.user_data_dir)
        except (OSError, ProbeTimeout, ProbeUnavailable) as error:
            return HealthCapability(
                id="storage",
                title="Local data",
                state=CapabilityState.ERROR,
                summary="Personal data directory is not writable.",
                required=True,
                path=str(self.user_data_dir.resolve()),
                detail=str(error),
            )
        settings = SettingsService(self.user_data_dir / "settings.json").load()
        migration_state = (
            "read_only" if settings.read_only
            else "recovery" if settings.warning and settings.migrated_from is None
            else "pending" if settings.migrated_from is not None
            else "not_required"
        )
        degraded = settings.read_only or migration_state == "recovery"
        return HealthCapability(
            id="storage",
            title="Local data",
            state=CapabilityState.DEGRADED if degraded else CapabilityState.READY,
            summary=(
                "Personal data is writable, but settings need recovery attention."
                if degraded
                else "Personal data is writable; legacy settings are ready for a safe migration."
                if migration_state == "pending"
                else "Personal data directory is writable; no migration is required."
            ),
            required=True,
            path=str(self.user_data_dir.resolve()),
            detail=settings.warning if degraded else None,
            metadata={
                "migrationState": migration_state,
                "schemaVersion": settings.schema_version,
                "settingsSource": settings.source,
            },
        )

    def check_tesseract(self) -> HealthCapability:
        executable = self.system.resolve_tesseract()
        if not executable:
            return HealthCapability(
                id="tesseract",
                title="Tesseract OCR",
                state=CapabilityState.UNAVAILABLE,
                summary="Tesseract is not installed or bundled.",
                actions=(INSTALL_TESSERACT,),
            )
        try:
            result = self.system.run([executable, "--version"], self.command_timeout)
        except ProbeTimeout as error:
            return HealthCapability(
                id="tesseract",
                title="Tesseract OCR",
                state=CapabilityState.ERROR,
                summary="Tesseract did not answer before the timeout.",
                path=executable,
                detail=str(error),
            )
        except ProbeUnavailable as error:
            return HealthCapability(
                id="tesseract",
                title="Tesseract OCR",
                state=CapabilityState.UNAVAILABLE,
                summary="Tesseract could not be started.",
                path=executable,
                detail=str(error),
                actions=(INSTALL_TESSERACT,),
            )
        text = result.stdout or result.stderr
        version = _first_version(text)
        if result.returncode != 0:
            return HealthCapability(
                id="tesseract",
                title="Tesseract OCR",
                state=CapabilityState.ERROR,
                summary="Tesseract returned an error.",
                path=executable,
                version=version,
                detail=text or f"Exit code {result.returncode}",
            )
        return HealthCapability(
            id="tesseract",
            title="Tesseract OCR",
            state=CapabilityState.READY,
            summary="OCR engine is available.",
            path=executable,
            version=version,
        )

    def check_ollama(self, *, auto_start: bool) -> HealthCapability:
        executable = self.system.resolve_ollama()
        cli_version = None
        cli_error = None
        if executable:
            try:
                cli_result = self.system.run([executable, "--version"], self.command_timeout)
                cli_version = _first_version(cli_result.stdout or cli_result.stderr)
                if cli_result.returncode != 0:
                    cli_error = cli_result.stderr or cli_result.stdout
            except (ProbeTimeout, ProbeUnavailable) as error:
                cli_error = str(error)

        server = self._ollama_json("/api/version")
        if server is None and executable and auto_start:
            try:
                self.system.start_hidden([executable, "serve"])
                for _ in range(self.ollama_start_attempts):
                    self.system.sleep(self.ollama_start_interval)
                    server = self._ollama_json("/api/version")
                    if server is not None:
                        break
            except ProbeUnavailable as error:
                cli_error = str(error)

        if server is None:
            if not executable:
                return HealthCapability(
                    id="ollama",
                    title="Ollama vision",
                    state=CapabilityState.UNAVAILABLE,
                    summary="Ollama is not installed and no local server is responding.",
                    actions=(INSTALL_OLLAMA,),
                )
            return HealthCapability(
                id="ollama",
                title="Ollama vision",
                state=CapabilityState.DEGRADED,
                summary="Ollama is installed but its local server is not responding.",
                path=executable,
                version=cli_version,
                detail=cli_error,
                actions=(START_OLLAMA, INSTALL_OLLAMA),
            )

        server_version = str(server.get("version") or "") or None
        effective_version = server_version or cli_version
        if effective_version and _version_tuple(effective_version) < MINIMUM_OLLAMA_VERSION:
            return HealthCapability(
                id="ollama",
                title="Ollama vision",
                state=CapabilityState.DEGRADED,
                summary="Ollama must be updated before the vision model can be used reliably.",
                path=executable,
                version=effective_version,
                actions=(INSTALL_OLLAMA,),
                metadata={"minimumVersion": ".".join(map(str, MINIMUM_OLLAMA_VERSION))},
            )
        if cli_version and server_version and _version_tuple(cli_version) != _version_tuple(server_version):
            return HealthCapability(
                id="ollama",
                title="Ollama vision",
                state=CapabilityState.DEGRADED,
                summary="The running Ollama server does not match the installed executable.",
                path=executable,
                version=server_version,
                detail=f"Installed {cli_version}; running server {server_version}. Restart Ollama manually.",
                actions=(INSTALL_OLLAMA,),
            )

        tags = self._ollama_json("/api/tags")
        if tags is None:
            return HealthCapability(
                id="ollama",
                title="Ollama vision",
                state=CapabilityState.DEGRADED,
                summary="Ollama is running, but its model list could not be read.",
                path=executable,
                version=server_version,
                actions=(START_OLLAMA,),
            )
        models = tags.get("models")
        names = {
            str(model.get("model") or model.get("name") or "").lower()
            for model in models if isinstance(model, Mapping)
        } if isinstance(models, list) else set()
        if self.ollama_model.lower() not in names:
            return HealthCapability(
                id="ollama",
                title="Ollama vision",
                state=CapabilityState.DEGRADED,
                summary="Ollama is ready, but the required vision model is missing.",
                path=executable,
                version=server_version,
                actions=(PULL_OLLAMA,),
                metadata={"requiredModel": self.ollama_model},
            )
        return HealthCapability(
            id="ollama",
            title="Ollama vision",
            state=CapabilityState.READY,
            summary="Ollama and the required vision model are ready.",
            path=executable,
            version=server_version,
            metadata={"requiredModel": self.ollama_model},
        )

    def check_cuda(self) -> HealthCapability:
        component_status = self.cuda_components.status()
        try:
            nvidia = dict(self.system.nvidia_info(self.command_timeout))
        except (ProbeUnavailable, ProbeTimeout) as error:
            nvidia = {
                "status": "probe-failed",
                "detected": False,
                "adapters": (),
                "detail": str(error),
            }
        try:
            info = dict(self.system.cuda_info())
        except (ProbeUnavailable, ProbeTimeout) as error:
            metadata = {
                "mode": "cpu",
                "nvidia": nvidia,
                "component": component_status.to_dict(),
            }
            return HealthCapability(
                id="cuda",
                title="GPU acceleration",
                state=CapabilityState.DEGRADED,
                summary="CPU fallback is active; CUDA acceleration is unavailable.",
                detail=str(error),
                actions=self._cuda_setup_actions("cupy-unavailable", nvidia, component_status.installed, component_status.installer_available),
                metadata=metadata,
            )
        status = str(info.get("status") or "")
        metadata = {
            **{
                key: value
                for key, value in info.items()
                if key not in {"summary", "detail"}
            },
            "nvidia": nvidia,
            "component": component_status.to_dict(),
        }
        if status != "ready":
            detected_adapters = nvidia.get("adapters") if nvidia.get("detected") else ()
            adapter_name = ""
            if isinstance(detected_adapters, (list, tuple)) and detected_adapters:
                first = detected_adapters[0]
                if isinstance(first, Mapping):
                    adapter_name = str(first.get("name") or "")
            summary = str(info.get("summary") or "CPU fallback is active; CUDA acceleration is unavailable.")
            if status == "cupy-unavailable" and adapter_name:
                summary = f"CPU mode is ready. {adapter_name} was detected; optional GPU components can enable CUDA."
            return HealthCapability(
                id="cuda",
                title="GPU acceleration",
                state=CapabilityState.DEGRADED,
                summary=summary,
                detail=self._cuda_detail(info, nvidia, component_status.installer_available),
                version=None if info.get("cupyVersion") is None else str(info["cupyVersion"]),
                actions=self._cuda_setup_actions(status, nvidia, component_status.installed, component_status.installer_available),
                metadata=metadata,
            )
        return HealthCapability(
            id="cuda",
            title="GPU acceleration",
            state=CapabilityState.READY,
            summary=str(info.get("summary") or f"CUDA acceleration is ready on {info.get('deviceName') or 'the selected GPU'}."),
            version=str(info.get("cupyVersion") or "unknown"),
            metadata=metadata,
        )

    @staticmethod
    def _cuda_setup_actions(
        diagnostic_status: str,
        nvidia: Mapping[str, Any],
        component_installed: bool,
        installer_available: bool,
    ) -> tuple[HealthAction, ...]:
        if (
            diagnostic_status not in {"cupy-unavailable", "query-failed", "allocation-failed"}
            or not nvidia.get("detected")
            or not installer_available
        ):
            return ()
        return (REPAIR_CUDA if component_installed else INSTALL_CUDA,)

    @staticmethod
    def _cuda_detail(
        info: Mapping[str, Any],
        nvidia: Mapping[str, Any],
        installer_available: bool,
    ) -> str | None:
        details: list[str] = []
        if info.get("detail"):
            details.append(str(info["detail"]))
        adapters = nvidia.get("adapters")
        if nvidia.get("detected") and isinstance(adapters, (list, tuple)) and adapters:
            first = adapters[0]
            if isinstance(first, Mapping):
                name = str(first.get("name") or "NVIDIA GPU")
                driver = str(first.get("driverVersion") or "unknown")
                details.append(
                    f"Detected {name} with NVIDIA driver {driver}. The optional pinned component comes from "
                    f"{CUDA_COMPONENT_SOURCE}; {CUDA_COMPONENT_DOWNLOAD_NOTE} No CUDA Toolkit or nvcc is required."
                )
                if not installer_available:
                    details.append("This build does not yet include the trusted component installer helper.")
        elif nvidia.get("status") == "probe-failed":
            details.append("NVIDIA detection did not complete; CPU mode remains available.")
        return " ".join(details) or None

    def setup_cuda_components(
        self,
        progress: Callable[[float | None, str], None],
        cancelled: Callable[[], bool],
    ) -> None:
        try:
            self.cuda_components.install_or_repair(progress, cancelled)
        except CudaComponentCancelled as error:
            raise OperationCancelled(str(error)) from error

    def check_packet_capture(self) -> HealthCapability:
        try:
            info = self.system.packet_capture_info()
        except ProbeUnavailable as error:
            return HealthCapability(
                id="packet",
                title="Game packet capture",
                state=CapabilityState.UNAVAILABLE,
                summary="Packet reads are unavailable. Install Npcap to use packet-based tools.",
                detail=str(error),
                actions=(INSTALL_PACKET_CAPTURE,),
            )
        if not info.get("libpcap"):
            return HealthCapability(
                id="packet",
                title="Game packet capture",
                state=CapabilityState.UNAVAILABLE,
                summary="Npcap is required for packet-based tools.",
                detail="Install Npcap from its official installer, then restart Meowtoko E7 Tool.",
                version=str(info.get("version") or "unknown"),
                actions=(INSTALL_PACKET_CAPTURE,),
                metadata=dict(info),
            )
        return HealthCapability(
            id="packet",
            title="Game packet capture",
            state=CapabilityState.READY,
            summary=(
                f"Packet decoding is ready across {int(info.get('adapterCount', 0))} "
                "network adapter(s)."
            ),
            version=str(info.get("version") or "unknown"),
            metadata=dict(info),
        )

    def check_adb(self) -> HealthCapability:
        settings = self.system.settings()
        adb_settings = settings.get("adb", {}) if isinstance(settings, Mapping) else {}
        if not isinstance(adb_settings, Mapping):
            adb_settings = {}
        configured = str(adb_settings.get("adb_path") or "adb")
        serial = str(adb_settings.get("device_serial") or "").strip()
        executable = self.system.resolve_adb(configured)
        if not executable:
            return HealthCapability(
                id="adb",
                title="ADB automation",
                state=CapabilityState.UNAVAILABLE,
                summary="ADB was not found at the configured path. Screenshot and tap workflows need adb.exe.",
                detail=(
                    "Open Settings > Android connection and select adb.exe. "
                    "Download platform tools only if adb.exe is not installed."
                ),
                actions=(INSTALL_ADB,),
            )
        try:
            version_result = self.system.run([executable, "version"], self.command_timeout)
            devices_result = self.system.run([executable, "devices"], self.command_timeout)
        except ProbeTimeout as error:
            return HealthCapability(
                id="adb",
                title="ADB automation",
                state=CapabilityState.DEGRADED,
                summary="ADB did not answer before the timeout. Screenshot and tap workflows need ADB.",
                path=executable,
                detail=str(error),
            )
        except ProbeUnavailable as error:
            return HealthCapability(
                id="adb",
                title="ADB automation",
                state=CapabilityState.UNAVAILABLE,
                summary="ADB could not be started. Screenshot and tap workflows need ADB.",
                path=executable,
                detail=str(error),
                actions=(INSTALL_ADB,),
            )
        version = _first_version(version_result.stdout or version_result.stderr)
        devices = _parse_adb_devices(devices_result.stdout)
        ready_serials = {device_serial for device_serial, state in devices if state == "device"}
        if serial and serial not in ready_serials:
            return HealthCapability(
                id="adb",
                title="ADB automation",
                state=CapabilityState.DEGRADED,
                summary="The configured ADB device is not connected and ready.",
                path=executable,
                version=version,
                detail=f"Configured device: {serial}",
                metadata={"configuredSerial": serial, "devices": devices},
            )
        if not ready_serials:
            return HealthCapability(
                id="adb",
                title="ADB automation",
                state=CapabilityState.DEGRADED,
                summary="ADB is installed, but no ready device is connected.",
                path=executable,
                version=version,
                metadata={"devices": devices},
            )
        return HealthCapability(
            id="adb",
            title="ADB automation",
            state=CapabilityState.READY,
            summary=f"ADB is ready with {len(ready_serials)} connected device(s).",
            path=executable,
            version=version,
            metadata={"configuredSerial": serial or None, "devices": devices},
        )

    def start_ollama(self) -> None:
        if self._ollama_json("/api/version") is not None:
            return
        executable = self.system.resolve_ollama()
        if not executable:
            raise ProbeUnavailable("Ollama is not installed.")
        self.system.start_hidden([executable, "serve"])
        for _ in range(self.ollama_start_attempts):
            self.system.sleep(self.ollama_start_interval)
            if self._ollama_json("/api/version") is not None:
                return
        raise ProbeTimeout("Ollama did not start before the timeout.")

    def pull_ollama_model(
        self,
        progress: Callable[[float | None, str], None],
        cancelled: Callable[[], bool],
    ) -> None:
        if self._ollama_json("/api/version") is None:
            raise ProbeUnavailable("Start Ollama before downloading the vision model.")
        self.system.pull_ollama_model(
            self.ollama_base_url,
            self.ollama_model,
            progress,
            cancelled,
        )

    def _ollama_json(self, path: str) -> Mapping[str, Any] | None:
        try:
            return self.system.get_json(f"{self.ollama_base_url}{path}", self.http_timeout)
        except (ProbeTimeout, ProbeUnavailable):
            return None


def _first_version(text: str) -> str | None:
    match = re.search(r"\d+(?:\.\d+){1,3}", text or "")
    return match.group(0) if match else None


def _version_tuple(value: str) -> tuple[int, ...]:
    match = re.search(r"\d+(?:\.\d+){1,3}", value or "")
    return tuple(int(part) for part in match.group(0).split(".")) if match else (0,)


def _parse_adb_devices(output: str) -> list[list[str]]:
    devices: list[list[str]] = []
    for raw_line in (output or "").splitlines():
        line = raw_line.strip()
        if not line or line.lower().startswith("list of devices"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            devices.append([parts[0], parts[1]])
    return devices


def _parse_nvidia_smi_rows(output: str) -> tuple[dict[str, str], ...]:
    if not output or len(output) > MAX_NVIDIA_SMI_OUTPUT:
        raise ProbeUnavailable("NVIDIA diagnostics returned no bounded adapter data.")
    adapters: list[dict[str, str]] = []
    try:
        rows = csv.reader(io.StringIO(output))
        for row in rows:
            if len(row) != 2:
                raise ProbeUnavailable("NVIDIA diagnostics returned malformed adapter data.")
            name, driver_version = (part.strip() for part in row)
            if (
                not name
                or not driver_version
                or len(name) > 160
                or len(driver_version) > 40
                or not name.isprintable()
                or not driver_version.isprintable()
            ):
                raise ProbeUnavailable("NVIDIA diagnostics returned malformed adapter data.")
            adapters.append({"name": name, "driverVersion": driver_version})
            if len(adapters) > MAX_NVIDIA_ADAPTERS:
                raise ProbeUnavailable("NVIDIA diagnostics returned too many adapters.")
    except csv.Error as error:
        raise ProbeUnavailable("NVIDIA diagnostics returned malformed adapter data.") from error
    if not adapters:
        raise ProbeUnavailable("NVIDIA diagnostics returned no adapter data.")
    return tuple(adapters)
