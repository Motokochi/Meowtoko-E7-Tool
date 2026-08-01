"""Fribbels-compatible per-piece priority scoring."""

from __future__ import annotations

import math
from dataclasses import dataclass
from numbers import Real
from typing import Iterable

from src.optimizer.domain import (
    BuildMetrics,
    FINAL_STAT_ORDER,
    FinalStat,
    GearSlot,
    OptimizationRequest,
)
from src.optimizer.engine.derived_metrics import DerivedMetricsResult
from src.optimizer.engine.stat_aggregation import _f32, _fadd, _fmul


FRIBBELS_PRIORITY_NORMALIZATION_SOURCE_REVISION = (
    "b291cbbc415f11abede146859edc7b67d26e9c4b"
)
FRIBBELS_PRIORITY_NORMALIZATION_SOURCE_PATH = "app/js/lib/priorityFilter.js"
FRIBBELS_PRIORITY_NORMALIZATION_SOURCE_GIT_BLOB_SHA1 = (
    "f1aacc90e5e45c6724c8d4521a85de39976f4be3"
)


@dataclass(frozen=True, slots=True)
class PriorityNormalizationRule:
    """One max level-85 substat-roll unit in per-item contribution space."""

    stat: FinalStat
    base_ratio: float | None = None
    fixed_divisor: float | None = None

    def divisor(
        self,
        base_value: int | float,
        final_multiplier: int | float = 1,
    ) -> float:
        if self.base_ratio is not None:
            return _fmul(_fmul(base_value, self.base_ratio), final_multiplier)
        assert self.fixed_divisor is not None
        return _f32(self.fixed_divisor)


PRIORITY_NORMALIZATION_RULES = (
    PriorityNormalizationRule(FinalStat.ATTACK, base_ratio=0.08),
    PriorityNormalizationRule(FinalStat.HEALTH, base_ratio=0.08),
    PriorityNormalizationRule(FinalStat.DEFENSE, base_ratio=0.08),
    PriorityNormalizationRule(FinalStat.SPEED, fixed_divisor=4),
    PriorityNormalizationRule(FinalStat.CRITICAL_HIT_CHANCE, fixed_divisor=5),
    PriorityNormalizationRule(FinalStat.CRITICAL_HIT_DAMAGE, fixed_divisor=7),
    PriorityNormalizationRule(FinalStat.EFFECTIVENESS, fixed_divisor=8),
    PriorityNormalizationRule(FinalStat.EFFECT_RESISTANCE, fixed_divisor=8),
)


@dataclass(frozen=True, slots=True)
class PriorityStatDiagnostic:
    stat: FinalStat
    baseline_value: int | float
    effective_final_value: int | float
    raw_contribution: float
    normalization_divisor: float
    normalized_contribution: float
    priority_supplied: bool
    weight: int
    weighted_term: float


@dataclass(frozen=True, slots=True)
class PriorityPieceDiagnostic:
    """The unrounded and independently rounded score for one gear piece."""

    slot: GearSlot
    item_id: str
    dense_id: int | None
    stats: tuple[PriorityStatDiagnostic, ...]
    unrounded_score: float
    rounded_score: int

    def for_stat(self, stat: FinalStat) -> PriorityStatDiagnostic:
        selected = FinalStat(stat)
        return next(item for item in self.stats if item.stat is selected)


@dataclass(frozen=True, slots=True)
class PriorityScoreDiagnostics:
    """Per-stat totals plus the six independently rounded piece scores."""

    stats: tuple[PriorityStatDiagnostic, ...]
    pieces: tuple[PriorityPieceDiagnostic, ...]

    def for_stat(self, stat: FinalStat) -> PriorityStatDiagnostic:
        selected = FinalStat(stat)
        return next(item for item in self.stats if item.stat is selected)


@dataclass(frozen=True, slots=True)
class FinalBuildPriorityResult:
    """A scored build plus canonical Fribbels-style priority evidence."""

    derived_result: DerivedMetricsResult
    diagnostics: PriorityScoreDiagnostics

    @property
    def metrics(self) -> BuildMetrics:
        return self.derived_result.metrics

    @property
    def priority_score(self) -> int | float:
        return self.metrics.priority_score


def _fdiv(left: int | float, right: int | float) -> float:
    denominator = _f32(right)
    if denominator == 0:
        raise ValueError("A priority normalization divisor cannot be zero.")
    return _f32(_f32(left) / denominator)


def _fribbels_round(value: int | float) -> int:
    """Match JavaScript Math.round for the integer score stored by Fribbels."""

    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError("A priority score must be finite.")
    return math.floor(numeric + 0.5)


def _numeric_vector(
    values: Iterable[int | float],
    path: str,
) -> tuple[float, ...]:
    supplied = tuple(values)
    if len(supplied) != len(FINAL_STAT_ORDER):
        raise ValueError(f"{path} must contain exactly eight entries.")
    if any(
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(float(value))
        for value in supplied
    ):
        raise ValueError(f"{path} entries must be finite numbers.")
    return tuple(_f32(value) for value in supplied)


def calculate_item_priority_score(
    base_stats: Iterable[int | float],
    priorities: Iterable[int],
    item_contributions: Iterable[int | float],
) -> tuple[float, int]:
    """Return one item's Fribbels-style unrounded and rounded priority."""

    base = _numeric_vector(base_stats, "base_stats")
    contributions = _numeric_vector(item_contributions, "item_contributions")
    weights = tuple(priorities)
    if len(weights) != len(FINAL_STAT_ORDER) or any(
        isinstance(weight, bool)
        or not isinstance(weight, int)
        or weight < -1
        or weight > 3
        for weight in weights
    ):
        raise ValueError("priorities must contain eight integer weights from -1 to 3.")

    total = _f32(0)
    for index, rule in enumerate(PRIORITY_NORMALIZATION_RULES):
        divisor = rule.divisor(base[index])
        normalized = _fdiv(contributions[index], divisor)
        total = _fadd(total, _fmul(normalized, weights[index]))
    if total == 0:
        total = _f32(0)
    return total, _fribbels_round(total)


def _piece_diagnostic(
    *,
    slot: GearSlot,
    item_id: str,
    dense_id: int | None,
    base_stats: tuple[float, ...],
    priorities: tuple[int, ...],
    supplied_stats: frozenset[FinalStat],
    contributions: tuple[float, ...],
) -> PriorityPieceDiagnostic:
    stats: list[PriorityStatDiagnostic] = []
    total = _f32(0)
    for index, rule in enumerate(PRIORITY_NORMALIZATION_RULES):
        divisor = rule.divisor(base_stats[index])
        normalized = _fdiv(contributions[index], divisor)
        weighted = _fmul(normalized, priorities[index])
        total = _fadd(total, weighted)
        stats.append(
            PriorityStatDiagnostic(
                stat=rule.stat,
                baseline_value=0,
                effective_final_value=contributions[index],
                raw_contribution=contributions[index],
                normalization_divisor=divisor,
                normalized_contribution=normalized,
                priority_supplied=rule.stat in supplied_stats,
                weight=priorities[index],
                weighted_term=weighted,
            )
        )
    if total == 0:
        total = _f32(0)
    return PriorityPieceDiagnostic(
        slot=slot,
        item_id=item_id,
        dense_id=dense_id,
        stats=tuple(stats),
        unrounded_score=total,
        rounded_score=_fribbels_round(total),
    )


def score_final_build_priority(
    request: OptimizationRequest,
    derived_result: DerivedMetricsResult,
) -> FinalBuildPriorityResult:
    """Score six items independently, round each score, then sum the integers."""

    if not isinstance(request, OptimizationRequest):
        raise TypeError("request must be an OptimizationRequest.")
    if not isinstance(derived_result, DerivedMetricsResult):
        raise TypeError("derived_result must be a DerivedMetricsResult.")

    priorities_by_stat = dict(request.stat_priorities)
    priorities = tuple(priorities_by_stat.get(stat, 0) for stat in FINAL_STAT_ORDER)
    supplied_stats = frozenset(priorities_by_stat)
    pre_set = (
        derived_result.diagnostics.primary_bounds
        .set_evaluation.diagnostics.pre_set_diagnostics
    )
    base_stats_by_stat = dict(pre_set.base_stats)
    base_stats = tuple(_f32(base_stats_by_stat[stat]) for stat in FINAL_STAT_ORDER)

    pieces = tuple(
        _piece_diagnostic(
            slot=item.slot,
            item_id=item.item_id,
            dense_id=item.dense_id,
            base_stats=base_stats,
            priorities=priorities,
            supplied_stats=supplied_stats,
            contributions=tuple(
                _f32(dict(item.pre_set_final_contributions)[stat])
                for stat in FINAL_STAT_ORDER
            ),
        )
        for item in pre_set.items
    )
    priority_score = sum(piece.rounded_score for piece in pieces)

    aggregate_stats: list[PriorityStatDiagnostic] = []
    for index, rule in enumerate(PRIORITY_NORMALIZATION_RULES):
        raw = _f32(0)
        normalized = _f32(0)
        weighted = _f32(0)
        for piece in pieces:
            evidence = piece.stats[index]
            raw = _fadd(raw, evidence.raw_contribution)
            normalized = _fadd(normalized, evidence.normalized_contribution)
            weighted = _fadd(weighted, evidence.weighted_term)
        aggregate_stats.append(
            PriorityStatDiagnostic(
                stat=rule.stat,
                baseline_value=0,
                effective_final_value=raw,
                raw_contribution=raw,
                normalization_divisor=rule.divisor(base_stats[index]),
                normalized_contribution=normalized,
                priority_supplied=rule.stat in supplied_stats,
                weight=priorities[index],
                weighted_term=weighted,
            )
        )

    scored_metrics = BuildMetrics(
        final_stats=derived_result.metrics.final_stats,
        derived_metrics=derived_result.metrics.derived_metrics,
        priority_score=priority_score,
    )
    scored_derived_result = DerivedMetricsResult(
        metrics=scored_metrics,
        evaluations=derived_result.evaluations,
        diagnostics=derived_result.diagnostics,
    )
    return FinalBuildPriorityResult(
        derived_result=scored_derived_result,
        diagnostics=PriorityScoreDiagnostics(
            stats=tuple(aggregate_stats),
            pieces=pieces,
        ),
    )


assert tuple(rule.stat for rule in PRIORITY_NORMALIZATION_RULES) == FINAL_STAT_ORDER


__all__ = [
    "FRIBBELS_PRIORITY_NORMALIZATION_SOURCE_GIT_BLOB_SHA1",
    "FRIBBELS_PRIORITY_NORMALIZATION_SOURCE_PATH",
    "FRIBBELS_PRIORITY_NORMALIZATION_SOURCE_REVISION",
    "PRIORITY_NORMALIZATION_RULES",
    "FinalBuildPriorityResult",
    "PriorityNormalizationRule",
    "PriorityPieceDiagnostic",
    "PriorityScoreDiagnostics",
    "PriorityStatDiagnostic",
    "calculate_item_priority_score",
    "score_final_build_priority",
]
