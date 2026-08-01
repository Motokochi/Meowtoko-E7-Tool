"""Validated gear-analyzer operations shared by desktop and legacy workflows."""

from __future__ import annotations

import copy
import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from src.constants import ALL_SETS, ALL_SLOTS, ALL_STATS, RESTRICTED_SUBSTATS, SLOT_MAIN_STATS
from src.core.gear_evaluator import (
    build_gs_string,
    calculate_gear_score,
    calculate_gear_score_details,
    evaluate_archetypes,
)
from src.core.settings_service import SettingsService

REGION_NAMES = ("enhance", "slot", "main_stat", "set", "subs")
ENHANCEMENTS = tuple(f"+{level}" for level in range(16))


class AnalyzerError(RuntimeError):
    pass


class AnalyzerValidationError(AnalyzerError):
    def __init__(self, message: str, issues: Mapping[str, str] | None = None):
        super().__init__(message)
        self.issues = dict(issues or {})


class AnalyzerCaptureError(AnalyzerError):
    pass


class AnalyzerCancelledError(AnalyzerError):
    pass


CaptureRegionsFunction = Callable[
    [Mapping[str, Any], Mapping[str, Mapping[str, Any]], Callable[[], bool]],
    Mapping[str, Any],
]
ProgressFunction = Callable[[str, str, float], None]
ScanFunction = Callable[..., tuple[dict[str, Any], str]]


def _default_capture_regions(
    settings: Mapping[str, Any],
    regions: Mapping[str, Mapping[str, Any]],
    cancel_check: Callable[[], bool],
) -> Mapping[str, Any]:
    from src.vision.automation_backend import AdbAutomationBackend

    backend = AdbAutomationBackend(settings)
    backend.cancel_check = cancel_check
    return backend.capture_regions(regions)


def _default_scan_engine(*args: Any, **kwargs: Any) -> tuple[dict[str, Any], str]:
    from src.core.orchestrator import ScanCancelledError, ScanOrchestrator
    try:
        return ScanOrchestrator.scan(*args, **kwargs)
    except ScanCancelledError as error:
        raise AnalyzerCancelledError(str(error)) from error


def analyzer_options() -> dict[str, Any]:
    return {
        "enhancements": list(ENHANCEMENTS),
        "slots": list(ALL_SLOTS),
        "sets": list(ALL_SETS),
        "stats": list(ALL_STATS),
        "slotMainStats": copy.deepcopy(SLOT_MAIN_STATS),
        "restrictedSubstats": copy.deepcopy(RESTRICTED_SUBSTATS),
        "autoDetectCapabilities": ["tesseract", "ollama", "adb"],
    }


def _clean_mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def validate_piece(piece: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return a normalized camelCase analyzer piece."""
    issues: dict[str, str] = {}
    expected_keys = {"enhancement", "slot", "set", "mainStat", "substats"}
    unknown = sorted(set(piece) - expected_keys)
    if unknown:
        issues["piece"] = f"Unsupported field: {unknown[0]}."

    enhancement = piece.get("enhancement")
    if enhancement not in ENHANCEMENTS:
        issues["enhancement"] = "Enhancement must be +0 through +15."

    slot = piece.get("slot")
    if slot not in ALL_SLOTS:
        issues["slot"] = "Choose a supported equipment slot."

    gear_set = piece.get("set")
    if gear_set not in ALL_SETS:
        issues["set"] = "Choose a supported equipment set."

    main_stat = piece.get("mainStat")
    allowed_mains = SLOT_MAIN_STATS.get(str(slot), [])
    if main_stat not in allowed_mains:
        issues["mainStat"] = "Choose a main stat available for this slot."

    raw_substats = piece.get("substats")
    normalized_substats: list[dict[str, str]] = []
    if not isinstance(raw_substats, Sequence) or isinstance(raw_substats, (str, bytes)):
        issues["substats"] = "Exactly four substats are required."
        raw_substats = []
    elif len(raw_substats) != 4:
        issues["substats"] = "Exactly four substats are required."

    seen: set[str] = set()
    restricted = set(RESTRICTED_SUBSTATS.get(str(slot), []))
    for index, raw_substat in enumerate(raw_substats):
        path = f"substats.{index}"
        substat = _clean_mapping(raw_substat)
        if substat is None:
            issues[path] = "Substat must be an object."
            continue
        unknown_substat = sorted(set(substat) - {"stat", "value"})
        if unknown_substat:
            issues[path] = f"Unsupported field: {unknown_substat[0]}."
        stat = substat.get("stat")
        value = substat.get("value")
        if stat not in ALL_STATS:
            issues[f"{path}.stat"] = "Choose a supported substat."
        elif stat == main_stat:
            issues[f"{path}.stat"] = "A substat cannot match the main stat."
        elif stat in restricted:
            issues[f"{path}.stat"] = "This substat cannot appear on the selected slot."
        elif stat in seen:
            issues[f"{path}.stat"] = "Each substat must be unique."
        else:
            seen.add(str(stat))
        if not isinstance(value, str) or not value.isdigit():
            issues[f"{path}.value"] = "Enter a non-negative whole number."
        elif int(value) > 1_000_000:
            issues[f"{path}.value"] = "Value is too large."
        normalized_substats.append({
            "stat": str(stat or ""),
            "value": str(value if isinstance(value, str) else ""),
        })

    if issues:
        raise AnalyzerValidationError("Analyzer input is invalid.", issues)
    return {
        "enhancement": str(enhancement),
        "slot": str(slot),
        "set": str(gear_set),
        "mainStat": str(main_stat),
        "substats": normalized_substats,
    }


class AnalyzerService:
    def __init__(
        self,
        settings_service: SettingsService | None = None,
        *,
        user_data_dir: str | os.PathLike[str] | None = None,
        capture_regions: CaptureRegionsFunction | None = None,
        scan_engine: ScanFunction | None = None,
        archetype_loader: Callable[[], list[dict[str, Any]]] | None = None,
        scan_timeout_seconds: float = 90,
    ):
        self.settings_service = settings_service or SettingsService()
        selected_user_data = user_data_dir or os.environ.get("E7_USER_DATA_DIR")
        self.user_data_dir = (
            Path(selected_user_data)
            if selected_user_data is not None
            else self.settings_service.path.parent
        )
        self.capture_regions = capture_regions or _default_capture_regions
        self.scan_engine = scan_engine or _default_scan_engine
        self.archetype_loader = archetype_loader or self._load_archetypes
        self.scan_timeout_seconds = scan_timeout_seconds

    def get_options(self) -> dict[str, Any]:
        return analyzer_options()

    def evaluate(self, raw_piece: Mapping[str, Any]) -> dict[str, Any]:
        piece = validate_piece(raw_piece)
        archetypes = self.archetype_loader()
        archetype_text = evaluate_archetypes(
            piece["slot"],
            piece["set"],
            piece["mainStat"],
            [substat["stat"] for substat in piece["substats"]],
            archetypes,
        )
        subs_string = build_gs_string([
            {"stat": substat["stat"], "val": substat["value"]}
            for substat in piece["substats"]
        ])
        if not subs_string.strip():
            gear_score_text = "❌ Error: Could not extract numerical stats."
            gear_score = None
        else:
            gear_score_text = calculate_gear_score(subs_string, piece["enhancement"])
            details = calculate_gear_score_details(subs_string, piece["enhancement"])
            gear_score = {
                "current": details["current_gs"],
                "potential": details["potential_gs"],
                "rolls": details["rolls"],
                "enhancement": details["enhancement"],
                "recommendation": (
                    "final" if details["rolls"] == 5
                    else "keep" if details["potential_gs"] > 57
                    else "stop"
                ),
            }
        return {
            "piece": piece,
            "archetypeText": archetype_text,
            "gearScoreText": gear_score_text,
            "gearScore": gear_score,
        }

    def scan(
        self,
        job_id: str,
        cancel_check: Callable[[], bool],
        on_progress: ProgressFunction,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        settings = self.settings_service.load().document
        regions = settings.get("regions")
        if not isinstance(regions, Mapping):
            raise AnalyzerCaptureError("Capture regions are not configured.")

        selected_regions: dict[str, Mapping[str, Any]] = {}
        for index, region_name in enumerate(REGION_NAMES):
            if cancel_check():
                raise AnalyzerCancelledError("Scan was cancelled.")
            on_progress(
                "capture",
                f"Preparing {region_name.replace('_', ' ')} ADB region…",
                0.04 + ((index + 1) / len(REGION_NAMES)) * 0.12,
            )
            region = regions.get(region_name)
            if not isinstance(region, Mapping):
                raise AnalyzerCaptureError(f"The {region_name} capture region is not configured.")
            selected_regions[region_name] = region

        if cancel_check():
            raise AnalyzerCancelledError("Scan was cancelled.")
        on_progress("capture", "Capturing gear regions from the configured ADB device…", 0.20)
        try:
            captured = self.capture_regions(settings, selected_regions, cancel_check)
        except AnalyzerCancelledError:
            raise
        except Exception as error:
            if cancel_check():
                raise AnalyzerCancelledError("Scan was cancelled.") from error
            raise AnalyzerCaptureError(f"ADB screenshot capture failed: {error}") from error
        if not isinstance(captured, Mapping):
            raise AnalyzerCaptureError("ADB screenshot capture returned an invalid result.")

        images: dict[str, Any] = {}
        for region_name in REGION_NAMES:
            image = captured.get(region_name)
            if image is None:
                raise AnalyzerCaptureError(
                    f"Could not capture the {region_name.replace('_', ' ')} region from the configured ADB device."
                )
            images[region_name] = image

        artifact_dir = self.user_data_dir / "debug_images" / "analyzer" / job_id

        def scan_progress(stage: str, message: str, value: float) -> None:
            on_progress(stage, message, 0.22 + max(0.0, min(1.0, value)) * 0.76)

        parsed, debug_text = self.scan_engine(
            images,
            cancel_check=cancel_check,
            on_progress=scan_progress,
            debug_dir=artifact_dir,
            timeout_seconds=self.scan_timeout_seconds,
        )
        piece = self._piece_from_scan(parsed)
        evaluation = self.evaluate(piece)
        artifacts = sorted(
            path.relative_to(artifact_dir).as_posix()
            for path in artifact_dir.rglob("*")
            if path.is_file()
        ) if artifact_dir.exists() else []
        debug = {
            "available": True,
            "jobId": job_id,
            "text": str(debug_text),
            "artifacts": artifacts,
        }
        return {
            "piece": evaluation["piece"],
            "evaluation": evaluation,
            "debugAvailable": True,
        }, debug

    def _piece_from_scan(self, parsed: Mapping[str, Any]) -> dict[str, Any]:
        slot = parsed.get("slot") if parsed.get("slot") in ALL_SLOTS else "Weapon"
        gear_set = parsed.get("set") if parsed.get("set") in ALL_SETS else "Speed Set"
        enhancement = parsed.get("enhance") if parsed.get("enhance") in ENHANCEMENTS else "+0"
        allowed_mains = SLOT_MAIN_STATS[str(slot)]
        main_stat = parsed.get("main_stat") if parsed.get("main_stat") in allowed_mains else allowed_mains[0]
        restricted = set(RESTRICTED_SUBSTATS.get(str(slot), []))
        valid_stats = [stat for stat in ALL_STATS if stat != main_stat and stat not in restricted]
        substats: list[dict[str, str]] = []
        seen: set[str] = set()
        raw_substats = parsed.get("subs", [])
        if isinstance(raw_substats, Sequence) and not isinstance(raw_substats, (str, bytes)):
            for raw in raw_substats:
                if not isinstance(raw, Mapping):
                    continue
                stat = raw.get("stat")
                if stat not in valid_stats or stat in seen:
                    continue
                match = re.search(r"\d+", str(raw.get("val", raw.get("value", ""))))
                substats.append({"stat": str(stat), "value": match.group(0) if match else "0"})
                seen.add(str(stat))
                if len(substats) == 4:
                    break
        for stat in valid_stats:
            if len(substats) == 4:
                break
            if stat not in seen:
                substats.append({"stat": stat, "value": "0"})
                seen.add(stat)
        return {
            "enhancement": str(enhancement),
            "slot": str(slot),
            "set": str(gear_set),
            "mainStat": str(main_stat),
            "substats": substats,
        }

    def _load_archetypes(self) -> list[dict[str, Any]]:
        path = self.user_data_dir / "archetypes.json"
        if not path.exists():
            return []
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return []
        return [dict(item) for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []
