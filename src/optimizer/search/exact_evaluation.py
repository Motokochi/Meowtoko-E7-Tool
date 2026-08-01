"""Exact-set CPU evaluation over prepared numeric slot arrays and batches."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from numbers import Real

from src.optimizer.data import (
    ArtifactSelection,
    CharacterProfileSelection,
    HeroSkillContextSelection,
    SkillContextSelection,
)
from src.optimizer.domain import (
    FINAL_STAT_ORDER,
    FRIBBELS_SET_ORDER,
    SET_CATALOG,
    FinalStat,
    GearSet,
    HeroModifierStatType,
    ItemProjectionMode,
    OptimizationRequest,
    SkillHitType,
)
from src.optimizer.engine.derived_metrics import (
    DERIVED_METRIC_CATALOG,
    DERIVED_METRIC_IDS,
    DamageSetDiagnostic,
    _base_metrics,
)
from src.optimizer.engine.priority_scoring import calculate_item_priority_score
from src.optimizer.engine.set_evaluation import (
    _APPLICATION_BY_FINAL_STAT,
    _numeric_contribution,
)
from src.optimizer.engine.stat_aggregation import (
    StatAggregationError,
    _f32,
    _fadd,
    _fmul,
    _modifier_totals,
    _profile_final_stat_multipliers,
    _validate_artifact_selection,
)
from src.optimizer.search.cartesian import CARTESIAN_SLOT_COUNT, CartesianBatch
from src.optimizer.search.set_patterns import (
    SET_PATTERN_VECTOR_LENGTH,
    CompiledSetPattern,
    compile_set_pattern,
)
from src.optimizer.search.slot_arrays import SearchReadySlotArrays


FINAL_STAT_COUNT = len(FINAL_STAT_ORDER)
DERIVED_METRIC_COUNT = len(DERIVED_METRIC_IDS)

SKILL_UNAVAILABLE = 0
SKILL_DAMAGE = 1
SKILL_SUPPORT = 2
SKILL_KIND_CODES = (SKILL_UNAVAILABLE, SKILL_DAMAGE, SKILL_SUPPORT)

HIT_NONE = -1
HIT_CRITICAL = 0
HIT_CRUSHING = 1
HIT_NORMAL = 2
HIT_MISS = 3
HIT_TYPE_CODES = (HIT_NONE, HIT_CRITICAL, HIT_CRUSHING, HIT_NORMAL, HIT_MISS)

_HIT_TYPE_TO_CODE = {
    SkillHitType.CRITICAL: HIT_CRITICAL,
    SkillHitType.CRUSHING: HIT_CRUSHING,
    SkillHitType.NORMAL: HIT_NORMAL,
    SkillHitType.MISS: HIT_MISS,
}
_NONCRITICAL_HIT_MULTIPLIERS = {
    HIT_CRUSHING: 1.3,
    HIT_NORMAL: 1.0,
    HIT_MISS: 0.75,
}
_PROJECTION_MODE_CODES = {
    ItemProjectionMode.CURRENT: 0,
    ItemProjectionMode.REFORGED: 1,
}
_PRIMARY_CAPS = (None, None, None, None, 100, 350, None, None)
_BASE_RELATIVE_INDICES = (0, 1, 2)


class ExactBuildEvaluationError(ValueError):
    """Actionable context, prepared-array, batch, or numeric-record failure."""

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code} at {path}: {message}")


def _error(code: str, path: str, message: str) -> ExactBuildEvaluationError:
    return ExactBuildEvaluationError(code, path, message)


def _integer(
    value: object,
    path: str,
    *,
    minimum: int | None = 0,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _error("invalid-integer", path, "must be an integer; boolean values are not accepted.")
    if (minimum is not None and value < minimum) or (
        maximum is not None and value > maximum
    ):
        bounds = []
        if minimum is not None:
            bounds.append(f"at least {minimum}")
        if maximum is not None:
            bounds.append(f"at most {maximum}")
        raise _error(
            "integer-out-of-range",
            path,
            f"must be {' and '.join(bounds)}; found {value}.",
        )
    return value


def _number(value: object, path: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise _error("invalid-number", path, "must be a finite number.")
    numeric = float(value)
    if not math.isfinite(numeric) or (nonnegative and numeric < 0):
        suffix = " nonnegative" if nonnegative else ""
        raise _error("invalid-number", path, f"must be a finite{suffix} number.")
    return numeric


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _error("invalid-stable-id", path, "must be a non-empty stable ID.")
    return value.strip()


def _sequence(value: object, path: str) -> tuple[object, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise _error("invalid-sequence", path, "must be a sequence.")
    try:
        return tuple(value)  # type: ignore[arg-type]
    except TypeError:
        raise _error("invalid-sequence", path, "must be a sequence.") from None


def _integer_vector(
    value: object,
    path: str,
    length: int,
    *,
    minimum: int | None = 0,
    maximum: int | None = None,
) -> tuple[int, ...]:
    supplied = _sequence(value, path)
    if len(supplied) != length:
        raise _error("vector-length", path, f"must contain exactly {length} entries.")
    return tuple(
        _integer(item, f"{path}[{index}]", minimum=minimum, maximum=maximum)
        for index, item in enumerate(supplied)
    )


def _number_vector(
    value: object,
    path: str,
    length: int,
    *,
    nonnegative: bool = False,
) -> tuple[float, ...]:
    supplied = _sequence(value, path)
    if len(supplied) != length:
        raise _error("vector-length", path, f"must contain exactly {length} entries.")
    return tuple(
        _number(item, f"{path}[{index}]", nonnegative=nonnegative)
        for index, item in enumerate(supplied)
    )


def _range_vector(value: object, path: str, length: int) -> tuple[float | None, ...]:
    supplied = _sequence(value, path)
    if len(supplied) != length:
        raise _error("vector-length", path, f"must contain exactly {length} entries.")
    result: list[float | None] = []
    for index, item in enumerate(supplied):
        result.append(None if item is None else _number(item, f"{path}[{index}]"))
    return tuple(result)


def _fsub(left: int | float, right: int | float) -> float:
    return _f32(_f32(left) - _f32(right))


def _fdiv(left: int | float, right: int | float) -> float:
    denominator = _f32(right)
    if denominator == 0:
        raise _error("division-by-zero", "calculation", "a binary32 denominator became zero.")
    return _f32(_f32(left) / denominator)


@dataclass(frozen=True, slots=True)
class CompiledSkillMetric:
    """Numeric scalar form of one resolved S1/S2/S3 metric context."""

    skill_index: int
    kind_code: int
    hit_type_code: int
    rate: float
    power: float
    self_hp_scaling: float
    self_attack_scaling: float
    self_defense_scaling: float
    self_speed_scaling: float
    extra_attack_scaling: float
    extra_defense_scaling: float
    increased_value: float
    critical_damage_increase: float
    target_defense: float
    target_count: int
    penetration: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "skill_index",
            _integer(self.skill_index, "CompiledSkillMetric.skill_index", maximum=2),
        )
        kind = _integer(self.kind_code, "CompiledSkillMetric.kind_code", maximum=2)
        hit = _integer(
            self.hit_type_code,
            "CompiledSkillMetric.hit_type_code",
            minimum=HIT_NONE,
            maximum=HIT_MISS,
        )
        if kind not in SKILL_KIND_CODES or hit not in HIT_TYPE_CODES:
            raise _error("invalid-skill-code", "CompiledSkillMetric", "contains an unknown numeric code.")
        if kind == SKILL_DAMAGE and hit == HIT_NONE:
            raise _error("invalid-skill-code", "CompiledSkillMetric.hit_type_code", "damage requires a hit type.")
        if kind != SKILL_DAMAGE and hit != HIT_NONE:
            raise _error("invalid-skill-code", "CompiledSkillMetric.hit_type_code", "support/unavailable skills require HIT_NONE.")
        object.__setattr__(self, "kind_code", kind)
        object.__setattr__(self, "hit_type_code", hit)
        for field in (
            "rate",
            "power",
            "self_hp_scaling",
            "self_attack_scaling",
            "self_defense_scaling",
            "self_speed_scaling",
            "extra_attack_scaling",
            "extra_defense_scaling",
            "increased_value",
            "critical_damage_increase",
            "target_defense",
            "penetration",
        ):
            object.__setattr__(
                self,
                field,
                _number(getattr(self, field), f"CompiledSkillMetric.{field}", nonnegative=True),
            )
        target_count = _integer(self.target_count, "CompiledSkillMetric.target_count")
        if kind == SKILL_DAMAGE and target_count == 0:
            raise _error("invalid-target-count", "CompiledSkillMetric.target_count", "damage requires a positive target count.")
        object.__setattr__(self, "target_count", target_count)


@dataclass(frozen=True, slots=True)
class ExactBuildEvaluationContext:
    """Shared immutable numeric hero/request state for every permutation."""

    request_id: str
    hero_id: str
    base_profile_id: str
    projection_mode_code: int
    base_stats: tuple[float, ...]
    configured_naked_stats: tuple[float, ...]
    final_stat_multipliers: tuple[float, ...]
    set_insertion_base_stats: tuple[float, ...]
    post_set_modifier_contributions: tuple[float, ...]
    artifact_flat_stats: tuple[float, ...]
    required_piece_counts: tuple[int, ...]
    activation_counts: tuple[int, ...]
    numeric_set_contributions: tuple[tuple[float, ...], ...]
    primary_minimums: tuple[float | None, ...]
    primary_maximums: tuple[float | None, ...]
    derived_minimums: tuple[float | None, ...]
    derived_maximums: tuple[float | None, ...]
    priorities: tuple[int, ...]
    metric_target_defense: float
    penetration_set_multiplier: float
    percent_damage_multiplier: float
    skills: tuple[CompiledSkillMetric, ...]

    def __post_init__(self) -> None:
        for field in ("request_id", "hero_id", "base_profile_id"):
            object.__setattr__(self, field, _text(getattr(self, field), f"ExactBuildEvaluationContext.{field}"))
        object.__setattr__(
            self,
            "projection_mode_code",
            _integer(self.projection_mode_code, "ExactBuildEvaluationContext.projection_mode_code", maximum=1),
        )
        for field, nonnegative in (
            ("base_stats", True),
            ("configured_naked_stats", True),
            ("final_stat_multipliers", True),
            ("set_insertion_base_stats", True),
            ("post_set_modifier_contributions", True),
            ("artifact_flat_stats", True),
        ):
            object.__setattr__(
                self,
                field,
                _number_vector(
                    getattr(self, field),
                    f"ExactBuildEvaluationContext.{field}",
                    FINAL_STAT_COUNT,
                    nonnegative=nonnegative,
                ),
            )
        if any(value <= 0 for value in self.final_stat_multipliers):
            raise _error(
                "invalid-final-multiplier",
                "ExactBuildEvaluationContext.final_stat_multipliers",
                "entries must be positive.",
            )
        if any(value <= 0 for value in self.base_stats[:3]):
            raise _error(
                "invalid-base-stat",
                "ExactBuildEvaluationContext.base_stats",
                "Attack, Health, and Defense base stats must be positive.",
            )
        if any(value != 1 for value in self.final_stat_multipliers[3:]):
            raise _error(
                "final-multiplier-layout",
                "ExactBuildEvaluationContext.final_stat_multipliers",
                "only Attack, Health, and Defense may have non-unit final multipliers.",
            )
        if any(
            self.post_set_modifier_contributions[index] != 0
            for index in (0, 1, 2, 5)
        ):
            raise _error(
                "post-set-modifier-layout",
                "ExactBuildEvaluationContext.post_set_modifier_contributions",
                "only Speed, Crit Chance, Effectiveness, and Effect Resistance may be nonzero.",
            )
        if any(self.artifact_flat_stats[index] != 0 for index in range(3, FINAL_STAT_COUNT)):
            raise _error(
                "artifact-stat-layout",
                "ExactBuildEvaluationContext.artifact_flat_stats",
                "only Attack, Health, and Defense entries may be nonzero.",
            )

        required = _integer_vector(
            self.required_piece_counts,
            "ExactBuildEvaluationContext.required_piece_counts",
            SET_PATTERN_VECTOR_LENGTH,
        )
        if sum(required) > CARTESIAN_SLOT_COUNT:
            raise _error(
                "required-piece-sum",
                "ExactBuildEvaluationContext.required_piece_counts",
                "entries must sum to at most six.",
            )
        activations = _integer_vector(
            self.activation_counts,
            "ExactBuildEvaluationContext.activation_counts",
            SET_PATTERN_VECTOR_LENGTH,
        )
        expected_activations = []
        for index, gear_set in enumerate(FRIBBELS_SET_ORDER):
            metadata = SET_CATALOG[gear_set]
            completed = required[index] // metadata.pieces_required
            expected_activations.append(completed if metadata.stackable else min(completed, 1))
        if activations != tuple(expected_activations):
            raise _error(
                "activation-count-mismatch",
                "ExactBuildEvaluationContext.activation_counts",
                "must match required pieces and canonical set metadata.",
            )
        rows = _sequence(
            self.numeric_set_contributions,
            "ExactBuildEvaluationContext.numeric_set_contributions",
        )
        if len(rows) != SET_PATTERN_VECTOR_LENGTH:
            raise _error(
                "vector-length",
                "ExactBuildEvaluationContext.numeric_set_contributions",
                "must contain exactly 24 set rows.",
            )
        set_rows = tuple(
            _number_vector(
                row,
                f"ExactBuildEvaluationContext.numeric_set_contributions[{index}]",
                FINAL_STAT_COUNT,
            )
            for index, row in enumerate(rows)
        )
        base_by_stat = dict(zip(FINAL_STAT_ORDER, self.base_stats, strict=True))
        expected_set_rows = tuple(
            tuple(
                _f32(dict(_numeric_contribution(gear_set, activations[index], base_by_stat)).get(stat, 0))
                for stat in FINAL_STAT_ORDER
            )
            for index, gear_set in enumerate(FRIBBELS_SET_ORDER)
        )
        if set_rows != expected_set_rows:
            raise _error(
                "numeric-set-contribution-mismatch",
                "ExactBuildEvaluationContext.numeric_set_contributions",
                "must match canonical set formulas, activation counts, and base stats.",
            )
        object.__setattr__(self, "required_piece_counts", required)
        object.__setattr__(self, "activation_counts", activations)
        object.__setattr__(self, "numeric_set_contributions", set_rows)

        for minimum_field, maximum_field, length in (
            ("primary_minimums", "primary_maximums", FINAL_STAT_COUNT),
            ("derived_minimums", "derived_maximums", DERIVED_METRIC_COUNT),
        ):
            minimums = _range_vector(
                getattr(self, minimum_field),
                f"ExactBuildEvaluationContext.{minimum_field}",
                length,
            )
            maximums = _range_vector(
                getattr(self, maximum_field),
                f"ExactBuildEvaluationContext.{maximum_field}",
                length,
            )
            if any(
                minimum is not None and maximum is not None and minimum > maximum
                for minimum, maximum in zip(minimums, maximums, strict=True)
            ):
                raise _error(
                    "invalid-range",
                    f"ExactBuildEvaluationContext.{minimum_field}",
                    "minimum entries must not exceed maximum entries.",
                )
            object.__setattr__(self, minimum_field, minimums)
            object.__setattr__(self, maximum_field, maximums)
        object.__setattr__(
            self,
            "priorities",
            _integer_vector(
                self.priorities,
                "ExactBuildEvaluationContext.priorities",
                FINAL_STAT_COUNT,
                minimum=-1,
                maximum=3,
            ),
        )
        object.__setattr__(
            self,
            "metric_target_defense",
            _number(
                self.metric_target_defense,
                "ExactBuildEvaluationContext.metric_target_defense",
                nonnegative=True,
            ),
        )
        object.__setattr__(
            self,
            "penetration_set_multiplier",
            _number(
                self.penetration_set_multiplier,
                "ExactBuildEvaluationContext.penetration_set_multiplier",
                nonnegative=True,
            ),
        )
        object.__setattr__(
            self,
            "percent_damage_multiplier",
            _number(
                self.percent_damage_multiplier,
                "ExactBuildEvaluationContext.percent_damage_multiplier",
                nonnegative=True,
            ),
        )
        numerator = _fadd(_fdiv(self.metric_target_defense, 300), 1)
        denominator = _fadd(_fmul(0.00283333, self.metric_target_defense), 1)
        penetration_bonus = _fdiv(numerator, denominator)
        expected_penetration = penetration_bonus if activations[13] else _f32(1)
        expected_percent_damage = _fadd(
            _fadd(
                _fadd(1, _fmul(activations[11], 0.3)),
                _fmul(activations[17], 0.1),
            ),
            _fmul(min(activations[23], 1), 0.2),
        )
        if self.penetration_set_multiplier != expected_penetration:
            raise _error(
                "penetration-multiplier-mismatch",
                "ExactBuildEvaluationContext.penetration_set_multiplier",
                "must match target defense and the exact Penetration activation.",
            )
        if self.percent_damage_multiplier != expected_percent_damage:
            raise _error(
                "damage-multiplier-mismatch",
                "ExactBuildEvaluationContext.percent_damage_multiplier",
                "must match exact Rage, Torrent, and Fervor activations.",
            )
        skills = _sequence(self.skills, "ExactBuildEvaluationContext.skills")
        if len(skills) != 3 or not all(isinstance(item, CompiledSkillMetric) for item in skills):
            raise _error(
                "invalid-skill-contexts",
                "ExactBuildEvaluationContext.skills",
                "must contain compiled S1, S2, and S3 metrics.",
            )
        if tuple(item.skill_index for item in skills) != (0, 1, 2):
            raise _error(
                "noncanonical-skill-order",
                "ExactBuildEvaluationContext.skills",
                "must use canonical S1, S2, S3 order.",
            )
        object.__setattr__(self, "skills", tuple(skills))


@dataclass(frozen=True, slots=True)
class ExactBuildRow:
    """Compact numeric exact result in stable stat and metric order."""

    flat_index: int
    dense_ids: tuple[int, ...]
    unrounded_final_stats: tuple[float, ...]
    raw_final_stats: tuple[int, ...]
    effective_final_stats: tuple[int, ...]
    derived_metrics: tuple[int, ...]
    priority_score: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "flat_index", _integer(self.flat_index, "ExactBuildRow.flat_index"))
        dense_ids = _integer_vector(self.dense_ids, "ExactBuildRow.dense_ids", CARTESIAN_SLOT_COUNT)
        if len(set(dense_ids)) != CARTESIAN_SLOT_COUNT:
            raise _error("duplicate-dense-id", "ExactBuildRow.dense_ids", "must contain six unique item IDs.")
        unrounded = _number_vector(
            self.unrounded_final_stats,
            "ExactBuildRow.unrounded_final_stats",
            FINAL_STAT_COUNT,
            nonnegative=True,
        )
        raw = _integer_vector(self.raw_final_stats, "ExactBuildRow.raw_final_stats", FINAL_STAT_COUNT)
        effective = _integer_vector(
            self.effective_final_stats,
            "ExactBuildRow.effective_final_stats",
            FINAL_STAT_COUNT,
        )
        if raw != tuple(math.trunc(value) for value in unrounded):
            raise _error("raw-stat-mismatch", "ExactBuildRow.raw_final_stats", "must truncate unrounded stats exactly.")
        expected_effective = tuple(
            raw_value if cap is None else min(raw_value, cap)
            for raw_value, cap in zip(raw, _PRIMARY_CAPS, strict=True)
        )
        if effective != expected_effective:
            raise _error("effective-stat-mismatch", "ExactBuildRow.effective_final_stats", "must apply canonical gameplay caps.")
        metrics = _integer_vector(
            self.derived_metrics,
            "ExactBuildRow.derived_metrics",
            DERIVED_METRIC_COUNT,
            minimum=None,
        )
        priority = _number(self.priority_score, "ExactBuildRow.priority_score")
        object.__setattr__(self, "dense_ids", dense_ids)
        object.__setattr__(self, "unrounded_final_stats", unrounded)
        object.__setattr__(self, "raw_final_stats", raw)
        object.__setattr__(self, "effective_final_stats", effective)
        object.__setattr__(self, "derived_metrics", metrics)
        object.__setattr__(self, "priority_score", priority)


@dataclass(frozen=True, slots=True)
class ExactBuildBatchResult:
    """Deterministic counts and ordered retained rows for one input batch."""

    start_index: int
    stop_index: int
    evaluated_count: int
    exact_set_count: int
    hard_bound_rejected_count: int
    emitted_count: int
    rows: tuple[ExactBuildRow, ...]

    def __post_init__(self) -> None:
        start = _integer(self.start_index, "ExactBuildBatchResult.start_index")
        stop = _integer(self.stop_index, "ExactBuildBatchResult.stop_index", minimum=1)
        if stop <= start:
            raise _error("invalid-batch-range", "ExactBuildBatchResult.stop_index", "must exceed start_index.")
        evaluated = _integer(self.evaluated_count, "ExactBuildBatchResult.evaluated_count", minimum=1)
        exact = _integer(self.exact_set_count, "ExactBuildBatchResult.exact_set_count")
        rejected = _integer(
            self.hard_bound_rejected_count,
            "ExactBuildBatchResult.hard_bound_rejected_count",
        )
        emitted = _integer(self.emitted_count, "ExactBuildBatchResult.emitted_count")
        rows = _sequence(self.rows, "ExactBuildBatchResult.rows")
        if not all(isinstance(item, ExactBuildRow) for item in rows):
            raise _error("invalid-exact-rows", "ExactBuildBatchResult.rows", "must contain ExactBuildRow values.")
        if evaluated != stop - start:
            raise _error("evaluated-count-mismatch", "ExactBuildBatchResult.evaluated_count", "must equal the half-open batch width.")
        if exact > evaluated or rejected + emitted != exact or emitted != len(rows):
            raise _error(
                "inconsistent-batch-counts",
                "ExactBuildBatchResult",
                "exact rows must partition into rejected and emitted counts.",
            )
        flat_indices = tuple(item.flat_index for item in rows)
        if flat_indices != tuple(sorted(set(flat_indices))) or any(
            index < start or index >= stop for index in flat_indices
        ):
            raise _error(
                "noncanonical-exact-row-order",
                "ExactBuildBatchResult.rows",
                "flat indices must be unique, ascending, and inside the batch.",
            )
        object.__setattr__(self, "start_index", start)
        object.__setattr__(self, "stop_index", stop)
        object.__setattr__(self, "evaluated_count", evaluated)
        object.__setattr__(self, "exact_set_count", exact)
        object.__setattr__(self, "hard_bound_rejected_count", rejected)
        object.__setattr__(self, "emitted_count", emitted)
        object.__setattr__(self, "rows", tuple(rows))


def _selected_hit_type(selection: SkillContextSelection) -> SkillHitType | None:
    if selection.context.hit_type is not None:
        return selection.context.hit_type
    return selection.record.hit_types[0] if selection.record.hit_types else None


def _compile_skill(selection: SkillContextSelection, skill_index: int) -> CompiledSkillMetric:
    record = selection.record
    option = selection.source_option
    option_name = "" if option is None else option.name.casefold()
    is_support = "heal" in option_name or "barrier" in option_name
    hit_type = None if is_support else _selected_hit_type(selection)
    target_count = selection.effective_target_count
    penetration = selection.effective_penetration

    if option is None:
        rate = 0 if record.rate is None else record.rate
        power = 0 if record.power is None else record.power
        self_hp = 0 if record.self_hp_scaling is None else record.self_hp_scaling
        self_attack = 0
        self_defense = 0 if record.self_defense_scaling is None else record.self_defense_scaling
        self_speed = 0 if record.self_speed_scaling is None else record.self_speed_scaling
        extra_attack = 0 if record.extra_self_attack_scaling is None else record.extra_self_attack_scaling
        extra_defense = 0 if record.extra_self_defense_scaling is None else record.extra_self_defense_scaling
        increased = 0 if record.increased_value is None else record.increased_value
        critical_increase = 0 if record.critical_damage_increase is None else record.critical_damage_increase
    else:
        rate = option.rate
        power = option.power
        self_hp = 0 if option.self_hp_scaling is None else option.self_hp_scaling
        self_attack = 0 if option.self_attack_scaling is None else option.self_attack_scaling
        self_defense = 0 if option.self_defense_scaling is None else option.self_defense_scaling
        self_speed = 0
        extra_attack = 0
        extra_defense = 0
        increased = 0
        critical_increase = 0

    unavailable = not is_support and (
        not selection.is_damaging or hit_type is None or target_count is None
    )
    kind = SKILL_UNAVAILABLE if unavailable else SKILL_SUPPORT if is_support else SKILL_DAMAGE
    return CompiledSkillMetric(
        skill_index=skill_index,
        kind_code=kind,
        hit_type_code=HIT_NONE if kind != SKILL_DAMAGE else _HIT_TYPE_TO_CODE[hit_type],
        rate=rate,
        power=power,
        self_hp_scaling=self_hp,
        self_attack_scaling=self_attack,
        self_defense_scaling=self_defense,
        self_speed_scaling=self_speed,
        extra_attack_scaling=extra_attack,
        extra_defense_scaling=extra_defense,
        increased_value=increased,
        critical_damage_increase=critical_increase,
        target_defense=selection.context.target_defense,
        target_count=0 if target_count is None else target_count,
        penetration=0 if penetration is None else penetration,
    )


def compile_exact_build_context(
    request: OptimizationRequest,
    profile_selection: CharacterProfileSelection,
    artifact_selection: ArtifactSelection,
    skill_selection: HeroSkillContextSelection,
    compiled_pattern: CompiledSetPattern,
) -> ExactBuildEvaluationContext:
    """Validate and precompute every request/hero value shared by a batch."""

    if not isinstance(request, OptimizationRequest):
        raise _error("invalid-request", "request", "must be an OptimizationRequest.")
    if request.item_projection_mode is None:
        raise _error("projection-mode-required", "request.itemProjectionMode", "must select current or reforged totals.")
    if not isinstance(profile_selection, CharacterProfileSelection):
        raise _error("invalid-profile-selection", "profile_selection", "must be a CharacterProfileSelection.")
    if request.hero_id != profile_selection.hero_id:
        raise _error("hero-selection-mismatch", "profile_selection.hero_id", "does not match request.hero_id.")
    if request.base_profile_id != profile_selection.profile_id:
        raise _error("profile-selection-mismatch", "profile_selection.profile_id", "does not match request.base_profile_id.")
    if not isinstance(skill_selection, HeroSkillContextSelection):
        raise _error("invalid-skill-selection", "skill_selection", "must be a HeroSkillContextSelection.")
    if skill_selection.hero.hero_id != request.hero_id:
        raise _error("skill-hero-mismatch", "skill_selection.hero_id", "does not match request.hero_id.")
    if skill_selection.contexts != request.skill_contexts:
        raise _error("skill-context-mismatch", "skill_selection.contexts", "does not match request.skill_contexts.")
    if not isinstance(compiled_pattern, CompiledSetPattern):
        raise _error("invalid-compiled-pattern", "compiled_pattern", "must be a CompiledSetPattern.")
    if compile_set_pattern(request.set_pattern) != compiled_pattern:
        raise _error("set-pattern-mismatch", "compiled_pattern", "does not match request.set_pattern.")
    try:
        _validate_artifact_selection(request.modifiers, artifact_selection)
        modifier_pairs = _modifier_totals(request.modifiers)
        source_multiplier_pairs = _profile_final_stat_multipliers(profile_selection)
    except StatAggregationError as error:
        raise _error(error.code, error.path, error.message) from error

    base = dict(profile_selection.profile.final_stats)
    base_vector = tuple(float(base[stat]) for stat in FINAL_STAT_ORDER)
    modifiers = dict(modifier_pairs)
    artifact = artifact_selection.flat_stats
    artifact_by_stat = {
        FinalStat.ATTACK: artifact.attack,
        FinalStat.HEALTH: artifact.health,
        FinalStat.DEFENSE: artifact.defense,
    }
    final_multipliers = dict(source_multiplier_pairs)
    final_modifier_types = {
        FinalStat.ATTACK: HeroModifierStatType.FINAL_ATTACK_PERCENT,
        FinalStat.HEALTH: HeroModifierStatType.FINAL_HEALTH_PERCENT,
        FinalStat.DEFENSE: HeroModifierStatType.FINAL_DEFENSE_PERCENT,
    }
    for stat, modifier_type in final_modifier_types.items():
        final_multipliers[stat] = _fadd(final_multipliers[stat], modifiers[modifier_type])

    insertion_base = {stat: _f32(base[stat]) for stat in FINAL_STAT_ORDER}
    configured_naked = {stat: _f32(base[stat]) for stat in FINAL_STAT_ORDER}
    post_set = {stat: _f32(0) for stat in FINAL_STAT_ORDER}
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
    for stat in (FinalStat.ATTACK, FinalStat.HEALTH, FinalStat.DEFENSE):
        value = _fadd(base[stat], _fmul(base[stat], modifiers[ratio_types[stat]]))
        value = _fadd(value, modifiers[flat_types[stat]])
        value = _fadd(value, artifact_by_stat[stat])
        insertion_base[stat] = value
        configured_naked[stat] = _fmul(value, final_multipliers[stat])
    additive_types = {
        FinalStat.SPEED: HeroModifierStatType.SPEED,
        FinalStat.CRITICAL_HIT_CHANCE: HeroModifierStatType.CRITICAL_HIT_CHANCE_PERCENT,
        FinalStat.EFFECTIVENESS: HeroModifierStatType.EFFECTIVENESS_PERCENT,
        FinalStat.EFFECT_RESISTANCE: HeroModifierStatType.EFFECT_RESISTANCE_PERCENT,
    }
    for stat, modifier_type in additive_types.items():
        modifier_value = modifiers[modifier_type]
        if modifier_type is not HeroModifierStatType.SPEED:
            modifier_value = _fmul(modifier_value, 100)
        post_set[stat] = modifier_value
        configured_naked[stat] = _fadd(base[stat], modifier_value)

    required = compiled_pattern.required_piece_counts
    activations: list[int] = []
    set_rows: list[tuple[float, ...]] = []
    for index, gear_set in enumerate(FRIBBELS_SET_ORDER):
        metadata = SET_CATALOG[gear_set]
        completed = required[index] // metadata.pieces_required
        activation_count = completed if metadata.stackable else min(completed, 1)
        activations.append(activation_count)
        contributions = dict(_numeric_contribution(gear_set, activation_count, base))
        set_rows.append(
            tuple(_f32(contributions.get(stat, 0)) for stat in FINAL_STAT_ORDER)
        )

    primary_ranges = dict(request.stat_ranges)
    derived_ranges = dict(request.derived_metric_ranges)
    unknown_metrics = tuple(sorted(set(derived_ranges) - set(DERIVED_METRIC_CATALOG)))
    if unknown_metrics:
        raise _error(
            "unknown-derived-metric",
            "request.derived_metric_ranges",
            "unsupported metric IDs: " + ", ".join(unknown_metrics) + ".",
        )
    priorities = dict(request.stat_priorities)

    target_defense = request.target_defense
    numerator = _fadd(_fdiv(target_defense, 300), 1)
    denominator = _fadd(_fmul(0.00283333, target_defense), 1)
    penetration_bonus = _fdiv(numerator, denominator)
    penetration_multiplier = penetration_bonus if activations[13] else _f32(1)
    percent_damage = _fadd(
        _fadd(_fadd(1, _fmul(activations[11], 0.3)), _fmul(activations[17], 0.1)),
        _fmul(min(activations[23], 1), 0.2),
    )

    return ExactBuildEvaluationContext(
        request_id=request.request_id,
        hero_id=request.hero_id,
        base_profile_id=request.base_profile_id,
        projection_mode_code=_PROJECTION_MODE_CODES[request.item_projection_mode],
        base_stats=base_vector,
        configured_naked_stats=tuple(configured_naked[stat] for stat in FINAL_STAT_ORDER),
        final_stat_multipliers=tuple(final_multipliers[stat] for stat in FINAL_STAT_ORDER),
        set_insertion_base_stats=tuple(insertion_base[stat] for stat in FINAL_STAT_ORDER),
        post_set_modifier_contributions=tuple(post_set[stat] for stat in FINAL_STAT_ORDER),
        artifact_flat_stats=tuple(
            float(artifact_by_stat.get(stat, 0)) for stat in FINAL_STAT_ORDER
        ),
        required_piece_counts=required,
        activation_counts=tuple(activations),
        numeric_set_contributions=tuple(set_rows),
        primary_minimums=tuple(
            None
            if stat not in primary_ranges or primary_ranges[stat].minimum is None
            else _f32(primary_ranges[stat].minimum)
            for stat in FINAL_STAT_ORDER
        ),
        primary_maximums=tuple(
            None
            if stat not in primary_ranges or primary_ranges[stat].maximum is None
            else _f32(primary_ranges[stat].maximum)
            for stat in FINAL_STAT_ORDER
        ),
        derived_minimums=tuple(
            None
            if metric not in derived_ranges or derived_ranges[metric].minimum is None
            else _f32(derived_ranges[metric].minimum)
            for metric in DERIVED_METRIC_IDS
        ),
        derived_maximums=tuple(
            None
            if metric not in derived_ranges or derived_ranges[metric].maximum is None
            else _f32(derived_ranges[metric].maximum)
            for metric in DERIVED_METRIC_IDS
        ),
        priorities=tuple(priorities.get(stat, 0) for stat in FINAL_STAT_ORDER),
        metric_target_defense=request.target_defense,
        penetration_set_multiplier=penetration_multiplier,
        percent_damage_multiplier=percent_damage,
        skills=tuple(
            _compile_skill(selection, index)
            for index, selection in enumerate(skill_selection.skills)
        ),
    )


def with_set_piece_counts(
    context: ExactBuildEvaluationContext,
    piece_counts: object,
) -> ExactBuildEvaluationContext:
    """Clone compact evaluation state with activations for any six set pieces."""

    if not isinstance(context, ExactBuildEvaluationContext):
        raise _error(
            "invalid-evaluation-context",
            "context",
            "must be an ExactBuildEvaluationContext.",
        )
    counts = _integer_vector(
        piece_counts,
        "piece_counts",
        SET_PATTERN_VECTOR_LENGTH,
    )
    if sum(counts) != CARTESIAN_SLOT_COUNT:
        raise _error(
            "piece-count-sum",
            "piece_counts",
            "entries must sum to six.",
        )
    base = dict(zip(FINAL_STAT_ORDER, context.base_stats, strict=True))
    activations: list[int] = []
    set_rows: list[tuple[float, ...]] = []
    for index, gear_set in enumerate(FRIBBELS_SET_ORDER):
        metadata = SET_CATALOG[gear_set]
        completed = counts[index] // metadata.pieces_required
        activation_count = completed if metadata.stackable else min(completed, 1)
        activations.append(activation_count)
        contributions = dict(_numeric_contribution(gear_set, activation_count, base))
        set_rows.append(
            tuple(_f32(contributions.get(stat, 0)) for stat in FINAL_STAT_ORDER)
        )

    numerator = _fadd(_fdiv(context.metric_target_defense, 300), 1)
    denominator = _fadd(
        _fmul(0.00283333, context.metric_target_defense),
        1,
    )
    penetration_bonus = _fdiv(numerator, denominator)
    penetration_multiplier = penetration_bonus if activations[13] else _f32(1)
    percent_damage = _fadd(
        _fadd(
            _fadd(1, _fmul(activations[11], 0.3)),
            _fmul(activations[17], 0.1),
        ),
        _fmul(min(activations[23], 1), 0.2),
    )
    return replace(
        context,
        required_piece_counts=counts,
        activation_counts=tuple(activations),
        numeric_set_contributions=tuple(set_rows),
        penetration_set_multiplier=penetration_multiplier,
        percent_damage_multiplier=percent_damage,
    )


def _apply_stats(
    context: ExactBuildEvaluationContext,
    item_contributions: tuple[tuple[float, ...], ...],
) -> tuple[tuple[float, ...], tuple[int, ...], tuple[int, ...]]:
    unrounded: list[float] = []
    for stat_index, stat in enumerate(FINAL_STAT_ORDER):
        value = context.set_insertion_base_stats[stat_index]
        for contribution in item_contributions:
            value = _fadd(value, contribution[stat_index])
        for gear_set in _APPLICATION_BY_FINAL_STAT[stat]:
            set_index = SET_CATALOG[gear_set].fribbels_index
            numeric = context.numeric_set_contributions[set_index][stat_index]
            if numeric != 0:
                value = _fadd(value, numeric)
        multiplier = context.final_stat_multipliers[stat_index]
        if multiplier != 1:
            value = _fmul(value, multiplier)
        post = context.post_set_modifier_contributions[stat_index]
        if post != 0:
            value = _fadd(value, post)
        unrounded.append(value)
    raw = tuple(math.trunc(value) for value in unrounded)
    effective = tuple(
        value if cap is None else min(value, cap)
        for value, cap in zip(raw, _PRIMARY_CAPS, strict=True)
    )
    return tuple(unrounded), raw, effective


def _passes_ranges(
    values: tuple[int, ...],
    minimums: tuple[float | None, ...],
    maximums: tuple[float | None, ...],
) -> bool:
    return all(
        (minimum is None or value >= minimum) and (maximum is None or value <= maximum)
        for value, minimum, maximum in zip(values, minimums, maximums, strict=True)
    )


def _skill_value(
    skill: CompiledSkillMetric,
    formula_inputs: tuple[float | int, ...],
    context: ExactBuildEvaluationContext,
) -> int:
    if skill.kind_code == SKILL_UNAVAILABLE:
        return 0
    atk = _f32(formula_inputs[0])
    hp = _f32(formula_inputs[1])
    defense = _f32(formula_inputs[2])
    speed = _f32(formula_inputs[3])
    crit_damage = _fdiv(formula_inputs[5], 100)
    target_defense = _f32(skill.target_defense)
    single_target = 1 if skill.target_count == 1 else 0
    pen_set_on = 1 if context.activation_counts[13] else 0
    real_penetration = _fmul(
        _fsub(1, skill.penetration),
        _fsub(1, _fmul(_fmul(pen_set_on, 0.15), single_target)),
    )
    stat_scalings = _fadd(_fmul(skill.self_hp_scaling, hp), _fmul(skill.self_attack_scaling, atk))
    stat_scalings = _fadd(stat_scalings, _fmul(skill.self_defense_scaling, defense))
    stat_scalings = _fadd(stat_scalings, _fmul(skill.self_speed_scaling, speed))
    if skill.hit_type_code == HIT_CRITICAL:
        hit_multiplier = _fadd(crit_damage, skill.critical_damage_increase)
    elif skill.hit_type_code == HIT_NONE:
        hit_multiplier = _f32(0)
    else:
        hit_multiplier = _f32(_NONCRITICAL_HIT_MULTIPLIERS[skill.hit_type_code])
    increased_multiplier = _fadd(1, skill.increased_value)
    damage_up_multiplier = _fadd(1, _fmul(skill.self_speed_scaling, speed))
    extra_scalings = _fadd(
        _fmul(skill.extra_attack_scaling, atk),
        _fmul(skill.extra_defense_scaling, defense),
    )
    extra_denominator = _fadd(_fdiv(_fmul(target_defense, 0.3), 300), 1)
    extra_damage = _fdiv(_fmul(extra_scalings, 1.871), extra_denominator)
    offensive = _fadd(_fmul(atk, skill.rate), stat_scalings)
    for multiplier in (
        1.871,
        skill.power,
        increased_multiplier,
        hit_multiplier,
        damage_up_multiplier,
        context.percent_damage_multiplier,
    ):
        offensive = _fmul(offensive, multiplier)
    support = _fadd(_fmul(skill.self_hp_scaling, hp), _fmul(skill.self_attack_scaling, atk))
    support = _fadd(support, _fmul(skill.self_defense_scaling, defense))
    support = _fmul(support, 1 if skill.kind_code == SKILL_SUPPORT else 0)
    defensive_denominator = _fadd(
        _fdiv(_fmul(target_defense, max(0, real_penetration)), 300), 1
    )
    return math.trunc(
        _fadd(_fadd(_fdiv(offensive, defensive_denominator), support), extra_damage)
    )


def _build_score(
    context: ExactBuildEvaluationContext,
    unrounded: tuple[float, ...],
    raw: tuple[int, ...],
) -> int:
    set_sequences = {
        0: (GearSet.ATTACK,),
        1: (GearSet.HEALTH, GearSet.WARFARE, GearSet.TORRENT),
        2: (GearSet.DEFENSE,),
    }
    ratio_components: list[float] = []
    for stat_index in _BASE_RELATIVE_INDICES:
        value = _fsub(unrounded[stat_index], context.base_stats[stat_index])
        value = _fsub(value, context.artifact_flat_stats[stat_index])
        for gear_set in set_sequences[stat_index]:
            set_index = SET_CATALOG[gear_set].fribbels_index
            value = _fsub(value, context.numeric_set_contributions[set_index][stat_index])
        ratio_components.append(_fmul(_fdiv(value, context.base_stats[stat_index]), 100))
    additive_sequences = {
        3: (GearSet.SPEED, GearSet.REVENGE, GearSet.REVERSAL, GearSet.WEAKENING),
        4: (GearSet.CRITICAL,),
        5: (GearSet.DESTRUCTION,),
        6: (GearSet.HIT,),
        7: (GearSet.RESIST,),
    }
    additive: dict[int, float] = {}
    for stat_index, gear_sets in additive_sequences.items():
        value = _fsub(raw[stat_index], context.base_stats[stat_index])
        for gear_set in gear_sets:
            set_index = SET_CATALOG[gear_set].fribbels_index
            value = _fsub(value, context.numeric_set_contributions[set_index][stat_index])
        additive[stat_index] = value
    hp, attack, defense = ratio_components[1], ratio_components[0], ratio_components[2]
    weighted = _fadd(_fadd(hp, attack), defense)
    weighted = _fadd(weighted, _fmul(additive[4], 1.6))
    weighted = _fadd(weighted, _fmul(additive[5], 1.14))
    weighted = _fadd(weighted, additive[6])
    weighted = _fadd(weighted, additive[7])
    weighted = _fadd(weighted, _fmul(additive[3], 2))
    return math.trunc(weighted)


def _derived_metrics(
    context: ExactBuildEvaluationContext,
    unrounded: tuple[float, ...],
    raw: tuple[int, ...],
    effective: tuple[int, ...],
    gear_score: int,
) -> tuple[int, ...]:
    formula_inputs: tuple[float | int, ...] = (
        unrounded[0],
        unrounded[1],
        unrounded[2],
        effective[3],
        effective[4],
        effective[5],
        effective[6],
        effective[7],
    )
    damage_sets = DamageSetDiagnostic(
        rage_groups=context.activation_counts[11],
        penetration_groups=context.activation_counts[13],
        torrent_groups=context.activation_counts[17],
        fervor_groups=context.activation_counts[23],
        penetration_target_defense=0,
        penetration_set_multiplier=context.penetration_set_multiplier,
        percent_damage_multiplier=context.percent_damage_multiplier,
    )
    values = _base_metrics(
        dict(zip(FINAL_STAT_ORDER, formula_inputs, strict=True)),
        damage_sets,
    )
    for skill in context.skills:
        values[DERIVED_METRIC_IDS[12 + skill.skill_index]] = _skill_value(
            skill,
            formula_inputs,
            context,
        )
    values["metric.build_score"] = _build_score(context, unrounded, raw)
    values["metric.gear_score"] = gear_score
    return tuple(values[metric_id] for metric_id in DERIVED_METRIC_IDS)


def _priority_score(
    context: ExactBuildEvaluationContext,
    item_contributions: tuple[tuple[float, ...], ...],
) -> float:
    total = sum(
        calculate_item_priority_score(
            context.base_stats,
            context.priorities,
            contribution,
        )[1]
        for contribution in item_contributions
    )
    return _f32(total)


def _compact_projection_unchecked(
    context: ExactBuildEvaluationContext,
    flat_index: int,
    dense_ids: tuple[int, ...],
    item_contributions: tuple[tuple[float, ...], ...],
    gear_scores: tuple[int, ...],
) -> ExactBuildRow:
    unrounded, raw, effective = _apply_stats(context, item_contributions)
    metrics = _derived_metrics(
        context,
        unrounded,
        raw,
        effective,
        sum(gear_scores),
    )
    priority = _priority_score(context, item_contributions)
    return ExactBuildRow(
        flat_index=flat_index,
        dense_ids=dense_ids,
        unrounded_final_stats=unrounded,
        raw_final_stats=raw,
        effective_final_stats=effective,
        derived_metrics=metrics,
        priority_score=priority,
    )


def validate_exact_build_search_context(
    context: ExactBuildEvaluationContext,
    slot_arrays: SearchReadySlotArrays,
) -> tuple[int, ...]:
    """Validate shared exact-search identities and return canonical slot radices."""

    if not isinstance(context, ExactBuildEvaluationContext):
        raise _error("invalid-evaluation-context", "context", "must be an ExactBuildEvaluationContext.")
    if not isinstance(slot_arrays, SearchReadySlotArrays):
        raise _error("invalid-search-arrays", "slot_arrays", "must be SearchReadySlotArrays.")
    identities = (
        ("request_id", slot_arrays.request_id, context.request_id),
        ("hero_id", slot_arrays.hero_id, context.hero_id),
        ("base_profile_id", slot_arrays.base_profile_id, context.base_profile_id),
    )
    for field, actual, expected in identities:
        if actual != expected:
            raise _error("search-context-mismatch", f"slot_arrays.{field}", f"must be {expected!r}; found {actual!r}.")
    if tuple(float(value) for value in slot_arrays.base_stats) != context.base_stats:
        raise _error("base-stat-context-mismatch", "slot_arrays.base_stats", "must match the evaluation context.")
    expected_mode = _PROJECTION_MODE_CODES[slot_arrays.diagnostics.projection_mode]
    if expected_mode != context.projection_mode_code:
        raise _error("projection-context-mismatch", "slot_arrays.diagnostics.projection_mode", "must match the evaluation context.")
    return tuple(len(slot.dense_ids) for slot in slot_arrays.slots)


def evaluate_exact_build_batch(
    context: ExactBuildEvaluationContext,
    slot_arrays: SearchReadySlotArrays,
    batch: CartesianBatch,
) -> ExactBuildBatchResult:
    """Evaluate exact-set rows and retain only those passing every hard bound."""

    radices = validate_exact_build_search_context(context, slot_arrays)
    if not isinstance(batch, CartesianBatch):
        raise _error("invalid-cartesian-batch", "batch", "must be a CartesianBatch.")
    if batch.search_space.radices != radices:
        raise _error("cartesian-radix-mismatch", "batch.search_space.radices", f"must equal prepared slot lengths {radices!r}.")

    exact_set_count = 0
    rejected_count = 0
    retained: list[ExactBuildRow] = []
    actual_contexts: dict[tuple[int, ...], ExactBuildEvaluationContext] = {}
    for row_index, offsets in enumerate(batch.slot_offsets):
        flat_index = batch.start_index + row_index
        dense_ids = tuple(
            slot.dense_ids[offset]
            for slot, offset in zip(slot_arrays.slots, offsets, strict=True)
        )
        set_indices = tuple(
            slot.set_indices[offset]
            for slot, offset in zip(slot_arrays.slots, offsets, strict=True)
        )
        actual_counts = [0] * SET_PATTERN_VECTOR_LENGTH
        for set_index in set_indices:
            actual_counts[set_index] += 1
        actual_count_tuple = tuple(actual_counts)
        if any(
            actual < required
            for actual, required in zip(
                actual_count_tuple,
                context.required_piece_counts,
                strict=True,
            )
        ):
            continue
        exact_set_count += 1
        actual_context = actual_contexts.get(actual_count_tuple)
        if actual_context is None:
            actual_context = with_set_piece_counts(context, actual_count_tuple)
            actual_contexts[actual_count_tuple] = actual_context
        contributions = tuple(
            slot.final_stat_contributions[offset]
            for slot, offset in zip(slot_arrays.slots, offsets, strict=True)
        )
        row = _compact_projection_unchecked(
            actual_context,
            flat_index,
            dense_ids,
            contributions,
            tuple(
                slot.gear_scores[offset]
                for slot, offset in zip(slot_arrays.slots, offsets, strict=True)
            ),
        )
        primary_passes = _passes_ranges(
            row.effective_final_stats,
            actual_context.primary_minimums,
            actual_context.primary_maximums,
        )
        derived_passes = _passes_ranges(
            row.derived_metrics,
            actual_context.derived_minimums,
            actual_context.derived_maximums,
        )
        if not primary_passes or not derived_passes:
            rejected_count += 1
            continue
        retained.append(row)
    return ExactBuildBatchResult(
        start_index=batch.start_index,
        stop_index=batch.stop_index,
        evaluated_count=batch.count,
        exact_set_count=exact_set_count,
        hard_bound_rejected_count=rejected_count,
        emitted_count=len(retained),
        rows=tuple(retained),
    )


__all__ = [
    "DERIVED_METRIC_COUNT",
    "FINAL_STAT_COUNT",
    "HIT_CRITICAL",
    "HIT_CRUSHING",
    "HIT_MISS",
    "HIT_NONE",
    "HIT_NORMAL",
    "HIT_TYPE_CODES",
    "SKILL_DAMAGE",
    "SKILL_KIND_CODES",
    "SKILL_SUPPORT",
    "SKILL_UNAVAILABLE",
    "CompiledSkillMetric",
    "ExactBuildBatchResult",
    "ExactBuildEvaluationContext",
    "ExactBuildEvaluationError",
    "ExactBuildRow",
    "compile_exact_build_context",
    "evaluate_exact_build_batch",
    "validate_exact_build_search_context",
    "with_set_piece_counts",
]
