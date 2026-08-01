"""Immutable offline artifact repository, selection, and flat stat calculation."""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from src.optimizer.data.character_repository import normalize_character_search_text
from src.optimizer.data.character_snapshot import (
    CharacterSourceSnapshotDocument,
    load_bundled_character_catalog,
    load_bundled_character_source_snapshot,
)
from src.optimizer.data.schema_common import FrozenJsonObject, required_text
from src.optimizer.data.schemas import CharacterCatalogDocument
from src.optimizer.domain import ArtifactDefinition, HeroModifiers


ARTIFACT_MAX_LEVEL = 30
ARTIFACT_MAX_STAT_MULTIPLIER = 13
ARTIFACT_LEVEL_DIVISOR = 30
ARTIFACT_LEVEL_ROUNDING_DIGITS = 1
FRIBBELS_ARTIFACT_LOGIC_PATH = "app/js/lib/artifact.js"
FRIBBELS_ARTIFACT_LOGIC_GIT_BLOB_SHA1 = "34dfb714ef97d6bc05da79048f1ee8e14b1c342a"
FRIBBELS_ARTIFACT_BACKEND_LOGIC_PATH = "backend/src/main/java/com/fribbels/db/ArtifactStatsDb.java"
FRIBBELS_ARTIFACT_BACKEND_LOGIC_GIT_BLOB_SHA1 = "f1e9d6cedbc4915f6ad2dd82e1d89bf17bca9c98"
FRIBBELS_ROUNDING_LOGIC_PATH = "app/js/lib/utils.js"
FRIBBELS_ROUNDING_LOGIC_GIT_BLOB_SHA1 = "18acebcc88380f9f3a86d98e0693000486830f1b"

_SOURCE_ARTIFACT_FIELDS = frozenset({"code", "name", "rarity", "role", "stats"})


class ArtifactRepositoryError(ValueError):
    """An actionable artifact integrity, selection, or stat failure."""

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = required_text(code, "Artifact repository error code")
        self.path = required_text(path, "Artifact repository error path")
        self.message = required_text(message, "Artifact repository error message")
        super().__init__(f"{self.code} at {self.path}: {self.message}")


class ArtifactEffectDataState(StrEnum):
    NOT_APPLICABLE = "not-applicable"
    UNAVAILABLE_IN_SNAPSHOT = "unavailable-in-snapshot"


def _source_text(value: object, path: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        qualifier = "a string" if allow_empty else "a non-empty string"
        raise ArtifactRepositoryError("missing-required-text", path, f"Expected {qualifier}.")
    return value.strip()


def _source_object(value: object, path: str) -> FrozenJsonObject:
    if not isinstance(value, FrozenJsonObject):
        raise ArtifactRepositoryError("invalid-rich-field", path, "Expected an object.")
    return value


def _nonnegative_number(value: object, path: str) -> int | float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ArtifactRepositoryError(
            "invalid-number",
            path,
            "Expected a finite non-negative number.",
        )
    return value


def _optional_nonnegative_number(value: object, path: str) -> int | float | None:
    return None if value is None else _nonnegative_number(value, path)


def _artifact_slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-") or "record"


def artifact_stable_id(source_code: object, name: object) -> str:
    code = _source_text(source_code, "artifact.code")
    artifact_name = _source_text(name, "artifact.name")
    digest = hashlib.sha256(artifact_name.encode("utf-8")).hexdigest()[:8]
    return f"artifact.fribbels.{_artifact_slug(code)}.{_artifact_slug(artifact_name)}.{digest}"


def _javascript_round_tenth(value: int | float) -> int | float:
    rounded = math.floor(value * 10 + 0.5) / 10
    return int(rounded) if rounded.is_integer() else rounded


def calculate_artifact_flat_stat(
    base_value: object,
    level: object,
) -> int | float:
    """Match pinned Fribbels interpolation plus ``Math.round(value*10)/10``."""

    base = _nonnegative_number(base_value, "artifact.baseStat")
    if isinstance(level, bool) or not isinstance(level, int):
        raise ArtifactRepositoryError("invalid-level", "artifact.level", "Level must be an integer.")
    if not 0 <= level <= ARTIFACT_MAX_LEVEL:
        raise ArtifactRepositoryError(
            "invalid-level",
            "artifact.level",
            f"Level must be from 0 through {ARTIFACT_MAX_LEVEL}.",
        )
    maximum = base * ARTIFACT_MAX_STAT_MULTIPLIER
    leveled = (maximum - base) * (level / ARTIFACT_LEVEL_DIVISOR) + base
    return _javascript_round_tenth(leveled)


@dataclass(frozen=True, slots=True)
class ArtifactFlatStats:
    attack: int | float = 0
    health: int | float = 0
    defense: int | float = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "attack", _nonnegative_number(self.attack, "artifactStats.attack"))
        object.__setattr__(self, "health", _nonnegative_number(self.health, "artifactStats.health"))
        object.__setattr__(self, "defense", _nonnegative_number(self.defense, "artifactStats.defense"))


@dataclass(frozen=True, slots=True)
class ArtifactStatOverrides:
    attack: int | float | None = None
    health: int | float | None = None
    defense: int | float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "attack",
            _optional_nonnegative_number(self.attack, "artifactOverrides.attack"),
        )
        object.__setattr__(
            self,
            "health",
            _optional_nonnegative_number(self.health, "artifactOverrides.health"),
        )
        object.__setattr__(
            self,
            "defense",
            _optional_nonnegative_number(self.defense, "artifactOverrides.defense"),
        )

    @property
    def is_empty(self) -> bool:
        return self.attack is None and self.health is None and self.defense is None

    def apply(self, calculated: ArtifactFlatStats) -> ArtifactFlatStats:
        if not isinstance(calculated, ArtifactFlatStats):
            raise ArtifactRepositoryError(
                "invalid-calculated-stats",
                "artifactStats",
                "Expected ArtifactFlatStats.",
            )
        return ArtifactFlatStats(
            attack=calculated.attack if self.attack is None else self.attack,
            health=calculated.health if self.health is None else self.health,
            defense=calculated.defense if self.defense is None else self.defense,
        )


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    definition: ArtifactDefinition
    source_key: str
    source_code: str
    rarity: int
    role: str
    base_defense: int | float
    max_defense: int | float
    raw_stats: FrozenJsonObject
    unknown_fields: FrozenJsonObject
    raw_source: FrozenJsonObject

    def __post_init__(self) -> None:
        if not isinstance(self.definition, ArtifactDefinition):
            raise ArtifactRepositoryError(
                "invalid-definition",
                "artifact.definition",
                "Expected ArtifactDefinition.",
            )
        object.__setattr__(self, "source_key", _source_text(self.source_key, "artifact.sourceKey"))
        object.__setattr__(self, "source_code", _source_text(self.source_code, "artifact.sourceCode"))
        object.__setattr__(self, "role", _source_text(self.role, "artifact.role", allow_empty=True))
        if isinstance(self.rarity, bool) or not isinstance(self.rarity, int) or not 1 <= self.rarity <= 5:
            raise ArtifactRepositoryError(
                "invalid-rarity",
                f"artifacts[{self.source_key!r}].rarity",
                "Rarity must be an integer from 1 through 5.",
            )
        object.__setattr__(
            self,
            "base_defense",
            _nonnegative_number(self.base_defense, f"artifacts[{self.source_key!r}].baseDefense"),
        )
        object.__setattr__(
            self,
            "max_defense",
            _nonnegative_number(self.max_defense, f"artifacts[{self.source_key!r}].maxDefense"),
        )
        for field_name in ("raw_stats", "unknown_fields", "raw_source"):
            if not isinstance(getattr(self, field_name), FrozenJsonObject):
                raise ArtifactRepositoryError(
                    "invalid-rich-field",
                    f"artifacts[{self.source_key!r}].{field_name}",
                    "Expected an immutable JSON object.",
                )

    @property
    def artifact_id(self) -> str:
        return self.definition.artifact_id

    @property
    def dense_id(self) -> int | None:
        return self.definition.dense_id

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def max_level(self) -> int:
        return self.definition.max_level

    @property
    def base_flat_stats(self) -> ArtifactFlatStats:
        return ArtifactFlatStats(
            attack=self.definition.base_attack,
            health=self.definition.base_health,
            defense=self.base_defense,
        )

    @property
    def max_flat_stats(self) -> ArtifactFlatStats:
        return ArtifactFlatStats(
            attack=self.definition.max_attack,
            health=self.definition.max_health,
            defense=self.max_defense,
        )


def _artifact_unknown_fields(raw_source: FrozenJsonObject) -> FrozenJsonObject:
    return FrozenJsonObject(tuple(
        (key, value) for key, value in raw_source.entries if key not in _SOURCE_ARTIFACT_FIELDS
    ))


def _artifact_record(
    definition: ArtifactDefinition,
    source_key: str,
    raw_source: FrozenJsonObject,
) -> ArtifactRecord:
    path = f"artifacts[{source_key!r}]"
    name = _source_text(raw_source.get("name"), f"{path}.name")
    code = _source_text(raw_source.get("code"), f"{path}.code")
    if name != source_key or definition.name != name:
        raise ArtifactRepositoryError(
            "name-drift",
            f"{path}.name",
            "Sidecar key, source name, and canonical name must match exactly.",
        )
    expected_id = artifact_stable_id(code, name)
    if definition.artifact_id != expected_id:
        raise ArtifactRepositoryError(
            "artifact-id-drift",
            f"{path}.code",
            f"Expected canonical artifact ID {expected_id!r}; received {definition.artifact_id!r}.",
        )
    rarity = raw_source.get("rarity")
    role = raw_source.get("role")
    stats = _source_object(raw_source.get("stats"), f"{path}.stats")
    attack = _nonnegative_number(stats.get("attack"), f"{path}.stats.attack")
    health = _nonnegative_number(stats.get("health"), f"{path}.stats.health")
    defense = _nonnegative_number(stats.get("defense"), f"{path}.stats.defense")
    if definition.max_level != ARTIFACT_MAX_LEVEL:
        raise ArtifactRepositoryError(
            "max-level-drift",
            f"{path}.maxLevel",
            f"Expected maximum level {ARTIFACT_MAX_LEVEL}; received {definition.max_level}.",
        )
    expected_canonical = (attack, health, attack * 13, health * 13)
    actual_canonical = (
        definition.base_attack,
        definition.base_health,
        definition.max_attack,
        definition.max_health,
    )
    if actual_canonical != expected_canonical:
        raise ArtifactRepositoryError(
            "stat-drift",
            f"{path}.stats",
            f"Expected canonical Attack/Health relationship {expected_canonical!r}; "
            f"received {actual_canonical!r}.",
        )
    return ArtifactRecord(
        definition=definition,
        source_key=source_key,
        source_code=code,
        rarity=rarity,
        role=role,
        base_defense=defense,
        max_defense=defense * ARTIFACT_MAX_STAT_MULTIPLIER,
        raw_stats=stats,
        unknown_fields=_artifact_unknown_fields(raw_source),
        raw_source=raw_source,
    )


@dataclass(frozen=True, slots=True)
class ArtifactSelection:
    artifact: ArtifactRecord | None = None
    level: int | None = None
    limit_breaks: int | None = None
    overrides: ArtifactStatOverrides = ArtifactStatOverrides()

    def __post_init__(self) -> None:
        if not isinstance(self.overrides, ArtifactStatOverrides):
            raise ArtifactRepositoryError(
                "invalid-overrides",
                "artifactSelection.overrides",
                "Expected ArtifactStatOverrides.",
            )
        if self.artifact is None:
            if self.level is not None or self.limit_breaks is not None or not self.overrides.is_empty:
                raise ArtifactRepositoryError(
                    "no-artifact-configuration",
                    "artifactSelection",
                    "No-artifact selection cannot have a level, limit breaks, or stat overrides.",
                )
            return
        if not isinstance(self.artifact, ArtifactRecord):
            raise ArtifactRepositoryError(
                "invalid-artifact-record",
                "artifactSelection.artifact",
                "Expected ArtifactRecord or null.",
            )
        if isinstance(self.level, bool) or not isinstance(self.level, int):
            raise ArtifactRepositoryError(
                "invalid-level",
                "artifactSelection.level",
                "A selected artifact requires an integer level.",
            )
        if not 0 <= self.level <= self.artifact.max_level:
            raise ArtifactRepositoryError(
                "invalid-level",
                "artifactSelection.level",
                f"Level must be from 0 through {self.artifact.max_level}.",
            )
        if self.limit_breaks is not None:
            if (
                isinstance(self.limit_breaks, bool)
                or not isinstance(self.limit_breaks, int)
                or not 0 <= self.limit_breaks <= 5
            ):
                raise ArtifactRepositoryError(
                    "invalid-limit-breaks",
                    "artifactSelection.limitBreaks",
                    "Limit breaks must be an integer from 0 through 5 or null.",
                )
            raise ArtifactRepositoryError(
                "limit-break-data-unavailable",
                "artifactSelection.limitBreaks",
                "The pinned artifact snapshot has no limit-break effect table.",
            )

    @property
    def artifact_id(self) -> str | None:
        return None if self.artifact is None else self.artifact.artifact_id

    @property
    def calculated_flat_stats(self) -> ArtifactFlatStats:
        if self.artifact is None:
            return ArtifactFlatStats()
        assert self.level is not None
        base = self.artifact.base_flat_stats
        return ArtifactFlatStats(
            attack=calculate_artifact_flat_stat(base.attack, self.level),
            health=calculate_artifact_flat_stat(base.health, self.level),
            defense=calculate_artifact_flat_stat(base.defense, self.level),
        )

    @property
    def flat_stats(self) -> ArtifactFlatStats:
        return self.overrides.apply(self.calculated_flat_stats)

    @property
    def effect_data_state(self) -> ArtifactEffectDataState:
        return (
            ArtifactEffectDataState.NOT_APPLICABLE
            if self.artifact is None
            else ArtifactEffectDataState.UNAVAILABLE_IN_SNAPSHOT
        )

    @property
    def effect_value(self) -> None:
        return None

    def to_artifact_only_modifiers(self) -> HeroModifiers:
        return HeroModifiers(
            artifact_id=self.artifact_id,
            artifact_level=self.level,
            artifact_limit_breaks=self.limit_breaks,
            artifact_attack_override=self.overrides.attack,
            artifact_health_override=self.overrides.health,
            artifact_defense_override=self.overrides.defense,
        )


class ArtifactRepository:
    """Validated artifact records with stable identity and selection operations."""

    __slots__ = (
        "_artifacts",
        "_by_id",
        "_by_source_code",
        "_sealed",
    )

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("ArtifactRepository is immutable after construction.")
        object.__setattr__(self, name, value)

    def __init__(
        self,
        catalog: CharacterCatalogDocument,
        source_snapshot: CharacterSourceSnapshotDocument,
    ) -> None:
        if not isinstance(catalog, CharacterCatalogDocument):
            raise ArtifactRepositoryError("invalid-catalog", "catalog", "Expected CharacterCatalogDocument.")
        if not isinstance(source_snapshot, CharacterSourceSnapshotDocument):
            raise ArtifactRepositoryError(
                "invalid-source-snapshot",
                "sourceSnapshot",
                "Expected CharacterSourceSnapshotDocument.",
            )
        if catalog.generated_at != source_snapshot.generated_at:
            raise ArtifactRepositoryError(
                "snapshot-drift",
                "generatedAt",
                "Catalog and source snapshot build timestamps differ.",
            )
        if catalog.source.to_dict() != source_snapshot.source.to_dict():
            raise ArtifactRepositoryError(
                "source-provenance-drift",
                "source",
                "Catalog and source snapshot provenance differ.",
            )
        if len(catalog.artifacts) != len(source_snapshot.artifacts):
            raise ArtifactRepositoryError(
                "artifact-count-drift",
                "artifacts",
                f"Catalog contains {len(catalog.artifacts)} artifacts but source snapshot contains "
                f"{len(source_snapshot.artifacts)}.",
            )

        canonical_by_id = {artifact.artifact_id: artifact for artifact in catalog.artifacts}
        records: list[ArtifactRecord] = []
        matched_ids: set[str] = set()
        for source_key in source_snapshot.artifacts:
            raw_source = _source_object(
                source_snapshot.artifacts[source_key],
                f"artifacts[{source_key!r}]",
            )
            code = _source_text(raw_source.get("code"), f"artifacts[{source_key!r}].code")
            name = _source_text(raw_source.get("name"), f"artifacts[{source_key!r}].name")
            expected_id = artifact_stable_id(code, name)
            definition = canonical_by_id.get(expected_id)
            if definition is None:
                raise ArtifactRepositoryError(
                    "missing-canonical-artifact",
                    f"artifacts[{source_key!r}]",
                    f"Canonical artifact {expected_id!r} is missing.",
                )
            if expected_id in matched_ids:
                raise ArtifactRepositoryError(
                    "duplicate-source-artifact",
                    f"artifacts[{source_key!r}]",
                    f"Multiple source records map to {expected_id!r}.",
                )
            records.append(_artifact_record(definition, source_key, raw_source))
            matched_ids.add(expected_id)

        unmatched = sorted(set(canonical_by_id) - matched_ids)
        if unmatched:
            raise ArtifactRepositoryError(
                "missing-source-artifact",
                "artifacts",
                f"Canonical artifacts have no source records: {', '.join(unmatched)}.",
            )

        by_id: dict[str, ArtifactRecord] = {}
        by_source_code: dict[str, list[ArtifactRecord]] = defaultdict(list)
        for record in records:
            folded_id = record.artifact_id.casefold()
            if folded_id in by_id:
                raise ArtifactRepositoryError(
                    "duplicate-artifact-id",
                    record.artifact_id,
                    "Stable artifact IDs collide case-insensitively.",
                )
            by_id[folded_id] = record
            by_source_code[record.source_code.casefold()].append(record)

        self._artifacts = tuple(sorted(
            records,
            key=lambda record: (normalize_character_search_text(record.name), record.artifact_id),
        ))
        self._by_id = MappingProxyType(by_id)
        self._by_source_code = MappingProxyType({
            code: tuple(sorted(values, key=lambda record: record.artifact_id))
            for code, values in by_source_code.items()
        })
        self._sealed = True

    @classmethod
    def from_bundled(cls) -> "ArtifactRepository":
        return cls(
            load_bundled_character_catalog(),
            load_bundled_character_source_snapshot(),
        )

    def __len__(self) -> int:
        return len(self._artifacts)

    @property
    def artifacts(self) -> tuple[ArtifactRecord, ...]:
        return self._artifacts

    def get(self, artifact_id: object) -> ArtifactRecord:
        if not isinstance(artifact_id, str) or not artifact_id.strip():
            raise ArtifactRepositoryError(
                "artifact-not-found",
                "artifactId",
                f"Stable artifact ID was not found: {artifact_id!r}.",
            )
        record = self._by_id.get(artifact_id.strip().casefold())
        if record is None:
            raise ArtifactRepositoryError(
                "artifact-not-found",
                "artifactId",
                f"Stable artifact ID was not found: {artifact_id!r}.",
            )
        return record

    def source_code_matches(self, source_code: object) -> tuple[ArtifactRecord, ...]:
        if not isinstance(source_code, str) or not source_code.strip():
            return ()
        return self._by_source_code.get(source_code.strip().casefold(), ())

    def select_none(self) -> ArtifactSelection:
        return ArtifactSelection()

    def select(
        self,
        artifact_id: object,
        *,
        level: object,
        limit_breaks: object = None,
        overrides: ArtifactStatOverrides | None = None,
    ) -> ArtifactSelection:
        if overrides is not None and not isinstance(overrides, ArtifactStatOverrides):
            raise ArtifactRepositoryError(
                "invalid-overrides",
                "artifactSelection.overrides",
                "Expected ArtifactStatOverrides or null.",
            )
        return ArtifactSelection(
            artifact=self.get(artifact_id),
            level=level,
            limit_breaks=limit_breaks,
            overrides=ArtifactStatOverrides() if overrides is None else overrides,
        )

    def select_from_modifiers(self, modifiers: object) -> ArtifactSelection:
        if not isinstance(modifiers, HeroModifiers):
            raise ArtifactRepositoryError(
                "invalid-modifiers",
                "modifiers",
                "Expected HeroModifiers.",
            )
        if modifiers.artifact_id is None:
            return self.select_none()
        return self.select(
            modifiers.artifact_id,
            level=modifiers.artifact_level,
            limit_breaks=modifiers.artifact_limit_breaks,
            overrides=ArtifactStatOverrides(
                attack=modifiers.artifact_attack_override,
                health=modifiers.artifact_health_override,
                defense=modifiers.artifact_defense_override,
            ),
        )


def load_bundled_artifact_repository() -> ArtifactRepository:
    return ArtifactRepository.from_bundled()


__all__ = [
    "ARTIFACT_LEVEL_DIVISOR",
    "ARTIFACT_LEVEL_ROUNDING_DIGITS",
    "ARTIFACT_MAX_LEVEL",
    "ARTIFACT_MAX_STAT_MULTIPLIER",
    "FRIBBELS_ARTIFACT_BACKEND_LOGIC_GIT_BLOB_SHA1",
    "FRIBBELS_ARTIFACT_BACKEND_LOGIC_PATH",
    "FRIBBELS_ARTIFACT_LOGIC_GIT_BLOB_SHA1",
    "FRIBBELS_ARTIFACT_LOGIC_PATH",
    "FRIBBELS_ROUNDING_LOGIC_GIT_BLOB_SHA1",
    "FRIBBELS_ROUNDING_LOGIC_PATH",
    "ArtifactEffectDataState",
    "ArtifactFlatStats",
    "ArtifactRecord",
    "ArtifactRepository",
    "ArtifactRepositoryError",
    "ArtifactSelection",
    "ArtifactStatOverrides",
    "artifact_stable_id",
    "calculate_artifact_flat_stat",
    "load_bundled_artifact_repository",
]
