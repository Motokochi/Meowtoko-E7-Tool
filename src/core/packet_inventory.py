"""Convert decoded account packets into the existing Fribbels import contract."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping, Sequence
from typing import Any


STAT_TYPES = {
    "att_rate": "AttackPercent",
    "max_hp_rate": "HealthPercent",
    "def_rate": "DefensePercent",
    "att": "Attack",
    "max_hp": "Health",
    "def": "Defense",
    "speed": "Speed",
    "res": "EffectResistancePercent",
    "cri": "CriticalHitChancePercent",
    "cri_dmg": "CriticalHitDamagePercent",
    "acc": "EffectivenessPercent",
    "coop": "DualAttackChancePercent",
}
PERCENT_STATS = frozenset(
    {"att_rate", "max_hp_rate", "def_rate", "res", "cri", "cri_dmg", "acc", "coop"}
)
RANKS = ("Unknown", "Normal", "Good", "Rare", "Heroic", "Epic")
SLOTS = {
    "w": "Weapon",
    "h": "Helmet",
    "a": "Armor",
    "n": "Necklace",
    "r": "Ring",
    "b": "Boots",
}
SETS = {
    "set_acc": "HitSet",
    "set_att": "AttackSet",
    "set_coop": "UnitySet",
    "set_counter": "CounterSet",
    "set_cri_dmg": "DestructionSet",
    "set_cri": "CriticalSet",
    "set_def": "DefenseSet",
    "set_immune": "ImmunitySet",
    "set_max_hp": "HealthSet",
    "set_penetrate": "PenetrationSet",
    "set_rage": "RageSet",
    "set_res": "ResistSet",
    "set_revenge": "RevengeSet",
    "set_scar": "InjurySet",
    "set_speed": "SpeedSet",
    "set_vampire": "LifestealSet",
    "set_shield": "ProtectionSet",
    "set_torrent": "TorrentSet",
    "set_revenant": "ReversalSet",
    "set_riposte": "RiposteSet",
    "set_chase": "PursuitSet",
    "set_opener": "WarfareSet",
    "set_weak": "WeakeningSet",
    "set_might": "FervorSet",
}
MAIN_BASE_LEVELS = {
    ("acc", 0.12): 85,
    ("acc", 0.13): 88,
    ("att", 100): 85,
    ("att", 103): 88,
    ("att_rate", 0.12): 85,
    ("att_rate", 0.13): 88,
    ("cri", 0.11): 85,
    ("cri", 0.12): 88,
    ("cri_dmg", 0.13): 85,
    ("cri_dmg", 0.14): 88,
    ("def", 55): 75,
    ("def", 60): 85,
    ("def", 62): 88,
    ("def_rate", 0.12): 85,
    ("def_rate", 0.13): 88,
    ("max_hp", 540): 85,
    ("max_hp", 553): 88,
    ("max_hp_rate", 0.12): 85,
    ("max_hp_rate", 0.13): 88,
    ("res", 0.12): 85,
    ("res", 0.13): 88,
    ("speed", 8): 85,
    ("speed", 9): 88,
}


class PacketInventoryError(ValueError):
    pass


def normalize_account_inventory(
    account_data: Mapping[str, Any],
    *,
    hero_names: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], tuple[dict[str, str], ...]]:
    if not isinstance(account_data, Mapping):
        raise PacketInventoryError("Account data must be an object.")
    raw_equips = _rows(account_data.get("equips"), "equips")
    raw_units = _rows(account_data.get("units"), "units")
    items: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for raw in raw_equips:
        try:
            items.append(_normalize_item(raw))
        except PacketInventoryError as error:
            skipped.append({"itemId": str(raw.get("id", "unknown")), "reason": str(error)})

    names = dict(hero_names or {})
    heroes = []
    for raw in raw_units:
        stars = raw.get("g")
        if isinstance(stars, bool) or not isinstance(stars, int) or stars < 5:
            continue
        hero_id = raw.get("id")
        code = raw.get("code")
        if isinstance(hero_id, bool) or not isinstance(hero_id, (str, int)) or not isinstance(code, str):
            continue
        heroes.append(
            {
                "id": str(hero_id),
                "name": names.get(code, code),
                "stars": min(6, stars),
                "awaken": _bounded_grade(raw.get("z")),
                "code": code,
            }
        )
    return {"items": items, "heroes": heroes}, tuple(skipped)


def _rows(value: Any, field: str) -> list[Mapping[str, Any]]:
    rows: Sequence[Any]
    if isinstance(value, Mapping):
        rows = tuple(value.values())
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        rows = value
    else:
        raise PacketInventoryError(f"Account {field} must be an object or array.")
    return [row for row in rows if isinstance(row, Mapping)]


def _normalize_item(raw: Mapping[str, Any]) -> dict[str, Any]:
    item_id = raw.get("id")
    code = raw.get("code")
    grade = raw.get("g")
    operations = raw.get("op")
    if isinstance(item_id, bool) or not isinstance(item_id, (str, int)):
        raise PacketInventoryError("Equipment ID is missing.")
    if not isinstance(code, str) or not code:
        raise PacketInventoryError("Equipment code is missing.")
    if isinstance(grade, bool) or not isinstance(grade, int) or not 1 <= grade <= 5:
        raise PacketInventoryError("Equipment rank is unsupported.")
    parsed_operations = _operations(operations)
    gear_set = SETS.get(raw.get("f"))
    if gear_set is None:
        raise PacketInventoryError("Equipment set is absent from the account packet.")
    slot = _slot(code)
    if slot is None:
        raise PacketInventoryError(f"Equipment slot could not be inferred from code {code!r}.")
    enhancement = max((min(len(parsed_operations) - 1, grade + 4) - (grade - 1)) * 3, 0)
    level = _level(code, parsed_operations[0])
    if level == 0:
        raise PacketInventoryError(f"Equipment level could not be inferred from code {code!r}.")
    main_code, main_base, *_ = parsed_operations[0]
    main_type = STAT_TYPES.get(main_code)
    if main_type is None:
        raise PacketInventoryError(f"Main stat {main_code!r} is unsupported.")
    return {
        "id": str(item_id),
        "ingameId": str(item_id),
        "ingameEquippedId": (
            None
            if raw.get("p") in {None, 0, "0", ""}
            else str(raw["p"])
        ),
        "gear": slot,
        "rank": RANKS[grade],
        "set": gear_set,
        "name": "Unknown",
        "level": level,
        "enhance": min(15, enhancement),
        "main": {
            "type": main_type,
            "value": _main_value(main_code, main_base, min(15, enhancement)),
        },
        "substats": _substats(parsed_operations[1:]),
        "locked": bool(raw.get("l", False)),
        "code": code,
    }


def _operations(value: Any) -> list[tuple[Any, ...]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise PacketInventoryError("Equipment operations are missing.")
    parsed = []
    for raw in value:
        if (
            not isinstance(raw, Sequence)
            or isinstance(raw, (str, bytes))
            or len(raw) < 2
            or not isinstance(raw[0], str)
            or isinstance(raw[1], bool)
            or not isinstance(raw[1], (int, float))
        ):
            raise PacketInventoryError("Equipment operation is invalid.")
        parsed.append(tuple(raw))
    if not parsed:
        raise PacketInventoryError("Equipment operations are empty.")
    return parsed


def _slot(code: str) -> str | None:
    parts = code.split("_")
    candidates = [part[-1] for part in parts if part and part[-1] in SLOTS]
    return SLOTS.get(candidates[0]) if candidates else None


def _level(code: str, main_operation: tuple[Any, ...]) -> int:
    if code.endswith("_u"):
        return 90
    stat, value, *_ = main_operation
    rounded = round(float(value), 6)
    lookup_value: int | float = int(rounded) if rounded.is_integer() else rounded
    return MAIN_BASE_LEVELS.get((stat, lookup_value), 0)


def _main_value(code: str, base: int | float, enhancement: int) -> int | float:
    value = float(base) * (1 + (4 * enhancement / 15))
    if code in PERCENT_STATS:
        value *= 100
    return _rounded(value)


def _substats(operations: Sequence[tuple[Any, ...]]) -> list[dict[str, Any]]:
    totals: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for operation in operations:
        code, raw_value, *metadata = operation
        stat_type = STAT_TYPES.get(code)
        if stat_type is None:
            continue
        value = float(raw_value) if code not in PERCENT_STATS else float(raw_value) * 100
        annotation = metadata[0] if metadata else None
        current = totals.get(stat_type)
        if current is None:
            current = {
                "type": stat_type,
                "value": 0,
                "rolls": 1,
                "ingameRolls": 1,
            }
            totals[stat_type] = current
        else:
            if annotation == "c":
                current["modified"] = True
            elif annotation != "u":
                current["rolls"] += 1
                current["ingameRolls"] += 1
        current["value"] = _rounded(float(current["value"]) + value)
    return list(totals.values())


def _rounded(value: float) -> int | float:
    rounded = round(value, 1)
    return int(rounded) if rounded.is_integer() else rounded


def _bounded_grade(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return max(0, min(6, value))


__all__ = ["PacketInventoryError", "normalize_account_inventory"]
