"""Immutable optimizer input, request, metric, and result records.

These records are the unversioned domain payloads. Versioned persistence
envelopes and migrations belong to phase P00-T03.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from numbers import Real
from typing import Any, TypeVar

from src.optimizer.domain.catalog import (
    ALLOWED_MAIN_STATS_BY_SLOT,
    FRIBBELS_ITEM_STAT_ORDER,
    GEAR_SLOT_ORDER,
    RIGHT_SIDE_GEAR_SLOTS,
    SET_CATALOG,
)
from src.optimizer.domain.enums import (
    ExecutionPreference,
    FinalStat,
    GearSet,
    GearSlot,
    HeroModifierStatType,
    ItemProjectionMode,
    ItemStatType,
    SkillHitType,
    SkillSlot,
)


MAX_RESULT_CAP = 5_000_000


class DomainValidationError(ValueError):
    """Raised when an optimizer record violates its domain contract."""


EnumType = TypeVar("EnumType", bound=Enum)
Number = int | float


def _record_mapping(
    value: object,
    record_name: str,
    *,
    required: Iterable[str],
    optional: Iterable[str] = (),
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DomainValidationError(f"{record_name} must be an object.")
    required_fields = set(required)
    allowed_fields = required_fields | set(optional)
    missing = sorted(required_fields - set(value))
    unknown = sorted(set(value) - allowed_fields)
    if missing:
        raise DomainValidationError(f"{record_name} is missing required field(s): {', '.join(missing)}.")
    if unknown:
        raise DomainValidationError(f"{record_name} contains unknown field(s): {', '.join(unknown)}.")
    return value


def _stable_id(value: object, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        suffix = " or null" if optional else ""
        raise DomainValidationError(f"{field} must be a non-empty string{suffix}.")
    return value.strip()


def _integer(
    value: object,
    field: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DomainValidationError(f"{field} must be an integer.")
    if minimum is not None and value < minimum:
        raise DomainValidationError(f"{field} must be at least {minimum}.")
    if maximum is not None and value > maximum:
        raise DomainValidationError(f"{field} must be at most {maximum}.")
    return value


def _dense_id(value: object, field: str) -> int | None:
    if value is None:
        return None
    return _integer(value, field, minimum=0)


def _number(
    value: object,
    field: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> Number:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise DomainValidationError(f"{field} must be a finite number.")
    numeric = int(value) if isinstance(value, int) else float(value)
    if not math.isfinite(numeric):
        raise DomainValidationError(f"{field} must be a finite number.")
    if minimum is not None and numeric < minimum:
        raise DomainValidationError(f"{field} must be at least {minimum}.")
    if maximum is not None and numeric > maximum:
        raise DomainValidationError(f"{field} must be at most {maximum}.")
    return numeric


def _optional_number(value: object, field: str) -> Number | None:
    return None if value is None else _number(value, field)


def _optional_nonnegative_number(value: object, field: str) -> Number | None:
    return None if value is None else _number(value, field, minimum=0)


def _boolean(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise DomainValidationError(f"{field} must be a boolean.")
    return value


def _enum_member(value: object, enum_type: type[EnumType], field: str) -> EnumType:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        raise DomainValidationError(f"{field} must be a valid {enum_type.__name__} stable ID.") from None


def _pair_items(value: object, field: str) -> tuple[tuple[object, object], ...]:
    raw_items = value.items() if isinstance(value, Mapping) else value
    try:
        items = tuple(raw_items)
    except TypeError:
        raise DomainValidationError(f"{field} must be an object or sequence of key/value pairs.") from None
    normalized = []
    for index, pair in enumerate(items):
        try:
            key, item_value = pair
        except (TypeError, ValueError):
            raise DomainValidationError(f"{field}[{index}] must contain exactly two values.") from None
        normalized.append((key, item_value))
    return tuple(normalized)


def _enum_number_pairs(
    value: object,
    enum_type: type[EnumType],
    field: str,
    *,
    require_all: bool = False,
) -> tuple[tuple[EnumType, Number], ...]:
    order = {member: index for index, member in enumerate(enum_type)}
    result: dict[EnumType, Number] = {}
    for raw_key, raw_value in _pair_items(value, field):
        key = _enum_member(raw_key, enum_type, f"{field}.key")
        if key in result:
            raise DomainValidationError(f"{field} contains duplicate key {key.value}.")
        result[key] = _number(raw_value, f"{field}.{key.value}")
    if require_all and set(result) != set(enum_type):
        missing = [member.value for member in enum_type if member not in result]
        raise DomainValidationError(f"{field} is missing: {', '.join(missing)}.")
    return tuple(sorted(result.items(), key=lambda pair: order[pair[0]]))


def _string_number_pairs(value: object, field: str) -> tuple[tuple[str, Number], ...]:
    result: dict[str, Number] = {}
    for raw_key, raw_value in _pair_items(value, field):
        key = _stable_id(raw_key, f"{field}.key")
        if key in result:
            raise DomainValidationError(f"{field} contains duplicate key {key}.")
        result[key] = _number(raw_value, f"{field}.{key}")
    return tuple(sorted(result.items()))


def _stat_range_pairs(value: object, field: str) -> tuple[tuple[FinalStat, StatRange], ...]:
    order = {member: index for index, member in enumerate(FinalStat)}
    result: dict[FinalStat, StatRange] = {}
    for raw_key, raw_range in _pair_items(value, field):
        key = _enum_member(raw_key, FinalStat, f"{field}.key")
        if key in result:
            raise DomainValidationError(f"{field} contains duplicate key {key.value}.")
        result[key] = raw_range if isinstance(raw_range, StatRange) else StatRange.from_dict(raw_range)
    return tuple(sorted(result.items(), key=lambda pair: order[pair[0]]))


def _metric_range_pairs(value: object, field: str) -> tuple[tuple[str, StatRange], ...]:
    result: dict[str, StatRange] = {}
    for raw_key, raw_range in _pair_items(value, field):
        key = _stable_id(raw_key, f"{field}.key")
        if key in result:
            raise DomainValidationError(f"{field} contains duplicate key {key}.")
        result[key] = raw_range if isinstance(raw_range, StatRange) else StatRange.from_dict(raw_range)
    return tuple(sorted(result.items()))


def _priority_pairs(value: object, field: str) -> tuple[tuple[FinalStat, int], ...]:
    order = {member: index for index, member in enumerate(FinalStat)}
    result: dict[FinalStat, int] = {}
    for raw_key, raw_priority in _pair_items(value, field):
        key = _enum_member(raw_key, FinalStat, f"{field}.key")
        if key in result:
            raise DomainValidationError(f"{field} contains duplicate key {key.value}.")
        result[key] = _integer(raw_priority, f"{field}.{key.value}", minimum=-1, maximum=3)
    return tuple(sorted(result.items(), key=lambda pair: order[pair[0]]))


def _string_ids(value: object, field: str) -> tuple[str, ...]:
    if isinstance(value, str):
        raise DomainValidationError(f"{field} must be a sequence of stable IDs, not a string.")
    try:
        values = tuple(_stable_id(item, f"{field}[]") for item in value)
    except TypeError:
        raise DomainValidationError(f"{field} must be a sequence of stable IDs.") from None
    if len(values) != len(set(values)):
        raise DomainValidationError(f"{field} must not contain duplicate IDs.")
    return tuple(sorted(values))


def _right_side_main_stat_pairs(
    value: object,
    field: str,
) -> tuple[tuple[GearSlot, tuple[ItemStatType, ...]], ...]:
    slot_order = {slot: index for index, slot in enumerate(GEAR_SLOT_ORDER)}
    stat_order = {stat: index for index, stat in enumerate(FRIBBELS_ITEM_STAT_ORDER)}
    result: dict[GearSlot, tuple[ItemStatType, ...]] = {}
    for raw_slot, raw_stats in _pair_items(value, field):
        slot = _enum_member(raw_slot, GearSlot, f"{field}.key")
        if slot in result:
            raise DomainValidationError(f"{field} contains duplicate slot {slot.value}.")
        if slot not in RIGHT_SIDE_GEAR_SLOTS:
            raise DomainValidationError(
                f"{field} supports Necklace, Ring, and Boots only; {slot.value} is fixed-side gear."
            )
        if isinstance(raw_stats, (str, bytes, bytearray)):
            raise DomainValidationError(
                f"{field}.{slot.value} must be a non-empty sequence of item stat IDs."
            )
        try:
            supplied = tuple(raw_stats)
        except TypeError:
            raise DomainValidationError(
                f"{field}.{slot.value} must be a non-empty sequence of item stat IDs."
            ) from None
        if not supplied:
            raise DomainValidationError(
                f"{field}.{slot.value} must not be empty; omit the slot for no restriction."
            )
        stats = tuple(
            _enum_member(raw_stat, ItemStatType, f"{field}.{slot.value}[]")
            for raw_stat in supplied
        )
        if len(stats) != len(set(stats)):
            raise DomainValidationError(
                f"{field}.{slot.value} must not contain duplicate item stat IDs."
            )
        illegal = tuple(
            stat for stat in stats if stat not in ALLOWED_MAIN_STATS_BY_SLOT[slot]
        )
        if illegal:
            raise DomainValidationError(
                f"{field}.{slot.value} contains illegal main stat {illegal[0].value}."
            )
        result[slot] = tuple(sorted(stats, key=stat_order.__getitem__))
    return tuple(sorted(result.items(), key=lambda pair: slot_order[pair[0]]))


@dataclass(frozen=True, slots=True)
class StatRange:
    minimum: Number | None = None
    maximum: Number | None = None

    def __post_init__(self) -> None:
        minimum = _optional_number(self.minimum, "StatRange.minimum")
        maximum = _optional_number(self.maximum, "StatRange.maximum")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise DomainValidationError("StatRange.minimum must not exceed StatRange.maximum.")
        object.__setattr__(self, "minimum", minimum)
        object.__setattr__(self, "maximum", maximum)

    def contains(self, value: object) -> bool:
        numeric = _number(value, "StatRange.value")
        return (self.minimum is None or numeric >= self.minimum) and (
            self.maximum is None or numeric <= self.maximum
        )

    def to_dict(self) -> dict[str, Number | None]:
        return {"minimum": self.minimum, "maximum": self.maximum}

    @classmethod
    def from_dict(cls, value: object) -> StatRange:
        data = _record_mapping(value, "StatRange", required=(), optional=("minimum", "maximum"))
        return cls(minimum=data.get("minimum"), maximum=data.get("maximum"))


@dataclass(frozen=True, slots=True)
class GearSearchFilters:
    """Saved request filters applied before dense search arrays are assigned."""

    right_side_main_stats: tuple[
        tuple[GearSlot, tuple[ItemStatType, ...]], ...
    ] = ()
    minimum_enhance: int = 0
    excluded_item_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "right_side_main_stats",
            _right_side_main_stat_pairs(
                self.right_side_main_stats,
                "GearSearchFilters.right_side_main_stats",
            ),
        )
        object.__setattr__(
            self,
            "minimum_enhance",
            _integer(
                self.minimum_enhance,
                "GearSearchFilters.minimum_enhance",
                minimum=0,
                maximum=15,
            ),
        )
        object.__setattr__(
            self,
            "excluded_item_ids",
            _string_ids(
                self.excluded_item_ids,
                "GearSearchFilters.excluded_item_ids",
            ),
        )

    def allowed_main_stats_for(
        self,
        slot: GearSlot,
    ) -> tuple[ItemStatType, ...] | None:
        return dict(self.right_side_main_stats).get(GearSlot(slot))

    def to_dict(self) -> dict[str, object]:
        return {
            "rightSideMainStats": {
                slot.value: [stat.value for stat in stats]
                for slot, stats in self.right_side_main_stats
            },
            "minimumEnhance": self.minimum_enhance,
            "excludedItemIds": list(self.excluded_item_ids),
        }

    @classmethod
    def from_dict(cls, value: object) -> GearSearchFilters:
        data = _record_mapping(
            value,
            "GearSearchFilters",
            required=(),
            optional=(
                "rightSideMainStats",
                "minimumEnhance",
                "excludedItemIds",
            ),
        )
        return cls(
            right_side_main_stats=data.get("rightSideMainStats", ()),
            minimum_enhance=data.get("minimumEnhance", 0),
            excluded_item_ids=data.get("excludedItemIds", ()),
        )


@dataclass(frozen=True, slots=True)
class SetPattern:
    sets: tuple[GearSet, ...]

    def __post_init__(self) -> None:
        if isinstance(self.sets, str):
            raise DomainValidationError("SetPattern.sets must be a sequence of set stable IDs.")
        try:
            sets = tuple(_enum_member(value, GearSet, "SetPattern.sets[]") for value in self.sets)
        except TypeError:
            raise DomainValidationError("SetPattern.sets must be a sequence of set stable IDs.") from None

        if len(sets) > 3:
            raise DomainValidationError("SetPattern accepts at most three optional set selections.")
        piece_counts = tuple(SET_CATALOG[gear_set].pieces_required for gear_set in sets)
        if sum(piece_counts) > 6:
            raise DomainValidationError(
                "SetPattern selections cannot require more than the six equipped gear pieces."
            )

        for gear_set in set(sets):
            if sets.count(gear_set) > 1 and not SET_CATALOG[gear_set].stackable:
                raise DomainValidationError(f"SetPattern cannot repeat non-stackable set {gear_set.value}.")

        set_order = {member: index for index, member in enumerate(GearSet)}
        sets = tuple(
            sorted(sets, key=lambda gear_set: (-SET_CATALOG[gear_set].pieces_required, set_order[gear_set]))
        )
        object.__setattr__(self, "sets", sets)

    @property
    def kind(self) -> str:
        piece_counts = tuple(SET_CATALOG[gear_set].pieces_required for gear_set in self.sets)
        if piece_counts == (4, 2):
            return "4+2"
        if piece_counts == (2, 2, 2):
            return "2+2+2"
        return "flexible"

    def to_dict(self) -> dict[str, list[str]]:
        return {"sets": [gear_set.value for gear_set in self.sets]}

    @classmethod
    def from_dict(cls, value: object) -> SetPattern:
        data = _record_mapping(value, "SetPattern", required=("sets",))
        return cls(sets=tuple(data["sets"]))


@dataclass(frozen=True, slots=True)
class GearItem:
    item_id: str
    slot: GearSlot
    gear_set: GearSet
    main_stat: ItemStatType
    main_stat_value: Number
    substats: tuple[tuple[ItemStatType, Number], ...] = ()
    dense_id: int | None = None
    item_level: int = 85
    enhance: int = 0
    equipped_hero_id: str | None = None
    locked: bool = False

    def __post_init__(self) -> None:
        item_id = _stable_id(self.item_id, "GearItem.item_id")
        slot = _enum_member(self.slot, GearSlot, "GearItem.slot")
        gear_set = _enum_member(self.gear_set, GearSet, "GearItem.gear_set")
        main_stat = _enum_member(self.main_stat, ItemStatType, "GearItem.main_stat")
        main_value = _number(self.main_stat_value, "GearItem.main_stat_value")
        substats = _enum_number_pairs(self.substats, ItemStatType, "GearItem.substats")
        if main_value < 0 or any(value < 0 for _, value in substats):
            raise DomainValidationError("GearItem stat values must not be negative.")
        if len(substats) > 4:
            raise DomainValidationError("GearItem.substats may contain at most four stats.")
        if main_stat in dict(substats):
            raise DomainValidationError("GearItem.main_stat must not also appear in substats.")

        object.__setattr__(self, "item_id", item_id)
        object.__setattr__(self, "slot", slot)
        object.__setattr__(self, "gear_set", gear_set)
        object.__setattr__(self, "main_stat", main_stat)
        object.__setattr__(self, "main_stat_value", main_value)
        object.__setattr__(self, "substats", substats)
        object.__setattr__(self, "dense_id", _dense_id(self.dense_id, "GearItem.dense_id"))
        object.__setattr__(self, "item_level", _integer(self.item_level, "GearItem.item_level", minimum=1, maximum=100))
        object.__setattr__(self, "enhance", _integer(self.enhance, "GearItem.enhance", minimum=0, maximum=15))
        object.__setattr__(
            self,
            "equipped_hero_id",
            _stable_id(self.equipped_hero_id, "GearItem.equipped_hero_id", optional=True),
        )
        object.__setattr__(self, "locked", _boolean(self.locked, "GearItem.locked"))

    def to_dict(self) -> dict[str, object]:
        return {
            "itemId": self.item_id,
            "denseId": self.dense_id,
            "slot": self.slot.value,
            "set": self.gear_set.value,
            "itemLevel": self.item_level,
            "enhance": self.enhance,
            "mainStat": {"type": self.main_stat.value, "value": self.main_stat_value},
            "substats": [{"type": stat.value, "value": value} for stat, value in self.substats],
            "equippedHeroId": self.equipped_hero_id,
            "locked": self.locked,
        }

    @classmethod
    def from_dict(cls, value: object) -> GearItem:
        data = _record_mapping(
            value,
            "GearItem",
            required=("itemId", "slot", "set", "mainStat"),
            optional=("denseId", "itemLevel", "enhance", "substats", "equippedHeroId", "locked"),
        )
        main = _record_mapping(data["mainStat"], "GearItem.mainStat", required=("type", "value"))
        raw_substats = data.get("substats", ())
        if isinstance(raw_substats, str):
            raise DomainValidationError("GearItem.substats must be an array.")
        try:
            substats = tuple(
                (
                    _record_mapping(item, f"GearItem.substats[{index}]", required=("type", "value"))["type"],
                    item["value"],
                )
                for index, item in enumerate(raw_substats)
            )
        except TypeError:
            raise DomainValidationError("GearItem.substats must be an array.") from None
        return cls(
            item_id=data["itemId"],
            dense_id=data.get("denseId"),
            slot=data["slot"],
            gear_set=data["set"],
            item_level=data.get("itemLevel", 85),
            enhance=data.get("enhance", 0),
            main_stat=main["type"],
            main_stat_value=main["value"],
            substats=substats,
            equipped_hero_id=data.get("equippedHeroId"),
            locked=data.get("locked", False),
        )


@dataclass(frozen=True, slots=True)
class HeroBaseProfile:
    profile_id: str
    label: str
    level: int
    stars: int
    final_stats: tuple[tuple[FinalStat, Number], ...]
    dense_id: int | None = None

    def __post_init__(self) -> None:
        stats = _enum_number_pairs(self.final_stats, FinalStat, "HeroBaseProfile.final_stats", require_all=True)
        if any(value < 0 for _, value in stats):
            raise DomainValidationError("HeroBaseProfile.final_stats must not contain negative values.")
        object.__setattr__(self, "profile_id", _stable_id(self.profile_id, "HeroBaseProfile.profile_id"))
        object.__setattr__(self, "label", _stable_id(self.label, "HeroBaseProfile.label"))
        object.__setattr__(self, "level", _integer(self.level, "HeroBaseProfile.level", minimum=1, maximum=100))
        object.__setattr__(self, "stars", _integer(self.stars, "HeroBaseProfile.stars", minimum=1, maximum=6))
        object.__setattr__(self, "final_stats", stats)
        object.__setattr__(self, "dense_id", _dense_id(self.dense_id, "HeroBaseProfile.dense_id"))

    def to_dict(self) -> dict[str, object]:
        return {
            "profileId": self.profile_id,
            "denseId": self.dense_id,
            "label": self.label,
            "level": self.level,
            "stars": self.stars,
            "finalStats": {stat.value: value for stat, value in self.final_stats},
        }

    @classmethod
    def from_dict(cls, value: object) -> HeroBaseProfile:
        data = _record_mapping(
            value,
            "HeroBaseProfile",
            required=("profileId", "label", "level", "stars", "finalStats"),
            optional=("denseId",),
        )
        return cls(
            profile_id=data["profileId"],
            dense_id=data.get("denseId"),
            label=data["label"],
            level=data["level"],
            stars=data["stars"],
            final_stats=data["finalStats"],
        )


@dataclass(frozen=True, slots=True)
class HeroDefinition:
    hero_id: str
    name: str
    base_profiles: tuple[HeroBaseProfile, ...]
    dense_id: int | None = None

    def __post_init__(self) -> None:
        try:
            profiles = tuple(self.base_profiles)
        except TypeError:
            raise DomainValidationError("HeroDefinition.base_profiles must be a sequence.") from None
        if not profiles or not all(isinstance(profile, HeroBaseProfile) for profile in profiles):
            raise DomainValidationError("HeroDefinition.base_profiles must contain at least one HeroBaseProfile.")
        profile_ids = [profile.profile_id for profile in profiles]
        if len(profile_ids) != len(set(profile_ids)):
            raise DomainValidationError("HeroDefinition.base_profiles must have unique profile IDs.")
        dense_ids = [profile.dense_id for profile in profiles if profile.dense_id is not None]
        if len(dense_ids) != len(set(dense_ids)):
            raise DomainValidationError("HeroDefinition.base_profiles must have unique dense IDs when supplied.")
        object.__setattr__(self, "hero_id", _stable_id(self.hero_id, "HeroDefinition.hero_id"))
        object.__setattr__(self, "name", _stable_id(self.name, "HeroDefinition.name"))
        object.__setattr__(self, "base_profiles", tuple(sorted(profiles, key=lambda profile: profile.profile_id)))
        object.__setattr__(self, "dense_id", _dense_id(self.dense_id, "HeroDefinition.dense_id"))

    def to_dict(self) -> dict[str, object]:
        return {
            "heroId": self.hero_id,
            "denseId": self.dense_id,
            "name": self.name,
            "baseProfiles": [profile.to_dict() for profile in self.base_profiles],
        }

    @classmethod
    def from_dict(cls, value: object) -> HeroDefinition:
        data = _record_mapping(
            value,
            "HeroDefinition",
            required=("heroId", "name", "baseProfiles"),
            optional=("denseId",),
        )
        try:
            profiles = tuple(HeroBaseProfile.from_dict(profile) for profile in data["baseProfiles"])
        except TypeError:
            raise DomainValidationError("HeroDefinition.baseProfiles must be an array.") from None
        return cls(
            hero_id=data["heroId"],
            dense_id=data.get("denseId"),
            name=data["name"],
            base_profiles=profiles,
        )


@dataclass(frozen=True, slots=True)
class ArtifactDefinition:
    artifact_id: str
    name: str
    max_level: int
    base_attack: Number
    base_health: Number
    max_attack: Number
    max_health: Number
    dense_id: int | None = None

    def __post_init__(self) -> None:
        base_attack = _number(self.base_attack, "ArtifactDefinition.base_attack", minimum=0)
        base_health = _number(self.base_health, "ArtifactDefinition.base_health", minimum=0)
        max_attack = _number(self.max_attack, "ArtifactDefinition.max_attack", minimum=base_attack)
        max_health = _number(self.max_health, "ArtifactDefinition.max_health", minimum=base_health)
        object.__setattr__(self, "artifact_id", _stable_id(self.artifact_id, "ArtifactDefinition.artifact_id"))
        object.__setattr__(self, "name", _stable_id(self.name, "ArtifactDefinition.name"))
        object.__setattr__(self, "max_level", _integer(self.max_level, "ArtifactDefinition.max_level", minimum=0, maximum=100))
        object.__setattr__(self, "base_attack", base_attack)
        object.__setattr__(self, "base_health", base_health)
        object.__setattr__(self, "max_attack", max_attack)
        object.__setattr__(self, "max_health", max_health)
        object.__setattr__(self, "dense_id", _dense_id(self.dense_id, "ArtifactDefinition.dense_id"))

    def to_dict(self) -> dict[str, object]:
        return {
            "artifactId": self.artifact_id,
            "denseId": self.dense_id,
            "name": self.name,
            "maxLevel": self.max_level,
            "baseAttack": self.base_attack,
            "baseHealth": self.base_health,
            "maxAttack": self.max_attack,
            "maxHealth": self.max_health,
        }

    @classmethod
    def from_dict(cls, value: object) -> ArtifactDefinition:
        data = _record_mapping(
            value,
            "ArtifactDefinition",
            required=("artifactId", "name", "maxLevel", "baseAttack", "baseHealth", "maxAttack", "maxHealth"),
            optional=("denseId",),
        )
        return cls(
            artifact_id=data["artifactId"],
            dense_id=data.get("denseId"),
            name=data["name"],
            max_level=data["maxLevel"],
            base_attack=data["baseAttack"],
            base_health=data["baseHealth"],
            max_attack=data["maxAttack"],
            max_health=data["maxHealth"],
        )


@dataclass(frozen=True, slots=True)
class HeroModifierContribution:
    """One typed hero contribution in canonical source units.

    Percentage values are stored as ratios (``0.14`` means 14%). Flat values
    remain in their native stat units. The legacy final-stat projection exists
    only for compatibility with pre-v3 optimizer profiles.
    """

    stat_type: HeroModifierStatType
    value: Number

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "stat_type",
            _enum_member(
                self.stat_type,
                HeroModifierStatType,
                "HeroModifierContribution.stat_type",
            ),
        )
        object.__setattr__(
            self,
            "value",
            _number(self.value, "HeroModifierContribution.value", minimum=0),
        )

    @property
    def is_percentage(self) -> bool:
        return self.stat_type in {
            HeroModifierStatType.ATTACK_PERCENT,
            HeroModifierStatType.HEALTH_PERCENT,
            HeroModifierStatType.DEFENSE_PERCENT,
            HeroModifierStatType.CRITICAL_HIT_CHANCE_PERCENT,
            HeroModifierStatType.EFFECTIVENESS_PERCENT,
            HeroModifierStatType.EFFECT_RESISTANCE_PERCENT,
            HeroModifierStatType.FINAL_ATTACK_PERCENT,
            HeroModifierStatType.FINAL_HEALTH_PERCENT,
            HeroModifierStatType.FINAL_DEFENSE_PERCENT,
            HeroModifierStatType.DUAL_ATTACK_CHANCE_PERCENT,
        }

    @property
    def display_value(self) -> Number:
        return round(float(self.value) * 100, 10) if self.is_percentage else self.value

    def legacy_final_stat_bonus(self) -> tuple[tuple[FinalStat, Number], ...]:
        final_stat = {
            HeroModifierStatType.FLAT_ATTACK: FinalStat.ATTACK,
            HeroModifierStatType.ATTACK_PERCENT: FinalStat.ATTACK,
            HeroModifierStatType.FLAT_HEALTH: FinalStat.HEALTH,
            HeroModifierStatType.HEALTH_PERCENT: FinalStat.HEALTH,
            HeroModifierStatType.FLAT_DEFENSE: FinalStat.DEFENSE,
            HeroModifierStatType.DEFENSE_PERCENT: FinalStat.DEFENSE,
            HeroModifierStatType.SPEED: FinalStat.SPEED,
            HeroModifierStatType.CRITICAL_HIT_CHANCE_PERCENT: FinalStat.CRITICAL_HIT_CHANCE,
            HeroModifierStatType.EFFECTIVENESS_PERCENT: FinalStat.EFFECTIVENESS,
            HeroModifierStatType.EFFECT_RESISTANCE_PERCENT: FinalStat.EFFECT_RESISTANCE,
            HeroModifierStatType.FINAL_ATTACK_PERCENT: FinalStat.ATTACK,
            HeroModifierStatType.FINAL_HEALTH_PERCENT: FinalStat.HEALTH,
            HeroModifierStatType.FINAL_DEFENSE_PERCENT: FinalStat.DEFENSE,
        }.get(self.stat_type)
        return () if final_stat is None else ((final_stat, self.display_value),)

    def to_dict(self) -> dict[str, object]:
        return {"statType": self.stat_type.value, "value": self.value}

    @classmethod
    def from_dict(cls, value: object) -> HeroModifierContribution:
        data = _record_mapping(
            value,
            "HeroModifierContribution",
            required=("statType", "value"),
        )
        return cls(stat_type=data["statType"], value=data["value"])


def _hero_modifier_contributions(
    value: object,
    field: str,
    *,
    allow_dual_attack: bool,
) -> tuple[HeroModifierContribution, ...]:
    if isinstance(value, Mapping):
        raw_values: tuple[object, ...] = tuple(
            {"statType": key.value if isinstance(key, HeroModifierStatType) else key, "value": amount}
            for key, amount in value.items()
        )
    else:
        if isinstance(value, (str, bytes)):
            raise DomainValidationError(f"{field} must be a sequence or object.")
        try:
            raw_values = tuple(value)
        except TypeError:
            raise DomainValidationError(f"{field} must be a sequence or object.") from None

    order = {member: index for index, member in enumerate(HeroModifierStatType)}
    result: dict[HeroModifierStatType, HeroModifierContribution] = {}
    for index, raw in enumerate(raw_values):
        if isinstance(raw, HeroModifierContribution):
            contribution = raw
        elif isinstance(raw, Mapping):
            contribution = HeroModifierContribution.from_dict(raw)
        else:
            try:
                stat_type, amount = raw
            except (TypeError, ValueError):
                raise DomainValidationError(
                    f"{field}[{index}] must be a HeroModifierContribution or stat/value pair."
                ) from None
            contribution = HeroModifierContribution(stat_type, amount)
        if (
            not allow_dual_attack
            and contribution.stat_type is HeroModifierStatType.DUAL_ATTACK_CHANCE_PERCENT
        ):
            raise DomainValidationError(f"{field} does not support dual-attack chance.")
        if contribution.stat_type in result:
            raise DomainValidationError(
                f"{field} contains duplicate key {contribution.stat_type.value}."
            )
        result[contribution.stat_type] = contribution
    return tuple(sorted(result.values(), key=lambda item: order[item.stat_type]))


def custom_bonus_projection(
    contributions: Iterable[HeroModifierContribution],
) -> tuple[tuple[FinalStat, Number], ...]:
    """Aggregate typed custom values into the pre-v4 compatibility map."""

    totals: dict[FinalStat, Number] = {}
    for contribution in contributions:
        if not isinstance(contribution, HeroModifierContribution):
            raise DomainValidationError(
                "custom_bonus_projection requires HeroModifierContribution values."
            )
        for final_stat, amount in contribution.legacy_final_stat_bonus():
            totals[final_stat] = totals.get(final_stat, 0) + amount
    order = {member: index for index, member in enumerate(FinalStat)}
    return tuple(sorted(totals.items(), key=lambda pair: order[pair[0]]))


@dataclass(frozen=True, slots=True)
class SkillContext:
    """Persisted per-skill user choices; null overrides mean source defaults."""

    skill: SkillSlot
    target_defense: Number
    source_option_id: str | None = None
    hit_type: SkillHitType | None = None
    target_count_override: int | None = None
    penetration_override: Number | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "skill", _enum_member(self.skill, SkillSlot, "SkillContext.skill"))
        object.__setattr__(
            self,
            "target_defense",
            _number(self.target_defense, "SkillContext.target_defense", minimum=0),
        )
        object.__setattr__(
            self,
            "source_option_id",
            _stable_id(self.source_option_id, "SkillContext.source_option_id", optional=True),
        )
        if self.hit_type is not None:
            object.__setattr__(
                self,
                "hit_type",
                _enum_member(self.hit_type, SkillHitType, "SkillContext.hit_type"),
            )
        if self.target_count_override is not None:
            object.__setattr__(
                self,
                "target_count_override",
                _integer(
                    self.target_count_override,
                    "SkillContext.target_count_override",
                    minimum=1,
                ),
            )
        if self.penetration_override is not None:
            object.__setattr__(
                self,
                "penetration_override",
                _number(
                    self.penetration_override,
                    "SkillContext.penetration_override",
                    minimum=0,
                    maximum=1,
                ),
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "skill": self.skill.value,
            "sourceOptionId": self.source_option_id,
            "hitType": None if self.hit_type is None else self.hit_type.value,
            "targetCountOverride": self.target_count_override,
            "penetrationOverride": self.penetration_override,
            "targetDefense": self.target_defense,
        }

    @classmethod
    def from_dict(cls, value: object) -> SkillContext:
        data = _record_mapping(
            value,
            "SkillContext",
            required=("skill", "targetDefense"),
            optional=(
                "sourceOptionId",
                "hitType",
                "targetCountOverride",
                "penetrationOverride",
            ),
        )
        return cls(
            skill=data["skill"],
            source_option_id=data.get("sourceOptionId"),
            hit_type=data.get("hitType"),
            target_count_override=data.get("targetCountOverride"),
            penetration_override=data.get("penetrationOverride"),
            target_defense=data["targetDefense"],
        )


def _skill_contexts(value: object, target_defense: Number) -> tuple[SkillContext, ...]:
    if value in (None, (), []):
        return tuple(SkillContext(skill, target_defense) for skill in SkillSlot)
    if isinstance(value, Mapping):
        raw_values = []
        for raw_skill, raw_context in value.items():
            if not isinstance(raw_context, Mapping):
                raise DomainValidationError("OptimizationRequest.skill_contexts values must be objects.")
            payload = dict(raw_context)
            payload.setdefault("skill", raw_skill.value if isinstance(raw_skill, SkillSlot) else raw_skill)
            raw_values.append(payload)
    else:
        if isinstance(value, (str, bytes)):
            raise DomainValidationError("OptimizationRequest.skill_contexts must be a sequence or object.")
        try:
            raw_values = list(value)
        except TypeError:
            raise DomainValidationError("OptimizationRequest.skill_contexts must be a sequence or object.") from None
    result: dict[SkillSlot, SkillContext] = {}
    for index, raw_context in enumerate(raw_values):
        context = raw_context if isinstance(raw_context, SkillContext) else SkillContext.from_dict(raw_context)
        if context.skill in result:
            raise DomainValidationError(
                f"OptimizationRequest.skill_contexts contains duplicate {context.skill.value}."
            )
        result[context.skill] = context
    if set(result) != set(SkillSlot):
        missing = [skill.value for skill in SkillSlot if skill not in result]
        raise DomainValidationError(
            "OptimizationRequest.skill_contexts must contain S1, S2, and S3; missing: "
            + ", ".join(missing)
            + "."
        )
    return tuple(result[skill] for skill in SkillSlot)


@dataclass(frozen=True, slots=True)
class HeroModifiers:
    artifact_id: str | None = None
    artifact_level: int | None = None
    artifact_limit_breaks: int | None = None
    artifact_attack_override: Number | None = None
    artifact_health_override: Number | None = None
    artifact_defense_override: Number | None = None
    imprint_level: str | None = None
    imprint_bonuses: tuple[tuple[FinalStat, Number], ...] = ()
    imprint_contribution: HeroModifierContribution | None = None
    exclusive_equipment_id: str | None = None
    exclusive_equipment_bonuses: tuple[tuple[FinalStat, Number], ...] = ()
    exclusive_equipment_contribution: HeroModifierContribution | None = None
    exclusive_equipment_skill_option_id: str | None = None
    custom_bonuses: tuple[tuple[FinalStat, Number], ...] = ()
    custom_contributions: tuple[HeroModifierContribution, ...] = ()
    skill_options: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        artifact_id = _stable_id(self.artifact_id, "HeroModifiers.artifact_id", optional=True)
        artifact_level = None if self.artifact_level is None else _integer(
            self.artifact_level, "HeroModifiers.artifact_level", minimum=0, maximum=100
        )
        artifact_limit_breaks = None if self.artifact_limit_breaks is None else _integer(
            self.artifact_limit_breaks,
            "HeroModifiers.artifact_limit_breaks",
            minimum=0,
            maximum=5,
        )
        artifact_attack_override = _optional_nonnegative_number(
            self.artifact_attack_override,
            "HeroModifiers.artifact_attack_override",
        )
        artifact_health_override = _optional_nonnegative_number(
            self.artifact_health_override,
            "HeroModifiers.artifact_health_override",
        )
        artifact_defense_override = _optional_nonnegative_number(
            self.artifact_defense_override,
            "HeroModifiers.artifact_defense_override",
        )
        artifact_configuration = (
            artifact_level,
            artifact_limit_breaks,
            artifact_attack_override,
            artifact_health_override,
            artifact_defense_override,
        )
        if artifact_id is None and any(value is not None for value in artifact_configuration):
            raise DomainValidationError(
                "HeroModifiers artifact level, limit breaks, and stat overrides require artifact_id."
            )
        if artifact_id is not None and artifact_level is None:
            raise DomainValidationError(
                "HeroModifiers.artifact_id and artifact_level must be supplied together."
            )
        object.__setattr__(self, "artifact_id", artifact_id)
        object.__setattr__(self, "artifact_level", artifact_level)
        object.__setattr__(self, "artifact_limit_breaks", artifact_limit_breaks)
        object.__setattr__(self, "artifact_attack_override", artifact_attack_override)
        object.__setattr__(self, "artifact_health_override", artifact_health_override)
        object.__setattr__(self, "artifact_defense_override", artifact_defense_override)
        imprint_level = _stable_id(
            self.imprint_level,
            "HeroModifiers.imprint_level",
            optional=True,
        )
        imprint_bonuses = _enum_number_pairs(
            self.imprint_bonuses,
            FinalStat,
            "HeroModifiers.imprint_bonuses",
        )
        imprint_contribution = self.imprint_contribution
        if imprint_contribution is not None and not isinstance(
            imprint_contribution,
            HeroModifierContribution,
        ):
            imprint_contribution = HeroModifierContribution.from_dict(imprint_contribution)
        if imprint_level is None and (imprint_bonuses or imprint_contribution is not None):
            raise DomainValidationError(
                "HeroModifiers imprint bonuses and contribution require imprint_level."
            )
        if imprint_contribution is not None and (
            imprint_bonuses != imprint_contribution.legacy_final_stat_bonus()
        ):
            raise DomainValidationError(
                "HeroModifiers.imprint_bonuses must match imprint_contribution."
            )

        exclusive_equipment_id = _stable_id(
            self.exclusive_equipment_id,
            "HeroModifiers.exclusive_equipment_id",
            optional=True,
        )
        exclusive_equipment_bonuses = _enum_number_pairs(
            self.exclusive_equipment_bonuses,
            FinalStat,
            "HeroModifiers.exclusive_equipment_bonuses",
        )
        exclusive_equipment_contribution = self.exclusive_equipment_contribution
        if exclusive_equipment_contribution is not None and not isinstance(
            exclusive_equipment_contribution,
            HeroModifierContribution,
        ):
            exclusive_equipment_contribution = HeroModifierContribution.from_dict(
                exclusive_equipment_contribution
            )
        exclusive_equipment_skill_option_id = _stable_id(
            self.exclusive_equipment_skill_option_id,
            "HeroModifiers.exclusive_equipment_skill_option_id",
            optional=True,
        )
        if exclusive_equipment_id is None and (
            exclusive_equipment_bonuses
            or exclusive_equipment_contribution is not None
            or exclusive_equipment_skill_option_id is not None
        ):
            raise DomainValidationError(
                "HeroModifiers exclusive-equipment bonuses, contribution, and skill option require exclusive_equipment_id."
            )
        if exclusive_equipment_contribution is not None and (
            exclusive_equipment_bonuses
            != exclusive_equipment_contribution.legacy_final_stat_bonus()
        ):
            raise DomainValidationError(
                "HeroModifiers.exclusive_equipment_bonuses must match exclusive_equipment_contribution."
            )
        if (
            exclusive_equipment_skill_option_id is not None
            and not exclusive_equipment_skill_option_id.startswith(
                f"{exclusive_equipment_id}.skill-option."
            )
        ):
            raise DomainValidationError(
                "HeroModifiers.exclusive_equipment_skill_option_id must be scoped to exclusive_equipment_id."
            )

        object.__setattr__(self, "imprint_level", imprint_level)
        object.__setattr__(self, "imprint_bonuses", imprint_bonuses)
        object.__setattr__(self, "imprint_contribution", imprint_contribution)
        object.__setattr__(self, "exclusive_equipment_id", exclusive_equipment_id)
        object.__setattr__(
            self,
            "exclusive_equipment_bonuses",
            exclusive_equipment_bonuses,
        )
        object.__setattr__(
            self,
            "exclusive_equipment_contribution",
            exclusive_equipment_contribution,
        )
        object.__setattr__(
            self,
            "exclusive_equipment_skill_option_id",
            exclusive_equipment_skill_option_id,
        )
        custom_bonuses = _enum_number_pairs(
            self.custom_bonuses,
            FinalStat,
            "HeroModifiers.custom_bonuses",
        )
        custom_contributions = _hero_modifier_contributions(
            self.custom_contributions,
            "HeroModifiers.custom_contributions",
            allow_dual_attack=False,
        )
        if custom_contributions and custom_bonuses != custom_bonus_projection(custom_contributions):
            raise DomainValidationError(
                "HeroModifiers.custom_bonuses must match custom_contributions."
            )
        object.__setattr__(self, "custom_bonuses", custom_bonuses)
        object.__setattr__(self, "custom_contributions", custom_contributions)
        object.__setattr__(self, "skill_options", _string_ids(self.skill_options, "HeroModifiers.skill_options"))

    def to_dict(self) -> dict[str, object]:
        return {
            "artifactId": self.artifact_id,
            "artifactLevel": self.artifact_level,
            "artifactLimitBreaks": self.artifact_limit_breaks,
            "artifactAttackOverride": self.artifact_attack_override,
            "artifactHealthOverride": self.artifact_health_override,
            "artifactDefenseOverride": self.artifact_defense_override,
            "imprintLevel": self.imprint_level,
            "imprintBonuses": {stat.value: value for stat, value in self.imprint_bonuses},
            "imprintContribution": (
                None if self.imprint_contribution is None else self.imprint_contribution.to_dict()
            ),
            "exclusiveEquipmentId": self.exclusive_equipment_id,
            "exclusiveEquipmentBonuses": {
                stat.value: value for stat, value in self.exclusive_equipment_bonuses
            },
            "exclusiveEquipmentContribution": (
                None
                if self.exclusive_equipment_contribution is None
                else self.exclusive_equipment_contribution.to_dict()
            ),
            "exclusiveEquipmentSkillOptionId": self.exclusive_equipment_skill_option_id,
            "customBonuses": {stat.value: value for stat, value in self.custom_bonuses},
            "customContributions": [item.to_dict() for item in self.custom_contributions],
            "skillOptions": list(self.skill_options),
        }

    @classmethod
    def from_dict(cls, value: object) -> HeroModifiers:
        fields = (
            "artifactId",
            "artifactLevel",
            "artifactLimitBreaks",
            "artifactAttackOverride",
            "artifactHealthOverride",
            "artifactDefenseOverride",
            "imprintLevel",
            "imprintBonuses",
            "imprintContribution",
            "exclusiveEquipmentId",
            "exclusiveEquipmentBonuses",
            "exclusiveEquipmentContribution",
            "exclusiveEquipmentSkillOptionId",
            "customBonuses",
            "customContributions",
            "skillOptions",
        )
        data = _record_mapping(value, "HeroModifiers", required=(), optional=fields)
        return cls(
            artifact_id=data.get("artifactId"),
            artifact_level=data.get("artifactLevel"),
            artifact_limit_breaks=data.get("artifactLimitBreaks"),
            artifact_attack_override=data.get("artifactAttackOverride"),
            artifact_health_override=data.get("artifactHealthOverride"),
            artifact_defense_override=data.get("artifactDefenseOverride"),
            imprint_level=data.get("imprintLevel"),
            imprint_bonuses=data.get("imprintBonuses", ()),
            imprint_contribution=data.get("imprintContribution"),
            exclusive_equipment_id=data.get("exclusiveEquipmentId"),
            exclusive_equipment_bonuses=data.get("exclusiveEquipmentBonuses", ()),
            exclusive_equipment_contribution=data.get("exclusiveEquipmentContribution"),
            exclusive_equipment_skill_option_id=data.get("exclusiveEquipmentSkillOptionId"),
            custom_bonuses=data.get("customBonuses", ()),
            custom_contributions=data.get("customContributions", ()),
            skill_options=data.get("skillOptions", ()),
        )


@dataclass(frozen=True, slots=True)
class OptimizationRequest:
    request_id: str
    hero_id: str
    base_profile_id: str
    modifiers: HeroModifiers
    set_pattern: SetPattern
    stat_ranges: tuple[tuple[FinalStat, StatRange], ...] = ()
    stat_priorities: tuple[tuple[FinalStat, int], ...] = ()
    derived_metric_ranges: tuple[tuple[str, StatRange], ...] = ()
    include_equipped: bool = False
    gear_filters: GearSearchFilters = GearSearchFilters()
    near_set_tolerance: Number = 0
    maximum_replacement_distance: int = 0
    target_defense: Number = 1000
    skill_contexts: tuple[SkillContext, ...] = ()
    result_cap: int = MAX_RESULT_CAP
    execution_preference: ExecutionPreference = ExecutionPreference.AUTO
    item_projection_mode: ItemProjectionMode | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.modifiers, HeroModifiers):
            raise DomainValidationError("OptimizationRequest.modifiers must be HeroModifiers.")
        if not isinstance(self.set_pattern, SetPattern):
            raise DomainValidationError("OptimizationRequest.set_pattern must be SetPattern.")
        object.__setattr__(self, "request_id", _stable_id(self.request_id, "OptimizationRequest.request_id"))
        object.__setattr__(self, "hero_id", _stable_id(self.hero_id, "OptimizationRequest.hero_id"))
        object.__setattr__(
            self, "base_profile_id", _stable_id(self.base_profile_id, "OptimizationRequest.base_profile_id")
        )
        object.__setattr__(self, "stat_ranges", _stat_range_pairs(self.stat_ranges, "OptimizationRequest.stat_ranges"))
        object.__setattr__(
            self,
            "stat_priorities",
            _priority_pairs(self.stat_priorities, "OptimizationRequest.stat_priorities"),
        )
        object.__setattr__(
            self,
            "derived_metric_ranges",
            _metric_range_pairs(self.derived_metric_ranges, "OptimizationRequest.derived_metric_ranges"),
        )
        object.__setattr__(
            self, "include_equipped", _boolean(self.include_equipped, "OptimizationRequest.include_equipped")
        )
        if not isinstance(self.gear_filters, GearSearchFilters):
            raise DomainValidationError(
                "OptimizationRequest.gear_filters must be GearSearchFilters."
            )
        # Direct construction remains a compatibility seam for old internal
        # fixtures. Active desktop drafts and deserialization both migrate to
        # exact-only zero values before reaching the optimizer.
        object.__setattr__(
            self,
            "near_set_tolerance",
            _number(
                self.near_set_tolerance,
                "OptimizationRequest.near_set_tolerance",
                minimum=0,
                maximum=1,
            ),
        )
        object.__setattr__(
            self,
            "maximum_replacement_distance",
            _integer(
                self.maximum_replacement_distance,
                "OptimizationRequest.maximum_replacement_distance",
                minimum=0,
                maximum=2,
            ),
        )
        target_defense = _number(
            self.target_defense,
            "OptimizationRequest.target_defense",
            minimum=0,
        )
        object.__setattr__(self, "target_defense", target_defense)
        object.__setattr__(
            self,
            "skill_contexts",
            _skill_contexts(self.skill_contexts, target_defense),
        )
        object.__setattr__(
            self,
            "result_cap",
            _integer(self.result_cap, "OptimizationRequest.result_cap", minimum=1, maximum=MAX_RESULT_CAP),
        )
        object.__setattr__(
            self,
            "execution_preference",
            _enum_member(
                self.execution_preference,
                ExecutionPreference,
                "OptimizationRequest.execution_preference",
            ),
        )
        object.__setattr__(
            self,
            "item_projection_mode",
            None
            if self.item_projection_mode is None
            else _enum_member(
                self.item_projection_mode,
                ItemProjectionMode,
                "OptimizationRequest.item_projection_mode",
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "requestId": self.request_id,
            "heroId": self.hero_id,
            "baseProfileId": self.base_profile_id,
            "modifiers": self.modifiers.to_dict(),
            "setPattern": self.set_pattern.to_dict(),
            "statRanges": {stat.value: value.to_dict() for stat, value in self.stat_ranges},
            "statPriorities": {stat.value: value for stat, value in self.stat_priorities},
            "derivedMetricRanges": {metric: value.to_dict() for metric, value in self.derived_metric_ranges},
            "includeEquipped": self.include_equipped,
            "gearFilters": self.gear_filters.to_dict(),
            "nearSetTolerance": self.near_set_tolerance,
            "maximumReplacementDistance": self.maximum_replacement_distance,
            "targetDefense": self.target_defense,
            "skillContexts": [context.to_dict() for context in self.skill_contexts],
            "resultCap": self.result_cap,
            "executionPreference": self.execution_preference.value,
            "itemProjectionMode": (
                None if self.item_projection_mode is None else self.item_projection_mode.value
            ),
        }

    @classmethod
    def from_dict(cls, value: object) -> OptimizationRequest:
        data = _record_mapping(
            value,
            "OptimizationRequest",
            required=("requestId", "heroId", "baseProfileId", "modifiers", "setPattern"),
            optional=(
                "statRanges",
                "statPriorities",
                "derivedMetricRanges",
                "includeEquipped",
                "gearFilters",
                "nearSetTolerance",
                "maximumReplacementDistance",
                "targetDefense",
                "skillContexts",
                "resultCap",
                "executionPreference",
                "itemProjectionMode",
            ),
        )
        return cls(
            request_id=data["requestId"],
            hero_id=data["heroId"],
            base_profile_id=data["baseProfileId"],
            modifiers=HeroModifiers.from_dict(data["modifiers"]),
            set_pattern=SetPattern.from_dict(data["setPattern"]),
            stat_ranges=data.get("statRanges", ()),
            stat_priorities=data.get("statPriorities", ()),
            derived_metric_ranges=data.get("derivedMetricRanges", ()),
            include_equipped=data.get("includeEquipped", False),
            gear_filters=GearSearchFilters.from_dict(data.get("gearFilters", {})),
            near_set_tolerance=data.get("nearSetTolerance", 0),
            maximum_replacement_distance=data.get("maximumReplacementDistance", 0),
            target_defense=data.get("targetDefense", 1000),
            skill_contexts=data.get("skillContexts", ()),
            result_cap=data.get("resultCap", MAX_RESULT_CAP),
            execution_preference=data.get("executionPreference", ExecutionPreference.AUTO),
            item_projection_mode=data.get("itemProjectionMode"),
        )


@dataclass(frozen=True, slots=True)
class BuildMetrics:
    final_stats: tuple[tuple[FinalStat, Number], ...]
    derived_metrics: tuple[tuple[str, Number], ...] = ()
    priority_score: Number = 0

    def __post_init__(self) -> None:
        final_stats = _enum_number_pairs(self.final_stats, FinalStat, "BuildMetrics.final_stats", require_all=True)
        if any(value < 0 for _, value in final_stats):
            raise DomainValidationError("BuildMetrics.final_stats must not contain negative values.")
        object.__setattr__(self, "final_stats", final_stats)
        object.__setattr__(
            self, "derived_metrics", _string_number_pairs(self.derived_metrics, "BuildMetrics.derived_metrics")
        )
        object.__setattr__(
            self, "priority_score", _number(self.priority_score, "BuildMetrics.priority_score")
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "finalStats": {stat.value: value for stat, value in self.final_stats},
            "derivedMetrics": dict(self.derived_metrics),
            "priorityScore": self.priority_score,
        }

    @classmethod
    def from_dict(cls, value: object) -> BuildMetrics:
        data = _record_mapping(
            value,
            "BuildMetrics",
            required=("finalStats",),
            optional=("derivedMetrics", "priorityScore"),
        )
        return cls(
            final_stats=data["finalStats"],
            derived_metrics=data.get("derivedMetrics", ()),
            priority_score=data.get("priorityScore", 0),
        )


@dataclass(frozen=True, slots=True)
class SearchSummary:
    request_id: str
    evaluated_permutations: int
    exact_count: int
    one_away_count: int
    two_away_count: int
    duration_seconds: Number
    execution_preference: ExecutionPreference
    overflowed: bool = False
    cancelled: bool = False

    def __post_init__(self) -> None:
        counts = (
            _integer(self.exact_count, "SearchSummary.exact_count", minimum=0),
            _integer(self.one_away_count, "SearchSummary.one_away_count", minimum=0),
            _integer(self.two_away_count, "SearchSummary.two_away_count", minimum=0),
        )
        overflowed = _boolean(self.overflowed, "SearchSummary.overflowed")
        cancelled = _boolean(self.cancelled, "SearchSummary.cancelled")
        if overflowed and cancelled:
            raise DomainValidationError("SearchSummary cannot be both overflowed and cancelled.")
        if (overflowed or cancelled) and any(counts):
            raise DomainValidationError("Aborted SearchSummary records must not present partial result counts.")
        if sum(counts) > MAX_RESULT_CAP:
            raise DomainValidationError(f"SearchSummary result count must not exceed {MAX_RESULT_CAP}.")
        object.__setattr__(self, "request_id", _stable_id(self.request_id, "SearchSummary.request_id"))
        object.__setattr__(
            self,
            "evaluated_permutations",
            _integer(self.evaluated_permutations, "SearchSummary.evaluated_permutations", minimum=0),
        )
        object.__setattr__(self, "exact_count", counts[0])
        object.__setattr__(self, "one_away_count", counts[1])
        object.__setattr__(self, "two_away_count", counts[2])
        object.__setattr__(
            self,
            "duration_seconds",
            _number(self.duration_seconds, "SearchSummary.duration_seconds", minimum=0),
        )
        object.__setattr__(
            self,
            "execution_preference",
            _enum_member(self.execution_preference, ExecutionPreference, "SearchSummary.execution_preference"),
        )
        object.__setattr__(self, "overflowed", overflowed)
        object.__setattr__(self, "cancelled", cancelled)

    @property
    def result_count(self) -> int:
        return self.exact_count + self.one_away_count + self.two_away_count

    def to_dict(self) -> dict[str, object]:
        return {
            "requestId": self.request_id,
            "evaluatedPermutations": self.evaluated_permutations,
            "exactCount": self.exact_count,
            "oneAwayCount": self.one_away_count,
            "twoAwayCount": self.two_away_count,
            "resultCount": self.result_count,
            "durationSeconds": self.duration_seconds,
            "executionPreference": self.execution_preference.value,
            "overflowed": self.overflowed,
            "cancelled": self.cancelled,
        }

    @classmethod
    def from_dict(cls, value: object) -> SearchSummary:
        data = _record_mapping(
            value,
            "SearchSummary",
            required=(
                "requestId",
                "evaluatedPermutations",
                "exactCount",
                "oneAwayCount",
                "twoAwayCount",
                "durationSeconds",
                "executionPreference",
            ),
            optional=("resultCount", "overflowed", "cancelled"),
        )
        summary = cls(
            request_id=data["requestId"],
            evaluated_permutations=data["evaluatedPermutations"],
            exact_count=data["exactCount"],
            one_away_count=data["oneAwayCount"],
            two_away_count=data["twoAwayCount"],
            duration_seconds=data["durationSeconds"],
            execution_preference=data["executionPreference"],
            overflowed=data.get("overflowed", False),
            cancelled=data.get("cancelled", False),
        )
        if "resultCount" in data:
            result_count = _integer(data["resultCount"], "SearchSummary.resultCount", minimum=0)
            if result_count != summary.result_count:
                raise DomainValidationError("SearchSummary.resultCount does not match category counts.")
        return summary
