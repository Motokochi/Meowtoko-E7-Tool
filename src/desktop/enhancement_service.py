"""Validated desktop enhancement operations with lazy automation dependencies."""

from __future__ import annotations

import copy
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from src.core.settings_service import SettingsService

ENHANCEMENT_MODES = ("adb",)


class EnhancementError(RuntimeError):
    pass


class EnhancementValidationError(EnhancementError):
    def __init__(self, message: str, issues: Mapping[str, str] | None = None):
        super().__init__(message)
        self.issues = dict(issues or {})


class EnhancementCancelledError(EnhancementError):
    pass


def enhancement_options() -> dict[str, Any]:
    return {
        "modes": [
            {
                "id": "adb",
                "label": "Android device or emulator (ADB)",
                "description": "Reads exact game packets and taps through ADB without taking mouse focus.",
                "requiredCapabilities": ["packet", "adb"],
            },
        ],
        "maxRetainedLogs": 200,
    }


def validate_start_options(raw: Mapping[str, Any]) -> dict[str, Any]:
    issues: dict[str, str] = {}
    unknown = sorted(set(raw) - {"mode", "allowDestroy", "maxPieces"})
    if unknown:
        issues["options"] = f"Unsupported field: {unknown[0]}."

    mode = raw.get("mode")
    if mode not in ENHANCEMENT_MODES:
        issues["mode"] = "ADB is the required automation backend."

    allow_destroy = raw.get("allowDestroy")
    if not isinstance(allow_destroy, bool):
        issues["allowDestroy"] = "Destroy permission must be true or false."

    max_pieces = raw.get("maxPieces")
    if max_pieces is not None:
        if isinstance(max_pieces, bool) or not isinstance(max_pieces, int):
            issues["maxPieces"] = "Maximum pieces must be a positive whole number or blank."
        elif max_pieces < 1 or max_pieces > 1_000_000:
            issues["maxPieces"] = "Maximum pieces must be between 1 and 1,000,000."

    if issues:
        raise EnhancementValidationError("Enhancement options are invalid.", issues)
    return {
        "mode": str(mode),
        "allowDestroy": allow_destroy,
        "maxPieces": max_pieces,
    }


class EnhancementService:
    def __init__(
        self,
        settings_service: SettingsService | None = None,
        *,
        user_data_dir: str | os.PathLike[str] | None = None,
        backend_factories: Mapping[str, Callable[[Mapping[str, Any]], Any]] | None = None,
        automator_factory: Callable[..., Any] | None = None,
        packet_source_factory: Callable[[], Any] | None = None,
        item_metadata_resolver: Callable[[str], Mapping[str, Any] | None] | None = None,
        enhancement_normalizer: Callable[..., Mapping[str, Any]] | None = None,
    ):
        self.settings_service = settings_service or SettingsService()
        selected_user_data = user_data_dir or os.environ.get("E7_USER_DATA_DIR")
        self.user_data_dir = Path(selected_user_data) if selected_user_data else self.settings_service.path.parent
        self.backend_factories = dict(backend_factories or {})
        self.automator_factory = automator_factory
        self.packet_source_factory = packet_source_factory
        self.item_metadata_resolver = item_metadata_resolver or self._resolve_item_metadata
        self.enhancement_normalizer = enhancement_normalizer

    def get_options(self) -> dict[str, Any]:
        return enhancement_options()

    def prepare(self, raw_options: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Validate input and snapshot settings before the worker begins."""
        options = validate_start_options(raw_options)
        settings = copy.deepcopy(self.settings_service.load().document)
        return options, settings

    def run(
        self,
        job_id: str,
        options: Mapping[str, Any],
        settings: Mapping[str, Any],
        cancel_check: Callable[[], bool],
        on_progress: Callable[[str, str, float, int, dict[str, Any] | None], None],
        on_log: Callable[[str], None],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if cancel_check():
            raise EnhancementCancelledError("Enhancement automation was cancelled.")

        mode = str(options["mode"])
        backend = self._create_backend(mode, settings)
        packet_source = self._create_packet_source()
        if hasattr(backend, "cancel_check"):
            backend.cancel_check = cancel_check
        artifact_dir = self.user_data_dir / "debug_images" / "enhancement" / job_id
        automator = self._create_automator(
            settings=copy.deepcopy(dict(settings)),
            allow_destroy=bool(options["allowDestroy"]),
            max_pieces=options.get("maxPieces"),
            on_log=on_log,
            on_complete=lambda: None,
            on_error=lambda _message: None,
            backend=backend,
            cancel_check=cancel_check,
            on_progress=on_progress,
            debug_dir=artifact_dir,
            packet_source=packet_source,
            item_metadata_resolver=self.item_metadata_resolver,
            enhancement_normalizer=self.enhancement_normalizer,
        )
        result = automator.run()
        if cancel_check() or result.get("outcome") == "cancelled":
            raise EnhancementCancelledError("Enhancement automation was cancelled.")

        artifacts = sorted(
            path.relative_to(artifact_dir).as_posix()
            for path in artifact_dir.rglob("*")
            if path.is_file()
        ) if artifact_dir.exists() else []
        details: dict[str, Any] | None = None
        packet_path = artifact_dir / "latest_enhancement_packet.json"
        if packet_path.exists():
            try:
                import json

                loaded = json.loads(packet_path.read_text(encoding="utf-8"))
                if isinstance(loaded, Mapping):
                    details = {
                        "backend": loaded.get("backend"),
                        "capturedAt": loaded.get("captured_at"),
                        "parsedData": loaded.get("parsed_data"),
                        "itemId": loaded.get("item_id"),
                        "gearSet": loaded.get("gear_set"),
                        "importedEnhancement": loaded.get("imported_enhancement"),
                        "initialSubstatCount": loaded.get("initial_substat_count"),
                        "enhancementRollStats": loaded.get("enhancement_roll_stats"),
                    }
            except (OSError, UnicodeError, json.JSONDecodeError):
                details = None
        debug = {
            "available": bool(artifacts or getattr(automator, "last_debug_log", None)),
            "jobId": job_id,
            "text": str(getattr(automator, "last_debug_log", "") or ""),
            "artifacts": artifacts,
            **({"details": details} if details is not None else {}),
        }
        return {
            "outcome": result.get("outcome", "completed"),
            "processedPieces": int(result.get("processed_pieces", 0)),
            "currentPiece": int(result.get("current_piece", 0)),
            "lastDecision": result.get("last_decision"),
            "debugAvailable": debug["available"],
        }, debug

    def _create_backend(self, mode: str, settings: Mapping[str, Any]) -> Any:
        factory = self.backend_factories.get(mode)
        if factory is not None:
            return factory(settings)
        from src.vision.automation_backend import AdbAutomationBackend

        return AdbAutomationBackend(settings)

    def _create_packet_source(self) -> Any:
        if self.packet_source_factory is not None:
            return self.packet_source_factory()
        from src.core.live_packet_source import LivePacketSource

        return LivePacketSource()

    def _resolve_item_metadata(self, item_id: str) -> dict[str, Any] | None:
        from src.optimizer.data.inventory_repository import InventoryRepository
        from src.optimizer.domain import GearRank
        from src.optimizer.domain import gear_set_display_name

        initial_substats = {
            GearRank.NORMAL: 0,
            GearRank.GOOD: 1,
            GearRank.RARE: 2,
            GearRank.HEROIC: 3,
            GearRank.EPIC: 4,
        }
        repository = InventoryRepository(self.user_data_dir / "optimizer.db")
        repository.initialize()
        for item in repository.load_inventory():
            if item.current_ingame_id == item_id or any(
                identity.kind.value == "ingame" and identity.value == item_id
                for identity in item.identities
            ):
                return {
                    "set": gear_set_display_name(item.gear_item.gear_set),
                    "setId": item.gear_item.gear_set.value,
                    "slotId": item.gear_item.slot.value,
                    "mainStatId": item.gear_item.main_stat.value,
                    "enhance": item.gear_item.enhance,
                    "initialSubstats": initial_substats[item.rank],
                }
        return None

    def _create_automator(self, **kwargs: Any) -> Any:
        factory = self.automator_factory
        if factory is None:
            from src.core.enhancement_automator import EnhancementAutomator
            factory = EnhancementAutomator
        return factory(**kwargs)
