"""Versioned settings with lossless migrations, validation, and atomic persistence."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core.workspace_paths import resolve_user_data_directory

SETTINGS_SCHEMA_VERSION = 1
THEMES = frozenset({"system", "light", "dark"})

REGION_NAMES = ("enhance", "slot", "main_stat", "set", "subs")
POINT_NAMES = (
    "lock",
    "back",
    "next_piece",
    "open_enhance",
    "destroy",
    "destroy_confirm",
    "enhance",
    "auto_select",
    "probe_ingredient",
    "probe_select",
)
LEVEL_NAMES = ("+3", "+6", "+9", "+12", "+15")

AUTOMATION_DEFAULTS: dict[str, int | float] = {
    "after_auto_select_seconds": 0.6,
    "after_level_select_seconds": 0.4,
    "after_enhance_seconds": 2.0,
    "after_destroy_seconds": 0.6,
    "after_destroy_confirm_seconds": 1.0,
    "after_lock_seconds": 0.4,
    "after_back_seconds": 0.8,
    "after_next_piece_seconds": 0.6,
    "after_open_enhance_seconds": 0.8,
    "after_reward_popup_seconds": 0.6,
    "enhancement_packet_timeout_seconds": 2.0,
    "after_enhancement_retry_seconds": 0.8,
    "enhancement_read_retries": 2,
}

REGION_PROTOCOL_TO_FILE = {
    "enhance": "enhance",
    "slot": "slot",
    "mainStat": "main_stat",
    "set": "set",
    "subs": "subs",
}
POINT_PROTOCOL_TO_FILE = {
    "lock": "lock",
    "back": "back",
    "nextPiece": "next_piece",
    "openEnhance": "open_enhance",
    "destroy": "destroy",
    "destroyConfirm": "destroy_confirm",
    "enhance": "enhance",
    "autoSelect": "auto_select",
    "probeIngredient": "probe_ingredient",
    "probeSelect": "probe_select",
}
AUTOMATION_PROTOCOL_TO_FILE = {
    "afterAutoSelectSeconds": "after_auto_select_seconds",
    "afterLevelSelectSeconds": "after_level_select_seconds",
    "afterEnhanceSeconds": "after_enhance_seconds",
    "afterDestroySeconds": "after_destroy_seconds",
    "afterDestroyConfirmSeconds": "after_destroy_confirm_seconds",
    "afterLockSeconds": "after_lock_seconds",
    "afterBackSeconds": "after_back_seconds",
    "afterNextPieceSeconds": "after_next_piece_seconds",
    "afterOpenEnhanceSeconds": "after_open_enhance_seconds",
    "afterRewardPopupSeconds": "after_reward_popup_seconds",
    "enhancementPacketTimeoutSeconds": "enhancement_packet_timeout_seconds",
    "afterEnhancementRetrySeconds": "after_enhancement_retry_seconds",
    "enhancementReadRetries": "enhancement_read_retries",
}
ADB_PROTOCOL_TO_FILE = {
    "adbPath": "adb_path",
    "deviceSerial": "device_serial",
    "coordinateWidth": "coordinate_width",
    "coordinateHeight": "coordinate_height",
    "commandTimeoutSeconds": "command_timeout_seconds",
}


def settings_path(environment: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    explicit = env.get("E7_SETTINGS_PATH")
    if explicit:
        return Path(explicit)
    return resolve_user_data_directory(env) / "settings.json"


def default_settings() -> dict[str, Any]:
    return {
        "schema_version": SETTINGS_SCHEMA_VERSION,
        "target_window": "Epic Seven",
        "appearance": {"theme": "system"},
        "regions": {
            "enhance": {"x": 105, "y": 110, "width": 35, "height": 30},
            "slot": {"x": 140, "y": 135, "width": 250, "height": 30},
            "main_stat": {"x": 70, "y": 235, "width": 300, "height": 40},
            "set": {"x": 80, "y": 460, "width": 250, "height": 35},
            "subs": {"x": 40, "y": 300, "width": 330, "height": 100},
        },
        "click_points": {
            "lock": {"x": 203, "y": 680},
            "back": {"x": 35, "y": 45},
            "next_piece": {"x": 200, "y": 220},
            "open_enhance": {"x": 1150, "y": 700},
            "destroy": {"x": 346, "y": 680},
            "destroy_confirm": {"x": 760, "y": 550},
            "enhance": {"x": 695, "y": 680},
            "auto_select": {"x": 1060, "y": 680},
            "probe_ingredient": {"x": 1060, "y": 170},
            "probe_select": {"x": 640, "y": 490},
            "levels": {
                "+3": {"x": 1060, "y": 600},
                "+6": {"x": 1060, "y": 550},
                "+9": {"x": 1060, "y": 490},
                "+12": {"x": 1060, "y": 430},
                "+15": {"x": 1060, "y": 370},
            },
        },
        "automation": copy.deepcopy(AUTOMATION_DEFAULTS),
        "adb": {
            "adb_path": "adb",
            "device_serial": "",
            "coordinate_width": 1280,
            "coordinate_height": 720,
            "command_timeout_seconds": 10.0,
        },
    }


class SettingsError(RuntimeError):
    pass


class SettingsValidationError(SettingsError):
    def __init__(self, message: str, issues: Mapping[str, str] | None = None):
        super().__init__(message)
        self.issues = dict(issues or {})


class SettingsConflictError(SettingsError):
    pass


class SettingsReadOnlyError(SettingsError):
    pass


class SettingsWriteError(SettingsError):
    pass


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(dict(base))
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_integer(
    issues: dict[str, str],
    path: str,
    value: Any,
    *,
    minimum: int,
    maximum: int,
) -> None:
    if not _is_number(value) or int(value) != value:
        issues[path] = "Must be a whole number."
    elif not minimum <= int(value) <= maximum:
        issues[path] = f"Must be between {minimum} and {maximum}."


def validate_document(document: Mapping[str, Any]) -> None:
    issues: dict[str, str] = {}
    target = document.get("target_window")
    if not isinstance(target, str) or not target.strip():
        issues["targetWindow"] = "Target window is required."
    elif len(target) > 200:
        issues["targetWindow"] = "Target window must be 200 characters or fewer."

    appearance = document.get("appearance")
    if not isinstance(appearance, Mapping) or appearance.get("theme") not in THEMES:
        issues["appearance.theme"] = "Theme must be system, light, or dark."

    regions = document.get("regions")
    if not isinstance(regions, Mapping):
        issues["regions"] = "Regions must be an object."
    else:
        for region_name in REGION_NAMES:
            region = regions.get(region_name)
            prefix = f"regions.{region_name}"
            if not isinstance(region, Mapping):
                issues[prefix] = "Region is required."
                continue
            _validate_integer(issues, f"{prefix}.x", region.get("x"), minimum=0, maximum=100_000)
            _validate_integer(issues, f"{prefix}.y", region.get("y"), minimum=0, maximum=100_000)
            _validate_integer(issues, f"{prefix}.width", region.get("width"), minimum=1, maximum=100_000)
            _validate_integer(issues, f"{prefix}.height", region.get("height"), minimum=1, maximum=100_000)

    click_points = document.get("click_points")
    if not isinstance(click_points, Mapping):
        issues["clickPoints"] = "Click points must be an object."
    else:
        for point_name in POINT_NAMES:
            point = click_points.get(point_name)
            prefix = f"clickPoints.{point_name}"
            if not isinstance(point, Mapping):
                issues[prefix] = "Click point is required."
                continue
            _validate_integer(issues, f"{prefix}.x", point.get("x"), minimum=0, maximum=100_000)
            _validate_integer(issues, f"{prefix}.y", point.get("y"), minimum=0, maximum=100_000)
        levels = click_points.get("levels")
        if not isinstance(levels, Mapping):
            issues["clickPoints.levels"] = "Enhancement level points are required."
        else:
            for level_name in LEVEL_NAMES:
                point = levels.get(level_name)
                prefix = f"clickPoints.levels.{level_name}"
                if not isinstance(point, Mapping):
                    issues[prefix] = "Enhancement level point is required."
                    continue
                _validate_integer(issues, f"{prefix}.x", point.get("x"), minimum=0, maximum=100_000)
                _validate_integer(issues, f"{prefix}.y", point.get("y"), minimum=0, maximum=100_000)

    automation = document.get("automation")
    if not isinstance(automation, Mapping):
        issues["automation"] = "Automation settings must be an object."
    else:
        for key in AUTOMATION_DEFAULTS:
            value = automation.get(key)
            path = f"automation.{key}"
            if key == "enhancement_read_retries":
                _validate_integer(issues, path, value, minimum=0, maximum=20)
            elif key == "after_enhance_seconds":
                if not _is_number(value) or not 2 <= float(value) <= 300:
                    issues[path] = "Must be a number between 2 and 300 seconds."
            elif not _is_number(value) or not 0 <= float(value) <= 300:
                issues[path] = "Must be a number between 0 and 300 seconds."

    adb = document.get("adb")
    if not isinstance(adb, Mapping):
        issues["adb"] = "ADB settings must be an object."
    else:
        if not isinstance(adb.get("adb_path"), str) or not str(adb.get("adb_path")).strip():
            issues["adb.adbPath"] = "ADB path is required."
        if not isinstance(adb.get("device_serial"), str):
            issues["adb.deviceSerial"] = "Device serial must be text."
        _validate_integer(issues, "adb.coordinateWidth", adb.get("coordinate_width"), minimum=1, maximum=100_000)
        _validate_integer(issues, "adb.coordinateHeight", adb.get("coordinate_height"), minimum=1, maximum=100_000)
        timeout = adb.get("command_timeout_seconds")
        if not _is_number(timeout) or not 0.1 <= float(timeout) <= 300:
            issues["adb.commandTimeoutSeconds"] = "Must be between 0.1 and 300 seconds."

    if issues:
        raise SettingsValidationError("Settings validation failed.", issues)


def migrate_document(raw: Mapping[str, Any]) -> tuple[dict[str, Any], int | None, bool]:
    document = copy.deepcopy(dict(raw))
    raw_version = document.get("schema_version", 0)
    if isinstance(raw_version, bool) or not isinstance(raw_version, int) or raw_version < 0:
        raise SettingsValidationError("Settings schema_version must be a non-negative integer.")
    if raw_version > SETTINGS_SCHEMA_VERSION:
        normalized = _deep_merge(default_settings(), document)
        validate_document(normalized)
        return normalized, None, True
    migrated_from = raw_version if raw_version < SETTINGS_SCHEMA_VERSION else None
    version = raw_version
    while version < SETTINGS_SCHEMA_VERSION:
        if version == 0:
            document.setdefault("appearance", {"theme": "system"})
            document["schema_version"] = 1
            version = 1
            continue
        raise SettingsValidationError(f"No settings migration is available from schema {version}.")
    normalized = _deep_merge(default_settings(), document)
    normalized["schema_version"] = SETTINGS_SCHEMA_VERSION
    validate_document(normalized)
    return normalized, migrated_from, False


def _invert(mapping: Mapping[str, str]) -> dict[str, str]:
    return {file_name: protocol_name for protocol_name, file_name in mapping.items()}


def document_to_protocol(document: Mapping[str, Any]) -> dict[str, Any]:
    region_file_to_protocol = _invert(REGION_PROTOCOL_TO_FILE)
    point_file_to_protocol = _invert(POINT_PROTOCOL_TO_FILE)
    automation_file_to_protocol = _invert(AUTOMATION_PROTOCOL_TO_FILE)
    adb_file_to_protocol = _invert(ADB_PROTOCOL_TO_FILE)
    click_points = document["click_points"]
    return {
        "targetWindow": document["target_window"],
        "appearance": {"theme": document["appearance"]["theme"]},
        "regions": {
            region_file_to_protocol[name]: copy.deepcopy(document["regions"][name])
            for name in REGION_NAMES
        },
        "clickPoints": {
            **{
                point_file_to_protocol[name]: copy.deepcopy(click_points[name])
                for name in POINT_NAMES
            },
            "levels": {name: copy.deepcopy(click_points["levels"][name]) for name in LEVEL_NAMES},
        },
        "automation": {
            automation_file_to_protocol[name]: document["automation"][name]
            for name in AUTOMATION_DEFAULTS
        },
        "adb": {
            adb_file_to_protocol[name]: document["adb"][name]
            for name in ADB_PROTOCOL_TO_FILE.values()
        },
    }


def _reject_unknown(value: Mapping[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise SettingsValidationError(
            f"Unsupported settings field: {path}{unknown[0]}",
            {f"{path}{key}": "Unsupported field." for key in unknown},
        )


def _translate_flat(value: Any, mapping: Mapping[str, str], path: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SettingsValidationError(f"{path.rstrip('.')} must be an object.")
    _reject_unknown(value, set(mapping), path)
    return {mapping[key]: copy.deepcopy(item) for key, item in value.items()}


def protocol_patch_to_document(patch: Mapping[str, Any]) -> dict[str, Any]:
    _reject_unknown(
        patch,
        {"targetWindow", "appearance", "regions", "clickPoints", "automation", "adb"},
        "",
    )
    result: dict[str, Any] = {}
    if "targetWindow" in patch:
        result["target_window"] = copy.deepcopy(patch["targetWindow"])
    if "appearance" in patch:
        result["appearance"] = _translate_flat(patch["appearance"], {"theme": "theme"}, "appearance.")
    if "regions" in patch:
        regions = patch["regions"]
        if not isinstance(regions, Mapping):
            raise SettingsValidationError("regions must be an object.")
        _reject_unknown(regions, set(REGION_PROTOCOL_TO_FILE), "regions.")
        result["regions"] = {}
        for name, region in regions.items():
            result["regions"][REGION_PROTOCOL_TO_FILE[name]] = _translate_flat(
                region,
                {"x": "x", "y": "y", "width": "width", "height": "height"},
                f"regions.{name}.",
            )
    if "clickPoints" in patch:
        points = patch["clickPoints"]
        if not isinstance(points, Mapping):
            raise SettingsValidationError("clickPoints must be an object.")
        _reject_unknown(points, set(POINT_PROTOCOL_TO_FILE) | {"levels"}, "clickPoints.")
        result["click_points"] = {}
        for name, point in points.items():
            if name == "levels":
                if not isinstance(point, Mapping):
                    raise SettingsValidationError("clickPoints.levels must be an object.")
                _reject_unknown(point, set(LEVEL_NAMES), "clickPoints.levels.")
                result["click_points"]["levels"] = {
                    level: _translate_flat(coords, {"x": "x", "y": "y"}, f"clickPoints.levels.{level}.")
                    for level, coords in point.items()
                }
            else:
                result["click_points"][POINT_PROTOCOL_TO_FILE[name]] = _translate_flat(
                    point, {"x": "x", "y": "y"}, f"clickPoints.{name}."
                )
    if "automation" in patch:
        result["automation"] = _translate_flat(
            patch["automation"], AUTOMATION_PROTOCOL_TO_FILE, "automation."
        )
    if "adb" in patch:
        result["adb"] = _translate_flat(patch["adb"], ADB_PROTOCOL_TO_FILE, "adb.")
    return result


def protocol_settings_to_document(settings: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a complete renderer settings draft without persisting it."""

    translated = protocol_patch_to_document(settings)
    document = _deep_merge(default_settings(), translated)
    document["schema_version"] = SETTINGS_SCHEMA_VERSION
    validate_document(document)
    return document


class SettingsStorage:
    def __init__(self, replace_file: Callable[[Path, Path], None] | None = None):
        self.replace_file = replace_file or (lambda source, target: os.replace(source, target))

    @staticmethod
    def revision(path: Path) -> str:
        if not path.exists():
            return "missing"
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _atomic_bytes(self, target: Path, payload: bytes) -> None:
        temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            self.replace_file(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def write_document(self, path: Path, document: Mapping[str, Any], *, backup_existing: bool) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            existing = path.read_bytes() if path.exists() else None
            if existing is not None:
                if backup_existing:
                    self._atomic_bytes(Path(f"{path}.bak"), existing)
                else:
                    corrupt_path = Path(f"{path}.corrupt")
                    if not corrupt_path.exists():
                        self._atomic_bytes(corrupt_path, existing)
            payload = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            self._atomic_bytes(path, payload)
        except OSError as error:
            raise SettingsWriteError(f"Settings could not be saved: {error}") from error


@dataclass(frozen=True)
class SettingsSnapshot:
    document: dict[str, Any]
    revision: str
    source: str
    schema_version: int
    migrated_from: int | None = None
    read_only: bool = False
    warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schemaVersion": self.schema_version,
            "revision": self.revision,
            "source": self.source,
            "readOnly": self.read_only,
            "settings": document_to_protocol(self.document),
        }
        if self.migrated_from is not None:
            result["migratedFrom"] = self.migrated_from
        if self.warning:
            result["warning"] = self.warning
        return result


class SettingsService:
    def __init__(self, path: str | Path | None = None, storage: SettingsStorage | None = None):
        self.path = settings_path() if path is None else Path(path)
        self.storage = storage or SettingsStorage()

    @staticmethod
    def _decode(path: Path) -> Mapping[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise SettingsValidationError("Settings file must contain a JSON object.")
        return value

    def load(self) -> SettingsSnapshot:
        revision = self.storage.revision(self.path)
        primary_error: Exception | None = None
        if self.path.exists():
            try:
                document, migrated_from, read_only = migrate_document(self._decode(self.path))
                return SettingsSnapshot(
                    document=document,
                    revision=revision,
                    source="file",
                    schema_version=int(document["schema_version"]),
                    migrated_from=migrated_from,
                    read_only=read_only,
                    warning=(
                        f"Settings were created by schema {document['schema_version']}; this version will not overwrite them."
                        if read_only
                        else "Settings will be upgraded safely when they are next saved."
                        if migrated_from is not None
                        else None
                    ),
                )
            except (OSError, UnicodeError, json.JSONDecodeError, SettingsValidationError) as error:
                primary_error = error

        backup_path = Path(f"{self.path}.bak")
        if primary_error is not None and backup_path.exists():
            try:
                document, migrated_from, read_only = migrate_document(self._decode(backup_path))
                return SettingsSnapshot(
                    document=document,
                    revision=revision,
                    source="backup",
                    schema_version=int(document["schema_version"]),
                    migrated_from=migrated_from,
                    read_only=read_only,
                    warning="The primary settings file is invalid. A validated backup was loaded.",
                )
            except (OSError, UnicodeError, json.JSONDecodeError, SettingsValidationError):
                pass

        document = default_settings()
        warning = None
        if primary_error is not None:
            warning = "The settings file is invalid. Safe defaults are loaded; the original will be preserved before saving."
        return SettingsSnapshot(
            document=document,
            revision=revision,
            source="defaults",
            schema_version=SETTINGS_SCHEMA_VERSION,
            warning=warning,
        )

    def update(self, expected_revision: str, protocol_patch: Mapping[str, Any]) -> SettingsSnapshot:
        current = self.load()
        if expected_revision != current.revision:
            raise SettingsConflictError("Settings changed outside this window. Reload them and try again.")
        if current.read_only:
            raise SettingsReadOnlyError("These settings were created by a newer application version and are read-only.")
        translated = protocol_patch_to_document(protocol_patch)
        candidate = _deep_merge(current.document, translated)
        candidate["schema_version"] = SETTINGS_SCHEMA_VERSION
        validate_document(candidate)
        self.storage.write_document(self.path, candidate, backup_existing=current.source == "file")
        return self.load()

    def replace_legacy(self, document: Mapping[str, Any]) -> SettingsSnapshot:
        current = self.load()
        if current.read_only:
            raise SettingsReadOnlyError("Settings from a newer application version cannot be overwritten.")
        candidate = _deep_merge(current.document, document)
        candidate, _migrated_from, read_only = migrate_document(candidate)
        if read_only:
            raise SettingsReadOnlyError("Settings from a newer application version cannot be overwritten.")
        self.storage.write_document(self.path, candidate, backup_existing=current.source == "file")
        return self.load()
