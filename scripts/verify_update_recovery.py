"""Rehearse an app update and startup recovery through the frozen backend."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verify_frozen_backend import FrozenBackend, SYNTHETIC_INVENTORY
from src.optimizer.data import InventoryRepository
from src.optimizer.result_store import ResultRunStore


DEFAULT_EXECUTABLE = ROOT / "dist" / "backend" / "e7-core.exe"


def _wait_for_search(session: FrozenBackend) -> dict:
    deadline = time.monotonic() + 20
    snapshot = session.request("optimizer.search.get")
    while snapshot["state"] in {"preparing", "running"}:
        if time.monotonic() >= deadline:
            raise AssertionError("Frozen update-recovery search did not finish within 20 seconds.")
        time.sleep(0.01)
        snapshot = session.request("optimizer.search.get")
    if snapshot["state"] != "completed" or not snapshot["resultRunId"]:
        raise AssertionError(f"Frozen update-recovery search failed: {snapshot}")
    return snapshot


def _legacy_settings(path: Path) -> bytes:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.pop("schema_version", None) != 1:
        raise AssertionError("Frozen settings did not start at schema version 1.")
    value.pop("appearance", None)
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    path.write_bytes(payload)
    return payload


def _legacy_profile(path: Path) -> bytes:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schemaVersion") != 7:
        raise AssertionError("Frozen profile did not start at schema version 7.")
    value["schemaVersion"] = 6
    del value["configuration"]["maximumReplacementDistance"]
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    path.write_bytes(payload)
    return payload


def run(executable: Path, *, source_mode: bool = False) -> None:
    if not executable.is_file():
        raise AssertionError(f"Backend executable is missing: {executable}")
    resources = (ROOT / "dist").resolve() if source_mode else executable.parent.parent.resolve()
    if not (resources / "cuda-installer" / "python.exe").is_file():
        raise AssertionError("Backend resources are missing the trusted CUDA installer helper.")

    def start(user_data: Path) -> FrozenBackend:
        if not source_mode:
            return FrozenBackend(executable, user_data, resources)
        return FrozenBackend(
            executable,
            user_data,
            resources,
            command=[str(executable), "-u", "-m", "src.desktop.backend"],
            working_directory=ROOT,
        )

    with tempfile.TemporaryDirectory(prefix="e7-frozen-update-") as temporary:
        temp_root = Path(temporary).resolve()
        user_data = temp_root / "user-data"
        user_data.mkdir()
        source = temp_root / "synthetic-update-gear.txt"
        source.write_text(json.dumps(SYNTHETIC_INVENTORY), encoding="utf-8")

        first = start(user_data)
        try:
            settings = first.request("settings.get")
            first.request(
                "settings.update",
                {
                    "revision": settings["revision"],
                    "patch": {"targetWindow": "Synthetic Pre-Update Window"},
                },
            )
            imported = first.request(
                "optimizer.inventory.import",
                {"sourcePath": str(source)},
            )
            hero = first.request(
                "optimizer.hero.search",
                {"query": "Achates", "limit": 1},
            )["results"][0]
            profile = first.request(
                "optimizer.profile.load",
                {"heroId": hero["heroId"]},
            )
            draft = profile["draft"]
            draft["maximumReplacementDistance"] = 0
            draft["nearSetTolerancePercent"] = 0
            first.request("optimizer.profile.save", {"draft": draft})
            first.request("optimizer.search.start", {"draft": draft})
            completed_search = _wait_for_search(first)
        finally:
            first.stop()

        if imported["inventory"]["totalItems"] != 6:
            raise AssertionError("Frozen pre-update inventory did not contain six synthetic items.")
        database = user_data / "optimizer.db"
        before_repository = InventoryRepository(database)
        before_repository.initialize()
        before_inventory = before_repository.load_inventory()
        before_heroes = before_repository.load_heroes()
        before_history = before_repository.load_import_history()
        before_summary = before_repository.inventory_summary()
        before_dense = before_repository.dense_snapshot()

        settings_path = user_data / "settings.json"
        profile_files = list((user_data / "optimizer_profiles").glob("*.json"))
        if len(profile_files) != 1:
            raise AssertionError("Frozen pre-update profile storage is not singular.")
        profile_path = profile_files[0]
        legacy_settings = _legacy_settings(settings_path)
        legacy_profile = _legacy_profile(profile_path)

        result_root = user_data / "optimizer_results"
        run_id = completed_search["resultRunId"]
        valid_run = result_root / "runs" / run_id
        valid_manifest = (valid_run / "manifest.json").read_bytes()
        corrupt_run = result_root / "runs" / "corrupt-update-run"
        shutil.copytree(valid_run, corrupt_run)
        stale = result_root / ".incomplete" / f"stale-owned.{('a' * 32)}.tmp"
        (stale / "columns").mkdir(parents=True)
        old = time.time() - (2 * 24 * 60 * 60)
        os.utime(stale, (old, old))

        second = start(user_data)
        try:
            migrated_settings = second.request("settings.get")
            migrated_inventory = second.request("optimizer.inventory.get")
            migrated_profile = second.request(
                "optimizer.profile.load",
                {"heroId": hero["heroId"]},
            )
            if settings_path.read_bytes() != legacy_settings:
                raise AssertionError("Settings were rewritten during migration read.")
            if profile_path.read_bytes() != legacy_profile:
                raise AssertionError("Profile was rewritten during migration read.")
            saved_settings = second.request(
                "settings.update",
                {
                    "revision": migrated_settings["revision"],
                    "patch": {"appearance": {"theme": "dark"}},
                },
            )
            saved_profile = second.request(
                "optimizer.profile.save",
                {"draft": migrated_profile["draft"]},
            )
        finally:
            second.stop()

        if migrated_settings.get("migratedFrom") != 0:
            raise AssertionError("Frozen settings did not report the v0 migration.")
        if migrated_settings["settings"]["targetWindow"] != "Synthetic Pre-Update Window":
            raise AssertionError("Frozen settings lost the pre-update preference.")
        if saved_settings["schemaVersion"] != 1:
            raise AssertionError("Frozen settings did not publish the current schema.")
        if (Path(f"{settings_path}.bak")).read_bytes() != legacy_settings:
            raise AssertionError("Frozen settings did not preserve the exact legacy backup.")
        if migrated_inventory["state"] != "ready" or migrated_inventory["totalItems"] != 6:
            raise AssertionError("Frozen inventory was not preserved across update startup.")
        if migrated_profile["draft"]["maximumReplacementDistance"] != 0:
            raise AssertionError("Frozen v6 profile did not normalize to exact-only search.")
        if saved_profile["state"] != "saved":
            raise AssertionError("Frozen migrated profile did not resave explicitly.")
        if json.loads(profile_path.read_text(encoding="utf-8"))["schemaVersion"] != 7:
            raise AssertionError("Frozen profile did not publish the current schema.")
        if stale.exists():
            raise AssertionError("Frozen startup did not remove the proven stale owned writer.")
        if not corrupt_run.is_dir():
            raise AssertionError("Frozen startup removed a corrupt completed run instead of failing closed.")
        if (valid_run / "manifest.json").read_bytes() != valid_manifest:
            raise AssertionError("Frozen startup changed the valid completed result run.")

        after_repository = InventoryRepository(database)
        after_repository.initialize()
        if after_repository.load_inventory() != before_inventory:
            raise AssertionError("Frozen update changed inventory item or identity state.")
        if after_repository.load_heroes() != before_heroes:
            raise AssertionError("Frozen update changed imported hero state.")
        if after_repository.load_import_history() != before_history:
            raise AssertionError("Frozen update changed import history.")
        if after_repository.inventory_summary() != before_summary:
            raise AssertionError("Frozen update changed inventory aggregates.")
        if after_repository.dense_snapshot() != before_dense:
            raise AssertionError("Frozen update changed the canonical six-slot snapshot.")
        reopened_run = ResultRunStore(result_root).open_run(run_id, verify_hashes=True)
        if reopened_run.run_id != run_id:
            raise AssertionError("Frozen update could not reopen the valid completed run.")

    print(
        f"E7_UPDATE_RECOVERY_OK mode={'source-protocol' if source_mode else 'frozen'} "
        "settings=0->1 inventory=6 profile=6->7 "
        "results=1 staleRemoved=1 corruptPreserved=1"
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--source":
        run(Path(sys.executable).resolve(), source_mode=True)
    else:
        selected = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_EXECUTABLE
        run(selected)
