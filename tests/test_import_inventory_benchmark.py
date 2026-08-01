from __future__ import annotations

import ast
import inspect
import json
import tempfile
import unittest
from pathlib import Path

from scripts import benchmark_import_inventory as benchmark_module
from scripts.benchmark_import_inventory import (
    BENCHMARK_ID,
    DEFAULT_ITEM_COUNT,
    ImportBenchmarkError,
    build_synthetic_payload,
    run_campaign,
    run_sample,
    validate_document,
)
from src.optimizer.data import parse_fribbels_gear_bytes
from src.optimizer.domain import GEAR_SLOT_ORDER


class ImportInventoryBenchmarkTests(unittest.TestCase):
    def test_payload_is_deterministic_enriched_and_balanced(self) -> None:
        first = build_synthetic_payload(12)
        self.assertEqual(first, build_synthetic_payload(12))
        parsed = parse_fribbels_gear_bytes(first)
        self.assertEqual(12, parsed.source_item_count)
        self.assertEqual(12, parsed.accepted_count)
        self.assertEqual(0, parsed.rejected_count)
        self.assertEqual(0, parsed.warning_count)
        self.assertEqual(12, len(parsed.heroes))
        self.assertEqual(
            {slot: 2 for slot in GEAR_SLOT_ORDER},
            {slot: sum(item.slot is slot for item in parsed.items) for slot in GEAR_SLOT_ORDER},
        )

    def test_fresh_and_unchanged_samples_cover_transaction_and_dense_snapshot(self) -> None:
        payload = build_synthetic_payload(12)
        fresh = run_sample("fresh", payload, 12, 0)
        unchanged = run_sample("unchanged-reimport", payload, 12, 0)
        self.assertEqual((12, 0), (fresh["insertedItems"], fresh["unchangedItems"]))
        self.assertEqual((0, 12), (unchanged["insertedItems"], unchanged["unchangedItems"]))
        self.assertEqual((1, 2), (fresh["historyRows"], unchanged["historyRows"]))
        self.assertEqual(fresh["inventoryDigestSha256"], unchanged["inventoryDigestSha256"])
        for sample in (fresh, unchanged):
            self.assertEqual(12, sample["acceptedItems"])
            self.assertEqual(12, sample["denseItems"])
            self.assertEqual({"ingame": 12, "source": 12, "fingerprint": 12}, sample["aliases"])
            self.assertGreater(sample["databaseBytes"], 0)
            self.assertGreaterEqual(sample["importSeconds"], 0)
            self.assertGreaterEqual(sample["denseSnapshotSeconds"], 0)

    def test_small_campaign_preserves_raw_samples_summaries_and_privacy(self) -> None:
        document = run_campaign(item_count=12, warmups=0, samples=2)
        validate_document(document)
        self.assertEqual(BENCHMARK_ID, document["benchmarkId"])
        self.assertEqual(4, len(document["samples"]))
        self.assertEqual(2, len(document["summaries"]))
        self.assertNotIn("machine", document["environment"])
        self.assertNotIn("workspace", json.dumps(document).lower())
        self.assertEqual(1, len({item["inventoryDigestSha256"] for item in document["samples"]}))

    def test_configuration_and_scenario_validation_fail_closed(self) -> None:
        for count in (0, 7, 20_001):
            with self.subTest(count=count), self.assertRaises(ImportBenchmarkError):
                build_synthetic_payload(count)
        payload = build_synthetic_payload(12)
        with self.assertRaises(ImportBenchmarkError):
            run_sample("unknown", payload, 12, 0)
        with self.assertRaises(ImportBenchmarkError):
            run_campaign(item_count=12, samples=0)
        with self.assertRaises(ImportBenchmarkError):
            run_campaign(item_count=12, scenarios=("fresh", "fresh"))

    def test_cli_writes_valid_local_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="e7-import-benchmark-cli-") as temporary:
            output = Path(temporary) / "evidence.json"
            self.assertEqual(
                0,
                benchmark_module.main(
                    [
                        "--items",
                        "12",
                        "--warmups",
                        "0",
                        "--samples",
                        "1",
                        "--output",
                        str(output),
                    ]
                ),
            )
            document = json.loads(output.read_text(encoding="utf-8"))
            validate_document(document)

    def test_harness_is_developer_only_and_has_no_live_service_dependency(self) -> None:
        source = inspect.getsource(benchmark_module)
        tree = ast.parse(source)
        imports = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        self.assertFalse(any(name.startswith("src.desktop") for name in imports))
        self.assertNotIn("gear.txt", source)
        self.assertNotIn("tests.", source)
        spec = (benchmark_module.ROOT / "packaging" / "e7-core.spec").read_text(encoding="utf-8")
        self.assertNotIn("benchmark_import_inventory", spec)


if __name__ == "__main__":
    unittest.main()
