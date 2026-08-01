"""Pure completed-set counting and Fribbels-compatible primary-stat bonuses."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from src.optimizer.data.artifact_repository import ArtifactSelection
from src.optimizer.data.character_profiles import CharacterProfileSelection
from src.optimizer.domain import (
    FINAL_STAT_ORDER,
    SET_CATALOG,
    FinalStat,
    GearSet,
    OptimizationRequest,
)
from src.optimizer.engine.stat_aggregation import (
    FinalStats,
    ProjectedGearItem,
    StatAggregationDiagnostics,
    UnroundedFinalStats,
    _f32,
    _fadd,
    _fmul,
    aggregate_pre_set_stats,
)


FRIBBELS_SET_SOURCE_REVISION = "f49b0676c27d893ae4aa1b69920e4c98f37eb3fb"
FRIBBELS_SET_ENUM_PATH = "backend/src/main/java/com/fribbels/enums/Set.java"
FRIBBELS_SET_ENUM_GIT_BLOB_SHA1 = "9b90048232956d96a0dc3a5da7ac90364f477c2d"
FRIBBELS_SET_CALCULATOR_PATH = (
    "backend/src/main/java/com/fribbels/core/StatCalculator.java"
)
FRIBBELS_SET_CALCULATOR_GIT_BLOB_SHA1 = "dfd9b1e363905a0aef3a2fca2e3369acde8d020e"
FRIBBELS_SET_GPU_KERNEL_PATH = (
    "backend/src/main/java/com/fribbels/gpu/GpuOptimizerKernel.java"
)
FRIBBELS_SET_GPU_KERNEL_GIT_BLOB_SHA1 = "80d34477fd0548be8f63f4086884756febac5425"

NumericSetContributions = tuple[tuple[FinalStat, float], ...]


@dataclass(frozen=True, slots=True)
class SetActivation:
    """One completed set effect with raw and effective multiplicity."""

    gear_set: GearSet
    source_index: int
    piece_count: int
    pieces_required: int
    completed_groups: int
    activation_count: int
    numeric_contributions: NumericSetContributions

    @property
    def changes_primary_stats(self) -> bool:
        return bool(self.numeric_contributions)


@dataclass(frozen=True, slots=True)
class SetEvaluationDiagnostics:
    piece_counts: tuple[tuple[GearSet, int], ...]
    activations: tuple[SetActivation, ...]
    numeric_application_order: tuple[GearSet, ...]
    numeric_set_contributions: UnroundedFinalStats
    pre_set_final_stats: FinalStats
    pre_set_diagnostics: StatAggregationDiagnostics
    unrounded_final_stats: UnroundedFinalStats

    def activation_for(self, gear_set: GearSet) -> SetActivation | None:
        selected = GearSet(gear_set)
        return next(
            (activation for activation in self.activations if activation.gear_set is selected),
            None,
        )


@dataclass(frozen=True, slots=True)
class SetEvaluationResult:
    """Eight displayed stats after completed sets, before caps or metrics."""

    final_stats: FinalStats
    diagnostics: SetEvaluationDiagnostics

    def value(self, stat: FinalStat) -> int:
        return dict(self.final_stats)[FinalStat(stat)]


# This is expression order in the pinned CPU calculator, not display order.
_NUMERIC_APPLICATION_ORDER = (
    GearSet.ATTACK,
    GearSet.HEALTH,
    GearSet.WARFARE,
    GearSet.TORRENT,
    GearSet.DEFENSE,
    GearSet.CRITICAL,
    GearSet.DESTRUCTION,
    GearSet.HIT,
    GearSet.RESIST,
    GearSet.SPEED,
    GearSet.REVENGE,
    GearSet.REVERSAL,
    GearSet.WEAKENING,
)

_APPLICATION_BY_FINAL_STAT = {
    FinalStat.ATTACK: (GearSet.ATTACK,),
    FinalStat.HEALTH: (GearSet.HEALTH, GearSet.WARFARE, GearSet.TORRENT),
    FinalStat.DEFENSE: (GearSet.DEFENSE,),
    FinalStat.SPEED: (
        GearSet.SPEED,
        GearSet.REVENGE,
        GearSet.REVERSAL,
        GearSet.WEAKENING,
    ),
    FinalStat.CRITICAL_HIT_CHANCE: (GearSet.CRITICAL,),
    FinalStat.CRITICAL_HIT_DAMAGE: (GearSet.DESTRUCTION,),
    FinalStat.EFFECTIVENESS: (GearSet.HIT,),
    FinalStat.EFFECT_RESISTANCE: (GearSet.RESIST,),
}


def _scale(unit: float, activations: int) -> float:
    return _fmul(activations, unit)


def _numeric_contribution(
    gear_set: GearSet,
    activation_count: int,
    base: dict[FinalStat, int | float],
) -> NumericSetContributions:
    contribution: tuple[FinalStat, float] | None = None
    if gear_set is GearSet.ATTACK:
        attack_bonus = _fmul(0.45, base[FinalStat.ATTACK])
        contribution = (FinalStat.ATTACK, _scale(attack_bonus, activation_count))
    elif gear_set is GearSet.HEALTH:
        health_bonus = _fmul(0.20, base[FinalStat.HEALTH])
        contribution = (FinalStat.HEALTH, _scale(health_bonus, activation_count))
    elif gear_set is GearSet.WARFARE:
        health_bonus = _fmul(0.20, base[FinalStat.HEALTH])
        contribution = (FinalStat.HEALTH, _scale(health_bonus, activation_count))
    elif gear_set is GearSet.TORRENT:
        health_bonus = _fmul(0.20, base[FinalStat.HEALTH])
        scaled = _scale(health_bonus, activation_count)
        contribution = (FinalStat.HEALTH, _f32(scaled / _f32(-2)))
    elif gear_set is GearSet.DEFENSE:
        defense_bonus = _fmul(0.20, base[FinalStat.DEFENSE])
        contribution = (FinalStat.DEFENSE, _scale(defense_bonus, activation_count))
    elif gear_set is GearSet.SPEED:
        speed_bonus = _fmul(0.25, base[FinalStat.SPEED])
        contribution = (FinalStat.SPEED, _scale(speed_bonus, activation_count))
    elif gear_set is GearSet.REVENGE:
        revenge_bonus = _fmul(0.12, base[FinalStat.SPEED])
        contribution = (FinalStat.SPEED, _scale(revenge_bonus, activation_count))
    elif gear_set in {GearSet.REVERSAL, GearSet.WEAKENING}:
        reversal_bonus = _fmul(0.15, base[FinalStat.SPEED])
        contribution = (FinalStat.SPEED, _scale(reversal_bonus, activation_count))
    elif gear_set is GearSet.CRITICAL:
        contribution = (FinalStat.CRITICAL_HIT_CHANCE, _f32(activation_count * 12))
    elif gear_set is GearSet.DESTRUCTION:
        contribution = (FinalStat.CRITICAL_HIT_DAMAGE, _f32(activation_count * 60))
    elif gear_set is GearSet.HIT:
        contribution = (FinalStat.EFFECTIVENESS, _f32(activation_count * 20))
    elif gear_set is GearSet.RESIST:
        contribution = (FinalStat.EFFECT_RESISTANCE, _f32(activation_count * 20))
    return () if contribution is None else (contribution,)


def aggregate_with_set_bonuses(
    request: OptimizationRequest,
    profile_selection: CharacterProfileSelection,
    artifact_selection: ArtifactSelection,
    items: Iterable[ProjectedGearItem],
) -> SetEvaluationResult:
    """Aggregate one six-item build and apply only completed set effects."""

    pre_set = aggregate_pre_set_stats(
        request,
        profile_selection,
        artifact_selection,
        items,
    )
    counts = {gear_set: 0 for gear_set in GearSet}
    for item in pre_set.diagnostics.items:
        counts[item.gear_set] += 1

    base = dict(profile_selection.profile.final_stats)
    activations: list[SetActivation] = []
    contribution_by_set: dict[GearSet, NumericSetContributions] = {}
    for gear_set in GearSet:
        metadata = SET_CATALOG[gear_set]
        completed_groups = counts[gear_set] // metadata.pieces_required
        if completed_groups == 0:
            continue
        activation_count = completed_groups if metadata.stackable else 1
        numeric = _numeric_contribution(gear_set, activation_count, base)
        contribution_by_set[gear_set] = numeric
        activations.append(
            SetActivation(
                gear_set=gear_set,
                source_index=metadata.fribbels_index,
                piece_count=counts[gear_set],
                pieces_required=metadata.pieces_required,
                completed_groups=completed_groups,
                activation_count=activation_count,
                numeric_contributions=numeric,
            )
        )

    insertion = dict(pre_set.diagnostics.set_insertion_stats)
    post_set = dict(pre_set.diagnostics.post_set_modifier_contributions)
    final_multipliers = dict(pre_set.diagnostics.final_stat_multipliers)
    numeric_totals = {stat: _f32(0) for stat in FinalStat}
    unrounded: dict[FinalStat, float] = {}
    for stat in FINAL_STAT_ORDER:
        value = insertion[stat]
        for gear_set in _APPLICATION_BY_FINAL_STAT[stat]:
            numeric = dict(contribution_by_set.get(gear_set, ())).get(stat)
            if numeric is None:
                continue
            value = _fadd(value, numeric)
            numeric_totals[stat] = _fadd(numeric_totals[stat], numeric)
        if final_multipliers[stat] != 1:
            value = _fmul(value, final_multipliers[stat])
        if post_set[stat] != 0:
            value = _fadd(value, post_set[stat])
        unrounded[stat] = value

    unrounded_pairs = tuple((stat, unrounded[stat]) for stat in FINAL_STAT_ORDER)
    final_stats = tuple((stat, math.trunc(unrounded[stat])) for stat in FINAL_STAT_ORDER)
    active_numeric_order = tuple(
        gear_set for gear_set in _NUMERIC_APPLICATION_ORDER if gear_set in contribution_by_set
    )
    return SetEvaluationResult(
        final_stats=final_stats,
        diagnostics=SetEvaluationDiagnostics(
            piece_counts=tuple((gear_set, counts[gear_set]) for gear_set in GearSet),
            activations=tuple(activations),
            numeric_application_order=active_numeric_order,
            numeric_set_contributions=tuple(
                (stat, numeric_totals[stat]) for stat in FINAL_STAT_ORDER
            ),
            pre_set_final_stats=pre_set.final_stats,
            pre_set_diagnostics=pre_set.diagnostics,
            unrounded_final_stats=unrounded_pairs,
        ),
    )


__all__ = [
    "FRIBBELS_SET_CALCULATOR_GIT_BLOB_SHA1",
    "FRIBBELS_SET_CALCULATOR_PATH",
    "FRIBBELS_SET_ENUM_GIT_BLOB_SHA1",
    "FRIBBELS_SET_ENUM_PATH",
    "FRIBBELS_SET_GPU_KERNEL_GIT_BLOB_SHA1",
    "FRIBBELS_SET_GPU_KERNEL_PATH",
    "FRIBBELS_SET_SOURCE_REVISION",
    "NumericSetContributions",
    "SetActivation",
    "SetEvaluationDiagnostics",
    "SetEvaluationResult",
    "aggregate_with_set_bonuses",
]
