"""Narrow desktop settings operations and update events."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from src.core.settings_service import SettingsService
from src.desktop.settings_preview import SettingsPreviewService

EventSink = Callable[[dict[str, Any]], None]


class SettingsController:
    def __init__(
        self,
        service: SettingsService,
        event_sink: EventSink | None = None,
        preview_service: SettingsPreviewService | None = None,
    ):
        self.service = service
        self.event_sink = event_sink or (lambda _snapshot: None)
        self.preview_service = preview_service or SettingsPreviewService()

    def get_snapshot(self) -> dict[str, Any]:
        return self.service.load().to_dict()

    def update(self, revision: str, patch: Mapping[str, Any]) -> dict[str, Any]:
        snapshot = self.service.update(revision, patch).to_dict()
        self.event_sink(snapshot)
        return snapshot

    def preview(
        self,
        settings: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self.preview_service.preview(settings, request)
