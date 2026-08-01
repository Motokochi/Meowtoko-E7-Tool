from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

import numpy as np

from src.optimizer.domain import (
    FINAL_STAT_ORDER,
    FRIBBELS_SET_ORDER,
    GEAR_SLOT_ORDER,
    MAX_RESULT_CAP,
    RESULT_CATEGORY_ORDER,
    SET_CATALOG,
    GearSet,
    ResultCategory,
)
from src.optimizer.engine import DERIVED_METRIC_IDS
from src.optimizer.result_store import (
    NO_REPLACEMENT_METADATA_REFERENCE,
    RESULT_COLUMN_NAMES,
    RESULT_DERIVED_METRIC_ORDER,
    RESULT_MAX_DENSE_ITEM_ID,
    RESULT_MAX_ROW_ORDINAL,
    RESULT_PRIMARY_STAT_ORDER,
    RESULT_ROW_BYTES,
    RESULT_SCHEMA,
    RESULT_SCHEMA_ID,
    RESULT_SCHEMA_VERSION,
    RESULT_SLOT_ORDER,
    ReplacementMetadataReference,
    ResultSchemaError,
    decode_result_category,
    encode_result_category,
    project_result_payload,
    replacement_metadata_reference,
    set_activation_counts,
    set_piece_counts,
    validate_dense_item_ids,
    validate_result_columns,
    validate_row_ordinal,
    validate_set_signature,
)


ROOT = Path(__file__).resolve().parents[1]


def _valid_columns(row_count: int = 3) -> dict[str, np.ndarray]:
    dense = np.arange(row_count * 6, dtype="<i4").reshape(row_count, 6)
    sets = np.array(
        [tuple((row + column) % len(FRIBBELS_SET_ORDER) for column in range(6)) for row in range(row_count)],
        dtype="u1",
    )
    categories = np.arange(row_count, dtype="u1") % len(RESULT_CATEGORY_ORDER)
    stats = np.arange(row_count * 8, dtype="<i8").reshape(row_count, 8)
    metrics = (np.arange(row_count * 15, dtype="<i8") - 10).reshape(row_count, 15)
    priority_bits = np.array([0x3F800001 + row for row in range(row_count)], dtype="<u4")
    priority = priority_bits.view("<f4")
    constraints = np.array([0 if code == 0 else (index + 1) / 10 for index, code in enumerate(categories)], dtype="<f4")
    equipped = np.arange(row_count, dtype="u1") % 7
    return {
        "dense_item_ids": dense,
        "owned_set_indices": sets,
        "category_codes": categories,
        "replacement_distances": categories.copy(),
        "effective_final_stats": stats,
        "raw_critical_hit_chances": stats[:, 4].copy(),
        "derived_metrics": metrics,
        "priority_scores": priority,
        "constraint_distances": constraints,
        "equipped_item_counts": equipped,
    }


class ResultSchemaContractTests(unittest.TestCase):
    def test_version_identity_and_exact_column_order_are_pinned(self) -> None:
        self.assertEqual("e7.optimizer.result-columns", RESULT_SCHEMA_ID)
        self.assertEqual(2, RESULT_SCHEMA_VERSION)
        self.assertEqual(RESULT_SCHEMA_ID, RESULT_SCHEMA.schema_id)
        self.assertEqual(RESULT_SCHEMA_VERSION, RESULT_SCHEMA.version)
        self.assertEqual(MAX_RESULT_CAP, RESULT_SCHEMA.maximum_rows)
        self.assertEqual(
            (
                "dense_item_ids",
                "owned_set_indices",
                "category_codes",
                "replacement_distances",
                "effective_final_stats",
                "raw_critical_hit_chances",
                "derived_metrics",
                "priority_scores",
                "constraint_distances",
                "equipped_item_counts",
            ),
            RESULT_COLUMN_NAMES,
        )
        self.assertEqual(RESULT_COLUMN_NAMES, RESULT_SCHEMA.column_names)

    def test_shapes_dtypes_null_rules_and_byte_totals_are_exact(self) -> None:
        expected = (
            ("dense_item_ids", "<i4", (6,), 24),
            ("owned_set_indices", "|u1", (6,), 6),
            ("category_codes", "|u1", (), 1),
            ("replacement_distances", "|u1", (), 1),
            ("effective_final_stats", "<i8", (8,), 64),
            ("raw_critical_hit_chances", "<i8", (), 8),
            ("derived_metrics", "<i8", (15,), 120),
            ("priority_scores", "<f4", (), 4),
            ("constraint_distances", "<f4", (), 4),
            ("equipped_item_counts", "|u1", (), 1),
        )
        actual = tuple(
            (column.name, column.dtype.str, column.shape, column.bytes_per_row)
            for column in RESULT_SCHEMA.columns
        )
        self.assertEqual(expected, actual)
        self.assertTrue(all(not column.nullable and column.sentinel is None for column in RESULT_SCHEMA.columns))
        self.assertEqual(233, RESULT_ROW_BYTES)
        self.assertEqual(RESULT_ROW_BYTES, sum(item[3] for item in expected))

    def test_vector_axes_and_enum_codes_cover_every_canonical_value(self) -> None:
        self.assertEqual(GEAR_SLOT_ORDER, RESULT_SLOT_ORDER)
        self.assertEqual(FINAL_STAT_ORDER, RESULT_PRIMARY_STAT_ORDER)
        self.assertEqual(DERIVED_METRIC_IDS, RESULT_DERIVED_METRIC_ORDER)
        self.assertEqual(8, len(RESULT_PRIMARY_STAT_ORDER))
        self.assertEqual(15, len(RESULT_DERIVED_METRIC_ORDER))
        self.assertEqual(
            tuple(slot.value for slot in GEAR_SLOT_ORDER),
            RESULT_SCHEMA.column("dense_item_ids").semantic_order,
        )
        self.assertEqual(
            tuple(stat.value for stat in FINAL_STAT_ORDER),
            RESULT_SCHEMA.column("effective_final_stats").semantic_order,
        )
        self.assertEqual(DERIVED_METRIC_IDS, RESULT_SCHEMA.column("derived_metrics").semantic_order)
        expected_codes = tuple(category.value for category in RESULT_CATEGORY_ORDER)
        self.assertEqual(expected_codes, RESULT_SCHEMA.column("category_codes").code_values)
        for code, category in enumerate(RESULT_CATEGORY_ORDER):
            with self.subTest(category=category):
                self.assertEqual(code, encode_result_category(category))
                self.assertEqual(category, decode_result_category(code))

    def test_implicit_row_identity_is_bounded_and_not_stored_redundantly(self) -> None:
        identity = RESULT_SCHEMA.row_identity
        self.assertEqual("<u4", identity.ordinal_dtype.str)
        self.assertEqual(MAX_RESULT_CAP - 1, identity.maximum_ordinal)
        self.assertFalse(identity.stored)
        self.assertEqual("dense_item_ids", identity.permutation_key_column)
        self.assertNotIn("flat_indices", RESULT_COLUMN_NAMES)
        self.assertNotIn("row_ordinals", RESULT_COLUMN_NAMES)
        self.assertEqual(0, validate_row_ordinal(0))
        self.assertEqual(RESULT_MAX_ROW_ORDINAL, validate_row_ordinal(RESULT_MAX_ROW_ORDINAL))

    def test_replacement_reference_is_deterministic_for_all_categories(self) -> None:
        dense_ids = (10, 11, 12, 13, 14, 15)
        exact = replacement_metadata_reference(7, ResultCategory.EXACT, dense_ids)
        one = replacement_metadata_reference(7, ResultCategory.ONE_AWAY, dense_ids)
        two = replacement_metadata_reference(7, ResultCategory.TWO_AWAY, dense_ids)
        self.assertIsNone(NO_REPLACEMENT_METADATA_REFERENCE)
        self.assertIs(exact, NO_REPLACEMENT_METADATA_REFERENCE)
        self.assertEqual(
            ReplacementMetadataReference(2, 7, ResultCategory.ONE_AWAY, dense_ids),
            one,
        )
        self.assertEqual(2, encode_result_category(two.category))  # type: ignore[union-attr]
        self.assertEqual(hash(one), hash(replacement_metadata_reference(7, ResultCategory.ONE_AWAY, dense_ids)))
        self.assertNotIn("replacement_metadata_refs", RESULT_COLUMN_NAMES)
        with self.assertRaisesRegex(ResultSchemaError, "exact-replacement-reference"):
            ReplacementMetadataReference(2, 7, ResultCategory.EXACT, dense_ids)

    def test_six_set_ids_preserve_slot_counts_and_repeated_stackable_activation(self) -> None:
        health = SET_CATALOG[GearSet.HEALTH].fribbels_index
        immunity = SET_CATALOG[GearSet.IMMUNITY].fribbels_index
        repeated_health = validate_set_signature((health,) * 6)
        health_counts = set_piece_counts(repeated_health)
        health_activations = set_activation_counts(repeated_health)
        self.assertEqual(6, health_counts[health])
        self.assertEqual(3, health_activations[health])
        nonstackable_activations = set_activation_counts((immunity,) * 6)
        self.assertEqual(1, nonstackable_activations[immunity])
        mixed = (health, immunity, health, immunity, health, health)
        self.assertEqual(mixed, validate_set_signature(mixed))
        self.assertEqual(4, set_piece_counts(mixed)[health])
        self.assertEqual(2, set_piece_counts(mixed)[immunity])

    def test_valid_batch_preserves_binary32_bits_and_signed_metrics(self) -> None:
        arrays = _valid_columns()
        priority_bits = arrays["priority_scores"].view("<u4").copy()
        metric_bytes = arrays["derived_metrics"].tobytes()
        self.assertEqual(3, validate_result_columns(arrays))
        np.testing.assert_array_equal(priority_bits, arrays["priority_scores"].view("<u4"))
        self.assertEqual(metric_bytes, arrays["derived_metrics"].tobytes())
        self.assertLess(int(arrays["derived_metrics"].min()), 0)

    def test_maximum_legal_fixed_width_values_are_accepted(self) -> None:
        arrays = _valid_columns(1)
        arrays["dense_item_ids"][0] = np.arange(
            RESULT_MAX_DENSE_ITEM_ID - 5,
            RESULT_MAX_DENSE_ITEM_ID + 1,
            dtype="<i4",
        )
        arrays["owned_set_indices"].fill(len(FRIBBELS_SET_ORDER) - 1)
        arrays["category_codes"].fill(2)
        arrays["replacement_distances"].fill(2)
        arrays["effective_final_stats"].fill(np.iinfo(np.int64).max)
        arrays["derived_metrics"][0, 0] = np.iinfo(np.int64).min
        arrays["derived_metrics"][0, 1:] = np.iinfo(np.int64).max
        arrays["priority_scores"].fill(np.finfo(np.float32).max)
        arrays["constraint_distances"].fill(np.finfo(np.float32).max)
        arrays["equipped_item_counts"].fill(6)
        self.assertEqual(1, validate_result_columns(arrays))

    def test_overflow_invalid_sentinel_and_narrowing_are_rejected(self) -> None:
        cases = (
            lambda: validate_row_ordinal(RESULT_MAX_ROW_ORDINAL + 1),
            lambda: validate_dense_item_ids((0, 1, 2, 3, 4, RESULT_MAX_DENSE_ITEM_ID + 1)),
            lambda: validate_set_signature((0, 1, 2, 3, 4, len(FRIBBELS_SET_ORDER))),
            lambda: decode_result_category(len(RESULT_CATEGORY_ORDER)),
            lambda: project_result_payload(MAX_RESULT_CAP + 1),
        )
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(ResultSchemaError):
                    case()

        arrays = _valid_columns()
        narrowed = dict(arrays)
        narrowed["dense_item_ids"] = arrays["dense_item_ids"].astype("<i2")
        with self.assertRaisesRegex(ResultSchemaError, "column-dtype-mismatch"):
            validate_result_columns(narrowed)
        widened = dict(arrays)
        widened["priority_scores"] = arrays["priority_scores"].astype("<f8")
        with self.assertRaisesRegex(ResultSchemaError, "column-dtype-mismatch"):
            validate_result_columns(widened)
        wrong_signedness = dict(arrays)
        wrong_signedness["dense_item_ids"] = arrays["dense_item_ids"].astype("<u4")
        with self.assertRaisesRegex(ResultSchemaError, "column-dtype-mismatch"):
            validate_result_columns(wrong_signedness)
        wrong_endian = dict(arrays)
        wrong_endian["derived_metrics"] = arrays["derived_metrics"].astype(">i8")
        with self.assertRaisesRegex(ResultSchemaError, "column-dtype-mismatch"):
            validate_result_columns(wrong_endian)

    def test_semantic_range_order_and_shape_failures_are_rejected(self) -> None:
        mutations = []
        arrays = _valid_columns()
        bad = dict(arrays)
        bad["category_codes"] = np.array([0, 1, 3], dtype="u1")
        mutations.append(bad)
        bad = dict(arrays)
        bad["replacement_distances"] = np.array([0, 2, 2], dtype="u1")
        mutations.append(bad)
        bad = dict(arrays)
        bad["owned_set_indices"] = arrays["owned_set_indices"].copy()
        bad["owned_set_indices"][0, 0] = len(FRIBBELS_SET_ORDER)
        mutations.append(bad)
        bad = dict(arrays)
        bad["equipped_item_counts"] = np.array([0, 6, 7], dtype="u1")
        mutations.append(bad)
        bad = dict(arrays)
        bad["dense_item_ids"] = arrays["dense_item_ids"].copy()
        bad["dense_item_ids"][0, 1] = bad["dense_item_ids"][0, 0]
        mutations.append(bad)
        bad = dict(arrays)
        bad["effective_final_stats"] = arrays["effective_final_stats"].copy()
        bad["effective_final_stats"][0, 0] = -1
        mutations.append(bad)
        bad = dict(arrays)
        bad["priority_scores"] = arrays["priority_scores"].copy()
        bad["priority_scores"][1] = np.nan
        mutations.append(bad)
        bad = dict(arrays)
        bad["constraint_distances"] = arrays["constraint_distances"].copy()
        bad["constraint_distances"][0] = 0.1
        mutations.append(bad)
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index):
                with self.assertRaises(ResultSchemaError):
                    validate_result_columns(mutation)

        reordered = {name: arrays[name] for name in reversed(RESULT_COLUMN_NAMES)}
        with self.assertRaisesRegex(ResultSchemaError, "column-order-mismatch"):
            validate_result_columns(reordered)
        wrong_shape = dict(arrays)
        wrong_shape["derived_metrics"] = np.zeros((3, 14), dtype="<i8")
        with self.assertRaisesRegex(ResultSchemaError, "column-shape-mismatch"):
            validate_result_columns(wrong_shape)

    def test_equipped_item_count_accepts_every_value_zero_through_six(self) -> None:
        arrays = _valid_columns(7)
        arrays["category_codes"] = np.array([0, 1, 2, 1, 2, 1, 2], dtype="u1")
        arrays["replacement_distances"] = arrays["category_codes"].copy()
        arrays["constraint_distances"] = np.array([0, .1, .2, .3, .4, .5, .6], dtype="<f4")
        arrays["equipped_item_counts"] = np.arange(7, dtype="u1")
        self.assertEqual(7, validate_result_columns(arrays))

    def test_payload_projections_are_checked_and_have_no_premature_overhead(self) -> None:
        one = project_result_payload(1)
        million = project_result_payload(1_000_000)
        cap = project_result_payload(5_000_000)
        self.assertEqual(233, one.payload_bytes)
        self.assertEqual(233_000_000, million.payload_bytes)
        self.assertEqual(1_165_000_000, cap.payload_bytes)
        self.assertAlmostEqual(222.20611572265625, million.payload_mib)
        self.assertAlmostEqual(1.0849907994270325, cap.payload_gib)
        self.assertEqual(cap.payload_bytes, cap.total_fixed_bytes)
        self.assertEqual((0, 0, 0), (cap.fixed_header_bytes, cap.fixed_manifest_bytes, cap.fixed_index_bytes))

    def test_schema_projection_and_references_have_deterministic_equality_and_hashes(self) -> None:
        self.assertEqual(RESULT_SCHEMA, RESULT_SCHEMA)
        self.assertEqual(hash(RESULT_SCHEMA), hash(RESULT_SCHEMA))
        self.assertEqual(project_result_payload(5_000_000), project_result_payload(5_000_000))
        self.assertEqual(hash(project_result_payload(5)), hash(project_result_payload(5)))
        reference = replacement_metadata_reference(4, ResultCategory.TWO_AWAY, (0, 1, 2, 3, 4, 5))
        self.assertEqual(reference, replacement_metadata_reference(4, "result.two_away", (0, 1, 2, 3, 4, 5)))
        with self.assertRaises(FrozenInstanceError):
            RESULT_SCHEMA.version = 2  # type: ignore[misc]

    def test_import_is_hardware_independent_and_creates_no_storage(self) -> None:
        code = (
            "import pathlib,sys; "
            "from src.optimizer.result_store import RESULT_ROW_BYTES; "
            "assert RESULT_ROW_BYTES == 233; "
            "assert not any(name == 'cupy' or name.startswith('cupy.') for name in sys.modules); "
            "assert not any(name == 'src.desktop' or name.startswith('src.desktop.') for name in sys.modules); "
            "assert list(pathlib.Path('.').iterdir()) == []; print('RESULT_SCHEMA_IMPORT_OK')"
        )
        with tempfile.TemporaryDirectory(prefix="e7-result-schema-") as temporary:
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(ROOT)
            completed = subprocess.run(
                [sys.executable, "-B", "-c", code],
                cwd=temporary,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("RESULT_SCHEMA_IMPORT_OK", completed.stdout.strip())


if __name__ == "__main__":
    unittest.main()
