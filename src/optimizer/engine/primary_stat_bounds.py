"""Pure primary-stat cap application and inclusive bound evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from src.optimizer.domain import FINAL_STAT_ORDER, FinalStat, OptimizationRequest, StatRange
from src.optimizer.engine.set_evaluation import SetEvaluationResult
from src.optimizer.engine.stat_aggregation import FinalStats


FRIBBELS_PRIMARY_CAP_SOURCE_REVISION = "f49b0676c27d893ae4aa1b69920e4c98f37eb3fb"
FRIBBELS_PRIMARY_CAP_CALCULATOR_PATH = (
    "backend/src/main/java/com/fribbels/core/StatCalculator.java"
)
FRIBBELS_PRIMARY_CAP_CALCULATOR_GIT_BLOB_SHA1 = (
    "dfd9b1e363905a0aef3a2fca2e3369acde8d020e"
)
FRIBBELS_PRIMARY_CAP_GPU_KERNEL_PATH = (
    "backend/src/main/java/com/fribbels/gpu/GpuOptimizerKernel.java"
)
FRIBBELS_PRIMARY_CAP_GPU_KERNEL_GIT_BLOB_SHA1 = (
    "80d34477fd0548be8f63f4086884756febac5425"
)


@dataclass(frozen=True, slots=True)
class PrimaryStatRule:
    """One canonical primary stat and its optional gameplay upper cap."""

    stat: FinalStat
    upper_cap: int | None = None

    def __post_init__(self) -> None:
        try:
            stat = FinalStat(self.stat)
        except (TypeError, ValueError):
            raise ValueError("PrimaryStatRule.stat must be a canonical FinalStat.") from None
        if self.upper_cap is not None and (
            isinstance(self.upper_cap, bool)
            or not isinstance(self.upper_cap, int)
            or self.upper_cap <= 0
        ):
            raise ValueError("PrimaryStatRule.upper_cap must be a positive integer or None.")
        object.__setattr__(self, "stat", stat)

    def apply(self, raw_value: int) -> int:
        return raw_value if self.upper_cap is None else min(raw_value, self.upper_cap)


_UPPER_CAPS = {
    FinalStat.CRITICAL_HIT_CHANCE: 100,
    FinalStat.CRITICAL_HIT_DAMAGE: 350,
}

PRIMARY_STAT_RULES = tuple(
    PrimaryStatRule(stat=stat, upper_cap=_UPPER_CAPS.get(stat))
    for stat in FINAL_STAT_ORDER
)


class PrimaryStatBoundStatus(StrEnum):
    """Outcome of applying one optional range to one effective stat."""

    UNRESTRICTED = "unrestricted"
    PASSED = "passed"
    BELOW_MINIMUM = "below-minimum"
    ABOVE_MAXIMUM = "above-maximum"
    MINIMUM_ABOVE_CAP = "minimum-above-cap"


class PrimaryStatBoundSide(StrEnum):
    MINIMUM = "minimum"
    MAXIMUM = "maximum"


@dataclass(frozen=True, slots=True)
class PrimaryStatBoundEvaluation:
    """Raw, effective, cap, and requested-range evidence for one stat."""

    stat: FinalStat
    raw_value: int
    effective_value: int
    upper_cap: int | None
    requested_range: StatRange
    range_supplied: bool
    status: PrimaryStatBoundStatus

    @property
    def cap_applied(self) -> bool:
        return self.upper_cap is not None and self.raw_value > self.upper_cap

    @property
    def constrained(self) -> bool:
        return (
            self.requested_range.minimum is not None
            or self.requested_range.maximum is not None
        )

    @property
    def passes(self) -> bool:
        return self.status in {
            PrimaryStatBoundStatus.UNRESTRICTED,
            PrimaryStatBoundStatus.PASSED,
        }

    @property
    def failure_side(self) -> PrimaryStatBoundSide | None:
        if self.status in {
            PrimaryStatBoundStatus.BELOW_MINIMUM,
            PrimaryStatBoundStatus.MINIMUM_ABOVE_CAP,
        }:
            return PrimaryStatBoundSide.MINIMUM
        if self.status is PrimaryStatBoundStatus.ABOVE_MAXIMUM:
            return PrimaryStatBoundSide.MAXIMUM
        return None


@dataclass(frozen=True, slots=True)
class PrimaryStatBoundsResult:
    """Effective stats and deterministic inclusive-bound outcomes."""

    raw_final_stats: FinalStats
    effective_final_stats: FinalStats
    evaluations: tuple[PrimaryStatBoundEvaluation, ...]
    set_evaluation: SetEvaluationResult

    @property
    def failures(self) -> tuple[PrimaryStatBoundEvaluation, ...]:
        return tuple(evaluation for evaluation in self.evaluations if not evaluation.passes)

    @property
    def passes(self) -> bool:
        return not self.failures

    def raw_value(self, stat: FinalStat) -> int:
        return dict(self.raw_final_stats)[FinalStat(stat)]

    def effective_value(self, stat: FinalStat) -> int:
        return dict(self.effective_final_stats)[FinalStat(stat)]

    def evaluation_for(self, stat: FinalStat) -> PrimaryStatBoundEvaluation:
        selected = FinalStat(stat)
        return next(
            evaluation for evaluation in self.evaluations if evaluation.stat is selected
        )


_UNRESTRICTED_RANGE = StatRange()


def _status(
    rule: PrimaryStatRule,
    requested_range: StatRange,
    effective_value: int,
) -> PrimaryStatBoundStatus:
    minimum = requested_range.minimum
    maximum = requested_range.maximum
    if minimum is None and maximum is None:
        return PrimaryStatBoundStatus.UNRESTRICTED
    if rule.upper_cap is not None and minimum is not None and minimum > rule.upper_cap:
        return PrimaryStatBoundStatus.MINIMUM_ABOVE_CAP
    if minimum is not None and effective_value < minimum:
        return PrimaryStatBoundStatus.BELOW_MINIMUM
    if maximum is not None and effective_value > maximum:
        return PrimaryStatBoundStatus.ABOVE_MAXIMUM
    return PrimaryStatBoundStatus.PASSED


def evaluate_primary_stat_bounds(
    request: OptimizationRequest,
    set_evaluation: SetEvaluationResult,
) -> PrimaryStatBoundsResult:
    """Apply gameplay caps and the request's inclusive primary-stat ranges."""

    if not isinstance(request, OptimizationRequest):
        raise TypeError("request must be an OptimizationRequest.")
    if not isinstance(set_evaluation, SetEvaluationResult):
        raise TypeError("set_evaluation must be a SetEvaluationResult.")

    raw_by_stat = dict(set_evaluation.final_stats)
    ranges = dict(request.stat_ranges)
    effective_by_stat: dict[FinalStat, int] = {}
    evaluations: list[PrimaryStatBoundEvaluation] = []
    for rule in PRIMARY_STAT_RULES:
        raw_value = raw_by_stat[rule.stat]
        effective_value = rule.apply(raw_value)
        effective_by_stat[rule.stat] = effective_value
        range_supplied = rule.stat in ranges
        requested_range = ranges.get(rule.stat, _UNRESTRICTED_RANGE)
        evaluations.append(
            PrimaryStatBoundEvaluation(
                stat=rule.stat,
                raw_value=raw_value,
                effective_value=effective_value,
                upper_cap=rule.upper_cap,
                requested_range=requested_range,
                range_supplied=range_supplied,
                status=_status(rule, requested_range, effective_value),
            )
        )

    return PrimaryStatBoundsResult(
        raw_final_stats=set_evaluation.final_stats,
        effective_final_stats=tuple(
            (stat, effective_by_stat[stat]) for stat in FINAL_STAT_ORDER
        ),
        evaluations=tuple(evaluations),
        set_evaluation=set_evaluation,
    )


__all__ = [
    "FRIBBELS_PRIMARY_CAP_CALCULATOR_GIT_BLOB_SHA1",
    "FRIBBELS_PRIMARY_CAP_CALCULATOR_PATH",
    "FRIBBELS_PRIMARY_CAP_GPU_KERNEL_GIT_BLOB_SHA1",
    "FRIBBELS_PRIMARY_CAP_GPU_KERNEL_PATH",
    "FRIBBELS_PRIMARY_CAP_SOURCE_REVISION",
    "PRIMARY_STAT_RULES",
    "PrimaryStatBoundEvaluation",
    "PrimaryStatBoundSide",
    "PrimaryStatBoundStatus",
    "PrimaryStatBoundsResult",
    "PrimaryStatRule",
    "evaluate_primary_stat_bounds",
]
