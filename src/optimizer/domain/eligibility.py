"""Pure equipment-eligibility policy for owned optimizer gear."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from src.optimizer.domain.records import GearItem


class EquipmentEligibilityInputError(ValueError):
    """Raised when a policy or inventory sequence violates the contract."""


class EquipmentEligibilityReason(StrEnum):
    """Stable reason IDs for one eligibility decision."""

    UNEQUIPPED = "eligibility.unequipped"
    SELECTED_HERO = "eligibility.selected_hero"
    INCLUDED_EQUIPPED = "eligibility.include_equipped"
    OTHER_HERO = "eligibility.other_hero"


@dataclass(frozen=True, slots=True)
class EquipmentEligibilityPolicy:
    selected_hero_id: str
    include_equipped: bool
    selected_hero_alias_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.selected_hero_id, str) or not self.selected_hero_id.strip():
            raise EquipmentEligibilityInputError(
                "selected_hero_id must be a non-empty stable hero ID."
            )
        if not isinstance(self.include_equipped, bool):
            raise EquipmentEligibilityInputError("include_equipped must be boolean.")
        selected = self.selected_hero_id.strip()
        if isinstance(self.selected_hero_alias_ids, (str, bytes, bytearray)):
            raise EquipmentEligibilityInputError(
                "selected_hero_alias_ids must be a sequence of stable hero IDs."
            )
        try:
            aliases = tuple(self.selected_hero_alias_ids)
        except TypeError:
            raise EquipmentEligibilityInputError(
                "selected_hero_alias_ids must be a sequence of stable hero IDs."
            ) from None
        if any(not isinstance(alias, str) or not alias.strip() for alias in aliases):
            raise EquipmentEligibilityInputError(
                "selected_hero_alias_ids must contain non-empty stable hero IDs."
            )
        aliases = tuple(sorted({alias.strip() for alias in aliases if alias.strip() != selected}))
        object.__setattr__(self, "selected_hero_id", selected)
        object.__setattr__(self, "selected_hero_alias_ids", aliases)

    @property
    def selected_hero_ids(self) -> frozenset[str]:
        return frozenset((self.selected_hero_id, *self.selected_hero_alias_ids))


@dataclass(frozen=True, slots=True)
class EquipmentEligibilityDecision:
    item: GearItem
    eligible: bool
    reason: EquipmentEligibilityReason

    def __post_init__(self) -> None:
        if not isinstance(self.item, GearItem):
            raise EquipmentEligibilityInputError("Eligibility decisions require a GearItem.")
        if not isinstance(self.eligible, bool):
            raise EquipmentEligibilityInputError("Eligibility decision state must be boolean.")
        try:
            reason = (
                self.reason
                if isinstance(self.reason, EquipmentEligibilityReason)
                else EquipmentEligibilityReason(self.reason)
            )
        except (TypeError, ValueError):
            raise EquipmentEligibilityInputError(
                "Eligibility decision reason is unsupported."
            ) from None
        expected_eligible = reason is not EquipmentEligibilityReason.OTHER_HERO
        if self.eligible is not expected_eligible:
            raise EquipmentEligibilityInputError(
                "Eligibility decision state does not agree with its reason."
            )
        object.__setattr__(self, "reason", reason)

    @property
    def stable_item_id(self) -> str:
        return self.item.item_id


def _require_policy(policy: object) -> EquipmentEligibilityPolicy:
    if not isinstance(policy, EquipmentEligibilityPolicy):
        raise EquipmentEligibilityInputError(
            "policy must be an EquipmentEligibilityPolicy."
        )
    return policy


def _validated_items(items: Iterable[GearItem]) -> tuple[GearItem, ...]:
    if isinstance(items, (str, bytes, bytearray)):
        raise EquipmentEligibilityInputError("items must be an iterable of GearItem values.")
    try:
        iterator = iter(items)
    except TypeError:
        raise EquipmentEligibilityInputError(
            "items must be an iterable of GearItem values."
        ) from None

    validated: list[GearItem] = []
    stable_ids: set[str] = set()
    for item in iterator:
        if not isinstance(item, GearItem):
            raise EquipmentEligibilityInputError("items contains a value that is not a GearItem.")
        if item.item_id in stable_ids:
            raise EquipmentEligibilityInputError("items contains duplicate stable item IDs.")
        stable_ids.add(item.item_id)
        validated.append(item)
    return tuple(validated)


def decide_equipment_eligibility(
    item: GearItem,
    policy: EquipmentEligibilityPolicy,
) -> EquipmentEligibilityDecision:
    """Decide one already-owned item without consulting a hero catalog."""

    checked_policy = _require_policy(policy)
    if not isinstance(item, GearItem):
        raise EquipmentEligibilityInputError("item must be a GearItem.")

    owner_id = item.equipped_hero_id
    if owner_id is None:
        return EquipmentEligibilityDecision(
            item=item,
            eligible=True,
            reason=EquipmentEligibilityReason.UNEQUIPPED,
        )
    if owner_id in checked_policy.selected_hero_ids:
        return EquipmentEligibilityDecision(
            item=item,
            eligible=True,
            reason=EquipmentEligibilityReason.SELECTED_HERO,
        )
    if checked_policy.include_equipped:
        return EquipmentEligibilityDecision(
            item=item,
            eligible=True,
            reason=EquipmentEligibilityReason.INCLUDED_EQUIPPED,
        )
    return EquipmentEligibilityDecision(
        item=item,
        eligible=False,
        reason=EquipmentEligibilityReason.OTHER_HERO,
    )


def evaluate_equipment_eligibility(
    items: Iterable[GearItem],
    policy: EquipmentEligibilityPolicy,
) -> tuple[EquipmentEligibilityDecision, ...]:
    """Return one deterministic decision per unique input item, in input order."""

    checked_policy = _require_policy(policy)
    validated = _validated_items(items)
    return tuple(
        decide_equipment_eligibility(item, checked_policy) for item in validated
    )


def filter_eligible_gear(
    items: Iterable[GearItem],
    policy: EquipmentEligibilityPolicy,
) -> tuple[GearItem, ...]:
    """Return the original eligible item objects in deterministic input order."""

    return tuple(
        decision.item
        for decision in evaluate_equipment_eligibility(items, policy)
        if decision.eligible
    )


__all__ = [
    "EquipmentEligibilityDecision",
    "EquipmentEligibilityInputError",
    "EquipmentEligibilityPolicy",
    "EquipmentEligibilityReason",
    "decide_equipment_eligibility",
    "evaluate_equipment_eligibility",
    "filter_eligible_gear",
]
