"""Executable entry point for the Electron-managed Python backend."""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

from src.desktop.health_controller import HealthController
from src.desktop.health_service import HealthService
from src.desktop.cuda_setup import activate_cuda_component, recover_cuda_component_transactions
from src.desktop.analyzer_controller import AnalyzerController
from src.desktop.analyzer_service import AnalyzerService
from src.desktop.enhancement_controller import EnhancementController
from src.desktop.enhancement_service import EnhancementService
from src.desktop.optimizer_inventory_controller import OptimizerInventoryController
from src.desktop.optimizer_inventory_service import OptimizerInventoryService
from src.desktop.optimizer_profile_controller import OptimizerProfileController
from src.desktop.optimizer_profile_service import OptimizerProfileService
from src.desktop.optimizer_search_controller import OptimizerSearchController
from src.desktop.optimizer_search_service import OptimizerSearchService
from src.desktop.optimizer_result_controller import OptimizerResultController
from src.desktop.optimizer_result_service import OptimizerResultService
from src.desktop.protocol import serve, write_protocol_message
from src.core.settings_service import SettingsService
from src.core.packet_api_client import PacketApiClient
from src.core.live_packet_source import LivePacketSource
from src.desktop.settings_controller import SettingsController


def _log(level: str, event: str, message: str, **data: object) -> None:
    record: dict[str, object] = {
        "level": level,
        "event": event,
        "message": message,
    }
    if data:
        record["data"] = data
    print(json.dumps(record, separators=(",", ":")), file=sys.stderr, flush=True)


def main() -> int:
    _log("info", "backend.started", "Python backend started.", pid=os.getpid())
    output_lock = threading.Lock()

    def emit_health(snapshot: dict) -> None:
        write_protocol_message(
            sys.stdout,
            {
                "protocol": 1,
                "event": "health.updated",
                "payload": snapshot,
            },
            output_lock,
        )

    def emit_settings(snapshot: dict) -> None:
        write_protocol_message(
            sys.stdout,
            {
                "protocol": 1,
                "event": "settings.updated",
                "payload": snapshot,
            },
            output_lock,
        )

    def emit_analyzer(snapshot: dict) -> None:
        write_protocol_message(
            sys.stdout,
            {
                "protocol": 1,
                "event": "analyzer.updated",
                "payload": snapshot,
            },
            output_lock,
        )

    def emit_enhancement(snapshot: dict) -> None:
        write_protocol_message(
            sys.stdout,
            {
                "protocol": 1,
                "event": "enhancement.updated",
                "payload": snapshot,
            },
            output_lock,
        )

    def emit_optimizer_search(snapshot: dict) -> None:
        write_protocol_message(
            sys.stdout,
            {
                "protocol": 1,
                "event": "optimizer.search.updated",
                "payload": snapshot,
            },
            output_lock,
        )

    def emit_optimizer_results(snapshot: dict) -> None:
        write_protocol_message(
            sys.stdout,
            {
                "protocol": 1,
                "event": "optimizer.results.updated",
                "payload": snapshot,
            },
            output_lock,
        )

    def emit_optimizer_result_detail(snapshot: dict) -> None:
        write_protocol_message(
            sys.stdout,
            {
                "protocol": 1,
                "event": "optimizer.results.detail-updated",
                "payload": snapshot,
            },
            output_lock,
        )

    def emit_optimizer_result_export(snapshot: dict) -> None:
        write_protocol_message(
            sys.stdout,
            {
                "protocol": 1,
                "event": "optimizer.results.export-updated",
                "payload": snapshot,
            },
            output_lock,
        )

    settings_service = SettingsService()
    packet_api = PacketApiClient()
    user_data_dir = Path(os.environ.get("E7_USER_DATA_DIR") or settings_service.path.parent)
    recover_cuda_component_transactions(user_data_dir)
    activate_cuda_component(user_data_dir)
    health_controller = HealthController(HealthService(user_data_dir=user_data_dir), emit_health)
    settings_controller = SettingsController(settings_service, emit_settings)
    analyzer_controller = AnalyzerController(
        AnalyzerService(settings_service, user_data_dir=user_data_dir),
        emit_analyzer,
    )
    enhancement_controller = EnhancementController(
        EnhancementService(
            settings_service,
            user_data_dir=user_data_dir,
            packet_source_factory=lambda: LivePacketSource(
                enhancement_reader=packet_api.inspect_enhancement,
            ),
            enhancement_normalizer=packet_api.normalize_enhancement,
        ),
        emit_enhancement,
    )
    optimizer_inventory_controller = OptimizerInventoryController(
        OptimizerInventoryService(
            user_data_dir,
            inventory_normalizer=packet_api.normalize_inventory,
        )
    )
    optimizer_profile_service = OptimizerProfileService(user_data_dir)
    optimizer_profile_controller = OptimizerProfileController(optimizer_profile_service)
    optimizer_search_service = OptimizerSearchService(
        user_data_dir,
        profile_service=optimizer_profile_service,
    )
    try:
        removed_search_artifacts = optimizer_search_service.cleanup_stale_results()
        if removed_search_artifacts:
            _log(
                "info",
                "optimizer.search.cleanup",
                "Removed stale optimizer result artifacts.",
                removed=removed_search_artifacts,
            )

    except Exception:
        _log(
            "warning",
            "optimizer.search.cleanup_failed",
            "Stale optimizer result cleanup could not be completed safely.",
        )
    optimizer_search_controller = OptimizerSearchController(
        optimizer_search_service,
        emit_optimizer_search,
    )
    optimizer_result_controller = OptimizerResultController(
        OptimizerResultService(
            user_data_dir,
            optimizer_search_service.result_store,
            optimizer_profile_service.characters,
        ),
        optimizer_search_controller,
        emit_optimizer_results,
        emit_optimizer_result_detail,
        emit_optimizer_result_export,
    )
    try:
        serve(
            sys.stdin,
            sys.stdout,
            health_controller=health_controller,
            settings_controller=settings_controller,
            analyzer_controller=analyzer_controller,
            enhancement_controller=enhancement_controller,
            optimizer_inventory_controller=optimizer_inventory_controller,
            optimizer_profile_controller=optimizer_profile_controller,
            optimizer_search_controller=optimizer_search_controller,
            optimizer_result_controller=optimizer_result_controller,
            output_lock=output_lock,
        )
    except Exception as error:
        _log("error", "backend.fatal", "Python backend stopped after an unhandled error.", error=str(error))
        return 1
    finally:
        analyzer_controller.close()
        enhancement_controller.close()
        optimizer_inventory_controller.close()
        optimizer_search_controller.close()
        optimizer_result_controller.close()
        health_controller.close()
    _log("info", "backend.stopped", "Python backend stopped cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
