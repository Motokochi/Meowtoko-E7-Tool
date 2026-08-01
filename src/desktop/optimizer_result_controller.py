"""Latest-query-wins background controller for bounded optimizer result pages."""

from __future__ import annotations

import copy
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from src.desktop.optimizer_result_service import (
    OptimizerResultCancelled,
    OptimizerResultPageContext,
    OptimizerResultService,
    OptimizerResultServiceError,
    result_options,
)
from src.desktop.optimizer_search_controller import OptimizerSearchController


class OptimizerResultSessionError(RuntimeError):
    pass


class OptimizerResultQueryNotFoundError(RuntimeError):
    pass


class OptimizerResultDetailUnavailableError(RuntimeError):
    pass


class OptimizerResultEquipUnavailableError(RuntimeError):
    pass


class OptimizerResultExportUnavailableError(RuntimeError):
    pass


def _idle(sequence: int = 0) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "state": "idle",
        "queryId": None,
        "runId": None,
        "stage": None,
        "scannedRows": "0",
        "totalRows": "0",
        "canCancel": False,
        "categoryCounts": {"exact": "0", "oneAway": "0", "twoAway": "0"},
        "filteredRows": None,
        "pageIndex": 0,
        "pageSize": 100,
        "pageCount": 0,
        "startOffset": "0",
        "endOffset": "0",
        "hasPrevious": False,
        "hasNext": False,
        "outOfRange": False,
        "rows": [],
        "rerunReasons": [],
        "failure": None,
    }


def _idle_detail(sequence: int = 0) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "state": "idle",
        "selectionId": None,
        "runId": None,
        "queryId": None,
        "rowKey": None,
        "detail": None,
        "failure": None,
    }


def _idle_export(sequence: int = 0) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "state": "idle",
        "exportId": None,
        "runId": None,
        "queryId": None,
        "format": None,
        "rowCount": "0",
        "writtenRows": "0",
        "fileBytes": None,
        "sha256": None,
        "canCancel": False,
        "failure": None,
    }


class OptimizerResultController:
    def __init__(
        self,
        service: OptimizerResultService,
        search_controller: OptimizerSearchController,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
        detail_event_sink: Callable[[dict[str, Any]], None] | None = None,
        export_event_sink: Callable[[dict[str, Any]], None] | None = None,
        *,
        query_id_factory: Callable[[], str] = lambda: f"result-query-{uuid.uuid4().hex}",
        selection_id_factory: Callable[[], str] = lambda: f"result-detail-{uuid.uuid4().hex}",
        export_id_factory: Callable[[], str] = lambda: f"result-export-{uuid.uuid4().hex}",
        clock: Callable[[], float] = time.monotonic,
        event_interval_seconds: float = 0.1,
    ) -> None:
        self.service = service
        self.search_controller = search_controller
        self.event_sink = event_sink or (lambda _snapshot: None)
        self.detail_event_sink = detail_event_sink or (lambda _snapshot: None)
        self.export_event_sink = export_event_sink or (lambda _snapshot: None)
        self.query_id_factory = query_id_factory
        self.selection_id_factory = selection_id_factory
        self.export_id_factory = export_id_factory
        self.clock = clock
        self.event_interval_seconds = max(0.0, float(event_interval_seconds))
        self._lock = threading.RLock()
        self._execution_lock = threading.Lock()
        self._detail_execution_lock = threading.Lock()
        self._snapshot = _idle()
        self._detail_snapshot = _idle_detail()
        self._export_snapshot = _idle_export()
        self._page_context: OptimizerResultPageContext | None = None
        self._cancel = threading.Event()
        self._detail_cancel = threading.Event()
        self._export_cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._detail_thread: threading.Thread | None = None
        self._export_thread: threading.Thread | None = None
        self._last_emitted = float("-inf")
        self._last_detail_emitted = float("-inf")
        self._last_export_emitted = float("-inf")
        setter = getattr(search_controller, "set_result_invalidator", None)
        if callable(setter):
            setter(self.invalidate)

    def get_options(self) -> dict[str, Any]:
        return result_options()

    def invalidate(self) -> None:
        with self._lock:
            self._cancel.set()
            self._detail_cancel.set()
            self._export_cancel.set()
            self._page_context = None
            self._snapshot = _idle(int(self._snapshot["sequence"]) + 1)
            self._detail_snapshot = _idle_detail(int(self._detail_snapshot["sequence"]) + 1)
            self._export_snapshot = _idle_export(int(self._export_snapshot["sequence"]) + 1)
            detail_snapshot = copy.deepcopy(self._detail_snapshot)
            export_snapshot = copy.deepcopy(self._export_snapshot)
        self._emit_detail(detail_snapshot, force=True)
        self._emit_export(export_snapshot, force=True)

    def _active_context(self, run_id: str | None = None):
        return self.search_controller.get_completed_context(run_id)

    def get_snapshot(self) -> dict[str, Any]:
        with self._lock:
            run_id = self._snapshot["runId"]
            if run_id is not None and self._active_context(run_id) is None:
                self._cancel.set()
                self._detail_cancel.set()
                self._page_context = None
                self._snapshot = _idle(int(self._snapshot["sequence"]) + 1)
                self._detail_snapshot = _idle_detail(int(self._detail_snapshot["sequence"]) + 1)
            return copy.deepcopy(self._snapshot)

    def get_detail_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._detail_snapshot)

    def get_export_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._export_snapshot)

    def query(self, payload: Mapping[str, object]) -> dict[str, Any]:
        run_id = payload.get("runId") if isinstance(payload, Mapping) else None
        if not isinstance(run_id, str) or not run_id:
            raise OptimizerResultSessionError("A completed result run is required.")
        completed = self._active_context(run_id)
        if completed is None:
            raise OptimizerResultSessionError("The completed result run is no longer active.")
        search = self.search_controller.get_snapshot()
        requested_page = payload.get("pageIndex")
        requested_size = payload.get("pageSize")
        page_index = requested_page if isinstance(requested_page, int) and not isinstance(requested_page, bool) else 0
        page_size = requested_size if isinstance(requested_size, int) and not isinstance(requested_size, bool) else 100
        query_id = self.query_id_factory()
        cancel = threading.Event()
        with self._lock:
            self._cancel.set()
            self._detail_cancel.set()
            self._export_cancel.set()
            self._page_context = None
            self._detail_snapshot = _idle_detail(int(self._detail_snapshot["sequence"]) + 1)
            self._export_snapshot = _idle_export(int(self._export_snapshot["sequence"]) + 1)
            detail_snapshot = copy.deepcopy(self._detail_snapshot)
            export_snapshot = copy.deepcopy(self._export_snapshot)
            self._cancel = cancel
            self._snapshot = {
                **_idle(int(self._snapshot["sequence"]) + 1),
                "state": "running",
                "queryId": query_id,
                "runId": run_id,
                "stage": "queued",
                "canCancel": True,
                "categoryCounts": copy.deepcopy(search["categoryCounts"]),
                "totalRows": str(sum(int(value) for value in search["categoryCounts"].values())),
                "pageIndex": page_index,
                "pageSize": page_size,
            }
            self._thread = threading.Thread(
                target=self._run,
                args=(query_id, run_id, completed[1], copy.deepcopy(dict(payload)), cancel),
                name=f"e7-results-{query_id[-8:]}",
                daemon=True,
            )
            thread = self._thread
            snapshot = copy.deepcopy(self._snapshot)
        self._emit_detail(detail_snapshot, force=True)
        self._emit_export(export_snapshot, force=True)
        self._emit(snapshot, force=True)
        thread.start()
        return snapshot

    def start_export(self, payload: Mapping[str, object]) -> dict[str, Any]:
        expected = {"runId", "queryId", "format", "destination"}
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise OptimizerResultExportUnavailableError("Export requires the active result view and a save destination.")
        run_id, query_id = payload.get("runId"), payload.get("queryId")
        export_format, destination = payload.get("format"), payload.get("destination")
        if not all(isinstance(value, str) and value for value in (run_id, query_id, export_format, destination)):
            raise OptimizerResultExportUnavailableError("Export requires the active result view and a save destination.")
        export_id = self.export_id_factory()
        cancel = threading.Event()
        with self._lock:
            if (
                self._snapshot["state"] != "completed"
                or self._snapshot["runId"] != run_id
                or self._snapshot["queryId"] != query_id
                or self._page_context is None
                or self._export_snapshot["state"] == "running"
            ):
                raise OptimizerResultExportUnavailableError("Export requires the active completed result view.")
            page_context = self._page_context
            self._export_cancel = cancel
            self._export_snapshot = {
                **_idle_export(int(self._export_snapshot["sequence"]) + 1),
                "state": "running",
                "exportId": export_id,
                "runId": run_id,
                "queryId": query_id,
                "format": export_format,
                "rowCount": str(page_context.index.row_count),
                "canCancel": True,
            }
            self._export_thread = threading.Thread(
                target=self._run_export,
                args=(export_id, page_context, run_id, query_id, destination, export_format, cancel),
                name=f"e7-result-export-{export_id[-8:]}",
                daemon=True,
            )
            thread = self._export_thread
            snapshot = copy.deepcopy(self._export_snapshot)
        self._emit_export(snapshot, force=True)
        thread.start()
        return snapshot

    def cancel_export(self, export_id: str) -> dict[str, Any]:
        with self._lock:
            if self._export_snapshot["exportId"] != export_id:
                raise OptimizerResultExportUnavailableError("The result export is no longer active.")
            if self._export_snapshot["state"] != "running":
                return copy.deepcopy(self._export_snapshot)
            self._export_cancel.set()
            self._export_snapshot = {
                **self._export_snapshot,
                "sequence": int(self._export_snapshot["sequence"]) + 1,
                "canCancel": False,
            }
            snapshot = copy.deepcopy(self._export_snapshot)
        self._emit_export(snapshot, force=True)
        return snapshot

    def cancel(self, query_id: str) -> dict[str, Any]:
        with self._lock:
            if self._snapshot["queryId"] != query_id:
                raise OptimizerResultQueryNotFoundError("The result query is no longer active.")
            if self._snapshot["state"] != "running":
                return copy.deepcopy(self._snapshot)
            self._cancel.set()
            self._snapshot = {
                **self._snapshot,
                "sequence": int(self._snapshot["sequence"]) + 1,
                "canCancel": False,
            }
            snapshot = copy.deepcopy(self._snapshot)
        self._emit(snapshot, force=True)
        return snapshot

    def detail(self, payload: Mapping[str, object]) -> dict[str, Any]:
        if not isinstance(payload, Mapping) or set(payload) != {"runId", "queryId", "rowKey"}:
            raise OptimizerResultDetailUnavailableError("Choose a build from the active visible result page.")
        run_id = payload.get("runId")
        query_id = payload.get("queryId")
        row_key = payload.get("rowKey")
        if not all(isinstance(value, str) and value for value in (run_id, query_id, row_key)):
            raise OptimizerResultDetailUnavailableError("Choose a build from the active visible result page.")
        selection_id = self.selection_id_factory()
        cancel = threading.Event()
        with self._lock:
            if (
                self._snapshot["state"] != "completed"
                or self._snapshot["runId"] != run_id
                or self._snapshot["queryId"] != query_id
                or not any(row.get("rowKey") == row_key for row in self._snapshot["rows"])
                or self._page_context is None
            ):
                raise OptimizerResultDetailUnavailableError("Choose a build from the active visible result page.")
            page_context = self._page_context
            self._detail_cancel.set()
            self._detail_cancel = cancel
            self._detail_snapshot = {
                "sequence": int(self._detail_snapshot["sequence"]) + 1,
                "state": "loading",
                "selectionId": selection_id,
                "runId": run_id,
                "queryId": query_id,
                "rowKey": row_key,
                "detail": None,
                "failure": None,
            }
            self._detail_thread = threading.Thread(
                target=self._run_detail,
                args=(selection_id, page_context, run_id, query_id, row_key, cancel),
                name=f"e7-result-detail-{selection_id[-8:]}",
                daemon=True,
            )
            thread = self._detail_thread
            snapshot = copy.deepcopy(self._detail_snapshot)
        self._emit_detail(snapshot, force=True)
        thread.start()
        return snapshot

    def equip(self, payload: Mapping[str, object]) -> dict[str, Any]:
        if not isinstance(payload, Mapping) or set(payload) != {"runId", "queryId", "rowKey"}:
            raise OptimizerResultEquipUnavailableError(
                "Choose a build from the active visible result page."
            )
        run_id = payload.get("runId")
        query_id = payload.get("queryId")
        row_key = payload.get("rowKey")
        if not all(isinstance(value, str) and value for value in (run_id, query_id, row_key)):
            raise OptimizerResultEquipUnavailableError(
                "Choose a build from the active visible result page."
            )
        with self._lock:
            if (
                self._snapshot["state"] != "completed"
                or self._snapshot["runId"] != run_id
                or self._snapshot["queryId"] != query_id
                or not any(row.get("rowKey") == row_key for row in self._snapshot["rows"])
                or self._page_context is None
            ):
                raise OptimizerResultEquipUnavailableError(
                    "Choose a build from the active visible result page."
                )
            page_context = self._page_context
        try:
            with self._detail_execution_lock:
                result = self.service.equip_build(
                    page_context,
                    run_id,
                    query_id,
                    row_key,
                )
        except OptimizerResultServiceError as error:
            raise OptimizerResultEquipUnavailableError(str(error)) from error
        except Exception:
            raise OptimizerResultEquipUnavailableError(
                "The selected build could not be equipped locally."
            ) from None

        return result

    def close(self, timeout_seconds: float = 2.5) -> None:
        self._cancel.set()
        self._detail_cancel.set()
        self._export_cancel.set()
        with self._lock:
            thread = self._thread
            detail_thread = self._detail_thread
            export_thread = self._export_thread
            self._page_context = None
        for active in (thread, detail_thread, export_thread):
            if active is not None and active is not threading.current_thread():
                active.join(timeout=max(0.0, float(timeout_seconds)))

    def reset_for_data_erasure(self, timeout_seconds: float = 15.0) -> dict[str, Any]:
        """Quiesce result readers/exports before their owned stores are erased."""

        self.close(timeout_seconds)
        with self._lock:
            active = (self._thread, self._detail_thread, self._export_thread)
            if any(thread is not None and thread.is_alive() for thread in active):
                raise OptimizerResultSessionError(
                    "Optimizer results are still stopping. Wait a moment and erase again."
                )
            self._thread = None
            self._detail_thread = None
            self._export_thread = None
            self._cancel = threading.Event()
            self._detail_cancel = threading.Event()
            self._export_cancel = threading.Event()
            self._page_context = None
            self._snapshot = _idle(int(self._snapshot["sequence"]) + 1)
            self._detail_snapshot = _idle_detail(int(self._detail_snapshot["sequence"]) + 1)
            self._export_snapshot = _idle_export(int(self._export_snapshot["sequence"]) + 1)
            snapshot = copy.deepcopy(self._snapshot)
            detail_snapshot = copy.deepcopy(self._detail_snapshot)
            export_snapshot = copy.deepcopy(self._export_snapshot)
        self._emit(snapshot, force=True)
        self._emit_detail(detail_snapshot, force=True)
        self._emit_export(export_snapshot, force=True)
        return snapshot

    def _run(self, query_id: str, run_id: str, prepared: object, payload: Mapping[str, object], cancel: threading.Event) -> None:
        try:
            with self._execution_lock:
                if cancel.is_set():
                    raise OptimizerResultCancelled()
                outcome = self.service.execute(
                    prepared,
                    run_id,
                    query_id,
                    payload,
                    cancel.is_set,
                    lambda stage, scanned, total: self._progress(query_id, stage, scanned, total),
                )
        except OptimizerResultCancelled:
            self._finish(query_id, "cancelled")
            return
        except OptimizerResultServiceError as error:
            self._finish(query_id, "failed", failure={"code": error.code, "message": str(error)})
            return
        except Exception:
            self._finish(
                query_id,
                "failed",
                failure={"code": "result-query-failed", "message": "The result page could not be prepared safely."},
            )
            return
        detail_context = getattr(outcome, "detail_context", None)
        if cancel.is_set():
            self._finish(query_id, "cancelled")
        elif outcome["kind"] == "rerun-required":
            self._finish(query_id, "rerun-required", rerunReasons=outcome["reasons"])
        else:
            page = dict(outcome)
            page.pop("kind")
            self._finish(query_id, "completed", detail_context=detail_context, **page)

    def _run_detail(
        self,
        selection_id: str,
        page_context: OptimizerResultPageContext,
        run_id: str,
        query_id: str,
        row_key: str,
        cancel: threading.Event,
    ) -> None:
        try:
            with self._detail_execution_lock:
                if cancel.is_set():
                    return
                detail = self.service.resolve_detail(page_context, run_id, query_id, row_key)
        except OptimizerResultServiceError as error:
            self._finish_detail(selection_id, "failed", failure={"code": error.code, "message": str(error)})
            return
        except Exception:
            self._finish_detail(
                selection_id,
                "failed",
                failure={"code": "result-detail-failed", "message": "The selected build detail could not be prepared safely."},
            )
            return
        if not cancel.is_set():
            self._finish_detail(selection_id, "completed", detail=detail)

    def _run_export(
        self,
        export_id: str,
        page_context: OptimizerResultPageContext,
        run_id: str,
        query_id: str,
        destination: str,
        export_format: str,
        cancel: threading.Event,
    ) -> None:
        try:
            outcome = self.service.export_active_view(
                page_context,
                run_id,
                query_id,
                destination,
                export_format,
                cancel.is_set,
                lambda written, total: self._export_progress(export_id, written, total),
            )
        except OptimizerResultCancelled:
            self._finish_export(export_id, "cancelled")
            return
        except OptimizerResultServiceError as error:
            self._finish_export(export_id, "failed", failure={"code": error.code, "message": str(error)})
            return
        except Exception:
            self._finish_export(
                export_id,
                "failed",
                failure={"code": "result-export-failed", "message": "The result view could not be exported safely."},
            )
            return
        self._finish_export(export_id, "completed", **outcome)

    def _progress(self, query_id: str, stage: str, scanned: int, total: int) -> None:
        with self._lock:
            if self._snapshot["queryId"] != query_id or self._snapshot["state"] != "running":
                return
            self._snapshot = {
                **self._snapshot,
                "sequence": int(self._snapshot["sequence"]) + 1,
                "stage": stage,
                "scannedRows": str(max(int(self._snapshot["scannedRows"]), scanned)),
                "totalRows": str(max(int(self._snapshot["totalRows"]), total)),
            }
            snapshot = copy.deepcopy(self._snapshot)
        self._emit(snapshot)

    def _finish(
        self,
        query_id: str,
        state: str,
        *,
        detail_context: OptimizerResultPageContext | None = None,
        **changes: object,
    ) -> None:
        with self._lock:
            if self._snapshot["queryId"] != query_id:
                return
            self._page_context = detail_context if state == "completed" else None
            self._snapshot = {
                **self._snapshot,
                **changes,
                "sequence": int(self._snapshot["sequence"]) + 1,
                "state": state,
                "stage": None,
                "canCancel": False,
            }
            snapshot = copy.deepcopy(self._snapshot)
        self._emit(snapshot, force=True)

    def _finish_detail(self, selection_id: str, state: str, **changes: object) -> None:
        with self._lock:
            if self._detail_snapshot["selectionId"] != selection_id:
                return
            self._detail_snapshot = {
                **self._detail_snapshot,
                **changes,
                "sequence": int(self._detail_snapshot["sequence"]) + 1,
                "state": state,
            }
            snapshot = copy.deepcopy(self._detail_snapshot)
        self._emit_detail(snapshot, force=True)

    def _export_progress(self, export_id: str, written: int, total: int) -> None:
        with self._lock:
            if self._export_snapshot["exportId"] != export_id or self._export_snapshot["state"] != "running":
                return
            self._export_snapshot = {
                **self._export_snapshot,
                "sequence": int(self._export_snapshot["sequence"]) + 1,
                "writtenRows": str(max(int(self._export_snapshot["writtenRows"]), written)),
                "rowCount": str(max(int(self._export_snapshot["rowCount"]), total)),
            }
            snapshot = copy.deepcopy(self._export_snapshot)
        self._emit_export(snapshot)

    def _finish_export(self, export_id: str, state: str, **changes: object) -> None:
        with self._lock:
            if self._export_snapshot["exportId"] != export_id:
                return
            self._export_snapshot = {
                **self._export_snapshot,
                **changes,
                "sequence": int(self._export_snapshot["sequence"]) + 1,
                "state": state,
                "canCancel": False,
            }
            snapshot = copy.deepcopy(self._export_snapshot)
        self._emit_export(snapshot, force=True)

    def _emit(self, snapshot: dict[str, Any], *, force: bool = False) -> None:
        now = self.clock()
        if not force and now - self._last_emitted < self.event_interval_seconds:
            return
        self._last_emitted = now
        self.event_sink(copy.deepcopy(snapshot))

    def _emit_detail(self, snapshot: dict[str, Any], *, force: bool = False) -> None:
        now = self.clock()
        if not force and now - self._last_detail_emitted < self.event_interval_seconds:
            return
        self._last_detail_emitted = now
        self.detail_event_sink(copy.deepcopy(snapshot))

    def _emit_export(self, snapshot: dict[str, Any], *, force: bool = False) -> None:
        now = self.clock()
        if not force and now - self._last_export_emitted < self.event_interval_seconds:
            return
        self._last_export_emitted = now
        self.export_event_sink(copy.deepcopy(snapshot))


__all__ = [
    "OptimizerResultController",
    "OptimizerResultDetailUnavailableError",
    "OptimizerResultEquipUnavailableError",
    "OptimizerResultExportUnavailableError",
    "OptimizerResultQueryNotFoundError",
    "OptimizerResultSessionError",
]
