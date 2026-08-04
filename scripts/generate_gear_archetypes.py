"""Generate Emperor gear archetypes from Epic Seven's hero records."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.packet_inventory import SETS  # noqa: E402
from src.optimizer.domain import gear_set_display_name, resolve_gear_set  # noqa: E402


API_ROOT = "https://e7api.onstove.com/gameApi"
SOURCE_PAGE = "https://epic7.onstove.com/en/gg/herorecord"
GRADE = "emperor"
MIN_SET_USAGE_PERCENT = 5
PEAK_WINDOW_BINS = 3
DEFAULT_OUTPUT = ROOT / "src" / "core" / "data" / "gear_archetypes.json"
DEFAULT_CACHE = ROOT / ".local" / "emperor-hero-records.json"
DEFAULT_HERO_DATA = (
    ROOT / "src" / "optimizer" / "data" / "character_data" / "source" / "herodata.json"
)

# The site renders ten bars between these eleven fixed boundaries.
HISTOGRAMS = {
    "att": ("Attack", [1200, 1600, 2000, 2400, 2800, 3200, 3600, 4000, 4400, 4800, 5200]),
    "def": ("Defense", [800, 960, 1120, 1280, 1440, 1600, 1760, 1920, 2080, 2240, 2400]),
    "max_hp": ("Health", [9000, 10600, 12200, 13800, 15400, 17000, 18600, 20200, 21800, 23400, 25000]),
    "speed": ("Speed", [110, 126, 142, 158, 174, 190, 206, 222, 238, 254, 270]),
    "cri": ("Critical Hit Chance", [15, 24, 32, 41, 49, 58, 66, 75, 83, 92, 100]),
    "cri_dmg": ("Critical Hit Damage", [150, 170, 190, 210, 230, 250, 270, 290, 310, 330, 350]),
    "acc": ("Effectiveness", [0, 18, 36, 54, 72, 90, 108, 126, 144, 162, 180]),
    "res": ("Effect Resistance", [0, 23, 45, 68, 90, 113, 135, 158, 180, 203, 225]),
}

BASE_KEYS = {
    "Attack": "atk",
    "Defense": "def",
    "Health": "hp",
    "Speed": "spd",
    "Critical Hit Chance": "chc",
    "Critical Hit Damage": "chd",
    "Effectiveness": "eff",
    "Effect Resistance": "efr",
}

# Approximate maximum level-85 substat rolls. The result is a comparable
# investment measure, not a claim about the exact gear worn by an individual.
ROLL_SIZES = {
    "Attack": 8,
    "Defense": 8,
    "Health": 8,
    "Speed": 4,
    "Critical Hit Chance": 5,
    "Critical Hit Damage": 7,
    "Effectiveness": 8,
    "Effect Resistance": 8,
}

MIN_ROLL_INVESTMENT = {
    "Attack": 5,
    "Defense": 5,
    "Health": 5,
    "Speed": 3,
    "Critical Hit Chance": 5,
    "Critical Hit Damage": 5,
    "Effectiveness": 5,
    "Effect Resistance": 5,
}

# Histogram peaks describe population-wide investment, but a few sparse or
# unusually specialized builds need their full desired-stat pool restored from
# the supplied finished builds.
PREFERRED_STAT_EXCEPTIONS = {
    "Aki": ("Attack", "Effect Resistance", "Effectiveness", "Speed"),
    "Inheritor Amiki": ("Attack", "Effect Resistance", "Health", "Speed"),
    "Ivana": ("Defense", "Effect Resistance", "Health", "Speed"),
    "Mort": ("Critical Hit Chance", "Critical Hit Damage", "Health", "Speed"),
    "Politis": ("Defense", "Effectiveness", "Health", "Speed"),
    "Rimuru": ("Attack", "Defense", "Effect Resistance", "Health", "Speed"),
    "Schniel": ("Defense", "Effect Resistance", "Health", "Speed"),
    "Solitaria of the Snow": (
        "Defense",
        "Effect Resistance",
        "Effectiveness",
        "Health",
        "Speed",
    ),
    "Successor Taeyou": ("Attack", "Critical Hit Damage", "Effectiveness"),
    "Wanderer Silk": ("Defense", "Effectiveness", "Health", "Speed"),
    "Zeno": ("Critical Hit Chance", "Critical Hit Damage", "Defense", "Speed"),
}

# These level-90 left-side main stats are present on every fully equipped hero
# and therefore say nothing about build intent.
FIXED_LEFT_MAIN_STATS = {
    "Attack": 525,
    "Defense": 310,
    "Health": 2835,
}

FLAT_FALLBACKS = {
    "Attack": "Flat Attack",
    "Defense": "Flat Defense",
    "Health": "Flat Health",
}


def _post(endpoint: str, **parameters: object) -> object:
    url = f"{API_ROOT}/{endpoint}?{urlencode(parameters)}"
    request = Request(url, data=b"", method="POST", headers={"User-Agent": "Meowtoko-E7-Tool/1"})
    error: Exception | None = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.load(response)
            if payload.get("code") != 0:
                raise RuntimeError(f"{endpoint}: {payload.get('message', 'API error')}")
            return payload["value"]
        except Exception as exc:  # network failures need the same retry path
            error = exc
            if attempt < 2:
                time.sleep(1 + attempt)
    raise RuntimeError(f"Failed to fetch {endpoint}: {error}") from error


def fetch_snapshot(season_code: str | None = None, workers: int = 4) -> dict[str, object]:
    seasons = _post("getSeasonList", lang="en")["result_body"]
    if not isinstance(seasons, list) or not seasons:
        raise RuntimeError("The season list was empty")
    if season_code is None:
        season = seasons[0]
    else:
        season = next((item for item in seasons if item.get("season_code") == season_code), None)
        if season is None:
            raise ValueError(f"Unknown season: {season_code}")

    season_code = season["season_code"]
    first_page = _post(
        "getPopularHero",
        season_code=season_code,
        grade_code=GRADE,
        current_page=1,
        lang="en",
    )
    total_count = int(first_page["total_count"])
    page_count = math.ceil(total_count / 10)

    def fetch_page(page: int) -> list[dict[str, object]]:
        result = _post(
            "getPopularHero",
            season_code=season_code,
            grade_code=GRADE,
            current_page=page,
            lang="en",
        )
        return result["result_body"]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        remaining_pages = pool.map(fetch_page, range(2, page_count + 1))
        heroes = [*first_page["result_body"]]
        for records in remaining_pages:
            heroes.extend(records)

    # Match the page's own filtering: monsters and zero-pick records are not heroes in the table.
    heroes = [
        hero for hero in heroes
        if float(hero.get("pick_rate", 0)) > 0 and not str(hero.get("hero_code", "")).startswith("m")
    ]
    heroes_by_code = {str(hero["hero_code"]): hero for hero in heroes}

    def fetch_detail(code: str) -> dict[str, object]:
        return _post(
            "getHeroAnalysis",
            hero_code=code,
            season_code=season_code,
            grade_code=GRADE,
            lang="en",
        )["result_body"]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        details = list(pool.map(fetch_detail, sorted(heroes_by_code)))

    return {
        "season": season,
        "grade": GRADE,
        "totalCount": total_count,
        "heroes": [heroes_by_code[code] for code in sorted(heroes_by_code)],
        "details": details,
    }


def _damage_scaling_stat(hero: dict[str, object]) -> str:
    scores = {"Health": 0.0, "Defense": 0.0, "Speed": 0.0}
    for skill in hero.get("skills", {}).values():
        if not skill.get("hitTypes"):
            continue
        for stat, key in (
            ("Health", "selfHpScaling"),
            ("Defense", "selfDefScaling"),
            ("Speed", "selfSpdScaling"),
        ):
            scores[stat] += float(skill.get(key, 0) or 0)
    scaling = max(scores, key=scores.get)
    return scaling if scores[scaling] > 0 else "Attack"


def load_base_stats(path: Path) -> dict[str, dict[str, object]]:
    records = json.loads(path.read_text(encoding="utf-8"))
    result = {}
    for hero in records.values():
        status = hero.get("calculatedStatus", {}).get("lv60SixStarFullyAwakened")
        if status and hero.get("code"):
            result[str(hero["code"])] = {
                **{key: float(value) for key, value in status.items()},
                "role": str(hero.get("role", "")),
                "scalingStat": _damage_scaling_stat(hero),
            }
    return result


def dominant_histogram_value(counts: str, boundaries: list[int]) -> float:
    values = [int(value) for value in counts.split(",")]
    if len(values) != len(boundaries) - 1 or sum(values) <= 0:
        raise ValueError("Expected ten non-empty histogram bins")
    starts = range(len(values) - PEAK_WINDOW_BINS + 1)
    start = max(
        starts,
        key=lambda index: (
            sum(values[index:index + PEAK_WINDOW_BINS]),
            max(values[index:index + PEAK_WINDOW_BINS]),
            -index,
        ),
    )
    peak = values[start:start + PEAK_WINDOW_BINS]
    # Use lower boundaries so a coarse bucket cannot invent unsupported rolls.
    lower_bounds = boundaries[start:start + PEAK_WINDOW_BINS]
    return sum(count * value for count, value in zip(peak, lower_bounds)) / sum(peak)


def estimate_investment(ability: dict[str, str], base: dict[str, object]) -> dict[str, float]:
    investment = {}
    for api_key, (stat, boundaries) in HISTOGRAMS.items():
        final_value = dominant_histogram_value(ability[api_key], boundaries)
        base_value = float(base[BASE_KEYS[stat]])
        if stat in {"Attack", "Defense", "Health"}:
            final_without_fixed_main = final_value - FIXED_LEFT_MAIN_STATS[stat]
            gained = max(0.0, 100 * (final_without_fixed_main / base_value - 1))
        elif stat in {"Critical Hit Chance", "Critical Hit Damage", "Effectiveness", "Effect Resistance"}:
            gained = max(0.0, final_value - 100 * base_value)
        else:
            gained = max(0.0, final_value - base_value)
        investment[stat] = gained / ROLL_SIZES[stat]
    return investment


def preferred_stats(investment: dict[str, float]) -> tuple[str, ...]:
    return tuple(sorted(
        stat for stat, value in investment.items()
        if value >= MIN_ROLL_INVESTMENT[stat]
    ))


def archetype_class(signature: tuple[str, ...], scaling_stat: str, hero_role: str) -> str:
    stats = set(signature)
    bulk = bool({"Health", "Defense"} & stats)
    crit = bool({"Critical Hit Chance", "Critical Hit Damage"} & stats)
    deals_damage = hero_role != "manauser" and (
        crit or "Attack" in stats or (scaling_stat != "Attack" and scaling_stat in stats)
    )
    if deals_damage:
        return "Bruiser" if bulk else "DPS"
    if bulk and ("Effect Resistance" in stats or hero_role == "knight"):
        return "Tank"
    return "Support"


def archetype_name(signature: tuple[str, ...], scaling_stat: str, build_class: str) -> str:
    stats = set(signature)
    if build_class in {"Bruiser", "DPS"}:
        parts = []
        if "Effect Resistance" in stats:
            parts.append("ER")
        if "Effectiveness" in stats:
            parts.append("Effectiveness")
        if "Critical Hit Chance" not in stats:
            parts.append("Non-Crit-Chance")
        return " ".join([*parts, f"{scaling_stat}-Scaling", build_class])
    if build_class == "Tank":
        parts = []
        if "Effect Resistance" in stats:
            parts.append("ER")
        if "Effectiveness" in stats:
            parts.append("Effectiveness")
        return " ".join([*parts, "Tank"])
    if "Effectiveness" in stats and "Speed" in stats:
        return "Effectiveness Opener"
    if "Effect Resistance" in stats:
        return "ER Support"
    if "Speed" in stats:
        return "Fast Support"
    if "Effectiveness" in stats:
        return "Effectiveness Support"
    return "Support"


def canonical_set(api_set: str) -> tuple[str, str]:
    try:
        gear_set = resolve_gear_set(SETS[api_set])
    except (KeyError, ValueError):
        raise ValueError(f"Unknown equipment set code: {api_set}") from None
    return gear_set.value, gear_set_display_name(gear_set)


def _hero_profiles(snapshot: dict[str, object], base_stats: dict[str, dict[str, object]]) -> list[dict[str, object]]:
    pick_rates = {str(hero["hero_code"]): float(hero["pick_rate"]) for hero in snapshot["heroes"]}
    hero_names = {
        str(hero["hero_code"]): str(hero["hero_names"][str(hero["hero_code"])])
        for hero in snapshot["heroes"]
    }
    profiles = []
    missing = []
    for detail in snapshot["details"]:
        code = str(detail["heroCode"])
        if code not in base_stats:
            missing.append(code)
            continue
        equipment = detail.get("equip") or []
        if not equipment:
            continue
        investment = estimate_investment(detail["abillity"], base_stats[code])
        hero_name = hero_names[code]
        signature = PREFERRED_STAT_EXCEPTIONS.get(hero_name, preferred_stats(investment))
        if not signature:
            continue
        reported_scaling = str(base_stats[code].get("scalingStat", "Attack"))
        scaling_stat = reported_scaling if reported_scaling in signature else "Attack"
        build_class = archetype_class(
            signature,
            scaling_stat,
            str(base_stats[code].get("role", "")),
        )
        set_usage: dict[str, float] = defaultdict(float)
        set_names = {}
        for combination in equipment:
            # A stacked two-piece set remains one compatible individual set.
            for api_set in set(combination["equip_list"]):
                set_id, display_name = canonical_set(api_set)
                set_usage[set_id] += float(combination["rate"])
                set_names[set_id] = display_name
        profiles.append({
            "code": code,
            "name": hero_name,
            "pickRate": pick_rates.get(code, 0),
            "signature": signature,
            "scalingStat": scaling_stat,
            "class": build_class,
            "investment": investment,
            "setUsage": dict(set_usage),
            "setNames": set_names,
            "snapshotAt": detail.get("regDate"),
        })
    if missing:
        print(f"Skipped {len(missing)} records missing bundled base stats", file=sys.stderr)
    return profiles


def build_catalog(snapshot: dict[str, object], base_stats: dict[str, dict[str, object]]) -> dict[str, object]:
    profiles = _hero_profiles(snapshot, base_stats)
    grouped: dict[tuple[tuple[str, ...], str, str], list[dict[str, object]]] = defaultdict(list)
    for profile in profiles:
        grouped[(profile["signature"], profile["scalingStat"], profile["class"])].append(profile)

    archetypes = []
    for (signature, scaling_stat, build_class), members in grouped.items():
        population = len(members)
        average_investment = {
            stat: round(sum(member["investment"][stat] for member in members) / population, 2)
            for stat, _boundaries in HISTOGRAMS.values()
        }
        set_totals: dict[str, float] = defaultdict(float)
        set_support: dict[str, int] = defaultdict(int)
        set_names = {}
        for member in members:
            for set_id, usage in member["setUsage"].items():
                set_totals[set_id] += usage
                set_support[set_id] += 1
                set_names[set_id] = member["setNames"][set_id]
        sets = [{
            "id": set_id,
            "name": set_names[set_id],
            "averageUsagePercent": round(set_totals[set_id] / population, 2),
            "populationSupportPercent": round(100 * set_support[set_id] / population, 2),
        } for set_id in set_totals if set_totals[set_id] / population >= MIN_SET_USAGE_PERCENT]
        sets.sort(key=lambda item: (-item["averageUsagePercent"], item["name"]))
        stat_slug = "-".join(stat.lower().replace(" ", "-") for stat in signature)
        slug = f"{scaling_stat.lower()}-{build_class.lower()}-{stat_slug}"
        archetypes.append({
            "id": slug,
            "name": archetype_name(signature, scaling_stat, build_class),
            "archetypeClass": build_class,
            "scalingStat": scaling_stat,
            "heroes": sorted(member["name"] for member in members),
            "preferredStats": list(signature),
            "flatStatFallbacks": [FLAT_FALLBACKS[stat] for stat in signature if stat in FLAT_FALLBACKS],
            "compatibleSets": [item["id"] for item in sets],
            "setEvidence": sets,
            "averageMaxRollInvestment": average_investment,
            "population": population,
            "metaPickRatePercent": round(sum(member["pickRate"] for member in members), 2),
        })

    archetypes.sort(key=lambda item: (-item["population"], -item["metaPickRatePercent"], item["id"]))
    snapshot_times = [profile["snapshotAt"] for profile in profiles if profile.get("snapshotAt")]
    season = snapshot["season"]
    return {
        "schemaVersion": 2,
        "source": {
            "page": SOURCE_PAGE,
            "seasonCode": season["season_code"],
            "seasonName": season["name"],
            "seasonStart": season["startDate"],
            "seasonEnd": season["endDate"],
            "grade": GRADE,
            "snapshotAt": max(snapshot_times, default=None),
            "heroRecordsFetched": len(snapshot["details"]),
            "populationRecords": len(profiles),
            "excludedRecords": len(snapshot["details"]) - len(profiles),
            "minimumSetUsagePercent": MIN_SET_USAGE_PERCENT,
            "peakWindowBins": PEAK_WINDOW_BINS,
            "minimumRollInvestment": MIN_ROLL_INVESTMENT,
            "preferredStatExceptions": sorted(PREFERRED_STAT_EXCEPTIONS),
            "method": "Dominant contiguous histogram peaks use conservative lower boundaries, then subtract base and universal left-side main stats and convert the remainder to approximate max-roll investment.",
            "limitation": "The source publishes marginal population histograms, not stat distributions per equipment-set combination.",
        },
        "archetypes": archetypes,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--hero-data", type=Path, default=DEFAULT_HERO_DATA)
    parser.add_argument("--season", help="Season code; defaults to the newest season shown by the site")
    parser.add_argument("--refresh", action="store_true", help="Ignore the raw local cache")
    parser.add_argument("--workers", type=int, default=4)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not 1 <= args.workers <= 8:
        raise ValueError("--workers must be between 1 and 8")
    if args.cache.exists() and not args.refresh:
        snapshot = json.loads(args.cache.read_text(encoding="utf-8"))
    else:
        snapshot = fetch_snapshot(args.season, args.workers)
        args.cache.parent.mkdir(parents=True, exist_ok=True)
        args.cache.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")

    catalog = build_catalog(snapshot, load_base_stats(args.hero_data))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"ARCHETYPES_OK season={catalog['source']['seasonCode']} grade={GRADE} "
        f"records={catalog['source']['populationRecords']} archetypes={len(catalog['archetypes'])} "
        f"output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
