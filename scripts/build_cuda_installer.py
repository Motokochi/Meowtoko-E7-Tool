"""Build the hash-pinned, application-local CUDA component installer helper."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
DIST_ROOT = ROOT / "dist"
DEFAULT_DESTINATION = DIST_ROOT / "cuda-installer"
DOWNLOAD_CACHE = ROOT / ".build" / "downloads"
MANIFEST_NAME = "asset-manifest.json"
COMPONENT_REQUIREMENTS_NAME = "component-requirements.txt"
COMPONENT_REQUIREMENTS_SOURCE = ROOT / "requirements-cuda-component-lock.txt"
MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024
DISTLIB_CONSOLE_LAUNCHER = "Lib/site-packages/pip/_vendor/distlib/t64.exe"


@dataclass(frozen=True, slots=True)
class PinnedArchive:
    filename: str
    url: str
    sha256: str


CPYTHON_VERSION = "3.12.10"
CPYTHON_TAG = "cp312"
CPYTHON_ARCHIVE = PinnedArchive(
    filename="python-3.12.10-embed-amd64.zip",
    url="https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip",
    sha256="4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3",
)
PIP_VERSION = "26.1.2"
PIP_ARCHIVE = PinnedArchive(
    filename="pip-26.1.2-py3-none-any.whl",
    url=(
        "https://files.pythonhosted.org/packages/5d/95/"
        "6b5cb3461ea5673ba0995989746db58eb18b91b54dbf331e72f569540946/"
        "pip-26.1.2-py3-none-any.whl"
    ),
    sha256="382ff9f685ee3bc25864f820aa50505825f10f5458ffff07e30a6d96e5715cab",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(archive: PinnedArchive) -> Path:
    DOWNLOAD_CACHE.mkdir(parents=True, exist_ok=True)
    cached = DOWNLOAD_CACHE / archive.filename
    if cached.is_file() and _sha256(cached) == archive.sha256:
        return cached
    cached.unlink(missing_ok=True)
    request = urllib.request.Request(
        archive.url,
        headers={"User-Agent": "Meowtoko-E7-Tool-reproducible-resource-builder/1"},
    )
    temporary = cached.with_suffix(cached.suffix + ".download")
    temporary.unlink(missing_ok=True)
    total = 0
    try:
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise RuntimeError(f"Pinned download exceeds {MAX_DOWNLOAD_BYTES} bytes: {archive.url}")
                output.write(chunk)
        actual = _sha256(temporary)
        if actual != archive.sha256:
            raise RuntimeError(
                f"Pinned archive hash mismatch for {archive.filename}: expected {archive.sha256}, got {actual}"
            )
        temporary.replace(cached)
        return cached
    finally:
        temporary.unlink(missing_ok=True)


def _safe_extract(archive_path: Path, destination: Path) -> None:
    destination = destination.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            relative = PurePosixPath(info.filename)
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                raise RuntimeError(f"Unsafe path in pinned archive {archive_path.name}: {info.filename}")
            target = destination.joinpath(*relative.parts).resolve()
            if target != destination and destination not in target.parents:
                raise RuntimeError(f"Archive path escaped destination: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def _configure_isolated_site_packages(destination: Path) -> None:
    pth = destination / "python312._pth"
    if not pth.is_file():
        raise RuntimeError("The pinned CPython archive does not contain python312._pth")
    pth.write_text(
        "python312.zip\n.\nLib\\site-packages\nimport site\n",
        encoding="utf-8",
        newline="\n",
    )


def _file_inventory(destination: Path) -> list[dict[str, object]]:
    return [
        {
            "path": path.relative_to(destination).as_posix(),
            "sha256": _sha256(path),
            "size": path.stat().st_size,
        }
        for path in sorted(destination.rglob("*"))
        if path.is_file() and path.name != MANIFEST_NAME
    ]


def _component_requirements() -> tuple[bytes, list[dict[str, str]]]:
    content = COMPONENT_REQUIREMENTS_SOURCE.read_bytes()
    if b"\r" in content or not content.endswith(b"\n"):
        raise RuntimeError("CUDA component lock must use LF and end with a newline.")
    packages: list[dict[str, str]] = []
    for raw in content.decode("utf-8").splitlines():
        parts = raw.split("==")
        if len(parts) != 2 or not all(parts) or "[" in parts[0]:
            raise RuntimeError(f"CUDA component lock entry is not an exact package pin: {raw}")
        packages.append({"name": parts[0], "version": parts[1]})
    if not packages or packages[0] != {"name": "cupy-cuda13x", "version": "14.1.1"}:
        raise RuntimeError("CUDA component lock must start with cupy-cuda13x==14.1.1.")
    if len({record["name"].lower() for record in packages}) != len(packages):
        raise RuntimeError("CUDA component lock contains a duplicate package.")
    return content, packages


def _valid_existing(destination: Path) -> dict[str, object] | None:
    manifest_path = destination / MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    expected = {
        "schemaVersion": 1,
        "assetId": "e7.cuda-installer",
        "layout": "cuda-installer/python.exe",
        "architecture": "x64",
    }
    if not isinstance(manifest, dict) or any(manifest.get(key) != value for key, value in expected.items()):
        return None
    python = manifest.get("python")
    installer = manifest.get("installer")
    component = manifest.get("component")
    requirements_content, requirements_packages = _component_requirements()
    if (
        not isinstance(python, dict)
        or python.get("version") != CPYTHON_VERSION
        or python.get("abiTag") != CPYTHON_TAG
        or python.get("sourceSha256") != CPYTHON_ARCHIVE.sha256
        or not isinstance(installer, dict)
        or installer.get("version") != PIP_VERSION
        or installer.get("sourceSha256") != PIP_ARCHIVE.sha256
        or installer.get("consoleLauncherResource") != DISTLIB_CONSOLE_LAUNCHER
        or not isinstance(component, dict)
        or component.get("displayPackage") != "cupy-cuda13x[ctk]==14.1.1"
        or component.get("requirements") != COMPONENT_REQUIREMENTS_NAME
        or component.get("requirementsSha256") != hashlib.sha256(requirements_content).hexdigest()
        or component.get("packages") != requirements_packages
    ):
        return None
    declared = manifest.get("files")
    if not isinstance(declared, list) or declared != _file_inventory(destination):
        return None
    executables = {
        record["path"].lower()
        for record in declared
        if isinstance(record.get("path"), str) and record["path"].lower().endswith(".exe")
    }
    if executables != {"python.exe", DISTLIB_CONSOLE_LAUNCHER.lower()}:
        return None
    return manifest


def build(destination: Path = DEFAULT_DESTINATION) -> None:
    cpython_archive = _download(CPYTHON_ARCHIVE)
    pip_archive = _download(PIP_ARCHIVE)
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    requirements_content, requirements_packages = _component_requirements()
    existing = _valid_existing(destination)
    if existing is not None:
        print(
            "E7_CUDA_INSTALLER_BUILD_OK "
            f"python={CPYTHON_VERSION} abi={CPYTHON_TAG} pip={PIP_VERSION} "
            f"files={len(existing['files'])} reused=1"
        )
        return
    with tempfile.TemporaryDirectory(prefix="cuda-installer-", dir=destination.parent) as temporary:
        staging = Path(temporary)
        _safe_extract(cpython_archive, staging)
        site_packages = staging / "Lib" / "site-packages"
        site_packages.mkdir(parents=True)
        _safe_extract(pip_archive, site_packages)
        _configure_isolated_site_packages(staging)
        (staging / COMPONENT_REQUIREMENTS_NAME).write_bytes(requirements_content)

        # The fixed helper is console-free when launched by Meowtoko E7 Tool. Pip still
        # needs its x64 console-launcher resource while installing the pinned
        # graph because several wheels expose console entry points. Keep only
        # that internal data executable; remove GUI, 32-bit, and ARM launchers.
        (staging / "pythonw.exe").unlink(missing_ok=True)
        console_launcher = staging / Path(DISTLIB_CONSOLE_LAUNCHER)
        for launcher in (site_packages / "pip" / "_vendor" / "distlib").glob("*.exe"):
            if launcher.resolve() != console_launcher.resolve():
                launcher.unlink()
        if not (staging / "python.exe").is_file():
            raise RuntimeError("The pinned CPython helper does not contain python.exe")
        if not (site_packages / "pip" / "__main__.py").is_file():
            raise RuntimeError("The pinned pip wheel did not provide pip/__main__.py")
        if not console_launcher.is_file():
            raise RuntimeError("The pinned pip wheel did not provide its x64 console launcher resource")

        manifest = {
            "schemaVersion": 1,
            "assetId": "e7.cuda-installer",
            "layout": "cuda-installer/python.exe",
            "architecture": "x64",
            "python": {
                "implementation": "cpython",
                "version": CPYTHON_VERSION,
                "abiTag": CPYTHON_TAG,
                "license": "PSF-2.0",
                "sourceUrl": CPYTHON_ARCHIVE.url,
                "sourceSha256": CPYTHON_ARCHIVE.sha256,
            },
            "installer": {
                "name": "pip",
                "version": PIP_VERSION,
                "license": "MIT",
                "sourceUrl": PIP_ARCHIVE.url,
                "sourceSha256": PIP_ARCHIVE.sha256,
                "consoleLauncherResource": DISTLIB_CONSOLE_LAUNCHER,
            },
            "component": {
                "displayPackage": "cupy-cuda13x[ctk]==14.1.1",
                "requirements": COMPONENT_REQUIREMENTS_NAME,
                "requirementsSha256": hashlib.sha256(requirements_content).hexdigest(),
                "packages": requirements_packages,
                "dependencyResolution": "disabled-with-pip-no-deps",
                "source": "https://pypi.org/simple",
            },
            "purpose": "Installs only the fixed optional CUDA component into app-owned user data.",
            "files": _file_inventory(staging),
        }
        (staging / MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        if destination.exists():
            shutil.rmtree(destination)
        staging.replace(destination)

    print(
        "E7_CUDA_INSTALLER_BUILD_OK "
        f"python={CPYTHON_VERSION} abi={CPYTHON_TAG} pip={PIP_VERSION} "
        f"files={len(manifest['files'])}"
    )


if __name__ == "__main__":
    build()
