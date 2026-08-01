"""Narrow controller for desktop optimizer hero configuration."""

from __future__ import annotations

from typing import Any

from src.desktop.optimizer_profile_service import OptimizerProfileService


class OptimizerProfileController:
    def __init__(self, service: OptimizerProfileService) -> None:
        self.service = service

    def search_heroes(self, query: str, limit: int) -> dict[str, Any]:
        return self.service.search_heroes(query, limit)

    def hero_details(self, hero_id: str) -> dict[str, Any]:
        return self.service.get_hero_details(hero_id)

    def search_artifacts(self, query: str, limit: int) -> dict[str, Any]:
        return self.service.search_artifacts(query, limit)

    def load_draft(self, hero_id: str) -> dict[str, Any]:
        return self.service.load_draft(hero_id)

    def save_draft(self, draft: object) -> dict[str, Any]:
        return self.service.save_draft(draft)


__all__ = ["OptimizerProfileController"]
