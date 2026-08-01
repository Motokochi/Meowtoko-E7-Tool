"""Canonical display, import-alias, and set-completion metadata.

Fribbels names and set indices are aligned with RexQian's ``feat/offline``
branch. Callers must use the source-specific item-stat resolvers because the
Fribbels name ``Attack`` means flat Attack while the existing Meowtoko E7 Tool display
name ``Attack`` means Attack percent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, TypeVar

from src.optimizer.domain.enums import (
    FinalStat,
    GearRank,
    GearSet,
    GearSlot,
    ItemStatType,
    ReforgeMaterial,
    ResultCategory,
)


FRIBBELS_VOCABULARY_SOURCE_REVISION = "f49b0676c27d893ae4aa1b69920e4c98f37eb3fb"
FRIBBELS_VOCABULARY_SOURCE_URL = (
    "https://github.com/RexQian/Fribbels-Epic-7-Optimizer/tree/"
    + FRIBBELS_VOCABULARY_SOURCE_REVISION
)


@dataclass(frozen=True, slots=True)
class VocabularyMetadata:
    display_name: str
    fribbels_name: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SetMetadata(VocabularyMetadata):
    fribbels_index: int = 0
    pieces_required: int = 2
    stackable: bool = False


GEAR_SLOT_ORDER = tuple(GearSlot)
GEAR_RANK_ORDER = tuple(GearRank)
REFORGE_MATERIAL_ORDER = tuple(ReforgeMaterial)

GEAR_SLOT_CATALOG: Mapping[GearSlot, VocabularyMetadata] = MappingProxyType({
    GearSlot.WEAPON: VocabularyMetadata("Weapon", "Weapon", ("sword",)),
    GearSlot.HELMET: VocabularyMetadata("Helmet", "Helmet", ("helm", "helme")),
    GearSlot.ARMOR: VocabularyMetadata("Armor", "Armor", ("armour", "chest")),
    GearSlot.NECKLACE: VocabularyMetadata("Necklace", "Necklace", ("neck",)),
    GearSlot.RING: VocabularyMetadata("Ring", "Ring"),
    GearSlot.BOOTS: VocabularyMetadata("Boots", "Boots", ("boot", "shoes")),
})

GEAR_RANK_CATALOG: Mapping[GearRank, VocabularyMetadata] = MappingProxyType({
    GearRank.NORMAL: VocabularyMetadata("Normal", "Normal"),
    GearRank.GOOD: VocabularyMetadata("Good", "Good"),
    GearRank.RARE: VocabularyMetadata("Rare", "Rare"),
    GearRank.HEROIC: VocabularyMetadata("Heroic", "Heroic"),
    GearRank.EPIC: VocabularyMetadata("Epic", "Epic"),
})

REFORGE_MATERIAL_CATALOG: Mapping[ReforgeMaterial, VocabularyMetadata] = MappingProxyType({
    ReforgeMaterial.HUNT: VocabularyMetadata("Hunt", "Hunt"),
    ReforgeMaterial.CONVERSION: VocabularyMetadata("Conversion", "Conversion"),
    # Fribbels' Java enum has Hunt/Conversion, while the JavaScript augmenter
    # also emits this explicit fallback when name matching is inconclusive.
    ReforgeMaterial.UNKNOWN: VocabularyMetadata("Unknown", "Unknown"),
})


ITEM_STAT_CATALOG: Mapping[ItemStatType, VocabularyMetadata] = MappingProxyType({
    ItemStatType.FLAT_ATTACK: VocabularyMetadata(
        "Flat Attack", "Attack", ("flat atk", "flat attack", "flat ATK", "attack", "atk")
    ),
    ItemStatType.ATTACK_PERCENT: VocabularyMetadata(
        "Attack", "AttackPercent", ("ATK%", "atk%", "attack percent", "attack percentage")
    ),
    ItemStatType.FLAT_HEALTH: VocabularyMetadata(
        "Flat Health", "Health", ("flat hp", "flat health", "flat HP", "health", "hp")
    ),
    ItemStatType.HEALTH_PERCENT: VocabularyMetadata(
        "Health", "HealthPercent", ("HP%", "hp%", "health percent", "health percentage")
    ),
    ItemStatType.FLAT_DEFENSE: VocabularyMetadata(
        "Flat Defense", "Defense", ("flat def", "flat defence", "flat defense", "flat DEF", "defense", "defence", "def")
    ),
    ItemStatType.DEFENSE_PERCENT: VocabularyMetadata(
        "Defense", "DefensePercent", ("DEF%", "def%", "defense percent", "defense percentage")
    ),
    ItemStatType.SPEED: VocabularyMetadata("Speed", "Speed", ("spd",)),
    ItemStatType.CRITICAL_HIT_CHANCE_PERCENT: VocabularyMetadata(
        "Critical Hit Chance",
        "CriticalHitChancePercent",
        ("critical chance", "crit chance", "critical rate", "crit rate", "c.rate", "crate", "cc"),
    ),
    ItemStatType.CRITICAL_HIT_DAMAGE_PERCENT: VocabularyMetadata(
        "Critical Hit Damage",
        "CriticalHitDamagePercent",
        ("critical damage", "crit damage", "crit dmg", "c.dmg", "cdmg", "cd"),
    ),
    ItemStatType.EFFECTIVENESS_PERCENT: VocabularyMetadata(
        "Effectiveness", "EffectivenessPercent", ("eff",)
    ),
    ItemStatType.EFFECT_RESISTANCE_PERCENT: VocabularyMetadata(
        "Effect Resistance", "EffectResistancePercent", ("effect resist", "resistance", "eff res")
    ),
})

# Matches Fribbels StatType indices 0..10. Its additional ``Dac`` entry is a
# calculated hero property, not a legal gear main/substat, so it is excluded.
FRIBBELS_ITEM_STAT_ORDER = (
    ItemStatType.FLAT_ATTACK,
    ItemStatType.FLAT_HEALTH,
    ItemStatType.FLAT_DEFENSE,
    ItemStatType.ATTACK_PERCENT,
    ItemStatType.HEALTH_PERCENT,
    ItemStatType.DEFENSE_PERCENT,
    ItemStatType.CRITICAL_HIT_CHANCE_PERCENT,
    ItemStatType.CRITICAL_HIT_DAMAGE_PERCENT,
    ItemStatType.EFFECTIVENESS_PERCENT,
    ItemStatType.EFFECT_RESISTANCE_PERCENT,
    ItemStatType.SPEED,
)

# Canonical game legality for gear main stats. Import validation, saved
# right-side filters, and search preparation all consume this one catalog so
# they cannot silently drift apart.
RIGHT_SIDE_GEAR_SLOTS = (
    GearSlot.NECKLACE,
    GearSlot.RING,
    GearSlot.BOOTS,
)
_RIGHT_SIDE_BASE_MAIN_STATS = frozenset({
    ItemStatType.FLAT_ATTACK,
    ItemStatType.ATTACK_PERCENT,
    ItemStatType.FLAT_HEALTH,
    ItemStatType.HEALTH_PERCENT,
    ItemStatType.FLAT_DEFENSE,
    ItemStatType.DEFENSE_PERCENT,
})
ALLOWED_MAIN_STATS_BY_SLOT: Mapping[GearSlot, frozenset[ItemStatType]] = MappingProxyType({
    GearSlot.WEAPON: frozenset({ItemStatType.FLAT_ATTACK}),
    GearSlot.HELMET: frozenset({ItemStatType.FLAT_HEALTH}),
    GearSlot.ARMOR: frozenset({ItemStatType.FLAT_DEFENSE}),
    GearSlot.NECKLACE: _RIGHT_SIDE_BASE_MAIN_STATS
    | frozenset({
        ItemStatType.CRITICAL_HIT_CHANCE_PERCENT,
        ItemStatType.CRITICAL_HIT_DAMAGE_PERCENT,
    }),
    GearSlot.RING: _RIGHT_SIDE_BASE_MAIN_STATS
    | frozenset({
        ItemStatType.EFFECTIVENESS_PERCENT,
        ItemStatType.EFFECT_RESISTANCE_PERCENT,
    }),
    GearSlot.BOOTS: _RIGHT_SIDE_BASE_MAIN_STATS | frozenset({ItemStatType.SPEED}),
})

# Preserve the original analyzer dropdown order while deriving its values from
# the canonical item-stat catalog.
ITEM_STAT_DISPLAY_ORDER = (
    ItemStatType.FLAT_ATTACK,
    ItemStatType.ATTACK_PERCENT,
    ItemStatType.DEFENSE_PERCENT,
    ItemStatType.FLAT_DEFENSE,
    ItemStatType.FLAT_HEALTH,
    ItemStatType.HEALTH_PERCENT,
    ItemStatType.SPEED,
    ItemStatType.CRITICAL_HIT_CHANCE_PERCENT,
    ItemStatType.CRITICAL_HIT_DAMAGE_PERCENT,
    ItemStatType.EFFECTIVENESS_PERCENT,
    ItemStatType.EFFECT_RESISTANCE_PERCENT,
)


FINAL_STAT_ORDER = tuple(FinalStat)
FINAL_STAT_DISPLAY_NAMES: Mapping[FinalStat, str] = MappingProxyType({
    FinalStat.ATTACK: "Attack",
    FinalStat.HEALTH: "Health",
    FinalStat.DEFENSE: "Defense",
    FinalStat.SPEED: "Speed",
    FinalStat.CRITICAL_HIT_CHANCE: "Critical Hit Chance",
    FinalStat.CRITICAL_HIT_DAMAGE: "Critical Hit Damage",
    FinalStat.EFFECTIVENESS: "Effectiveness",
    FinalStat.EFFECT_RESISTANCE: "Effect Resistance",
})


# ``stackable`` means that a second completed copy changes the modeled bonus;
# it is not merely a statement that six slots can contain the same set name.
SET_CATALOG: Mapping[GearSet, SetMetadata] = MappingProxyType({
    GearSet.HEALTH: SetMetadata("Health Set", "HealthSet", ("health", "hp set"), 0, 2, True),
    GearSet.DEFENSE: SetMetadata("Defense Set", "DefenseSet", ("defense", "defence", "def set"), 1, 2, True),
    GearSet.ATTACK: SetMetadata("Attack Set", "AttackSet", ("attack", "atk set"), 2, 4, False),
    GearSet.SPEED: SetMetadata("Speed Set", "SpeedSet", ("speed",), 3, 4, False),
    GearSet.CRITICAL: SetMetadata("Critical Set", "CriticalSet", ("critical", "crit", "crit set", "critical chance"), 4, 2, True),
    GearSet.HIT: SetMetadata("Hit Set", "HitSet", ("hit",), 5, 2, True),
    GearSet.DESTRUCTION: SetMetadata("Destruction Set", "DestructionSet", ("destruction",), 6, 4, False),
    GearSet.LIFESTEAL: SetMetadata("Lifesteal Set", "LifestealSet", ("lifesteal", "life steal"), 7, 4, False),
    GearSet.COUNTER: SetMetadata("Counter Set", "CounterSet", ("counter",), 8, 4, False),
    GearSet.RESIST: SetMetadata("Resist Set", "ResistSet", ("resist", "resistance"), 9, 2, True),
    GearSet.UNITY: SetMetadata("Unity Set", "UnitySet", ("unity",), 10, 2, True),
    GearSet.RAGE: SetMetadata("Rage Set", "RageSet", ("rage",), 11, 4, False),
    GearSet.IMMUNITY: SetMetadata("Immunity Set", "ImmunitySet", ("immunity",), 12, 2, False),
    GearSet.PENETRATION: SetMetadata("Penetration Set", "PenetrationSet", ("penetration", "pen", "pen set"), 13, 2, False),
    GearSet.REVENGE: SetMetadata("Revenge Set", "RevengeSet", ("revenge",), 14, 4, False),
    GearSet.INJURY: SetMetadata("Injury Set", "InjurySet", ("injury",), 15, 4, False),
    GearSet.PROTECTION: SetMetadata("Protection Set", "ProtectionSet", ("protection",), 16, 4, False),
    GearSet.TORRENT: SetMetadata("Torrent Set", "TorrentSet", ("torrent",), 17, 2, True),
    GearSet.REVERSAL: SetMetadata("Reversal Set", "ReversalSet", ("reversal",), 18, 4, False),
    GearSet.RIPOSTE: SetMetadata("Riposte Set", "RiposteSet", ("riposte",), 19, 4, False),
    GearSet.WARFARE: SetMetadata("Warfare Set", "WarfareSet", ("warfare", "opener"), 20, 4, False),
    GearSet.PURSUIT: SetMetadata("Pursuit Set", "PursuitSet", ("pursuit", "chase"), 21, 2, False),
    GearSet.WEAKENING: SetMetadata("Weakening Set", "WeakeningSet", ("weakening", "weaken"), 22, 4, False),
    GearSet.FERVOR: SetMetadata("Fervor Set", "FervorSet", ("fervor", "fervour"), 23, 2, False),
})

FRIBBELS_SET_ORDER = tuple(GearSet)
DISPLAY_SET_ORDER = tuple(sorted(GearSet, key=lambda gear_set: SET_CATALOG[gear_set].display_name))


RESULT_CATEGORY_ORDER = tuple(ResultCategory)
RESULT_CATEGORY_DISPLAY_NAMES: Mapping[ResultCategory, str] = MappingProxyType({
    ResultCategory.EXACT: "Exact",
    ResultCategory.ONE_AWAY: "One replacement away",
    ResultCategory.TWO_AWAY: "Two replacements away",
})


EnumValue = TypeVar("EnumValue")


def _normalize(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _exact_index(catalog: Mapping[EnumValue, VocabularyMetadata], attribute: str) -> Mapping[str, EnumValue]:
    return MappingProxyType({_normalize(getattr(metadata, attribute)): key for key, metadata in catalog.items()})


def _alias_index(catalog: Mapping[EnumValue, VocabularyMetadata]) -> Mapping[str, EnumValue]:
    index: dict[str, EnumValue] = {}
    for key, metadata in catalog.items():
        for alias in metadata.aliases:
            normalized = _normalize(alias)
            if normalized and normalized not in index:
                index[normalized] = key
    return MappingProxyType(index)


_SLOT_DISPLAY_INDEX = _exact_index(GEAR_SLOT_CATALOG, "display_name")
_SLOT_FRIBBELS_INDEX = _exact_index(GEAR_SLOT_CATALOG, "fribbels_name")
_SLOT_ALIAS_INDEX = _alias_index(GEAR_SLOT_CATALOG)
_RANK_DISPLAY_INDEX = _exact_index(GEAR_RANK_CATALOG, "display_name")
_RANK_FRIBBELS_INDEX = _exact_index(GEAR_RANK_CATALOG, "fribbels_name")
_MATERIAL_DISPLAY_INDEX = _exact_index(REFORGE_MATERIAL_CATALOG, "display_name")
_MATERIAL_FRIBBELS_INDEX = _exact_index(REFORGE_MATERIAL_CATALOG, "fribbels_name")
_ITEM_DISPLAY_INDEX = _exact_index(ITEM_STAT_CATALOG, "display_name")
_ITEM_FRIBBELS_INDEX = _exact_index(ITEM_STAT_CATALOG, "fribbels_name")
_ITEM_ALIAS_INDEX = _alias_index(ITEM_STAT_CATALOG)
_SET_DISPLAY_INDEX = _exact_index(SET_CATALOG, "display_name")
_SET_FRIBBELS_INDEX = _exact_index(SET_CATALOG, "fribbels_name")
_SET_ALIAS_INDEX = _alias_index(SET_CATALOG)
_FINAL_DISPLAY_INDEX = MappingProxyType({_normalize(name): key for key, name in FINAL_STAT_DISPLAY_NAMES.items()})
_RESULT_DISPLAY_INDEX = MappingProxyType({_normalize(name): key for key, name in RESULT_CATEGORY_DISPLAY_NAMES.items()})


def _resolve(value: object, enum_type: type[EnumValue], *indices: Mapping[str, EnumValue]) -> EnumValue:
    if isinstance(value, enum_type):
        return value
    normalized = _normalize(value)
    for index in indices:
        if normalized in index:
            return index[normalized]
    try:
        return enum_type(str(value))
    except (TypeError, ValueError):
        raise ValueError(f"Unknown {enum_type.__name__}: {value!r}") from None


def resolve_gear_slot(value: object) -> GearSlot:
    return _resolve(value, GearSlot, _SLOT_DISPLAY_INDEX, _SLOT_FRIBBELS_INDEX, _SLOT_ALIAS_INDEX)


def gear_slot_display_name(value: object) -> str:
    return GEAR_SLOT_CATALOG[resolve_gear_slot(value)].display_name


def gear_slot_fribbels_name(value: object) -> str:
    return GEAR_SLOT_CATALOG[resolve_gear_slot(value)].fribbels_name


def resolve_gear_rank(value: object) -> GearRank:
    return _resolve(value, GearRank, _RANK_DISPLAY_INDEX, _RANK_FRIBBELS_INDEX)


def gear_rank_fribbels_name(value: object) -> str:
    return GEAR_RANK_CATALOG[resolve_gear_rank(value)].fribbels_name


def resolve_reforge_material(value: object) -> ReforgeMaterial:
    return _resolve(
        value,
        ReforgeMaterial,
        _MATERIAL_DISPLAY_INDEX,
        _MATERIAL_FRIBBELS_INDEX,
    )


def reforge_material_fribbels_name(value: object) -> str:
    return REFORGE_MATERIAL_CATALOG[resolve_reforge_material(value)].fribbels_name


def item_stat_from_display(value: object) -> ItemStatType:
    return _resolve(value, ItemStatType, _ITEM_DISPLAY_INDEX, _ITEM_ALIAS_INDEX)


def item_stat_from_fribbels(value: object) -> ItemStatType:
    return _resolve(value, ItemStatType, _ITEM_FRIBBELS_INDEX)


def item_stat_display_name(value: object) -> str:
    stat = value if isinstance(value, ItemStatType) else item_stat_from_display(value)
    return ITEM_STAT_CATALOG[stat].display_name


def item_stat_fribbels_name(value: object) -> str:
    stat = value if isinstance(value, ItemStatType) else item_stat_from_fribbels(value)
    return ITEM_STAT_CATALOG[stat].fribbels_name


def resolve_final_stat(value: object) -> FinalStat:
    return _resolve(value, FinalStat, _FINAL_DISPLAY_INDEX)


def final_stat_display_name(value: object) -> str:
    return FINAL_STAT_DISPLAY_NAMES[resolve_final_stat(value)]


def resolve_gear_set(value: object) -> GearSet:
    return _resolve(value, GearSet, _SET_DISPLAY_INDEX, _SET_FRIBBELS_INDEX, _SET_ALIAS_INDEX)


def gear_set_display_name(value: object) -> str:
    return SET_CATALOG[resolve_gear_set(value)].display_name


def gear_set_fribbels_name(value: object) -> str:
    return SET_CATALOG[resolve_gear_set(value)].fribbels_name


def get_set_metadata(value: object) -> SetMetadata:
    return SET_CATALOG[resolve_gear_set(value)]


def resolve_result_category(value: object) -> ResultCategory:
    return _resolve(value, ResultCategory, _RESULT_DISPLAY_INDEX)


def result_category_display_name(value: object) -> str:
    return RESULT_CATEGORY_DISPLAY_NAMES[resolve_result_category(value)]


def set_match_aliases_by_display() -> dict[str, list[str]]:
    return {
        metadata.display_name: list(metadata.aliases) + [metadata.fribbels_name]
        for metadata in SET_CATALOG.values()
    }


def gear_slot_match_aliases_by_display() -> dict[str, list[str]]:
    return {
        metadata.display_name: list(metadata.aliases)
        for metadata in GEAR_SLOT_CATALOG.values()
    }


def item_stat_match_aliases_by_display() -> dict[str, list[str]]:
    return {
        metadata.display_name: list(metadata.aliases) + [metadata.fribbels_name]
        for metadata in ITEM_STAT_CATALOG.values()
    }
