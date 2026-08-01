"""Verify the packaged CUDA installer helper without system Python or Node."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HELPER = ROOT / "dist" / "cuda-installer"
MANIFEST_NAME = "asset-manifest.json"
DISTLIB_CONSOLE_LAUNCHER = "Lib/site-packages/pip/_vendor/distlib/t64.exe"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise AssertionError(f"CUDA installer manifest is unreadable: {path}") from error
    if not isinstance(value, Mapping):
        raise AssertionError("CUDA installer manifest must be an object.")
    return value


def verify_files(helper: Path) -> Mapping[str, Any]:
    helper = helper.resolve()
    manifest = _manifest(helper / MANIFEST_NAME)
    if manifest.get("schemaVersion") != 1 or manifest.get("assetId") != "e7.cuda-installer":
        raise AssertionError("CUDA installer manifest identity is invalid.")
    if manifest.get("layout") != "cuda-installer/python.exe":
        raise AssertionError("CUDA installer manifest layout drifted.")
    if manifest.get("architecture") != "x64":
        raise AssertionError("CUDA installer architecture drifted.")
    python = manifest.get("python")
    installer = manifest.get("installer")
    if not isinstance(python, Mapping) or python.get("abiTag") != "cp312":
        raise AssertionError("CUDA installer Python ABI is not cp312.")
    if (
        not isinstance(installer, Mapping)
        or installer.get("name") != "pip"
        or installer.get("consoleLauncherResource") != DISTLIB_CONSOLE_LAUNCHER
    ):
        raise AssertionError("CUDA installer pip metadata is invalid.")
    component = manifest.get("component")
    if (
        not isinstance(component, Mapping)
        or component.get("displayPackage") != "cupy-cuda13x[ctk]==14.1.1"
        or component.get("requirements") != "component-requirements.txt"
        or component.get("requirementsSha256")
        != "c39d7b64e59aa31e7125a6efebf4112f8591e42f114f72269f90dec7b0544ed4"
        or component.get("dependencyResolution") != "disabled-with-pip-no-deps"
        or not isinstance(component.get("packages"), list)
        or len(component["packages"]) != 11
    ):
        raise AssertionError("CUDA installer fixed component graph is invalid.")
    declared = manifest.get("files")
    if not isinstance(declared, list) or not declared:
        raise AssertionError("CUDA installer file inventory is empty.")

    declared_by_path: dict[str, Mapping[str, Any]] = {}
    for record in declared:
        if not isinstance(record, Mapping) or not isinstance(record.get("path"), str):
            raise AssertionError("CUDA installer file record is invalid.")
        relative = record["path"]
        if relative in declared_by_path or "\\" in relative or Path(relative).is_absolute():
            raise AssertionError(f"CUDA installer file path is invalid: {relative!r}")
        declared_by_path[relative] = record

    actual = {
        path.relative_to(helper).as_posix(): path
        for path in helper.rglob("*")
        if path.is_file() and path.name != MANIFEST_NAME
    }
    if set(actual) != set(declared_by_path):
        missing = sorted(set(declared_by_path) - set(actual))
        orphaned = sorted(set(actual) - set(declared_by_path))
        raise AssertionError(f"CUDA installer inventory drift: missing={missing!r}, orphaned={orphaned!r}")
    for relative, path in actual.items():
        record = declared_by_path[relative]
        if record.get("size") != path.stat().st_size or record.get("sha256") != _sha256(path):
            raise AssertionError(f"CUDA installer file failed integrity verification: {relative}")

    executables = sorted(relative.lower() for relative in actual if relative.lower().endswith(".exe"))
    if executables != [DISTLIB_CONSOLE_LAUNCHER.lower(), "python.exe"]:
        raise AssertionError(f"Unexpected CUDA installer executables: {executables!r}")
    forbidden = [
        relative for relative in actual
        if relative.lower().endswith((".bat", ".cmd", ".ps1", ".pyo", ".pyc", ".map"))
        or "__pycache__" in relative.lower().split("/")
        or any(token in relative.lower() for token in ("cupy", "nvidia"))
    ]
    if forbidden:
        raise AssertionError(f"Forbidden CUDA installer content: {forbidden!r}")
    return manifest


def _write_console_smoke_wheel(path: Path) -> None:
    module = "e7_cuda_helper_smoke.py"
    dist_info = "e7_cuda_helper_smoke-1.0.dist-info"
    payloads = {
        module: b"def main():\n    print('E7_CUDA_HELPER_CONSOLE_OK')\n",
        f"{dist_info}/METADATA": (
            b"Metadata-Version: 2.1\nName: e7-cuda-helper-smoke\nVersion: 1.0\n"
        ),
        f"{dist_info}/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: Meowtoko E7 Tool verifier\n"
            b"Root-Is-Purelib: true\nTag: py3-none-any\n"
        ),
        f"{dist_info}/entry_points.txt": (
            b"[console_scripts]\ne7-cuda-helper-smoke=e7_cuda_helper_smoke:main\n"
        ),
    }
    record = "".join(f"{name},,\n" for name in payloads)
    payloads[f"{dist_info}/RECORD"] = (record + f"{dist_info}/RECORD,,\n").encode("utf-8")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in payloads.items():
            archive.writestr(name, payload)


def verify_runtime(helper: Path, manifest: Mapping[str, Any]) -> None:
    executable = helper.resolve() / "python.exe"
    with tempfile.TemporaryDirectory(prefix="e7-cuda-helper-") as temporary:
        isolated = Path(temporary).resolve()
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.upper().startswith(("PYTHON", "PIP"))
            and key.upper() not in {"VIRTUAL_ENV", "CONDA_PREFIX", "NODE_PATH"}
        }
        environment.update({
            "APPDATA": str(isolated / "appdata"),
            "LOCALAPPDATA": str(isolated / "localappdata"),
            "USERPROFILE": str(isolated / "profile"),
            "PATH": str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"),
            "PIP_NO_INPUT": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        })
        probe = subprocess.run(
            [
                str(executable),
                "-I",
                "-B",
                "-c",
                (
                    "import json,pip,ssl,sys;"
                    "print(json.dumps({'version':'.'.join(map(str,sys.version_info[:3])),"
                    "'pip':pip.__version__,'prefix':sys.prefix,'openssl':ssl.OPENSSL_VERSION}))"
                ),
            ],
            cwd=isolated,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        if probe.returncode != 0:
            raise AssertionError(f"CUDA installer runtime probe failed: {probe.stderr}")
        runtime = json.loads(probe.stdout)
        python = manifest["python"]
        installer = manifest["installer"]
        if runtime["version"] != python["version"] or runtime["pip"] != installer["version"]:
            raise AssertionError(f"CUDA installer version drift: {runtime!r}")
        if Path(runtime["prefix"]).resolve() != helper.resolve():
            raise AssertionError("CUDA installer runtime escaped its application-local prefix.")
        if not str(runtime["openssl"]).startswith("OpenSSL "):
            raise AssertionError("CUDA installer helper has no usable TLS runtime.")

        smoke_wheel = isolated / "e7_cuda_helper_smoke-1.0-py3-none-any.whl"
        smoke_target = isolated / "installed"
        _write_console_smoke_wheel(smoke_wheel)
        install = subprocess.run(
            [
                str(executable),
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
                "--target",
                str(smoke_target),
                str(smoke_wheel),
            ],
            cwd=isolated,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        if install.returncode != 0:
            raise AssertionError(
                "CUDA installer could not create a console entry point: "
                f"{install.stdout}\n{install.stderr}"
            )
        generated = list(smoke_target.rglob("e7-cuda-helper-smoke.exe"))
        if len(generated) != 1:
            raise AssertionError(
                f"CUDA installer console entry point was not generated exactly once: {generated!r}"
            )


def run(helper: Path) -> None:
    manifest = verify_files(helper)
    verify_runtime(helper, manifest)
    print(
        "E7_CUDA_INSTALLER_OK "
        f"python={manifest['python']['version']} abi={manifest['python']['abiTag']} "
        f"pip={manifest['installer']['version']} files={len(manifest['files'])} isolated=1"
    )


if __name__ == "__main__":
    selected = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_HELPER
    run(selected)
