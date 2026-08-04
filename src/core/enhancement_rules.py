from collections import Counter
from dataclasses import dataclass, field

from src.core.enhancement_packets import STAT_NAMES
from src.core.gear_archetypes import analyze_gear_archetypes, item_stat_id
from src.core.gear_evaluator import build_gs_string, calculate_gear_score_details


ENHANCE_TARGETS = [3, 6, 9, 12, 15]
INITIAL_POTENTIAL_THRESHOLD = 62
MINIMUM_POTENTIAL_THRESHOLD = 58
TOTAL_ENHANCEMENT_ROLLS = 5
REQUIRED_MATCHING_ROLLS = 4
FLAT_STAT_CODES = frozenset({"att", "def", "max_hp"})


@dataclass
class AutomationState:
    quality_track: bool | None = None
    roll_stats: list[str] = field(default_factory=list)
    recorded_enhancements: set[int] = field(default_factory=set)
    archetype_context: dict[str, str] | None = None
    initial_substat_count: int = 4
    initial_stat_labels: set[str] = field(default_factory=set)


@dataclass
class GearDecision:
    action: str
    reason: str
    current_gs: float
    potential_gs: float
    enhancement: int
    next_target: int | None = None


def _enhancement_int(value):
    try:
        return int(str(value).replace("+", ""))
    except ValueError:
        return 0


def _next_target(enhancement):
    for target in ENHANCE_TARGETS:
        if target > enhancement:
            return target
    return None


def record_enhancement_event(parsed_data, state):
    enhancement = _enhancement_int(parsed_data.get("enhance", "+0"))
    event = parsed_data.get("_enhancement_event", {})
    stat_code = event.get("statCode") if isinstance(event, dict) else None
    if enhancement not in ENHANCE_TARGETS or enhancement in state.recorded_enhancements:
        return
    if not isinstance(stat_code, str) or not stat_code:
        raise ValueError("Packet enhancement data is missing the latest roll stat.")
    state.recorded_enhancements.add(enhancement)
    state.roll_stats.append(stat_code)


def _roll_progress(state):
    counts = Counter(stat for stat in state.roll_stats if stat not in FLAT_STAT_CODES)
    if not counts:
        return None, 0
    return max(counts.items(), key=lambda item: (item[1], item[0]))


def _four_matching_rolls_still_possible(state):
    _stat, matching = _roll_progress(state)
    remaining = max(0, TOTAL_ENHANCEMENT_ROLLS - len(state.roll_stats))
    return matching + remaining >= REQUIRED_MATCHING_ROLLS


def _archetype_analysis(parsed_data, state):
    if state.archetype_context is None:
        return None
    raw_substats = parsed_data.get("subs", [])
    labels = [
        stat.get("stat") for stat in raw_substats
        if isinstance(stat, dict) and isinstance(stat.get("stat"), str)
    ]
    if len(labels) < 4:
        return None
    if not state.initial_stat_labels:
        state.initial_stat_labels.update(labels[:state.initial_substat_count])
    event_counts = Counter(STAT_NAMES.get(code, code) for code in state.roll_stats)
    substats = [item_stat_id(label) for label in labels]
    roll_counts = {
        item_stat_id(label): event_counts[label] + int(label in state.initial_stat_labels)
        for label in labels
    }
    return analyze_gear_archetypes(
        gear_set=state.archetype_context["setId"],
        slot=state.archetype_context["slotId"],
        main_stat=state.archetype_context["mainStatId"],
        substats=substats,
        roll_counts=roll_counts,
    )


def decide_enhancement_action(parsed_data, state):
    subs_string = build_gs_string(parsed_data.get("subs", []))
    gs = calculate_gear_score_details(subs_string, parsed_data.get("enhance", "+0"))
    enhancement = _enhancement_int(parsed_data.get("enhance", "+0"))
    potential = gs["potential_gs"]
    repeated_stat, repeated_rolls = _roll_progress(state)

    archetype = _archetype_analysis(parsed_data, state)
    if archetype is not None and archetype["verdict"] != "keep":
        return GearDecision(
            action="destroy",
            reason=f"Archetype rule: {archetype['reason']}",
            current_gs=gs["current_gs"],
            potential_gs=potential,
            enhancement=enhancement,
        )

    if state.quality_track is None and enhancement >= 3:
        state.quality_track = potential >= INITIAL_POTENTIAL_THRESHOLD

    if enhancement >= 15:
        if repeated_rolls >= REQUIRED_MATCHING_ROLLS:
            return GearDecision(
                action="lock",
                reason=(
                    f"Kept because {repeated_stat} received "
                    f"{repeated_rolls}/{TOTAL_ENHANCEMENT_ROLLS} enhancement rolls."
                ),
                current_gs=gs["current_gs"],
                potential_gs=potential,
                enhancement=enhancement,
            )
        if not state.quality_track or potential < MINIMUM_POTENTIAL_THRESHOLD:
            return GearDecision(
                action="destroy",
                reason=(
                    f"Final potential GS is below {MINIMUM_POTENTIAL_THRESHOLD} "
                    f"and no non-flat stat received {REQUIRED_MATCHING_ROLLS} enhancement rolls."
                ),
                current_gs=gs["current_gs"],
                potential_gs=potential,
                enhancement=enhancement,
            )
        return GearDecision(
            action="lock",
            reason=f"Piece reached +15 with at least {MINIMUM_POTENTIAL_THRESHOLD} potential GS.",
            current_gs=gs["current_gs"],
            potential_gs=potential,
            enhancement=enhancement,
        )

    if (
        (not state.quality_track or potential < MINIMUM_POTENTIAL_THRESHOLD)
        and not _four_matching_rolls_still_possible(state)
    ):
        return GearDecision(
            action="destroy",
            reason=(
                f"Potential GS does not qualify and {REQUIRED_MATCHING_ROLLS}/"
                f"{TOTAL_ENHANCEMENT_ROLLS} matching non-flat enhancement rolls are no longer possible."
            ),
            current_gs=gs["current_gs"],
            potential_gs=potential,
            enhancement=enhancement,
        )

    target = _next_target(enhancement)
    if target is None:
        return GearDecision(
            action="lock",
            reason="No remaining enhancement target.",
            current_gs=gs["current_gs"],
            potential_gs=potential,
            enhancement=enhancement,
        )

    if state.quality_track and potential >= MINIMUM_POTENTIAL_THRESHOLD:
        reason = "Potential GS track passed."
    else:
        remaining = TOTAL_ENHANCEMENT_ROLLS - len(state.roll_stats)
        reason = (
            f"Continuing the non-flat matching-roll check: {repeated_rolls} on {repeated_stat}; "
            f"{remaining} enhancement roll(s) remain."
        )
    return GearDecision(
        action="enhance",
        reason=reason,
        current_gs=gs["current_gs"],
        potential_gs=potential,
        enhancement=enhancement,
        next_target=target,
    )


record_snapshot = record_enhancement_event
