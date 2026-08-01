"""Pure deterministic preparation of compact per-slot optimizer arrays."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from src.optimizer.data.character_profiles import CharacterProfileSelection
from src.optimizer.data.fribbels_merge import FribbelsInventoryItem
from src.optimizer.domain import (
    FINAL_STAT_ORDER,
    GEAR_SLOT_ORDER,
    SET_CATALOG,
    EquipmentEligibilityPolicy,
    EquipmentEligibilityReason,
    FinalStat,
    GearSlot,
    ItemProjectionMode,
    OptimizationRequest,
    decide_equipment_eligibility,
)
from src.optimizer.engine import (
    DerivedMetricError,
    ItemProjectionEvidence,
    ProjectedGearItem,
    StatAggregationError,
    calculate_item_final_contributions,
    calculate_item_gear_score,
)


class SearchPreparationExclusionReason(StrEnum):
    """Stable terminal reason why one owned item did not enter a slot array."""

    OTHER_HERO = "eligibility.other_hero"
    EXPLICIT_ITEM = "filter.explicit_item"
    BELOW_MINIMUM_ENHANCE = "filter.minimum_enhance"
    MAIN_STAT = "filter.main_stat"
    REFORGED_UNAVAILABLE = "filter.reforged_unavailable"
    SET_REQUIREMENT = "filter.set_requirement"


SEARCH_PREPARATION_EXCLUSION_ORDER = tuple(SearchPreparationExclusionReason)


class SearchPreparationError(ValueError):
    """Actionable request, inventory, or empty-slot preparation failure."""

    def __init__(
        self,
        code: str,
        path: str,
        message: str,
        *,
        diagnostics: "SearchPreparationDiagnostics | None" = None,
    ) -> None:
        self.code = code
        self.path = path
        self.message = message
        self.diagnostics = diagnostics
        super().__init__(f"{code} at {path}: {message}")


@dataclass(frozen=True, slots=True)
class SearchItemPreparationDiagnostic:
    stable_item_id: str
    slot: GearSlot
    included: bool
    eligibility_reason: EquipmentEligibilityReason
    exclusion_reason: SearchPreparationExclusionReason | None
    projection_evidence: ItemProjectionEvidence | None

    def __post_init__(self) -> None:
        if not isinstance(self.stable_item_id, str) or not self.stable_item_id.strip():
            raise ValueError("Search preparation item IDs must be non-empty.")
        object.__setattr__(self, "stable_item_id", self.stable_item_id.strip())
        object.__setattr__(self, "slot", GearSlot(self.slot))
        object.__setattr__(
            self,
            "eligibility_reason",
            EquipmentEligibilityReason(self.eligibility_reason),
        )
        if not isinstance(self.included, bool):
            raise ValueError("Search preparation inclusion state must be boolean.")
        if self.included:
            if self.exclusion_reason is not None or self.projection_evidence is None:
                raise ValueError(
                    "Included search items require projection evidence and no exclusion reason."
                )
        else:
            if self.exclusion_reason is None or self.projection_evidence is not None:
                raise ValueError(
                    "Excluded search items require one reason and no projection evidence."
                )
            object.__setattr__(
                self,
                "exclusion_reason",
                SearchPreparationExclusionReason(self.exclusion_reason),
            )
            if (
                self.exclusion_reason is SearchPreparationExclusionReason.OTHER_HERO
            ) is not (
                self.eligibility_reason is EquipmentEligibilityReason.OTHER_HERO
            ):
                raise ValueError(
                    "Search ownership exclusions must agree with eligibility evidence."
                )
        if self.projection_evidence is not None:
            object.__setattr__(
                self,
                "projection_evidence",
                ItemProjectionEvidence(self.projection_evidence),
            )


@dataclass(frozen=True, slots=True)
class SearchSlotPreparationDiagnostic:
    slot: GearSlot
    input_count: int
    included_count: int
    exclusion_counts: tuple[tuple[SearchPreparationExclusionReason, int], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "slot", GearSlot(self.slot))
        if (
            isinstance(self.input_count, bool)
            or not isinstance(self.input_count, int)
            or self.input_count < 0
            or isinstance(self.included_count, bool)
            or not isinstance(self.included_count, int)
            or self.included_count < 0
        ):
            raise ValueError("Search slot counts must be nonnegative integers.")
        counts = tuple(
            (SearchPreparationExclusionReason(reason), count)
            for reason, count in self.exclusion_counts
        )
        if tuple(reason for reason, _ in counts) != SEARCH_PREPARATION_EXCLUSION_ORDER:
            raise ValueError("Search exclusion counts must use canonical reason order.")
        if any(isinstance(count, bool) or not isinstance(count, int) or count < 0 for _, count in counts):
            raise ValueError("Search exclusion counts must be nonnegative integers.")
        if self.included_count + sum(count for _, count in counts) != self.input_count:
            raise ValueError("Search slot included and excluded counts must match input_count.")
        object.__setattr__(self, "exclusion_counts", counts)

    def excluded_for(self, reason: SearchPreparationExclusionReason) -> int:
        return dict(self.exclusion_counts)[SearchPreparationExclusionReason(reason)]


@dataclass(frozen=True, slots=True)
class SearchPreparationDiagnostics:
    projection_mode: ItemProjectionMode
    decisions: tuple[SearchItemPreparationDiagnostic, ...]
    slots: tuple[SearchSlotPreparationDiagnostic, ...]
    unmatched_excluded_item_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "projection_mode",
            ItemProjectionMode(self.projection_mode),
        )
        decisions = tuple(self.decisions)
        slots = tuple(self.slots)
        unmatched = tuple(self.unmatched_excluded_item_ids)
        if tuple(item.slot for item in slots) != GEAR_SLOT_ORDER:
            raise ValueError("Search preparation slots must use canonical six-slot order.")
        decision_keys = tuple(
            (GEAR_SLOT_ORDER.index(item.slot), item.stable_item_id) for item in decisions
        )
        if decision_keys != tuple(sorted(decision_keys)) or len(decision_keys) != len(set(decision_keys)):
            raise ValueError("Search preparation decisions must be uniquely canonical.")
        if unmatched != tuple(sorted(set(unmatched))):
            raise ValueError("Unmatched excluded item IDs must be sorted and unique.")
        for slot_summary in slots:
            matching = tuple(item for item in decisions if item.slot is slot_summary.slot)
            if slot_summary.input_count != len(matching):
                raise ValueError("Search slot input counts must agree with item diagnostics.")
            if slot_summary.included_count != sum(item.included for item in matching):
                raise ValueError("Search slot included counts must agree with item diagnostics.")
        object.__setattr__(self, "decisions", decisions)
        object.__setattr__(self, "slots", slots)
        object.__setattr__(self, "unmatched_excluded_item_ids", unmatched)

    @property
    def input_count(self) -> int:
        return len(self.decisions)

    @property
    def included_count(self) -> int:
        return sum(item.included for item in self.decisions)

    @property
    def empty_slots(self) -> tuple[GearSlot, ...]:
        return tuple(item.slot for item in self.slots if item.included_count == 0)

    def slot(self, slot: GearSlot) -> SearchSlotPreparationDiagnostic:
        return self.slots[GEAR_SLOT_ORDER.index(GearSlot(slot))]


@dataclass(frozen=True, slots=True)
class SearchSlotArray:
    """Parallel immutable numeric rows for one canonical gear slot."""

    slot: GearSlot
    dense_ids: tuple[int, ...]
    set_indices: tuple[int, ...]
    final_stat_contributions: tuple[tuple[float, ...], ...]
    gear_scores: tuple[int, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "slot", GearSlot(self.slot))
        dense_ids = tuple(self.dense_ids)
        set_indices = tuple(self.set_indices)
        contributions = tuple(tuple(row) for row in self.final_stat_contributions)
        gear_scores = tuple(self.gear_scores)
        lengths = {len(dense_ids), len(set_indices), len(contributions), len(gear_scores)}
        if len(lengths) != 1:
            raise ValueError("Search slot parallel arrays must have identical lengths.")
        if not dense_ids:
            raise ValueError("Search slot arrays must not be empty.")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in dense_ids):
            raise ValueError("Search dense IDs must be nonnegative integers.")
        if len(dense_ids) != len(set(dense_ids)):
            raise ValueError("Search dense IDs must be unique.")
        if any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            or value >= len(SET_CATALOG)
            for value in set_indices
        ):
            raise ValueError("Search set indices must be canonical Fribbels indices.")
        if any(
            len(row) != len(FINAL_STAT_ORDER)
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
                for value in row
            )
            for row in contributions
        ):
            raise ValueError("Search contribution rows must contain eight finite nonnegative values.")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in gear_scores):
            raise ValueError("Search gear scores must be nonnegative integers.")
        object.__setattr__(self, "dense_ids", dense_ids)
        object.__setattr__(self, "set_indices", set_indices)
        object.__setattr__(self, "final_stat_contributions", contributions)
        object.__setattr__(self, "gear_scores", gear_scores)


@dataclass(frozen=True, slots=True)
class SearchReadySlotArrays:
    slots: tuple[SearchSlotArray, ...]
    dense_id_to_stable_id: tuple[tuple[int, str], ...]
    diagnostics: SearchPreparationDiagnostics
    request_id: str
    hero_id: str
    base_profile_id: str
    base_stats: tuple[int | float, ...]

    def __post_init__(self) -> None:
        slots = tuple(self.slots)
        reverse = tuple(self.dense_id_to_stable_id)
        identities = (
            ("request_id", self.request_id),
            ("hero_id", self.hero_id),
            ("base_profile_id", self.base_profile_id),
        )
        for field, value in identities:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Search arrays {field} must be a non-empty stable ID.")
            object.__setattr__(self, field, value.strip())
        base_stats = tuple(self.base_stats)
        if len(base_stats) != len(FINAL_STAT_ORDER) or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
            for value in base_stats
        ):
            raise ValueError(
                "Search array base stats must contain eight finite nonnegative values."
            )
        if tuple(item.slot for item in slots) != GEAR_SLOT_ORDER:
            raise ValueError("Search arrays must use canonical six-slot order.")
        expected_ids = tuple(range(len(reverse)))
        if tuple(dense_id for dense_id, _ in reverse) != expected_ids:
            raise ValueError("Search reverse dense IDs must be contiguous from zero.")
        stable_ids = tuple(stable_id for _, stable_id in reverse)
        if any(not isinstance(value, str) or not value for value in stable_ids):
            raise ValueError("Search reverse mappings require stable item IDs.")
        if len(stable_ids) != len(set(stable_ids)):
            raise ValueError("Search reverse stable item IDs must be unique.")
        flattened = tuple(dense_id for slot in slots for dense_id in slot.dense_ids)
        if flattened != expected_ids:
            raise ValueError("Search slot arrays must contain contiguous canonical dense IDs.")
        if not isinstance(self.diagnostics, SearchPreparationDiagnostics):
            raise ValueError("Search arrays require preparation diagnostics.")
        if self.diagnostics.included_count != len(reverse):
            raise ValueError("Search reverse mapping must agree with preparation diagnostics.")
        included_stable_ids = tuple(
            item.stable_item_id for item in self.diagnostics.decisions if item.included
        )
        if stable_ids != included_stable_ids:
            raise ValueError("Search reverse mapping must preserve included diagnostic order.")
        object.__setattr__(self, "slots", slots)
        object.__setattr__(self, "dense_id_to_stable_id", reverse)
        object.__setattr__(self, "base_stats", base_stats)

    @property
    def total_items(self) -> int:
        return len(self.dense_id_to_stable_id)

    def for_slot(self, slot: GearSlot) -> SearchSlotArray:
        return self.slots[GEAR_SLOT_ORDER.index(GearSlot(slot))]

    def stable_item_id_for_dense_id(self, dense_id: int) -> str:
        if isinstance(dense_id, bool) or not isinstance(dense_id, int) or dense_id < 0:
            raise KeyError(dense_id)
        try:
            return self.dense_id_to_stable_id[dense_id][1]
        except IndexError:
            raise KeyError(dense_id) from None


def _require_inputs(
    request: object,
    profile_selection: object,
    inventory: Iterable[FribbelsInventoryItem],
) -> tuple[OptimizationRequest, CharacterProfileSelection, tuple[FribbelsInventoryItem, ...]]:
    if not isinstance(request, OptimizationRequest):
        raise SearchPreparationError(
            "invalid-request",
            "request",
            "Expected an OptimizationRequest.",
        )
    if request.item_projection_mode is None:
        raise SearchPreparationError(
            "projection-mode-required",
            "request.itemProjectionMode",
            "Select current or reforged item totals before search preparation.",
        )
    if not isinstance(profile_selection, CharacterProfileSelection):
        raise SearchPreparationError(
            "invalid-profile-selection",
            "profileSelection",
            "Expected a CharacterProfileSelection.",
        )
    if request.hero_id != profile_selection.hero_id:
        raise SearchPreparationError(
            "hero-selection-mismatch",
            "profileSelection.heroId",
            "The resolved hero does not match request.heroId.",
        )
    if request.base_profile_id != profile_selection.profile_id:
        raise SearchPreparationError(
            "profile-selection-mismatch",
            "profileSelection.profileId",
            "The resolved base profile does not match request.baseProfileId.",
        )
    if isinstance(inventory, (str, bytes, bytearray)):
        raise SearchPreparationError(
            "invalid-inventory",
            "inventory",
            "Expected persisted Fribbels inventory items.",
        )
    try:
        supplied = tuple(inventory)
    except TypeError:
        raise SearchPreparationError(
            "invalid-inventory",
            "inventory",
            "Expected persisted Fribbels inventory items.",
        ) from None
    if not all(isinstance(item, FribbelsInventoryItem) for item in supplied):
        raise SearchPreparationError(
            "invalid-inventory",
            "inventory",
            "Every entry must be a FribbelsInventoryItem.",
        )
    stable_ids = tuple(item.stable_item_id for item in supplied)
    if len(stable_ids) != len(set(stable_ids)):
        raise SearchPreparationError(
            "duplicate-item-id",
            "inventory",
            "Persisted inventory items must have unique stable IDs.",
        )
    slot_order = {slot: index for index, slot in enumerate(GEAR_SLOT_ORDER)}
    ordered = tuple(
        sorted(supplied, key=lambda item: (slot_order[item.gear_item.slot], item.stable_item_id))
    )
    return request, profile_selection, ordered


def _diagnostics(
    mode: ItemProjectionMode,
    decisions: list[SearchItemPreparationDiagnostic],
    unmatched: tuple[str, ...],
) -> SearchPreparationDiagnostics:
    slot_summaries: list[SearchSlotPreparationDiagnostic] = []
    for slot in GEAR_SLOT_ORDER:
        matching = tuple(item for item in decisions if item.slot is slot)
        slot_summaries.append(
            SearchSlotPreparationDiagnostic(
                slot=slot,
                input_count=len(matching),
                included_count=sum(item.included for item in matching),
                exclusion_counts=tuple(
                    (
                        reason,
                        sum(item.exclusion_reason is reason for item in matching),
                    )
                    for reason in SEARCH_PREPARATION_EXCLUSION_ORDER
                ),
            )
        )
    return SearchPreparationDiagnostics(
        projection_mode=mode,
        decisions=tuple(decisions),
        slots=tuple(slot_summaries),
        unmatched_excluded_item_ids=unmatched,
    )


def prepare_search_slot_arrays(
    request: OptimizationRequest,
    profile_selection: CharacterProfileSelection,
    inventory: Iterable[FribbelsInventoryItem],
    *,
    prefilter_fully_constrained_sets: bool = False,
    selected_hero_alias_ids: Iterable[str] = (),
) -> SearchReadySlotArrays:
    """Filter owned gear and precompute deterministic numeric search arrays."""

    checked_request, checked_profile, ordered = _require_inputs(
        request,
        profile_selection,
        inventory,
    )
    mode = checked_request.item_projection_mode
    assert mode is not None
    selected_name = checked_profile.hero.name.strip().casefold()
    if isinstance(selected_hero_alias_ids, (str, bytes, bytearray)):
        raise SearchPreparationError(
            "invalid-hero-aliases",
            "selectedHeroAliasIds",
            "Expected a sequence of imported hero IDs.",
        )
    try:
        supplied_alias_ids = tuple(selected_hero_alias_ids)
    except TypeError:
        raise SearchPreparationError(
            "invalid-hero-aliases",
            "selectedHeroAliasIds",
            "Expected a sequence of imported hero IDs.",
        ) from None
    if any(not isinstance(alias, str) or not alias.strip() for alias in supplied_alias_ids):
        raise SearchPreparationError(
            "invalid-hero-aliases",
            "selectedHeroAliasIds",
            "Imported hero IDs must be non-empty text.",
        )
    resolved_alias_id_set = {alias.strip() for alias in supplied_alias_ids}
    resolved_alias_id_set.update(
        {
            state.gear_item.equipped_hero_id
            for state in ordered
            if state.gear_item.equipped_hero_id is not None
            and state.equipped_by_name is not None
            and state.equipped_by_name.strip().casefold() == selected_name
        }
    )
    resolved_alias_ids = tuple(sorted(resolved_alias_id_set))
    policy = EquipmentEligibilityPolicy(
        checked_request.hero_id,
        checked_request.include_equipped,
        resolved_alias_ids,
    )
    filters = checked_request.gear_filters
    excluded_ids = set(filters.excluded_item_ids)
    inventory_ids = {item.stable_item_id for item in ordered}
    unmatched = tuple(sorted(excluded_ids - inventory_ids))
    main_stat_filters = dict(filters.right_side_main_stats)
    selected_set_piece_count = sum(
        SET_CATALOG[gear_set].pieces_required
        for gear_set in checked_request.set_pattern.sets
    )
    fully_constrained_sets = (
        frozenset(checked_request.set_pattern.sets)
        if prefilter_fully_constrained_sets
        and selected_set_piece_count == len(GEAR_SLOT_ORDER)
        else frozenset()
    )

    decisions: list[SearchItemPreparationDiagnostic] = []
    accepted: list[tuple[FribbelsInventoryItem, ProjectedGearItem]] = []
    for state in ordered:
        gear = state.gear_item
        eligibility = decide_equipment_eligibility(gear, policy)
        exclusion: SearchPreparationExclusionReason | None = None
        if not eligibility.eligible:
            exclusion = SearchPreparationExclusionReason.OTHER_HERO
        elif state.stable_item_id in excluded_ids:
            exclusion = SearchPreparationExclusionReason.EXPLICIT_ITEM
        elif gear.enhance < filters.minimum_enhance:
            exclusion = SearchPreparationExclusionReason.BELOW_MINIMUM_ENHANCE
        elif (
            gear.slot in main_stat_filters
            and gear.main_stat not in main_stat_filters[gear.slot]
        ):
            exclusion = SearchPreparationExclusionReason.MAIN_STAT
        elif fully_constrained_sets and gear.gear_set not in fully_constrained_sets:
            exclusion = SearchPreparationExclusionReason.SET_REQUIREMENT

        projected: ProjectedGearItem | None = None
        evidence: ItemProjectionEvidence | None = None
        if exclusion is None:
            try:
                projected = ProjectedGearItem.from_fribbels_inventory_item(state)
                projected.totals_for(mode)
                evidence = projected.evidence_for(mode)
                projected.main_value_for(mode)
            except StatAggregationError as error:
                if mode is ItemProjectionMode.REFORGED and error.code == "reforged-projection-unavailable":
                    exclusion = SearchPreparationExclusionReason.REFORGED_UNAVAILABLE
                else:
                    raise SearchPreparationError(
                        "invalid-inventory-projection",
                        f"inventory[{state.stable_item_id}]",
                        error.message,
                    ) from error

        included = exclusion is None
        decisions.append(
            SearchItemPreparationDiagnostic(
                stable_item_id=state.stable_item_id,
                slot=gear.slot,
                included=included,
                eligibility_reason=eligibility.reason,
                exclusion_reason=exclusion,
                projection_evidence=evidence if included else None,
            )
        )
        if included:
            assert projected is not None
            accepted.append((state, projected))

    diagnostics = _diagnostics(mode, decisions, unmatched)
    if diagnostics.empty_slots:
        display = ", ".join(slot.value for slot in diagnostics.empty_slots)
        raise SearchPreparationError(
            "empty-search-slots",
            "slots",
            "At least one candidate is required for every slot; empty: " + display + ".",
            diagnostics=diagnostics,
        )

    by_slot: dict[GearSlot, dict[str, list[object]]] = {
        slot: {
            "dense_ids": [],
            "set_indices": [],
            "contributions": [],
            "gear_scores": [],
        }
        for slot in GEAR_SLOT_ORDER
    }
    reverse: list[tuple[int, str]] = []
    base_stats = dict(checked_profile.profile.final_stats)
    for dense_id, (state, projected) in enumerate(accepted):
        totals = projected.totals_for(mode)
        main_stat = projected.main_stat
        assert main_stat is not None
        main_value = projected.main_value_for(mode)
        try:
            contributions = calculate_item_final_contributions(totals, base_stats)
            gear_score = calculate_item_gear_score(
                state.stable_item_id,
                totals,
                main_stat,
                main_value,
            ).score
        except (StatAggregationError, DerivedMetricError) as error:
            raise SearchPreparationError(
                "item-contribution-failed",
                f"inventory[{state.stable_item_id}]",
                error.message,
                diagnostics=diagnostics,
            ) from error
        arrays = by_slot[state.gear_item.slot]
        arrays["dense_ids"].append(dense_id)
        arrays["set_indices"].append(SET_CATALOG[state.gear_item.gear_set].fribbels_index)
        arrays["contributions"].append(tuple(value for _, value in contributions))
        arrays["gear_scores"].append(gear_score)
        reverse.append((dense_id, state.stable_item_id))

    slots = tuple(
        SearchSlotArray(
            slot=slot,
            dense_ids=tuple(by_slot[slot]["dense_ids"]),
            set_indices=tuple(by_slot[slot]["set_indices"]),
            final_stat_contributions=tuple(by_slot[slot]["contributions"]),
            gear_scores=tuple(by_slot[slot]["gear_scores"]),
        )
        for slot in GEAR_SLOT_ORDER
    )
    return SearchReadySlotArrays(
        slots=slots,
        dense_id_to_stable_id=tuple(reverse),
        diagnostics=diagnostics,
        request_id=checked_request.request_id,
        hero_id=checked_request.hero_id,
        base_profile_id=checked_request.base_profile_id,
        base_stats=tuple(value for _, value in checked_profile.profile.final_stats),
    )


__all__ = [
    "SEARCH_PREPARATION_EXCLUSION_ORDER",
    "SearchItemPreparationDiagnostic",
    "SearchPreparationDiagnostics",
    "SearchPreparationError",
    "SearchPreparationExclusionReason",
    "SearchReadySlotArrays",
    "SearchSlotArray",
    "SearchSlotPreparationDiagnostic",
    "prepare_search_slot_arrays",
]
