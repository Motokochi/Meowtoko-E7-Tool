"""Pure compilation of validated set patterns into compact numeric vectors."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from src.optimizer.domain import (
    FRIBBELS_SET_ORDER,
    SET_CATALOG,
    SetMetadata,
    SetPattern,
)
from src.optimizer.domain.enums import GearSet


FOUR_PLUS_TWO = "4+2"
THREE_TWO_PIECE = "2+2+2"
FLEXIBLE = "flexible"
COMPILED_SET_PATTERN_KINDS = (FOUR_PLUS_TWO, THREE_TWO_PIECE, FLEXIBLE)
SET_PATTERN_PIECE_COUNT = 6
SET_PATTERN_VECTOR_LENGTH = 24


class SetPatternCompilationError(ValueError):
    """Actionable invalid-input, catalog, or compiled-vector failure."""

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code} at {path}: {message}")


def _error(code: str, path: str, message: str) -> SetPatternCompilationError:
    return SetPatternCompilationError(code, path, message)


def _numeric_tuple(value: object, path: str) -> tuple[int, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise _error("invalid-integer-vector", path, "must be a sequence of integers.")
    try:
        values = tuple(value)  # type: ignore[arg-type]
    except TypeError:
        raise _error("invalid-integer-vector", path, "must be a sequence of integers.") from None
    if any(isinstance(item, bool) or not isinstance(item, int) for item in values):
        raise _error(
            "invalid-integer-vector",
            path,
            "must contain integers; boolean values are not accepted.",
        )
    return values


def _validated_catalog_by_index() -> tuple[tuple[GearSet, SetMetadata], ...]:
    """Return the catalog in wire order, rejecting any index drift."""

    if len(FRIBBELS_SET_ORDER) != SET_PATTERN_VECTOR_LENGTH:
        raise _error(
            "set-catalog-size-mismatch",
            "FRIBBELS_SET_ORDER",
            f"must contain exactly {SET_PATTERN_VECTOR_LENGTH} sets; "
            f"found {len(FRIBBELS_SET_ORDER)}.",
        )
    if set(SET_CATALOG) != set(FRIBBELS_SET_ORDER):
        raise _error(
            "set-catalog-membership-mismatch",
            "SET_CATALOG",
            "must contain exactly the sets in FRIBBELS_SET_ORDER.",
        )

    result: list[tuple[GearSet, SetMetadata]] = []
    for expected_index, gear_set in enumerate(FRIBBELS_SET_ORDER):
        metadata = SET_CATALOG[gear_set]
        if (
            isinstance(metadata.fribbels_index, bool)
            or not isinstance(metadata.fribbels_index, int)
            or metadata.fribbels_index != expected_index
        ):
            raise _error(
                "set-catalog-index-mismatch",
                f"SET_CATALOG[{gear_set.value}].fribbels_index",
                f"must be {expected_index} to agree with FRIBBELS_SET_ORDER; "
                f"found {metadata.fribbels_index!r}.",
            )
        if (
            isinstance(metadata.pieces_required, bool)
            or not isinstance(metadata.pieces_required, int)
            or metadata.pieces_required not in (2, 4)
        ):
            raise _error(
                "set-catalog-piece-count",
                f"SET_CATALOG[{gear_set.value}].pieces_required",
                f"must be 2 or 4; found {metadata.pieces_required!r}.",
            )
        if not isinstance(metadata.stackable, bool):
            raise _error(
                "set-catalog-stackability",
                f"SET_CATALOG[{gear_set.value}].stackable",
                "must be boolean.",
            )
        result.append((gear_set, metadata))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class CompiledSetPattern:
    """Self-validating immutable numeric form of optional set requirements."""

    kind: str
    selected_set_indices: tuple[int, ...]
    group_piece_counts: tuple[int, ...]
    required_piece_counts: tuple[int, ...]
    expanded_required_set_indices: tuple[int, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or self.kind not in COMPILED_SET_PATTERN_KINDS:
            raise _error(
                "invalid-pattern-kind",
                "CompiledSetPattern.kind",
                f"must be one of {COMPILED_SET_PATTERN_KINDS!r}.",
            )

        selected = _numeric_tuple(
            self.selected_set_indices,
            "CompiledSetPattern.selected_set_indices",
        )
        group_counts = _numeric_tuple(
            self.group_piece_counts,
            "CompiledSetPattern.group_piece_counts",
        )
        required = _numeric_tuple(
            self.required_piece_counts,
            "CompiledSetPattern.required_piece_counts",
        )
        expanded = _numeric_tuple(
            self.expanded_required_set_indices,
            "CompiledSetPattern.expanded_required_set_indices",
        )
        catalog = _validated_catalog_by_index()

        expected_groups = (
            (4, 2)
            if self.kind == FOUR_PLUS_TWO
            else (2, 2, 2)
            if self.kind == THREE_TWO_PIECE
            else group_counts
        )
        if len(selected) != len(expected_groups) or len(group_counts) != len(selected):
            raise _error(
                "pattern-group-shape",
                "CompiledSetPattern.selected_set_indices",
                f"kind {self.kind} requires {len(expected_groups)} selected groups "
                "and one piece count per group.",
            )
        if group_counts != expected_groups:
            raise _error(
                "pattern-group-counts",
                "CompiledSetPattern.group_piece_counts",
                f"kind {self.kind} requires canonical counts {expected_groups!r}; "
                f"found {group_counts!r}.",
            )
        if len(selected) > 3:
            raise _error(
                "pattern-group-shape",
                "CompiledSetPattern.selected_set_indices",
                "at most three optional set groups may be selected.",
            )
        if any(index < 0 or index >= SET_PATTERN_VECTOR_LENGTH for index in selected):
            raise _error(
                "set-index-out-of-range",
                "CompiledSetPattern.selected_set_indices",
                f"indices must be between 0 and {SET_PATTERN_VECTOR_LENGTH - 1}.",
            )

        selected_metadata = tuple(catalog[index][1] for index in selected)
        actual_group_counts = tuple(item.pieces_required for item in selected_metadata)
        if actual_group_counts != group_counts:
            raise _error(
                "set-group-catalog-mismatch",
                "CompiledSetPattern.group_piece_counts",
                "each group count must match its selected set's catalog piece requirement.",
            )
        canonical_selected = tuple(
            sorted(
                selected,
                key=lambda index: (-catalog[index][1].pieces_required, index),
            )
        )
        if selected != canonical_selected:
            raise _error(
                "noncanonical-selected-sets",
                "CompiledSetPattern.selected_set_indices",
                f"must use canonical group order {canonical_selected!r}.",
            )
        for index in set(selected):
            if selected.count(index) > 1 and not catalog[index][1].stackable:
                raise _error(
                    "nonstackable-set-repeat",
                    "CompiledSetPattern.selected_set_indices",
                    f"set index {index} is not stackable and cannot be repeated.",
                )

        if len(required) != SET_PATTERN_VECTOR_LENGTH:
            raise _error(
                "required-vector-length",
                "CompiledSetPattern.required_piece_counts",
                f"must contain exactly {SET_PATTERN_VECTOR_LENGTH} entries.",
            )
        if any(count < 0 for count in required):
            raise _error(
                "negative-required-piece-count",
                "CompiledSetPattern.required_piece_counts",
                "entries must be nonnegative.",
            )
        if sum(required) > SET_PATTERN_PIECE_COUNT:
            raise _error(
                "required-vector-sum",
                "CompiledSetPattern.required_piece_counts",
                f"entries must sum to at most {SET_PATTERN_PIECE_COUNT}; found {sum(required)}.",
            )

        expected_required = [0] * SET_PATTERN_VECTOR_LENGTH
        for index, count in zip(selected, group_counts, strict=True):
            expected_required[index] += count
        expected_required_tuple = tuple(expected_required)
        if required != expected_required_tuple:
            raise _error(
                "required-vector-mismatch",
                "CompiledSetPattern.required_piece_counts",
                "entries must exactly aggregate the selected groups and piece counts.",
            )

        expected_expanded = tuple(
            index
            for index, count in zip(selected, group_counts, strict=True)
            for _ in range(count)
        )
        if len(expanded) != sum(required):
            raise _error(
                "expanded-vector-length",
                "CompiledSetPattern.expanded_required_set_indices",
                f"must contain exactly {sum(required)} entries.",
            )
        if any(index < 0 or index >= SET_PATTERN_VECTOR_LENGTH for index in expanded):
            raise _error(
                "expanded-set-index-out-of-range",
                "CompiledSetPattern.expanded_required_set_indices",
                f"indices must be between 0 and {SET_PATTERN_VECTOR_LENGTH - 1}.",
            )
        if expanded != expected_expanded:
            raise _error(
                "expanded-vector-mismatch",
                "CompiledSetPattern.expanded_required_set_indices",
                f"must be the canonical group expansion {expected_expanded!r}.",
            )

        object.__setattr__(self, "selected_set_indices", selected)
        object.__setattr__(self, "group_piece_counts", group_counts)
        object.__setattr__(self, "required_piece_counts", required)
        object.__setattr__(self, "expanded_required_set_indices", expanded)


def compile_set_pattern(pattern: SetPattern) -> CompiledSetPattern:
    """Compile one domain-validated pattern without accepting raw payloads."""

    if not isinstance(pattern, SetPattern):
        raise _error(
            "invalid-set-pattern-input",
            "pattern",
            "must be an already-validated SetPattern instance.",
        )
    catalog = _validated_catalog_by_index()
    metadata_by_set: Mapping[GearSet, SetMetadata] = dict(catalog)
    selected = tuple(metadata_by_set[gear_set].fribbels_index for gear_set in pattern.sets)
    group_counts = tuple(metadata_by_set[gear_set].pieces_required for gear_set in pattern.sets)
    required = [0] * SET_PATTERN_VECTOR_LENGTH
    for index, count in zip(selected, group_counts, strict=True):
        required[index] += count
    expanded = tuple(
        index
        for index, count in zip(selected, group_counts, strict=True)
        for _ in range(count)
    )
    return CompiledSetPattern(
        kind=pattern.kind,
        selected_set_indices=selected,
        group_piece_counts=group_counts,
        required_piece_counts=tuple(required),
        expanded_required_set_indices=expanded,
    )


__all__ = [
    "COMPILED_SET_PATTERN_KINDS",
    "FLEXIBLE",
    "FOUR_PLUS_TWO",
    "SET_PATTERN_PIECE_COUNT",
    "SET_PATTERN_VECTOR_LENGTH",
    "THREE_TWO_PIECE",
    "CompiledSetPattern",
    "SetPatternCompilationError",
    "compile_set_pattern",
]
