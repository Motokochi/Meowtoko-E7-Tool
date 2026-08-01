"""Benchmark synthetic Fribbels import and dense inventory publication."""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import statistics
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.optimizer.data import (  # noqa: E402
    FribbelsImportRequest,
    FribbelsImportService,
    InventoryRepository,
)
from src.optimizer.domain import (  # noqa: E402
    GEAR_SLOT_ORDER,
    GearSet,
    GearSlot,
    ItemStatType,
    gear_set_fribbels_name,
    gear_slot_fribbels_name,
    item_stat_fribbels_name,
)


BENCHMARK_ID = "e7.optimizer.fribbels-import-inventory"
BENCHMARK_SCHEMA_VERSION = 1
DEFAULT_ITEM_COUNT = 3_000
DEFAULT_WARMUPS = 1
DEFAULT_SAMPLES = 5
SCENARIOS = ("fresh", "unchanged-reimport")
EQUIPPED_EVERY = 10
LOCKED_EVERY = 7

_MAIN_STATS = {
    GearSlot.WEAPON: (ItemStatType.FLAT_ATTACK, 500),
    GearSlot.HELMET: (ItemStatType.FLAT_HEALTH, 2_500),
    GearSlot.ARMOR: (ItemStatType.FLAT_DEFENSE, 300),
    GearSlot.NECKLACE: (ItemStatType.CRITICAL_HIT_DAMAGE_PERCENT, 65),
    GearSlot.RING: (ItemStatType.EFFECTIVENESS_PERCENT, 65),
    GearSlot.BOOTS: (ItemStatType.SPEED, 45),
}
_SETS = (
    GearSet.SPEED,
    GearSet.HEALTH,
    GearSet.ATTACK,
    GearSet.DEFENSE,
    GearSet.CRITICAL,
    GearSet.RESIST,
)
_SUBSTATS = {
    GearSlot.WEAPON: (ItemStatType.SPEED, ItemStatType.HEALTH_PERCENT),
    GearSlot.HELMET: (ItemStatType.SPEED, ItemStatType.ATTACK_PERCENT),
    GearSlot.ARMOR: (ItemStatType.SPEED, ItemStatType.HEALTH_PERCENT),
    GearSlot.NECKLACE: (ItemStatType.SPEED, ItemStatType.ATTACK_PERCENT),
    GearSlot.RING: (ItemStatType.SPEED, ItemStatType.ATTACK_PERCENT),
    GearSlot.BOOTS: (ItemStatType.ATTACK_PERCENT, ItemStatType.CRITICAL_HIT_CHANCE_PERCENT),
}


class ImportBenchmarkError(ValueError):
    """Raised when configuration or deterministic import evidence drifts."""


def _integer(value: object, path: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ImportBenchmarkError(f"{path} must be an integer.")
    if value < minimum or value > maximum:
        raise ImportBenchmarkError(
            f"{path} must be between {minimum:,} and {maximum:,}; found {value:,}."
        )
    return value


def build_synthetic_payload(item_count: int = DEFAULT_ITEM_COUNT) -> bytes:
    """Return one deterministic enriched Fribbels-shaped UTF-8 document."""

    count = _integer(item_count, "item_count", len(GEAR_SLOT_ORDER), 20_000)
    if count % len(GEAR_SLOT_ORDER):
        raise ImportBenchmarkError("item_count must be divisible by six.")
    hero_count = min(30, count)
    heroes = [
        {
            "id": f"benchmark-hero-{index:02d}",
            "name": f"Synthetic Hero {index:02d}",
            "stars": 6,
            "awaken": 6,
        }
        for index in range(hero_count)
    ]
    rows: list[dict[str, object]] = []
    for index in range(count):
        slot = GEAR_SLOT_ORDER[index % len(GEAR_SLOT_ORDER)]
        main_stat, main_value = _MAIN_STATS[slot]
        first_substat, second_substat = _SUBSTATS[slot]
        hero_id = (
            f"benchmark-hero-{(index // EQUIPPED_EVERY) % hero_count:02d}"
            if index % EQUIPPED_EVERY == 0
            else None
        )
        rows.append(
            {
                "id": f"benchmark-source-{index:06d}",
                "ingameId": f"benchmark:{index:06d}",
                "ingameEquippedId": hero_id,
                "equippedById": hero_id,
                "equippedByName": None if hero_id is None else f"Synthetic Hero {(index // EQUIPPED_EVERY) % hero_count:02d}",
                "locked": index % LOCKED_EVERY == 0,
                "material": "Hunt",
                "gear": gear_slot_fribbels_name(slot),
                "rank": "Epic",
                "set": gear_set_fribbels_name(_SETS[(index // len(GEAR_SLOT_ORDER)) % len(_SETS)]),
                "enhance": 15,
                "level": 85,
                "name": f"Synthetic benchmark item {index:06d}",
                "main": {
                    "type": item_stat_fribbels_name(main_stat),
                    "value": main_value,
                    "reforgedValue": main_value,
                },
                "substats": [
                    {
                        "type": item_stat_fribbels_name(first_substat),
                        "value": 8 + index % 24,
                        "rolls": 2 + index % 3,
                        "reforgedValue": 10 + index % 24,
                    },
                    {
                        "type": item_stat_fribbels_name(second_substat),
                        "value": 3 + index % 10,
                        "rolls": 1 + index % 2,
                        "reforgedValue": 4 + index % 10,
                    },
                ],
                "benchmarkRevision": 1,
            }
        )
    payload = {
        "items": rows,
        "heroes": heroes,
        "benchmark": {
            "fixtureId": "p09.synthetic.fribbels-import.v1",
            "privacy": "synthetic",
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _dense_digest(reverse: Sequence[tuple[int, str]]) -> str:
    digest = hashlib.sha256()
    for dense_id, stable_id in reverse:
        digest.update(str(dense_id).encode("ascii"))
        digest.update(b"\0")
        digest.update(stable_id.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _request(source: Path, import_id: str, second: int) -> FribbelsImportRequest:
    return FribbelsImportRequest(
        source,
        import_id,
        f"2026-07-22T00:{second // 60:02d}:{second % 60:02d}Z",
        {"fixtureId": "p09.synthetic.fribbels-import.v1"},
    )


def run_sample(
    scenario: str,
    payload: bytes,
    item_count: int,
    sample_index: int,
) -> dict[str, object]:
    if scenario not in SCENARIOS:
        raise ImportBenchmarkError(f"unknown scenario: {scenario}")
    count = _integer(item_count, "item_count", len(GEAR_SLOT_ORDER), 20_000)
    index = _integer(sample_index, "sample_index", 0, 99)
    expected_per_slot = count // len(GEAR_SLOT_ORDER)
    expected_heroes = min(30, count)
    expected_equipped = math.ceil(count / EQUIPPED_EVERY)
    expected_locked = math.ceil(count / LOCKED_EVERY)

    with tempfile.TemporaryDirectory(prefix="e7-import-benchmark-") as temporary:
        root = Path(temporary)
        source = root / "synthetic-input.json"
        source.write_bytes(payload)
        repository = InventoryRepository(root / "optimizer.db")
        service = FribbelsImportService(repository)
        if scenario == "unchanged-reimport":
            seed = service.import_file(_request(source, f"benchmark.seed.{index}", index * 2))
            if seed.inserted_count != count:
                raise ImportBenchmarkError("unchanged scenario seed import drifted.")

        gc.collect()
        wall_started = time.perf_counter()
        import_started = time.perf_counter()
        report = service.import_file(
            _request(source, f"benchmark.measured.{scenario}.{index}", index * 2 + 1)
        )
        import_seconds = time.perf_counter() - import_started
        summary_started = time.perf_counter()
        summary = repository.inventory_summary()
        summary_seconds = time.perf_counter() - summary_started
        dense_started = time.perf_counter()
        dense = repository.dense_snapshot()
        dense_seconds = time.perf_counter() - dense_started
        wall_seconds = time.perf_counter() - wall_started

        expected_inserted = count if scenario == "fresh" else 0
        expected_unchanged = 0 if scenario == "fresh" else count
        expected_history = 1 if scenario == "fresh" else 2
        if (
            report.source_item_count != count
            or report.accepted_count != count
            or report.rejected_count != 0
            or report.warning_count != 0
            or report.inserted_count != expected_inserted
            or report.updated_count != 0
            or report.unchanged_count != expected_unchanged
            or report.conflict_count != 0
            or report.resulting_inventory_count != count
            or report.equipped_item_count != expected_equipped
            or report.imported_hero_count != expected_heroes
        ):
            raise ImportBenchmarkError(
                "import report deterministic evidence drifted: "
                f"source={report.source_item_count} accepted={report.accepted_count} "
                f"rejected={report.rejected_count} warnings={report.warning_count} "
                f"inserted={report.inserted_count} updated={report.updated_count} "
                f"unchanged={report.unchanged_count} conflicts={report.conflict_count} "
                f"resulting={report.resulting_inventory_count} "
                f"equipped={report.equipped_item_count} heroes={report.imported_hero_count}."
            )
        if any(slot_count != expected_per_slot for _, slot_count in report.items_by_slot):
            raise ImportBenchmarkError("canonical import slot counts drifted.")
        if (
            summary.total_items != count
            or summary.equipped_items != expected_equipped
            or summary.locked_items != expected_locked
            or summary.imported_heroes != expected_heroes
            or summary.import_history_records != expected_history
            or summary.ingame_aliases != count
            or summary.source_aliases != count
            or summary.fingerprint_aliases != count
        ):
            raise ImportBenchmarkError("repository aggregate/alias evidence drifted.")
        if len(dense.dense_id_to_stable_id) != count:
            raise ImportBenchmarkError("dense snapshot item count drifted.")
        if tuple(dense_id for dense_id, _ in dense.dense_id_to_stable_id) != tuple(range(count)):
            raise ImportBenchmarkError("dense snapshot IDs are not contiguous.")
        if any(len(dense.items_for_slot(slot)) != expected_per_slot for slot in GEAR_SLOT_ORDER):
            raise ImportBenchmarkError("dense snapshot slot counts drifted.")

        return {
            "scenario": scenario,
            "sampleIndex": index,
            "importSeconds": import_seconds,
            "summarySeconds": summary_seconds,
            "denseSnapshotSeconds": dense_seconds,
            "endToEndSeconds": wall_seconds,
            "databaseBytes": repository.database_path.stat().st_size,
            "inventoryDigestSha256": _dense_digest(dense.dense_id_to_stable_id),
            "acceptedItems": report.accepted_count,
            "insertedItems": report.inserted_count,
            "unchangedItems": report.unchanged_count,
            "equippedItems": summary.equipped_items,
            "lockedItems": summary.locked_items,
            "heroes": summary.imported_heroes,
            "historyRows": summary.import_history_records,
            "aliases": {
                "ingame": summary.ingame_aliases,
                "source": summary.source_aliases,
                "fingerprint": summary.fingerprint_aliases,
            },
            "denseItems": len(dense.dense_id_to_stable_id),
        }


def _summary(scenario: str, samples: Sequence[Mapping[str, object]]) -> dict[str, object]:
    if not samples:
        raise ImportBenchmarkError("summary requires measured samples.")
    if any(sample.get("scenario") != scenario for sample in samples):
        raise ImportBenchmarkError("summary samples must share one scenario.")
    digests = {sample.get("inventoryDigestSha256") for sample in samples}
    if len(digests) != 1:
        raise ImportBenchmarkError("inventory digest changed between samples.")

    def stats(field: str) -> dict[str, float]:
        values = [float(sample[field]) for sample in samples]
        return {
            "median": statistics.median(values),
            "minimum": min(values),
            "maximum": max(values),
        }

    return {
        "scenario": scenario,
        "sampleCount": len(samples),
        "importSeconds": stats("importSeconds"),
        "denseSnapshotSeconds": stats("denseSnapshotSeconds"),
        "endToEndSeconds": stats("endToEndSeconds"),
        "inventoryDigestSha256": next(iter(digests)),
        "medianItemsPerSecond": int(samples[0]["acceptedItems"])
        / stats("importSeconds")["median"],
    }


def run_campaign(
    *,
    item_count: int = DEFAULT_ITEM_COUNT,
    warmups: int = DEFAULT_WARMUPS,
    samples: int = DEFAULT_SAMPLES,
    scenarios: Sequence[str] = SCENARIOS,
) -> dict[str, object]:
    count = _integer(item_count, "item_count", len(GEAR_SLOT_ORDER), 20_000)
    warmup_count = _integer(warmups, "warmups", 0, 10)
    sample_count = _integer(samples, "samples", 1, 20)
    selected = tuple(scenarios)
    if not selected or len(selected) != len(set(selected)) or any(item not in SCENARIOS for item in selected):
        raise ImportBenchmarkError("scenarios must be a unique non-empty supported sequence.")
    payload = build_synthetic_payload(count)
    raw: list[dict[str, object]] = []
    summaries: list[dict[str, object]] = []
    for scenario in selected:
        for index in range(warmup_count):
            run_sample(scenario, payload, count, 50 + index)
        measured = [run_sample(scenario, payload, count, index) for index in range(sample_count)]
        raw.extend(measured)
        summaries.append(_summary(scenario, measured))
    digests = {sample["inventoryDigestSha256"] for sample in raw}
    if len(digests) != 1:
        raise ImportBenchmarkError("fresh and unchanged scenarios produced different inventories.")
    return {
        "benchmarkId": BENCHMARK_ID,
        "schemaVersion": BENCHMARK_SCHEMA_VERSION,
        "measuredUtc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "privacy": "synthetic-temporary-storage-only",
        "environment": {
            "system": platform.system(),
            "release": platform.release(),
            "architecture": platform.machine(),
            "processor": platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER", "unknown"),
            "logicalCpuCount": os.cpu_count(),
            "pythonImplementation": platform.python_implementation(),
            "pythonVersion": platform.python_version(),
            "numpyVersion": importlib.metadata.version("numpy"),
        },
        "configuration": {
            "fixtureId": "p09.synthetic.fribbels-import.v1",
            "itemCount": count,
            "itemsPerSlot": count // len(GEAR_SLOT_ORDER),
            "heroCount": min(30, count),
            "equippedItems": math.ceil(count / EQUIPPED_EVERY),
            "lockedItems": math.ceil(count / LOCKED_EVERY),
            "aliasesPerItem": 3,
            "warmups": warmup_count,
            "samples": sample_count,
            "scenarios": list(selected),
            "payloadBytes": len(payload),
            "payloadSha256": hashlib.sha256(payload).hexdigest(),
        },
        "samples": raw,
        "summaries": summaries,
        "validations": {
            "allItemsAccepted": True,
            "zeroWarningsRejectionsConflicts": True,
            "transactionalPublication": True,
            "canonicalSixSlotDenseSnapshot": True,
            "stableAcrossFreshAndUnchanged": True,
            "liveDataRead": False,
        },
    }


def validate_document(document: object) -> None:
    if not isinstance(document, dict):
        raise ImportBenchmarkError("benchmark document must be an object.")
    if document.get("benchmarkId") != BENCHMARK_ID or document.get("schemaVersion") != 1:
        raise ImportBenchmarkError("benchmark identity/version is invalid.")
    environment = document.get("environment")
    configuration = document.get("configuration")
    samples = document.get("samples")
    summaries = document.get("summaries")
    if not all(isinstance(value, dict) for value in (environment, configuration)):
        raise ImportBenchmarkError("environment/configuration evidence is missing.")
    if "machine" in environment or "workspace" in document:
        raise ImportBenchmarkError("benchmark document contains private host/path data.")
    if not isinstance(samples, list) or not samples or not isinstance(summaries, list):
        raise ImportBenchmarkError("raw samples and summaries are required.")
    expected = int(configuration["samples"]) * len(configuration["scenarios"])
    if len(samples) != expected:
        raise ImportBenchmarkError("raw sample count does not match configuration.")
    if len({sample["inventoryDigestSha256"] for sample in samples}) != 1:
        raise ImportBenchmarkError("inventory digest drifted across the campaign.")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", type=int, default=DEFAULT_ITEM_COUNT)
    parser.add_argument("--warmups", type=int, default=DEFAULT_WARMUPS)
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    parser.add_argument("--scenario", action="append", choices=SCENARIOS, dest="scenarios")
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        document = run_campaign(
            item_count=args.items,
            warmups=args.warmups,
            samples=args.samples,
            scenarios=SCENARIOS if args.scenarios is None else args.scenarios,
        )
        validate_document(document)
        serialized = json.dumps(document, indent=2, sort_keys=True) + "\n"
        if args.output is None:
            print(serialized, end="")
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized, encoding="utf-8")
        return 0
    except (OSError, ImportBenchmarkError, ValueError) as error:
        print(f"Import benchmark error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
