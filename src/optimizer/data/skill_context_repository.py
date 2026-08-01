"""Offline typed custom bonuses and per-skill calculation context."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Mapping

from src.optimizer.data.character_repository import (
    CharacterHeroRecord,
    CharacterNotFoundError,
    CharacterRepository,
    load_bundled_character_repository,
)
from src.optimizer.data.schema_common import FrozenJsonArray, FrozenJsonObject, required_text, thaw_json
from src.optimizer.domain import (
    HeroModifierContribution,
    HeroModifiers,
    HeroModifierStatType,
    OptimizationRequest,
    SkillContext,
    SkillHitType,
    SkillSlot,
    custom_bonus_projection,
)


SOURCE_SKILL_NAMES = MappingProxyType(
    {"S1": SkillSlot.S1, "S2": SkillSlot.S2, "S3": SkillSlot.S3}
)
SOURCE_SKILL_HIT_TYPES = MappingProxyType(
    {
        "crit": SkillHitType.CRITICAL,
        "crushing": SkillHitType.CRUSHING,
        "normal": SkillHitType.NORMAL,
        "miss": SkillHitType.MISS,
    }
)
SOURCE_SKILL_OPTION_ID_PREFIX = "skill-option.fribbels."

_DIRECT_SKILL_FIELDS = frozenset(
    {
        "hitTypes",
        "options",
        "rate",
        "pow",
        "targets",
        "penetration",
        "note",
        "selfHpScaling",
        "selfDefScaling",
        "selfSpdScaling",
        "extraSelfAtkScaling",
        "extraSelfDefScaling",
        "increasedValue",
        "cdmgIncrease",
    }
)
_OPTION_FIELDS = frozenset(
    {
        "name",
        "rate",
        "pow",
        "targets",
        "selfHpScaling",
        "selfAtkScaling",
        "selfDefScaling",
    }
)


class SkillContextRepositoryError(ValueError):
    """Actionable skill-source, identity, or context failure."""

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = required_text(code, "Skill context repository error code")
        self.path = required_text(path, "Skill context repository error path")
        self.message = required_text(message, "Skill context repository error message")
        super().__init__(f"{self.code} at {self.path}: {self.message}")


def _object(value: object, path: str) -> FrozenJsonObject:
    if not isinstance(value, FrozenJsonObject):
        raise SkillContextRepositoryError("invalid-source-object", path, "Expected an object.")
    return value


def _array(value: object, path: str) -> FrozenJsonArray:
    if not isinstance(value, FrozenJsonArray):
        raise SkillContextRepositoryError("invalid-source-array", path, "Expected an array.")
    return value


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SkillContextRepositoryError("invalid-source-text", path, "Expected a non-empty string.")
    return value.strip()


def _number(
    value: object,
    path: str,
    *,
    minimum: float = 0,
    maximum: float | None = None,
) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SkillContextRepositoryError("invalid-source-number", path, "Expected a finite number.")
    if value < minimum or (maximum is not None and value > maximum):
        maximum_text = "" if maximum is None else f" through {maximum}"
        raise SkillContextRepositoryError(
            "invalid-source-number",
            path,
            f"Expected a value from {minimum}{maximum_text}.",
        )
    return value


def _optional_number(
    source: FrozenJsonObject,
    field: str,
    path: str,
    *,
    minimum: float = 0,
    maximum: float | None = None,
) -> int | float | None:
    return (
        None
        if field not in source
        else _number(source[field], f"{path}.{field}", minimum=minimum, maximum=maximum)
    )


def _integer(value: object, path: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SkillContextRepositoryError(
            "invalid-source-integer",
            path,
            f"Expected an integer of at least {minimum}.",
        )
    return value


def _optional_integer(
    source: FrozenJsonObject,
    field: str,
    path: str,
    *,
    minimum: int,
) -> int | None:
    return None if field not in source else _integer(source[field], f"{path}.{field}", minimum=minimum)


def _unknown_fields(source: FrozenJsonObject, known: frozenset[str]) -> FrozenJsonObject:
    return FrozenJsonObject(tuple((key, value) for key, value in source.entries if key not in known))


def _skill_slot(value: object, path: str) -> SkillSlot:
    if isinstance(value, SkillSlot):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if normalized in SOURCE_SKILL_NAMES:
            return SOURCE_SKILL_NAMES[normalized]
        try:
            return SkillSlot(normalized)
        except ValueError:
            pass
    raise SkillContextRepositoryError(
        "invalid-skill-slot",
        path,
        "Expected S1, S2, S3, or a canonical skill slot ID.",
    )


def skill_option_stable_id(
    hero: CharacterHeroRecord,
    skill: SkillSlot,
    source_index: int,
    raw_source: FrozenJsonObject,
) -> str:
    if not isinstance(hero, CharacterHeroRecord):
        raise SkillContextRepositoryError("invalid-hero", "hero", "Expected CharacterHeroRecord.")
    slot = _skill_slot(skill, "skill")
    index = _integer(source_index, "sourceIndex", minimum=0)
    source = _object(raw_source, "rawSource")
    evidence = json.dumps(
        {
            "heroId": hero.hero_id,
            "skill": slot.value,
            "sourceIndex": index,
            "source": thaw_json(source),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = hashlib.sha256(evidence.encode("utf-8")).hexdigest()[:8]
    return (
        f"{SOURCE_SKILL_OPTION_ID_PREFIX}{hero.source_id}."
        f"{slot.name.casefold()}.{index}.{digest}"
    )


@dataclass(frozen=True, slots=True)
class SkillSourceOption:
    option_id: str
    hero_id: str
    skill: SkillSlot
    source_index: int
    name: str
    rate: int | float
    power: int | float
    target_count: int | None
    self_hp_scaling: int | float | None
    self_attack_scaling: int | float | None
    self_defense_scaling: int | float | None
    unknown_fields: FrozenJsonObject
    raw_source: FrozenJsonObject

    @property
    def is_damaging(self) -> bool:
        return self.rate > 0 and self.power > 0


@dataclass(frozen=True, slots=True)
class SkillRecord:
    skill_id: str
    hero_id: str
    skill: SkillSlot
    hit_types: tuple[SkillHitType, ...]
    rate: int | float | None
    power: int | float | None
    target_count: int | None
    penetration: int | float | None
    note: str | None
    self_hp_scaling: int | float | None
    self_defense_scaling: int | float | None
    self_speed_scaling: int | float | None
    extra_self_attack_scaling: int | float | None
    extra_self_defense_scaling: int | float | None
    increased_value: int | float | None
    critical_damage_increase: int | float | None
    options: tuple[SkillSourceOption, ...]
    unknown_fields: FrozenJsonObject
    raw_source: FrozenJsonObject

    @property
    def is_damaging(self) -> bool:
        return bool(self.hit_types)


@dataclass(frozen=True, slots=True)
class CustomBonusSelection:
    contributions: tuple[HeroModifierContribution, ...] = ()

    def __post_init__(self) -> None:
        raw = self.contributions
        if isinstance(raw, Mapping):
            values = tuple(HeroModifierContribution(kind, amount) for kind, amount in raw.items())
        else:
            try:
                values = tuple(
                    item
                    if isinstance(item, HeroModifierContribution)
                    else HeroModifierContribution.from_dict(item)
                    if isinstance(item, Mapping)
                    else HeroModifierContribution(*item)
                    for item in raw
                )
            except (TypeError, ValueError):
                raise SkillContextRepositoryError(
                    "invalid-custom-bonuses",
                    "customContributions",
                    "Expected typed contributions or stat/value pairs.",
                ) from None
        order = {member: index for index, member in enumerate(HeroModifierStatType)}
        result: dict[HeroModifierStatType, HeroModifierContribution] = {}
        for contribution in values:
            if contribution.stat_type is HeroModifierStatType.DUAL_ATTACK_CHANCE_PERCENT:
                raise SkillContextRepositoryError(
                    "unsupported-custom-bonus",
                    "customContributions",
                    "Custom dual-attack chance is outside the P02-T06 contract.",
                )
            if contribution.stat_type in result:
                raise SkillContextRepositoryError(
                    "duplicate-custom-bonus",
                    "customContributions",
                    f"Duplicate {contribution.stat_type.value}.",
                )
            result[contribution.stat_type] = contribution
        object.__setattr__(
            self,
            "contributions",
            tuple(sorted(result.values(), key=lambda item: order[item.stat_type])),
        )

    @property
    def legacy_projection(self):
        return custom_bonus_projection(self.contributions)

    def apply_to_modifiers(self, modifiers: HeroModifiers | None = None) -> HeroModifiers:
        base = HeroModifiers() if modifiers is None else modifiers
        if not isinstance(base, HeroModifiers):
            raise SkillContextRepositoryError("invalid-modifiers", "modifiers", "Expected HeroModifiers.")
        return replace(
            base,
            custom_bonuses=self.legacy_projection,
            custom_contributions=self.contributions,
        )


@dataclass(frozen=True, slots=True)
class SkillContextSelection:
    record: SkillRecord
    context: SkillContext
    source_option: SkillSourceOption | None = None

    @property
    def is_damaging(self) -> bool:
        return self.record.is_damaging and (
            self.source_option is None or self.source_option.is_damaging
        )

    @property
    def effective_target_count(self) -> int | None:
        if not self.is_damaging:
            return None
        if self.context.target_count_override is not None:
            return self.context.target_count_override
        if (
            self.source_option is not None
            and self.source_option.target_count is not None
            and self.source_option.target_count > 0
        ):
            return self.source_option.target_count
        return self.record.target_count

    @property
    def effective_penetration(self) -> int | float | None:
        if not self.is_damaging:
            return None
        if self.context.penetration_override is not None:
            return self.context.penetration_override
        return 0 if self.record.penetration is None else self.record.penetration

    @property
    def uses_target_count_override(self) -> bool:
        return self.context.target_count_override is not None

    @property
    def uses_penetration_override(self) -> bool:
        return self.context.penetration_override is not None


@dataclass(frozen=True, slots=True)
class HeroSkillContextSelection:
    hero: CharacterHeroRecord
    skills: tuple[SkillContextSelection, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.hero, CharacterHeroRecord):
            raise SkillContextRepositoryError("invalid-hero", "hero", "Expected CharacterHeroRecord.")
        expected = tuple(SkillSlot)
        actual = tuple(item.record.skill for item in self.skills)
        if actual != expected:
            raise SkillContextRepositoryError(
                "invalid-skill-selection",
                "skills",
                "Expected one ordered S1, S2, and S3 selection.",
            )
        if any(item.record.hero_id != self.hero.hero_id for item in self.skills):
            raise SkillContextRepositoryError(
                "skill-hero-mismatch",
                "skills",
                "Every skill selection must belong to the selected hero.",
            )

    @property
    def contexts(self) -> tuple[SkillContext, ...]:
        return tuple(item.context for item in self.skills)

    @property
    def source_option_ids(self) -> tuple[str, ...]:
        return tuple(
            item.source_option.option_id
            for item in self.skills
            if item.source_option is not None
        )

    def apply_to_request(self, request: OptimizationRequest) -> OptimizationRequest:
        if not isinstance(request, OptimizationRequest):
            raise SkillContextRepositoryError("invalid-request", "request", "Expected OptimizationRequest.")
        if request.hero_id != self.hero.hero_id:
            raise SkillContextRepositoryError(
                "request-hero-mismatch",
                "request.heroId",
                f"Request selects {request.hero_id!r}, not {self.hero.hero_id!r}.",
            )
        retained = tuple(
            option_id
            for option_id in request.modifiers.skill_options
            if not option_id.startswith(SOURCE_SKILL_OPTION_ID_PREFIX)
        )
        modifiers = replace(
            request.modifiers,
            skill_options=retained + self.source_option_ids,
        )
        return replace(request, modifiers=modifiers, skill_contexts=self.contexts)


class SkillContextRepository:
    """Validated immutable skill records and context resolution."""

    __slots__ = ("_character_repository", "_options_by_id", "_records", "_records_by_hero", "_sealed")

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("SkillContextRepository is immutable after construction.")
        object.__setattr__(self, name, value)

    def __init__(self, character_repository: CharacterRepository) -> None:
        if not isinstance(character_repository, CharacterRepository):
            raise SkillContextRepositoryError(
                "invalid-character-repository",
                "characterRepository",
                "Expected CharacterRepository.",
            )
        records = []
        by_hero: dict[str, tuple[SkillRecord, ...]] = {}
        options_by_id: dict[str, SkillSourceOption] = {}
        for hero in character_repository.heroes:
            hero_records = tuple(self._build_skill(hero, source_name) for source_name in SOURCE_SKILL_NAMES)
            by_hero[hero.hero_id] = hero_records
            records.extend(hero_records)
            for record in hero_records:
                for option in record.options:
                    folded = option.option_id.casefold()
                    if folded in options_by_id:
                        raise SkillContextRepositoryError(
                            "duplicate-skill-option-id",
                            option.option_id,
                            "Stable source option IDs collide case-insensitively.",
                        )
                    options_by_id[folded] = option
        self._character_repository = character_repository
        self._records = tuple(records)
        self._records_by_hero = MappingProxyType(by_hero)
        self._options_by_id = MappingProxyType(options_by_id)
        self._sealed = True

    @staticmethod
    def _build_option(
        hero: CharacterHeroRecord,
        skill: SkillSlot,
        source_index: int,
        raw_source: FrozenJsonObject,
        path: str,
    ) -> SkillSourceOption:
        source = _object(raw_source, path)
        return SkillSourceOption(
            option_id=skill_option_stable_id(hero, skill, source_index, source),
            hero_id=hero.hero_id,
            skill=skill,
            source_index=source_index,
            name=_text(source.get("name"), f"{path}.name"),
            rate=_number(source.get("rate"), f"{path}.rate"),
            power=_number(source.get("pow"), f"{path}.pow"),
            target_count=_optional_integer(source, "targets", path, minimum=0),
            self_hp_scaling=_optional_number(source, "selfHpScaling", path),
            self_attack_scaling=_optional_number(source, "selfAtkScaling", path),
            self_defense_scaling=_optional_number(source, "selfDefScaling", path),
            unknown_fields=_unknown_fields(source, _OPTION_FIELDS),
            raw_source=source,
        )

    @classmethod
    def _build_skill(cls, hero: CharacterHeroRecord, source_name: str) -> SkillRecord:
        skill = SOURCE_SKILL_NAMES[source_name]
        path = f"heroes[{hero.hero_id}].skills.{source_name}"
        skills = _object(hero.skills, f"heroes[{hero.hero_id}].skills")
        source = _object(skills.get(source_name), path)
        raw_hit_types = _array(source.get("hitTypes"), f"{path}.hitTypes")
        hit_types = []
        for index, raw_hit_type in enumerate(raw_hit_types):
            source_hit_type = _text(raw_hit_type, f"{path}.hitTypes[{index}]")
            hit_type = SOURCE_SKILL_HIT_TYPES.get(source_hit_type)
            if hit_type is None:
                raise SkillContextRepositoryError(
                    "unsupported-hit-type",
                    f"{path}.hitTypes[{index}]",
                    f"Unsupported source hit type {source_hit_type!r}.",
                )
            if hit_type in hit_types:
                raise SkillContextRepositoryError(
                    "duplicate-hit-type",
                    f"{path}.hitTypes",
                    f"Duplicate hit type {source_hit_type!r}.",
                )
            hit_types.append(hit_type)

        rate = _optional_number(source, "rate", path)
        power = _optional_number(source, "pow", path)
        if (rate is None) != (power is None):
            raise SkillContextRepositoryError(
                "incomplete-skill-scalar",
                path,
                "Source rate and pow must be present together.",
            )
        options_source = _array(source.get("options"), f"{path}.options")
        options = tuple(
            cls._build_option(
                hero,
                skill,
                index,
                _object(raw, f"{path}.options[{index}]"),
                f"{path}.options[{index}]",
            )
            for index, raw in enumerate(options_source)
        )
        note = None if "note" not in source else _text(source["note"], f"{path}.note")
        return SkillRecord(
            skill_id=f"skill.fribbels.{hero.source_id}.{skill.name.casefold()}",
            hero_id=hero.hero_id,
            skill=skill,
            hit_types=tuple(hit_types),
            rate=rate,
            power=power,
            target_count=_optional_integer(source, "targets", path, minimum=1),
            penetration=_optional_number(source, "penetration", path, maximum=1),
            note=note,
            self_hp_scaling=_optional_number(source, "selfHpScaling", path),
            self_defense_scaling=_optional_number(source, "selfDefScaling", path),
            self_speed_scaling=_optional_number(source, "selfSpdScaling", path),
            extra_self_attack_scaling=_optional_number(source, "extraSelfAtkScaling", path),
            extra_self_defense_scaling=_optional_number(source, "extraSelfDefScaling", path),
            increased_value=_optional_number(source, "increasedValue", path),
            critical_damage_increase=_optional_number(source, "cdmgIncrease", path),
            options=options,
            unknown_fields=_unknown_fields(source, _DIRECT_SKILL_FIELDS),
            raw_source=source,
        )

    @classmethod
    def from_bundled(cls) -> "SkillContextRepository":
        return cls(load_bundled_character_repository())

    @property
    def character_repository(self) -> CharacterRepository:
        return self._character_repository

    @property
    def records(self) -> tuple[SkillRecord, ...]:
        return self._records

    @property
    def source_options(self) -> tuple[SkillSourceOption, ...]:
        return tuple(sorted(self._options_by_id.values(), key=lambda item: item.option_id))

    def _hero(self, hero_id: object) -> CharacterHeroRecord:
        try:
            return self._character_repository.get(hero_id)
        except CharacterNotFoundError:
            raise SkillContextRepositoryError(
                "unknown-hero-id",
                "heroId",
                f"Hero ID was not found: {hero_id!r}.",
            ) from None

    def skills_for(self, hero_id: object) -> tuple[SkillRecord, ...]:
        hero = self._hero(hero_id)
        return self._records_by_hero[hero.hero_id]

    def get(self, hero_id: object, skill: object) -> SkillRecord:
        hero = self._hero(hero_id)
        slot = _skill_slot(skill, "skill")
        return next(record for record in self._records_by_hero[hero.hero_id] if record.skill is slot)

    def create_default_contexts(
        self,
        hero_id: object,
        *,
        target_defense: int | float,
    ) -> tuple[SkillContext, ...]:
        self._hero(hero_id)
        value = _number(target_defense, "targetDefense")
        return tuple(SkillContext(skill, value) for skill in SkillSlot)

    def select_context(self, hero_id: object, context: SkillContext) -> SkillContextSelection:
        if not isinstance(context, SkillContext):
            raise SkillContextRepositoryError("invalid-context", "skillContext", "Expected SkillContext.")
        record = self.get(hero_id, context.skill)
        option = None
        if context.source_option_id is not None:
            if context.source_option_id.startswith("exclusive-equipment."):
                raise SkillContextRepositoryError(
                    "ee-option-namespace",
                    "skillContext.sourceOptionId",
                    "EE skill choices are not damage-calculator source options.",
                )
            option = self._options_by_id.get(context.source_option_id.casefold())
            if option is None:
                raise SkillContextRepositoryError(
                    "unknown-source-option-id",
                    "skillContext.sourceOptionId",
                    f"Source option ID was not found: {context.source_option_id!r}.",
                )
            if option.hero_id != record.hero_id:
                raise SkillContextRepositoryError(
                    "source-option-hero-mismatch",
                    "skillContext.sourceOptionId",
                    f"Option belongs to {option.hero_id!r}, not {record.hero_id!r}.",
                )
            if option.skill is not record.skill:
                raise SkillContextRepositoryError(
                    "source-option-skill-mismatch",
                    "skillContext.sourceOptionId",
                    f"Option belongs to {option.skill.value}, not {record.skill.value}.",
                )
        is_damaging = record.is_damaging and (option is None or option.is_damaging)
        if not is_damaging and context.hit_type is not None:
            raise SkillContextRepositoryError(
                "hit-type-not-applicable",
                "skillContext.hitType",
                "A non-damaging skill or selected source option cannot use a hit type.",
            )
        if context.hit_type is not None and context.hit_type not in record.hit_types:
            raise SkillContextRepositoryError(
                "unsupported-context-hit-type",
                "skillContext.hitType",
                f"{context.hit_type.value} is not supported by {record.skill_id}.",
            )
        if not is_damaging and context.target_count_override is not None:
            raise SkillContextRepositoryError(
                "target-count-not-applicable",
                "skillContext.targetCountOverride",
                "A non-damaging skill or selected source option cannot use a target-count override.",
            )
        if not is_damaging and context.penetration_override is not None:
            raise SkillContextRepositoryError(
                "penetration-not-applicable",
                "skillContext.penetrationOverride",
                "A non-damaging skill or selected source option cannot use penetration.",
            )
        return SkillContextSelection(record, context, option)

    def select(
        self,
        hero_id: object,
        contexts: tuple[SkillContext, ...] | list[SkillContext],
    ) -> HeroSkillContextSelection:
        hero = self._hero(hero_id)
        try:
            values = tuple(contexts)
        except TypeError:
            raise SkillContextRepositoryError(
                "invalid-contexts",
                "skillContexts",
                "Expected S1, S2, and S3 contexts.",
            ) from None
        by_skill: dict[SkillSlot, SkillContext] = {}
        for context in values:
            if not isinstance(context, SkillContext):
                raise SkillContextRepositoryError("invalid-context", "skillContexts", "Expected SkillContext values.")
            if context.skill in by_skill:
                raise SkillContextRepositoryError(
                    "duplicate-skill-context",
                    "skillContexts",
                    f"Duplicate {context.skill.value}.",
                )
            by_skill[context.skill] = context
        if set(by_skill) != set(SkillSlot):
            raise SkillContextRepositoryError(
                "incomplete-skill-contexts",
                "skillContexts",
                "Expected exactly one S1, S2, and S3 context.",
            )
        return HeroSkillContextSelection(
            hero,
            tuple(self.select_context(hero.hero_id, by_skill[skill]) for skill in SkillSlot),
        )

    def validate_request(self, request: OptimizationRequest) -> None:
        if not isinstance(request, OptimizationRequest):
            raise SkillContextRepositoryError("invalid-request", "request", "Expected OptimizationRequest.")
        selection = self.select(request.hero_id, request.skill_contexts)
        selected_ids = set(selection.source_option_ids)
        persisted_source_ids = {
            option_id
            for option_id in request.modifiers.skill_options
            if option_id.startswith(SOURCE_SKILL_OPTION_ID_PREFIX)
        }
        if selected_ids != persisted_source_ids:
            raise SkillContextRepositoryError(
                "source-option-projection-drift",
                "modifiers.skillOptions",
                "Source option compatibility IDs do not match structured skill contexts.",
            )


def load_bundled_skill_context_repository() -> SkillContextRepository:
    return SkillContextRepository.from_bundled()
