"""Build the gear-archetype catalog from local Hero Journal screenshots."""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.generate_gear_archetypes import (  # noqa: E402
    BASE_KEYS,
    FIXED_LEFT_MAIN_STATS,
    FLAT_FALLBACKS,
    MIN_ROLL_INVESTMENT,
    ROLL_SIZES,
    _damage_scaling_stat,
    archetype_class,
)
from src.optimizer.domain import GearSet, gear_set_display_name  # noqa: E402


ARCHIVE = ROOT / ".local" / "hero-journal-builds"
DEFAULT_MANIFEST = ARCHIVE / "attachment-manifest.json"
DEFAULT_EVIDENCE = ARCHIVE / "build-evidence.json"
DEFAULT_OUTPUT = ROOT / "src" / "core" / "data" / "gear_archetypes.json"
DEFAULT_OVERRIDES = ROOT / "src" / "core" / "data" / "hero_journal_build_overrides.json"
DEFAULT_HERO_DATA = (
    ROOT / "src" / "optimizer" / "data" / "character_data" / "source" / "herodata.json"
)
SET_ASSETS = ROOT / "assets" / "equipment" / "sets"
STAT_LABELS = tuple(BASE_KEYS)
NUMBER = re.compile(r"(?<!\d)(\d[\d,.]*)")
SET_MATCH_THRESHOLD = 0.72
MIN_PER_HERO_SET_SUPPORT_PERCENT = 10
def _number(text: str) -> float | None:
    normalized = (
        text.replace("O", "0")
        .replace("o", "0")
        .replace("I", "1")
        .replace("|", "1")
    )
    match = NUMBER.search(normalized)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _stat_ranges(base: dict[str, float]) -> tuple[tuple[float, float], ...]:
    values = (
        base.get("atk", 600),
        base.get("def", 400),
        base.get("hp", 3000),
        base.get("spd", 70),
        100 * base.get("chc", 0.15),
        100 * base.get("chd", 1.5),
        100 * base.get("eff", 0),
        100 * base.get("efr", 0),
    )
    return (
        (max(500, 0.7 * values[0]), 12_000),
        (max(350, 0.7 * values[1]), 5_000),
        (max(2_500, 0.7 * values[2]), 60_000),
        (max(60, 0.8 * values[3]), 450),
        (max(0, values[4] - 1), 100.5),
        (max(100, values[5] - 1), 450),
        (max(0, values[6] - 1), 400),
        (max(0, values[7] - 1), 400),
    )


def select_stat_block(
    tokens: list[dict[str, float]],
    base: dict[str, float],
    image_width: int,
) -> dict[str, object] | None:
    """Select the aligned eight-value final-stat column from OCR tokens."""
    ranges = _stat_ranges(base)
    best: tuple[float, str, list[dict[str, float]]] | None = None
    for axis in ("x", "right"):
        first_rows = [token for token in tokens if ranges[0][0] <= token["value"] <= ranges[0][1]]
        for first in first_rows:
            anchor = first[axis]
            beam: list[tuple[list[dict[str, float]], float]] = [([first], 0.0)]
            for low, high in ranges[1:]:
                next_beam = []
                for sequence, cost in beam:
                    previous = sequence[-1]
                    for token in tokens:
                        gap = token["y"] - previous["y"]
                        alignment = abs(token[axis] - anchor)
                        if not (low <= token["value"] <= high and 5 < gap < 115 and alignment <= 48):
                            continue
                        gaps = [
                            sequence[index + 1]["y"] - sequence[index]["y"]
                            for index in range(len(sequence) - 1)
                        ]
                        gap_penalty = 0.0 if not gaps else abs(gap - statistics.median(gaps)) / 35
                        next_beam.append((
                            [*sequence, token],
                            cost + alignment / 28 + gap_penalty,
                        ))
                beam = sorted(next_beam, key=lambda item: item[1])[:120]
                if not beam:
                    break
            for sequence, cost in beam:
                gaps = [sequence[index + 1]["y"] - sequence[index]["y"] for index in range(7)]
                score = cost + statistics.pstdev(gaps) / 12 + anchor / max(image_width, 1) * 2.5
                if best is None or score < best[0]:
                    best = (score, axis, sequence)
    if best is None:
        return None
    score, axis, sequence = best
    return {
        "values": {
            label: (
                round(token["value"])
                if label in {"Attack", "Defense", "Health", "Speed"}
                else round(token["value"], 1)
            )
            for label, token in zip(STAT_LABELS, sequence)
        },
        "score": round(score, 3),
        "alignment": axis,
        "x": round(statistics.median(token["x"] for token in sequence)),
        "right": round(statistics.median(token["right"] for token in sequence)),
        "rows": [round(token["y"]) for token in sequence],
    }


def _ocr_tokens(image: Image.Image, *, mode: str, psm: int) -> list[dict[str, float]]:
    import pytesseract
    from pytesseract import Output

    source = image if mode == "rgb" else ImageOps.autocontrast(ImageOps.grayscale(image))
    data = pytesseract.image_to_data(source, config=f"--psm {psm}", output_type=Output.DICT)
    tokens = []
    for index, text in enumerate(data["text"]):
        value = _number(text)
        if value is None:
            continue
        x = int(data["left"][index])
        y = int(data["top"][index])
        width = int(data["width"][index])
        height = int(data["height"][index])
        tokens.append({
            "value": value,
            "x": float(x),
            "right": float(x + width),
            "y": y + height / 2,
            "confidence": float(data["conf"][index]),
        })
    return tokens


def extract_stat_block(path: Path, base: dict[str, float]) -> tuple[dict[str, object] | None, Image.Image]:
    image = Image.open(path).convert("RGB")
    width, height = image.size
    top = int(0.12 * height)
    for width_ratio, minimum_width in ((0.58, 900), (0.38, 1_000)):
        crop = image.crop((0, top, int(width_ratio * width), height))
        scale = max(1.0, minimum_width / crop.width)
        if scale > 1:
            crop = crop.resize((round(crop.width * scale), round(crop.height * scale)))

        tokens = _ocr_tokens(crop, mode="rgb", psm=6)
        block = select_stat_block(tokens, base, crop.width)
        if block is None:
            tokens.extend(_ocr_tokens(crop, mode="gray", psm=6))
            block = select_stat_block(tokens, base, crop.width)
        if block is None:
            tokens.extend(_ocr_tokens(crop, mode="gray", psm=11))
            block = select_stat_block(tokens, base, crop.width)
        if block is not None:
            block["cropScale"] = scale
            block["cropTop"] = top
            return block, image
    return None, image


def _set_templates() -> dict[str, object]:
    import cv2

    templates = {}
    valid = {gear_set.value for gear_set in GearSet}
    for path in SET_ASSETS.glob("set*.png"):
        set_id = f"set.{path.stem.removeprefix('set')}"
        if set_id in valid:
            templates[set_id] = cv2.imread(str(path), cv2.IMREAD_COLOR)
    return templates


def detect_sets(image: Image.Image, block: dict[str, object], templates: dict[str, object]) -> list[dict[str, object]]:
    import cv2
    import numpy as np

    scale = float(block["cropScale"])
    top = int(block["cropTop"])
    rows = [row / scale + top for row in block["rows"]]
    stat_x = max(float(block["right"]) / scale, float(block["x"]) / scale)
    source = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
    height, width = source.shape[:2]
    right = min(width, max(120, round(stat_x + 35)))
    regions = [
        (0, max(0, round(rows[0] - 190)), right, max(1, round(rows[0] - 4))),
        (0, min(height - 1, round(rows[-1] + 3)), right, min(height, round(rows[-1] + 220))),
    ]
    candidates = []
    for set_id, template in templates.items():
        best: tuple[float, int, int] | None = None
        for left, region_top, region_right, bottom in regions:
            region = source[region_top:bottom, left:region_right]
            if region.size == 0:
                continue
            for factor in (0.55, 0.65, 0.75, 0.85, 1.0, 1.15):
                resized = cv2.resize(template, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
                if resized.shape[0] > region.shape[0] or resized.shape[1] > region.shape[1]:
                    continue
                result = cv2.matchTemplate(region, resized, cv2.TM_CCOEFF_NORMED)
                _, score, _, location = cv2.minMaxLoc(result)
                candidate = (float(score), location[0] + left, location[1] + region_top)
                if best is None or candidate[0] > best[0]:
                    best = candidate
        if best is not None and best[0] >= SET_MATCH_THRESHOLD:
            candidates.append((best[0], best[1], best[2], set_id))

    selected = []
    for score, x, y, set_id in sorted(candidates, reverse=True):
        if any(abs(x - item[1]) < 18 and abs(y - item[2]) < 18 for item in selected):
            continue
        selected.append((score, x, y, set_id))
        if len(selected) == 3:
            break
    return [
        {
            "id": set_id,
            "name": gear_set_display_name(set_id),
            "score": round(score, 3),
        }
        for score, _x, _y, set_id in selected
    ]


def _hero_index(path: Path) -> tuple[dict[str, dict[str, object]], dict[str, dict[str, object]]]:
    records = json.loads(path.read_text(encoding="utf-8"))
    by_slug = {str(hero["_id"]): hero for hero in records.values()}
    by_name = {str(hero["name"]): hero for hero in records.values()}
    return by_slug, by_name


def extract_evidence(
    manifest_path: Path,
    hero_data_path: Path,
    workers: int,
) -> dict[str, object]:
    import pytesseract

    pytesseract.pytesseract.tesseract_cmd = str(
        ROOT / "Programs" / "Tesseract-OCR" / "tesseract.exe"
    )
    if not Path(pytesseract.pytesseract.tesseract_cmd).exists():
        pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    by_slug, by_name = _hero_index(hero_data_path)
    templates = _set_templates()
    attachments = []
    for channel in manifest["channels"]:
        for attachment in channel["attachments"]:
            attachments.append((channel, attachment))

    def extract(item: tuple[dict[str, object], dict[str, object]]) -> dict[str, object]:
        channel, attachment = item
        slug = str(channel["slug"])
        filename = f'{attachment["attachmentId"]}-{attachment["filename"]}'
        path = ARCHIVE / "images" / slug / filename
        hero = by_slug.get(slug) or by_name.get(str(channel["name"]))
        status = (hero or {}).get("calculatedStatus", {}).get("lv60SixStarFullyAwakened", {})
        try:
            block, image = extract_stat_block(path, status)
            sets = [] if block is None else detect_sets(image, block, templates)
            return {
                "hero": str(channel["name"]),
                "heroSlug": slug,
                "attachmentId": str(attachment["attachmentId"]),
                "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                "status": "reference" if block is None else "build",
                "stats": None if block is None else block["values"],
                "sets": sets,
                "ocr": None if block is None else {
                    "score": block["score"],
                    "alignment": block["alignment"],
                },
            }
        except Exception as error:
            return {
                "hero": str(channel["name"]),
                "heroSlug": slug,
                "attachmentId": str(attachment["attachmentId"]),
                "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                "status": "error",
                "error": f"{type(error).__name__}: {error}",
            }

    with ThreadPoolExecutor(max_workers=workers) as pool:
        records = list(pool.map(extract, attachments))
    status_counts = Counter(record["status"] for record in records)
    builds_by_hero = Counter(record["hero"] for record in records if record["status"] == "build")
    return {
        "schemaVersion": 1,
        "source": {
            "name": "Hero Journal Discord",
            "serverId": manifest["server"]["id"],
            "manifest": str(manifest_path.relative_to(ROOT)).replace("\\", "/"),
            "processedAt": datetime.now(timezone.utc).isoformat(),
        },
        "summary": {
            "attachments": len(records),
            "statuses": dict(sorted(status_counts.items())),
            "heroesWithBuilds": len(builds_by_hero),
            "heroesWithoutBuilds": sorted(
                str(channel["name"])
                for channel in manifest["channels"]
                if builds_by_hero[str(channel["name"])] == 0
            ),
        },
        "records": records,
    }


def apply_overrides(evidence: dict[str, object], path: Path) -> dict[str, object]:
    """Apply the small, reviewed queue that OCR cannot read unambiguously."""
    if not path.exists():
        return evidence
    overrides = json.loads(path.read_text(encoding="utf-8"))["records"]
    records_by_id = {record["attachmentId"]: record for record in evidence["records"]}
    missing = sorted(set(overrides) - set(records_by_id))
    if missing:
        raise ValueError(f"Manual overrides reference unknown attachments: {', '.join(missing)}")
    for attachment_id, override in overrides.items():
        record = records_by_id[attachment_id]
        if "status" in override:
            record["status"] = override["status"]
        if "stats" in override:
            record["stats"] = override["stats"]
            record["ocr"] = None
        if "sets" in override:
            record["sets"] = [
                {"id": set_id, "name": gear_set_display_name(set_id), "score": None}
                for set_id in override["sets"]
            ]
        if "desiredStats" in override:
            unknown = set(override["desiredStats"]) - set(STAT_LABELS)
            if unknown:
                raise ValueError(f"Unknown desired stats for {attachment_id}: {sorted(unknown)}")
            record["desiredStats"] = override["desiredStats"]
        if "scalingStat" in override:
            if override["scalingStat"] not in {"Attack", "Defense", "Health", "Speed"}:
                raise ValueError(f"Unknown scaling stat for {attachment_id}: {override['scalingStat']}")
            record["scalingStat"] = override["scalingStat"]
        record["manualReview"] = override["note"]

    statuses = Counter(record["status"] for record in evidence["records"])
    builds = Counter(record["hero"] for record in evidence["records"] if record["status"] == "build")
    manifest = json.loads((ROOT / evidence["source"]["manifest"]).read_text(encoding="utf-8"))
    evidence["summary"].update({
        "statuses": dict(sorted(statuses.items())),
        "heroesWithBuilds": len(builds),
        "heroesWithoutBuilds": sorted(
            channel["name"] for channel in manifest["channels"] if not builds[channel["name"]]
        ),
        "manualOverrides": len(overrides),
    })
    return evidence


def estimate_build_investment(
    stats: dict[str, float],
    base: dict[str, float],
) -> dict[str, float]:
    investment = {}
    for stat in STAT_LABELS:
        final = float(stats[stat])
        base_value = float(base[BASE_KEYS[stat]])
        if stat in FIXED_LEFT_MAIN_STATS:
            gained = max(0.0, 100 * ((final - FIXED_LEFT_MAIN_STATS[stat]) / base_value - 1))
        elif stat in {"Critical Hit Chance", "Critical Hit Damage", "Effectiveness", "Effect Resistance"}:
            gained = max(0.0, final - 100 * base_value)
        else:
            gained = max(0.0, final - base_value)
        investment[stat] = gained / ROLL_SIZES[stat]
    return investment


def build_signature(
    investment: dict[str, float],
    desired_stats: list[str] | None = None,
) -> tuple[str, ...]:
    if desired_stats is not None:
        return tuple(sorted(desired_stats))
    return tuple(sorted(
        stat for stat, rolls in investment.items()
        if rolls >= MIN_ROLL_INVESTMENT[stat]
    ))


def resolve_scaling(
    signature: tuple[str, ...],
    reported_scaling: str,
    reviewed_scaling: str | None = None,
) -> str:
    return reviewed_scaling or (reported_scaling if reported_scaling in signature else "Attack")


def filter_reviewed_stat_groups(
    groups: list[tuple[str, ...]],
    desired_stats: list[str] | None,
) -> list[tuple[str, ...]]:
    if desired_stats is None:
        return groups
    desired = set(desired_stats)
    return [filtered for group in groups if (filtered := tuple(stat for stat in group if stat in desired))]


def _slug(parts: tuple[str, ...]) -> str:
    return "-".join(re.sub(r"[^a-z0-9]+", "-", part.lower()).strip("-") for part in parts)


def functional_archetype(
    signature: tuple[str, ...],
    scaling: str,
    build_class: str,
) -> dict[str, object]:
    """Collapse exact screenshot signatures into gear-relevant build functions."""
    stats = set(signature)
    has_cr = "Critical Hit Chance" in stats
    has_cd = "Critical Hit Damage" in stats
    has_eff = "Effectiveness" in stats
    has_er = "Effect Resistance" in stats
    has_bulk = bool({"Health", "Defense"} & stats)

    def result(
        name: str,
        archetype_class_name: str,
        groups: list[tuple[str, ...]],
        scaling_stat: str = scaling,
    ) -> dict[str, object]:
        groups = list(dict.fromkeys(groups))
        return {
            "id": _slug((name,)),
            "name": name,
            "archetypeClass": archetype_class_name,
            "scalingStat": scaling_stat,
            "substatGroups": groups,
        }

    if has_eff and not has_cr and not has_cd:
        if "Attack" in stats:
            groups = [
                ("Attack",),
                ("Speed",),
                ("Effectiveness",),
                ("Health", "Defense"),
            ]
            if has_er:
                groups.append(("Effect Resistance",))
            prefix = "ER " if has_er else ""
            return result(f"{prefix}Fast Attack-Scaling Debuffer", "Support", groups, "Attack")
        if has_er:
            return result(
                "ER Effectiveness Support",
                "Support",
                [
                    ("Speed",),
                    ("Effectiveness",),
                    ("Effect Resistance",),
                    ("Health", "Defense"),
                ],
                "Attack",
            )
        return result(
            "Fast Debuffer",
            "Support",
            [("Speed",), ("Effectiveness",), ("Health",), ("Defense",)],
            "Attack",
        )

    if has_eff and has_cr and not has_cd and "Attack" not in stats:
        return result(
            "Fast Crit-Chance Debuffer",
            "Support",
            [
                ("Speed",),
                ("Effectiveness",),
                ("Critical Hit Chance",),
                ("Health", "Defense"),
            ],
            "Attack",
        )

    if not ({"Attack", "Critical Hit Chance", "Critical Hit Damage", "Effectiveness"} & stats) and has_bulk:
        if has_er:
            if "Speed" in stats:
                return result(
                    "ER Tank",
                    "Tank",
                    [("Speed",), ("Effect Resistance",), ("Health",), ("Defense",)],
                    "Attack",
                )
            return result(
                "ER Bulk Tank",
                "Tank",
                [("Effect Resistance",), ("Health",), ("Defense",)],
                "Attack",
            )
        return result(
            "Fast Tank",
            "Tank",
            [("Speed",), ("Health",), ("Defense",)],
            "Attack",
        )

    if has_cr or has_cd:
        damage_class = "Bruiser" if has_bulk else "DPS"
        parts = []
        if has_er:
            parts.append("ER")
        if has_eff:
            parts.append("Effectiveness")
        if not has_cr:
            parts.append("Non-Crit-Chance")
        name = " ".join([*parts, f"{scaling}-Scaling", damage_class])
        groups = [(scaling,), ("Speed",)]
        if scaling == "Speed":
            groups.append(("Attack",))
        if has_cr:
            groups.append(("Critical Hit Chance",))
        groups.append(("Critical Hit Damage",))
        if damage_class == "Bruiser":
            if scaling in {"Attack", "Speed"}:
                groups.append(("Health", "Defense"))
            elif scaling == "Health":
                groups.append(("Defense",))
            elif scaling == "Defense":
                groups.append(("Health",))
        if has_eff:
            groups.append(("Effectiveness",))
        if has_er:
            groups.append(("Effect Resistance",))
        return result(name, damage_class, groups)

    if "Attack" in stats:
        prefix = "ER " if has_er else ""
        groups = [("Attack",), ("Speed",), ("Health", "Defense")]
        if has_er:
            groups.append(("Effect Resistance",))
        return result(
            f"{prefix}Non-Crit Attack-Scaling Bruiser",
            "Bruiser",
            groups,
            "Attack",
        )

    if scaling in {"Health", "Defense", "Speed"} and scaling in stats:
        prefix = "ER " if has_er else ""
        groups = [(scaling,), ("Speed",)]
        if scaling in {"Health", "Speed"}:
            groups.append(("Defense",))
        if scaling in {"Defense", "Speed"}:
            groups.append(("Health",))
        if has_er:
            groups.append(("Effect Resistance",))
        return result(
            f"{prefix}Non-Crit {scaling}-Scaling Bruiser",
            "Bruiser",
            groups,
        )

    if has_er:
        return result(
            "ER Tank",
            "Tank",
            [("Speed",), ("Effect Resistance",), ("Health",), ("Defense",)],
            "Attack",
        )

    return result("Fast Tank", "Tank", [("Speed",), ("Health",), ("Defense",)], "Attack")


def build_catalog(evidence: dict[str, object], hero_data_path: Path) -> dict[str, object]:
    by_slug, by_name = _hero_index(hero_data_path)
    profiles = []
    for record in evidence["records"]:
        if record["status"] != "build" or not record["sets"]:
            continue
        hero = by_slug.get(record["heroSlug"]) or by_name.get(record["hero"])
        if not hero:
            continue
        base = hero["calculatedStatus"]["lv60SixStarFullyAwakened"]
        investment = estimate_build_investment(record["stats"], base)
        signature = build_signature(investment, record.get("desiredStats"))
        reported_scaling = _damage_scaling_stat(hero)
        scaling = resolve_scaling(signature, reported_scaling, record.get("scalingStat"))
        build_class = archetype_class(signature, scaling, str(hero.get("role", "")))
        if build_class in {"Tank", "Support"}:
            scaling = "Attack"
        functional = functional_archetype(signature, scaling, build_class)
        functional["substatGroups"] = filter_reviewed_stat_groups(
            functional["substatGroups"],
            record.get("desiredStats"),
        )
        profiles.append({
            "hero": record["hero"],
            "attachmentId": record["attachmentId"],
            "signature": signature,
            "functional": functional,
            "investment": investment,
            "sets": tuple(item["id"] for item in record["sets"]),
        })

    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for profile in profiles:
        grouped[profile["functional"]["id"]].append(profile)

    archetypes = []
    for _functional_id, group in sorted(grouped.items()):
        functional = group[0]["functional"]
        stat_groups = functional["substatGroups"]
        preferred_stats = list(dict.fromkeys(
            stat for stat_group in stat_groups for stat in stat_group
        ))
        heroes = sorted({profile["hero"] for profile in group})
        set_counts = Counter(set_id for profile in group for set_id in profile["sets"])
        hero_build_counts = Counter(profile["hero"] for profile in group)
        hero_set_counts = Counter(
            (profile["hero"], set_id)
            for profile in group
            for set_id in profile["sets"]
        )
        heroes_by_set = defaultdict(list)
        for (hero, set_id), count in hero_set_counts.items():
            minimum_set_support = max(
                1,
                math.ceil(
                    hero_build_counts[hero] * MIN_PER_HERO_SET_SUPPORT_PERCENT / 100
                ),
            )
            if count >= minimum_set_support:
                heroes_by_set[set_id].append(hero)
        supported_sets = [
            (set_id, count)
            for set_id, count in set_counts.most_common()
            if set_id in heroes_by_set
        ]
        compatible_sets = [set_id for set_id, _count in supported_sets]
        archetypes.append({
            "id": functional["id"],
            "name": functional["name"],
            "archetypeClass": functional["archetypeClass"],
            "scalingStat": functional["scalingStat"],
            "heroes": heroes,
            "preferredStats": preferred_stats,
            "substatGroups": [list(stat_group) for stat_group in stat_groups],
            "flatStatFallbacks": sorted(
                FLAT_FALLBACKS[stat] for stat in preferred_stats if stat in FLAT_FALLBACKS
            ),
            "compatibleSets": compatible_sets,
            "heroesBySet": {
                set_id: sorted(heroes_by_set[set_id])
                for set_id in compatible_sets
            },
            "setEvidence": [
                {
                    "id": set_id,
                    "name": gear_set_display_name(set_id),
                    "buildSupportCount": count,
                    "buildSupportPercent": round(100 * count / len(group), 2),
                    "heroes": sorted(heroes_by_set[set_id]),
                }
                for set_id, count in supported_sets
            ],
            "averageMaxRollInvestment": {
                stat: round(statistics.mean(profile["investment"][stat] for profile in group), 2)
                for stat in STAT_LABELS
            },
            "population": len(heroes),
            "buildCount": len(group),
            "sourceAttachments": sorted({profile["attachmentId"] for profile in group}),
        })

    return {
        "schemaVersion": 4,
        "source": {
            **evidence["source"],
            "method": (
                "Exact final stats and displayed equipment-set icons were extracted from each "
                "locally archived Hero Journal image. Functional archetypes are derived from "
                "estimated max-roll investment after subtracting hero base stats and universal "
                "left-side mains; interchangeable bulk stats share one conceptual stat slot."
            ),
            "attachmentsReviewed": evidence["summary"]["attachments"],
            "buildScreenshots": evidence["summary"]["statuses"].get("build", 0),
            "cataloguedBuilds": len(profiles),
            "heroesWithCataloguedBuilds": len({profile["hero"] for profile in profiles}),
            "manualOverrides": evidence["summary"].get("manualOverrides", 0),
            "minimumRollInvestment": MIN_ROLL_INVESTMENT,
            "minimumPerHeroSetSupportPercent": MIN_PER_HERO_SET_SUPPORT_PERCENT,
        },
        "archetypes": archetypes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--hero-data", type=Path, default=DEFAULT_HERO_DATA)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--reuse-evidence", action="store_true")
    parser.add_argument("--write-catalog", action="store_true")
    args = parser.parse_args()

    if args.reuse_evidence:
        evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    else:
        evidence = extract_evidence(args.manifest, args.hero_data, max(1, args.workers))
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence = apply_overrides(evidence, args.overrides)
    args.evidence.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(evidence["summary"], indent=2, ensure_ascii=False))
    if args.write_catalog:
        catalog = build_catalog(evidence, args.hero_data)
        args.output.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f'Wrote {len(catalog["archetypes"])} archetypes to {args.output}')


if __name__ == "__main__":
    main()
