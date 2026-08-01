"""Stable identifiers shared by every optimizer boundary.

The prefixed string values deliberately keep item contributions, final hero
totals, sets, slots, and result categories in disjoint namespaces. Display and
Fribbels persistence names belong to the catalog rather than to enum values.
"""

from enum import StrEnum


class GearSlot(StrEnum):
    WEAPON = "slot.weapon"
    HELMET = "slot.helmet"
    ARMOR = "slot.armor"
    NECKLACE = "slot.necklace"
    RING = "slot.ring"
    BOOTS = "slot.boots"


class GearRank(StrEnum):
    NORMAL = "rank.normal"
    GOOD = "rank.good"
    RARE = "rank.rare"
    HEROIC = "rank.heroic"
    EPIC = "rank.epic"


class ReforgeMaterial(StrEnum):
    HUNT = "material.hunt"
    CONVERSION = "material.conversion"
    UNKNOWN = "material.unknown"


class ItemStatType(StrEnum):
    FLAT_ATTACK = "item_stat.flat_attack"
    ATTACK_PERCENT = "item_stat.attack_percent"
    FLAT_HEALTH = "item_stat.flat_health"
    HEALTH_PERCENT = "item_stat.health_percent"
    FLAT_DEFENSE = "item_stat.flat_defense"
    DEFENSE_PERCENT = "item_stat.defense_percent"
    SPEED = "item_stat.speed"
    CRITICAL_HIT_CHANCE_PERCENT = "item_stat.critical_hit_chance_percent"
    CRITICAL_HIT_DAMAGE_PERCENT = "item_stat.critical_hit_damage_percent"
    EFFECTIVENESS_PERCENT = "item_stat.effectiveness_percent"
    EFFECT_RESISTANCE_PERCENT = "item_stat.effect_resistance_percent"


class FinalStat(StrEnum):
    ATTACK = "final_stat.attack"
    HEALTH = "final_stat.health"
    DEFENSE = "final_stat.defense"
    SPEED = "final_stat.speed"
    CRITICAL_HIT_CHANCE = "final_stat.critical_hit_chance"
    CRITICAL_HIT_DAMAGE = "final_stat.critical_hit_damage"
    EFFECTIVENESS = "final_stat.effectiveness"
    EFFECT_RESISTANCE = "final_stat.effect_resistance"


class HeroModifierStatType(StrEnum):
    """Typed non-gear hero contributions before final-stat aggregation."""

    FLAT_ATTACK = "hero_modifier.flat_attack"
    ATTACK_PERCENT = "hero_modifier.attack_percent"
    FLAT_HEALTH = "hero_modifier.flat_health"
    HEALTH_PERCENT = "hero_modifier.health_percent"
    FLAT_DEFENSE = "hero_modifier.flat_defense"
    DEFENSE_PERCENT = "hero_modifier.defense_percent"
    SPEED = "hero_modifier.speed"
    CRITICAL_HIT_CHANCE_PERCENT = "hero_modifier.critical_hit_chance_percent"
    EFFECTIVENESS_PERCENT = "hero_modifier.effectiveness_percent"
    EFFECT_RESISTANCE_PERCENT = "hero_modifier.effect_resistance_percent"
    FINAL_ATTACK_PERCENT = "hero_modifier.final_attack_percent"
    FINAL_HEALTH_PERCENT = "hero_modifier.final_health_percent"
    FINAL_DEFENSE_PERCENT = "hero_modifier.final_defense_percent"
    DUAL_ATTACK_CHANCE_PERCENT = "hero_modifier.dual_attack_chance_percent"


class SkillSlot(StrEnum):
    S1 = "skill.s1"
    S2 = "skill.s2"
    S3 = "skill.s3"


class SkillHitType(StrEnum):
    CRITICAL = "hit.critical"
    CRUSHING = "hit.crushing"
    NORMAL = "hit.normal"
    MISS = "hit.miss"


class GearSet(StrEnum):
    # Declaration order matches Fribbels' serialized set index.
    HEALTH = "set.health"
    DEFENSE = "set.defense"
    ATTACK = "set.attack"
    SPEED = "set.speed"
    CRITICAL = "set.critical"
    HIT = "set.hit"
    DESTRUCTION = "set.destruction"
    LIFESTEAL = "set.lifesteal"
    COUNTER = "set.counter"
    RESIST = "set.resist"
    UNITY = "set.unity"
    RAGE = "set.rage"
    IMMUNITY = "set.immunity"
    PENETRATION = "set.penetration"
    REVENGE = "set.revenge"
    INJURY = "set.injury"
    PROTECTION = "set.protection"
    TORRENT = "set.torrent"
    REVERSAL = "set.reversal"
    RIPOSTE = "set.riposte"
    WARFARE = "set.warfare"
    PURSUIT = "set.pursuit"
    WEAKENING = "set.weakening"
    FERVOR = "set.fervor"


class ResultCategory(StrEnum):
    EXACT = "result.exact"
    ONE_AWAY = "result.one_away"
    TWO_AWAY = "result.two_away"


class ExecutionPreference(StrEnum):
    AUTO = "execution.auto"
    CPU = "execution.cpu"
    GPU = "execution.gpu"


class ItemProjectionMode(StrEnum):
    """The explicit item-stat view used by one optimizer execution."""

    CURRENT = "projection.current"
    REFORGED = "projection.reforged"
