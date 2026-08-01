from __future__ import annotations

import ast
import inspect
import unittest
from dataclasses import FrozenInstanceError, replace
from itertools import combinations_with_replacement
from types import MappingProxyType
from unittest.mock import patch

from src.optimizer.domain import (
    FRIBBELS_SET_ORDER,
    SET_CATALOG,
    DomainValidationError,
    GearSet,
    SetPattern,
)
from src.optimizer.search import (
    FOUR_PLUS_TWO,
    SET_PATTERN_PIECE_COUNT,
    SET_PATTERN_VECTOR_LENGTH,
    THREE_TWO_PIECE,
    CompiledSetPattern,
    SetPatternCompilationError,
    compile_set_pattern,
)
from src.optimizer.search import set_patterns as set_patterns_module


def _sets_with_piece_count(piece_count: int) -> tuple[GearSet, ...]:
    return tuple(
        gear_set
        for gear_set in FRIBBELS_SET_ORDER
        if SET_CATALOG[gear_set].pieces_required == piece_count
    )


def _expected_vector(groups: tuple[GearSet, ...]) -> tuple[int, ...]:
    result = [0] * SET_PATTERN_VECTOR_LENGTH
    for gear_set in groups:
        metadata = SET_CATALOG[gear_set]
        result[metadata.fribbels_index] += metadata.pieces_required
    return tuple(result)


def _expected_expanded(groups: tuple[GearSet, ...]) -> tuple[int, ...]:
    return tuple(
        SET_CATALOG[gear_set].fribbels_index
        for gear_set in groups
        for _ in range(SET_CATALOG[gear_set].pieces_required)
    )


class SetPatternCompilationTests(unittest.TestCase):
    def test_every_four_plus_two_catalog_pair_compiles_exactly(self) -> None:
        four_piece_sets = _sets_with_piece_count(4)
        two_piece_sets = _sets_with_piece_count(2)
        self.assertTrue(four_piece_sets)
        self.assertTrue(two_piece_sets)

        for four_piece in four_piece_sets:
            for two_piece in two_piece_sets:
                with self.subTest(four_piece=four_piece, two_piece=two_piece):
                    pattern = SetPattern((two_piece, four_piece))
                    compiled = compile_set_pattern(pattern)
                    expected_groups = (four_piece, two_piece)
                    self.assertEqual(FOUR_PLUS_TWO, compiled.kind)
                    self.assertEqual(
                        tuple(SET_CATALOG[item].fribbels_index for item in expected_groups),
                        compiled.selected_set_indices,
                    )
                    self.assertEqual((4, 2), compiled.group_piece_counts)
                    self.assertEqual(_expected_vector(expected_groups), compiled.required_piece_counts)
                    self.assertEqual(
                        _expected_expanded(expected_groups),
                        compiled.expanded_required_set_indices,
                    )

    def test_every_valid_three_two_piece_pattern_compiles_exactly(self) -> None:
        two_piece_sets = _sets_with_piece_count(2)
        compiled_count = 0
        for groups in combinations_with_replacement(two_piece_sets, 3):
            repeated_nonstackable = any(
                groups.count(gear_set) > 1 and not SET_CATALOG[gear_set].stackable
                for gear_set in set(groups)
            )
            if repeated_nonstackable:
                with self.assertRaises(DomainValidationError):
                    SetPattern(groups)
                continue
            pattern = SetPattern(tuple(reversed(groups)))
            compiled = compile_set_pattern(pattern)
            canonical_groups = pattern.sets
            compiled_count += 1
            with self.subTest(groups=groups):
                self.assertEqual(THREE_TWO_PIECE, compiled.kind)
                self.assertEqual(
                    tuple(SET_CATALOG[item].fribbels_index for item in canonical_groups),
                    compiled.selected_set_indices,
                )
                self.assertEqual((2, 2, 2), compiled.group_piece_counts)
                self.assertEqual(_expected_vector(canonical_groups), compiled.required_piece_counts)
                self.assertEqual(
                    _expected_expanded(canonical_groups),
                    compiled.expanded_required_set_indices,
                )
        self.assertGreater(compiled_count, 0)

    def test_hand_fixtures_pin_fribbels_indices_and_repetition(self) -> None:
        speed_health = compile_set_pattern(SetPattern((GearSet.HEALTH, GearSet.SPEED)))
        self.assertEqual((3, 0), speed_health.selected_set_indices)
        self.assertEqual((4, 2), speed_health.group_piece_counts)
        self.assertEqual(2, speed_health.required_piece_counts[0])
        self.assertEqual(4, speed_health.required_piece_counts[3])
        self.assertEqual((3, 3, 3, 3, 0, 0), speed_health.expanded_required_set_indices)

        repeated = compile_set_pattern(
            SetPattern((GearSet.DEFENSE, GearSet.HEALTH, GearSet.HEALTH))
        )
        self.assertEqual((0, 0, 1), repeated.selected_set_indices)
        self.assertEqual((2, 2, 2), repeated.group_piece_counts)
        self.assertEqual(4, repeated.required_piece_counts[0])
        self.assertEqual(2, repeated.required_piece_counts[1])
        self.assertEqual((0, 0, 0, 0, 1, 1), repeated.expanded_required_set_indices)
        self.assertEqual(SET_PATTERN_PIECE_COUNT, sum(repeated.required_piece_counts))

    def test_domain_accepts_optional_requirements_and_rejects_impossible_or_nonstackable_patterns(self) -> None:
        for groups in (
            (),
            (GearSet.SPEED,),
            (GearSet.HEALTH, GearSet.DEFENSE),
        ):
            with self.subTest(valid_groups=groups):
                compiled = compile_set_pattern(SetPattern(groups))
                self.assertEqual(
                    sum(SET_CATALOG[item].pieces_required for item in groups),
                    sum(compiled.required_piece_counts),
                )

        invalid = (
            (GearSet.SPEED, GearSet.ATTACK),
            (GearSet.SPEED, GearSet.SPEED, GearSet.HEALTH),
            (GearSet.IMMUNITY, GearSet.IMMUNITY, GearSet.HEALTH),
            (GearSet.PENETRATION, GearSet.PENETRATION, GearSet.TORRENT),
            (GearSet.HEALTH, GearSet.DEFENSE, GearSet.CRITICAL, GearSet.HIT),
        )
        for groups in invalid:
            with self.subTest(groups=groups):
                with self.assertRaisesRegex(DomainValidationError, "SetPattern"):
                    SetPattern(groups)

    def test_normalization_repeated_calls_and_catalog_order_are_stable(self) -> None:
        first_pattern = SetPattern((GearSet.DEFENSE, GearSet.HEALTH, GearSet.HEALTH))
        second_pattern = SetPattern((GearSet.HEALTH, GearSet.DEFENSE, GearSet.HEALTH))
        self.assertEqual(first_pattern, second_pattern)
        self.assertEqual(compile_set_pattern(first_pattern), compile_set_pattern(second_pattern))
        self.assertEqual(compile_set_pattern(first_pattern), compile_set_pattern(first_pattern))
        self.assertEqual(tuple(range(24)), tuple(SET_CATALOG[item].fribbels_index for item in FRIBBELS_SET_ORDER))

    def test_compiler_rejects_wrong_input_and_catalog_index_drift(self) -> None:
        for invalid in ({"sets": [GearSet.SPEED.value, GearSet.HEALTH.value]}, None, (GearSet.SPEED, GearSet.HEALTH)):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(SetPatternCompilationError, "already-validated SetPattern"):
                    compile_set_pattern(invalid)  # type: ignore[arg-type]

        bad_catalog = dict(SET_CATALOG)
        bad_catalog[GearSet.HEALTH] = replace(
            bad_catalog[GearSet.HEALTH],
            fribbels_index=7,
        )
        with patch.object(set_patterns_module, "SET_CATALOG", MappingProxyType(bad_catalog)):
            with self.assertRaisesRegex(SetPatternCompilationError, "set-catalog-index-mismatch"):
                compile_set_pattern(SetPattern((GearSet.SPEED, GearSet.HEALTH)))

    def test_direct_record_rejects_invalid_numeric_invariants(self) -> None:
        valid = compile_set_pattern(SetPattern((GearSet.SPEED, GearSet.HEALTH)))
        swapped_required = list(valid.required_piece_counts)
        swapped_required[0], swapped_required[3] = 4, 2
        negative_required = list(valid.required_piece_counts)
        negative_required[0], negative_required[3] = -1, 7
        noncanonical_triple = compile_set_pattern(
            SetPattern((GearSet.HEALTH, GearSet.DEFENSE, GearSet.CRITICAL))
        )
        nonstackable_required = [0] * 24
        nonstackable_required[0] = 2
        nonstackable_required[12] = 4

        invalid_changes = (
            {"kind": "four-plus-two"},
            {"kind": True},
            {"selected_set_indices": (3,)},
            {"selected_set_indices": (True, 0)},
            {"selected_set_indices": (3.0, 0)},
            {"selected_set_indices": (24, 0)},
            {"group_piece_counts": (4, True)},
            {"group_piece_counts": (4, 2.0)},
            {"group_piece_counts": (2, 4)},
            {"selected_set_indices": (0, 3)},
            {"required_piece_counts": valid.required_piece_counts[:-1]},
            {"required_piece_counts": valid.required_piece_counts[:3] + (True,) + valid.required_piece_counts[4:]},
            {"required_piece_counts": valid.required_piece_counts[:3] + (4.0,) + valid.required_piece_counts[4:]},
            {"required_piece_counts": tuple(negative_required)},
            {"required_piece_counts": tuple(value + (1 if index == 3 else 0) for index, value in enumerate(valid.required_piece_counts))},
            {"required_piece_counts": tuple(swapped_required)},
            {"expanded_required_set_indices": valid.expanded_required_set_indices[:-1]},
            {"expanded_required_set_indices": (True,) + valid.expanded_required_set_indices[1:]},
            {"expanded_required_set_indices": (3.0,) + valid.expanded_required_set_indices[1:]},
            {"expanded_required_set_indices": (24,) + valid.expanded_required_set_indices[1:]},
            {"expanded_required_set_indices": (0, 0, 3, 3, 3, 3)},
        )
        for changes in invalid_changes:
            with self.subTest(changes=changes):
                with self.assertRaises(SetPatternCompilationError):
                    replace(valid, **changes)

        with self.assertRaisesRegex(SetPatternCompilationError, "noncanonical-selected-sets"):
            replace(noncanonical_triple, selected_set_indices=(4, 0, 1))
        with self.assertRaisesRegex(SetPatternCompilationError, "nonstackable-set-repeat"):
            CompiledSetPattern(
                kind=THREE_TWO_PIECE,
                selected_set_indices=(0, 12, 12),
                group_piece_counts=(2, 2, 2),
                required_piece_counts=tuple(nonstackable_required),
                expanded_required_set_indices=(0, 0, 12, 12, 12, 12),
            )

    def test_output_is_deeply_immutable_and_hashable(self) -> None:
        source = compile_set_pattern(SetPattern((GearSet.SPEED, GearSet.HEALTH)))
        selected = list(source.selected_set_indices)
        counts = list(source.group_piece_counts)
        required = list(source.required_piece_counts)
        expanded = list(source.expanded_required_set_indices)
        copied = CompiledSetPattern(source.kind, selected, counts, required, expanded)  # type: ignore[arg-type]
        selected[0] = 0
        counts[0] = 2
        required[0] = 6
        expanded[0] = 0
        self.assertEqual(source, copied)
        self.assertIsInstance(hash(copied), int)
        with self.assertRaises(FrozenInstanceError):
            copied.kind = THREE_TWO_PIECE  # type: ignore[misc]

    def test_module_has_no_later_phase_dependencies(self) -> None:
        tree = ast.parse(inspect.getsource(set_patterns_module))
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
        forbidden_fragments = (
            "inventory",
            "enumerat",
            "replacement",
            "result_store",
            "cuda",
            "desktop",
            "repository",
        )
        self.assertFalse(
            tuple(
                name
                for name in imports
                if any(fragment in name.casefold() for fragment in forbidden_fragments)
            )
        )


if __name__ == "__main__":
    unittest.main()
