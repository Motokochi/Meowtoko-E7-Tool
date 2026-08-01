"""Offline self-imprint and exclusive-equipment selection.

Only self-concentration data from the pinned Fribbels hero snapshot is used.
The snapshot carries EE stat metadata but no skill-enhancement descriptions or
effects, so those choices are represented as scoped opaque slots.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from src.optimizer.data.character_repository import (
    CharacterHeroRecord,
    CharacterNotFoundError,
    CharacterRepository,
    load_bundled_character_repository,
)
from src.optimizer.data.schema_common import FrozenJsonArray, FrozenJsonObject, required_text
from src.optimizer.domain import (
    HeroModifierContribution,
    HeroModifiers,
    HeroModifierStatType,
)


FRIBBELS_IMPRINT_EE_DIALOG_PATH = "app/js/lib/dialog.js"
FRIBBELS_IMPRINT_EE_DIALOG_GIT_BLOB_SHA1 = "d82799ef58fcbe1e8ae19d72a0d8dd256630835e"
FRIBBELS_IMPRINT_EE_APPLICATION_PATH = "app/js/lib/tabs/heroesTab.js"
FRIBBELS_IMPRINT_EE_APPLICATION_GIT_BLOB_SHA1 = "532f826eeb4cea1a1345c70f0878f9d4038717c2"

IMPRINT_GRADE_ORDER = ("D", "C", "B", "A", "S", "SS", "SSS")
EXCLUSIVE_EQUIPMENT_SKILL_OPTION_COUNT = 3

SOURCE_HERO_MODIFIER_STAT_TYPES = MappingProxyType(
    {
        "att": HeroModifierStatType.FLAT_ATTACK,
        "att_rate": HeroModifierStatType.ATTACK_PERCENT,
        "max_hp": HeroModifierStatType.FLAT_HEALTH,
        "max_hp_rate": HeroModifierStatType.HEALTH_PERCENT,
        "def": HeroModifierStatType.FLAT_DEFENSE,
        "def_rate": HeroModifierStatType.DEFENSE_PERCENT,
        "speed": HeroModifierStatType.SPEED,
        "cri": HeroModifierStatType.CRITICAL_HIT_CHANCE_PERCENT,
        "acc": HeroModifierStatType.EFFECTIVENESS_PERCENT,
        "res": HeroModifierStatType.EFFECT_RESISTANCE_PERCENT,
        "coop": HeroModifierStatType.DUAL_ATTACK_CHANCE_PERCENT,
    }
)


class HeroModifierRepositoryError(ValueError):
    """Actionable source, identity, or selection failure."""

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = required_text(code, "Hero modifier repository error code")
        self.path = required_text(path, "Hero modifier repository error path")
        self.message = required_text(message, "Hero modifier repository error message")
        super().__init__(f"{self.code} at {self.path}: {self.message}")


class ExclusiveEquipmentEffectDataState(StrEnum):
    UNAVAILABLE_IN_SNAPSHOT = "unavailable-in-snapshot"


def _source_object(value: object, path: str) -> FrozenJsonObject:
    if not isinstance(value, FrozenJsonObject):
        raise HeroModifierRepositoryError("invalid-source-object", path, "Expected an object.")
    return value


def _source_array(value: object, path: str) -> FrozenJsonArray:
    if not isinstance(value, FrozenJsonArray):
        raise HeroModifierRepositoryError("invalid-source-array", path, "Expected an array.")
    return value


def _source_text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HeroModifierRepositoryError("invalid-source-text", path, "Expected a non-empty string.")
    return value.strip()


def _stable_text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HeroModifierRepositoryError(
            "invalid-stable-id",
            path,
            "Expected a non-empty stable ID.",
        )
    return value.strip()


def _source_number(value: object, path: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise HeroModifierRepositoryError("invalid-source-number", path, "Expected a finite number.")
    if value < 0:
        raise HeroModifierRepositoryError("invalid-source-number", path, "Expected a non-negative number.")
    return value


def _integer(value: object, path: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise HeroModifierRepositoryError("invalid-integer", path, "Expected an integer.")
    if minimum is not None and value < minimum:
        raise HeroModifierRepositoryError("invalid-integer", path, f"Expected at least {minimum}.")
    if maximum is not None and value > maximum:
        raise HeroModifierRepositoryError("invalid-integer", path, f"Expected at most {maximum}.")
    return value


def _stat_type(source_type: object, path: str) -> HeroModifierStatType:
    source = _source_text(source_type, path)
    stat_type = SOURCE_HERO_MODIFIER_STAT_TYPES.get(source)
    if stat_type is None:
        raise HeroModifierRepositoryError(
            "unsupported-stat-type",
            path,
            f"Unsupported source hero modifier stat type {source!r}.",
        )
    return stat_type


def _js_round_positive(value: float) -> int:
    return math.floor(value + 0.5)


def _display_value(contribution: HeroModifierContribution) -> int | float:
    value = contribution.display_value
    return int(value) if float(value).is_integer() else value


def _canonical_roll_value(stat_type: HeroModifierStatType, display_value: int) -> int | float:
    if stat_type in {
        HeroModifierStatType.FLAT_ATTACK,
        HeroModifierStatType.FLAT_HEALTH,
        HeroModifierStatType.FLAT_DEFENSE,
        HeroModifierStatType.SPEED,
    }:
        return display_value
    return display_value / 100


def exclusive_equipment_stable_id(
    hero: CharacterHeroRecord,
    source_index: int,
    source_stat_type: str,
    source_base_value: int | float,
) -> str:
    """Derive identity from canonical hero ownership and immutable stat evidence."""

    if not isinstance(hero, CharacterHeroRecord):
        raise HeroModifierRepositoryError("invalid-hero", "hero", "Expected CharacterHeroRecord.")
    index = _integer(source_index, "sourceIndex", minimum=0)
    source_type = _source_text(source_stat_type, "sourceStatType")
    base_value = _source_number(source_base_value, "sourceBaseValue")
    evidence = json.dumps(
        {
            "heroId": hero.hero_id,
            "sourceIndex": index,
            "sourceStatType": source_type,
            "sourceBaseValue": base_value,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = hashlib.sha256(evidence.encode("utf-8")).hexdigest()[:8]
    return f"exclusive-equipment.fribbels.{hero.source_id}.{index}.{digest}"


@dataclass(frozen=True, slots=True)
class ImprintGradeOption:
    hero_id: str
    grade: str
    source_stat_type: str
    contribution: HeroModifierContribution

    def __post_init__(self) -> None:
        object.__setattr__(self, "hero_id", _stable_text(self.hero_id, "imprintOption.heroId"))
        grade = _source_text(self.grade, "imprintOption.grade").upper()
        if grade not in IMPRINT_GRADE_ORDER:
            raise HeroModifierRepositoryError(
                "unsupported-imprint-grade",
                "imprintOption.grade",
                f"Unsupported grade {grade!r}.",
            )
        object.__setattr__(self, "grade", grade)
        _stat_type(self.source_stat_type, "imprintOption.sourceStatType")
        if not isinstance(self.contribution, HeroModifierContribution):
            raise HeroModifierRepositoryError(
                "invalid-imprint-contribution",
                "imprintOption.contribution",
                "Expected HeroModifierContribution.",
            )
        if SOURCE_HERO_MODIFIER_STAT_TYPES[self.source_stat_type] is not self.contribution.stat_type:
            raise HeroModifierRepositoryError(
                "imprint-stat-type-drift",
                "imprintOption.contribution.statType",
                "Contribution type does not match the source stat type.",
            )

    @property
    def display_value(self) -> int | float:
        return _display_value(self.contribution)


@dataclass(frozen=True, slots=True)
class ImprintSelection:
    hero_id: str
    option: ImprintGradeOption | None = None

    def __post_init__(self) -> None:
        hero_id = _stable_text(self.hero_id, "imprint.heroId")
        if self.option is not None:
            if not isinstance(self.option, ImprintGradeOption):
                raise HeroModifierRepositoryError("invalid-imprint-option", "imprint", "Expected ImprintGradeOption.")
            if self.option.hero_id != hero_id:
                raise HeroModifierRepositoryError(
                    "imprint-hero-mismatch",
                    "imprint.heroId",
                    f"Imprint belongs to {self.option.hero_id!r}, not {hero_id!r}.",
                )
        object.__setattr__(self, "hero_id", hero_id)

    @property
    def grade(self) -> str | None:
        return None if self.option is None else self.option.grade

    @property
    def contribution(self) -> HeroModifierContribution | None:
        return None if self.option is None else self.option.contribution


@dataclass(frozen=True, slots=True)
class ExclusiveEquipmentSkillOption:
    option_id: str
    equipment_id: str
    ordinal: int
    effect_data_state: ExclusiveEquipmentEffectDataState = (
        ExclusiveEquipmentEffectDataState.UNAVAILABLE_IN_SNAPSHOT
    )

    def __post_init__(self) -> None:
        equipment_id = _stable_text(self.equipment_id, "skillOption.equipmentId")
        ordinal = _integer(
            self.ordinal,
            "EE skill option ordinal",
            minimum=1,
            maximum=EXCLUSIVE_EQUIPMENT_SKILL_OPTION_COUNT,
        )
        expected = f"{equipment_id}.skill-option.{ordinal}"
        if self.option_id != expected:
            raise HeroModifierRepositoryError(
                "invalid-skill-option-id",
                "skillOption.optionId",
                f"Expected scoped option ID {expected!r}.",
            )
        object.__setattr__(self, "equipment_id", equipment_id)
        object.__setattr__(self, "ordinal", ordinal)

    @property
    def description(self) -> None:
        return None

    @property
    def effect_value(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class ExclusiveEquipmentRecord:
    equipment_id: str
    hero_id: str
    source_index: int
    source_stat_type: str
    base_contribution: HeroModifierContribution
    roll_display_values: tuple[int, ...]
    skill_options: tuple[ExclusiveEquipmentSkillOption, ...]
    raw_source: FrozenJsonObject

    def __post_init__(self) -> None:
        equipment_id = _stable_text(self.equipment_id, "exclusiveEquipment.equipmentId")
        hero_id = _stable_text(self.hero_id, "exclusiveEquipment.heroId")
        source_index = _integer(self.source_index, "exclusiveEquipment.sourceIndex", minimum=0)
        expected_type = _stat_type(
            self.source_stat_type,
            "exclusiveEquipment.sourceStatType",
        )
        if not isinstance(self.base_contribution, HeroModifierContribution):
            raise HeroModifierRepositoryError(
                "invalid-ee-contribution",
                "exclusiveEquipment.baseContribution",
                "Expected HeroModifierContribution.",
            )
        if self.base_contribution.stat_type is not expected_type:
            raise HeroModifierRepositoryError(
                "ee-stat-type-drift",
                "exclusiveEquipment.baseContribution.statType",
                "Contribution type does not match the source stat type.",
            )
        if not isinstance(self.raw_source, FrozenJsonObject):
            raise HeroModifierRepositoryError(
                "invalid-ee-raw-source",
                "exclusiveEquipment.rawSource",
                "Expected an immutable source object.",
            )
        if not self.roll_display_values:
            raise HeroModifierRepositoryError("invalid-ee-rolls", equipment_id, "EE needs at least one stat roll.")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in self.roll_display_values):
            raise HeroModifierRepositoryError("invalid-ee-rolls", equipment_id, "EE rolls must be integers.")
        expected = tuple(range(self.roll_display_values[0], self.roll_display_values[-1] + 1))
        if self.roll_display_values != expected:
            raise HeroModifierRepositoryError("invalid-ee-rolls", equipment_id, "EE rolls must be contiguous.")
        if self.base_display_value != _display_value(self.base_contribution):
            raise HeroModifierRepositoryError(
                "ee-base-roll-drift",
                equipment_id,
                "The first EE roll does not match the converted source base contribution.",
            )
        if self.maximum_display_value != self.base_display_value * 2:
            raise HeroModifierRepositoryError(
                "ee-maximum-roll-drift",
                equipment_id,
                "The last EE roll must be exactly twice the converted source base.",
            )
        if len(self.skill_options) != EXCLUSIVE_EQUIPMENT_SKILL_OPTION_COUNT:
            raise HeroModifierRepositoryError(
                "invalid-ee-skill-options",
                equipment_id,
                f"Expected {EXCLUSIVE_EQUIPMENT_SKILL_OPTION_COUNT} scoped opaque skill slots.",
            )
        if any(option.equipment_id != equipment_id for option in self.skill_options):
            raise HeroModifierRepositoryError(
                "ee-skill-option-mismatch",
                equipment_id,
                "Every EE skill option must be scoped to this equipment.",
            )
        object.__setattr__(self, "equipment_id", equipment_id)
        object.__setattr__(self, "hero_id", hero_id)
        object.__setattr__(self, "source_index", source_index)

    @property
    def base_display_value(self) -> int:
        return self.roll_display_values[0]

    @property
    def maximum_display_value(self) -> int:
        return self.roll_display_values[-1]

    @property
    def effect_data_state(self) -> ExclusiveEquipmentEffectDataState:
        return ExclusiveEquipmentEffectDataState.UNAVAILABLE_IN_SNAPSHOT


@dataclass(frozen=True, slots=True)
class ExclusiveEquipmentSelection:
    hero_id: str
    equipment: ExclusiveEquipmentRecord | None = None
    stat_display_value: int | None = None
    skill_option: ExclusiveEquipmentSkillOption | None = None

    def __post_init__(self) -> None:
        hero_id = _stable_text(self.hero_id, "exclusiveEquipment.heroId")
        if self.equipment is None:
            if self.stat_display_value is not None or self.skill_option is not None:
                raise HeroModifierRepositoryError(
                    "ee-configuration-without-ee",
                    "exclusiveEquipment",
                    "EE stat value and skill option require selected equipment.",
                )
        else:
            if not isinstance(self.equipment, ExclusiveEquipmentRecord):
                raise HeroModifierRepositoryError("invalid-ee", "exclusiveEquipment", "Expected ExclusiveEquipmentRecord.")
            if self.equipment.hero_id != hero_id:
                raise HeroModifierRepositoryError(
                    "ee-hero-mismatch",
                    "exclusiveEquipment.heroId",
                    f"EE belongs to {self.equipment.hero_id!r}, not {hero_id!r}.",
                )
            value = _integer(self.stat_display_value, "exclusiveEquipment.statValue")
            if value not in self.equipment.roll_display_values:
                raise HeroModifierRepositoryError(
                    "invalid-ee-stat-value",
                    "exclusiveEquipment.statValue",
                    f"Expected one of {self.equipment.base_display_value} through "
                    f"{self.equipment.maximum_display_value}.",
                )
            object.__setattr__(self, "stat_display_value", value)
            if self.skill_option is not None:
                if not isinstance(self.skill_option, ExclusiveEquipmentSkillOption):
                    raise HeroModifierRepositoryError(
                        "invalid-ee-skill-option",
                        "exclusiveEquipment.skillOption",
                        "Expected ExclusiveEquipmentSkillOption.",
                    )
                if self.skill_option not in self.equipment.skill_options:
                    raise HeroModifierRepositoryError(
                        "ee-skill-option-mismatch",
                        "exclusiveEquipment.skillOption",
                        "Skill option does not belong to the selected EE.",
                    )
        object.__setattr__(self, "hero_id", hero_id)

    @property
    def equipment_id(self) -> str | None:
        return None if self.equipment is None else self.equipment.equipment_id

    @property
    def skill_option_id(self) -> str | None:
        return None if self.skill_option is None else self.skill_option.option_id

    @property
    def contribution(self) -> HeroModifierContribution | None:
        if self.equipment is None:
            return None
        return HeroModifierContribution(
            self.equipment.base_contribution.stat_type,
            _canonical_roll_value(
                self.equipment.base_contribution.stat_type,
                self.stat_display_value,
            ),
        )


@dataclass(frozen=True, slots=True)
class HeroModifierSelection:
    hero: CharacterHeroRecord
    imprint: ImprintSelection
    exclusive_equipment: ExclusiveEquipmentSelection

    def __post_init__(self) -> None:
        if not isinstance(self.hero, CharacterHeroRecord):
            raise HeroModifierRepositoryError("invalid-hero", "hero", "Expected CharacterHeroRecord.")
        for field, selection, expected_type in (
            ("imprint", self.imprint, ImprintSelection),
            ("exclusiveEquipment", self.exclusive_equipment, ExclusiveEquipmentSelection),
        ):
            if not isinstance(selection, expected_type):
                raise HeroModifierRepositoryError(
                    "invalid-selection",
                    field,
                    f"Expected {expected_type.__name__}.",
                )
            if selection.hero_id != self.hero.hero_id:
                raise HeroModifierRepositoryError(
                    "selection-hero-mismatch",
                    field,
                    f"Selection belongs to {selection.hero_id!r}, not {self.hero.hero_id!r}.",
                )

    def apply_to_modifiers(self, modifiers: HeroModifiers | None = None) -> HeroModifiers:
        base = HeroModifiers() if modifiers is None else modifiers
        if not isinstance(base, HeroModifiers):
            raise HeroModifierRepositoryError("invalid-modifiers", "modifiers", "Expected HeroModifiers.")
        imprint_contribution = self.imprint.contribution
        ee_contribution = self.exclusive_equipment.contribution
        return replace(
            base,
            imprint_level=self.imprint.grade,
            imprint_bonuses=(
                () if imprint_contribution is None else imprint_contribution.legacy_final_stat_bonus()
            ),
            imprint_contribution=imprint_contribution,
            exclusive_equipment_id=self.exclusive_equipment.equipment_id,
            exclusive_equipment_bonuses=(
                () if ee_contribution is None else ee_contribution.legacy_final_stat_bonus()
            ),
            exclusive_equipment_contribution=ee_contribution,
            exclusive_equipment_skill_option_id=self.exclusive_equipment.skill_option_id,
        )


class HeroModifierRepository:
    """Validated, immutable selections derived from rich character records."""

    __slots__ = ("_character_repository", "_ee_by_hero", "_ee_by_id", "_imprints_by_hero", "_sealed")

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("HeroModifierRepository is immutable after construction.")
        object.__setattr__(self, name, value)

    def __init__(self, character_repository: CharacterRepository) -> None:
        if not isinstance(character_repository, CharacterRepository):
            raise HeroModifierRepositoryError(
                "invalid-character-repository",
                "characterRepository",
                "Expected CharacterRepository.",
            )
        imprints_by_hero: dict[str, tuple[ImprintGradeOption, ...]] = {}
        ee_by_hero: dict[str, ExclusiveEquipmentRecord | None] = {}
        ee_by_id: dict[str, ExclusiveEquipmentRecord] = {}

        for hero in character_repository.heroes:
            imprints_by_hero[hero.hero_id] = self._build_imprints(hero)
            equipment_records = self._build_exclusive_equipment(hero)
            if len(equipment_records) > 1:
                raise HeroModifierRepositoryError(
                    "unsupported-multiple-ee-records",
                    f"heroes[{hero.hero_id}].ex_equip",
                    "The pinned selection contract supports at most one EE stat record per hero.",
                )
            equipment = equipment_records[0] if equipment_records else None
            ee_by_hero[hero.hero_id] = equipment
            if equipment is not None:
                folded_id = equipment.equipment_id.casefold()
                if folded_id in ee_by_id:
                    raise HeroModifierRepositoryError(
                        "duplicate-ee-id",
                        equipment.equipment_id,
                        "Stable EE IDs collide case-insensitively.",
                    )
                ee_by_id[folded_id] = equipment

        self._character_repository = character_repository
        self._imprints_by_hero = MappingProxyType(imprints_by_hero)
        self._ee_by_hero = MappingProxyType(ee_by_hero)
        self._ee_by_id = MappingProxyType(ee_by_id)
        self._sealed = True

    @staticmethod
    def _build_imprints(hero: CharacterHeroRecord) -> tuple[ImprintGradeOption, ...]:
        path = f"heroes[{hero.hero_id}].self_devotion"
        devotion = _source_object(hero.self_devotion, path)
        source_type = _source_text(devotion.get("type"), f"{path}.type")
        stat_type = _stat_type(source_type, f"{path}.type")
        grades = _source_object(devotion.get("grades"), f"{path}.grades")
        if not grades:
            raise HeroModifierRepositoryError("missing-imprint-grades", f"{path}.grades", "At least one grade is required.")
        unknown_grades = sorted(set(grades) - set(IMPRINT_GRADE_ORDER))
        if unknown_grades:
            raise HeroModifierRepositoryError(
                "unsupported-imprint-grade",
                f"{path}.grades",
                f"Unsupported grade(s): {', '.join(unknown_grades)}.",
            )
        return tuple(
            ImprintGradeOption(
                hero_id=hero.hero_id,
                grade=grade,
                source_stat_type=source_type,
                contribution=HeroModifierContribution(
                    stat_type,
                    _source_number(grades[grade], f"{path}.grades.{grade}"),
                ),
            )
            for grade in IMPRINT_GRADE_ORDER
            if grade in grades
        )

    @staticmethod
    def _build_exclusive_equipment(hero: CharacterHeroRecord) -> tuple[ExclusiveEquipmentRecord, ...]:
        equipment_array = _source_array(
            hero.exclusive_equipment,
            f"heroes[{hero.hero_id}].ex_equip",
        )
        result = []
        for index, raw_item in enumerate(equipment_array):
            path = f"heroes[{hero.hero_id}].ex_equip[{index}]"
            item = _source_object(raw_item, path)
            stat = _source_object(item.get("stat"), f"{path}.stat")
            source_type = _source_text(stat.get("type"), f"{path}.stat.type")
            stat_type = _stat_type(source_type, f"{path}.stat.type")
            source_value = _source_number(stat.get("value"), f"{path}.stat.value")
            contribution = HeroModifierContribution(stat_type, source_value)
            converted_base = (
                _js_round_positive(float(source_value) * 100)
                if contribution.is_percentage
                else source_value
            )
            if isinstance(converted_base, bool) or not float(converted_base).is_integer():
                raise HeroModifierRepositoryError(
                    "non-integral-ee-base",
                    f"{path}.stat.value",
                    "Pinned Fribbels EE roll generation requires an integral converted base value.",
                )
            base_display = int(converted_base)
            if base_display <= 0:
                raise HeroModifierRepositoryError(
                    "invalid-ee-base",
                    f"{path}.stat.value",
                    "Pinned Fribbels EE roll generation requires a positive base value.",
                )
            equipment_id = exclusive_equipment_stable_id(
                hero,
                index,
                source_type,
                source_value,
            )
            skill_options = tuple(
                ExclusiveEquipmentSkillOption(
                    option_id=f"{equipment_id}.skill-option.{ordinal}",
                    equipment_id=equipment_id,
                    ordinal=ordinal,
                )
                for ordinal in range(1, EXCLUSIVE_EQUIPMENT_SKILL_OPTION_COUNT + 1)
            )
            result.append(
                ExclusiveEquipmentRecord(
                    equipment_id=equipment_id,
                    hero_id=hero.hero_id,
                    source_index=index,
                    source_stat_type=source_type,
                    base_contribution=contribution,
                    roll_display_values=tuple(range(base_display, base_display * 2 + 1)),
                    skill_options=skill_options,
                    raw_source=item,
                )
            )
        return tuple(result)

    @classmethod
    def from_bundled(cls) -> "HeroModifierRepository":
        return cls(load_bundled_character_repository())

    @property
    def character_repository(self) -> CharacterRepository:
        return self._character_repository

    @property
    def exclusive_equipment(self) -> tuple[ExclusiveEquipmentRecord, ...]:
        return tuple(sorted(self._ee_by_id.values(), key=lambda item: item.equipment_id))

    def _hero(self, hero_id: object) -> CharacterHeroRecord:
        try:
            return self._character_repository.get(hero_id)
        except CharacterNotFoundError:
            raise HeroModifierRepositoryError(
                "unknown-hero-id",
                "heroId",
                f"Hero ID was not found: {hero_id!r}.",
            ) from None

    def imprint_options_for(self, hero_id: object) -> tuple[ImprintGradeOption, ...]:
        hero = self._hero(hero_id)
        return self._imprints_by_hero[hero.hero_id]

    def exclusive_equipment_for(self, hero_id: object) -> ExclusiveEquipmentRecord | None:
        hero = self._hero(hero_id)
        return self._ee_by_hero[hero.hero_id]

    def select_imprint(self, hero_id: object, grade: object = None) -> ImprintSelection:
        hero = self._hero(hero_id)
        if grade is None:
            return ImprintSelection(hero.hero_id)
        if not isinstance(grade, str) or not grade.strip():
            raise HeroModifierRepositoryError("invalid-imprint-grade", "imprint.grade", "Expected a non-empty grade or null.")
        grade = grade.strip().upper()
        option = next((item for item in self._imprints_by_hero[hero.hero_id] if item.grade == grade), None)
        if option is None:
            available = ", ".join(item.grade for item in self._imprints_by_hero[hero.hero_id])
            raise HeroModifierRepositoryError(
                "unknown-imprint-grade",
                "imprint.grade",
                f"Grade {grade!r} is unavailable for {hero.hero_id!r}; expected one of {available}.",
            )
        return ImprintSelection(hero.hero_id, option)

    def select_exclusive_equipment(
        self,
        hero_id: object,
        equipment_id: object = None,
        *,
        stat_display_value: object = None,
        skill_option_id: object = None,
    ) -> ExclusiveEquipmentSelection:
        hero = self._hero(hero_id)
        if equipment_id is None:
            return ExclusiveEquipmentSelection(
                hero.hero_id,
                stat_display_value=stat_display_value,
                skill_option=skill_option_id,
            )
        if not isinstance(equipment_id, str) or not equipment_id.strip():
            raise HeroModifierRepositoryError("invalid-ee-id", "exclusiveEquipmentId", "Expected a non-empty stable ID or null.")
        equipment = self._ee_by_id.get(equipment_id.strip().casefold())
        if equipment is None:
            raise HeroModifierRepositoryError(
                "unknown-ee-id",
                "exclusiveEquipmentId",
                f"EE ID was not found: {equipment_id!r}.",
            )
        if equipment.hero_id != hero.hero_id:
            raise HeroModifierRepositoryError(
                "ee-hero-mismatch",
                "exclusiveEquipmentId",
                f"EE belongs to {equipment.hero_id!r}, not {hero.hero_id!r}.",
            )
        skill_option = None
        if skill_option_id is not None:
            if not isinstance(skill_option_id, str) or not skill_option_id.strip():
                raise HeroModifierRepositoryError(
                    "invalid-ee-skill-option-id",
                    "exclusiveEquipmentSkillOptionId",
                    "Expected a non-empty stable ID or null.",
                )
            skill_option = next(
                (item for item in equipment.skill_options if item.option_id == skill_option_id.strip()),
                None,
            )
            if skill_option is None:
                raise HeroModifierRepositoryError(
                    "unknown-ee-skill-option-id",
                    "exclusiveEquipmentSkillOptionId",
                    "Skill option is not one of the selected EE's scoped opaque slots.",
                )
        return ExclusiveEquipmentSelection(
            hero.hero_id,
            equipment,
            stat_display_value,
            skill_option,
        )

    def select(
        self,
        hero_id: object,
        *,
        imprint_grade: object = None,
        equipment_id: object = None,
        ee_stat_display_value: object = None,
        ee_skill_option_id: object = None,
    ) -> HeroModifierSelection:
        hero = self._hero(hero_id)
        return HeroModifierSelection(
            hero=hero,
            imprint=self.select_imprint(hero.hero_id, imprint_grade),
            exclusive_equipment=self.select_exclusive_equipment(
                hero.hero_id,
                equipment_id,
                stat_display_value=ee_stat_display_value,
                skill_option_id=ee_skill_option_id,
            ),
        )

    def select_from_modifiers(self, hero_id: object, modifiers: HeroModifiers) -> HeroModifierSelection:
        if not isinstance(modifiers, HeroModifiers):
            raise HeroModifierRepositoryError("invalid-modifiers", "modifiers", "Expected HeroModifiers.")
        imprint = self.select_imprint(hero_id, modifiers.imprint_level)
        if modifiers.imprint_contribution is not None and modifiers.imprint_contribution != imprint.contribution:
            raise HeroModifierRepositoryError(
                "imprint-contribution-drift",
                "modifiers.imprintContribution",
                "Persisted contribution does not match the selected hero and grade.",
            )
        if imprint.contribution is not None and modifiers.imprint_bonuses not in (
            (),
            imprint.contribution.legacy_final_stat_bonus(),
        ):
            raise HeroModifierRepositoryError(
                "imprint-bonus-drift",
                "modifiers.imprintBonuses",
                "Persisted legacy bonus does not match the selected hero and grade.",
            )

        if modifiers.exclusive_equipment_id is None:
            ee = self.select_exclusive_equipment(hero_id)
        else:
            contribution = modifiers.exclusive_equipment_contribution
            if contribution is None:
                raise HeroModifierRepositoryError(
                    "missing-ee-contribution",
                    "modifiers.exclusiveEquipmentContribution",
                    "Selected EE needs its typed stat contribution.",
                )
            stat_display_value = _display_value(contribution)
            if not float(stat_display_value).is_integer():
                raise HeroModifierRepositoryError(
                    "invalid-ee-stat-value",
                    "modifiers.exclusiveEquipmentContribution.value",
                    "EE stat contribution must convert to an integral selectable roll.",
                )
            ee = self.select_exclusive_equipment(
                hero_id,
                modifiers.exclusive_equipment_id,
                stat_display_value=int(stat_display_value),
                skill_option_id=modifiers.exclusive_equipment_skill_option_id,
            )
            if ee.contribution != contribution:
                raise HeroModifierRepositoryError(
                    "ee-contribution-drift",
                    "modifiers.exclusiveEquipmentContribution",
                    "Persisted contribution kind/value does not match the selected EE roll.",
                )
            if modifiers.exclusive_equipment_bonuses != contribution.legacy_final_stat_bonus():
                raise HeroModifierRepositoryError(
                    "ee-bonus-drift",
                    "modifiers.exclusiveEquipmentBonuses",
                    "Persisted legacy bonus does not match the selected EE roll.",
                )
        return HeroModifierSelection(self._hero(hero_id), imprint, ee)

    def validate_modifiers(self, hero_id: object, modifiers: HeroModifiers) -> None:
        self.select_from_modifiers(hero_id, modifiers)


def load_bundled_hero_modifier_repository() -> HeroModifierRepository:
    return HeroModifierRepository.from_bundled()
