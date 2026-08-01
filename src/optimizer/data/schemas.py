"""Versioned persistence envelopes for optimizer data.

The four schema families deliberately evolve independently. Domain records
remain the authority for hero, gear, request, and summary invariants; this
module adds persistence versioning, attribution, identity, and cross-record
validation around them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from src.optimizer.data.schema_common import (
    FrozenJsonObject,
    SchemaValidationError,
    SourceMetadata,
    deterministic_json,
    document_object,
    domain_value,
    freeze_json_object,
    json_array,
    load_versioned_document,
    migration_registry,
    optional_dense_values,
    optional_text,
    parse_json_document,
    required_text,
    sha256_checksum,
    thaw_json,
    timestamp_value,
    unique_values,
    utc_timestamp,
)
from src.optimizer.domain import (
    ArtifactDefinition,
    GearItem,
    HeroDefinition,
    OptimizationRequest,
    SearchSummary,
)


CHARACTER_CATALOG_SCHEMA_ID = "e7.optimizer.character-catalog"
CHARACTER_CATALOG_CURRENT_VERSION = 1
INVENTORY_SCHEMA_ID = "e7.optimizer.inventory"
INVENTORY_CURRENT_VERSION = 1
OPTIMIZER_PROFILE_SCHEMA_ID = "e7.optimizer.optimizer-profile"
OPTIMIZER_PROFILE_CURRENT_VERSION = 7
RUN_MANIFEST_SCHEMA_ID = "e7.optimizer.run-manifest"
RUN_MANIFEST_CURRENT_VERSION = 7

_ARTIFACT_CONFIGURATION_V2_DEFAULTS = {
    "artifactLimitBreaks": None,
    "artifactAttackOverride": None,
    "artifactHealthOverride": None,
    "artifactDefenseOverride": None,
}

_HERO_MODIFIER_CONFIGURATION_V3_DEFAULTS = {
    "imprintContribution": None,
    "exclusiveEquipmentContribution": None,
    "exclusiveEquipmentSkillOptionId": None,
}

_TYPED_CUSTOM_AND_SKILL_CONTEXT_V4_MODIFIER_DEFAULTS = {
    "customContributions": [],
}


def _migrate_request_artifact_configuration(
    request: object,
    *,
    family: str,
) -> None:
    if not isinstance(request, Mapping):
        raise SchemaValidationError(f"{family} request payload must be an object during migration.")
    modifiers = request.get("modifiers")
    if not isinstance(modifiers, dict):
        raise SchemaValidationError(f"{family} modifiers must be an object during migration.")
    for field, default in _ARTIFACT_CONFIGURATION_V2_DEFAULTS.items():
        if field in modifiers:
            raise SchemaValidationError(
                f"{family} schemaVersion 1 must not already contain version-2 field {field}."
            )
        modifiers[field] = default


def _migrate_optimizer_profile_v1_to_v2(value: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = thaw_json(freeze_json_object(value, "Optimizer profile version-1 migration"))
    _migrate_request_artifact_configuration(
        payload.get("configuration"),
        family="Optimizer profile",
    )
    payload["schemaVersion"] = 2
    return payload


def _migrate_run_manifest_v1_to_v2(value: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = thaw_json(freeze_json_object(value, "Run manifest version-1 migration"))
    _migrate_request_artifact_configuration(
        payload.get("requestSnapshot"),
        family="Run manifest",
    )
    payload["schemaVersion"] = 2
    return payload


def _migrate_request_typed_hero_modifiers(
    request: object,
    *,
    family: str,
) -> None:
    if not isinstance(request, Mapping):
        raise SchemaValidationError(f"{family} request payload must be an object during migration.")
    modifiers = request.get("modifiers")
    if not isinstance(modifiers, dict):
        raise SchemaValidationError(f"{family} modifiers must be an object during migration.")
    for field, default in _HERO_MODIFIER_CONFIGURATION_V3_DEFAULTS.items():
        if field in modifiers:
            raise SchemaValidationError(
                f"{family} schemaVersion 2 must not already contain version-3 field {field}."
            )
        modifiers[field] = default


def _migrate_optimizer_profile_v2_to_v3(value: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = thaw_json(freeze_json_object(value, "Optimizer profile version-2 migration"))
    _migrate_request_typed_hero_modifiers(
        payload.get("configuration"),
        family="Optimizer profile",
    )
    payload["schemaVersion"] = 3
    return payload


def _migrate_run_manifest_v2_to_v3(value: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = thaw_json(freeze_json_object(value, "Run manifest version-2 migration"))
    _migrate_request_typed_hero_modifiers(
        payload.get("requestSnapshot"),
        family="Run manifest",
    )
    payload["schemaVersion"] = 3
    return payload


def _migrate_request_custom_and_skill_context(
    request: object,
    *,
    family: str,
) -> None:
    if not isinstance(request, Mapping):
        raise SchemaValidationError(f"{family} request payload must be an object during migration.")
    modifiers = request.get("modifiers")
    if not isinstance(modifiers, dict):
        raise SchemaValidationError(f"{family} modifiers must be an object during migration.")
    for field, default in _TYPED_CUSTOM_AND_SKILL_CONTEXT_V4_MODIFIER_DEFAULTS.items():
        if field in modifiers:
            raise SchemaValidationError(
                f"{family} schemaVersion 3 must not already contain version-4 field {field}."
            )
        modifiers[field] = list(default)
    if "skillContexts" in request:
        raise SchemaValidationError(
            f"{family} schemaVersion 3 must not already contain version-4 field skillContexts."
        )
    target_defense = request.get("targetDefense", 1000)
    request["skillContexts"] = [
        {
            "skill": skill,
            "sourceOptionId": None,
            "hitType": None,
            "targetCountOverride": None,
            "penetrationOverride": None,
            "targetDefense": target_defense,
        }
        for skill in ("skill.s1", "skill.s2", "skill.s3")
    ]


def _migrate_optimizer_profile_v3_to_v4(value: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = thaw_json(freeze_json_object(value, "Optimizer profile version-3 migration"))
    _migrate_request_custom_and_skill_context(
        payload.get("configuration"),
        family="Optimizer profile",
    )
    payload["schemaVersion"] = 4
    return payload


def _migrate_run_manifest_v3_to_v4(value: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = thaw_json(freeze_json_object(value, "Run manifest version-3 migration"))
    _migrate_request_custom_and_skill_context(
        payload.get("requestSnapshot"),
        family="Run manifest",
    )
    payload["schemaVersion"] = 4
    return payload


def _migrate_request_item_projection_mode(
    request: object,
    *,
    family: str,
) -> None:
    if not isinstance(request, Mapping):
        raise SchemaValidationError(f"{family} request payload must be an object during migration.")
    if "itemProjectionMode" in request:
        raise SchemaValidationError(
            f"{family} schemaVersion 4 must not already contain version-5 field itemProjectionMode."
        )
    request["itemProjectionMode"] = None


def _migrate_optimizer_profile_v4_to_v5(value: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = thaw_json(freeze_json_object(value, "Optimizer profile version-4 migration"))
    _migrate_request_item_projection_mode(
        payload.get("configuration"),
        family="Optimizer profile",
    )
    payload["schemaVersion"] = 5
    return payload


def _migrate_run_manifest_v4_to_v5(value: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = thaw_json(freeze_json_object(value, "Run manifest version-4 migration"))
    _migrate_request_item_projection_mode(
        payload.get("requestSnapshot"),
        family="Run manifest",
    )
    payload["schemaVersion"] = 5
    return payload


def _migrate_request_gear_filters(
    request: object,
    *,
    family: str,
) -> None:
    if not isinstance(request, Mapping):
        raise SchemaValidationError(f"{family} request payload must be an object during migration.")
    if "gearFilters" in request:
        raise SchemaValidationError(
            f"{family} schemaVersion 5 must not already contain version-6 field gearFilters."
        )
    request["gearFilters"] = {
        "rightSideMainStats": {},
        "minimumEnhance": 0,
        "excludedItemIds": [],
    }


def _migrate_optimizer_profile_v5_to_v6(value: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = thaw_json(freeze_json_object(value, "Optimizer profile version-5 migration"))
    _migrate_request_gear_filters(
        payload.get("configuration"),
        family="Optimizer profile",
    )
    payload["schemaVersion"] = 6
    return payload


def _migrate_run_manifest_v5_to_v6(value: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = thaw_json(freeze_json_object(value, "Run manifest version-5 migration"))
    _migrate_request_gear_filters(
        payload.get("requestSnapshot"),
        family="Run manifest",
    )
    payload["schemaVersion"] = 6
    return payload


def _migrate_request_maximum_replacement_distance(
    request: object,
    *,
    family: str,
) -> None:
    if not isinstance(request, Mapping):
        raise SchemaValidationError(f"{family} request payload must be an object during migration.")
    if "maximumReplacementDistance" in request:
        raise SchemaValidationError(
            f"{family} schemaVersion 6 must not already contain version-7 field maximumReplacementDistance."
        )
    request["maximumReplacementDistance"] = 2


def _migrate_optimizer_profile_v6_to_v7(value: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = thaw_json(freeze_json_object(value, "Optimizer profile version-6 migration"))
    _migrate_request_maximum_replacement_distance(
        payload.get("configuration"),
        family="Optimizer profile",
    )
    payload["schemaVersion"] = 7
    return payload


def _migrate_run_manifest_v6_to_v7(value: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = thaw_json(freeze_json_object(value, "Run manifest version-6 migration"))
    _migrate_request_maximum_replacement_distance(
        payload.get("requestSnapshot"),
        family="Run manifest",
    )
    payload["schemaVersion"] = 7
    return payload

CHARACTER_CATALOG_MIGRATIONS = migration_registry({})
INVENTORY_MIGRATIONS = migration_registry({})
OPTIMIZER_PROFILE_MIGRATIONS = migration_registry(
    {
        1: _migrate_optimizer_profile_v1_to_v2,
        2: _migrate_optimizer_profile_v2_to_v3,
        3: _migrate_optimizer_profile_v3_to_v4,
        4: _migrate_optimizer_profile_v4_to_v5,
        5: _migrate_optimizer_profile_v5_to_v6,
        6: _migrate_optimizer_profile_v6_to_v7,
    }
)
RUN_MANIFEST_MIGRATIONS = migration_registry(
    {
        1: _migrate_run_manifest_v1_to_v2,
        2: _migrate_run_manifest_v2_to_v3,
        3: _migrate_run_manifest_v3_to_v4,
        4: _migrate_run_manifest_v4_to_v5,
        5: _migrate_run_manifest_v5_to_v6,
        6: _migrate_run_manifest_v6_to_v7,
    }
)

CHARACTER_FAMILY = "Character catalog"
INVENTORY_FAMILY = "Inventory"
PROFILE_FAMILY = "Optimizer profile"
RUN_FAMILY = "Run manifest"


def _records(value: object, field: str, record_type: type, family: str) -> tuple[Any, ...]:
    raw_items = json_array(value, field)
    return tuple(
        domain_value(
            family,
            f"{field}[{index}]",
            lambda item=item: record_type.from_dict(item),
        )
        for index, item in enumerate(raw_items)
    )


@dataclass(frozen=True, slots=True)
class CharacterCatalogDocument:
    """A source-attributed snapshot of hero profiles and artifacts."""

    catalog_id: str
    generated_at: str
    source: SourceMetadata
    heroes: tuple[HeroDefinition, ...]
    artifacts: tuple[ArtifactDefinition, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.source, SourceMetadata):
            raise SchemaValidationError("Character catalog source must be SourceMetadata.")
        try:
            heroes = tuple(self.heroes)
            artifacts = tuple(self.artifacts)
        except TypeError:
            raise SchemaValidationError("Character catalog heroes and artifacts must be sequences.") from None
        if not all(isinstance(hero, HeroDefinition) for hero in heroes):
            raise SchemaValidationError("Character catalog heroes must contain HeroDefinition records.")
        if not all(isinstance(artifact, ArtifactDefinition) for artifact in artifacts):
            raise SchemaValidationError(
                "Character catalog artifacts must contain ArtifactDefinition records."
            )

        unique_values([hero.hero_id for hero in heroes], "Character catalog hero IDs")
        optional_dense_values(
            [hero.dense_id for hero in heroes], "Character catalog hero dense IDs"
        )
        profiles = [profile for hero in heroes for profile in hero.base_profiles]
        unique_values(
            [profile.profile_id for profile in profiles], "Character catalog profile IDs"
        )
        optional_dense_values(
            [profile.dense_id for profile in profiles], "Character catalog profile dense IDs"
        )
        unique_values(
            [artifact.artifact_id for artifact in artifacts], "Character catalog artifact IDs"
        )
        optional_dense_values(
            [artifact.dense_id for artifact in artifacts],
            "Character catalog artifact dense IDs",
        )

        object.__setattr__(self, "catalog_id", required_text(self.catalog_id, "Character catalog catalogId"))
        object.__setattr__(
            self, "generated_at", utc_timestamp(self.generated_at, "Character catalog generatedAt")
        )
        object.__setattr__(self, "heroes", tuple(sorted(heroes, key=lambda hero: hero.hero_id)))
        object.__setattr__(
            self,
            "artifacts",
            tuple(sorted(artifacts, key=lambda artifact: artifact.artifact_id)),
        )

    @property
    def hero_ids(self) -> frozenset[str]:
        return frozenset(hero.hero_id for hero in self.heroes)

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaId": CHARACTER_CATALOG_SCHEMA_ID,
            "schemaVersion": CHARACTER_CATALOG_CURRENT_VERSION,
            "catalogId": self.catalog_id,
            "generatedAt": self.generated_at,
            "source": self.source.to_dict(),
            "heroes": [hero.to_dict() for hero in self.heroes],
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
        }

    def to_json(self) -> str:
        return deterministic_json(self)

    @classmethod
    def from_dict(cls, value: object) -> "CharacterCatalogDocument":
        return load_character_catalog(value)

    @classmethod
    def from_json(cls, value: object) -> "CharacterCatalogDocument":
        return load_character_catalog_json(value)

    @classmethod
    def from_current_dict(cls, value: Mapping[str, Any]) -> "CharacterCatalogDocument":
        data = document_object(
            value,
            CHARACTER_FAMILY,
            required=(
                "schemaId",
                "schemaVersion",
                "catalogId",
                "generatedAt",
                "source",
                "heroes",
                "artifacts",
            ),
        )
        return cls(
            catalog_id=data["catalogId"],
            generated_at=data["generatedAt"],
            source=SourceMetadata.from_dict(data["source"], family=CHARACTER_FAMILY),
            heroes=_records(data["heroes"], "heroes", HeroDefinition, CHARACTER_FAMILY),
            artifacts=_records(
                data["artifacts"], "artifacts", ArtifactDefinition, CHARACTER_FAMILY
            ),
        )


@dataclass(frozen=True, slots=True)
class InventoryDocument:
    """A source-attributed snapshot of the user's owned gear."""

    inventory_id: str
    imported_at: str
    source: SourceMetadata
    items: tuple[GearItem, ...]
    character_catalog_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, SourceMetadata):
            raise SchemaValidationError("Inventory source must be SourceMetadata.")
        try:
            items = tuple(self.items)
        except TypeError:
            raise SchemaValidationError("Inventory items must be a sequence.") from None
        if not all(isinstance(item, GearItem) for item in items):
            raise SchemaValidationError("Inventory items must contain GearItem records.")
        unique_values([item.item_id for item in items], "Inventory item IDs")
        optional_dense_values([item.dense_id for item in items], "Inventory item dense IDs")

        object.__setattr__(self, "inventory_id", required_text(self.inventory_id, "Inventory inventoryId"))
        object.__setattr__(self, "imported_at", utc_timestamp(self.imported_at, "Inventory importedAt"))
        object.__setattr__(
            self,
            "character_catalog_id",
            optional_text(self.character_catalog_id, "Inventory characterCatalogId"),
        )
        object.__setattr__(self, "items", tuple(sorted(items, key=lambda item: item.item_id)))

    def validate_character_catalog(
        self, catalog: CharacterCatalogDocument
    ) -> "InventoryDocument":
        if not isinstance(catalog, CharacterCatalogDocument):
            raise SchemaValidationError("Inventory character catalog context is invalid.")
        if self.character_catalog_id is not None and self.character_catalog_id != catalog.catalog_id:
            raise SchemaValidationError(
                "Inventory characterCatalogId does not match the supplied character catalog."
            )
        unknown = sorted(
            {
                item.equipped_hero_id
                for item in self.items
                if item.equipped_hero_id is not None
                and item.equipped_hero_id not in catalog.hero_ids
            }
        )
        if unknown:
            raise SchemaValidationError(
                "Inventory equippedHeroId reference(s) are absent from the supplied character catalog: "
                + ", ".join(unknown)
                + "."
            )
        return self

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaId": INVENTORY_SCHEMA_ID,
            "schemaVersion": INVENTORY_CURRENT_VERSION,
            "inventoryId": self.inventory_id,
            "importedAt": self.imported_at,
            "source": self.source.to_dict(),
            "characterCatalogId": self.character_catalog_id,
            "items": [item.to_dict() for item in self.items],
        }

    def to_json(self) -> str:
        return deterministic_json(self)

    @classmethod
    def from_dict(
        cls,
        value: object,
        *,
        character_catalog: CharacterCatalogDocument | None = None,
    ) -> "InventoryDocument":
        return load_inventory(value, character_catalog=character_catalog)

    @classmethod
    def from_json(
        cls,
        value: object,
        *,
        character_catalog: CharacterCatalogDocument | None = None,
    ) -> "InventoryDocument":
        return load_inventory_json(value, character_catalog=character_catalog)

    @classmethod
    def from_current_dict(cls, value: Mapping[str, Any]) -> "InventoryDocument":
        data = document_object(
            value,
            INVENTORY_FAMILY,
            required=(
                "schemaId",
                "schemaVersion",
                "inventoryId",
                "importedAt",
                "source",
                "items",
            ),
            optional=("characterCatalogId",),
        )
        return cls(
            inventory_id=data["inventoryId"],
            imported_at=data["importedAt"],
            source=SourceMetadata.from_dict(data["source"], family=INVENTORY_FAMILY),
            character_catalog_id=data.get("characterCatalogId"),
            items=_records(data["items"], "items", GearItem, INVENTORY_FAMILY),
        )


_PROFILE_VALIDATION_REQUEST_ID = "persistence.profile-validation"


def _require_current_maximum_replacement_distance(
    value: object,
    *,
    family: str,
    path: str,
) -> None:
    if not isinstance(value, Mapping):
        raise SchemaValidationError(f"{family} {path} must be an object.")
    if "maximumReplacementDistance" not in value:
        raise SchemaValidationError(
            f"{family} {path} requires version-7 field maximumReplacementDistance."
        )


@dataclass(frozen=True, slots=True)
class OptimizerConfiguration:
    """A normalized request configuration that deliberately has no request ID."""

    payload: FrozenJsonObject

    def __post_init__(self) -> None:
        raw = thaw_json(freeze_json_object(self.payload, "Optimizer profile configuration"))
        if "requestId" in raw:
            raise SchemaValidationError(
                "Optimizer profile configuration must not contain requestId; a new request ID is "
                "required when starting an execution."
            )
        request_payload = {"requestId": _PROFILE_VALIDATION_REQUEST_ID, **raw}
        request = domain_value(
            PROFILE_FAMILY,
            "configuration",
            lambda: OptimizationRequest.from_dict(request_payload),
        )
        normalized = request.to_dict()
        del normalized["requestId"]
        object.__setattr__(
            self,
            "payload",
            freeze_json_object(normalized, "Optimizer profile configuration"),
        )

    @classmethod
    def from_request(cls, request: OptimizationRequest) -> "OptimizerConfiguration":
        if not isinstance(request, OptimizationRequest):
            raise SchemaValidationError(
                "OptimizerConfiguration.from_request requires an OptimizationRequest."
            )
        payload = request.to_dict()
        del payload["requestId"]
        return cls(freeze_json_object(payload, "Optimizer profile configuration"))

    @classmethod
    def from_dict(cls, value: object) -> "OptimizerConfiguration":
        return cls(freeze_json_object(value, "Optimizer profile configuration"))

    def to_dict(self) -> dict[str, object]:
        return thaw_json(self.payload)

    def create_request(self, request_id: str) -> OptimizationRequest:
        """Create a new execution request; callers must supply its new identity."""

        payload = self.to_dict()
        payload["requestId"] = required_text(request_id, "Optimization request requestId")
        return domain_value(
            PROFILE_FAMILY,
            "new request",
            lambda: OptimizationRequest.from_dict(payload),
        )


def _validate_request_catalog(
    request: OptimizationRequest,
    catalog: CharacterCatalogDocument,
    family: str,
) -> None:
    hero = next((item for item in catalog.heroes if item.hero_id == request.hero_id), None)
    if hero is None:
        raise SchemaValidationError(
            f"{family} heroId {request.hero_id!r} is absent from the supplied character catalog."
        )
    if request.base_profile_id not in {profile.profile_id for profile in hero.base_profiles}:
        raise SchemaValidationError(
            f"{family} baseProfileId {request.base_profile_id!r} does not belong to hero "
            f"{request.hero_id!r} in the supplied character catalog."
        )
    artifact_id = request.modifiers.artifact_id
    artifact = next(
        (item for item in catalog.artifacts if item.artifact_id == artifact_id),
        None,
    )
    if artifact_id is not None and artifact is None:
        raise SchemaValidationError(
            f"{family} artifactId {artifact_id!r} is absent from the supplied character catalog."
        )
    if artifact is not None and request.modifiers.artifact_level > artifact.max_level:
        raise SchemaValidationError(
            f"{family} artifactLevel {request.modifiers.artifact_level} exceeds maximum level "
            f"{artifact.max_level} for artifact {artifact_id!r}."
        )
    if request.modifiers.artifact_limit_breaks is not None:
        raise SchemaValidationError(
            f"{family} artifactLimitBreaks cannot be resolved because the supplied character "
            "catalog contains no limit-break effect data."
        )
    if any(
        value is not None
        for value in (
            request.modifiers.imprint_contribution,
            request.modifiers.exclusive_equipment_contribution,
            request.modifiers.exclusive_equipment_skill_option_id,
        )
    ):
        from src.optimizer.data.hero_modifier_repository import (
            HeroModifierRepositoryError,
            load_bundled_hero_modifier_repository,
        )

        try:
            load_bundled_hero_modifier_repository().validate_modifiers(
                request.hero_id,
                request.modifiers,
            )
        except HeroModifierRepositoryError as exc:
            raise SchemaValidationError(
                f"{family} hero modifier selection is invalid: {exc}"
            ) from exc
    skill_context_active = any(
        context.source_option_id is not None
        or context.hit_type is not None
        or context.target_count_override is not None
        or context.penetration_override is not None
        or context.target_defense != request.target_defense
        for context in request.skill_contexts
    ) or any(
        option_id.startswith("skill-option.fribbels.")
        for option_id in request.modifiers.skill_options
    )
    if skill_context_active:
        from src.optimizer.data.skill_context_repository import (
            SkillContextRepositoryError,
            load_bundled_skill_context_repository,
        )

        try:
            load_bundled_skill_context_repository().validate_request(request)
        except SkillContextRepositoryError as exc:
            raise SchemaValidationError(
                f"{family} skill context selection is invalid: {exc}"
            ) from exc


@dataclass(frozen=True, slots=True)
class OptimizerProfileDocument:
    """A reusable optimizer configuration, separate from execution identity."""

    profile_id: str
    name: str
    saved_at: str
    source: SourceMetadata
    configuration: OptimizerConfiguration
    description: str | None = None
    character_catalog_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, SourceMetadata):
            raise SchemaValidationError("Optimizer profile source must be SourceMetadata.")
        if not isinstance(self.configuration, OptimizerConfiguration):
            raise SchemaValidationError(
                "Optimizer profile configuration must be OptimizerConfiguration."
            )
        object.__setattr__(self, "profile_id", required_text(self.profile_id, "Optimizer profile profileId"))
        object.__setattr__(self, "name", required_text(self.name, "Optimizer profile name"))
        object.__setattr__(self, "saved_at", utc_timestamp(self.saved_at, "Optimizer profile savedAt"))
        object.__setattr__(
            self, "description", optional_text(self.description, "Optimizer profile description")
        )
        object.__setattr__(
            self,
            "character_catalog_id",
            optional_text(self.character_catalog_id, "Optimizer profile characterCatalogId"),
        )

    def validate_character_catalog(
        self, catalog: CharacterCatalogDocument
    ) -> "OptimizerProfileDocument":
        if self.character_catalog_id is not None and self.character_catalog_id != catalog.catalog_id:
            raise SchemaValidationError(
                "Optimizer profile characterCatalogId does not match the supplied character catalog."
            )
        _validate_request_catalog(
            self.configuration.create_request(_PROFILE_VALIDATION_REQUEST_ID),
            catalog,
            PROFILE_FAMILY,
        )
        return self

    def create_request(self, request_id: str) -> OptimizationRequest:
        return self.configuration.create_request(request_id)

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaId": OPTIMIZER_PROFILE_SCHEMA_ID,
            "schemaVersion": OPTIMIZER_PROFILE_CURRENT_VERSION,
            "profileId": self.profile_id,
            "name": self.name,
            "description": self.description,
            "savedAt": self.saved_at,
            "source": self.source.to_dict(),
            "characterCatalogId": self.character_catalog_id,
            "configuration": self.configuration.to_dict(),
        }

    def to_json(self) -> str:
        return deterministic_json(self)

    @classmethod
    def from_dict(
        cls,
        value: object,
        *,
        character_catalog: CharacterCatalogDocument | None = None,
    ) -> "OptimizerProfileDocument":
        return load_optimizer_profile(value, character_catalog=character_catalog)

    @classmethod
    def from_json(
        cls,
        value: object,
        *,
        character_catalog: CharacterCatalogDocument | None = None,
    ) -> "OptimizerProfileDocument":
        return load_optimizer_profile_json(value, character_catalog=character_catalog)

    @classmethod
    def from_current_dict(cls, value: Mapping[str, Any]) -> "OptimizerProfileDocument":
        data = document_object(
            value,
            PROFILE_FAMILY,
            required=(
                "schemaId",
                "schemaVersion",
                "profileId",
                "name",
                "savedAt",
                "source",
                "configuration",
            ),
            optional=("description", "characterCatalogId"),
        )
        _require_current_maximum_replacement_distance(
            data["configuration"],
            family=PROFILE_FAMILY,
            path="configuration",
        )
        return cls(
            profile_id=data["profileId"],
            name=data["name"],
            description=data.get("description"),
            saved_at=data["savedAt"],
            source=SourceMetadata.from_dict(data["source"], family=PROFILE_FAMILY),
            character_catalog_id=data.get("characterCatalogId"),
            configuration=OptimizerConfiguration.from_dict(data["configuration"]),
        )


class RunCompletionState(StrEnum):
    COMPLETED = "completed"
    OVERFLOWED = "overflowed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ResultStoreReference:
    """Location and integrity identity for results stored outside a manifest."""

    reference: str
    sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "reference", required_text(self.reference, "Result store reference"))
        object.__setattr__(self, "sha256", sha256_checksum(self.sha256, "Result store sha256"))

    def to_dict(self) -> dict[str, str]:
        return {"reference": self.reference, "sha256": self.sha256}

    @classmethod
    def from_dict(cls, value: object) -> "ResultStoreReference":
        data = document_object(
            value,
            "Result store reference",
            required=("reference", "sha256"),
        )
        return cls(reference=data["reference"], sha256=data["sha256"])


@dataclass(frozen=True, slots=True)
class RunManifestDocument:
    """A completed execution manifest; result rows live in a separate store."""

    run_id: str
    created_at: str
    completed_at: str
    completion_state: RunCompletionState
    source: SourceMetadata
    request_snapshot: OptimizationRequest
    summary: SearchSummary
    result_store: ResultStoreReference | None

    def __post_init__(self) -> None:
        if not isinstance(self.source, SourceMetadata):
            raise SchemaValidationError("Run manifest source must be SourceMetadata.")
        if not isinstance(self.request_snapshot, OptimizationRequest):
            raise SchemaValidationError(
                "Run manifest requestSnapshot must be an OptimizationRequest."
            )
        if not isinstance(self.summary, SearchSummary):
            raise SchemaValidationError("Run manifest summary must be a SearchSummary.")
        try:
            state = RunCompletionState(self.completion_state)
        except (TypeError, ValueError):
            raise SchemaValidationError(
                "Run manifest completionState must be completed, overflowed, or cancelled."
            ) from None
        if self.result_store is not None and not isinstance(
            self.result_store, ResultStoreReference
        ):
            raise SchemaValidationError(
                "Run manifest resultStore must be a ResultStoreReference or null."
            )

        run_id = required_text(self.run_id, "Run manifest runId")
        created_at = utc_timestamp(self.created_at, "Run manifest createdAt")
        completed_at = utc_timestamp(self.completed_at, "Run manifest completedAt")
        if timestamp_value(completed_at) < timestamp_value(created_at):
            raise SchemaValidationError("Run manifest completedAt must not precede createdAt.")
        if self.request_snapshot.request_id != self.summary.request_id:
            raise SchemaValidationError(
                "Run manifest requestSnapshot.requestId must match summary.requestId."
            )

        expected_state = (
            RunCompletionState.OVERFLOWED
            if self.summary.overflowed
            else RunCompletionState.CANCELLED
            if self.summary.cancelled
            else RunCompletionState.COMPLETED
        )
        if state is not expected_state:
            raise SchemaValidationError(
                "Run manifest completionState does not agree with SearchSummary abort flags."
            )
        if state is RunCompletionState.COMPLETED and self.result_store is None:
            raise SchemaValidationError(
                "Completed run manifests require a resultStore reference and checksum."
            )
        if state is not RunCompletionState.COMPLETED and self.result_store is not None:
            raise SchemaValidationError(
                "Overflowed or cancelled run manifests must not reference partial result rows."
            )

        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "completed_at", completed_at)
        object.__setattr__(self, "completion_state", state)

    def validate_character_catalog(
        self, catalog: CharacterCatalogDocument
    ) -> "RunManifestDocument":
        _validate_request_catalog(self.request_snapshot, catalog, RUN_FAMILY)
        return self

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaId": RUN_MANIFEST_SCHEMA_ID,
            "schemaVersion": RUN_MANIFEST_CURRENT_VERSION,
            "runId": self.run_id,
            "createdAt": self.created_at,
            "completedAt": self.completed_at,
            "completionState": self.completion_state.value,
            "source": self.source.to_dict(),
            "requestSnapshot": self.request_snapshot.to_dict(),
            "summary": self.summary.to_dict(),
            "resultStore": None if self.result_store is None else self.result_store.to_dict(),
        }

    def to_json(self) -> str:
        return deterministic_json(self)

    @classmethod
    def from_dict(
        cls,
        value: object,
        *,
        character_catalog: CharacterCatalogDocument | None = None,
    ) -> "RunManifestDocument":
        return load_run_manifest(value, character_catalog=character_catalog)

    @classmethod
    def from_json(
        cls,
        value: object,
        *,
        character_catalog: CharacterCatalogDocument | None = None,
    ) -> "RunManifestDocument":
        return load_run_manifest_json(value, character_catalog=character_catalog)

    @classmethod
    def from_current_dict(cls, value: Mapping[str, Any]) -> "RunManifestDocument":
        data = document_object(
            value,
            RUN_FAMILY,
            required=(
                "schemaId",
                "schemaVersion",
                "runId",
                "createdAt",
                "completedAt",
                "completionState",
                "source",
                "requestSnapshot",
                "summary",
                "resultStore",
            ),
        )
        _require_current_maximum_replacement_distance(
            data["requestSnapshot"],
            family=RUN_FAMILY,
            path="requestSnapshot",
        )
        request = domain_value(
            RUN_FAMILY,
            "requestSnapshot",
            lambda: OptimizationRequest.from_dict(data["requestSnapshot"]),
        )
        summary = domain_value(
            RUN_FAMILY,
            "summary",
            lambda: SearchSummary.from_dict(data["summary"]),
        )
        result_store = (
            None
            if data["resultStore"] is None
            else ResultStoreReference.from_dict(data["resultStore"])
        )
        return cls(
            run_id=data["runId"],
            created_at=data["createdAt"],
            completed_at=data["completedAt"],
            completion_state=data["completionState"],
            source=SourceMetadata.from_dict(data["source"], family=RUN_FAMILY),
            request_snapshot=request,
            summary=summary,
            result_store=result_store,
        )


def load_character_catalog(value: object) -> CharacterCatalogDocument:
    return load_versioned_document(
        value,
        family=CHARACTER_FAMILY,
        schema_id=CHARACTER_CATALOG_SCHEMA_ID,
        current_version=CHARACTER_CATALOG_CURRENT_VERSION,
        migrations=CHARACTER_CATALOG_MIGRATIONS,
        parser=CharacterCatalogDocument.from_current_dict,
    )


def load_inventory(
    value: object,
    *,
    character_catalog: CharacterCatalogDocument | None = None,
) -> InventoryDocument:
    document = load_versioned_document(
        value,
        family=INVENTORY_FAMILY,
        schema_id=INVENTORY_SCHEMA_ID,
        current_version=INVENTORY_CURRENT_VERSION,
        migrations=INVENTORY_MIGRATIONS,
        parser=InventoryDocument.from_current_dict,
    )
    return (
        document
        if character_catalog is None
        else document.validate_character_catalog(character_catalog)
    )


def load_optimizer_profile(
    value: object,
    *,
    character_catalog: CharacterCatalogDocument | None = None,
) -> OptimizerProfileDocument:
    document = load_versioned_document(
        value,
        family=PROFILE_FAMILY,
        schema_id=OPTIMIZER_PROFILE_SCHEMA_ID,
        current_version=OPTIMIZER_PROFILE_CURRENT_VERSION,
        migrations=OPTIMIZER_PROFILE_MIGRATIONS,
        parser=OptimizerProfileDocument.from_current_dict,
    )
    return (
        document
        if character_catalog is None
        else document.validate_character_catalog(character_catalog)
    )


def load_run_manifest(
    value: object,
    *,
    character_catalog: CharacterCatalogDocument | None = None,
) -> RunManifestDocument:
    document = load_versioned_document(
        value,
        family=RUN_FAMILY,
        schema_id=RUN_MANIFEST_SCHEMA_ID,
        current_version=RUN_MANIFEST_CURRENT_VERSION,
        migrations=RUN_MANIFEST_MIGRATIONS,
        parser=RunManifestDocument.from_current_dict,
    )
    return (
        document
        if character_catalog is None
        else document.validate_character_catalog(character_catalog)
    )


def load_character_catalog_json(value: object) -> CharacterCatalogDocument:
    return load_character_catalog(parse_json_document(value, CHARACTER_FAMILY))


def load_inventory_json(
    value: object,
    *,
    character_catalog: CharacterCatalogDocument | None = None,
) -> InventoryDocument:
    return load_inventory(
        parse_json_document(value, INVENTORY_FAMILY),
        character_catalog=character_catalog,
    )


def load_optimizer_profile_json(
    value: object,
    *,
    character_catalog: CharacterCatalogDocument | None = None,
) -> OptimizerProfileDocument:
    return load_optimizer_profile(
        parse_json_document(value, PROFILE_FAMILY),
        character_catalog=character_catalog,
    )


def load_run_manifest_json(
    value: object,
    *,
    character_catalog: CharacterCatalogDocument | None = None,
) -> RunManifestDocument:
    return load_run_manifest(
        parse_json_document(value, RUN_FAMILY),
        character_catalog=character_catalog,
    )
