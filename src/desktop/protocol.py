"""Versioned newline-delimited JSON protocol for the desktop backend."""

from __future__ import annotations

import gc
import json
import os
import platform
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TextIO

from src.desktop import BACKEND_VERSION, PROTOCOL_VERSION
from src.core.settings_service import (
    SettingsConflictError,
    SettingsReadOnlyError,
    SettingsValidationError,
    SettingsWriteError,
)
from src.desktop.analyzer_controller import AnalyzerBusyError, AnalyzerJobNotFoundError
from src.desktop.analyzer_service import AnalyzerValidationError
from src.desktop.enhancement_controller import EnhancementBusyError, EnhancementJobNotFoundError
from src.desktop.enhancement_service import EnhancementValidationError
from src.desktop.optimizer_inventory_service import OptimizerInventoryServiceError
from src.desktop.optimizer_profile_service import OptimizerProfileServiceError
from src.desktop.optimizer_search_controller import (
    OptimizerSearchBusyError,
    OptimizerSearchJobNotFoundError,
)
from src.desktop.optimizer_result_controller import (
    OptimizerResultDetailUnavailableError,
    OptimizerResultEquipUnavailableError,
    OptimizerResultExportUnavailableError,
    OptimizerResultQueryNotFoundError,
    OptimizerResultSessionError,
)
from src.desktop.settings_preview import SettingsPreviewError

if TYPE_CHECKING:
    from src.desktop.health_controller import HealthController
    from src.desktop.settings_controller import SettingsController
    from src.desktop.analyzer_controller import AnalyzerController
    from src.desktop.enhancement_controller import EnhancementController
    from src.desktop.optimizer_inventory_controller import OptimizerInventoryController
    from src.desktop.optimizer_profile_controller import OptimizerProfileController
    from src.desktop.optimizer_search_controller import OptimizerSearchController


def _failure(
    request_id: str | None,
    code: str,
    message: str,
    *,
    data: Any = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {
        "protocol": PROTOCOL_VERSION,
        "id": request_id,
        "ok": False,
        "error": error,
    }


def _success(request_id: str, result: Any) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL_VERSION,
        "id": request_id,
        "ok": True,
        "result": result,
    }


def dispatch_message(
    message: Any,
    health_controller: "HealthController | None" = None,
    settings_controller: "SettingsController | None" = None,
    analyzer_controller: "AnalyzerController | None" = None,
    enhancement_controller: "EnhancementController | None" = None,
    optimizer_inventory_controller: "OptimizerInventoryController | None" = None,
    optimizer_profile_controller: "OptimizerProfileController | None" = None,
    optimizer_search_controller: "OptimizerSearchController | None" = None,
    optimizer_result_controller: Any = None,
) -> dict[str, Any]:
    """Validate and dispatch one already-decoded protocol message."""

    if not isinstance(message, Mapping):
        return _failure(None, "invalid_request", "Request must be a JSON object.")

    raw_id = message.get("id")
    request_id = raw_id if isinstance(raw_id, str) and raw_id else None
    if request_id is None:
        return _failure(None, "invalid_request", "Request id must be a non-empty string.")

    protocol = message.get("protocol")
    if protocol != PROTOCOL_VERSION:
        return _failure(
            request_id,
            "incompatible_protocol",
            f"Expected protocol {PROTOCOL_VERSION}, received {protocol!r}.",
            data={"expected": PROTOCOL_VERSION, "received": protocol},
        )

    method = message.get("method")
    if not isinstance(method, str) or not method:
        return _failure(request_id, "invalid_request", "Method must be a non-empty string.")

    params = message.get("params", {})
    if not isinstance(params, Mapping):
        return _failure(request_id, "invalid_params", "Params must be a JSON object.")

    if method == "system.ping":
        return _success(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "backendVersion": BACKEND_VERSION,
                "pythonVersion": platform.python_version(),
                "pid": os.getpid(),
            },
        )

    if method == "system.shutdown":
        return _success(request_id, {"accepted": True})

    if method.startswith("health.") and health_controller is None:
        return _failure(request_id, "service_unavailable", "Health service is not available.")

    if method == "health.get":
        return _success(request_id, health_controller.get_snapshot())

    if method == "health.refresh":
        return _success(request_id, health_controller.refresh())

    if method == "health.action":
        action_id = params.get("actionId")
        if not isinstance(action_id, str) or not action_id:
            return _failure(request_id, "invalid_params", "Health actionId must be a non-empty string.")
        try:
            result = health_controller.run_action(action_id)
        except ValueError as error:
            return _failure(request_id, "invalid_params", str(error))
        return _success(request_id, result)

    if method.startswith("settings.") and settings_controller is None:
        return _failure(request_id, "service_unavailable", "Settings service is not available.")

    if method == "settings.get":
        return _success(request_id, settings_controller.get_snapshot())

    if method == "settings.preview":
        settings = params.get("settings")
        preview_request = params.get("request")
        if set(params) != {"settings", "request"} or not isinstance(settings, Mapping) or not isinstance(preview_request, Mapping):
            return _failure(
                request_id,
                "invalid_params",
                "Settings preview requires only settings and request objects.",
            )
        try:
            result = settings_controller.preview(settings, preview_request)
        except SettingsValidationError as error:
            return _failure(request_id, "settings_validation", str(error), data={"issues": error.issues})
        except SettingsPreviewError as error:
            return _failure(request_id, "settings_preview_failed", str(error))
        return _success(request_id, result)

    if method == "settings.update":
        revision = params.get("revision")
        patch = params.get("patch")
        if not isinstance(revision, str) or not revision:
            return _failure(request_id, "invalid_params", "Settings revision must be a non-empty string.")
        if not isinstance(patch, Mapping):
            return _failure(request_id, "invalid_params", "Settings patch must be a JSON object.")
        try:
            result = settings_controller.update(revision, patch)
        except SettingsValidationError as error:
            return _failure(request_id, "settings_validation", str(error), data={"issues": error.issues})
        except SettingsConflictError as error:
            return _failure(request_id, "settings_conflict", str(error))
        except SettingsReadOnlyError as error:
            return _failure(request_id, "settings_read_only", str(error))
        except SettingsWriteError as error:
            return _failure(request_id, "settings_write_failed", str(error))
        return _success(request_id, result)

    if method.startswith("analyzer.") and analyzer_controller is None:
        return _failure(request_id, "service_unavailable", "Analyzer service is not available.")

    if method == "analyzer.options":
        if params:
            return _failure(request_id, "invalid_params", "Analyzer options does not accept parameters.")
        return _success(request_id, analyzer_controller.get_options())

    if method == "analyzer.evaluate":
        piece = params.get("piece")
        if not isinstance(piece, Mapping):
            return _failure(request_id, "invalid_params", "Analyzer piece must be a JSON object.")
        try:
            result = analyzer_controller.evaluate(piece)
        except AnalyzerValidationError as error:
            return _failure(request_id, "analyzer_validation", str(error), data={"issues": error.issues})
        return _success(request_id, result)

    if method == "analyzer.scan.get":
        if params:
            return _failure(request_id, "invalid_params", "Analyzer scan status does not accept parameters.")
        return _success(request_id, analyzer_controller.get_snapshot())

    if method == "analyzer.scan.start":
        if params:
            return _failure(request_id, "invalid_params", "Analyzer scan start does not accept parameters.")
        try:
            result = analyzer_controller.start_scan()
        except AnalyzerBusyError as error:
            return _failure(request_id, "analyzer_busy", str(error))
        return _success(request_id, result)

    if method == "analyzer.scan.cancel":
        job_id = params.get("jobId")
        if not isinstance(job_id, str) or not job_id:
            return _failure(request_id, "invalid_params", "Analyzer jobId must be a non-empty string.")
        try:
            result = analyzer_controller.cancel_scan(job_id)
        except AnalyzerJobNotFoundError as error:
            return _failure(request_id, "analyzer_job_not_found", str(error))
        return _success(request_id, result)

    if method == "analyzer.debug.get":
        if params:
            return _failure(request_id, "invalid_params", "Analyzer debug does not accept parameters.")
        return _success(request_id, analyzer_controller.get_debug())

    if method.startswith("enhancement.") and enhancement_controller is None:
        return _failure(request_id, "service_unavailable", "Enhancement service is not available.")

    if method == "enhancement.options":
        if params:
            return _failure(request_id, "invalid_params", "Enhancement options does not accept parameters.")
        return _success(request_id, enhancement_controller.get_options())

    if method == "enhancement.job.get":
        if params:
            return _failure(request_id, "invalid_params", "Enhancement status does not accept parameters.")
        return _success(request_id, enhancement_controller.get_snapshot())

    if method == "enhancement.job.start":
        if set(params) != {"options"} or not isinstance(params.get("options"), Mapping):
            return _failure(request_id, "invalid_params", "Enhancement start requires only an options object.")
        try:
            result = enhancement_controller.start(params["options"])
        except EnhancementValidationError as error:
            return _failure(request_id, "enhancement_validation", str(error), data={"issues": error.issues})
        except EnhancementBusyError as error:
            return _failure(request_id, "enhancement_busy", str(error))
        return _success(request_id, result)

    if method == "enhancement.job.cancel":
        if set(params) != {"jobId"}:
            return _failure(request_id, "invalid_params", "Enhancement cancel requires only jobId.")
        job_id = params.get("jobId")
        if not isinstance(job_id, str) or not job_id:
            return _failure(request_id, "invalid_params", "Enhancement jobId must be a non-empty string.")
        try:
            result = enhancement_controller.cancel(job_id)
        except EnhancementJobNotFoundError as error:
            return _failure(request_id, "enhancement_job_not_found", str(error))
        return _success(request_id, result)

    if method == "enhancement.debug.get":
        if params:
            return _failure(request_id, "invalid_params", "Enhancement debug does not accept parameters.")
        return _success(request_id, enhancement_controller.get_debug())

    if method.startswith("optimizer.inventory.") and optimizer_inventory_controller is None:
        return _failure(request_id, "service_unavailable", "Optimizer inventory service is not available.")

    if method == "optimizer.inventory.get":
        if params:
            return _failure(request_id, "invalid_params", "Optimizer inventory status does not accept parameters.")
        try:
            result = optimizer_inventory_controller.get_snapshot()
        except OptimizerInventoryServiceError as error:
            data = {"category": error.category, "issueCode": error.code}
            if error.document_path is not None:
                data["documentPath"] = error.document_path
            return _failure(
                request_id,
                "optimizer_inventory_status_failed",
                str(error),
                data=data,
            )
        return _success(request_id, result)

    if method == "optimizer.inventory.import":
        source_path = params.get("sourcePath")
        if set(params) != {"sourcePath"} or not isinstance(source_path, str) or not source_path.strip():
            return _failure(
                request_id,
                "invalid_params",
                "Optimizer inventory import requires only a selected source path.",
            )
        try:
            result = optimizer_inventory_controller.import_file(source_path)
        except OptimizerInventoryServiceError as error:
            data = {"category": error.category, "issueCode": error.code}
            if error.document_path is not None:
                data["documentPath"] = error.document_path
            return _failure(
                request_id,
                "optimizer_inventory_import_failed",
                str(error),
                data=data,
            )
        return _success(request_id, result)

    if method == "optimizer.inventory.capture.start":
        if params:
            return _failure(
                request_id,
                "invalid_params",
                "Starting Optimizer game capture does not accept parameters.",
            )
        try:
            result = optimizer_inventory_controller.start_game_inventory_capture()
        except OptimizerInventoryServiceError as error:
            data = {"category": error.category, "issueCode": error.code}
            return _failure(
                request_id,
                "optimizer_inventory_capture_failed",
                str(error),
                data=data,
            )
        return _success(request_id, result)

    if method == "optimizer.inventory.capture.finish":
        if params:
            return _failure(
                request_id,
                "invalid_params",
                "Finishing Optimizer game capture does not accept parameters.",
            )
        try:
            result = optimizer_inventory_controller.finish_game_inventory_capture()
        except OptimizerInventoryServiceError as error:
            data = {"category": error.category, "issueCode": error.code}
            if error.document_path is not None:
                data["documentPath"] = error.document_path
            return _failure(
                request_id,
                "optimizer_inventory_capture_failed",
                str(error),
                data=data,
            )
        return _success(request_id, result)

    if method == "optimizer.inventory.reset":
        if params:
            return _failure(
                request_id,
                "invalid_params",
                "Optimizer data reset does not accept parameters.",
            )
        if optimizer_search_controller is None or optimizer_result_controller is None:
            return _failure(
                request_id,
                "service_unavailable",
                "Optimizer reset coordination is not available.",
            )
        try:
            optimizer_search_controller.reset_for_data_erasure()
            optimizer_result_controller.reset_for_data_erasure()
            gc.collect()
            result = optimizer_inventory_controller.reset_all_optimizer_data()
        except (OptimizerSearchBusyError, OptimizerResultSessionError) as error:
            return _failure(request_id, "optimizer_data_reset_busy", str(error))
        except OptimizerInventoryServiceError as error:
            return _failure(
                request_id,
                "optimizer_data_reset_failed",
                str(error),
                data={"category": error.category, "issueCode": error.code},
            )
        return _success(request_id, result)

    if method.startswith("optimizer.search.") and optimizer_search_controller is None:
        return _failure(request_id, "service_unavailable", "Optimizer search service is not available.")

    if method == "optimizer.search.get":
        if params:
            return _failure(request_id, "invalid_params", "Optimizer search status does not accept parameters.")
        return _success(request_id, optimizer_search_controller.get_snapshot())

    if method == "optimizer.search.start":
        draft = params.get("draft")
        if set(params) != {"draft"} or not isinstance(draft, Mapping):
            return _failure(request_id, "invalid_params", "Optimizer search start requires only a draft object.")
        try:
            result = optimizer_search_controller.start(draft)
        except OptimizerSearchBusyError as error:
            return _failure(request_id, "optimizer_search_busy", str(error))
        return _success(request_id, result)

    if method.startswith("optimizer.results.") and optimizer_result_controller is None:
        return _failure(request_id, "service_unavailable", "Optimizer result service is not available.")

    if method == "optimizer.results.options":
        if params:
            return _failure(request_id, "invalid_params", "Optimizer result options do not accept parameters.")
        return _success(request_id, optimizer_result_controller.get_options())

    if method == "optimizer.results.get":
        if params:
            return _failure(request_id, "invalid_params", "Optimizer result status does not accept parameters.")
        return _success(request_id, optimizer_result_controller.get_snapshot())

    if method == "optimizer.results.query":
        query = params.get("query")
        if set(params) != {"query"} or not isinstance(query, Mapping):
            return _failure(request_id, "invalid_params", "Optimizer result query requires only a query object.")
        try:
            return _success(request_id, optimizer_result_controller.query(query))
        except OptimizerResultSessionError as error:
            return _failure(request_id, "optimizer_result_session_unavailable", str(error))

    if method == "optimizer.results.cancel":
        query_id = params.get("queryId")
        if set(params) != {"queryId"} or not isinstance(query_id, str) or not query_id:
            return _failure(request_id, "invalid_params", "Optimizer result cancel requires only queryId.")
        try:
            return _success(request_id, optimizer_result_controller.cancel(query_id))
        except OptimizerResultQueryNotFoundError as error:
            return _failure(request_id, "optimizer_result_query_not_found", str(error))

    if method == "optimizer.results.detail":
        if set(params) != {"runId", "queryId", "rowKey"} or not all(
            isinstance(params.get(field), str) and params[field]
            for field in ("runId", "queryId", "rowKey")
        ):
            return _failure(
                request_id,
                "invalid_params",
                "Optimizer result detail requires only runId, queryId, and rowKey.",
            )
        try:
            return _success(request_id, optimizer_result_controller.detail(params))
        except OptimizerResultDetailUnavailableError as error:
            return _failure(request_id, "optimizer_result_detail_unavailable", str(error))

    if method == "optimizer.results.equip":
        if set(params) != {"runId", "queryId", "rowKey"} or not all(
            isinstance(params.get(field), str) and params[field]
            for field in ("runId", "queryId", "rowKey")
        ):
            return _failure(
                request_id,
                "invalid_params",
                "Optimizer build equip requires only runId, queryId, and rowKey.",
            )
        try:
            return _success(request_id, optimizer_result_controller.equip(params))
        except OptimizerResultEquipUnavailableError as error:
            return _failure(request_id, "optimizer_result_equip_unavailable", str(error))

    if method == "optimizer.results.export.get":
        if params:
            return _failure(request_id, "invalid_params", "Optimizer result export status does not accept parameters.")
        return _success(request_id, optimizer_result_controller.get_export_snapshot())

    if method == "optimizer.results.export.start":
        required = {"runId", "queryId", "format", "destination"}
        if set(params) != required or not all(isinstance(params.get(field), str) and params[field] for field in required):
            return _failure(request_id, "invalid_params", "Optimizer result export requires an active view, format, and destination.")
        try:
            return _success(request_id, optimizer_result_controller.start_export(params))
        except OptimizerResultExportUnavailableError as error:
            return _failure(request_id, "optimizer_result_export_unavailable", str(error))

    if method == "optimizer.results.export.cancel":
        export_id = params.get("exportId")
        if set(params) != {"exportId"} or not isinstance(export_id, str) or not export_id:
            return _failure(request_id, "invalid_params", "Optimizer result export cancel requires only exportId.")
        try:
            return _success(request_id, optimizer_result_controller.cancel_export(export_id))
        except OptimizerResultExportUnavailableError as error:
            return _failure(request_id, "optimizer_result_export_unavailable", str(error))

    if method == "optimizer.search.cancel":
        job_id = params.get("jobId")
        if set(params) != {"jobId"} or not isinstance(job_id, str) or not job_id:
            return _failure(request_id, "invalid_params", "Optimizer search cancel requires only jobId.")
        try:
            result = optimizer_search_controller.cancel(job_id)
        except OptimizerSearchJobNotFoundError as error:
            return _failure(request_id, "optimizer_search_job_not_found", str(error))
        return _success(request_id, result)

    if method == "optimizer.search.retry-cpu":
        job_id = params.get("jobId")
        if set(params) != {"jobId"} or not isinstance(job_id, str) or not job_id:
            return _failure(request_id, "invalid_params", "Optimizer CPU retry requires only jobId.")
        try:
            result = optimizer_search_controller.retry_cpu(job_id)
        except (OptimizerSearchBusyError, OptimizerSearchJobNotFoundError) as error:
            return _failure(request_id, "optimizer_search_recovery_unavailable", str(error))
        return _success(request_id, result)

    if (
        method.startswith("optimizer.hero.")
        or method.startswith("optimizer.artifact.")
        or method.startswith("optimizer.profile.")
    ) and optimizer_profile_controller is None:
        return _failure(request_id, "service_unavailable", "Optimizer profile service is not available.")

    try:
        if method == "optimizer.hero.search":
            if set(params) != {"query", "limit"}:
                return _failure(request_id, "invalid_params", "Hero search requires only query and limit.")
            query = params.get("query")
            limit = params.get("limit")
            if not isinstance(query, str) or isinstance(limit, bool) or not isinstance(limit, int):
                return _failure(request_id, "invalid_params", "Hero search requires text query and integer limit.")
            return _success(request_id, optimizer_profile_controller.search_heroes(query, limit))

        if method == "optimizer.hero.details":
            hero_id = params.get("heroId")
            if set(params) != {"heroId"} or not isinstance(hero_id, str) or not hero_id.strip():
                return _failure(request_id, "invalid_params", "Hero details requires only heroId.")
            return _success(request_id, optimizer_profile_controller.hero_details(hero_id))

        if method == "optimizer.artifact.search":
            if set(params) != {"query", "limit"}:
                return _failure(request_id, "invalid_params", "Artifact search requires only query and limit.")
            query = params.get("query")
            limit = params.get("limit")
            if not isinstance(query, str) or isinstance(limit, bool) or not isinstance(limit, int):
                return _failure(request_id, "invalid_params", "Artifact search requires text query and integer limit.")
            return _success(request_id, optimizer_profile_controller.search_artifacts(query, limit))

        if method == "optimizer.profile.load":
            hero_id = params.get("heroId")
            if set(params) != {"heroId"} or not isinstance(hero_id, str) or not hero_id.strip():
                return _failure(request_id, "invalid_params", "Optimizer profile load requires only heroId.")
            return _success(request_id, optimizer_profile_controller.load_draft(hero_id))

        if method == "optimizer.profile.save":
            draft = params.get("draft")
            if set(params) != {"draft"} or not isinstance(draft, Mapping):
                return _failure(request_id, "invalid_params", "Optimizer profile save requires only a draft object.")
            return _success(request_id, optimizer_profile_controller.save_draft(draft))
    except OptimizerProfileServiceError as error:
        data: dict[str, Any] = {
            "category": error.category,
            "issueCode": error.code,
            "readOnly": error.read_only,
        }
        if error.field_path is not None:
            data["fieldPath"] = error.field_path
        return _failure(request_id, "optimizer_profile_failed", str(error), data=data)

    return _failure(request_id, "method_not_found", f"Unknown method: {method}")


def write_protocol_message(output_stream: TextIO, message: Mapping[str, Any], lock: Any = None) -> None:
    encoded = json.dumps(message, separators=(",", ":")) + "\n"
    if lock is None:
        output_stream.write(encoded)
        output_stream.flush()
        return
    with lock:
        output_stream.write(encoded)
        output_stream.flush()


def serve(
    input_stream: TextIO,
    output_stream: TextIO,
    *,
    health_controller: "HealthController | None" = None,
    settings_controller: "SettingsController | None" = None,
    analyzer_controller: "AnalyzerController | None" = None,
    enhancement_controller: "EnhancementController | None" = None,
    optimizer_inventory_controller: "OptimizerInventoryController | None" = None,
    optimizer_profile_controller: "OptimizerProfileController | None" = None,
    optimizer_search_controller: "OptimizerSearchController | None" = None,
    optimizer_result_controller: Any = None,
    output_lock: Any = None,
) -> None:
    """Serve protocol messages until standard input closes."""

    for raw_line in input_stream:
        line = raw_line.strip()
        if not line:
            continue

        shutdown_requested = False
        try:
            message = json.loads(line)
        except json.JSONDecodeError as error:
            response = _failure(
                None,
                "parse_error",
                "Input was not valid JSON.",
                data={"line": error.lineno, "column": error.colno},
            )
        else:
            response = dispatch_message(
                message,
                health_controller,
                settings_controller,
                analyzer_controller,
                enhancement_controller,
                optimizer_inventory_controller,
                optimizer_profile_controller,
                optimizer_search_controller,
                optimizer_result_controller,
            )
            shutdown_requested = (
                isinstance(message, Mapping)
                and message.get("method") == "system.shutdown"
                and response.get("ok") is True
            )

        write_protocol_message(output_stream, response, output_lock)

        if shutdown_requested:
            return
