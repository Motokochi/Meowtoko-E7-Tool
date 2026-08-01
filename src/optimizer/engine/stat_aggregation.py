"""Pure Fribbels-compatible pre-set stat aggregation for one six-item build."""

from __future__ import annotations

import math
import struct
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from numbers import Real

from src.optimizer.data.artifact_repository import ArtifactSelection
from src.optimizer.data.character_profiles import CharacterProfileSelection
from src.optimizer.domain import (
    FINAL_STAT_ORDER,
    GEAR_SLOT_ORDER,
    FinalStat,
    GearItem,
    GearSet,
    GearSlot,
    HeroModifierContribution,
    HeroModifiers,
    HeroModifierStatType,
    ItemProjectionMode,
    ItemStatType,
    OptimizationRequest,
)


FRIBBELS_STAT_CALCULATOR_REVISION = "f49b0676c27d893ae4aa1b69920e4c98f37eb3fb"
FRIBBELS_STAT_CALCULATOR_PATH = (
    "backend/src/main/java/com/fribbels/core/StatCalculator.java"
)
FRIBBELS_STAT_CALCULATOR_GIT_BLOB_SHA1 = "dfd9b1e363905a0aef3a2fca2e3369acde8d020e"
FRIBBELS_GPU_KERNEL_PATH = "backend/src/main/java/com/fribbels/gpu/GpuOptimizerKernel.java"
FRIBBELS_GPU_KERNEL_GIT_BLOB_SHA1 = "80d34477fd0548be8f63f4086884756febac5425"


Number = int | float
StatTotals = tuple[tuple[ItemStatType, Number], ...]
FinalStats = tuple[tuple[FinalStat, int], ...]
UnroundedFinalStats = tuple[tuple[FinalStat, float], ...]
FinalStatMultipliers = tuple[tuple[FinalStat, float], ...]


class StatAggregationError(ValueError):
    """An actionable aggregation-input or selection failure."""

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = _text(code, "error.code")
        self.path = _text(path, "error.path")
        self.message = _text(message, "error.message")
        super().__init__(f"{self.code} at {self.path}: {self.message}")


class ItemProjectionEvidence(StrEnum):
    """Evidence retained alongside a complete current or projected total."""

    DOMAIN_CURRENT = "domain.current"
    FRIBBELS_MISSING = "fribbels.missing"
    FRIBBELS_VALID = "fribbels.valid"
    FRIBBELS_INVALID_FALLBACK = "fribbels.invalid-fallback"


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be a non-empty string.")
    return value.strip()


def _number(value: object, path: str) -> Number:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise StatAggregationError("invalid-number", path, "Expected a finite nonnegative number.")
    numeric = int(value) if isinstance(value, int) else float(value)
    if not math.isfinite(numeric) or numeric < 0:
        raise StatAggregationError("invalid-number", path, "Expected a finite nonnegative number.")
    return numeric


def _totals(value: object, path: str) -> StatTotals:
    raw_items = value.items() if isinstance(value, Mapping) else value
    if isinstance(value, (str, bytes)):
        raise StatAggregationError("invalid-stat-totals", path, "Expected stat/value pairs.")
    try:
        pairs = tuple(raw_items)
    except TypeError:
        raise StatAggregationError("invalid-stat-totals", path, "Expected stat/value pairs.") from None
    result: dict[ItemStatType, Number] = {}
    for index, pair in enumerate(pairs):
        try:
            raw_stat, raw_value = pair
        except (TypeError, ValueError):
            raise StatAggregationError(
                "invalid-stat-totals",
                f"{path}[{index}]",
                "Expected exactly one stat and one value.",
            ) from None
        try:
            stat = raw_stat if isinstance(raw_stat, ItemStatType) else ItemStatType(raw_stat)
        except (TypeError, ValueError):
            raise StatAggregationError(
                "unknown-item-stat",
                f"{path}[{index}].stat",
                "Expected a canonical ItemStatType stable ID.",
            ) from None
        if stat in result:
            raise StatAggregationError(
                "duplicate-item-stat",
                f"{path}.{stat.value}",
                "Each item stat total must appear exactly once.",
            )
        result[stat] = _number(raw_value, f"{path}.{stat.value}")
    missing = [stat.value for stat in ItemStatType if stat not in result]
    if missing:
        raise StatAggregationError(
            "partial-stat-totals",
            path,
            "Projection totals must contain every item stat; missing: " + ", ".join(missing) + ".",
        )
    return tuple((stat, result[stat]) for stat in ItemStatType)


def _evidence(value: object, path: str) -> ItemProjectionEvidence:
    if isinstance(value, ItemProjectionEvidence):
        return value
    try:
        return ItemProjectionEvidence(value)
    except (TypeError, ValueError):
        raise StatAggregationError(
            "invalid-projection-evidence",
            path,
            "Expected a canonical projection evidence state.",
        ) from None


def _fribbels_evidence(value: object) -> ItemProjectionEvidence:
    source_value = getattr(value, "value", value)
    return {
        "missing": ItemProjectionEvidence.FRIBBELS_MISSING,
        "valid": ItemProjectionEvidence.FRIBBELS_VALID,
        "invalid": ItemProjectionEvidence.FRIBBELS_INVALID_FALLBACK,
    }.get(source_value) or _raise_invalid_fribbels_evidence(source_value)


def _raise_invalid_fribbels_evidence(value: object) -> ItemProjectionEvidence:
    raise StatAggregationError(
        "invalid-projection-evidence",
        "item.projection.evidence",
        f"Unsupported Fribbels projection evidence state {value!r}.",
    )


@dataclass(frozen=True, slots=True)
class ProjectedGearItem:
    """One owned gear item with explicit complete current/projected totals."""

    item_id: str
    slot: GearSlot
    gear_set: GearSet
    current_totals: StatTotals | Mapping[ItemStatType | str, Number]
    current_evidence: ItemProjectionEvidence
    reforged_totals: StatTotals | Mapping[ItemStatType | str, Number] | None = None
    reforged_evidence: ItemProjectionEvidence | None = None
    dense_id: int | None = None
    main_stat: ItemStatType | None = None
    current_main_value: Number | None = None
    reforged_main_value: Number | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "item_id", _text(self.item_id, "item.itemId"))
        try:
            object.__setattr__(self, "slot", GearSlot(self.slot))
        except (TypeError, ValueError):
            raise StatAggregationError("invalid-slot", "item.slot", "Expected a canonical gear slot.") from None
        try:
            object.__setattr__(self, "gear_set", GearSet(self.gear_set))
        except (TypeError, ValueError):
            raise StatAggregationError("invalid-set", "item.set", "Expected a canonical gear set.") from None
        if self.dense_id is not None and (
            isinstance(self.dense_id, bool) or not isinstance(self.dense_id, int) or self.dense_id < 0
        ):
            raise StatAggregationError(
                "invalid-dense-id", "item.denseId", "Expected a nonnegative integer or null."
            )
        object.__setattr__(self, "current_totals", _totals(self.current_totals, "item.currentTotals"))
        object.__setattr__(
            self, "current_evidence", _evidence(self.current_evidence, "item.currentEvidence")
        )
        if self.main_stat is None:
            if self.current_main_value is not None or self.reforged_main_value is not None:
                raise StatAggregationError(
                    "orphan-main-value",
                    "item.mainStat",
                    "Main-stat values require a canonical main-stat type.",
                )
        else:
            try:
                main_stat = ItemStatType(self.main_stat)
            except (TypeError, ValueError):
                raise StatAggregationError(
                    "invalid-main-stat",
                    "item.mainStat",
                    "Expected a canonical item stat.",
                ) from None
            if self.current_main_value is None:
                raise StatAggregationError(
                    "missing-main-value",
                    "item.currentMainValue",
                    "A known main-stat type requires its current value.",
                )
            current_main = _number(self.current_main_value, "item.currentMainValue")
            if current_main > dict(self.current_totals)[main_stat]:
                raise StatAggregationError(
                    "main-value-exceeds-total",
                    "item.currentMainValue",
                    "The current main-stat value cannot exceed its complete stat total.",
                )
            reforged_main = (
                None
                if self.reforged_main_value is None
                else _number(self.reforged_main_value, "item.reforgedMainValue")
            )
            object.__setattr__(self, "main_stat", main_stat)
            object.__setattr__(self, "current_main_value", current_main)
            object.__setattr__(self, "reforged_main_value", reforged_main)
        if self.reforged_totals is None:
            if self.reforged_evidence is not None:
                raise StatAggregationError(
                    "orphan-projection-evidence",
                    "item.reforgedEvidence",
                    "Reforged evidence cannot exist without reforged totals.",
                )
            if self.reforged_main_value is not None:
                raise StatAggregationError(
                    "orphan-main-value",
                    "item.reforgedMainValue",
                    "A reforged main value cannot exist without reforged totals.",
                )
            return
        object.__setattr__(self, "reforged_totals", _totals(self.reforged_totals, "item.reforgedTotals"))
        if self.main_stat is not None:
            reforged_main = (
                self.current_main_value
                if self.reforged_main_value is None
                else self.reforged_main_value
            )
            assert reforged_main is not None
            if reforged_main > dict(self.reforged_totals)[self.main_stat]:
                raise StatAggregationError(
                    "main-value-exceeds-total",
                    "item.reforgedMainValue",
                    "The reforged main-stat value cannot exceed its complete stat total.",
                )
        if self.reforged_evidence is None:
            raise StatAggregationError(
                "missing-projection-evidence",
                "item.reforgedEvidence",
                "Complete reforged totals require an evidence state.",
            )
        object.__setattr__(
            self, "reforged_evidence", _evidence(self.reforged_evidence, "item.reforgedEvidence")
        )

    @classmethod
    def from_gear_item(cls, item: GearItem) -> "ProjectedGearItem":
        """Adapt a current-only domain item without fabricating a reforge."""

        if not isinstance(item, GearItem):
            raise StatAggregationError("invalid-gear-item", "item", "Expected a GearItem.")
        totals: dict[ItemStatType, Number] = {stat: 0 for stat in ItemStatType}
        totals[item.main_stat] += item.main_stat_value
        for stat, value in item.substats:
            totals[stat] += value
        return cls(
            item_id=item.item_id,
            dense_id=item.dense_id,
            slot=item.slot,
            gear_set=item.gear_set,
            current_totals=totals,
            current_evidence=ItemProjectionEvidence.DOMAIN_CURRENT,
            main_stat=item.main_stat,
            current_main_value=item.main_stat_value,
        )

    @classmethod
    def from_fribbels_inventory_item(
        cls,
        item: object,
        *,
        dense_id: int | None = None,
    ) -> "ProjectedGearItem":
        """Adapt a persisted/imported item while retaining parser evidence."""

        from src.optimizer.data.fribbels_merge import FribbelsInventoryItem

        if not isinstance(item, FribbelsInventoryItem):
            raise StatAggregationError(
                "invalid-fribbels-item", "item", "Expected a FribbelsInventoryItem."
            )
        if dense_id is None:
            dense_id = item.gear_item.dense_id
        raw_main = item.source_metadata.get("main")
        raw_reforged_main = (
            raw_main.get("reforgedValue") if isinstance(raw_main, Mapping) else None
        )
        reforged_main = (
            raw_reforged_main
            if isinstance(raw_reforged_main, Real)
            and not isinstance(raw_reforged_main, bool)
            and math.isfinite(raw_reforged_main)
            and raw_reforged_main >= 0
            else item.gear_item.main_stat_value
        )
        return cls(
            item_id=item.stable_item_id,
            dense_id=dense_id,
            slot=item.gear_item.slot,
            gear_set=item.gear_item.gear_set,
            current_totals=item.projection.current_totals,
            current_evidence=_fribbels_evidence(item.projection.augmented_evidence),
            reforged_totals=item.projection.reforged_totals,
            reforged_evidence=_fribbels_evidence(item.projection.reforged_evidence),
            main_stat=item.gear_item.main_stat,
            current_main_value=item.gear_item.main_stat_value,
            reforged_main_value=reforged_main,
        )

    def totals_for(self, mode: ItemProjectionMode) -> StatTotals:
        if mode is ItemProjectionMode.CURRENT:
            return self.current_totals
        if mode is ItemProjectionMode.REFORGED and self.reforged_totals is not None:
            return self.reforged_totals
        raise StatAggregationError(
            "reforged-projection-unavailable",
            f"items[{self.slot.value}].reforgedTotals",
            f"Item {self.item_id!r} has no complete reforged projection.",
        )

    def evidence_for(self, mode: ItemProjectionMode) -> ItemProjectionEvidence:
        if mode is ItemProjectionMode.CURRENT:
            return self.current_evidence
        if mode is ItemProjectionMode.REFORGED and self.reforged_evidence is not None:
            return self.reforged_evidence
        raise StatAggregationError(
            "reforged-projection-unavailable",
            f"items[{self.slot.value}].reforgedEvidence",
            f"Item {self.item_id!r} has no reforged projection evidence.",
        )

    def main_value_for(self, mode: ItemProjectionMode) -> Number:
        """Return the selected main value so Fribbels gear score can exclude it."""

        if self.main_stat is None or self.current_main_value is None:
            raise StatAggregationError(
                "main-stat-evidence-required",
                f"items[{self.slot.value}].mainStat",
                "Gear-score calculation requires the item's main-stat evidence.",
            )
        if mode is ItemProjectionMode.CURRENT:
            return self.current_main_value
        if mode is ItemProjectionMode.REFORGED and self.reforged_totals is not None:
            return (
                self.current_main_value
                if self.reforged_main_value is None
                else self.reforged_main_value
            )
        raise StatAggregationError(
            "reforged-projection-unavailable",
            f"items[{self.slot.value}].reforgedMainValue",
            f"Item {self.item_id!r} has no complete reforged projection.",
        )


@dataclass(frozen=True, slots=True)
class ItemContributionDiagnostic:
    slot: GearSlot
    item_id: str
    dense_id: int | None
    gear_set: GearSet
    projection_evidence: ItemProjectionEvidence
    selected_totals: StatTotals
    main_stat: ItemStatType | None
    selected_main_value: Number | None
    pre_set_final_contributions: UnroundedFinalStats


@dataclass(frozen=True, slots=True)
class StatAggregationDiagnostics:
    base_stats: tuple[tuple[FinalStat, Number], ...]
    configured_naked_stats: UnroundedFinalStats
    final_stat_multipliers: FinalStatMultipliers
    item_totals: StatTotals
    artifact_flat_stats: tuple[tuple[FinalStat, Number], ...]
    modifier_totals: tuple[tuple[HeroModifierStatType, float], ...]
    set_insertion_stats: UnroundedFinalStats
    post_set_modifier_contributions: UnroundedFinalStats
    unrounded_final_stats: UnroundedFinalStats
    items: tuple[ItemContributionDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class StatAggregationResult:
    """All eight integer display stats before caps, sets, or metrics."""

    final_stats: FinalStats
    diagnostics: StatAggregationDiagnostics

    def value(self, stat: FinalStat) -> int:
        return dict(self.final_stats)[FinalStat(stat)]


def _f32(value: Number) -> float:
    """Round one operation boundary to the JVM/CUDA IEEE-754 binary32 format."""

    try:
        return struct.unpack("!f", struct.pack("!f", float(value)))[0]
    except OverflowError:
        raise StatAggregationError(
            "numeric-overflow",
            "calculation",
            "A value or intermediate result exceeds finite binary32 range.",
        ) from None


def _fadd(left: Number, right: Number) -> float:
    return _f32(_f32(left) + _f32(right))


def _fmul(left: Number, right: Number) -> float:
    return _f32(_f32(left) * _f32(right))


def _modifier_totals(modifiers: HeroModifiers) -> tuple[tuple[HeroModifierStatType, float], ...]:
    legacy_pairs = (
        (modifiers.imprint_bonuses, modifiers.imprint_contribution, "imprint"),
        (
            modifiers.exclusive_equipment_bonuses,
            modifiers.exclusive_equipment_contribution,
            "exclusiveEquipment",
        ),
        (modifiers.custom_bonuses, modifiers.custom_contributions, "custom"),
    )
    for legacy, typed, field in legacy_pairs:
        if legacy and not typed:
            raise StatAggregationError(
                "legacy-modifier-untyped",
                f"request.modifiers.{field}",
                "Legacy final-stat bonuses cannot distinguish flat values from rates; reselect or migrate this modifier.",
            )

    contributions: list[HeroModifierContribution] = []
    if modifiers.imprint_contribution is not None:
        contributions.append(modifiers.imprint_contribution)
    if modifiers.exclusive_equipment_contribution is not None:
        contributions.append(modifiers.exclusive_equipment_contribution)
    contributions.extend(modifiers.custom_contributions)

    totals = {stat: _f32(0) for stat in HeroModifierStatType}
    for contribution in contributions:
        totals[contribution.stat_type] = _fadd(
            totals[contribution.stat_type], contribution.value
        )
    return tuple((stat, totals[stat]) for stat in HeroModifierStatType)


_PROFILE_SOURCE_KEYS = {
    (50, 5): "lv50FiveStarFullyAwakened",
    (60, 6): "lv60SixStarFullyAwakened",
}
_FINAL_MULTIPLIER_FIELDS = {
    FinalStat.ATTACK: "bonusMaxAtkPercent",
    FinalStat.HEALTH: "bonusMaxHpPercent",
    FinalStat.DEFENSE: "bonusMaxDefPercent",
}


def _profile_final_stat_multipliers(
    selection: CharacterProfileSelection,
) -> FinalStatMultipliers:
    source_key = _PROFILE_SOURCE_KEYS.get((selection.profile.level, selection.profile.stars))
    statuses = selection.hero.raw_source.get("calculatedStatus")
    source_profile = statuses.get(source_key) if isinstance(statuses, Mapping) and source_key else None
    if not isinstance(source_profile, Mapping):
        raise StatAggregationError(
            "profile-source-missing",
            "profileSelection.profileId",
            "The selected base profile has no matching immutable source-stat record.",
        )

    result = {stat: _f32(1) for stat in FINAL_STAT_ORDER}
    for stat, field in _FINAL_MULTIPLIER_FIELDS.items():
        raw_percent = source_profile.get(field, 0)
        if (
            isinstance(raw_percent, bool)
            or not isinstance(raw_percent, Real)
            or not math.isfinite(raw_percent)
            or raw_percent < 0
        ):
            raise StatAggregationError(
                "invalid-final-stat-multiplier",
                f"profileSelection.source.{source_key}.{field}",
                "Expected a finite nonnegative percentage.",
            )
        ratio = _f32(_f32(raw_percent) / _f32(100))
        result[stat] = _fadd(1, ratio)
    return tuple((stat, result[stat]) for stat in FINAL_STAT_ORDER)


def _validate_artifact_selection(
    modifiers: HeroModifiers,
    artifact: ArtifactSelection,
) -> None:
    if not isinstance(artifact, ArtifactSelection):
        raise StatAggregationError(
            "invalid-artifact-selection", "artifactSelection", "Expected an ArtifactSelection."
        )
    selected = (
        artifact.artifact_id,
        artifact.level,
        artifact.limit_breaks,
        artifact.overrides.attack,
        artifact.overrides.health,
        artifact.overrides.defense,
    )
    configured = (
        modifiers.artifact_id,
        modifiers.artifact_level,
        modifiers.artifact_limit_breaks,
        modifiers.artifact_attack_override,
        modifiers.artifact_health_override,
        modifiers.artifact_defense_override,
    )
    if selected != configured:
        raise StatAggregationError(
            "artifact-selection-mismatch",
            "artifactSelection",
            "Resolved artifact identity, level, limit breaks, or overrides do not match the request.",
        )


def _validate_selection(
    request: OptimizationRequest,
    profile: CharacterProfileSelection,
    items: Iterable[ProjectedGearItem],
) -> tuple[ProjectedGearItem, ...]:
    if not isinstance(request, OptimizationRequest):
        raise StatAggregationError("invalid-request", "request", "Expected an OptimizationRequest.")
    if request.item_projection_mode is None:
        raise StatAggregationError(
            "projection-mode-required",
            "request.itemProjectionMode",
            "Select current or reforged item totals before aggregation.",
        )
    if not isinstance(profile, CharacterProfileSelection):
        raise StatAggregationError(
            "invalid-profile-selection", "profileSelection", "Expected a CharacterProfileSelection."
        )
    if request.hero_id != profile.hero_id:
        raise StatAggregationError(
            "hero-selection-mismatch",
            "profileSelection.heroId",
            "The resolved hero does not match request.heroId.",
        )
    if request.base_profile_id != profile.profile_id:
        raise StatAggregationError(
            "profile-selection-mismatch",
            "profileSelection.profileId",
            "The resolved base profile does not match request.baseProfileId.",
        )
    if isinstance(items, (str, bytes)):
        raise StatAggregationError("invalid-items", "items", "Expected six projected gear items.")
    try:
        supplied = tuple(items)
    except TypeError:
        raise StatAggregationError("invalid-items", "items", "Expected six projected gear items.") from None
    if len(supplied) != len(GEAR_SLOT_ORDER):
        raise StatAggregationError(
            "six-items-required", "items", "Exactly six owned gear items are required."
        )
    if not all(isinstance(item, ProjectedGearItem) for item in supplied):
        raise StatAggregationError(
            "invalid-items", "items", "Every entry must be a ProjectedGearItem."
        )
    by_slot: dict[GearSlot, ProjectedGearItem] = {}
    item_ids: set[str] = set()
    dense_ids: set[int] = set()
    for index, item in enumerate(supplied):
        if item.slot in by_slot:
            raise StatAggregationError(
                "duplicate-slot",
                f"items[{index}].slot",
                f"Slot {item.slot.value} appears more than once.",
            )
        if item.item_id in item_ids:
            raise StatAggregationError(
                "duplicate-item-id",
                f"items[{index}].itemId",
                f"Stable item ID {item.item_id!r} appears more than once.",
            )
        if item.dense_id is not None and item.dense_id in dense_ids:
            raise StatAggregationError(
                "duplicate-dense-id",
                f"items[{index}].denseId",
                f"Dense item ID {item.dense_id} appears more than once.",
            )
        by_slot[item.slot] = item
        item_ids.add(item.item_id)
        if item.dense_id is not None:
            dense_ids.add(item.dense_id)
    missing = [slot.value for slot in GEAR_SLOT_ORDER if slot not in by_slot]
    if missing:
        raise StatAggregationError(
            "missing-slot", "items", "One item is required for each slot; missing: " + ", ".join(missing) + "."
        )
    return tuple(by_slot[slot] for slot in GEAR_SLOT_ORDER)


def _item_final_contributions(
    totals: Mapping[ItemStatType, Number],
    base: Mapping[FinalStat, Number],
) -> dict[FinalStat, float]:
    result = {
        FinalStat.ATTACK: _fadd(
            totals[ItemStatType.FLAT_ATTACK],
            _fmul(_f32(totals[ItemStatType.ATTACK_PERCENT]) / _f32(100), base[FinalStat.ATTACK]),
        ),
        FinalStat.HEALTH: _fadd(
            totals[ItemStatType.FLAT_HEALTH],
            _fmul(_f32(totals[ItemStatType.HEALTH_PERCENT]) / _f32(100), base[FinalStat.HEALTH]),
        ),
        FinalStat.DEFENSE: _fadd(
            totals[ItemStatType.FLAT_DEFENSE],
            _fmul(_f32(totals[ItemStatType.DEFENSE_PERCENT]) / _f32(100), base[FinalStat.DEFENSE]),
        ),
        FinalStat.SPEED: _f32(totals[ItemStatType.SPEED]),
        FinalStat.CRITICAL_HIT_CHANCE: _f32(
            totals[ItemStatType.CRITICAL_HIT_CHANCE_PERCENT]
        ),
        FinalStat.CRITICAL_HIT_DAMAGE: _f32(
            totals[ItemStatType.CRITICAL_HIT_DAMAGE_PERCENT]
        ),
        FinalStat.EFFECTIVENESS: _f32(totals[ItemStatType.EFFECTIVENESS_PERCENT]),
        FinalStat.EFFECT_RESISTANCE: _f32(
            totals[ItemStatType.EFFECT_RESISTANCE_PERCENT]
        ),
    }
    return result


def calculate_item_final_contributions(
    selected_totals: StatTotals | Mapping[ItemStatType | str, Number],
    base_stats: Mapping[FinalStat | str, Number],
) -> UnroundedFinalStats:
    """Convert one selected item projection into the exact P03 contribution vector."""

    totals = dict(_totals(selected_totals, "item.selectedTotals"))
    if not isinstance(base_stats, Mapping):
        raise StatAggregationError(
            "invalid-base-stats",
            "profile.baseStats",
            "Expected a complete final-stat mapping.",
        )
    normalized_base: dict[FinalStat, Number] = {}
    for raw_stat, raw_value in base_stats.items():
        try:
            stat = raw_stat if isinstance(raw_stat, FinalStat) else FinalStat(raw_stat)
        except (TypeError, ValueError):
            raise StatAggregationError(
                "unknown-final-stat",
                "profile.baseStats",
                "Expected canonical FinalStat stable IDs.",
            ) from None
        if stat in normalized_base:
            raise StatAggregationError(
                "duplicate-final-stat",
                f"profile.baseStats.{stat.value}",
                "Each final stat must appear exactly once.",
            )
        normalized_base[stat] = _number(raw_value, f"profile.baseStats.{stat.value}")
    missing = [stat.value for stat in FINAL_STAT_ORDER if stat not in normalized_base]
    if missing:
        raise StatAggregationError(
            "partial-base-stats",
            "profile.baseStats",
            "Base stats must contain every final stat; missing: " + ", ".join(missing) + ".",
        )
    contributions = _item_final_contributions(totals, normalized_base)
    return tuple((stat, contributions[stat]) for stat in FINAL_STAT_ORDER)


def aggregate_pre_set_stats(
    request: OptimizationRequest,
    profile_selection: CharacterProfileSelection,
    artifact_selection: ArtifactSelection,
    items: Iterable[ProjectedGearItem],
) -> StatAggregationResult:
    """Aggregate all eight displayed stats without sets, caps, or metrics."""

    selected_items = _validate_selection(request, profile_selection, items)
    _validate_artifact_selection(request.modifiers, artifact_selection)
    mode = request.item_projection_mode
    assert mode is not None

    base = dict(profile_selection.profile.final_stats)
    modifier_pairs = _modifier_totals(request.modifiers)
    modifiers = dict(modifier_pairs)
    artifact = artifact_selection.flat_stats
    source_final_multipliers = dict(_profile_final_stat_multipliers(profile_selection))
    request_final_multiplier_types = {
        FinalStat.ATTACK: HeroModifierStatType.FINAL_ATTACK_PERCENT,
        FinalStat.HEALTH: HeroModifierStatType.FINAL_HEALTH_PERCENT,
        FinalStat.DEFENSE: HeroModifierStatType.FINAL_DEFENSE_PERCENT,
    }
    final_multipliers = dict(source_final_multipliers)
    for stat, modifier_type in request_final_multiplier_types.items():
        final_multipliers[stat] = _fadd(
            final_multipliers[stat], modifiers[modifier_type]
        )
    final_multiplier_pairs = tuple(
        (stat, final_multipliers[stat]) for stat in FINAL_STAT_ORDER
    )

    item_totals = {stat: _f32(0) for stat in ItemStatType}
    item_diagnostics: list[ItemContributionDiagnostic] = []
    item_contributions: list[dict[FinalStat, float]] = []
    for item in selected_items:
        selected_totals = item.totals_for(mode)
        totals_map = dict(selected_totals)
        contribution = _item_final_contributions(totals_map, base)
        item_contributions.append(contribution)
        for stat, value in selected_totals:
            item_totals[stat] = _fadd(item_totals[stat], value)
        item_diagnostics.append(
            ItemContributionDiagnostic(
                slot=item.slot,
                item_id=item.item_id,
                dense_id=item.dense_id,
                gear_set=item.gear_set,
                projection_evidence=item.evidence_for(mode),
                selected_totals=selected_totals,
                main_stat=item.main_stat,
                selected_main_value=(
                    None if item.main_stat is None else item.main_value_for(mode)
                ),
                pre_set_final_contributions=tuple(
                    (stat, contribution[stat]) for stat in FINAL_STAT_ORDER
                ),
            )
        )

    ratio_types = {
        FinalStat.ATTACK: HeroModifierStatType.ATTACK_PERCENT,
        FinalStat.HEALTH: HeroModifierStatType.HEALTH_PERCENT,
        FinalStat.DEFENSE: HeroModifierStatType.DEFENSE_PERCENT,
    }
    flat_types = {
        FinalStat.ATTACK: HeroModifierStatType.FLAT_ATTACK,
        FinalStat.HEALTH: HeroModifierStatType.FLAT_HEALTH,
        FinalStat.DEFENSE: HeroModifierStatType.FLAT_DEFENSE,
    }
    artifact_flats = {
        FinalStat.ATTACK: artifact.attack,
        FinalStat.HEALTH: artifact.health,
        FinalStat.DEFENSE: artifact.defense,
    }
    unrounded: dict[FinalStat, float] = {}
    configured_naked: dict[FinalStat, float] = {}
    set_insertion: dict[FinalStat, float] = {}
    post_set_modifiers = {stat: _f32(0) for stat in FINAL_STAT_ORDER}
    for stat in (FinalStat.ATTACK, FinalStat.HEALTH, FinalStat.DEFENSE):
        value = _fadd(base[stat], _fmul(base[stat], modifiers[ratio_types[stat]]))
        value = _fadd(value, modifiers[flat_types[stat]])
        value = _fadd(value, artifact_flats[stat])
        configured_naked[stat] = _fmul(value, final_multipliers[stat])
        for contribution in item_contributions:
            value = _fadd(value, contribution[stat])
        set_insertion[stat] = value
        unrounded[stat] = _fmul(value, final_multipliers[stat])

    additive_modifier_types = {
        FinalStat.SPEED: HeroModifierStatType.SPEED,
        FinalStat.CRITICAL_HIT_CHANCE: HeroModifierStatType.CRITICAL_HIT_CHANCE_PERCENT,
        FinalStat.EFFECTIVENESS: HeroModifierStatType.EFFECTIVENESS_PERCENT,
        FinalStat.EFFECT_RESISTANCE: HeroModifierStatType.EFFECT_RESISTANCE_PERCENT,
    }
    for stat in (
        FinalStat.SPEED,
        FinalStat.CRITICAL_HIT_CHANCE,
        FinalStat.CRITICAL_HIT_DAMAGE,
        FinalStat.EFFECTIVENESS,
        FinalStat.EFFECT_RESISTANCE,
    ):
        base_value = _f32(base[stat])
        value = base_value
        for contribution in item_contributions:
            value = _fadd(value, contribution[stat])
        set_insertion[stat] = value
        modifier_type = additive_modifier_types.get(stat)
        if modifier_type is not None:
            modifier_value = modifiers[modifier_type]
            if modifier_type is not HeroModifierStatType.SPEED:
                modifier_value = _fmul(modifier_value, 100)
            post_set_modifiers[stat] = modifier_value
            configured_naked[stat] = _fadd(base_value, modifier_value)
            value = _fadd(value, modifier_value)
        else:
            configured_naked[stat] = base_value
        unrounded[stat] = value

    configured_naked_pairs = tuple(
        (stat, configured_naked[stat]) for stat in FINAL_STAT_ORDER
    )
    set_insertion_pairs = tuple((stat, set_insertion[stat]) for stat in FINAL_STAT_ORDER)
    post_set_modifier_pairs = tuple(
        (stat, post_set_modifiers[stat]) for stat in FINAL_STAT_ORDER
    )
    unrounded_pairs = tuple((stat, unrounded[stat]) for stat in FINAL_STAT_ORDER)
    final_stats = tuple((stat, math.trunc(unrounded[stat])) for stat in FINAL_STAT_ORDER)
    return StatAggregationResult(
        final_stats=final_stats,
        diagnostics=StatAggregationDiagnostics(
            base_stats=profile_selection.profile.final_stats,
            configured_naked_stats=configured_naked_pairs,
            final_stat_multipliers=final_multiplier_pairs,
            item_totals=tuple((stat, item_totals[stat]) for stat in ItemStatType),
            artifact_flat_stats=tuple(
                (stat, artifact_flats[stat])
                for stat in (FinalStat.ATTACK, FinalStat.HEALTH, FinalStat.DEFENSE)
            ),
            modifier_totals=modifier_pairs,
            set_insertion_stats=set_insertion_pairs,
            post_set_modifier_contributions=post_set_modifier_pairs,
            unrounded_final_stats=unrounded_pairs,
            items=tuple(item_diagnostics),
        ),
    )


__all__ = [
    "FRIBBELS_GPU_KERNEL_GIT_BLOB_SHA1",
    "FRIBBELS_GPU_KERNEL_PATH",
    "FRIBBELS_STAT_CALCULATOR_GIT_BLOB_SHA1",
    "FRIBBELS_STAT_CALCULATOR_PATH",
    "FRIBBELS_STAT_CALCULATOR_REVISION",
    "ItemContributionDiagnostic",
    "FinalStatMultipliers",
    "ItemProjectionEvidence",
    "ProjectedGearItem",
    "StatAggregationDiagnostics",
    "StatAggregationError",
    "StatAggregationResult",
    "aggregate_pre_set_stats",
    "calculate_item_final_contributions",
]
