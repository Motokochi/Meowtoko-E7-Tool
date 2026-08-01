"""Narrow controller for desktop optimizer inventory operations."""

from __future__ import annotations

from typing import Any

from src.desktop.optimizer_inventory_service import OptimizerInventoryService


class OptimizerInventoryController:
    def __init__(self, service: OptimizerInventoryService) -> None:
        self.service = service

    def get_snapshot(self) -> dict[str, Any]:
        return self.service.get_snapshot()

    def import_file(self, source_path: str) -> dict[str, Any]:
        return self.service.import_file(source_path)

    def start_game_inventory_capture(self) -> dict[str, Any]:
        return self.service.start_game_inventory_capture()

    def finish_game_inventory_capture(self) -> dict[str, Any]:
        return self.service.finish_game_inventory_capture()

    def reset_all_optimizer_data(self) -> dict[str, Any]:
        return self.service.reset_all_optimizer_data()

    def close(self) -> None:
        self.service.close()


__all__ = ["OptimizerInventoryController"]
