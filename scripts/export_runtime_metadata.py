"""Export exact frozen-runtime metadata and applicable license files."""

from __future__ import annotations

import importlib.metadata
import hashlib
import json
import platform
import shutil
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_TEMPLATE = ROOT / "desktop" / "resources" / "runtime"
THIRD_PARTY_NOTICES = ROOT / "docs" / "legal" / "THIRD_PARTY_NOTICES.md"
CHARACTER_DATA = ROOT / "src" / "optimizer" / "data" / "character_data"
CUDA_INSTALLER = ROOT / "dist" / "cuda-installer"
CUDA_INSTALLER_MANIFEST = CUDA_INSTALLER / "asset-manifest.json"
SOURCE_URLS = {
    "numpy": "https://github.com/numpy/numpy",
    "opencv-python": "https://github.com/opencv/opencv-python",
    "pillow": "https://github.com/python-pillow/Pillow",
    "pytesseract": "https://github.com/madmaze/pytesseract",
    "requests": "https://github.com/psf/requests",
    "scapy": "https://github.com/secdev/scapy",
    "packaging": "https://github.com/pypa/packaging",
    "urllib3": "https://github.com/urllib3/urllib3",
    "certifi": "https://github.com/certifi/python-certifi",
    "charset-normalizer": "https://github.com/jawah/charset_normalizer",
    "idna": "https://github.com/kjd/idna",
    "pyinstaller": "https://github.com/pyinstaller/pyinstaller",
    "altgraph": "https://github.com/ronaldoussoren/altgraph",
    "pefile": "https://github.com/erocarrera/pefile",
    "pyinstaller-hooks-contrib": "https://github.com/pyinstaller/pyinstaller-hooks-contrib",
    "pywin32-ctypes": "https://github.com/enthought/pywin32-ctypes",
    "setuptools": "https://github.com/pypa/setuptools",
}
LICENSE_OVERRIDES = {
    "opencv-python": "Apache-2.0",
    "pytesseract": "Apache-2.0",
}
CHARACTER_FILES = (
    "character-catalog-v1.json",
    "character-source-v1.json",
    "character-validation-v1.json",
    "manual-heroes-v1.json",
    "manifest-v1.json",
    "source/artifactdata.json",
    "source/herodata.json",
)
MIGRATION_AUTHORITIES = (
    ("settings", "src.core.settings_service"),
    ("inventory-store", "src.optimizer.data.inventory_repository"),
    ("optimizer-documents", "src.optimizer.data.schemas"),
    ("optimizer-profiles", "src.desktop.optimizer_profile_service"),
    ("result-schema", "src.optimizer.result_store.schema"),
    ("result-lifecycle", "src.optimizer.result_store.lifecycle"),
)


def normalized_name(value: str) -> str:
    return value.lower().replace("_", "-")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_text_sha256(path: Path) -> str:
    """Hash UTF-8 text with canonical LF newlines across Git checkouts."""

    content = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def requirements_pins(path: Path) -> list[tuple[str, str]]:
    pins: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("-r "):
            continue
        parts = line.split("==")
        if len(parts) != 2 or not all(parts):
            raise RuntimeError(f"Build dependency is not exactly pinned: {line}")
        pins.append((parts[0], parts[1]))
    return pins


def build_dependency_records() -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for name, pinned_version in requirements_pins(ROOT / "requirements-build.txt"):
        record, _distribution = package_record(name)
        if record["version"] != pinned_version:
            raise RuntimeError(
                f"Build dependency drift for {name}: expected {pinned_version}, got {record['version']}"
            )
        records.append(record)
    return records


def helper_manifest() -> dict[str, Any]:
    try:
        manifest = json.loads(CUDA_INSTALLER_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise RuntimeError(
            "The trusted CUDA installer helper must be built before backend metadata export."
        ) from error
    expected = {
        "schemaVersion": 1,
        "assetId": "e7.cuda-installer",
        "layout": "cuda-installer/python.exe",
        "architecture": "x64",
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise RuntimeError("The trusted CUDA installer manifest identity drifted.")
    declared = {record["path"]: record for record in manifest.get("files", [])}
    actual = {
        path.relative_to(CUDA_INSTALLER).as_posix(): path
        for path in CUDA_INSTALLER.rglob("*")
        if path.is_file() and path != CUDA_INSTALLER_MANIFEST
    }
    if set(declared) != set(actual):
        raise RuntimeError("The trusted CUDA installer file inventory drifted.")
    for relative, path in actual.items():
        record = declared[relative]
        if record.get("size") != path.stat().st_size or record.get("sha256") != sha256(path):
            raise RuntimeError(f"The trusted CUDA installer failed integrity verification: {relative}")
    return manifest


def character_data_records() -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for relative in CHARACTER_FILES:
        path = CHARACTER_DATA / relative
        if not path.is_file():
            raise RuntimeError(f"Required character/artifact data is missing: {path}")
        records.append({
            "path": f"backend/_internal/src/optimizer/data/character_data/{relative}",
            "sha256": normalized_text_sha256(path),
            "size": path.stat().st_size,
        })
    return records


def package_record(name: str) -> tuple[dict[str, str], importlib.metadata.Distribution]:
    distribution = importlib.metadata.distribution(name)
    metadata = distribution.metadata
    canonical = normalized_name(str(metadata.get("Name") or name))
    license_name = LICENSE_OVERRIDES.get(canonical)
    if not license_name:
        license_name = str(metadata.get("License-Expression") or metadata.get("License") or "UNKNOWN")
        if "\n" in license_name or len(license_name) > 100:
            license_name = "See packaged license files"
    return ({
        "name": str(metadata.get("Name") or name),
        "version": distribution.version,
        "license": license_name,
        "sourceUrl": SOURCE_URLS[canonical],
    }, distribution)


def copy_distribution_licenses(
    distribution: importlib.metadata.Distribution,
    destination: Path,
) -> int:
    copied = 0
    for entry in distribution.files or ():
        lowered_parts = tuple(part.lower() for part in entry.parts)
        filename = lowered_parts[-1]
        from_dist_info = bool(lowered_parts and lowered_parts[0].endswith(".dist-info"))
        is_license = (
            filename.startswith(("license", "copying", "notice"))
            or (from_dist_info and "licenses" in lowered_parts)
        )
        if not is_license:
            continue
        source = Path(distribution.locate_file(entry))
        if not source.is_file():
            continue
        relative_parts = entry.parts[1:] if entry.parts and entry.parts[0].endswith(".dist-info") else (entry.name,)
        target = destination.joinpath(*relative_parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied += 1
    return copied


def export(destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(RUNTIME_TEMPLATE, destination)
    shutil.copy2(THIRD_PARTY_NOTICES, destination / "THIRD_PARTY_NOTICES.md")

    manifest_path = destination / "manifest.json"
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    helper = helper_manifest()
    dependencies: list[dict[str, str]] = []
    license_root = destination / "licenses"

    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise RuntimeError(f"CPython license file is missing: {python_license}")
    python_license_target = license_root / "Python" / "LICENSE.txt"
    python_license_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(python_license, python_license_target)

    for name, pinned_version in requirements_pins(ROOT / "requirements-core.txt"):
        record, distribution = package_record(name)
        if record["version"] != pinned_version:
            raise RuntimeError(
                f"Runtime dependency drift for {name}: "
                f"expected {pinned_version}, got {record['version']}"
            )
        dependencies.append(record)
        copy_distribution_licenses(distribution, license_root / record["name"])

    pyinstaller_record, pyinstaller_distribution = package_record("PyInstaller")
    copy_distribution_licenses(pyinstaller_distribution, license_root / "PyInstaller")

    python_version = ".".join(str(part) for part in sys.version_info[:3])
    if sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 12) or sys.maxsize <= 2**32:
        raise RuntimeError("Release packaging requires 64-bit CPython 3.12.")
    package_metadata = json.loads((ROOT / "desktop" / "package.json").read_text(encoding="utf-8"))
    manifest["desktopBuild"] = {
        "platform": "win32",
        "architecture": "x64",
        "packageManager": package_metadata["packageManager"],
        "electronVersion": package_metadata["devDependencies"]["electron"],
        "forgeVersion": package_metadata["devDependencies"]["@electron-forge/cli"],
        "lockSha256": normalized_text_sha256(ROOT / "desktop" / "pnpm-lock.yaml"),
    }
    manifest["bundledExecutables"] = [{
        "id": helper["assetId"],
        "layout": helper["layout"],
        "architecture": helper["architecture"],
        "python": helper["python"],
        "installer": helper["installer"],
        "component": helper["component"],
        "manifest": "cuda-installer/asset-manifest.json",
    }]
    manifest["bundledData"] = [{
        "id": "e7.optimizer.character-artifact-snapshot",
        "classification": "immutable-bundled-data",
        "layout": "backend/_internal/src/optimizer/data/character_data",
        "source": "Fribbels Epic 7 Optimizer offline-compatible data snapshot",
        "sourceUrl": "https://github.com/RexQian/Fribbels-Epic-7-Optimizer/tree/feat/offline",
        "files": character_data_records(),
    }]
    manifest["migrationAuthorities"] = [
        {"id": authority_id, "module": module}
        for authority_id, module in MIGRATION_AUTHORITIES
    ]
    manifest["optionalComponents"][0]["resolvedGraph"] = helper["component"]
    manifest["runtime"] = {
        "python": {
            "implementation": sys.implementation.name,
            "version": python_version,
            "abiTag": f"cp{sys.version_info.major}{sys.version_info.minor}",
            "cacheTag": sys.implementation.cache_tag,
            "architecture": platform.machine(),
            "license": "PSF-2.0",
            "sourceUrl": "https://github.com/python/cpython",
        },
        "dependencies": dependencies,
        "buildTool": pyinstaller_record,
        "buildDependencies": build_dependency_records(),
        "licensesPath": "licenses",
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(
        "E7_RUNTIME_METADATA_OK "
        f"python={manifest['runtime']['python']['version']} packages={len(dependencies)} "
        f"build={len(manifest['runtime']['buildDependencies'])} data={len(CHARACTER_FILES)} helper=1"
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: export_runtime_metadata.py <destination>")
    export(Path(sys.argv[1]).resolve())
