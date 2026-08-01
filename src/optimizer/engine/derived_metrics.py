"""Pure Fribbels-compatible derived metrics and inclusive metric bounds."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from numbers import Real
from types import MappingProxyType
from typing import Mapping

from src.optimizer.data.skill_context_repository import (
    HeroSkillContextSelection,
    SkillContextSelection,
)
from src.optimizer.domain import (
    BuildMetrics,
    FinalStat,
    GearSet,
    ItemStatType,
    OptimizationRequest,
    SkillHitType,
    SkillSlot,
    StatRange,
)
from src.optimizer.engine.primary_stat_bounds import PrimaryStatBoundsResult
from src.optimizer.engine.stat_aggregation import _f32, _fadd, _fmul


FRIBBELS_DERIVED_METRIC_SOURCE_REVISION = "f49b0676c27d893ae4aa1b69920e4c98f37eb3fb"
FRIBBELS_DERIVED_METRIC_CALCULATOR_PATH = (
    "backend/src/main/java/com/fribbels/core/StatCalculator.java"
)
FRIBBELS_DERIVED_METRIC_CALCULATOR_GIT_BLOB_SHA1 = (
    "dfd9b1e363905a0aef3a2fca2e3369acde8d020e"
)
FRIBBELS_DERIVED_METRIC_GPU_KERNEL_PATH = (
    "backend/src/main/java/com/fribbels/gpu/GpuOptimizerKernel.java"
)
FRIBBELS_DERIVED_METRIC_GPU_KERNEL_GIT_BLOB_SHA1 = (
    "80d34477fd0548be8f63f4086884756febac5425"
)
FRIBBELS_GEAR_SCORE_PATH = "backend/src/main/java/com/fribbels/db/ItemDb.java"
FRIBBELS_GEAR_SCORE_GIT_BLOB_SHA1 = "de493420a0e6167c7a066f5d35a7a4f4e3edd623"
FRIBBELS_SKILL_MAPPING_PATH = "backend/src/main/java/com/fribbels/model/Hero.java"
FRIBBELS_SKILL_MAPPING_GIT_BLOB_SHA1 = "af788037c2fd4f8fb08b426d3c4b10ab8bdb2568"


@dataclass(frozen=True, slots=True)
class DerivedMetricRule:
    """Stable calculation identity separated from UI and Fribbels field names."""

    metric_id: str
    display_name: str
    fribbels_field: str


# BuildMetrics canonicalizes string keys lexically, so this public catalog uses
# that same stable order. Display order remains a UI concern.
DERIVED_METRIC_RULES = (
    DerivedMetricRule("metric.build_score", "Build Score", "bs"),
    DerivedMetricRule("metric.cp", "Combat Power", "cp"),
    DerivedMetricRule("metric.damage", "Average Damage", "dmg"),
    DerivedMetricRule("metric.damage_defense", "Damage × Defense", "dmgd"),
    DerivedMetricRule("metric.damage_health", "Damage × Health", "dmgh"),
    DerivedMetricRule("metric.damage_speed", "Damage × Speed", "dmgps"),
    DerivedMetricRule("metric.ehp", "Effective Health", "ehp"),
    DerivedMetricRule("metric.ehp_speed", "EHP × Speed", "ehpps"),
    DerivedMetricRule("metric.gear_score", "Gear Score", "score"),
    DerivedMetricRule("metric.hp_speed", "Health × Speed", "hpps"),
    DerivedMetricRule("metric.mcd", "Max Critical Damage", "mcdmg"),
    DerivedMetricRule("metric.mcd_speed", "MCD × Speed", "mcdmgps"),
    DerivedMetricRule("metric.s1", "S1", "s1"),
    DerivedMetricRule("metric.s2", "S2", "s2"),
    DerivedMetricRule("metric.s3", "S3", "s3"),
)
DERIVED_METRIC_CATALOG: Mapping[str, DerivedMetricRule] = MappingProxyType(
    {rule.metric_id: rule for rule in DERIVED_METRIC_RULES}
)
DERIVED_METRIC_IDS = tuple(rule.metric_id for rule in DERIVED_METRIC_RULES)


class DerivedMetricError(ValueError):
    """Actionable metric-input, source-selection, or catalog failure."""

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code} at {path}: {message}")


class DerivedMetricBoundStatus(StrEnum):
    UNRESTRICTED = "unrestricted"
    PASSED = "passed"
    BELOW_MINIMUM = "below-minimum"
    ABOVE_MAXIMUM = "above-maximum"


class DerivedMetricBoundSide(StrEnum):
    MINIMUM = "minimum"
    MAXIMUM = "maximum"


@dataclass(frozen=True, slots=True)
class DerivedMetricBoundEvaluation:
    metric_id: str
    value: int
    requested_range: StatRange
    range_supplied: bool
    status: DerivedMetricBoundStatus

    @property
    def constrained(self) -> bool:
        return (
            self.requested_range.minimum is not None
            or self.requested_range.maximum is not None
        )

    @property
    def passes(self) -> bool:
        return self.status in {
            DerivedMetricBoundStatus.UNRESTRICTED,
            DerivedMetricBoundStatus.PASSED,
        }

    @property
    def failure_side(self) -> DerivedMetricBoundSide | None:
        if self.status is DerivedMetricBoundStatus.BELOW_MINIMUM:
            return DerivedMetricBoundSide.MINIMUM
        if self.status is DerivedMetricBoundStatus.ABOVE_MAXIMUM:
            return DerivedMetricBoundSide.MAXIMUM
        return None


class SkillMetricKind(StrEnum):
    DAMAGE = "damage"
    SUPPORT = "support"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class SkillMetricDiagnostic:
    skill: SkillSlot
    metric_id: str
    kind: SkillMetricKind
    value: int
    source_option_id: str | None
    hit_type: SkillHitType | None
    target_defense: int | float
    target_count: int | None
    penetration: float | None
    penetration_set_applied: bool


@dataclass(frozen=True, slots=True)
class DamageSetDiagnostic:
    rage_groups: int
    penetration_groups: int
    torrent_groups: int
    fervor_groups: int
    penetration_target_defense: int | float
    penetration_set_multiplier: float
    percent_damage_multiplier: float


@dataclass(frozen=True, slots=True)
class GearScoreItemDiagnostic:
    item_id: str
    score: int


@dataclass(frozen=True, slots=True)
class BuildScoreDiagnostic:
    components: tuple[tuple[str, float], ...]
    value: int


@dataclass(frozen=True, slots=True)
class DerivedMetricDiagnostics:
    primary_bounds: PrimaryStatBoundsResult
    formula_inputs: tuple[tuple[FinalStat, int | float], ...]
    damage_sets: DamageSetDiagnostic
    skills: tuple[SkillMetricDiagnostic, ...]
    build_score: BuildScoreDiagnostic
    gear_scores: tuple[GearScoreItemDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class DerivedMetricsResult:
    metrics: BuildMetrics
    evaluations: tuple[DerivedMetricBoundEvaluation, ...]
    diagnostics: DerivedMetricDiagnostics

    @property
    def failures(self) -> tuple[DerivedMetricBoundEvaluation, ...]:
        return tuple(item for item in self.evaluations if not item.passes)

    @property
    def passes(self) -> bool:
        return not self.failures

    def value(self, metric_id: str) -> int | float:
        try:
            return dict(self.metrics.derived_metrics)[metric_id]
        except KeyError:
            raise DerivedMetricError(
                "unknown-derived-metric",
                "metricId",
                f"Unsupported derived metric ID {metric_id!r}.",
            ) from None

    def evaluation_for(self, metric_id: str) -> DerivedMetricBoundEvaluation:
        try:
            return next(item for item in self.evaluations if item.metric_id == metric_id)
        except StopIteration:
            raise DerivedMetricError(
                "unknown-derived-metric",
                "metricId",
                f"Unsupported derived metric ID {metric_id!r}.",
            ) from None


_UNRESTRICTED_RANGE = StatRange()
_SKILL_METRIC_IDS = {
    SkillSlot.S1: "metric.s1",
    SkillSlot.S2: "metric.s2",
    SkillSlot.S3: "metric.s3",
}
_NONCRITICAL_HIT_MULTIPLIERS = {
    SkillHitType.CRUSHING: 1.3,
    SkillHitType.NORMAL: 1.0,
    SkillHitType.MISS: 0.75,
}


def _fsub(left: int | float, right: int | float) -> float:
    return _f32(_f32(left) - _f32(right))


def _fdiv(left: int | float, right: int | float) -> float:
    denominator = _f32(right)
    if denominator == 0:
        raise DerivedMetricError(
            "division-by-zero", "calculation", "A Fribbels metric denominator became zero."
        )
    return _f32(_f32(left) / denominator)


def _activation_count(primary: PrimaryStatBoundsResult, gear_set: GearSet) -> int:
    activation = primary.set_evaluation.diagnostics.activation_for(gear_set)
    return 0 if activation is None else activation.activation_count


def _damage_set_effects(
    request: OptimizationRequest,
    primary: PrimaryStatBoundsResult,
) -> DamageSetDiagnostic:
    rage = _activation_count(primary, GearSet.RAGE)
    penetration = _activation_count(primary, GearSet.PENETRATION)
    torrent = _activation_count(primary, GearSet.TORRENT)
    fervor = _activation_count(primary, GearSet.FERVOR)

    target_defense = request.target_defense
    numerator = _fadd(_fdiv(target_defense, 300), 1)
    denominator = _fadd(_fmul(0.00283333, target_defense), 1)
    penetration_bonus = _fdiv(numerator, denominator)
    penetration_multiplier = penetration_bonus if penetration else _f32(1)
    rage_multiplier = _fmul(rage, 0.3)
    torrent_multiplier = _fmul(torrent, 0.1)
    fervor_multiplier = _fmul(min(fervor, 1), 0.2)
    percent_damage = _fadd(
        _fadd(_fadd(1, rage_multiplier), torrent_multiplier), fervor_multiplier
    )
    return DamageSetDiagnostic(
        rage_groups=rage,
        penetration_groups=penetration,
        torrent_groups=torrent,
        fervor_groups=fervor,
        penetration_target_defense=target_defense,
        penetration_set_multiplier=penetration_multiplier,
        percent_damage_multiplier=percent_damage,
    )


def _base_metrics(
    inputs: Mapping[FinalStat, int | float],
    damage_sets: DamageSetDiagnostic,
) -> dict[str, int]:
    atk = _f32(inputs[FinalStat.ATTACK])
    hp = _f32(inputs[FinalStat.HEALTH])
    defense = _f32(inputs[FinalStat.DEFENSE])
    speed = int(inputs[FinalStat.SPEED])
    crit_rate = _fdiv(inputs[FinalStat.CRITICAL_HIT_CHANCE], 100)
    crit_damage = _fdiv(inputs[FinalStat.CRITICAL_HIT_DAMAGE], 100)
    effectiveness = int(inputs[FinalStat.EFFECTIVENESS])
    resistance = int(inputs[FinalStat.EFFECT_RESISTANCE])

    attack_cp = _fadd(
        _fmul(atk, 1.6),
        _fmul(_fmul(_fmul(atk, 1.6), crit_rate), crit_damage),
    )
    speed_cp_float = _fmul(_fsub(speed, 45), 0.02)
    # The active Java expression uses the double literal 1.0 here, promoting
    # this subexpression and all remaining CP arithmetic to binary64.
    cp_inner = float(attack_cp) * (1.0 + float(speed_cp_float))
    cp_inner += float(hp)
    cp_inner += float(_fmul(defense, 9.3))
    cp_resist = _fadd(
        1,
        _fdiv(
            _fadd(_fdiv(resistance, 100), _fdiv(effectiveness, 100)),
            4,
        ),
    )
    cp = math.trunc(cp_inner * float(cp_resist))

    speed_div_1000 = _fdiv(speed, 1000)
    ehp = math.trunc(_fmul(hp, _fadd(_fdiv(defense, 300), 1)))
    hp_speed = math.trunc(_fmul(hp, speed_div_1000))
    ehp_speed = math.trunc(_fmul(ehp, speed_div_1000))

    expected_crit = _fadd(
        _fmul(_fmul(crit_rate, atk), crit_damage),
        _fmul(_fsub(1, crit_rate), atk),
    )
    damage = math.trunc(
        _fmul(
            _fmul(expected_crit, damage_sets.penetration_set_multiplier),
            damage_sets.percent_damage_multiplier,
        )
    )
    damage_speed = math.trunc(_fmul(damage, speed_div_1000))
    mcd = math.trunc(
        _fmul(
            _fmul(_fmul(atk, crit_damage), damage_sets.penetration_set_multiplier),
            damage_sets.percent_damage_multiplier,
        )
    )
    mcd_speed = math.trunc(_fmul(mcd, speed_div_1000))
    damage_health = math.trunc(
        _fdiv(
            _fmul(
                _fmul(
                    _fmul(crit_damage, hp),
                    damage_sets.penetration_set_multiplier,
                ),
                damage_sets.percent_damage_multiplier,
            ),
            10,
        )
    )
    damage_defense = math.trunc(
        _fmul(
            _fmul(
                _fmul(crit_damage, defense),
                damage_sets.penetration_set_multiplier,
            ),
            damage_sets.percent_damage_multiplier,
        )
    )
    return {
        "metric.cp": cp,
        "metric.damage": damage,
        "metric.damage_defense": damage_defense,
        "metric.damage_health": damage_health,
        "metric.damage_speed": damage_speed,
        "metric.ehp": ehp,
        "metric.ehp_speed": ehp_speed,
        "metric.hp_speed": hp_speed,
        "metric.mcd": mcd,
        "metric.mcd_speed": mcd_speed,
    }


def _selected_hit_type(selection: SkillContextSelection) -> SkillHitType | None:
    if selection.context.hit_type is not None:
        return selection.context.hit_type
    return selection.record.hit_types[0] if selection.record.hit_types else None


def _skill_metric(
    selection: SkillContextSelection,
    inputs: Mapping[FinalStat, int | float],
    damage_sets: DamageSetDiagnostic,
) -> SkillMetricDiagnostic:
    record = selection.record
    option = selection.source_option
    metric_id = _SKILL_METRIC_IDS[record.skill]
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
        self_defense = (
            0 if record.self_defense_scaling is None else record.self_defense_scaling
        )
        self_speed = 0 if record.self_speed_scaling is None else record.self_speed_scaling
        extra_attack = (
            0
            if record.extra_self_attack_scaling is None
            else record.extra_self_attack_scaling
        )
        extra_defense = (
            0
            if record.extra_self_defense_scaling is None
            else record.extra_self_defense_scaling
        )
        increased = 0 if record.increased_value is None else record.increased_value
        critical_increase = (
            0
            if record.critical_damage_increase is None
            else record.critical_damage_increase
        )
    else:
        rate = option.rate
        power = option.power
        self_hp = 0 if option.self_hp_scaling is None else option.self_hp_scaling
        self_attack = (
            0 if option.self_attack_scaling is None else option.self_attack_scaling
        )
        self_defense = (
            0 if option.self_defense_scaling is None else option.self_defense_scaling
        )
        # Source options are complete alternate SkillData records. Fields not
        # present on the option take the source model's zero defaults.
        self_speed = 0
        extra_attack = 0
        extra_defense = 0
        increased = 0
        critical_increase = 0

    if not is_support and (
        not selection.is_damaging or hit_type is None or target_count is None
    ):
        return SkillMetricDiagnostic(
            skill=record.skill,
            metric_id=metric_id,
            kind=SkillMetricKind.UNAVAILABLE,
            value=0,
            source_option_id=None if option is None else option.option_id,
            hit_type=hit_type,
            target_defense=selection.context.target_defense,
            target_count=target_count,
            penetration=None if penetration is None else float(penetration),
            penetration_set_applied=False,
        )

    atk = _f32(inputs[FinalStat.ATTACK])
    hp = _f32(inputs[FinalStat.HEALTH])
    defense = _f32(inputs[FinalStat.DEFENSE])
    speed = _f32(inputs[FinalStat.SPEED])
    crit_damage = _fdiv(inputs[FinalStat.CRITICAL_HIT_DAMAGE], 100)
    target_defense = _f32(selection.context.target_defense)
    single_target = 1 if target_count == 1 else 0
    penetration_value = _f32(0 if penetration is None else penetration)
    pen_set_on = 1 if damage_sets.penetration_groups else 0
    pen_set_applied = bool(pen_set_on and single_target and not is_support)
    real_penetration = _fmul(
        _fsub(1, penetration_value),
        _fsub(1, _fmul(_fmul(pen_set_on, 0.15), single_target)),
    )

    stat_scalings = _fadd(_fmul(self_hp, hp), _fmul(self_attack, atk))
    stat_scalings = _fadd(stat_scalings, _fmul(self_defense, defense))
    stat_scalings = _fadd(stat_scalings, _fmul(self_speed, speed))
    if hit_type is SkillHitType.CRITICAL:
        hit_multiplier = _fadd(crit_damage, critical_increase)
    elif hit_type is None:
        hit_multiplier = _f32(0)
    else:
        hit_multiplier = _f32(_NONCRITICAL_HIT_MULTIPLIERS[hit_type])
    increased_multiplier = _fadd(1, increased)
    damage_up_multiplier = _fadd(1, _fmul(self_speed, speed))

    extra_scalings = _fadd(_fmul(extra_attack, atk), _fmul(extra_defense, defense))
    extra_denominator = _fadd(_fdiv(_fmul(target_defense, 0.3), 300), 1)
    extra_damage = _fdiv(_fmul(extra_scalings, 1.871), extra_denominator)

    offensive = _fadd(_fmul(atk, rate), stat_scalings)
    for multiplier in (
        1.871,
        power,
        increased_multiplier,
        hit_multiplier,
        damage_up_multiplier,
        damage_sets.percent_damage_multiplier,
    ):
        offensive = _fmul(offensive, multiplier)
    support = _fadd(_fmul(self_hp, hp), _fmul(self_attack, atk))
    support = _fadd(support, _fmul(self_defense, defense))
    support = _fmul(support, 1 if is_support else 0)
    defensive_denominator = _fadd(
        _fdiv(_fmul(target_defense, max(0, real_penetration)), 300), 1
    )
    value = math.trunc(
        _fadd(_fadd(_fdiv(offensive, defensive_denominator), support), extra_damage)
    )
    return SkillMetricDiagnostic(
        skill=record.skill,
        metric_id=metric_id,
        kind=SkillMetricKind.SUPPORT if is_support else SkillMetricKind.DAMAGE,
        value=value,
        source_option_id=None if option is None else option.option_id,
        hit_type=hit_type,
        target_defense=selection.context.target_defense,
        target_count=target_count,
        penetration=None if penetration is None else float(penetration),
        penetration_set_applied=pen_set_applied,
    )


def _set_contribution(
    primary: PrimaryStatBoundsResult,
    gear_set: GearSet,
    stat: FinalStat,
) -> float:
    activation = primary.set_evaluation.diagnostics.activation_for(gear_set)
    if activation is None:
        return _f32(0)
    return _f32(dict(activation.numeric_contributions).get(stat, 0))


def _build_score(primary: PrimaryStatBoundsResult) -> BuildScoreDiagnostic:
    set_diagnostics = primary.set_evaluation.diagnostics
    unrounded = dict(set_diagnostics.unrounded_final_stats)
    raw = dict(primary.raw_final_stats)
    pre_set = set_diagnostics.pre_set_diagnostics
    base = dict(pre_set.base_stats)
    artifact = dict(pre_set.artifact_flat_stats)

    def ratio_component(stat: FinalStat, sets: tuple[GearSet, ...]) -> float:
        value = _fsub(unrounded[stat], base[stat])
        value = _fsub(value, artifact[stat])
        for gear_set in sets:
            value = _fsub(value, _set_contribution(primary, gear_set, stat))
        return _fmul(_fdiv(value, base[stat]), 100)

    hp = ratio_component(
        FinalStat.HEALTH, (GearSet.HEALTH, GearSet.WARFARE, GearSet.TORRENT)
    )
    attack = ratio_component(FinalStat.ATTACK, (GearSet.ATTACK,))
    defense = ratio_component(FinalStat.DEFENSE, (GearSet.DEFENSE,))

    additive_sets = {
        FinalStat.CRITICAL_HIT_CHANCE: (GearSet.CRITICAL,),
        FinalStat.CRITICAL_HIT_DAMAGE: (GearSet.DESTRUCTION,),
        FinalStat.EFFECTIVENESS: (GearSet.HIT,),
        FinalStat.EFFECT_RESISTANCE: (GearSet.RESIST,),
        FinalStat.SPEED: (
            GearSet.SPEED,
            GearSet.REVENGE,
            GearSet.REVERSAL,
            GearSet.WEAKENING,
        ),
    }
    additive: dict[FinalStat, float] = {}
    for stat, sets in additive_sets.items():
        value = _fsub(raw[stat], base[stat])
        for gear_set in sets:
            value = _fsub(value, _set_contribution(primary, gear_set, stat))
        additive[stat] = value

    components = (
        ("health", hp),
        ("attack", attack),
        ("defense", defense),
        ("critical_hit_chance", additive[FinalStat.CRITICAL_HIT_CHANCE]),
        ("critical_hit_damage", additive[FinalStat.CRITICAL_HIT_DAMAGE]),
        ("effectiveness", additive[FinalStat.EFFECTIVENESS]),
        ("effect_resistance", additive[FinalStat.EFFECT_RESISTANCE]),
        ("speed", additive[FinalStat.SPEED]),
    )
    weighted = _fadd(_fadd(hp, attack), defense)
    weighted = _fadd(
        weighted, _fmul(additive[FinalStat.CRITICAL_HIT_CHANCE], 1.6)
    )
    weighted = _fadd(
        weighted, _fmul(additive[FinalStat.CRITICAL_HIT_DAMAGE], 1.14)
    )
    weighted = _fadd(weighted, additive[FinalStat.EFFECTIVENESS])
    weighted = _fadd(weighted, additive[FinalStat.EFFECT_RESISTANCE])
    weighted = _fadd(weighted, _fmul(additive[FinalStat.SPEED], 2))
    return BuildScoreDiagnostic(components=components, value=math.trunc(weighted))


def calculate_item_gear_score(
    item_id: str,
    selected_totals: object,
    main_stat: ItemStatType | str,
    selected_main_value: int | float,
) -> GearScoreItemDiagnostic:
    """Calculate one item's exact Fribbels substat-only WSS contribution."""

    if not isinstance(item_id, str) or not item_id.strip():
        raise DerivedMetricError(
            "invalid-item-id",
            "item.itemId",
            "Expected a non-empty stable item ID.",
        )
    raw_items = selected_totals.items() if isinstance(selected_totals, Mapping) else selected_totals
    if isinstance(selected_totals, (str, bytes, bytearray)):
        raise DerivedMetricError(
            "invalid-stat-totals",
            "item.selectedTotals",
            "Expected complete item stat/value pairs.",
        )
    try:
        pairs = tuple(raw_items)
    except TypeError:
        raise DerivedMetricError(
            "invalid-stat-totals",
            "item.selectedTotals",
            "Expected complete item stat/value pairs.",
        ) from None
    stats: dict[ItemStatType, float] = {}
    for index, pair in enumerate(pairs):
        try:
            raw_stat, raw_value = pair
        except (TypeError, ValueError):
            raise DerivedMetricError(
                "invalid-stat-totals",
                f"item.selectedTotals[{index}]",
                "Expected exactly one stat and one value.",
            ) from None
        try:
            stat = raw_stat if isinstance(raw_stat, ItemStatType) else ItemStatType(raw_stat)
        except (TypeError, ValueError):
            raise DerivedMetricError(
                "unknown-item-stat",
                f"item.selectedTotals[{index}].stat",
                "Expected a canonical ItemStatType stable ID.",
            ) from None
        if stat in stats:
            raise DerivedMetricError(
                "duplicate-item-stat",
                f"item.selectedTotals.{stat.value}",
                "Each item stat must appear exactly once.",
            )
        if isinstance(raw_value, bool) or not isinstance(raw_value, Real):
            raise DerivedMetricError(
                "invalid-number",
                f"item.selectedTotals.{stat.value}",
                "Expected a finite nonnegative number.",
            )
        value = float(raw_value)
        if not math.isfinite(value) or value < 0:
            raise DerivedMetricError(
                "invalid-number",
                f"item.selectedTotals.{stat.value}",
                "Expected a finite nonnegative number.",
            )
        stats[stat] = value
    missing = [stat.value for stat in ItemStatType if stat not in stats]
    if missing:
        raise DerivedMetricError(
            "partial-stat-totals",
            "item.selectedTotals",
            "Item totals must contain every stat; missing: " + ", ".join(missing) + ".",
        )
    try:
        normalized_main_stat = (
            main_stat if isinstance(main_stat, ItemStatType) else ItemStatType(main_stat)
        )
    except (TypeError, ValueError):
        raise DerivedMetricError(
            "main-stat-evidence-required",
            "item.mainStat",
            "Fribbels gear score requires a canonical main stat.",
        ) from None
    if isinstance(selected_main_value, bool) or not isinstance(selected_main_value, Real):
        raise DerivedMetricError(
            "main-stat-evidence-required",
            "item.selectedMainValue",
            "Fribbels gear score requires a finite nonnegative main value.",
        )
    main_value = float(selected_main_value)
    if not math.isfinite(main_value) or main_value < 0:
        raise DerivedMetricError(
            "main-stat-evidence-required",
            "item.selectedMainValue",
            "Fribbels gear score requires a finite nonnegative main value.",
        )
    stats[normalized_main_stat] -= main_value
    if stats[normalized_main_stat] < -1e-9:
        raise DerivedMetricError(
            "main-value-exceeds-total",
            "item.mainStat",
            "The selected main value exceeds the selected complete stat total.",
        )
    stats[normalized_main_stat] = max(0.0, stats[normalized_main_stat])
    value = (
        stats[ItemStatType.ATTACK_PERCENT]
        + stats[ItemStatType.DEFENSE_PERCENT]
        + stats[ItemStatType.HEALTH_PERCENT]
        + stats[ItemStatType.EFFECT_RESISTANCE_PERCENT]
        + stats[ItemStatType.EFFECTIVENESS_PERCENT]
        + stats[ItemStatType.SPEED] * (8.0 / 4.0)
        + stats[ItemStatType.CRITICAL_HIT_DAMAGE_PERCENT] * (8.0 / 7.0)
        + stats[ItemStatType.CRITICAL_HIT_CHANCE_PERCENT] * (8.0 / 5.0)
        + stats[ItemStatType.FLAT_ATTACK] * (3.46 / 39.0)
        + stats[ItemStatType.FLAT_DEFENSE] * (4.99 / 31.0)
        + stats[ItemStatType.FLAT_HEALTH] * (3.09 / 174.0)
    )
    # Java Math.round is floor(x + 0.5) for these nonnegative values.
    return GearScoreItemDiagnostic(item_id.strip(), math.floor(value + 0.5))


def _gear_score(primary: PrimaryStatBoundsResult) -> tuple[int, tuple[GearScoreItemDiagnostic, ...]]:
    item_scores: list[GearScoreItemDiagnostic] = []
    for item in primary.set_evaluation.diagnostics.pre_set_diagnostics.items:
        if item.main_stat is None or item.selected_main_value is None:
            raise DerivedMetricError(
                "main-stat-evidence-required",
                f"items[{item.slot.value}].mainStat",
                "Fribbels gear score excludes main stats and requires their evidence.",
            )
        item_scores.append(
            calculate_item_gear_score(
                item.item_id,
                item.selected_totals,
                item.main_stat,
                item.selected_main_value,
            )
        )
    return sum(item.score for item in item_scores), tuple(item_scores)


def _bound_status(value: int, requested: StatRange) -> DerivedMetricBoundStatus:
    if requested.minimum is None and requested.maximum is None:
        return DerivedMetricBoundStatus.UNRESTRICTED
    if requested.minimum is not None and value < requested.minimum:
        return DerivedMetricBoundStatus.BELOW_MINIMUM
    if requested.maximum is not None and value > requested.maximum:
        return DerivedMetricBoundStatus.ABOVE_MAXIMUM
    return DerivedMetricBoundStatus.PASSED


def calculate_derived_metrics(
    request: OptimizationRequest,
    primary_bounds: PrimaryStatBoundsResult,
    skill_selection: HeroSkillContextSelection,
) -> DerivedMetricsResult:
    """Calculate all canonical metrics even when primary-stat bounds failed."""

    if not isinstance(request, OptimizationRequest):
        raise TypeError("request must be an OptimizationRequest.")
    if not isinstance(primary_bounds, PrimaryStatBoundsResult):
        raise TypeError("primary_bounds must be a PrimaryStatBoundsResult.")
    if not isinstance(skill_selection, HeroSkillContextSelection):
        raise TypeError("skill_selection must be a HeroSkillContextSelection.")
    if skill_selection.hero.hero_id != request.hero_id:
        raise DerivedMetricError(
            "hero-selection-mismatch",
            "skillSelection.heroId",
            "The resolved skills do not belong to request.heroId.",
        )
    if skill_selection.contexts != request.skill_contexts:
        raise DerivedMetricError(
            "skill-context-mismatch",
            "skillSelection.contexts",
            "The resolved skill contexts do not match the request.",
        )

    requested_ranges = dict(request.derived_metric_ranges)
    unknown = sorted(set(requested_ranges) - set(DERIVED_METRIC_CATALOG))
    if unknown:
        raise DerivedMetricError(
            "unknown-derived-metric",
            "request.derivedMetricRanges",
            "Unsupported derived metric ID(s): " + ", ".join(unknown) + ".",
        )

    effective = dict(primary_bounds.effective_final_stats)
    unrounded = dict(primary_bounds.set_evaluation.diagnostics.unrounded_final_stats)
    formula_inputs: dict[FinalStat, int | float] = dict(effective)
    for stat in (FinalStat.ATTACK, FinalStat.HEALTH, FinalStat.DEFENSE):
        formula_inputs[stat] = unrounded[stat]

    damage_sets = _damage_set_effects(request, primary_bounds)
    values = _base_metrics(formula_inputs, damage_sets)
    skill_diagnostics = tuple(
        _skill_metric(selection, formula_inputs, damage_sets)
        for selection in skill_selection.skills
    )
    values.update((item.metric_id, item.value) for item in skill_diagnostics)
    build_score = _build_score(primary_bounds)
    gear_score, item_gear_scores = _gear_score(primary_bounds)
    values["metric.build_score"] = build_score.value
    values["metric.gear_score"] = gear_score

    canonical_metrics = tuple((metric_id, values[metric_id]) for metric_id in DERIVED_METRIC_IDS)
    evaluations = tuple(
        DerivedMetricBoundEvaluation(
            metric_id=metric_id,
            value=values[metric_id],
            requested_range=requested_ranges.get(metric_id, _UNRESTRICTED_RANGE),
            range_supplied=metric_id in requested_ranges,
            status=_bound_status(
                values[metric_id], requested_ranges.get(metric_id, _UNRESTRICTED_RANGE)
            ),
        )
        for metric_id in DERIVED_METRIC_IDS
    )
    return DerivedMetricsResult(
        metrics=BuildMetrics(
            final_stats=primary_bounds.effective_final_stats,
            derived_metrics=canonical_metrics,
            priority_score=0,
        ),
        evaluations=evaluations,
        diagnostics=DerivedMetricDiagnostics(
            primary_bounds=primary_bounds,
            formula_inputs=tuple((stat, formula_inputs[stat]) for stat in FinalStat),
            damage_sets=damage_sets,
            skills=skill_diagnostics,
            build_score=build_score,
            gear_scores=item_gear_scores,
        ),
    )


__all__ = [
    "DERIVED_METRIC_CATALOG",
    "DERIVED_METRIC_IDS",
    "DERIVED_METRIC_RULES",
    "FRIBBELS_DERIVED_METRIC_CALCULATOR_GIT_BLOB_SHA1",
    "FRIBBELS_DERIVED_METRIC_CALCULATOR_PATH",
    "FRIBBELS_DERIVED_METRIC_GPU_KERNEL_GIT_BLOB_SHA1",
    "FRIBBELS_DERIVED_METRIC_GPU_KERNEL_PATH",
    "FRIBBELS_DERIVED_METRIC_SOURCE_REVISION",
    "FRIBBELS_GEAR_SCORE_GIT_BLOB_SHA1",
    "FRIBBELS_GEAR_SCORE_PATH",
    "FRIBBELS_SKILL_MAPPING_GIT_BLOB_SHA1",
    "FRIBBELS_SKILL_MAPPING_PATH",
    "BuildScoreDiagnostic",
    "DamageSetDiagnostic",
    "DerivedMetricBoundEvaluation",
    "DerivedMetricBoundSide",
    "DerivedMetricBoundStatus",
    "DerivedMetricDiagnostics",
    "DerivedMetricError",
    "DerivedMetricRule",
    "DerivedMetricsResult",
    "GearScoreItemDiagnostic",
    "SkillMetricDiagnostic",
    "SkillMetricKind",
    "calculate_derived_metrics",
    "calculate_item_gear_score",
]
