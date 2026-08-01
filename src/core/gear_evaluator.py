import re

from src.optimizer.domain import (
    gear_set_display_name,
    item_stat_display_name,
    item_stat_from_display,
    resolve_gear_set,
)


def _normalize_stat_name(stat):
    try:
        return item_stat_display_name(item_stat_from_display(stat))
    except ValueError:
        return str(stat)


def _normalize_set_name(gear_set):
    try:
        return gear_set_display_name(resolve_gear_set(gear_set))
    except ValueError:
        return str(gear_set)


def evaluate_archetypes(slot, gear_set, main_stat, subs, archetypes):
    """Checks the gear against the user's saved archetypes."""
    matches = []
    normalized_set = _normalize_set_name(gear_set)
    normalized_main = _normalize_stat_name(main_stat)
    normalized_subs = [_normalize_stat_name(s) for s in subs]

    for arch in archetypes:
        needed_stats = {_normalize_stat_name(s) for s in arch["needed_stats"]}
        priority_sets = {_normalize_set_name(s) for s in arch["priority_sets"]}

        # Weapons/Helmets/Armor have fixed main stats, so they are always valid.
        is_valid_main = (slot in ["Weapon", "Helmet", "Armor"]) or (normalized_main in needed_stats)

        if normalized_set in priority_sets and is_valid_main:
            count = sum(1 for s in normalized_subs if s in needed_stats)
            if count >= 3:
                matches.append(f"- {arch['name']} ({count}/4)")

    if matches:
        return "MATCHES:\n" + "\n".join(matches)
    return "NO MATCH\nDoesn't fit saved archetypes."


def build_gs_string(sub_data_list):
    """Converts [{'stat': 'Speed', 'val': '4'}] into the parser string '4s'."""
    subs = []
    for sub in sub_data_list:
        stat = _normalize_stat_name(sub["stat"])
        val = sub["val"]

        if not val.isdigit() or int(val) == 0:
            continue

        if stat in ["Attack", "Health", "Defense", "Effectiveness", "Effect Resistance"]:
            subs.append(val)
        elif stat == "Speed":
            subs.append(f"{val}s")
        elif stat == "Critical Hit Chance":
            subs.append(f"{val}cc")
        elif stat == "Critical Hit Damage":
            subs.append(f"{val}cd")
        elif stat == "Flat Attack":
            subs.append(f"{val}atk")
        elif stat == "Flat Defense":
            subs.append(f"{val}def")
        elif stat == "Flat Health":
            subs.append(f"{val}hp")

    return " ".join(subs)


def calculate_gear_score(subs_string, enhancement_level):
    """Calculates current and potential Gear Score based on the extracted string."""
    details = calculate_gear_score_details(subs_string, enhancement_level)
    gs = details["current_gs"]
    potential = details["potential_gs"]
    rolls = details["rolls"]

    if rolls == 5:
        return f"Final GS: {round(gs)}"

    txt = f"Current GS: {round(gs)}  |  Potential GS: {round(potential)}  |  Rolls Tested: {rolls}\n"
    txt += "Keep rolling" if potential > 57 else "Stop rolling"

    return txt


def calculate_gear_score_details(subs_string, enhancement_level):
    """Returns numeric GS details for automation decisions."""
    try:
        enhancement_val = int(str(enhancement_level).replace("+", ""))
    except ValueError:
        enhancement_val = 0

    rolls = enhancement_val // 3
    gs = 0

    for sub in subs_string.split(" "):
        if not sub:
            continue
        if "cc" in sub:
            gs += int(re.search(r"(\d+)cc", sub)[1]) * (8 / 5)
        elif "cd" in sub:
            gs += int(re.search(r"(\d+)cd", sub)[1]) * (8 / 7)
        elif "s" in sub:
            gs += int(re.search(r"(\d+)s", sub)[1]) * 2
        elif "atk" in sub:
            gs += int(re.search(r"(\d+)atk", sub)[1]) * (3.46 / 39)
        elif "def" in sub:
            gs += int(re.search(r"(\d+)def", sub)[1]) * (4.99 / 31)
        elif "hp" in sub:
            gs += int(re.search(r"(\d+)hp", sub)[1]) * (3.09 / 174)
        else:
            match = re.search(r"(\d+)", sub)
            if match:
                gs += int(match[1])

    potential = gs + (5 - rolls) * 8

    return {
        "current_gs": gs,
        "potential_gs": potential,
        "rolls": rolls,
        "enhancement": enhancement_val,
    }
