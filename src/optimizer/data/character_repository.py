"""Immutable, offline character repository and deterministic search index."""

from __future__ import annotations

import json
import math
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from urllib.parse import urlsplit

from src.optimizer.data.character_snapshot import (
    CharacterSourceSnapshotDocument,
    _normalize_hero,
    bundled_character_data_path,
    load_bundled_character_catalog,
    load_bundled_character_source_snapshot,
)
from src.optimizer.data.schema_common import (
    FrozenJson,
    FrozenJsonArray,
    FrozenJsonObject,
    freeze_json_object,
    required_text,
)
from src.optimizer.data.schemas import CharacterCatalogDocument
from src.optimizer.domain import HeroBaseProfile, HeroDefinition


DEFAULT_CHARACTER_SEARCH_LIMIT = 20
MAX_CHARACTER_SEARCH_LIMIT = 100
MANUAL_HERO_SOURCE_FILENAME = "manual-heroes-v1.json"
HERO_PLACEHOLDER_IMAGE_REFERENCE = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 128 128'%3E"
    "%3Crect width='128' height='128' rx='18' fill='%23242a38'/%3E"
    "%3Ccircle cx='64' cy='47' r='22' fill='%23596378'/%3E"
    "%3Cpath d='M25 112c4-25 18-38 39-38s35 13 39 38' fill='%23596378'/%3E%3C/svg%3E"
)

_SOURCE_HERO_FIELDS = frozenset({
    "_id",
    "assets",
    "attribute",
    "calculatedStatus",
    "code",
    "ex_equip",
    "name",
    "rarity",
    "role",
    "self_devotion",
    "skills",
    "zodiac",
})
_PROFILE_RELATIONSHIPS = (
    ("lv50FiveStarFullyAwakened", 50, 5),
    ("lv60SixStarFullyAwakened", 60, 6),
)


class CharacterRepositoryError(ValueError):
    """An actionable catalog/sidecar integrity or rich-field failure."""

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = required_text(code, "Character repository error code")
        self.path = required_text(path, "Character repository error path")
        self.message = required_text(message, "Character repository error message")
        super().__init__(f"{self.code} at {self.path}: {self.message}")


class CharacterAliasCollisionError(CharacterRepositoryError):
    """Two or more heroes claim the same normalized evidence-backed alias."""

    def __init__(self, normalized_alias: str, hero_ids: Sequence[str]) -> None:
        ids = tuple(sorted(set(hero_ids)))
        self.normalized_alias = normalized_alias
        self.hero_ids = ids
        super().__init__(
            "alias-collision",
            f"aliases[{normalized_alias!r}]",
            f"Normalized alias is claimed by: {', '.join(ids)}.",
        )


class CharacterNotFoundError(KeyError):
    """A required stable hero ID is absent from the repository."""

    def __init__(self, hero_id: object) -> None:
        self.hero_id = hero_id
        super().__init__(f"Character hero ID was not found: {hero_id!r}.")


class CharacterAliasKind(StrEnum):
    NAME = "name"
    SOURCE_ID = "source-id"
    SOURCE_CODE = "source-code"


def normalize_character_search_text(value: object) -> str:
    """NFKC-casefold text and collapse punctuation/whitespace to one separator."""

    if not isinstance(value, str):
        raise ValueError("Character search text must be a string.")
    folded = unicodedata.normalize("NFKC", value).casefold()
    output: list[str] = []
    pending_separator = False
    for character in folded:
        if character.isalnum():
            if pending_separator and output:
                output.append(" ")
            output.append(character)
            pending_separator = False
        else:
            pending_separator = bool(output)
    return "".join(output)


@dataclass(frozen=True, slots=True)
class CharacterAlias:
    kind: CharacterAliasKind
    value: str
    normalized: str = ""

    def __post_init__(self) -> None:
        try:
            kind = self.kind if isinstance(self.kind, CharacterAliasKind) else CharacterAliasKind(self.kind)
        except (TypeError, ValueError):
            raise CharacterRepositoryError("invalid-alias-kind", "alias.kind", "Alias kind is unsupported.") from None
        value = _source_text(self.value, "alias.value")
        normalized = normalize_character_search_text(value)
        if not normalized:
            raise CharacterRepositoryError("invalid-alias", "alias.value", "Alias has no searchable characters.")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "normalized", normalized)


@dataclass(frozen=True, slots=True)
class CharacterPortraitReferences:
    icon: str
    image: str
    thumbnail: str
    source_icon: str | None
    source_image: str | None
    source_thumbnail: str | None
    source_assets: FrozenJsonObject

    def __post_init__(self) -> None:
        for field_name in ("icon", "image", "thumbnail"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise CharacterRepositoryError(
                    "invalid-portrait-reference",
                    f"portraits.{field_name}",
                    "Effective portrait references must be non-empty strings.",
                )
        for field_name in ("source_icon", "source_image", "source_thumbnail"):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, str) or not value):
                raise CharacterRepositoryError(
                    "invalid-portrait-reference",
                    f"portraits.{field_name}",
                    "Source portrait references must be non-empty strings or null.",
                )
        if not isinstance(self.source_assets, FrozenJsonObject):
            raise CharacterRepositoryError(
                "invalid-portrait-reference",
                "portraits.sourceAssets",
                "Source assets must remain an immutable JSON object.",
            )

    @property
    def uses_placeholder(self) -> bool:
        return any(
            value == HERO_PLACEHOLDER_IMAGE_REFERENCE
            for value in (self.icon, self.image, self.thumbnail)
        )


@dataclass(frozen=True, slots=True)
class CharacterHeroRecord:
    definition: HeroDefinition
    source_key: str
    source_id: str
    source_code: str
    aliases: tuple[CharacterAlias, ...]
    element: str
    role: str
    rarity: int
    zodiac: str
    portraits: CharacterPortraitReferences
    skills: FrozenJsonObject
    self_devotion: FrozenJsonObject
    exclusive_equipment: FrozenJsonArray
    unknown_fields: FrozenJsonObject
    raw_source: FrozenJsonObject

    def __post_init__(self) -> None:
        if not isinstance(self.definition, HeroDefinition):
            raise CharacterRepositoryError("invalid-definition", "hero.definition", "Expected HeroDefinition.")
        aliases = tuple(self.aliases)
        if len(aliases) != 3 or {alias.kind for alias in aliases} != set(CharacterAliasKind):
            raise CharacterRepositoryError(
                "invalid-aliases", self.definition.hero_id, "Expected name, source-ID, and source-code aliases."
            )
        if not isinstance(self.portraits, CharacterPortraitReferences):
            raise CharacterRepositoryError("invalid-portraits", self.definition.hero_id, "Portrait references are invalid.")
        for field_name in ("skills", "self_devotion", "unknown_fields", "raw_source"):
            if not isinstance(getattr(self, field_name), FrozenJsonObject):
                raise CharacterRepositoryError("invalid-rich-field", f"{self.definition.hero_id}.{field_name}", "Expected immutable JSON object.")
        if not isinstance(self.exclusive_equipment, FrozenJsonArray):
            raise CharacterRepositoryError("invalid-rich-field", f"{self.definition.hero_id}.exclusive_equipment", "Expected immutable JSON array.")
        object.__setattr__(self, "source_key", _source_text(self.source_key, "hero.sourceKey"))
        object.__setattr__(self, "source_id", _source_text(self.source_id, "hero.sourceId"))
        object.__setattr__(self, "source_code", _source_text(self.source_code, "hero.sourceCode"))
        object.__setattr__(self, "element", _source_text(self.element, "hero.element"))
        object.__setattr__(self, "role", _source_text(self.role, "hero.role"))
        object.__setattr__(self, "zodiac", _source_text(self.zodiac, "hero.zodiac"))
        if isinstance(self.rarity, bool) or not isinstance(self.rarity, int) or not 1 <= self.rarity <= 6:
            raise CharacterRepositoryError("invalid-rarity", f"{self.definition.hero_id}.rarity", "Rarity must be an integer from 1 through 6.")
        object.__setattr__(self, "aliases", aliases)

    @property
    def hero_id(self) -> str:
        return self.definition.hero_id

    @property
    def dense_id(self) -> int | None:
        return self.definition.dense_id

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def base_profiles(self) -> tuple[HeroBaseProfile, ...]:
        return self.definition.base_profiles


def _source_text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CharacterRepositoryError("missing-required-text", path, "Expected a non-empty string.")
    return value.strip()


def _source_object(value: FrozenJson | object, path: str) -> FrozenJsonObject:
    if not isinstance(value, FrozenJsonObject):
        raise CharacterRepositoryError("invalid-rich-field", path, "Expected an object.")
    return value


def _source_array(value: FrozenJson | object, path: str) -> FrozenJsonArray:
    if not isinstance(value, FrozenJsonArray):
        raise CharacterRepositoryError("invalid-rich-field", path, "Expected an array.")
    return value


def _source_number(value: object, path: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CharacterRepositoryError("invalid-rich-field", path, "Expected a finite number.")
    return value


def _validate_skills(value: FrozenJson | object, path: str) -> FrozenJsonObject:
    skills = _source_object(value, path)
    for skill_name in ("S1", "S2", "S3"):
        skill = _source_object(skills.get(skill_name), f"{path}.{skill_name}")
        hit_types = _source_array(skill.get("hitTypes"), f"{path}.{skill_name}.hitTypes")
        if not all(isinstance(item, str) and item.strip() for item in hit_types):
            raise CharacterRepositoryError(
                "invalid-rich-field", f"{path}.{skill_name}.hitTypes", "Hit types must be non-empty strings."
            )
        options = _source_array(skill.get("options"), f"{path}.{skill_name}.options")
        if not all(isinstance(item, FrozenJsonObject) for item in options):
            raise CharacterRepositoryError(
                "invalid-rich-field", f"{path}.{skill_name}.options", "Skill options must be objects."
            )
    return skills


def _validate_self_devotion(value: FrozenJson | object, path: str) -> FrozenJsonObject:
    devotion = _source_object(value, path)
    _source_text(devotion.get("type"), f"{path}.type")
    grades = _source_object(devotion.get("grades"), f"{path}.grades")
    if not grades:
        raise CharacterRepositoryError("invalid-rich-field", f"{path}.grades", "At least one imprint grade is required.")
    for grade, amount in grades.items():
        _source_text(grade, f"{path}.grades")
        _source_number(amount, f"{path}.grades.{grade}")
    return devotion


def _validate_exclusive_equipment(value: FrozenJson | object, path: str) -> FrozenJsonArray:
    equipment = _source_array(value, path)
    for index, item in enumerate(equipment):
        record = _source_object(item, f"{path}[{index}]")
        stat = _source_object(record.get("stat"), f"{path}[{index}].stat")
        _source_text(stat.get("type"), f"{path}[{index}].stat.type")
        _source_number(stat.get("value"), f"{path}[{index}].stat.value")
    return equipment


def _source_reference(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _structurally_usable_asset_reference(value: str) -> bool:
    if value.startswith("data:image/"):
        return True
    parsed = urlsplit(value)
    if parsed.scheme in {"http", "https"}:
        return bool(parsed.netloc and parsed.path)
    if parsed.scheme == "file":
        return bool(parsed.path)
    return False


def _effective_asset_reference(
    value: object,
    usable: Callable[[str], bool] | None,
) -> tuple[str, str | None]:
    source = _source_reference(value)
    if source is None or not _structurally_usable_asset_reference(source):
        return HERO_PLACEHOLDER_IMAGE_REFERENCE, source
    if usable is not None:
        try:
            if not usable(source):
                return HERO_PLACEHOLDER_IMAGE_REFERENCE, source
        except Exception:
            return HERO_PLACEHOLDER_IMAGE_REFERENCE, source
    return source, source


def _portrait_references(
    raw_source: FrozenJsonObject,
    usable: Callable[[str], bool] | None,
) -> CharacterPortraitReferences:
    raw_assets = raw_source.get("assets")
    source_assets = raw_assets if isinstance(raw_assets, FrozenJsonObject) else FrozenJsonObject()
    icon, source_icon = _effective_asset_reference(source_assets.get("icon"), usable)
    image, source_image = _effective_asset_reference(source_assets.get("image"), usable)
    thumbnail, source_thumbnail = _effective_asset_reference(source_assets.get("thumbnail"), usable)
    return CharacterPortraitReferences(
        icon=icon,
        image=image,
        thumbnail=thumbnail,
        source_icon=source_icon,
        source_image=source_image,
        source_thumbnail=source_thumbnail,
        source_assets=source_assets,
    )


def _unknown_fields(raw_source: FrozenJsonObject) -> FrozenJsonObject:
    return FrozenJsonObject(tuple(
        (key, value) for key, value in raw_source.entries if key not in _SOURCE_HERO_FIELDS
    ))


def _validate_profile_relationships(hero: HeroDefinition, source_id: str, raw_source: FrozenJsonObject) -> None:
    status = _source_object(raw_source.get("calculatedStatus"), f"heroes[{hero.name!r}].calculatedStatus")
    expected: set[tuple[str, int, int]] = set()
    for source_profile, level, stars in _PROFILE_RELATIONSHIPS:
        _source_object(status.get(source_profile), f"heroes[{hero.name!r}].calculatedStatus.{source_profile}")
        expected.add((f"profile.fribbels.{source_id}.{level}.{stars}", level, stars))
    actual = {(profile.profile_id, profile.level, profile.stars) for profile in hero.base_profiles}
    if actual != expected:
        raise CharacterRepositoryError(
            "profile-drift",
            f"heroes[{hero.name!r}].baseProfiles",
            f"Expected profile relationships {sorted(expected)!r}; received {sorted(actual)!r}.",
        )


def _hero_record(
    hero: HeroDefinition,
    source_key: str,
    raw_source: FrozenJsonObject,
    usable_asset_reference: Callable[[str], bool] | None,
) -> CharacterHeroRecord:
    path = f"heroes[{source_key!r}]"
    source_name = _source_text(raw_source.get("name"), f"{path}.name")
    source_id = _source_text(raw_source.get("_id"), f"{path}._id")
    source_code = _source_text(raw_source.get("code"), f"{path}.code")
    if source_key != source_name or hero.name != source_name:
        raise CharacterRepositoryError(
            "name-drift", f"{path}.name", "Sidecar key, source name, and canonical name must match exactly."
        )
    expected_hero_id = f"hero.fribbels.{source_id}"
    if hero.hero_id != expected_hero_id:
        raise CharacterRepositoryError(
            "hero-id-drift", f"{path}._id", f"Expected canonical hero ID {expected_hero_id!r}; received {hero.hero_id!r}."
        )
    _validate_profile_relationships(hero, source_id, raw_source)
    aliases = (
        CharacterAlias(CharacterAliasKind.NAME, source_name),
        CharacterAlias(CharacterAliasKind.SOURCE_ID, source_id),
        CharacterAlias(CharacterAliasKind.SOURCE_CODE, source_code),
    )
    rarity = raw_source.get("rarity")
    if isinstance(rarity, bool) or not isinstance(rarity, int):
        raise CharacterRepositoryError("invalid-rarity", f"{path}.rarity", "Expected an integer.")
    return CharacterHeroRecord(
        definition=hero,
        source_key=source_key,
        source_id=source_id,
        source_code=source_code,
        aliases=aliases,
        element=_source_text(raw_source.get("attribute"), f"{path}.attribute"),
        role=_source_text(raw_source.get("role"), f"{path}.role"),
        rarity=rarity,
        zodiac=_source_text(raw_source.get("zodiac"), f"{path}.zodiac"),
        portraits=_portrait_references(raw_source, usable_asset_reference),
        skills=_validate_skills(raw_source.get("skills"), f"{path}.skills"),
        self_devotion=_validate_self_devotion(raw_source.get("self_devotion"), f"{path}.self_devotion"),
        exclusive_equipment=_validate_exclusive_equipment(raw_source.get("ex_equip"), f"{path}.ex_equip"),
        unknown_fields=_unknown_fields(raw_source),
        raw_source=raw_source,
    )


class CharacterRepository:
    """Validated character views plus immutable exact and ranked search indexes."""

    __slots__ = ("_alias_index", "_hero_id_index", "_heroes", "_name_order", "_sealed")

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("CharacterRepository is immutable after construction.")
        object.__setattr__(self, name, value)

    def __init__(
        self,
        catalog: CharacterCatalogDocument,
        source_snapshot: CharacterSourceSnapshotDocument,
        *,
        manual_heroes: Mapping[str, object] | None = None,
        usable_asset_reference: Callable[[str], bool] | None = None,
    ) -> None:
        if not isinstance(catalog, CharacterCatalogDocument):
            raise CharacterRepositoryError("invalid-catalog", "catalog", "Expected CharacterCatalogDocument.")
        if not isinstance(source_snapshot, CharacterSourceSnapshotDocument):
            raise CharacterRepositoryError(
                "invalid-source-snapshot", "sourceSnapshot", "Expected CharacterSourceSnapshotDocument."
            )
        if usable_asset_reference is not None and not callable(usable_asset_reference):
            raise CharacterRepositoryError(
                "invalid-asset-policy", "usableAssetReference", "Expected a callable or null."
            )
        if catalog.generated_at != source_snapshot.generated_at:
            raise CharacterRepositoryError(
                "snapshot-drift", "generatedAt", "Catalog and source snapshot build timestamps differ."
            )
        if catalog.source.to_dict() != source_snapshot.source.to_dict():
            raise CharacterRepositoryError(
                "source-provenance-drift", "source", "Catalog and source snapshot provenance differ."
            )
        if len(catalog.heroes) != len(source_snapshot.heroes):
            raise CharacterRepositoryError(
                "hero-count-drift",
                "heroes",
                f"Catalog contains {len(catalog.heroes)} heroes but source snapshot contains {len(source_snapshot.heroes)}.",
            )

        canonical_by_id = {hero.hero_id: hero for hero in catalog.heroes}
        records: list[CharacterHeroRecord] = []
        matched_ids: set[str] = set()
        for source_key in source_snapshot.heroes:
            raw_source = _source_object(source_snapshot.heroes[source_key], f"heroes[{source_key!r}]")
            source_id = _source_text(raw_source.get("_id"), f"heroes[{source_key!r}]._id")
            expected_id = f"hero.fribbels.{source_id}"
            hero = canonical_by_id.get(expected_id)
            if hero is None:
                raise CharacterRepositoryError(
                    "missing-canonical-hero", f"heroes[{source_key!r}]", f"Canonical hero {expected_id!r} is missing."
                )
            if expected_id in matched_ids:
                raise CharacterRepositoryError(
                    "duplicate-source-id", f"heroes[{source_key!r}]._id", f"Multiple source records map to {expected_id!r}."
                )
            record = _hero_record(hero, source_key, raw_source, usable_asset_reference)
            records.append(record)
            matched_ids.add(expected_id)
        unmatched = sorted(set(canonical_by_id) - matched_ids)
        if unmatched:
            raise CharacterRepositoryError(
                "missing-source-hero", "heroes", f"Canonical heroes have no source records: {', '.join(unmatched)}."
            )

        if manual_heroes:
            first_profile_dense_id = sum(len(hero.base_profiles) for hero in catalog.heroes)
            for offset, source_key in enumerate(sorted(manual_heroes)):
                raw_source = freeze_json_object(
                    manual_heroes[source_key],
                    f"manualHeroes[{source_key!r}]",
                )
                definition = _normalize_hero(
                    source_key,
                    manual_heroes[source_key],
                    hero_dense_id=len(catalog.heroes) + offset,
                    first_profile_dense_id=first_profile_dense_id + offset * 2,
                )
                records.append(
                    _hero_record(
                        definition,
                        source_key,
                        raw_source,
                        usable_asset_reference,
                    )
                )

        hero_id_index: dict[str, CharacterHeroRecord] = {}
        alias_claims: dict[str, set[str]] = defaultdict(set)
        for record in records:
            folded_id = record.hero_id.casefold()
            if folded_id in hero_id_index:
                raise CharacterRepositoryError(
                    "duplicate-hero-id", record.hero_id, "Stable hero IDs collide case-insensitively."
                )
            hero_id_index[folded_id] = record
            for alias in record.aliases:
                alias_claims[alias.normalized].add(record.hero_id)
        collisions = sorted(
            (alias, tuple(sorted(ids))) for alias, ids in alias_claims.items() if len(ids) > 1
        )
        if collisions:
            alias, hero_ids = collisions[0]
            raise CharacterAliasCollisionError(alias, hero_ids)

        alias_index = {
            alias: hero_id_index[next(iter(ids)).casefold()]
            for alias, ids in alias_claims.items()
        }
        heroes = tuple(sorted(records, key=lambda record: record.hero_id))
        name_order = tuple(sorted(records, key=lambda record: (normalize_character_search_text(record.name), record.hero_id)))
        self._heroes = heroes
        self._name_order = name_order
        self._hero_id_index = MappingProxyType(hero_id_index)
        self._alias_index = MappingProxyType(alias_index)
        self._sealed = True

    @classmethod
    def from_bundled(
        cls,
        *,
        usable_asset_reference: Callable[[str], bool] | None = None,
    ) -> "CharacterRepository":
        manual_document = json.loads(
            bundled_character_data_path(MANUAL_HERO_SOURCE_FILENAME).read_text(
                encoding="utf-8"
            )
        )
        return cls(
            load_bundled_character_catalog(),
            load_bundled_character_source_snapshot(),
            manual_heroes=manual_document["records"],
            usable_asset_reference=usable_asset_reference,
        )

    def __len__(self) -> int:
        return len(self._heroes)

    @property
    def heroes(self) -> tuple[CharacterHeroRecord, ...]:
        return self._heroes

    def get(self, hero_id: object) -> CharacterHeroRecord:
        if not isinstance(hero_id, str) or not hero_id.strip():
            raise CharacterNotFoundError(hero_id)
        record = self._hero_id_index.get(hero_id.strip().casefold())
        if record is None:
            raise CharacterNotFoundError(hero_id)
        return record

    def find_exact(self, value: object) -> CharacterHeroRecord | None:
        if not isinstance(value, str) or not value.strip():
            return None
        stable = self._hero_id_index.get(value.strip().casefold())
        if stable is not None:
            return stable
        normalized = normalize_character_search_text(value)
        return self._alias_index.get(normalized) if normalized else None

    def search(
        self,
        query: object,
        *,
        limit: int = DEFAULT_CHARACTER_SEARCH_LIMIT,
    ) -> tuple[CharacterHeroRecord, ...]:
        if not isinstance(query, str):
            raise ValueError("Character search query must be a string.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_CHARACTER_SEARCH_LIMIT:
            raise ValueError(
                f"Character search limit must be an integer from 1 through {MAX_CHARACTER_SEARCH_LIMIT}."
            )
        normalized = normalize_character_search_text(query)
        if not normalized:
            return self._name_order[:limit]

        ranked: list[tuple[int, str, str, CharacterHeroRecord]] = []
        for record in self._heroes:
            name_alias = next(alias for alias in record.aliases if alias.kind is CharacterAliasKind.NAME)
            other_aliases = tuple(alias.normalized for alias in record.aliases if alias.kind is not CharacterAliasKind.NAME)
            if normalized == name_alias.normalized:
                rank = 0
            elif normalized in other_aliases or query.strip().casefold() == record.hero_id.casefold():
                rank = 1
            elif name_alias.normalized.startswith(normalized):
                rank = 2
            elif any(alias.startswith(normalized) for alias in other_aliases):
                rank = 3
            elif normalized in name_alias.normalized:
                rank = 4
            elif any(normalized in alias for alias in other_aliases):
                rank = 5
            else:
                continue
            ranked.append((rank, name_alias.normalized, record.hero_id, record))
        ranked.sort(key=lambda item: item[:3])
        return tuple(item[3] for item in ranked[:limit])


def load_bundled_character_repository(
    *,
    usable_asset_reference: Callable[[str], bool] | None = None,
) -> CharacterRepository:
    return CharacterRepository.from_bundled(usable_asset_reference=usable_asset_reference)


def load_bundled_runtime_character_catalog() -> CharacterCatalogDocument:
    """Return the frozen catalog with reviewed manual hero additions appended."""

    catalog = load_bundled_character_catalog()
    repository = load_bundled_character_repository()
    return CharacterCatalogDocument(
        catalog_id=catalog.catalog_id,
        generated_at=catalog.generated_at,
        source=catalog.source,
        heroes=tuple(record.definition for record in repository.heroes),
        artifacts=catalog.artifacts,
    )


__all__ = [
    "DEFAULT_CHARACTER_SEARCH_LIMIT",
    "HERO_PLACEHOLDER_IMAGE_REFERENCE",
    "MAX_CHARACTER_SEARCH_LIMIT",
    "MANUAL_HERO_SOURCE_FILENAME",
    "CharacterAlias",
    "CharacterAliasCollisionError",
    "CharacterAliasKind",
    "CharacterHeroRecord",
    "CharacterNotFoundError",
    "CharacterPortraitReferences",
    "CharacterRepository",
    "CharacterRepositoryError",
    "load_bundled_character_repository",
    "load_bundled_runtime_character_catalog",
    "normalize_character_search_text",
]
