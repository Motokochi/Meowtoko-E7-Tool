"""Audit and run the P09-T04 supported-environment correctness campaign.

The campaign deliberately runs each Python category in a fresh subprocess and
redirects every user-data authority to a temporary directory. CUDA coverage in
this command is hardware-independent; real-device performance is measured
separately.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_MANIFEST = ROOT / "dist" / "runtime" / "manifest.json"
# Every root Python test module has exactly one primary correctness owner.  A
# test can support several contracts, but assigning it once makes omissions and
# accidental double-counting mechanically visible.
CATEGORY_MODULES: dict[str, tuple[str, ...]] = {
    "legacy": (
        "tests/test_analyzer_backend_integration.py",
        "tests/test_analyzer_service.py",
        "tests/test_automation_backend.py",
        "tests/test_badge_detection.py",
        "tests/test_candidates.py",
        "tests/test_config_manager.py",
        "tests/test_desktop_health.py",
        "tests/test_desktop_protocol.py",
        "tests/test_enhancement_automator.py",
        "tests/test_enhancement_desktop.py",
        "tests/test_enhancement_rules.py",
        "tests/test_optimizer_enums.py",
        "tests/test_optimizer_records.py",
        "tests/test_optimizer_schemas.py",
        "tests/test_orchestrator_parse.py",
        "tests/test_repository_layout.py",
        "tests/test_runtime_paths.py",
        "tests/test_settings_backend_restart.py",
        "tests/test_settings_preview.py",
        "tests/test_settings_service.py",
        "tests/test_workspace_paths.py",
    ),
    "import_inventory": (
        "tests/test_equipment_eligibility.py",
        "tests/test_fribbels_gear_txt_fixtures.py",
        "tests/test_fribbels_import_service.py",
        "tests/test_fribbels_merge.py",
        "tests/test_fribbels_parser.py",
        "tests/test_inventory_repository.py",
        "tests/test_import_inventory_benchmark.py",
        "tests/test_optimizer_inventory_backend_integration.py",
        "tests/test_optimizer_inventory_desktop.py",
    ),
    "character_modifiers": (
        "tests/test_artifact_repository.py",
        "tests/test_character_profiles.py",
        "tests/test_character_repository.py",
        "tests/test_character_snapshot.py",
        "tests/test_hero_modifier_repository.py",
        "tests/test_optimizer_profile_backend_integration.py",
        "tests/test_optimizer_profile_desktop.py",
        "tests/test_skill_context_repository.py",
    ),
    "stats_metrics": (
        "tests/test_derived_metrics.py",
        "tests/test_primary_stat_bounds.py",
        "tests/test_priority_scoring.py",
        "tests/test_set_evaluation.py",
        "tests/test_stat_aggregation.py",
    ),
    "cpu_search": (
        "tests/test_cartesian_enumeration.py",
        "tests/test_cpu_benchmark.py",
        "tests/test_cpu_capacity_benchmark.py",
        "tests/test_cpu_orchestration.py",
        "tests/test_exact_build_evaluation.py",
        "tests/test_match_counting.py",
        "tests/test_optimizer_search_backend_integration.py",
        "tests/test_optimizer_search_desktop.py",
        "tests/test_search_slot_arrays.py",
        "tests/test_set_pattern_compilation.py",
    ),
    "cuda_parity": (
        "tests/test_cuda_inputs.py",
        "tests/test_cuda_installer_resource.py",
        "tests/test_cuda_orchestration.py",
        "tests/test_cuda_packed.py",
        "tests/test_cuda_runtime.py",
        "tests/test_cuda_setup.py",
    ),
    "results": (
        "tests/test_optimizer_result_desktop.py",
        "tests/test_result_filtering.py",
        "tests/test_result_indexing.py",
        "tests/test_result_lifecycle.py",
        "tests/test_result_resolution.py",
        "tests/test_result_schema.py",
        "tests/test_result_storage.py",
        "tests/test_result_store_benchmark.py",
    ),
    "recovery": ("tests/test_persistence_recovery_matrix.py",),
}

DESKTOP_TEST_MODULES = (
    "desktop/src/analyzer.test.ts",
    "desktop/src/analyzer-center.test.tsx",
    "desktop/src/app-shell.test.tsx",
    "desktop/src/app.test.tsx",
    "desktop/src/backend-client.test.ts",
    "desktop/src/backend-launch.test.ts",
    "desktop/src/character-artwork.test.ts",
    "desktop/src/desktop-api.test.ts",
    "desktop/src/enhancement.test.ts",
    "desktop/src/enhancer-center.test.tsx",
    "desktop/src/health.test.ts",
    "desktop/src/health-center.test.tsx",
    "desktop/src/importer-center.test.tsx",
    "desktop/src/navigation.test.ts",
    "desktop/src/optimizer-center.test.tsx",
    "desktop/src/optimizer-inventory.test.ts",
    "desktop/src/optimizer-inventory-dialog.test.ts",
    "desktop/src/optimizer-profile.test.ts",
    "desktop/src/optimizer-profile-editor.test.tsx",
    "desktop/src/optimizer-results.test.tsx",
    "desktop/src/optimizer-search.test.tsx",
    "desktop/src/security-config.test.ts",
    "desktop/src/settings.test.ts",
    "desktop/src/settings-center.test.tsx",
    "desktop/src/squirrel-lifecycle.test.ts",
    "desktop/src/theme.test.ts",
    "desktop/src/update-center.test.tsx",
    "desktop/src/update-service.test.ts",
    "desktop/src/ui.test.tsx",
    "desktop/src/window-lifecycle.test.ts",
)

FIXTURE_AUTHORITIES = (
    "tests/fixtures/fribbels/manifest.json",
    "tests/fixtures/recovery/manifest.json",
    "src/optimizer/data/character_data/manifest-v1.json",
)

SUMMARY_PATTERN = re.compile(
    r"(?P<tests>\d+) passed(?:, (?P<subtests>\d+) subtests passed)? in (?P<seconds>[0-9.]+)s"
)


class CampaignError(RuntimeError):
    """Raised when the campaign cannot produce valid green evidence."""


def _canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _requirements(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("-r "):
            continue
        parts = line.split("==")
        if len(parts) != 2 or not all(parts):
            raise CampaignError(f"Requirement is not exactly pinned: {line}")
        name, version = parts
        canonical = _canonical_name(name)
        if canonical in pins:
            raise CampaignError(f"Duplicate requirement pin: {name}")
        pins[canonical] = version
    return pins


def _installed_versions(pins: dict[str, str]) -> dict[str, str]:
    installed: dict[str, str] = {}
    for name, expected in pins.items():
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError as error:
            raise CampaignError(f"Pinned distribution is not installed: {name}") from error
        if actual != expected:
            raise CampaignError(
                f"Installed distribution drift for {name}: expected {expected}, got {actual}"
            )
        installed[name] = actual
    return installed


def _runtime_records(records: Sequence[dict[str, Any]]) -> dict[str, str]:
    return {
        _canonical_name(str(record["name"])): str(record["version"])
        for record in records
    }


def audit_environment() -> dict[str, Any]:
    if sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 12):
        raise CampaignError("The supported correctness environment is CPython 3.12.")
    if sys.maxsize <= 2**32 or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise CampaignError("The supported correctness environment is 64-bit x64.")
    if sys.platform != "win32":
        raise CampaignError("P09 release correctness must run on Windows.")

    core_pins = _requirements(ROOT / "requirements-core.txt")
    build_pins = _requirements(ROOT / "requirements-build.txt")
    installed_core = _installed_versions(core_pins)
    installed_build = _installed_versions(build_pins)
    try:
        manifest = json.loads(RUNTIME_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise CampaignError("Frozen runtime metadata is missing or malformed.") from error

    runtime = manifest.get("runtime")
    if not isinstance(runtime, dict):
        raise CampaignError("Frozen runtime metadata has no runtime record.")
    python_record = runtime.get("python")
    if not isinstance(python_record, dict):
        raise CampaignError("Frozen runtime metadata has no Python record.")
    expected_python = platform.python_version()
    if python_record.get("version") != expected_python or python_record.get("abiTag") != "cp312":
        raise CampaignError("Frozen Python metadata drifted from the test interpreter.")
    if _runtime_records(runtime.get("dependencies", [])) != core_pins:
        raise CampaignError("Frozen runtime dependency metadata drifted from core pins.")
    if _runtime_records(runtime.get("buildDependencies", [])) != build_pins:
        raise CampaignError("Frozen build dependency metadata drifted from build pins.")
    build_tool = runtime.get("buildTool")
    if not isinstance(build_tool, dict) or (
        _canonical_name(str(build_tool.get("name"))) != "pyinstaller"
        or str(build_tool.get("version")) != build_pins.get("pyinstaller")
    ):
        raise CampaignError("Frozen PyInstaller metadata drifted from build pins.")

    return {
        "pythonImplementation": platform.python_implementation(),
        "pythonVersion": expected_python,
        "pythonAbi": str(python_record["abiTag"]),
        "numpyVersion": installed_core["numpy"],
        "pyinstallerVersion": installed_build["pyinstaller"],
        "pytestVersion": importlib.metadata.version("pytest"),
        "operatingSystem": platform.system(),
        "operatingSystemRelease": platform.release(),
        "architecture": platform.machine(),
        "coreRequirements": len(core_pins),
        "buildRequirements": len(build_pins),
        "runtimeManifestSha256": _sha256(RUNTIME_MANIFEST),
        "requirementsCoreSha256": _sha256(ROOT / "requirements-core.txt"),
        "requirementsBuildSha256": _sha256(ROOT / "requirements-build.txt"),
    }


def audit_matrix() -> dict[str, int]:
    discovered_python = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "tests").glob("test_*.py")
    }
    declared = [module for modules in CATEGORY_MODULES.values() for module in modules]
    duplicates = sorted(module for module, count in Counter(declared).items() if count != 1)
    missing = sorted(discovered_python - set(declared))
    stale = sorted(set(declared) - discovered_python)
    if duplicates or missing or stale:
        raise CampaignError(
            "Python matrix mismatch: "
            f"duplicates={duplicates}, missing={missing}, stale={stale}"
        )

    discovered_desktop = {
        path.relative_to(ROOT).as_posix()
        for pattern in ("*.test.ts", "*.test.tsx")
        for path in (ROOT / "desktop" / "src").glob(pattern)
    }
    declared_desktop = set(DESKTOP_TEST_MODULES)
    if discovered_desktop != declared_desktop:
        raise CampaignError(
            "Desktop matrix mismatch: "
            f"missing={sorted(discovered_desktop - declared_desktop)}, "
            f"stale={sorted(declared_desktop - discovered_desktop)}"
        )

    absent_fixtures = [relative for relative in FIXTURE_AUTHORITIES if not (ROOT / relative).is_file()]
    if absent_fixtures:
        raise CampaignError(f"Correctness fixture authorities are missing: {absent_fixtures}")
    return {
        "categories": len(CATEGORY_MODULES),
        "pythonModules": len(discovered_python),
        "desktopModules": len(discovered_desktop),
        "fixtureAuthorities": len(FIXTURE_AUTHORITIES),
    }


def _sanitized_environment(temporary: Path) -> dict[str, str]:
    environment = dict(os.environ)
    isolated_user_data = temporary / "user-data"
    environment.update(
        {
            "APPDATA": str(temporary / "app-data"),
            "LOCALAPPDATA": str(temporary / "local-app-data"),
            "USERPROFILE": str(temporary / "profile"),
            "E7_USER_DATA_DIR": str(isolated_user_data),
            "E7_SETTINGS_PATH": str(isolated_user_data / "settings.json"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTEST_ADDOPTS": "",
        }
    )
    return environment


def _command(modules: Sequence[str], temporary: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-W",
        "error",
        "--basetemp",
        str(temporary / "pytest"),
        *modules,
    ]


def _public_command(modules: Sequence[str]) -> str:
    return "python -m pytest -q -p no:cacheprovider -W error " + " ".join(modules)


def run_category(name: str) -> dict[str, Any]:
    modules = CATEGORY_MODULES[name]
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix=f"e7-correctness-{name}-") as raw_temporary:
        temporary = Path(raw_temporary)
        completed = subprocess.run(
            _command(modules, temporary),
            cwd=ROOT,
            env=_sanitized_environment(temporary),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30 * 60,
            check=False,
        )
    output = completed.stdout
    if completed.returncode != 0:
        print(output, file=sys.stderr)
        raise CampaignError(f"Correctness category failed: {name}")
    lowered = output.lower()
    forbidden_summaries = (" skipped", " xfailed", " xpassed", " warnings summary")
    if any(marker in lowered for marker in forbidden_summaries):
        print(output, file=sys.stderr)
        raise CampaignError(f"Correctness category produced hidden outcomes: {name}")
    match = SUMMARY_PATTERN.search(output)
    if match is None:
        print(output, file=sys.stderr)
        raise CampaignError(f"Could not parse the passing pytest summary for {name}")
    result = {
        "category": name,
        "status": "passed",
        "command": _public_command(modules),
        "moduleCount": len(modules),
        "testsPassed": int(match.group("tests")),
        "subtestsPassed": int(match.group("subtests") or 0),
        "pytestSeconds": float(match.group("seconds")),
        "wallSeconds": round(time.perf_counter() - started, 3),
        "unexpectedSkips": 0,
        "xfails": 0,
        "warnings": 0,
        "isolatedSubprocess": True,
        "temporaryUserData": True,
        "realCudaDevice": False,
    }
    print(
        "E7_CORRECTNESS_CATEGORY_OK "
        f"category={name} tests={result['testsPassed']} "
        f"subtests={result['subtestsPassed']} seconds={result['pytestSeconds']}"
    )
    return result


def _git_revision() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unavailable"


def build_report(environment: dict[str, Any], matrix: dict[str, int]) -> dict[str, Any]:
    categories = [run_category(name) for name in CATEGORY_MODULES]
    fixture_hashes = {
        relative: _sha256(ROOT / relative)
        for relative in FIXTURE_AUTHORITIES
    }
    return {
        "schemaId": "e7.correctness-campaign-report",
        "schemaVersion": 1,
        "task": "P09-T04",
        "createdAtUtc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "status": "passed",
        "sourceRevision": _git_revision(),
        "sourceTree": "working-tree-campaign",
        "privacy": "synthetic-fixtures-and-temporary-user-data-only",
        "environment": environment,
        "matrix": matrix,
        "categories": categories,
        "totals": {
            "testsPassed": sum(item["testsPassed"] for item in categories),
            "subtestsPassed": sum(item["subtestsPassed"] for item in categories),
            "unexpectedSkips": 0,
            "xfails": 0,
            "warnings": 0,
        },
        "cuda": {
            "hardwareIndependentParity": "passed",
            "method": "fake-array-kernel-boundary-and-deterministic-CPU-oracle",
            "realDeviceCampaign": "not-run-by-this-command",
            "realDeviceEvidenceMustBeRecordedSeparately": True,
        },
        "fixtureSha256": fixture_hashes,
        "controls": {
            "warningsAreErrors": True,
            "categoryIsolation": "fresh-subprocess-and-temporary-user-data",
            "networkInput": "none",
            "liveUserData": "not-opened",
            "privatePathsInReport": False,
        },
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--category",
        choices=tuple(CATEGORY_MODULES),
        help="Run one audited Python correctness category.",
    )
    parser.add_argument("--all", action="store_true", help="Run every Python category.")
    parser.add_argument("--report", type=Path, help="Write the --all JSON report here.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    matrix = audit_matrix()
    environment = audit_environment()
    print(
        "E7_CORRECTNESS_AUDIT_OK "
        f"categories={matrix['categories']} pythonModules={matrix['pythonModules']} "
        f"desktopModules={matrix['desktopModules']} python={environment['pythonVersion']} "
        f"numpy={environment['numpyVersion']} pyinstaller={environment['pyinstallerVersion']}"
    )
    if args.report and not args.all:
        raise CampaignError("--report requires --all.")
    if args.category:
        run_category(args.category)
    if args.all:
        report = build_report(environment, matrix)
        if args.report:
            destination = args.report.resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(
            "E7_CORRECTNESS_CAMPAIGN_OK "
            f"categories={len(report['categories'])} "
            f"tests={report['totals']['testsPassed']} "
            f"subtests={report['totals']['subtestsPassed']}"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CampaignError as error:
        raise SystemExit(f"E7_CORRECTNESS_CAMPAIGN_FAILED {error}") from error
