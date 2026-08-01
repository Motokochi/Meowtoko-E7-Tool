"""Versioned, app-owned installation boundary for optional CUDA components."""

from __future__ import annotations

import ctypes
import importlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CUDA_COMPONENT_SCHEMA_VERSION = 1
CUDA_COMPONENT_ID = "e7.cuda.cupy13"
CUDA_COMPONENT_VERSION = "14.1.1"
CUDA_COMPONENT_REVISION = "cupy-cuda13x-14.1.1-cuda13.3.1-graph-1"
CUDA_COMPONENT_PACKAGE = "cupy-cuda13x[ctk]==14.1.1"
CUDA_COMPONENT_GRAPH_SHA256 = "c39d7b64e59aa31e7125a6efebf4112f8591e42f114f72269f90dec7b0544ed4"
CUDA_COMPONENT_SOURCE = "https://pypi.org/simple"
CUDA_COMPONENT_DIRECTORY = "cuda-cupy13"
CUDA_COMPONENT_MANIFEST = "component-manifest.json"
CUDA_COMPONENT_REQUIREMENTS = "component-requirements.txt"
CUDA_COMPONENT_DOWNLOAD_NOTE = "Potentially over 1 GB; installed size can be several GB."
CUDA_COMPONENT_INSTALL_TIMEOUT_SECONDS = 30 * 60
CUDA_COMPONENT_VERIFY_TIMEOUT_SECONDS = 60
CUDA_COMPONENT_CUDA_DLL_DIRECTORY = Path("nvidia/cu13/bin/x86_64")
CUDA_COMPONENT_NVRTC_DLL = "nvrtc64_130_0.dll"
CUDA_COMPONENT_REQUIRED_PATHS = (
    Path("cupy/__init__.py"),
    Path("cupy/cuda/__init__.py"),
    Path("cuda/pathfinder/__init__.py"),
    Path("cuda_pathfinder-1.5.6.dist-info/METADATA"),
    Path("cupy_cuda13x-14.1.1.dist-info/METADATA"),
    CUDA_COMPONENT_CUDA_DLL_DIRECTORY / CUDA_COMPONENT_NVRTC_DLL,
)
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

_TRANSACTION_DIRECTORY_PATTERN = re.compile(
    rf"^\.{re.escape(CUDA_COMPONENT_DIRECTORY)}-[0-9a-f]{{32}}\.(staging|backup)$"
)

_CUDA_DLL_RESOURCES: dict[str, tuple[object, object]] = {}

ProgressSink = Callable[[float | None, str], None]
CancellationPredicate = Callable[[], bool]
CommandRunner = Callable[[Sequence[str], float, CancellationPredicate], None]
InstallerResolver = Callable[[], str | None]
RequirementsResolver = Callable[[str], str | None]
RequirementsValidator = Callable[[str], bool]


class CudaComponentError(RuntimeError):
    """A compact setup failure that is safe to display across the desktop boundary."""


class CudaComponentUnavailable(CudaComponentError):
    pass


class CudaComponentCancelled(CudaComponentError):
    pass


@dataclass(frozen=True, slots=True)
class CudaComponentStatus:
    installed: bool
    installer_available: bool
    revision: str | None
    python_tag: str

    def to_dict(self) -> dict[str, object]:
        return {
            "installed": self.installed,
            "installerAvailable": self.installer_available,
            "revision": self.revision,
            "pythonTag": self.python_tag,
            "package": CUDA_COMPONENT_PACKAGE,
            "source": CUDA_COMPONENT_SOURCE,
            "downloadNote": CUDA_COMPONENT_DOWNLOAD_NOTE,
        }


def _python_tag() -> str:
    return f"cp{sys.version_info.major}{sys.version_info.minor}"


def component_directory(user_data_dir: str | Path) -> Path:
    return Path(user_data_dir).resolve() / "components" / CUDA_COMPONENT_DIRECTORY


def _manifest_payload() -> dict[str, object]:
    return {
        "schemaVersion": CUDA_COMPONENT_SCHEMA_VERSION,
        "componentId": CUDA_COMPONENT_ID,
        "revision": CUDA_COMPONENT_REVISION,
        "package": CUDA_COMPONENT_PACKAGE,
        "requirementsSha256": CUDA_COMPONENT_GRAPH_SHA256,
        "pythonTag": _python_tag(),
        "source": CUDA_COMPONENT_SOURCE,
    }


def _read_manifest(directory: Path) -> Mapping[str, Any] | None:
    try:
        value = json.loads((directory / CUDA_COMPONENT_MANIFEST).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, ValueError):
        return None
    return value if isinstance(value, Mapping) else None


def valid_component_directory(directory: Path) -> bool:
    manifest = _read_manifest(directory)
    return bool(
        manifest is not None
        and dict(manifest) == _manifest_payload()
        and all(
            (directory / relative).is_file()
            for relative in CUDA_COMPONENT_REQUIRED_PATHS
        )
    )


def _is_owned_transaction_directory(path: Path, parent: Path) -> bool:
    """Reject links and any path outside the exact app-owned component parent."""

    if path.parent != parent or _TRANSACTION_DIRECTORY_PATTERN.fullmatch(path.name) is None:
        return False
    try:
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            return False
        return path.is_dir()
    except OSError:
        return False


def _transaction_directories(parent: Path) -> tuple[Path, ...]:
    try:
        entries = tuple(parent.iterdir())
    except (FileNotFoundError, OSError):
        return ()
    return tuple(path for path in entries if _is_owned_transaction_directory(path, parent))


def _remove_transaction_directory(path: Path, parent: Path) -> bool:
    """Best-effort cleanup; loaded Windows native modules may keep files locked."""

    if not _is_owned_transaction_directory(path, parent):
        return False
    try:
        shutil.rmtree(path)
    except OSError:
        return False
    return not path.exists()


def recover_cuda_component_transactions(user_data_dir: str | Path) -> bool:
    """Restore an interrupted pre-publication backup and reap safe stale siblings."""

    directory = component_directory(user_data_dir)
    parent = directory.parent
    candidates = _transaction_directories(parent)
    restored = False
    if not directory.exists():
        recoverable = tuple(
            sorted(
                (path for path in candidates if valid_component_directory(path)),
                key=lambda path: (path.name.endswith(".backup"), path.stat().st_mtime_ns),
                reverse=True,
            )
        )
        for candidate in recoverable:
            try:
                candidate.replace(directory)
            except OSError:
                continue
            restored = True
            break
    if valid_component_directory(directory):
        for candidate in candidates:
            if candidate.exists():
                _remove_transaction_directory(candidate, parent)
    return restored


def activate_cuda_component(user_data_dir: str | Path) -> bool:
    """Activate only the exact app-owned component revision for this Python ABI."""

    directory = component_directory(user_data_dir)
    if not valid_component_directory(directory):
        return False
    if os.name == "nt":
        cuda_root = directory / "nvidia" / "cu13"
        cuda_dll_directory = directory / CUDA_COMPONENT_CUDA_DLL_DIRECTORY
        nvrtc = cuda_dll_directory / CUDA_COMPONENT_NVRTC_DLL
        if cuda_dll_directory.is_dir() and nvrtc.is_file():
            key = str(cuda_dll_directory).casefold()
            if key not in _CUDA_DLL_RESOURCES:
                try:
                    directory_handle = os.add_dll_directory(str(cuda_dll_directory))
                    nvrtc_handle = ctypes.WinDLL(str(nvrtc))
                except OSError:
                    try:
                        directory_handle.close()
                    except (NameError, OSError):
                        pass
                    return False
                # Both handles must live for the process lifetime. Preloading
                # NVRTC lets cuda-pathfinder derive the unified wheel root;
                # --target directories are not returned by site.getsitepackages().
                _CUDA_DLL_RESOURCES[key] = (directory_handle, nvrtc_handle)
            os.environ["CUDA_PATH"] = str(cuda_root)
            os.environ["CUDA_HOME"] = str(cuda_root)
            current_path = os.environ.get("PATH", "")
            entries = [entry for entry in current_path.split(os.pathsep) if entry]
            if key not in {entry.casefold() for entry in entries}:
                os.environ["PATH"] = os.pathsep.join((str(cuda_dll_directory), *entries))
            os.environ["CUPY_CACHE_DIR"] = str(Path(user_data_dir).resolve() / "cache" / "cupy")
    value = str(directory)
    if value not in sys.path:
        sys.path.insert(0, value)
    importlib.invalidate_caches()
    return True


def resolve_cuda_installer_python(
    environment: Mapping[str, str] | None = None,
    *,
    frozen: bool | None = None,
    executable: str | None = None,
    exists: Callable[[str], bool] = os.path.isfile,
) -> str | None:
    """Select a fixed helper; packaged builds never fall back to system Python."""

    env = os.environ if environment is None else environment
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    resources = env.get("E7_RESOURCES_PATH")
    packaged_helper = str(Path(resources) / "cuda-installer" / "python.exe") if resources else None
    candidates: list[str | None] = [packaged_helper]
    if not is_frozen:
        candidates.extend([env.get("E7_CUDA_INSTALLER_PYTHON"), executable or sys.executable])
    for candidate in candidates:
        if candidate and exists(candidate):
            return str(Path(candidate).resolve())
    return None


def resolve_cuda_component_requirements(
    installer: str,
    *,
    frozen: bool | None = None,
    exists: Callable[[str], bool] = os.path.isfile,
) -> str | None:
    """Resolve only the packaged lock, or the repository lock in development."""

    adjacent = Path(installer).resolve().parent / CUDA_COMPONENT_REQUIREMENTS
    if exists(str(adjacent)):
        return str(adjacent)
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if not is_frozen:
        repository_lock = Path(__file__).resolve().parents[2] / "requirements-cuda-component-lock.txt"
        if exists(str(repository_lock)):
            return str(repository_lock)
    return None


def valid_cuda_component_requirements(path: str) -> bool:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest() == CUDA_COMPONENT_GRAPH_SHA256
    except OSError:
        return False


def _stop_process(process: subprocess.Popen[str]) -> None:
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def run_setup_command(
    command: Sequence[str],
    timeout: float,
    cancelled: CancellationPredicate,
) -> None:
    """Run one fixed setup command without a shell, inherited pip config, or a window."""

    if cancelled():
        raise CudaComponentCancelled("GPU component setup was cancelled. CPU mode remains available.")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("PIP_", "PYTHON"))
        and key.upper() not in {"VIRTUAL_ENV", "CONDA_PREFIX"}
    }
    environment.update({"PIP_NO_INPUT": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PYTHONNOUSERSITE": "1"})
    try:
        process = subprocess.Popen(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=environment,
            shell=False,
            creationflags=CREATE_NO_WINDOW,
        )
    except OSError as error:
        raise CudaComponentUnavailable("The trusted GPU component installer could not start.") from error

    deadline = time.monotonic() + timeout
    while True:
        try:
            process.communicate(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            if cancelled():
                _stop_process(process)
                raise CudaComponentCancelled("GPU component setup was cancelled. CPU mode remains available.")
            if time.monotonic() >= deadline:
                _stop_process(process)
                raise CudaComponentError("GPU component setup timed out. CPU mode remains available.")
    if process.returncode != 0:
        raise CudaComponentError(
            "GPU component setup did not complete. Check network access and the NVIDIA driver, then retry."
        )


_VERIFY_SCRIPT = (
    "import importlib,sys;"
    "sys.path.insert(0,sys.argv[1]);"
    "module=importlib.import_module('cupy');"
    "tag=f'cp{sys.version_info.major}{sys.version_info.minor}';"
    "raise SystemExit(0 if module.__version__==sys.argv[2] and tag==sys.argv[3] else 2)"
)


class CudaComponentManager:
    """Install or repair one exact component into an atomic app-owned directory."""

    def __init__(
        self,
        user_data_dir: str | Path,
        *,
        environment: Mapping[str, str] | None = None,
        installer_resolver: InstallerResolver | None = None,
        requirements_resolver: RequirementsResolver = resolve_cuda_component_requirements,
        requirements_validator: RequirementsValidator = valid_cuda_component_requirements,
        runner: CommandRunner = run_setup_command,
    ) -> None:
        self.user_data_dir = Path(user_data_dir).resolve()
        self.environment = dict(os.environ if environment is None else environment)
        self.installer_resolver = installer_resolver or (
            lambda: resolve_cuda_installer_python(self.environment)
        )
        self.requirements_resolver = requirements_resolver
        self.requirements_validator = requirements_validator
        self.runner = runner

    @property
    def directory(self) -> Path:
        return component_directory(self.user_data_dir)

    def status(self) -> CudaComponentStatus:
        installed = valid_component_directory(self.directory)
        installer = self.installer_resolver()
        return CudaComponentStatus(
            installed=installed,
            installer_available=bool(
                installer
                and (requirements := self.requirements_resolver(installer))
                and self.requirements_validator(requirements)
            ),
            revision=CUDA_COMPONENT_REVISION if installed else None,
            python_tag=_python_tag(),
        )

    def install_or_repair(
        self,
        progress: ProgressSink,
        cancelled: CancellationPredicate,
    ) -> None:
        installer = self.installer_resolver()
        requirements = self.requirements_resolver(installer) if installer else None
        if not installer or not requirements or not self.requirements_validator(requirements):
            raise CudaComponentUnavailable(
                "This build does not include the trusted GPU component installer. CPU mode remains available."
            )
        recover_cuda_component_transactions(self.user_data_dir)
        parent = self.directory.parent
        parent.mkdir(parents=True, exist_ok=True)
        staging = parent / f".{CUDA_COMPONENT_DIRECTORY}-{uuid.uuid4().hex}.staging"
        backup = parent / f".{CUDA_COMPONENT_DIRECTORY}-{uuid.uuid4().hex}.backup"
        staging.mkdir()
        replaced_existing = False
        committed = False
        rollback_restored = False
        try:
            progress(0.05, "Preparing the optional GPU component download…")
            install_command = (
                installer,
                "-I",
                "-B",
                "-m",
                "pip",
                "install",
                "--isolated",
                "--disable-pip-version-check",
                "--no-input",
                "--only-binary=:all:",
                "--no-deps",
                "--upgrade",
                "--index-url",
                CUDA_COMPONENT_SOURCE,
                "--target",
                str(staging),
                "--requirement",
                requirements,
            )
            self.runner(install_command, CUDA_COMPONENT_INSTALL_TIMEOUT_SECONDS, cancelled)
            if cancelled():
                raise CudaComponentCancelled("GPU component setup was cancelled. CPU mode remains available.")
            if not all(
                (staging / relative).is_file()
                for relative in CUDA_COMPONENT_REQUIRED_PATHS
            ):
                raise CudaComponentError("The GPU component installer returned an incomplete component.")

            progress(0.85, "Verifying the pinned CuPy component…")
            verify_command = (
                installer,
                "-I",
                "-B",
                "-c",
                _VERIFY_SCRIPT,
                str(staging),
                CUDA_COMPONENT_VERSION,
                _python_tag(),
            )
            self.runner(verify_command, CUDA_COMPONENT_VERIFY_TIMEOUT_SECONDS, cancelled)
            (staging / CUDA_COMPONENT_MANIFEST).write_text(
                json.dumps(_manifest_payload(), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            if self.directory.exists():
                self.directory.replace(backup)
                replaced_existing = True
            staging.replace(self.directory)
            committed = True
            activate_cuda_component(self.user_data_dir)
            progress(1.0, "Optional GPU components are installed. Checking CUDA readiness…")
        except Exception:
            if not committed and replaced_existing and backup.exists():
                if self.directory.exists():
                    try:
                        shutil.rmtree(self.directory)
                    except OSError:
                        pass
                if not self.directory.exists():
                    backup.replace(self.directory)
                    rollback_restored = True
            raise
        finally:
            if staging.exists():
                _remove_transaction_directory(staging, parent)
            if backup.exists() and (committed or rollback_restored or not replaced_existing):
                _remove_transaction_directory(backup, parent)


__all__ = [
    "CUDA_COMPONENT_DOWNLOAD_NOTE",
    "CUDA_COMPONENT_GRAPH_SHA256",
    "CUDA_COMPONENT_PACKAGE",
    "CUDA_COMPONENT_REQUIRED_PATHS",
    "CUDA_COMPONENT_REQUIREMENTS",
    "CUDA_COMPONENT_REVISION",
    "CUDA_COMPONENT_SOURCE",
    "CudaComponentCancelled",
    "CudaComponentError",
    "CudaComponentManager",
    "CudaComponentStatus",
    "CudaComponentUnavailable",
    "activate_cuda_component",
    "component_directory",
    "recover_cuda_component_transactions",
    "resolve_cuda_installer_python",
    "resolve_cuda_component_requirements",
    "run_setup_command",
    "valid_cuda_component_requirements",
    "valid_component_directory",
]
