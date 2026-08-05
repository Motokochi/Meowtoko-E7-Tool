from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Mapping, Sequence

from src.optimizer.domain import ITEM_STAT_CATALOG, ItemStatType


CATALOG_PATH = Path(__file__).with_name("data") / "gear_archetypes.json"
RIGHT_SIDE_SLOTS = frozenset({"slot.necklace", "slot.ring", "slot.boots"})
MAX_OFF_STAT_ROLLS = 2
_STAT_IDS_BY_LABEL = {
    details.display_name: stat.value for stat, details in ITEM_STAT_CATALOG.items()
}
_PERCENT_STAT_BY_FLAT = {
    "Flat Attack": "Attack",
    "Flat Defense": "Defense",
    "Flat Health": "Health",
}


@lru_cache(maxsize=1)
def load_gear_archetypes() -> tuple[dict[str, object], ...]:
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return tuple(payload["archetypes"])


def item_stat_id(label: str) -> str:
    return _STAT_IDS_BY_LABEL[label]


def _stat_label(stat_id: str) -> str:
    return ITEM_STAT_CATALOG[ItemStatType(stat_id)].display_name


def analyze_gear_archetypes(
    *,
    gear_set: str,
    slot: str,
    main_stat: str,
    substats: Sequence[str],
    roll_counts: Mapping[str, int | None] | None = None,
) -> dict[str, object]:
    main_label = _stat_label(main_stat)
    stat_rows = [(stat_id, _stat_label(stat_id)) for stat_id in substats]
    matches = []
    for archetype in load_gear_archetypes():
        preferred = set(archetype["preferredStats"])
        flat_fallbacks = set(archetype["flatStatFallbacks"])
        stat_groups = archetype.get("substatGroups") or [
            [stat] for stat in archetype["preferredStats"]
        ]
        accepted_groups = [
            set(group) | {
                flat_stat for flat_stat in flat_fallbacks
                if _PERCENT_STAT_BY_FLAT.get(flat_stat) in group
            }
            for group in stat_groups
        ]
        accepted_substats = set().union(*accepted_groups)
        matching = [label for _stat_id, label in stat_rows if label in accepted_substats]
        matching_group_count = sum(
            any(label in group for _stat_id, label in stat_rows)
            for group in accepted_groups
        )
        if (
            gear_set not in archetype["compatibleSets"]
            or (slot in RIGHT_SIDE_SLOTS and main_label not in preferred)
            or matching_group_count < 3
        ):
            continue

        off_stats = [
            {
                "statId": stat_id,
                "label": label,
                "rolls": None if roll_counts is None else roll_counts.get(stat_id),
            }
            for stat_id, label in stat_rows
            if label not in accepted_substats
        ]
        rejected = any(
            isinstance(stat["rolls"], int) and stat["rolls"] > MAX_OFF_STAT_ROLLS
            for stat in off_stats
        )
        unknown = any(stat["rolls"] is None for stat in off_stats)
        matches.append({
            "id": archetype["id"],
            "name": archetype["name"],
            "heroes": archetype.get("heroesBySet", {}).get(gear_set, archetype["heroes"]),
            "preferredStats": archetype["preferredStats"],
            "matchingSubstats": matching,
            "offStats": off_stats,
            "status": "rejected" if rejected else "unknown" if unknown else "eligible",
        })

    eligible = [match for match in matches if match["status"] == "eligible"]
    uncertain = [match for match in matches if match["status"] == "unknown"]
    if eligible:
        verdict = "keep"
        reason = f"Matches {len(eligible)} archetype(s) with acceptable off-stat rolls."
    elif uncertain:
        verdict = "review"
        reason = "Archetype fit found, but the off-stat roll history is unavailable."
    elif matches:
        verdict = "destroy"
        rejected_stats = {
            (stat["label"], stat["rolls"])
            for match in matches
            for stat in match["offStats"]
            if isinstance(stat["rolls"], int) and stat["rolls"] > MAX_OFF_STAT_ROLLS
        }
        details = ", ".join(
            f"{label} reached {rolls} total rolls"
            for label, rolls in sorted(rejected_stats)
        )
        reason = f"Every matching archetype was lost: {details}."
    else:
        verdict = "destroy"
        reason = "No archetype matches this set, main stat, and at least three substats."
    return {
        "verdict": verdict,
        "reason": reason,
        "rollHistoryAvailable": all(
            stat["rolls"] is not None
            for match in matches
            for stat in match["offStats"]
        ),
        "matches": matches,
    }


__all__ = [
    "MAX_OFF_STAT_ROLLS",
    "analyze_gear_archetypes",
    "item_stat_id",
    "load_gear_archetypes",
]
