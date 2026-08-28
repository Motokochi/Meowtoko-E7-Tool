"""Catalog-backed hero configuration and atomic per-hero desktop drafts."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.desktop import BACKEND_VERSION
from src.optimizer.data import (
    OPTIMIZER_PROFILE_CURRENT_VERSION,
    ArtifactRepository,
    ArtifactRepositoryError,
    ArtifactStatOverrides,
    CharacterProfileSelectionError,
    CharacterProfileSelector,
    CustomBonusSelection,
    HeroModifierRepository,
    HeroModifierRepositoryError,
    OptimizerConfiguration,
    OptimizerProfileDocument,
    SchemaValidationError,
    SkillContextRepository,
    SkillContextRepositoryError,
    SourceMetadata,
    load_bundled_character_catalog,
    load_bundled_character_repository,
    load_bundled_runtime_character_catalog,
    load_bundled_character_source_snapshot,
    load_optimizer_profile,
)
from src.optimizer.data.character_repository import CharacterNotFoundError, normalize_character_search_text
from src.optimizer.domain import (
    ALLOWED_MAIN_STATS_BY_SLOT,
    DISPLAY_SET_ORDER,
    FRIBBELS_ITEM_STAT_ORDER,
    RIGHT_SIDE_GEAR_SLOTS,
    SET_CATALOG,
    DomainValidationError,
    FinalStat,
    GearSearchFilters,
    GearSet,
    GearSlot,
    HeroModifierContribution,
    HeroModifierStatType,
    ItemProjectionMode,
    ItemStatType,
    OptimizationRequest,
    SetPattern,
    SkillContext,
    SkillSlot,
    StatRange,
    gear_slot_display_name,
    item_stat_display_name,
)


MAX_DESKTOP_CHARACTER_RESULTS = 50
MAX_DESKTOP_ARTIFACT_RESULTS = 50
DESKTOP_PROFILE_DIRECTORY = "optimizer_profiles"
DESKTOP_PROFILE_SOURCE_NAME = "Meowtoko E7 Tool desktop optimizer"
_VALIDATION_REQUEST_ID = "request.desktop-draft-validation"
_DEFAULT_TARGET_DEFENSE = 1000


class OptimizerProfileServiceError(RuntimeError):
    """Stable structured error that does not expose filesystem paths or raw records."""

    def __init__(
        self,
        category: str,
        *,
        code: str,
        message: str,
        field_path: str | None = None,
        read_only: bool = False,
    ) -> None:
        self.category = category
        self.code = code
        self.field_path = field_path
        self.read_only = read_only
        super().__init__(message)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Desktop optimizer clock must return a timezone-aware value.")
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _exact_object(
    value: object,
    *,
    path: str,
    required: frozenset[str],
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise OptimizerProfileServiceError(
            "validation", code="invalid-object", field_path=path, message=f"{path} must be an object."
        )
    keys = set(value)
    if keys != required:
        missing = sorted(required - keys)
        extra = sorted(keys - required)
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if extra:
            detail.append("unexpected " + ", ".join(extra))
        raise OptimizerProfileServiceError(
            "validation",
            code="invalid-fields",
            field_path=path,
            message=f"{path} has invalid fields ({'; '.join(detail)}).",
        )
    return value


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OptimizerProfileServiceError(
            "validation", code="required-text", field_path=path, message=f"{path} is required."
        )
    return value.strip()


def _nullable_text(value: object, path: str) -> str | None:
    if value is None:
        return None
    return _text(value, path)


def _boolean(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise OptimizerProfileServiceError(
            "validation", code="invalid-boolean", field_path=path, message=f"{path} must be true or false."
        )
    return value


def _number(
    value: object,
    path: str,
    *,
    minimum: float = 0,
    maximum: float | None = None,
    integer: bool = False,
    nullable: bool = False,
) -> int | float | None:
    if nullable and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise OptimizerProfileServiceError(
            "validation", code="invalid-number", field_path=path, message=f"{path} must be a finite number."
        )
    if integer:
        if not isinstance(value, int) and not (isinstance(value, float) and value.is_integer()):
            raise OptimizerProfileServiceError(
                "validation", code="invalid-integer", field_path=path, message=f"{path} must be an integer."
            )
        value = int(value)
    if value < minimum or (maximum is not None and value > maximum):
        suffix = f" through {maximum:g}" if maximum is not None else " or greater"
        raise OptimizerProfileServiceError(
            "validation",
            code="number-out-of-range",
            field_path=path,
            message=f"{path} must be {minimum:g}{suffix}.",
        )
    return value


_CUSTOM_BONUSES: tuple[tuple[str, str, HeroModifierStatType, bool], ...] = (
    ("flatAttack", "Attack", HeroModifierStatType.FLAT_ATTACK, False),
    ("attackPercent", "Attack %", HeroModifierStatType.ATTACK_PERCENT, True),
    ("flatHealth", "Health", HeroModifierStatType.FLAT_HEALTH, False),
    ("healthPercent", "Health %", HeroModifierStatType.HEALTH_PERCENT, True),
    ("flatDefense", "Defense", HeroModifierStatType.FLAT_DEFENSE, False),
    ("defensePercent", "Defense %", HeroModifierStatType.DEFENSE_PERCENT, True),
    ("speed", "Speed", HeroModifierStatType.SPEED, False),
    ("criticalHitChancePercent", "Critical hit chance %", HeroModifierStatType.CRITICAL_HIT_CHANCE_PERCENT, True),
    ("effectivenessPercent", "Effectiveness %", HeroModifierStatType.EFFECTIVENESS_PERCENT, True),
    ("effectResistancePercent", "Effect resistance %", HeroModifierStatType.EFFECT_RESISTANCE_PERCENT, True),
    ("finalAttackPercent", "Final Attack %", HeroModifierStatType.FINAL_ATTACK_PERCENT, True),
    ("finalHealthPercent", "Final Health %", HeroModifierStatType.FINAL_HEALTH_PERCENT, True),
    ("finalDefensePercent", "Final Defense %", HeroModifierStatType.FINAL_DEFENSE_PERCENT, True),
)
_CUSTOM_KEYS = frozenset(item[0] for item in _CUSTOM_BONUSES)

_PRIMARY_STATS: tuple[tuple[str, FinalStat, bool], ...] = (
    ("attack", FinalStat.ATTACK, False),
    ("health", FinalStat.HEALTH, False),
    ("defense", FinalStat.DEFENSE, False),
    ("speed", FinalStat.SPEED, False),
    ("criticalHitChancePercent", FinalStat.CRITICAL_HIT_CHANCE, True),
    ("criticalHitDamagePercent", FinalStat.CRITICAL_HIT_DAMAGE, True),
    ("effectivenessPercent", FinalStat.EFFECTIVENESS, True),
    ("effectResistancePercent", FinalStat.EFFECT_RESISTANCE, True),
)
_PRIMARY_KEYS = frozenset(item[0] for item in _PRIMARY_STATS)

_RIGHT_SIDE_SLOT_KEYS = frozenset(slot.value for slot in RIGHT_SIDE_GEAR_SLOTS)


def _hero_summary(hero: object) -> dict[str, Any]:
    return {
        "heroId": hero.hero_id,
        "name": hero.name,
        "element": hero.element,
        "role": hero.role,
        "rarity": hero.rarity,
        "portraitUrl": hero.portraits.thumbnail,
    }


def _artifact_summary(artifact: object) -> dict[str, Any]:
    return {
        "artifactId": artifact.artifact_id,
        "name": artifact.name,
        "role": artifact.role,
        "rarity": artifact.rarity,
        "maxLevel": artifact.max_level,
    }


class OptimizerProfileService:
    """Own desktop catalog projection, validation, and per-hero profile storage."""

    def __init__(
        self,
        user_data_dir: str | Path,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.user_data_dir = Path(user_data_dir)
        self.profile_directory = self.user_data_dir / DESKTOP_PROFILE_DIRECTORY
        self.clock = clock
        self.catalog = load_bundled_runtime_character_catalog()
        self.characters = load_bundled_character_repository()
        self.profiles = CharacterProfileSelector(self.characters)
        self.artifacts = ArtifactRepository(
            load_bundled_character_catalog(),
            load_bundled_character_source_snapshot(),
        )
        self.hero_modifiers = HeroModifierRepository(self.characters)
        self.skill_contexts = SkillContextRepository(self.characters)

    def search_heroes(self, query: str, limit: int = 20) -> dict[str, Any]:
        if not isinstance(query, str):
            raise OptimizerProfileServiceError(
                "validation", code="invalid-query", field_path="query", message="Hero search query must be text."
            )
        _number(limit, "limit", minimum=1, maximum=MAX_DESKTOP_CHARACTER_RESULTS, integer=True)
        matches = self.characters.search(query, limit=limit)
        return {"query": query, "results": [_hero_summary(hero) for hero in matches]}

    def search_artifacts(self, query: str, limit: int = 20) -> dict[str, Any]:
        if not isinstance(query, str):
            raise OptimizerProfileServiceError(
                "validation", code="invalid-query", field_path="query", message="Artifact search query must be text."
            )
        _number(limit, "limit", minimum=1, maximum=MAX_DESKTOP_ARTIFACT_RESULTS, integer=True)
        normalized = normalize_character_search_text(query)
        matches = [
            artifact
            for artifact in self.artifacts.artifacts
            if not normalized
            or normalized in normalize_character_search_text(artifact.name)
            or normalized in normalize_character_search_text(artifact.source_code)
            or normalized in normalize_character_search_text(artifact.artifact_id)
        ][:limit]
        return {"query": query, "results": [_artifact_summary(artifact) for artifact in matches]}

    def get_hero_details(self, hero_id: str) -> dict[str, Any]:
        try:
            hero = self.characters.get(hero_id)
            profiles = self.profiles.profiles_for(hero.hero_id)
            default_profile = self.profiles.create_default_selection(hero.hero_id).profile
            imprint_options = self.hero_modifiers.imprint_options_for(hero.hero_id)
            equipment = self.hero_modifiers.exclusive_equipment_for(hero.hero_id)
            skills = self.skill_contexts.skills_for(hero.hero_id)
        except (CharacterNotFoundError, ValueError, CharacterProfileSelectionError, HeroModifierRepositoryError, SkillContextRepositoryError) as error:
            raise OptimizerProfileServiceError(
                "catalog", code=getattr(error, "code", "unknown-hero"), field_path="heroId", message="The selected hero is unavailable."
            ) from error
        return {
            "hero": {**_hero_summary(hero), "zodiac": hero.zodiac},
            "defaultProfileId": default_profile.profile_id,
            "profiles": [
                {
                    "profileId": profile.profile_id,
                    "label": profile.label,
                    "level": profile.level,
                    "stars": profile.stars,
                    "finalStats": {stat.value: value for stat, value in profile.final_stats},
                }
                for profile in profiles
            ],
            "imprints": [
                {
                    "grade": option.grade,
                    "statType": option.contribution.stat_type.value,
                    "displayValue": option.display_value,
                }
                for option in imprint_options
            ],
            "exclusiveEquipment": None if equipment is None else {
                "equipmentId": equipment.equipment_id,
                "statType": equipment.base_contribution.stat_type.value,
                "rolls": list(equipment.roll_display_values),
                "skillOptions": [
                    {
                        "optionId": option.option_id,
                        "label": f"Skill slot {option.ordinal}",
                        "effectDataState": option.effect_data_state.value,
                    }
                    for option in equipment.skill_options
                ],
            },
            "customBonusFields": [
                {"key": key, "label": label, "percentage": percentage}
                for key, label, _kind, percentage in _CUSTOM_BONUSES
            ],
            "sets": [
                {
                    "setId": gear_set.value,
                    "label": SET_CATALOG[gear_set].display_name,
                    "piecesRequired": SET_CATALOG[gear_set].pieces_required,
                    "stackable": SET_CATALOG[gear_set].stackable,
                }
                for gear_set in DISPLAY_SET_ORDER
            ],
            "rightSideMainStats": [
                {
                    "slotId": slot.value,
                    "label": gear_slot_display_name(slot),
                    "options": [
                        {"statId": stat.value, "label": item_stat_display_name(stat)}
                        for stat in FRIBBELS_ITEM_STAT_ORDER
                        if stat in ALLOWED_MAIN_STATS_BY_SLOT[slot]
                    ],
                }
                for slot in RIGHT_SIDE_GEAR_SLOTS
            ],
            "skills": [
                {
                    "skill": skill.skill.value,
                    "label": skill.skill.name,
                    "isDamaging": skill.is_damaging,
                    "hitTypes": [hit.value for hit in skill.hit_types],
                    "sourceOptions": [
                        {"optionId": option.option_id, "label": option.name, "isDamaging": option.is_damaging}
                        for option in skill.options
                    ],
                    "sourceTargetCount": skill.target_count,
                    "sourcePenetrationPercent": None if skill.penetration is None else skill.penetration * 100,
                    "note": skill.note,
                }
                for skill in skills
            ],
        }

    def _profile_path(self, hero_id: str) -> Path:
        digest = hashlib.sha256(hero_id.encode("utf-8")).hexdigest()
        return self.profile_directory / f"{digest}.json"

    def _default_request(self, hero_id: str) -> OptimizationRequest:
        selection = self.profiles.create_default_selection(hero_id)
        return OptimizationRequest(
            request_id=_VALIDATION_REQUEST_ID,
            hero_id=selection.hero_id,
            base_profile_id=selection.profile_id,
            modifiers=self.artifacts.select_none().to_artifact_only_modifiers(),
            set_pattern=SetPattern(()),
            item_projection_mode=ItemProjectionMode.CURRENT,
            target_defense=_DEFAULT_TARGET_DEFENSE,
            skill_contexts=self.skill_contexts.create_default_contexts(
                selection.hero_id,
                target_defense=_DEFAULT_TARGET_DEFENSE,
            ),
        )

    def _recover_legacy_modifier_context(
        self,
        hero_id: str,
        request: OptimizationRequest,
        source_version: int | None,
    ) -> OptimizationRequest:
        """Catalog-validate v1/v2 modifier projections without rewriting their file."""

        if source_version not in (1, 2):
            return request
        modifiers = request.modifiers
        imprint = self.hero_modifiers.select_imprint(hero_id, modifiers.imprint_level)
        if imprint.contribution is not None and modifiers.imprint_bonuses not in (
            (),
            imprint.contribution.legacy_final_stat_bonus(),
        ):
            raise HeroModifierRepositoryError(
                "legacy-imprint-bonus-drift",
                "modifiers.imprintBonuses",
                "Legacy imprint bonuses do not match the selected hero and grade.",
            )

        if modifiers.exclusive_equipment_id is None:
            selection = self.hero_modifiers.select(
                hero_id,
                imprint_grade=modifiers.imprint_level,
            )
        else:
            equipment = self.get_hero_details(hero_id)["exclusiveEquipment"]
            if equipment is None or equipment["equipmentId"] != modifiers.exclusive_equipment_id:
                raise HeroModifierRepositoryError(
                    "legacy-ee-unavailable",
                    "modifiers.exclusiveEquipmentId",
                    "Legacy EE is not available for the selected hero.",
                )
            matches = []
            for display_value in equipment["rolls"]:
                candidate = self.hero_modifiers.select(
                    hero_id,
                    imprint_grade=modifiers.imprint_level,
                    equipment_id=modifiers.exclusive_equipment_id,
                    ee_stat_display_value=display_value,
                )
                contribution = candidate.exclusive_equipment.contribution
                if contribution is not None and (
                    contribution.legacy_final_stat_bonus()
                    == modifiers.exclusive_equipment_bonuses
                ):
                    matches.append(candidate)
            if len(matches) != 1:
                raise HeroModifierRepositoryError(
                    "legacy-ee-bonus-unresolved",
                    "modifiers.exclusiveEquipmentBonuses",
                    "Legacy EE bonuses do not identify exactly one catalog roll.",
                )
            selection = matches[0]

        recovered = selection.apply_to_modifiers(modifiers)
        return replace(request, modifiers=recovered)

    def _read_profile(self, hero_id: str) -> tuple[OptimizerProfileDocument | None, int | None]:
        path = self._profile_path(hero_id)
        if not path.exists():
            return None, None
        try:
            raw_text = path.read_text(encoding="utf-8")
            raw = json.loads(raw_text)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise OptimizerProfileServiceError(
                "storage",
                code="profile-malformed",
                message="This hero draft is malformed and has been left unchanged.",
                read_only=True,
            ) from error
        version = raw.get("schemaVersion") if isinstance(raw, Mapping) else None
        if isinstance(version, int) and version > OPTIMIZER_PROFILE_CURRENT_VERSION:
            raise OptimizerProfileServiceError(
                "storage",
                code="profile-future-version",
                message="This hero draft was saved by a newer application version and is read-only here.",
                read_only=True,
            )
        try:
            profile = load_optimizer_profile(raw, character_catalog=self.catalog)
            request = profile.create_request(_VALIDATION_REQUEST_ID)
            persisted_modifiers = request.modifiers
            request = self._recover_legacy_modifier_context(hero_id, request, version)
            if request.modifiers != persisted_modifiers:
                profile = replace(
                    profile,
                    configuration=OptimizerConfiguration.from_request(request),
                )
            self.profiles.select(hero_id, request.base_profile_id)
            if request.hero_id != hero_id:
                raise SchemaValidationError("Saved profile belongs to another hero.")
            self.artifacts.select_from_modifiers(request.modifiers)
            self.hero_modifiers.select_from_modifiers(hero_id, request.modifiers)
            self.skill_contexts.validate_request(request)
        except (SchemaValidationError, ArtifactRepositoryError, CharacterProfileSelectionError, HeroModifierRepositoryError, SkillContextRepositoryError) as error:
            raise OptimizerProfileServiceError(
                "storage",
                code="profile-invalid",
                message="This hero draft failed catalog validation and has been left unchanged.",
                read_only=True,
            ) from error
        return profile, version if isinstance(version, int) else None

    def _request_to_draft(self, request: OptimizationRequest) -> dict[str, Any]:
        custom = {key: None for key, _label, _kind, _percentage in _CUSTOM_BONUSES}
        by_kind = {kind: (key, percentage) for key, _label, kind, percentage in _CUSTOM_BONUSES}
        for contribution in request.modifiers.custom_contributions:
            key, percentage = by_kind[contribution.stat_type]
            custom[key] = contribution.display_value if percentage else contribution.value
        ee_value = None
        if request.modifiers.exclusive_equipment_contribution is not None:
            ee_value = request.modifiers.exclusive_equipment_contribution.display_value
        ranges = dict(request.stat_ranges)
        priorities = dict(request.stat_priorities)
        primary_stats = {}
        for key, stat, _percentage in _PRIMARY_STATS:
            requested = ranges.get(stat)
            minimum = None if requested is None else requested.minimum
            maximum = None if requested is None else requested.maximum
            primary_stats[key] = {
                "minimum": minimum,
                "maximum": maximum,
                "priority": priorities.get(stat, 0),
            }
        return {
            "heroId": request.hero_id,
            "baseProfileId": request.base_profile_id,
            "artifact": {
                "artifactId": request.modifiers.artifact_id,
                "level": request.modifiers.artifact_level,
                "attackOverride": request.modifiers.artifact_attack_override,
                "healthOverride": request.modifiers.artifact_health_override,
                "defenseOverride": request.modifiers.artifact_defense_override,
            },
            "imprintGrade": request.modifiers.imprint_level,
            "exclusiveEquipment": {
                "equipmentId": request.modifiers.exclusive_equipment_id,
                "statValue": ee_value,
                "skillOptionId": request.modifiers.exclusive_equipment_skill_option_id,
            },
            "customBonuses": custom,
            "primaryStats": primary_stats,
            "setPattern": {
                "kind": request.set_pattern.kind,
                "sets": (
                    [gear_set.value for gear_set in request.set_pattern.sets]
                    if request.set_pattern.kind != "flexible"
                    else [
                        *[gear_set.value for gear_set in request.set_pattern.sets],
                        *([None] * (3 - len(request.set_pattern.sets))),
                    ]
                ),
            },
            "includeEquipped": request.include_equipped,
            "maximumReplacementDistance": 0,
            "nearSetTolerancePercent": 0,
            "itemProjectionMode": (
                ItemProjectionMode.CURRENT.value
                if request.item_projection_mode is None
                else request.item_projection_mode.value
            ),
            "gearFilters": {
                # The desktop optimizer intentionally considers completed gear
                # only. Legacy profiles are normalized in memory and rewritten
                # only when the user explicitly saves them.
                "minimumEnhance": 15,
                "rightSideMainStats": {
                    slot.value: [stat.value for stat in (request.gear_filters.allowed_main_stats_for(slot) or ())]
                    for slot in RIGHT_SIDE_GEAR_SLOTS
                },
            },
            "skills": [
                {
                    "skill": context.skill.value,
                    "sourceOptionId": context.source_option_id,
                    "hitType": None if context.hit_type is None else context.hit_type.value,
                    "targetCountOverride": context.target_count_override,
                    "penetrationPercent": None if context.penetration_override is None else context.penetration_override * 100,
                    "targetDefense": context.target_defense,
                }
                for context in request.skill_contexts
            ],
        }

    def _envelope(self, profile: OptimizerProfileDocument | None, request: OptimizationRequest) -> dict[str, Any]:
        artifact = None
        if request.modifiers.artifact_id is not None:
            artifact = _artifact_summary(self.artifacts.get(request.modifiers.artifact_id))
        return {
            "state": "default" if profile is None else "saved",
            "savedAt": None if profile is None else profile.saved_at,
            "schemaVersion": OPTIMIZER_PROFILE_CURRENT_VERSION,
            "draft": self._request_to_draft(request),
            "selectedArtifact": artifact,
        }

    def load_draft(self, hero_id: str) -> dict[str, Any]:
        hero_id = _text(hero_id, "heroId")
        try:
            self.characters.get(hero_id)
            profile, _version = self._read_profile(hero_id)
            request = self._default_request(hero_id) if profile is None else profile.create_request(_VALIDATION_REQUEST_ID)
        except OptimizerProfileServiceError:
            raise
        except (CharacterNotFoundError, ValueError) as error:
            raise OptimizerProfileServiceError(
                "catalog", code="unknown-hero", field_path="heroId", message="The selected hero is unavailable."
            ) from error
        return self._envelope(profile, request)

    def _draft_to_request(self, value: object) -> OptimizationRequest:
        draft = _exact_object(
            value,
            path="draft",
            required=frozenset({
                "heroId", "baseProfileId", "artifact", "imprintGrade",
                "exclusiveEquipment", "customBonuses", "primaryStats",
                "setPattern", "includeEquipped",
                "maximumReplacementDistance", "nearSetTolerancePercent",
                "itemProjectionMode", "gearFilters", "skills",
            }),
        )
        hero_id = _text(draft["heroId"], "draft.heroId")
        profile_id = _text(draft["baseProfileId"], "draft.baseProfileId")
        try:
            self.profiles.select(hero_id, profile_id)
        except CharacterProfileSelectionError as error:
            raise OptimizerProfileServiceError(
                "validation", code=error.code, field_path="draft.baseProfileId", message=error.message
            ) from error

        artifact_value = _exact_object(
            draft["artifact"],
            path="draft.artifact",
            required=frozenset({"artifactId", "level", "attackOverride", "healthOverride", "defenseOverride"}),
        )
        artifact_id = _nullable_text(artifact_value["artifactId"], "draft.artifact.artifactId")
        if artifact_id is None:
            if any(artifact_value[key] is not None for key in ("level", "attackOverride", "healthOverride", "defenseOverride")):
                raise OptimizerProfileServiceError(
                    "validation", code="no-artifact-configuration", field_path="draft.artifact", message="Artifact values require an artifact selection."
                )
            artifact_selection = self.artifacts.select_none()
        else:
            level = _number(artifact_value["level"], "draft.artifact.level", minimum=0, maximum=30, integer=True)
            overrides = ArtifactStatOverrides(
                attack=_number(artifact_value["attackOverride"], "draft.artifact.attackOverride", nullable=True),
                health=_number(artifact_value["healthOverride"], "draft.artifact.healthOverride", nullable=True),
                defense=_number(artifact_value["defenseOverride"], "draft.artifact.defenseOverride", nullable=True),
            )
            try:
                artifact_selection = self.artifacts.select(artifact_id, level=level, overrides=overrides)
            except ArtifactRepositoryError as error:
                raise OptimizerProfileServiceError(
                    "validation", code=error.code, field_path="draft.artifact.artifactId", message=error.message
                ) from error
        modifiers = artifact_selection.to_artifact_only_modifiers()

        imprint_grade = _nullable_text(draft["imprintGrade"], "draft.imprintGrade")
        ee_value = _exact_object(
            draft["exclusiveEquipment"],
            path="draft.exclusiveEquipment",
            required=frozenset({"equipmentId", "statValue", "skillOptionId"}),
        )
        ee_id = _nullable_text(ee_value["equipmentId"], "draft.exclusiveEquipment.equipmentId")
        ee_stat = _number(ee_value["statValue"], "draft.exclusiveEquipment.statValue", integer=True, nullable=True)
        ee_skill = _nullable_text(ee_value["skillOptionId"], "draft.exclusiveEquipment.skillOptionId")
        try:
            hero_selection = self.hero_modifiers.select(
                hero_id,
                imprint_grade=imprint_grade,
                equipment_id=ee_id,
                ee_stat_display_value=ee_stat,
                ee_skill_option_id=ee_skill,
            )
            modifiers = hero_selection.apply_to_modifiers(modifiers)
        except HeroModifierRepositoryError as error:
            raise OptimizerProfileServiceError(
                "validation", code=error.code, field_path=f"draft.{error.path}", message=error.message
            ) from error

        custom_value = _exact_object(
            draft["customBonuses"], path="draft.customBonuses", required=_CUSTOM_KEYS
        )
        contributions = []
        for key, _label, kind, percentage in _CUSTOM_BONUSES:
            display = _number(custom_value[key], f"draft.customBonuses.{key}", nullable=True)
            if display is not None:
                contributions.append(HeroModifierContribution(kind, display / 100 if percentage else display))
        try:
            modifiers = CustomBonusSelection(tuple(contributions)).apply_to_modifiers(modifiers)
        except SkillContextRepositoryError as error:
            raise OptimizerProfileServiceError(
                "validation", code=error.code, field_path="draft.customBonuses", message=error.message
            ) from error

        primary_value = _exact_object(
            draft["primaryStats"], path="draft.primaryStats", required=_PRIMARY_KEYS
        )
        stat_ranges = []
        stat_priorities = []
        for key, stat, _percentage in _PRIMARY_STATS:
            requested = _exact_object(
                primary_value[key],
                path=f"draft.primaryStats.{key}",
                required=frozenset({"minimum", "maximum", "priority"}),
            )
            minimum = _number(
                requested["minimum"], f"draft.primaryStats.{key}.minimum", nullable=True
            )
            maximum = _number(
                requested["maximum"], f"draft.primaryStats.{key}.maximum", nullable=True
            )
            priority = _number(
                requested["priority"],
                f"draft.primaryStats.{key}.priority",
                minimum=-1,
                maximum=3,
                integer=True,
            )
            if minimum is not None and maximum is not None and minimum > maximum:
                raise OptimizerProfileServiceError(
                    "validation",
                    code="range-order",
                    field_path=f"draft.primaryStats.{key}.maximum",
                    message="Maximum must be greater than or equal to minimum.",
                )
            if minimum is not None or maximum is not None:
                stat_ranges.append((stat, StatRange(minimum, maximum)))
            stat_priorities.append((stat, priority))

        pattern_value = _exact_object(
            draft["setPattern"],
            path="draft.setPattern",
            required=frozenset({"kind", "sets"}),
        )
        pattern_kind = _text(pattern_value["kind"], "draft.setPattern.kind")
        if pattern_kind not in {"4+2", "2+2+2", "flexible"}:
            raise OptimizerProfileServiceError(
                "validation",
                code="invalid-set-layout",
                field_path="draft.setPattern.kind",
                message="Choose three optional set requirements.",
            )
        raw_sets = pattern_value["sets"]
        if isinstance(raw_sets, (str, bytes, bytearray)):
            selected_sets: tuple[object, ...] = ()
        else:
            try:
                selected_sets = tuple(raw_sets)
            except TypeError:
                selected_sets = ()
        expected_pieces: tuple[int | None, ...] = (
            (4, 2)
            if pattern_kind == "4+2"
            else (2, 2, 2)
            if pattern_kind == "2+2+2"
            else (None, None, None)
        )
        if len(selected_sets) != len(expected_pieces):
            raise OptimizerProfileServiceError(
                "validation",
                code="incomplete-set-pattern",
                field_path="draft.setPattern.sets",
                message=f"The {pattern_kind} layout requires exactly {len(expected_pieces)} set selections.",
            )
        gear_sets: list[GearSet] = []
        for index, raw_set in enumerate(selected_sets):
            path = f"draft.setPattern.sets[{index}]"
            if raw_set is None and pattern_kind == "flexible":
                continue
            set_id = _text(raw_set, path)
            try:
                gear_set = GearSet(set_id)
            except ValueError:
                raise OptimizerProfileServiceError(
                    "validation", code="unknown-set", field_path=path, message="Choose a known gear set."
                ) from None
            if (
                expected_pieces[index] is not None
                and SET_CATALOG[gear_set].pieces_required != expected_pieces[index]
            ):
                raise OptimizerProfileServiceError(
                    "validation",
                    code="wrong-set-size",
                    field_path=path,
                    message=f"This selector requires a {expected_pieces[index]}-piece set.",
                )
            if gear_set in gear_sets and not SET_CATALOG[gear_set].stackable:
                raise OptimizerProfileServiceError(
                    "validation",
                    code="nonstackable-set-repeat",
                    field_path=path,
                    message="This set cannot be selected more than once.",
                )
            gear_sets.append(gear_set)
        if sum(SET_CATALOG[gear_set].pieces_required for gear_set in gear_sets) > 6:
            raise OptimizerProfileServiceError(
                "validation",
                code="too-many-set-pieces",
                field_path="draft.setPattern.sets",
                message="Selected sets require more than the six gear pieces a hero can equip.",
            )
        set_pattern = SetPattern(tuple(gear_sets))
        if pattern_kind != "flexible" and set_pattern.kind != pattern_kind:
            raise OptimizerProfileServiceError(
                "validation",
                code="set-layout-mismatch",
                field_path="draft.setPattern.kind",
                message="The selected sets do not match this layout.",
            )

        include_equipped = _boolean(draft["includeEquipped"], "draft.includeEquipped")
        # Persisted version-7 documents migrate through OptimizationRequest.
        # New renderer submissions must already use the exact-only values.
        maximum_replacement_distance = _number(
            draft["maximumReplacementDistance"],
            "draft.maximumReplacementDistance",
            minimum=0,
            maximum=0,
            integer=True,
        )
        near_tolerance_percent = _number(
            draft["nearSetTolerancePercent"],
            "draft.nearSetTolerancePercent",
            minimum=0,
            maximum=0,
        )
        projection_id = _text(draft["itemProjectionMode"], "draft.itemProjectionMode")
        try:
            item_projection_mode = ItemProjectionMode(projection_id)
        except ValueError:
            raise OptimizerProfileServiceError(
                "validation",
                code="invalid-projection-mode",
                field_path="draft.itemProjectionMode",
                message="Choose current or reforged item stats.",
            ) from None

        gear_filter_value = _exact_object(
            draft["gearFilters"],
            path="draft.gearFilters",
            required=frozenset({"minimumEnhance", "rightSideMainStats"}),
        )
        minimum_enhance = _number(
            gear_filter_value["minimumEnhance"],
            "draft.gearFilters.minimumEnhance",
            minimum=15,
            maximum=15,
            integer=True,
        )
        main_stats_value = _exact_object(
            gear_filter_value["rightSideMainStats"],
            path="draft.gearFilters.rightSideMainStats",
            required=_RIGHT_SIDE_SLOT_KEYS,
        )
        right_side_main_stats: list[tuple[GearSlot, tuple[ItemStatType, ...]]] = []
        for slot in RIGHT_SIDE_GEAR_SLOTS:
            raw_stats = main_stats_value[slot.value]
            path = f"draft.gearFilters.rightSideMainStats.{slot.value}"
            if isinstance(raw_stats, (str, bytes, bytearray)):
                raise OptimizerProfileServiceError(
                    "validation", code="invalid-main-stats", field_path=path, message="Main-stat choices must be a list."
                )
            try:
                supplied_stats = tuple(raw_stats)
            except TypeError:
                raise OptimizerProfileServiceError(
                    "validation", code="invalid-main-stats", field_path=path, message="Main-stat choices must be a list."
                ) from None
            parsed_stats: list[ItemStatType] = []
            for index, raw_stat in enumerate(supplied_stats):
                item_path = f"{path}[{index}]"
                stat_id = _text(raw_stat, item_path)
                try:
                    stat = ItemStatType(stat_id)
                except ValueError:
                    raise OptimizerProfileServiceError(
                        "validation", code="unknown-main-stat", field_path=item_path, message="Choose a known main stat."
                    ) from None
                if stat in parsed_stats:
                    raise OptimizerProfileServiceError(
                        "validation", code="duplicate-main-stat", field_path=item_path, message="Choose each main stat only once."
                    )
                if stat not in ALLOWED_MAIN_STATS_BY_SLOT[slot]:
                    raise OptimizerProfileServiceError(
                        "validation",
                        code="illegal-main-stat",
                        field_path=item_path,
                        message=f"This main stat is not legal for {gear_slot_display_name(slot)}.",
                    )
                parsed_stats.append(stat)
            if parsed_stats:
                right_side_main_stats.append((slot, tuple(parsed_stats)))
        gear_filters = GearSearchFilters(
            right_side_main_stats=tuple(right_side_main_stats),
            minimum_enhance=minimum_enhance,
        )

        skills_value = draft["skills"]
        if isinstance(skills_value, (str, bytes)):
            skills = ()
        else:
            try:
                skills = tuple(skills_value)
            except TypeError:
                skills = ()
        if len(skills) != 3:
            raise OptimizerProfileServiceError(
                "validation", code="incomplete-skill-contexts", field_path="draft.skills", message="Exactly S1, S2, and S3 contexts are required."
            )
        contexts = []
        for index, raw_context in enumerate(skills):
            context = _exact_object(
                raw_context,
                path=f"draft.skills[{index}]",
                required=frozenset({"skill", "sourceOptionId", "hitType", "targetCountOverride", "penetrationPercent", "targetDefense"}),
            )
            penetration = _number(
                context["penetrationPercent"],
                f"draft.skills[{index}].penetrationPercent",
                minimum=0,
                maximum=100,
                nullable=True,
            )
            contexts.append(SkillContext(
                skill=_text(context["skill"], f"draft.skills[{index}].skill"),
                source_option_id=_nullable_text(context["sourceOptionId"], f"draft.skills[{index}].sourceOptionId"),
                hit_type=_nullable_text(context["hitType"], f"draft.skills[{index}].hitType"),
                target_count_override=_number(context["targetCountOverride"], f"draft.skills[{index}].targetCountOverride", minimum=1, integer=True, nullable=True),
                penetration_override=None if penetration is None else penetration / 100,
                target_defense=_number(context["targetDefense"], f"draft.skills[{index}].targetDefense", minimum=0),
            ))
        request = OptimizationRequest(
            request_id=_VALIDATION_REQUEST_ID,
            hero_id=hero_id,
            base_profile_id=profile_id,
            modifiers=modifiers,
            set_pattern=set_pattern,
            stat_ranges=tuple(stat_ranges),
            stat_priorities=tuple(stat_priorities),
            derived_metric_ranges=(),
            include_equipped=include_equipped,
            gear_filters=gear_filters,
            near_set_tolerance=near_tolerance_percent / 100,
            maximum_replacement_distance=maximum_replacement_distance,
            target_defense=_DEFAULT_TARGET_DEFENSE,
            skill_contexts=tuple(contexts),
            item_projection_mode=item_projection_mode,
        )
        try:
            return self.skill_contexts.select(hero_id, request.skill_contexts).apply_to_request(request)
        except SkillContextRepositoryError as error:
            raise OptimizerProfileServiceError(
                "validation", code=error.code, field_path=f"draft.{error.path}", message=error.message
            ) from error

    def create_request(self, value: object, request_id: str) -> OptimizationRequest:
        """Validate a desktop draft without persisting it and assign a trusted ID."""

        identifier = _text(request_id, "requestId")
        return replace(self._draft_to_request(value), request_id=identifier)

    def save_draft(self, value: object) -> dict[str, Any]:
        try:
            request = self._draft_to_request(value)
        except OptimizerProfileServiceError:
            raise
        except (DomainValidationError, ValueError, TypeError) as error:
            raise OptimizerProfileServiceError(
                "validation",
                code="invalid-draft",
                field_path="draft",
                message="The hero draft contains an invalid value.",
            ) from error
        # Never replace a malformed, catalog-invalid, or future-version document.
        # Explicit item exclusions are not exposed through the desktop DTO, so
        # retain any that already exist in a valid profile during resaves.
        existing_profile, _version = self._read_profile(request.hero_id)
        if existing_profile is not None:
            existing_request = existing_profile.create_request(_VALIDATION_REQUEST_ID)
            if existing_request.gear_filters.excluded_item_ids:
                request = replace(
                    request,
                    gear_filters=GearSearchFilters(
                        right_side_main_stats=request.gear_filters.right_side_main_stats,
                        minimum_enhance=request.gear_filters.minimum_enhance,
                        excluded_item_ids=existing_request.gear_filters.excluded_item_ids,
                    ),
                )
        saved_at = _timestamp(self.clock())
        digest = hashlib.sha256(request.hero_id.encode("utf-8")).hexdigest()[:24]
        try:
            profile = OptimizerProfileDocument(
                profile_id=f"optimizer-profile.desktop.{digest}",
                name=f"Desktop optimizer draft for {self.characters.get(request.hero_id).name}",
                saved_at=saved_at,
                source=SourceMetadata(source_name=DESKTOP_PROFILE_SOURCE_NAME, source_version=BACKEND_VERSION),
                character_catalog_id=self.catalog.catalog_id,
                configuration=OptimizerConfiguration.from_request(request),
            ).validate_character_catalog(self.catalog)
        except SchemaValidationError as error:
            raise OptimizerProfileServiceError(
                "validation",
                code="profile-validation-failed",
                field_path="draft",
                message="The hero draft failed catalog validation.",
            ) from error
        path = self._profile_path(request.hero_id)
        self.profile_directory.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=self.profile_directory)
            temporary = Path(temp_name)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(profile.to_json())
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            temporary = None
        except OSError as error:
            raise OptimizerProfileServiceError(
                "storage", code="profile-write-failed", message="The hero draft could not be saved atomically."
            ) from error
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        return self._envelope(profile, request)


__all__ = [
    "DESKTOP_PROFILE_DIRECTORY",
    "MAX_DESKTOP_ARTIFACT_RESULTS",
    "MAX_DESKTOP_CHARACTER_RESULTS",
    "OptimizerProfileService",
    "OptimizerProfileServiceError",
]
