"""Strict, UI-independent parsing for Fribbels ``gear.txt`` exports.

The parser owns source decoding, structural validation, row normalization, and
lossless immutable metadata. Identity merging, persistence, eligibility, and
file-selection UI belong to later Phase 01 tasks.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from numbers import Real
from pathlib import Path
from typing import Any

from src.optimizer.data.schema_common import FrozenJsonObject, freeze_json_object
from src.optimizer.domain import (
    ALLOWED_MAIN_STATS_BY_SLOT,
    GEAR_RANK_CATALOG,
    GEAR_SLOT_CATALOG,
    ITEM_STAT_CATALOG,
    REFORGE_MATERIAL_CATALOG,
    SET_CATALOG,
    GearItem,
    GearRank,
    GearSet,
    GearSlot,
    ItemStatType,
    ReforgeMaterial,
    item_stat_from_fribbels,
    resolve_gear_rank,
    resolve_gear_set,
    resolve_gear_slot,
    resolve_reforge_material,
)


Number = int | float
StatTotals = tuple[tuple[ItemStatType, Number], ...]
UTF8_BOM = b"\xef\xbb\xbf"


class FribbelsEncoding(StrEnum):
    UTF8 = "utf-8"
    UTF8_BOM = "utf-8-sig"


class FribbelsVariant(StrEnum):
    SCANNER = "scanner"
    ITEMS_ONLY = "items-only"
    ENRICHED = "enriched"


class ProjectionEvidenceState(StrEnum):
    MISSING = "missing"
    VALID = "valid"
    INVALID = "invalid"


class FribbelsDocumentError(ValueError):
    """A fatal source-file problem for which no rows can be returned."""

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message}")


@dataclass(frozen=True, slots=True)
class FribbelsIssue:
    """An actionable recoverable warning or rejected-item explanation."""

    code: str
    path: str
    message: str
    item_index: int | None = None
    hero_index: int | None = None


@dataclass(frozen=True, slots=True)
class ImportedHeroReference:
    """The minimum hero data needed to retain an equipped-item reference."""

    hero_id: str
    name: str | None
    stars: int | None
    awaken: int | None
    raw: FrozenJsonObject | Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw", freeze_json_object(self.raw, "ImportedHeroReference.raw"))


@dataclass(frozen=True, slots=True)
class NormalizedFribbelsStat:
    """A normalized main/substat plus all source stat evidence."""

    stat_type: ItemStatType
    value: Number
    rolls: int | None
    ingame_rolls: int | None
    modified: bool | None
    reforged_value: Number | None
    raw: FrozenJsonObject | Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw", freeze_json_object(self.raw, "NormalizedFribbelsStat.raw"))


@dataclass(frozen=True, slots=True)
class FribbelsItemProjection:
    """Deterministic current and reforge-projected item contributions."""

    current_totals: StatTotals
    reforged_totals: StatTotals
    augmented_evidence: ProjectionEvidenceState
    reforged_evidence: ProjectionEvidenceState

    def __post_init__(self) -> None:
        object.__setattr__(self, "current_totals", tuple(self.current_totals))
        object.__setattr__(self, "reforged_totals", tuple(self.reforged_totals))

    def current_value(self, stat_type: ItemStatType) -> Number:
        return dict(self.current_totals)[stat_type]

    def reforged_value(self, stat_type: ItemStatType) -> Number:
        return dict(self.reforged_totals)[stat_type]


@dataclass(frozen=True, slots=True)
class ParsedFribbelsItem:
    """One accepted source row before P01-T03 chooses its stable identity."""

    source_index: int
    ingame_id: str | None
    source_id: str | None
    name: str | None
    slot: GearSlot
    gear_set: GearSet
    rank: GearRank
    material: ReforgeMaterial | None
    item_level: int
    enhance: int
    main_stat: NormalizedFribbelsStat
    substats: tuple[NormalizedFribbelsStat, ...]
    equipped_hero_id: str | None
    equipped_by_name: str | None
    locked: bool
    projection: FribbelsItemProjection
    raw: FrozenJsonObject | Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "substats", tuple(self.substats))
        object.__setattr__(self, "raw", freeze_json_object(self.raw, "ParsedFribbelsItem.raw"))

    def to_gear_item(self, item_id: str) -> GearItem:
        """Create the optimizer contribution using an identity supplied later."""

        return GearItem(
            item_id=item_id,
            dense_id=None,
            slot=self.slot,
            gear_set=self.gear_set,
            item_level=self.item_level,
            enhance=self.enhance,
            main_stat=self.main_stat.stat_type,
            main_stat_value=self.main_stat.value,
            substats=tuple((stat.stat_type, stat.value) for stat in self.substats),
            equipped_hero_id=self.equipped_hero_id,
            locked=self.locked,
        )


@dataclass(frozen=True, slots=True)
class FribbelsParseResult:
    """Immutable accepted rows, hero references, issues, and source metadata."""

    encoding: FribbelsEncoding
    variant: FribbelsVariant
    source_item_count: int
    items: tuple[ParsedFribbelsItem, ...]
    heroes: tuple[ImportedHeroReference, ...]
    warnings: tuple[FribbelsIssue, ...]
    rejections: tuple[FribbelsIssue, ...]
    root_metadata: FrozenJsonObject | Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))
        object.__setattr__(self, "heroes", tuple(self.heroes))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "rejections", tuple(self.rejections))
        object.__setattr__(
            self,
            "root_metadata",
            freeze_json_object(self.root_metadata, "FribbelsParseResult.root_metadata"),
        )

    @property
    def accepted_count(self) -> int:
        return len(self.items)

    @property
    def rejected_count(self) -> int:
        return len(self.rejections)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)

    @property
    def warning_item_count(self) -> int:
        return len({issue.item_index for issue in self.warnings if issue.item_index is not None})


class _DuplicateKeyError(ValueError):
    pass


class _InvalidNumberError(ValueError):
    pass


class _ItemRejected(ValueError):
    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(message)


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(key)
        result[key] = value
    return result


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise _InvalidNumberError(value)
    return parsed


def _reject_json_constant(value: str) -> None:
    raise _InvalidNumberError(value)


def _decode_document(data: bytes) -> tuple[str, FribbelsEncoding]:
    encoding = FribbelsEncoding.UTF8_BOM if data.startswith(UTF8_BOM) else FribbelsEncoding.UTF8
    try:
        return data.decode(encoding.value, errors="strict"), encoding
    except UnicodeDecodeError as error:
        raise FribbelsDocumentError(
            "invalid-utf8",
            "$file",
            f"The selected file is not valid UTF-8 near byte {error.start}.",
        ) from error


def _load_json(text: str) -> object:
    try:
        return json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_float=_finite_float,
            parse_constant=_reject_json_constant,
        )
    except _DuplicateKeyError as error:
        raise FribbelsDocumentError(
            "duplicate-key",
            "$",
            "JSON contains a duplicate object key.",
        ) from error
    except _InvalidNumberError as error:
        raise FribbelsDocumentError(
            "invalid-number",
            "$",
            f"JSON contains non-finite number {str(error)!r}.",
        ) from error
    except json.JSONDecodeError as error:
        raise FribbelsDocumentError(
            "malformed-json",
            "$",
            f"Malformed JSON at line {error.lineno}, column {error.colno}.",
        ) from error


def _source_enum(
    value: object,
    *,
    path: str,
    label: str,
    catalog: Mapping[Any, Any],
    resolver: Any,
) -> Any:
    if not isinstance(value, str) or not value.strip():
        raise _ItemRejected("invalid-field", path, f"{label} must be a Fribbels string value.")
    try:
        resolved = resolver(value)
    except ValueError:
        allowed = ", ".join(metadata.fribbels_name for metadata in catalog.values())
        raise _ItemRejected("unknown-vocabulary", path, f"{label} must be one of: {allowed}.") from None
    source_name = catalog[resolved].fribbels_name
    if value.strip().casefold() != source_name.casefold():
        allowed = ", ".join(metadata.fribbels_name for metadata in catalog.values())
        raise _ItemRejected("unknown-vocabulary", path, f"{label} must be one of: {allowed}.")
    return resolved


def _required(row: Mapping[str, Any], key: str, path: str) -> Any:
    if key not in row:
        raise _ItemRejected("missing-field", f"{path}.{key}", f"Required field {key!r} is missing.")
    return row[key]


def _integer(value: object, path: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _ItemRejected("invalid-field", path, "Value must be an integer.")
    if value < minimum or value > maximum:
        raise _ItemRejected(
            "invalid-field",
            path,
            f"Value must be between {minimum} and {maximum}.",
        )
    return value


def _number(value: object, path: str, *, positive: bool = False) -> Number:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise _ItemRejected("invalid-field", path, "Value must be a finite number.")
    normalized: Number = int(value) if isinstance(value, int) else float(value)
    if not math.isfinite(normalized):
        raise _ItemRejected("invalid-field", path, "Value must be a finite number.")
    if positive and normalized <= 0:
        raise _ItemRejected("invalid-field", path, "Value must be greater than zero.")
    if not positive and normalized < 0:
        raise _ItemRejected("invalid-field", path, "Value must not be negative.")
    return normalized


def _optional_integer(row: Mapping[str, Any], key: str, path: str) -> int | None:
    if key not in row:
        return None
    return _integer(row[key], f"{path}.{key}", minimum=0, maximum=100)


def _optional_boolean(row: Mapping[str, Any], key: str, path: str) -> bool | None:
    if key not in row:
        return None
    value = row[key]
    if not isinstance(value, bool):
        raise _ItemRejected("invalid-field", f"{path}.{key}", "Value must be a boolean.")
    return value


def _fribbels_stat_type(value: object, path: str) -> ItemStatType:
    if not isinstance(value, str) or not value.strip():
        raise _ItemRejected("invalid-field", path, "Stat type must be a Fribbels string value.")
    try:
        stat_type = item_stat_from_fribbels(value)
    except ValueError:
        allowed = ", ".join(metadata.fribbels_name for metadata in ITEM_STAT_CATALOG.values())
        raise _ItemRejected("unknown-stat", path, f"Stat type must be one of: {allowed}.") from None
    if value.strip().casefold() != ITEM_STAT_CATALOG[stat_type].fribbels_name.casefold():
        allowed = ", ".join(metadata.fribbels_name for metadata in ITEM_STAT_CATALOG.values())
        raise _ItemRejected("unknown-stat", path, f"Stat type must be one of: {allowed}.")
    return stat_type


def _parse_stat(value: object, path: str, *, main: bool) -> NormalizedFribbelsStat:
    if not isinstance(value, Mapping):
        raise _ItemRejected("invalid-stat", path, "Stat must be an object.")
    stat_type = _fribbels_stat_type(_required(value, "type", path), f"{path}.type")
    stat_value = _number(_required(value, "value", path), f"{path}.value", positive=True)
    reforged_value = None
    if "reforgedValue" in value:
        reforged_value = _number(value["reforgedValue"], f"{path}.reforgedValue", positive=True)

    rolls = None if main else _optional_integer(value, "rolls", path)
    ingame_rolls = None if main else _optional_integer(value, "ingameRolls", path)
    modified = None if main else _optional_boolean(value, "modified", path)
    return NormalizedFribbelsStat(
        stat_type=stat_type,
        value=stat_value,
        rolls=rolls,
        ingame_rolls=ingame_rolls,
        modified=modified,
        reforged_value=reforged_value,
        raw=value,
    )


_FORBIDDEN_SUBSTATS: Mapping[GearSlot, frozenset[ItemStatType]] = {
    GearSlot.WEAPON: frozenset({ItemStatType.FLAT_DEFENSE, ItemStatType.DEFENSE_PERCENT}),
    GearSlot.ARMOR: frozenset({ItemStatType.FLAT_ATTACK, ItemStatType.ATTACK_PERCENT}),
}


def _stat_totals(
    main_stat: NormalizedFribbelsStat,
    substats: tuple[NormalizedFribbelsStat, ...],
    *,
    reforged: bool,
    include_main: bool,
) -> StatTotals:
    totals: dict[ItemStatType, Number] = {stat_type: 0 for stat_type in ItemStatType}
    for stat in substats:
        value = stat.reforged_value if reforged and stat.reforged_value is not None else stat.value
        totals[stat.stat_type] += value
    if include_main:
        main_value = (
            main_stat.reforged_value
            if reforged and main_stat.reforged_value is not None
            else main_stat.value
        )
        totals[main_stat.stat_type] += main_value
    return tuple((stat_type, totals[stat_type]) for stat_type in ItemStatType)


def _evidence_is_consistent(
    value: object,
    *,
    main_stat: NormalizedFribbelsStat,
    substats: tuple[NormalizedFribbelsStat, ...],
    reforged: bool,
) -> bool:
    if not isinstance(value, Mapping):
        return False
    expected_substats = dict(
        _stat_totals(main_stat, substats, reforged=reforged, include_main=False)
    )
    for stat_type, expected in expected_substats.items():
        key = ITEM_STAT_CATALOG[stat_type].fribbels_name
        if key not in value:
            return False
        supplied = value[key]
        if isinstance(supplied, bool) or not isinstance(supplied, Real):
            return False
        numeric = float(supplied)
        if not math.isfinite(numeric) or not math.isclose(numeric, float(expected), abs_tol=1e-9):
            return False
    if "mainType" not in value or "mainValue" not in value:
        return False
    try:
        supplied_main_type = item_stat_from_fribbels(value["mainType"])
    except ValueError:
        return False
    expected_main_value = (
        main_stat.reforged_value
        if reforged and main_stat.reforged_value is not None
        else main_stat.value
    )
    supplied_main_value = value["mainValue"]
    if isinstance(supplied_main_value, bool) or not isinstance(supplied_main_value, Real):
        return False
    return supplied_main_type is main_stat.stat_type and math.isclose(
        float(supplied_main_value),
        float(expected_main_value),
        abs_tol=1e-9,
    )


def _totals_from_evidence(value: Mapping[str, object]) -> StatTotals:
    totals: dict[ItemStatType, Number] = {}
    for stat_type in ItemStatType:
        supplied = value[ITEM_STAT_CATALOG[stat_type].fribbels_name]
        assert isinstance(supplied, Real) and not isinstance(supplied, bool)
        totals[stat_type] = int(supplied) if isinstance(supplied, int) else float(supplied)
    main_type = item_stat_from_fribbels(value["mainType"])
    main_value = value["mainValue"]
    assert isinstance(main_value, Real) and not isinstance(main_value, bool)
    normalized_main: Number = int(main_value) if isinstance(main_value, int) else float(main_value)
    totals[main_type] += normalized_main
    return tuple((stat_type, totals[stat_type]) for stat_type in ItemStatType)


def _projection(
    row: Mapping[str, Any],
    *,
    path: str,
    item_index: int,
    main_stat: NormalizedFribbelsStat,
    substats: tuple[NormalizedFribbelsStat, ...],
    warnings: list[FribbelsIssue],
) -> FribbelsItemProjection:
    states: dict[str, ProjectionEvidenceState] = {}
    for key, reforged in (("augmentedStats", False), ("reforgedStats", True)):
        if key not in row:
            states[key] = ProjectionEvidenceState.MISSING
            continue
        if _evidence_is_consistent(
            row[key],
            main_stat=main_stat,
            substats=substats,
            reforged=reforged,
        ):
            states[key] = ProjectionEvidenceState.VALID
            continue
        states[key] = ProjectionEvidenceState.INVALID
        warnings.append(
            FribbelsIssue(
                code=f"invalid-{key.replace('Stats', '-stats').lower()}",
                path=f"{path}.{key}",
                message=(
                    f"{key} did not match the normalized main/substat evidence; "
                    "a deterministic per-stat fallback was used."
                ),
                item_index=item_index,
            )
        )

    current_totals = (
        _totals_from_evidence(row["augmentedStats"])
        if states["augmentedStats"] is ProjectionEvidenceState.VALID
        else _stat_totals(main_stat, substats, reforged=False, include_main=True)
    )
    reforged_totals = (
        _totals_from_evidence(row["reforgedStats"])
        if states["reforgedStats"] is ProjectionEvidenceState.VALID
        else _stat_totals(main_stat, substats, reforged=True, include_main=True)
    )
    return FribbelsItemProjection(
        current_totals=current_totals,
        reforged_totals=reforged_totals,
        augmented_evidence=states["augmentedStats"],
        reforged_evidence=states["reforgedStats"],
    )


def _identity(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError
    normalized = str(value).strip()
    if not normalized:
        raise ValueError
    return normalized


def _optional_identity(
    row: Mapping[str, Any],
    key: str,
    *,
    path: str,
    item_index: int,
    warnings: list[FribbelsIssue],
    undefined_is_none: bool = False,
) -> str | None:
    if key not in row:
        return None
    value = row[key]
    if undefined_is_none and isinstance(value, str) and value.strip() == "undefined":
        return None
    try:
        return _identity(value)
    except ValueError:
        warnings.append(
            FribbelsIssue(
                code="invalid-optional-identity",
                path=f"{path}.{key}",
                message="Optional identity must be a non-empty string, integer, or null.",
                item_index=item_index,
            )
        )
        return None


def _optional_text(
    row: Mapping[str, Any],
    key: str,
    *,
    path: str,
    item_index: int,
    warnings: list[FribbelsIssue],
) -> str | None:
    if key not in row or row[key] is None:
        return None
    value = row[key]
    if not isinstance(value, str) or not value.strip():
        warnings.append(
            FribbelsIssue(
                code="invalid-optional-text",
                path=f"{path}.{key}",
                message="Optional text must be a non-empty string or null.",
                item_index=item_index,
            )
        )
        return None
    return value.strip()


def _parse_material(
    row: Mapping[str, Any],
    *,
    path: str,
    item_index: int,
    warnings: list[FribbelsIssue],
) -> ReforgeMaterial | None:
    if "material" not in row or row["material"] is None:
        return None
    try:
        return _source_enum(
            row["material"],
            path=f"{path}.material",
            label="Material",
            catalog=REFORGE_MATERIAL_CATALOG,
            resolver=resolve_reforge_material,
        )
    except _ItemRejected:
        warnings.append(
            FribbelsIssue(
                code="unknown-material",
                path=f"{path}.material",
                message="Optional material was not Hunt, Conversion, or Unknown; it was retained raw.",
                item_index=item_index,
            )
        )
        return None


def _parse_item(
    value: object,
    *,
    item_index: int,
    hero_ids: frozenset[str],
    hero_container_present: bool,
) -> tuple[ParsedFribbelsItem, tuple[FribbelsIssue, ...]]:
    path = f"$.items[{item_index}]"
    if not isinstance(value, Mapping):
        raise _ItemRejected("invalid-item", path, "Item row must be an object.")
    warnings: list[FribbelsIssue] = []

    slot = _source_enum(
        _required(value, "gear", path),
        path=f"{path}.gear",
        label="Gear slot",
        catalog=GEAR_SLOT_CATALOG,
        resolver=resolve_gear_slot,
    )
    gear_set = _source_enum(
        _required(value, "set", path),
        path=f"{path}.set",
        label="Gear set",
        catalog=SET_CATALOG,
        resolver=resolve_gear_set,
    )
    rank = _source_enum(
        _required(value, "rank", path),
        path=f"{path}.rank",
        label="Gear rank",
        catalog=GEAR_RANK_CATALOG,
        resolver=resolve_gear_rank,
    )
    item_level = _integer(
        _required(value, "level", path),
        f"{path}.level",
        minimum=1,
        maximum=100,
    )
    enhance = _integer(
        _required(value, "enhance", path),
        f"{path}.enhance",
        minimum=0,
        maximum=15,
    )
    main_stat = _parse_stat(_required(value, "main", path), f"{path}.main", main=True)
    if main_stat.stat_type not in ALLOWED_MAIN_STATS_BY_SLOT[slot]:
        raise _ItemRejected(
            "illegal-main-stat",
            f"{path}.main.type",
            "Main stat is not legal for this gear slot.",
        )

    raw_substats = _required(value, "substats", path)
    if not isinstance(raw_substats, list):
        raise _ItemRejected("invalid-substats", f"{path}.substats", "Substats must be an array.")
    if len(raw_substats) > 4:
        raise _ItemRejected(
            "invalid-substats",
            f"{path}.substats",
            "An item may contain at most four substats.",
        )
    substats = tuple(
        _parse_stat(substat, f"{path}.substats[{index}]", main=False)
        for index, substat in enumerate(raw_substats)
    )
    seen: set[ItemStatType] = set()
    forbidden = _FORBIDDEN_SUBSTATS.get(slot, frozenset())
    for index, stat in enumerate(substats):
        stat_path = f"{path}.substats[{index}].type"
        if stat.stat_type is main_stat.stat_type:
            raise _ItemRejected(
                "duplicate-main-stat",
                stat_path,
                "A substat must not duplicate the main stat type.",
            )
        if stat.stat_type in seen:
            raise _ItemRejected(
                "duplicate-substat",
                stat_path,
                "Substat types must be unique within an item.",
            )
        if stat.stat_type in forbidden:
            raise _ItemRejected(
                "illegal-substat",
                stat_path,
                "Substat is not legal for this gear slot.",
            )
        seen.add(stat.stat_type)

    ingame_id = _optional_identity(
        value,
        "ingameId",
        path=path,
        item_index=item_index,
        warnings=warnings,
    )
    source_id = _optional_identity(
        value,
        "id",
        path=path,
        item_index=item_index,
        warnings=warnings,
    )
    ingame_owner = _optional_identity(
        value,
        "ingameEquippedId",
        path=path,
        item_index=item_index,
        warnings=warnings,
        undefined_is_none=True,
    )
    enriched_owner = _optional_identity(
        value,
        "equippedById",
        path=path,
        item_index=item_index,
        warnings=warnings,
        undefined_is_none=True,
    )
    if ingame_owner is not None and enriched_owner is not None and ingame_owner != enriched_owner:
        warnings.append(
            FribbelsIssue(
                code="owner-conflict",
                path=f"{path}.ingameEquippedId",
                message=(
                    "ingameEquippedId conflicts with equippedById; the in-game owner ID was retained."
                ),
                item_index=item_index,
            )
        )
    equipped_hero_id = ingame_owner if ingame_owner is not None else enriched_owner
    if hero_container_present and equipped_hero_id is not None and equipped_hero_id not in hero_ids:
        warnings.append(
            FribbelsIssue(
                code="unresolved-owner",
                path=f"{path}.ingameEquippedId",
                message="Equipped owner ID does not resolve to an imported hero and was retained.",
                item_index=item_index,
            )
        )

    locked = False
    if "locked" in value:
        if isinstance(value["locked"], bool):
            locked = value["locked"]
        else:
            warnings.append(
                FribbelsIssue(
                    code="invalid-lock-state",
                    path=f"{path}.locked",
                    message="Optional locked value must be boolean; false was used.",
                    item_index=item_index,
                )
            )

    projection = _projection(
        value,
        path=path,
        item_index=item_index,
        main_stat=main_stat,
        substats=substats,
        warnings=warnings,
    )
    return (
        ParsedFribbelsItem(
            source_index=item_index,
            ingame_id=ingame_id,
            source_id=source_id,
            name=_optional_text(
                value,
                "name",
                path=path,
                item_index=item_index,
                warnings=warnings,
            ),
            slot=slot,
            gear_set=gear_set,
            rank=rank,
            material=_parse_material(
                value,
                path=path,
                item_index=item_index,
                warnings=warnings,
            ),
            item_level=item_level,
            enhance=enhance,
            main_stat=main_stat,
            substats=substats,
            equipped_hero_id=equipped_hero_id,
            equipped_by_name=_optional_text(
                value,
                "equippedByName",
                path=path,
                item_index=item_index,
                warnings=warnings,
            ),
            locked=locked,
            projection=projection,
            raw=value,
        ),
        tuple(warnings),
    )


def _hero_optional_text(
    row: Mapping[str, Any],
    key: str,
    *,
    path: str,
    hero_index: int,
    warnings: list[FribbelsIssue],
) -> str | None:
    if key not in row or row[key] is None:
        return None
    value = row[key]
    if not isinstance(value, str) or not value.strip():
        warnings.append(
            FribbelsIssue(
                code="invalid-hero-metadata",
                path=f"{path}.{key}",
                message="Optional hero text must be a non-empty string or null.",
                hero_index=hero_index,
            )
        )
        return None
    return value.strip()


def _hero_optional_integer(
    row: Mapping[str, Any],
    key: str,
    *,
    path: str,
    hero_index: int,
    warnings: list[FribbelsIssue],
) -> int | None:
    if key not in row or row[key] is None:
        return None
    value = row[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 6:
        warnings.append(
            FribbelsIssue(
                code="invalid-hero-metadata",
                path=f"{path}.{key}",
                message="Optional hero grade must be an integer from 0 through 6 or null.",
                hero_index=hero_index,
            )
        )
        return None
    return value


def _parse_heroes(values: list[object]) -> tuple[
    tuple[ImportedHeroReference, ...],
    tuple[FribbelsIssue, ...],
]:
    heroes: list[ImportedHeroReference] = []
    warnings: list[FribbelsIssue] = []
    seen_ids: set[str] = set()
    for index, value in enumerate(values):
        path = f"$.heroes[{index}]"
        if not isinstance(value, Mapping):
            warnings.append(
                FribbelsIssue(
                    code="invalid-hero-row",
                    path=path,
                    message="Hero row must be an object and was skipped.",
                    hero_index=index,
                )
            )
            continue
        if "id" not in value:
            warnings.append(
                FribbelsIssue(
                    code="missing-hero-id",
                    path=f"{path}.id",
                    message="Hero row has no ownership ID and was skipped.",
                    hero_index=index,
                )
            )
            continue
        try:
            hero_id = _identity(value["id"])
        except ValueError:
            hero_id = None
        if hero_id is None:
            warnings.append(
                FribbelsIssue(
                    code="invalid-hero-id",
                    path=f"{path}.id",
                    message="Hero ID must be a non-empty string or integer; the row was skipped.",
                    hero_index=index,
                )
            )
            continue
        if hero_id in seen_ids:
            warnings.append(
                FribbelsIssue(
                    code="duplicate-hero-id",
                    path=f"{path}.id",
                    message="Duplicate hero ownership ID was skipped.",
                    hero_index=index,
                )
            )
            continue
        seen_ids.add(hero_id)
        heroes.append(
            ImportedHeroReference(
                hero_id=hero_id,
                name=_hero_optional_text(
                    value,
                    "name",
                    path=path,
                    hero_index=index,
                    warnings=warnings,
                ),
                stars=_hero_optional_integer(
                    value,
                    "stars",
                    path=path,
                    hero_index=index,
                    warnings=warnings,
                ),
                awaken=_hero_optional_integer(
                    value,
                    "awaken",
                    path=path,
                    hero_index=index,
                    warnings=warnings,
                ),
                raw=value,
            )
        )
    return tuple(heroes), tuple(warnings)


_ENRICHED_ITEM_FIELDS = frozenset({
    "augmentedStats",
    "reforgedStats",
    "material",
    "locked",
    "equippedById",
    "equippedByName",
    "modId",
    "allowedMods",
    "upgradeable",
    "reforgeable",
})


def _variant(root: Mapping[str, Any], items: list[object]) -> FribbelsVariant:
    if "heroes" not in root:
        return FribbelsVariant.ITEMS_ONLY
    if any(isinstance(item, Mapping) and _ENRICHED_ITEM_FIELDS.intersection(item) for item in items):
        return FribbelsVariant.ENRICHED
    return FribbelsVariant.SCANNER


def parse_fribbels_gear_bytes(data: bytes) -> FribbelsParseResult:
    """Parse raw ``gear.txt`` bytes without performing external side effects."""

    if not isinstance(data, bytes):
        raise TypeError("parse_fribbels_gear_bytes data must be bytes.")
    text, encoding = _decode_document(data)
    document = _load_json(text)
    if not isinstance(document, Mapping):
        raise FribbelsDocumentError("wrong-root", "$", "Fribbels document root must be an object.")
    if "items" not in document:
        raise FribbelsDocumentError("missing-items", "$.items", "Required items array is missing.")
    raw_items = document["items"]
    if not isinstance(raw_items, list):
        raise FribbelsDocumentError(
            "invalid-items-container",
            "$.items",
            "Fribbels items must be an array.",
        )
    hero_container_present = "heroes" in document
    raw_heroes = document.get("heroes", [])
    if not isinstance(raw_heroes, list):
        raise FribbelsDocumentError(
            "invalid-heroes-container",
            "$.heroes",
            "Fribbels heroes must be an array when present.",
        )

    heroes, hero_warnings = _parse_heroes(raw_heroes)
    hero_ids = frozenset(hero.hero_id for hero in heroes)
    items: list[ParsedFribbelsItem] = []
    warnings = list(hero_warnings)
    rejections: list[FribbelsIssue] = []
    for index, raw_item in enumerate(raw_items):
        try:
            item, item_warnings = _parse_item(
                raw_item,
                item_index=index,
                hero_ids=hero_ids,
                hero_container_present=hero_container_present,
            )
        except _ItemRejected as error:
            rejections.append(
                FribbelsIssue(
                    code=error.code,
                    path=error.path,
                    message=error.message,
                    item_index=index,
                )
            )
            continue
        items.append(item)
        warnings.extend(item_warnings)

    root_metadata = {
        key: value for key, value in document.items() if key not in {"items", "heroes"}
    }
    return FribbelsParseResult(
        encoding=encoding,
        variant=_variant(document, raw_items),
        source_item_count=len(raw_items),
        items=tuple(items),
        heroes=heroes,
        warnings=tuple(warnings),
        rejections=tuple(rejections),
        root_metadata=root_metadata,
    )


def parse_fribbels_gear_file(path: str | os.PathLike[str]) -> FribbelsParseResult:
    """Read and parse a caller-selected Fribbels export path."""

    try:
        data = Path(path).read_bytes()
    except (OSError, TypeError, ValueError) as error:
        raise FribbelsDocumentError(
            "file-read",
            "$file",
            "The selected Fribbels export could not be read.",
        ) from error
    return parse_fribbels_gear_bytes(data)


__all__ = [
    "FribbelsDocumentError",
    "FribbelsEncoding",
    "FribbelsIssue",
    "FribbelsItemProjection",
    "FribbelsParseResult",
    "FribbelsVariant",
    "ImportedHeroReference",
    "NormalizedFribbelsStat",
    "ParsedFribbelsItem",
    "ProjectionEvidenceState",
    "parse_fribbels_gear_bytes",
    "parse_fribbels_gear_file",
]
