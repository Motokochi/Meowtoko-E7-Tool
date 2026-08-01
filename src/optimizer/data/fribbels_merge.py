"""Pure identity, deduplication, and re-import merging for Fribbels gear."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import quote

from src.optimizer.data.fribbels import (
    FribbelsIssue,
    FribbelsItemProjection,
    FribbelsParseResult,
    ParsedFribbelsItem,
)
from src.optimizer.data.schema_common import (
    FrozenJsonObject,
    deterministic_json,
    freeze_json_object,
)
from src.optimizer.domain import GearItem, GearRank, ItemStatType, ReforgeMaterial


FRIBBELS_FINGERPRINT_VERSION = 1
FRIBBELS_FINGERPRINT_ALGORITHM = "sha256"


class FribbelsIdentityKind(StrEnum):
    INGAME = "ingame"
    SOURCE = "source"
    FINGERPRINT = "fingerprint"


IDENTITY_KIND_PRIORITY = (
    FribbelsIdentityKind.INGAME,
    FribbelsIdentityKind.SOURCE,
    FribbelsIdentityKind.FINGERPRINT,
)
_IDENTITY_PRIORITY_INDEX = {kind: index for index, kind in enumerate(IDENTITY_KIND_PRIORITY)}


class FribbelsMergeOutcomeKind(StrEnum):
    INSERTED = "inserted"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    CONFLICT = "conflict"


class FribbelsMergeInputError(ValueError):
    """Raised when the existing merge state violates identity invariants."""


@dataclass(frozen=True, slots=True)
class FribbelsItemIdentity:
    kind: FribbelsIdentityKind
    value: str

    def __post_init__(self) -> None:
        try:
            kind = self.kind if isinstance(self.kind, FribbelsIdentityKind) else FribbelsIdentityKind(self.kind)
        except (TypeError, ValueError):
            raise FribbelsMergeInputError("Identity kind is not supported.") from None
        if not isinstance(self.value, str) or not self.value.strip():
            raise FribbelsMergeInputError("Identity value must be a non-empty string.")
        value = self.value.strip()
        if kind is FribbelsIdentityKind.FINGERPRINT:
            if len(value) != 64 or any(character not in "0123456789abcdefABCDEF" for character in value):
                raise FribbelsMergeInputError("Fingerprint identity must be a 64-character SHA-256 digest.")
            value = value.lower()
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "value", value)

    @property
    def namespaced_key(self) -> str:
        encoded = quote(self.value, safe="")
        if self.kind is FribbelsIdentityKind.FINGERPRINT:
            return f"fribbels:identity:fingerprint-v{FRIBBELS_FINGERPRINT_VERSION}:{encoded}"
        return f"fribbels:identity:{self.kind.value}:{encoded}"


def _identity_sort_key(identity: FribbelsItemIdentity) -> tuple[int, str]:
    return (_IDENTITY_PRIORITY_INDEX[identity.kind], identity.value)


def _normalized_number(value: int | float) -> int | float:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def fribbels_fingerprint_payload(item: ParsedFribbelsItem) -> FrozenJsonObject:
    """Return the exact immutable version-1 content-fingerprint payload."""

    substats = sorted(item.substats, key=lambda stat: stat.stat_type.value)
    payload = {
        "fingerprintVersion": FRIBBELS_FINGERPRINT_VERSION,
        "slot": item.slot.value,
        "set": item.gear_set.value,
        "rank": item.rank.value,
        "itemLevel": item.item_level,
        "enhance": item.enhance,
        "main": {
            "type": item.main_stat.stat_type.value,
            "value": _normalized_number(item.main_stat.value),
            "reforgedValue": (
                None
                if item.main_stat.reforged_value is None
                else _normalized_number(item.main_stat.reforged_value)
            ),
        },
        "substats": [
            {
                "type": stat.stat_type.value,
                "value": _normalized_number(stat.value),
                "rolls": stat.rolls,
                "ingameRolls": stat.ingame_rolls,
                "modified": stat.modified,
                "reforgedValue": (
                    None
                    if stat.reforged_value is None
                    else _normalized_number(stat.reforged_value)
                ),
            }
            for stat in substats
        ],
    }
    return freeze_json_object(payload, "Fribbels fingerprint payload")


def fribbels_item_fingerprint(item: ParsedFribbelsItem) -> str:
    serialized = deterministic_json(fribbels_fingerprint_payload(item)).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def fribbels_item_identities(item: ParsedFribbelsItem) -> tuple[FribbelsItemIdentity, ...]:
    identities: list[FribbelsItemIdentity] = []
    if item.ingame_id is not None:
        identities.append(FribbelsItemIdentity(FribbelsIdentityKind.INGAME, item.ingame_id))
    if item.source_id is not None:
        identities.append(FribbelsItemIdentity(FribbelsIdentityKind.SOURCE, item.source_id))
    identities.append(
        FribbelsItemIdentity(FribbelsIdentityKind.FINGERPRINT, fribbels_item_fingerprint(item))
    )
    return tuple(identities)


def stable_item_id_from_identity(
    identity: FribbelsItemIdentity,
    *,
    occurrence: int | None = None,
) -> str:
    encoded = quote(identity.value, safe="")
    if identity.kind is FribbelsIdentityKind.FINGERPRINT:
        if isinstance(occurrence, bool) or not isinstance(occurrence, int) or occurrence < 1:
            raise FribbelsMergeInputError(
                "Fingerprint stable IDs require a positive occurrence number."
            )
        return (
            f"fribbels:item:fingerprint-v{FRIBBELS_FINGERPRINT_VERSION}:"
            f"{encoded}:{occurrence}"
        )
    if occurrence is not None:
        raise FribbelsMergeInputError("Strong identity stable IDs do not use occurrences.")
    return f"fribbels:item:{identity.kind.value}:{encoded}"


@dataclass(frozen=True, slots=True)
class FribbelsInventoryItem:
    """Pure merged item state before P01-T04 persistence."""

    stable_item_id: str
    gear_item: GearItem
    identities: tuple[FribbelsItemIdentity, ...]
    current_ingame_id: str | None
    current_source_id: str | None
    name: str | None
    rank: GearRank
    material: ReforgeMaterial | None
    equipped_by_name: str | None
    projection: FribbelsItemProjection
    source_metadata: FrozenJsonObject | Mapping[str, object]
    user_metadata: FrozenJsonObject | Mapping[str, object] = FrozenJsonObject()

    def __post_init__(self) -> None:
        if not isinstance(self.stable_item_id, str) or not self.stable_item_id.strip():
            raise FribbelsMergeInputError("Stable item ID must be a non-empty string.")
        stable_item_id = self.stable_item_id.strip()
        if not isinstance(self.gear_item, GearItem):
            raise FribbelsMergeInputError("Merged gear item must be a GearItem.")
        if self.gear_item.item_id != stable_item_id:
            raise FribbelsMergeInputError("GearItem.item_id must match stable_item_id.")
        if self.gear_item.dense_id is not None:
            raise FribbelsMergeInputError("P01-T03 merged items must not carry dense search IDs.")
        raw_identities = tuple(self.identities)
        if not raw_identities or not all(
            isinstance(identity, FribbelsItemIdentity) for identity in raw_identities
        ):
            raise FribbelsMergeInputError(
                "Merged identities must contain FribbelsItemIdentity values."
            )
        identities = tuple(sorted(set(raw_identities), key=_identity_sort_key))
        fingerprints = [
            identity for identity in identities if identity.kind is FribbelsIdentityKind.FINGERPRINT
        ]
        if len(fingerprints) != 1:
            raise FribbelsMergeInputError("Merged items must contain exactly one current fingerprint.")
        for current_value, kind, field in (
            (self.current_ingame_id, FribbelsIdentityKind.INGAME, "current_ingame_id"),
            (self.current_source_id, FribbelsIdentityKind.SOURCE, "current_source_id"),
        ):
            if current_value is None:
                continue
            if not isinstance(current_value, str) or not current_value.strip():
                raise FribbelsMergeInputError(f"{field} must be a non-empty string or null.")
            normalized_current = current_value.strip()
            if FribbelsItemIdentity(kind, normalized_current) not in identities:
                raise FribbelsMergeInputError(f"{field} must have a matching identity alias.")
            object.__setattr__(self, field, normalized_current)
        for value, field in (
            (self.name, "name"),
            (self.equipped_by_name, "equipped_by_name"),
        ):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise FribbelsMergeInputError(f"Merged {field} must be non-empty text or null.")
            if isinstance(value, str):
                object.__setattr__(self, field, value.strip())
        if not isinstance(self.rank, GearRank):
            raise FribbelsMergeInputError("Merged rank must be a GearRank.")
        if self.material is not None and not isinstance(self.material, ReforgeMaterial):
            raise FribbelsMergeInputError("Merged material must be a ReforgeMaterial or null.")
        if not isinstance(self.projection, FribbelsItemProjection):
            raise FribbelsMergeInputError("Merged projection must be FribbelsItemProjection.")
        object.__setattr__(self, "stable_item_id", stable_item_id)
        object.__setattr__(self, "identities", identities)
        object.__setattr__(
            self,
            "source_metadata",
            (
                self.source_metadata
                if isinstance(self.source_metadata, FrozenJsonObject)
                else freeze_json_object(
                    self.source_metadata,
                    "FribbelsInventoryItem.source_metadata",
                )
            ),
        )
        object.__setattr__(
            self,
            "user_metadata",
            (
                self.user_metadata
                if isinstance(self.user_metadata, FrozenJsonObject)
                else freeze_json_object(
                    self.user_metadata,
                    "FribbelsInventoryItem.user_metadata",
                )
            ),
        )

    @property
    def fingerprint(self) -> FribbelsItemIdentity:
        return next(
            identity
            for identity in self.identities
            if identity.kind is FribbelsIdentityKind.FINGERPRINT
        )


@dataclass(frozen=True, slots=True)
class FribbelsMergeOutcome:
    kind: FribbelsMergeOutcomeKind
    source_index: int
    stable_item_id: str | None = None
    code: str | None = None
    message: str | None = None

    def __post_init__(self) -> None:
        try:
            kind = (
                self.kind
                if isinstance(self.kind, FribbelsMergeOutcomeKind)
                else FribbelsMergeOutcomeKind(self.kind)
            )
        except (TypeError, ValueError):
            raise FribbelsMergeInputError("Merge outcome kind is not supported.") from None
        if isinstance(self.source_index, bool) or not isinstance(self.source_index, int) or self.source_index < 0:
            raise FribbelsMergeInputError("Merge outcome source_index must be non-negative.")
        if kind is FribbelsMergeOutcomeKind.CONFLICT:
            if self.stable_item_id is not None or not self.code or not self.message:
                raise FribbelsMergeInputError(
                    "Conflict outcomes require code/message and no stable item ID."
                )
        elif not self.stable_item_id or self.code is not None or self.message is not None:
            raise FribbelsMergeInputError(
                "Successful outcomes require a stable item ID and no conflict details."
            )
        object.__setattr__(self, "kind", kind)


@dataclass(frozen=True, slots=True)
class FribbelsMergeResult:
    items: tuple[FribbelsInventoryItem, ...]
    outcomes: tuple[FribbelsMergeOutcome, ...]
    unseen_existing_ids: tuple[str, ...]
    source_warnings: tuple[FribbelsIssue, ...]
    source_rejections: tuple[FribbelsIssue, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))
        object.__setattr__(self, "outcomes", tuple(self.outcomes))
        object.__setattr__(self, "unseen_existing_ids", tuple(self.unseen_existing_ids))
        object.__setattr__(self, "source_warnings", tuple(self.source_warnings))
        object.__setattr__(self, "source_rejections", tuple(self.source_rejections))

    def outcomes_of_kind(
        self,
        kind: FribbelsMergeOutcomeKind,
    ) -> tuple[FribbelsMergeOutcome, ...]:
        return tuple(outcome for outcome in self.outcomes if outcome.kind is kind)

    @property
    def inserted(self) -> tuple[FribbelsMergeOutcome, ...]:
        return self.outcomes_of_kind(FribbelsMergeOutcomeKind.INSERTED)

    @property
    def updated(self) -> tuple[FribbelsMergeOutcome, ...]:
        return self.outcomes_of_kind(FribbelsMergeOutcomeKind.UPDATED)

    @property
    def unchanged(self) -> tuple[FribbelsMergeOutcome, ...]:
        return self.outcomes_of_kind(FribbelsMergeOutcomeKind.UNCHANGED)

    @property
    def conflicts(self) -> tuple[FribbelsMergeOutcome, ...]:
        return self.outcomes_of_kind(FribbelsMergeOutcomeKind.CONFLICT)


def _projection_totals(
    totals: tuple[tuple[ItemStatType, int | float], ...],
) -> list[dict[str, object]]:
    return [
        {"type": stat_type.value, "value": _normalized_number(value)}
        for stat_type, value in totals
    ]


def _incoming_source_key(item: ParsedFribbelsItem) -> str:
    payload = {
        "slot": item.slot.value,
        "set": item.gear_set.value,
        "itemLevel": item.item_level,
        "enhance": item.enhance,
        "main": {
            "type": item.main_stat.stat_type.value,
            "value": _normalized_number(item.main_stat.value),
        },
        "substats": sorted(
            (
                {"type": stat.stat_type.value, "value": _normalized_number(stat.value)}
                for stat in item.substats
            ),
            key=lambda stat: str(stat["type"]),
        ),
        "equippedHeroId": item.equipped_hero_id,
        "locked": item.locked,
        "rank": item.rank.value,
        "material": None if item.material is None else item.material.value,
        "name": item.name,
        "equippedByName": item.equipped_by_name,
        "currentTotals": _projection_totals(item.projection.current_totals),
        "reforgedTotals": _projection_totals(item.projection.reforged_totals),
    }
    return deterministic_json(payload)


def _existing_source_key(item: FribbelsInventoryItem) -> str:
    payload = {
        "slot": item.gear_item.slot.value,
        "set": item.gear_item.gear_set.value,
        "itemLevel": item.gear_item.item_level,
        "enhance": item.gear_item.enhance,
        "main": {
            "type": item.gear_item.main_stat.value,
            "value": _normalized_number(item.gear_item.main_stat_value),
        },
        "substats": sorted(
            (
                {"type": stat_type.value, "value": _normalized_number(value)}
                for stat_type, value in item.gear_item.substats
            ),
            key=lambda stat: str(stat["type"]),
        ),
        "equippedHeroId": item.gear_item.equipped_hero_id,
        "locked": item.gear_item.locked,
        "rank": item.rank.value,
        "material": None if item.material is None else item.material.value,
        "name": item.name,
        "equippedByName": item.equipped_by_name,
        "currentTotals": _projection_totals(item.projection.current_totals),
        "reforgedTotals": _projection_totals(item.projection.reforged_totals),
    }
    return deterministic_json(payload)


def _strong_identities(
    identities: Sequence[FribbelsItemIdentity],
) -> tuple[FribbelsItemIdentity, ...]:
    return tuple(
        identity
        for identity in identities
        if identity.kind is not FribbelsIdentityKind.FINGERPRINT
    )


def _identity_values(
    identities: Sequence[FribbelsItemIdentity],
    kind: FribbelsIdentityKind,
) -> frozenset[str]:
    return frozenset(identity.value for identity in identities if identity.kind is kind)


def _weakly_compatible(
    incoming: Sequence[FribbelsItemIdentity],
    existing: FribbelsInventoryItem,
) -> bool:
    for kind in (FribbelsIdentityKind.INGAME, FribbelsIdentityKind.SOURCE):
        incoming_values = _identity_values(incoming, kind)
        existing_values = _identity_values(existing.identities, kind)
        if incoming_values and existing_values and incoming_values.isdisjoint(existing_values):
            return False
    return True


def _merged_identities(
    existing: FribbelsInventoryItem | None,
    incoming: Sequence[FribbelsItemIdentity],
) -> tuple[FribbelsItemIdentity, ...]:
    identities = {
        identity
        for identity in (() if existing is None else existing.identities)
        if identity.kind is not FribbelsIdentityKind.FINGERPRINT
    }
    identities.update(_strong_identities(incoming))
    identities.add(
        next(
            identity
            for identity in incoming
            if identity.kind is FribbelsIdentityKind.FINGERPRINT
        )
    )
    return tuple(sorted(identities, key=_identity_sort_key))


def _state_from_parsed(
    item: ParsedFribbelsItem,
    *,
    stable_item_id: str,
    identities: tuple[FribbelsItemIdentity, ...],
    user_metadata: FrozenJsonObject | Mapping[str, object],
) -> FribbelsInventoryItem:
    return FribbelsInventoryItem(
        stable_item_id=stable_item_id,
        gear_item=item.to_gear_item(stable_item_id),
        identities=identities,
        current_ingame_id=item.ingame_id,
        current_source_id=item.source_id,
        name=item.name,
        rank=item.rank,
        material=item.material,
        equipped_by_name=item.equipped_by_name,
        projection=item.projection,
        source_metadata=item.raw,
        user_metadata=user_metadata,
    )


def _validate_existing(
    existing: Sequence[FribbelsInventoryItem],
) -> tuple[
    dict[str, FribbelsInventoryItem],
    dict[FribbelsItemIdentity, str],
    dict[FribbelsItemIdentity, list[FribbelsInventoryItem]],
]:
    by_stable: dict[str, FribbelsInventoryItem] = {}
    strong_index: dict[FribbelsItemIdentity, str] = {}
    fingerprint_buckets: dict[FribbelsItemIdentity, list[FribbelsInventoryItem]] = defaultdict(list)
    for item in existing:
        if not isinstance(item, FribbelsInventoryItem):
            raise FribbelsMergeInputError("Existing inventory contains an invalid item state.")
        if item.stable_item_id in by_stable:
            raise FribbelsMergeInputError("Existing stable item IDs must be unique.")
        by_stable[item.stable_item_id] = item
        for identity in item.identities:
            if identity.kind is FribbelsIdentityKind.FINGERPRINT:
                fingerprint_buckets[identity].append(item)
                continue
            if identity in strong_index:
                raise FribbelsMergeInputError("Existing strong identity aliases must be unique.")
            strong_index[identity] = item.stable_item_id
    for bucket in fingerprint_buckets.values():
        bucket.sort(key=lambda item: item.stable_item_id)
    return by_stable, strong_index, fingerprint_buckets


def _allocate_fingerprint_id(
    fingerprint: FribbelsItemIdentity,
    used_stable_ids: set[str],
) -> str:
    occurrence = 1
    while True:
        candidate = stable_item_id_from_identity(fingerprint, occurrence=occurrence)
        if candidate not in used_stable_ids:
            return candidate
        occurrence += 1


def merge_fribbels_inventory(
    existing: Sequence[FribbelsInventoryItem],
    parsed: FribbelsParseResult,
) -> FribbelsMergeResult:
    """Purely merge accepted parser rows into immutable existing item state."""

    if not isinstance(parsed, FribbelsParseResult):
        raise FribbelsMergeInputError("Merge input must be a FribbelsParseResult.")
    by_stable, strong_index, fingerprint_buckets = _validate_existing(existing)
    incoming_items = tuple(sorted(parsed.items, key=lambda item: item.source_index))
    source_indexes = [item.source_index for item in incoming_items]
    if len(source_indexes) != len(set(source_indexes)):
        raise FribbelsMergeInputError("Accepted parser source indexes must be unique.")

    identities_by_index = {
        item.source_index: fribbels_item_identities(item) for item in incoming_items
    }
    fingerprint_by_index = {
        item.source_index: next(
            identity
            for identity in identities_by_index[item.source_index]
            if identity.kind is FribbelsIdentityKind.FINGERPRINT
        )
        for item in incoming_items
    }
    incoming_by_index = {item.source_index: item for item in incoming_items}

    conflicts: dict[int, tuple[str, str]] = {}
    matched: dict[int, str] = {}
    claimed_strong: dict[FribbelsItemIdentity, int] = {}
    reserved_existing: set[str] = set()

    for item in incoming_items:
        index = item.source_index
        strong = _strong_identities(identities_by_index[index])
        if any(identity in claimed_strong for identity in strong):
            conflicts[index] = (
                "duplicate-incoming-identity",
                "Another source row already claimed the same strong identity alias.",
            )
            continue
        for identity in strong:
            claimed_strong[identity] = index
        strong_matches = {
            strong_index[identity] for identity in strong if identity in strong_index
        }
        if len(strong_matches) > 1:
            conflicts[index] = (
                "conflicting-existing-aliases",
                "The source row's strong aliases resolve to different existing items.",
            )
            continue
        if strong_matches:
            stable_item_id = next(iter(strong_matches))
            if stable_item_id in reserved_existing:
                conflicts[index] = (
                    "duplicate-existing-match",
                    "Another source row already matched the same existing item.",
                )
                continue
            matched[index] = stable_item_id
            reserved_existing.add(stable_item_id)

    unresolved_by_fingerprint: dict[FribbelsItemIdentity, list[int]] = defaultdict(list)
    for item in incoming_items:
        index = item.source_index
        if index not in conflicts and index not in matched:
            unresolved_by_fingerprint[fingerprint_by_index[index]].append(index)

    for fingerprint, indexes in sorted(
        unresolved_by_fingerprint.items(),
        key=lambda pair: pair[0].value,
    ):
        available = [
            item
            for item in fingerprint_buckets.get(fingerprint, ())
            if item.stable_item_id not in reserved_existing
        ]
        remaining_indexes = sorted(
            indexes,
            key=lambda index: (
                -len(_strong_identities(identities_by_index[index])),
                _incoming_source_key(incoming_by_index[index]),
                index,
            ),
        )

        unmatched_indexes: list[int] = []
        for index in remaining_indexes:
            source_key = _incoming_source_key(incoming_by_index[index])
            exact = [
                item
                for item in available
                if _existing_source_key(item) == source_key
                and _weakly_compatible(identities_by_index[index], item)
            ]
            if not exact:
                unmatched_indexes.append(index)
                continue
            selected = min(exact, key=lambda item: item.stable_item_id)
            matched[index] = selected.stable_item_id
            reserved_existing.add(selected.stable_item_id)
            available.remove(selected)

        for index in unmatched_indexes:
            compatible = [
                item
                for item in available
                if _weakly_compatible(identities_by_index[index], item)
            ]
            if not compatible:
                continue
            selected = min(
                compatible,
                key=lambda item: (_existing_source_key(item), item.stable_item_id),
            )
            matched[index] = selected.stable_item_id
            reserved_existing.add(selected.stable_item_id)
            available.remove(selected)

    used_stable_ids = set(by_stable)
    new_stable_ids: dict[int, str] = {}
    for item in incoming_items:
        index = item.source_index
        if index in conflicts or index in matched:
            continue
        strong = _strong_identities(identities_by_index[index])
        if not strong:
            continue
        candidate = stable_item_id_from_identity(strong[0])
        if candidate in used_stable_ids:
            conflicts[index] = (
                "stable-id-collision",
                "The selected stable item ID is already used by an unrelated item.",
            )
            continue
        new_stable_ids[index] = candidate
        used_stable_ids.add(candidate)

    fingerprint_only: dict[FribbelsItemIdentity, list[int]] = defaultdict(list)
    for item in incoming_items:
        index = item.source_index
        if index in conflicts or index in matched or index in new_stable_ids:
            continue
        fingerprint_only[fingerprint_by_index[index]].append(index)
    for fingerprint, indexes in sorted(fingerprint_only.items(), key=lambda pair: pair[0].value):
        for index in sorted(
            indexes,
            key=lambda item_index: (
                _incoming_source_key(incoming_by_index[item_index]),
                item_index,
            ),
        ):
            stable_item_id = _allocate_fingerprint_id(fingerprint, used_stable_ids)
            new_stable_ids[index] = stable_item_id
            used_stable_ids.add(stable_item_id)

    resulting = dict(by_stable)
    outcomes: list[FribbelsMergeOutcome] = []
    for item in incoming_items:
        index = item.source_index
        if index in conflicts:
            code, message = conflicts[index]
            outcomes.append(
                FribbelsMergeOutcome(
                    kind=FribbelsMergeOutcomeKind.CONFLICT,
                    source_index=index,
                    code=code,
                    message=message,
                )
            )
            continue
        if index in matched:
            stable_item_id = matched[index]
            prior = by_stable[stable_item_id]
            updated = _state_from_parsed(
                item,
                stable_item_id=stable_item_id,
                identities=_merged_identities(prior, identities_by_index[index]),
                user_metadata=prior.user_metadata,
            )
            resulting[stable_item_id] = updated
            outcomes.append(
                FribbelsMergeOutcome(
                    kind=(
                        FribbelsMergeOutcomeKind.UNCHANGED
                        if updated == prior
                        else FribbelsMergeOutcomeKind.UPDATED
                    ),
                    source_index=index,
                    stable_item_id=stable_item_id,
                )
            )
            continue
        stable_item_id = new_stable_ids[index]
        inserted = _state_from_parsed(
            item,
            stable_item_id=stable_item_id,
            identities=_merged_identities(None, identities_by_index[index]),
            user_metadata=FrozenJsonObject(),
        )
        resulting[stable_item_id] = inserted
        outcomes.append(
            FribbelsMergeOutcome(
                kind=FribbelsMergeOutcomeKind.INSERTED,
                source_index=index,
                stable_item_id=stable_item_id,
            )
        )

    unseen = tuple(sorted(set(by_stable) - set(matched.values())))
    return FribbelsMergeResult(
        items=tuple(sorted(resulting.values(), key=lambda item: item.stable_item_id)),
        outcomes=tuple(sorted(outcomes, key=lambda outcome: outcome.source_index)),
        unseen_existing_ids=unseen,
        source_warnings=parsed.warnings,
        source_rejections=parsed.rejections,
    )


__all__ = [
    "FRIBBELS_FINGERPRINT_ALGORITHM",
    "FRIBBELS_FINGERPRINT_VERSION",
    "IDENTITY_KIND_PRIORITY",
    "FribbelsIdentityKind",
    "FribbelsInventoryItem",
    "FribbelsItemIdentity",
    "FribbelsMergeInputError",
    "FribbelsMergeOutcome",
    "FribbelsMergeOutcomeKind",
    "FribbelsMergeResult",
    "fribbels_fingerprint_payload",
    "fribbels_item_fingerprint",
    "fribbels_item_identities",
    "merge_fribbels_inventory",
    "stable_item_id_from_identity",
]
