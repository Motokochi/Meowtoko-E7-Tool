"""Single-job analyzer coordinator with progress, cancellation, and stale-result guards."""

from __future__ import annotations

import copy
import threading
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from src.desktop.analyzer_service import AnalyzerCancelledError, AnalyzerService

EventSink = Callable[[dict[str, Any]], None]


class AnalyzerBusyError(RuntimeError):
    pass


class AnalyzerJobNotFoundError(RuntimeError):
    pass


class AnalyzerController:
    def __init__(self, service: AnalyzerService, event_sink: EventSink | None = None):
        self.service = service
        self.event_sink = event_sink or (lambda _snapshot: None)
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._snapshot: dict[str, Any] = {
            "state": "idle",
            "stage": "idle",
            "message": "Ready to analyze gear.",
            "progress": 0.0,
        }
        self._debug: dict[str, Any] = {"available": False, "artifacts": []}

    def get_options(self) -> dict[str, Any]:
        return self.service.get_options()

    def evaluate(self, piece: Mapping[str, Any]) -> dict[str, Any]:
        return self.service.evaluate(piece)

    def get_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._snapshot)

    def start_scan(self) -> dict[str, Any]:
        with self._lock:
            if self._snapshot.get("state") in {"running", "cancelling"}:
                raise AnalyzerBusyError("A gear scan is already running.")
            job_id = uuid.uuid4().hex
            self._cancel = threading.Event()
            self._debug = {"available": False, "artifacts": []}
            self._snapshot = {
                "jobId": job_id,
                "state": "running",
                "stage": "starting",
                "message": "Starting gear scan…",
                "progress": 0.0,
            }
            self._thread = threading.Thread(
                target=self._run_scan,
                args=(job_id, self._cancel),
                name=f"e7-analyzer-{job_id[:8]}",
                daemon=True,
            )
            thread = self._thread
            snapshot = copy.deepcopy(self._snapshot)
        self._emit(snapshot)
        thread.start()
        return snapshot

    def cancel_scan(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if self._snapshot.get("jobId") != job_id:
                raise AnalyzerJobNotFoundError("The analyzer job is no longer active.")
            if self._snapshot["state"] in {"succeeded", "failed", "cancelled"}:
                return copy.deepcopy(self._snapshot)
            self._cancel.set()
            self._snapshot = {
                **self._snapshot,
                "state": "cancelling",
                "message": "Cancelling gear scan…",
            }
            snapshot = copy.deepcopy(self._snapshot)
        self._emit(snapshot)
        return snapshot

    def get_debug(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._debug)

    def close(self) -> None:
        self._cancel.set()

    def _run_scan(self, job_id: str, cancel: threading.Event) -> None:
        try:
            result, debug = self.service.scan(
                job_id,
                cancel.is_set,
                lambda stage, message, progress: self._progress(job_id, stage, message, progress),
            )
        except AnalyzerCancelledError:
            self._finish_cancelled(job_id)
            return
        except Exception as error:
            if cancel.is_set():
                self._finish_cancelled(job_id)
            else:
                self._finish_failed(job_id, str(error))
            return
        if cancel.is_set():
            self._finish_cancelled(job_id)
            return
        with self._lock:
            if self._snapshot.get("jobId") != job_id:
                return
            self._debug = copy.deepcopy(debug)
            self._snapshot = {
                "jobId": job_id,
                "state": "succeeded",
                "stage": "complete",
                "message": "Gear scan complete.",
                "progress": 1.0,
                "result": copy.deepcopy(result),
            }
            snapshot = copy.deepcopy(self._snapshot)
        self._emit(snapshot)

    def _progress(self, job_id: str, stage: str, message: str, progress: float) -> None:
        with self._lock:
            if self._snapshot.get("jobId") != job_id or self._cancel.is_set():
                return
            if self._snapshot.get("state") != "running":
                return
            self._snapshot = {
                **self._snapshot,
                "stage": stage,
                "message": message,
                "progress": max(float(self._snapshot.get("progress", 0.0)), min(1.0, float(progress))),
            }
            snapshot = copy.deepcopy(self._snapshot)
        self._emit(snapshot)

    def _finish_cancelled(self, job_id: str) -> None:
        with self._lock:
            if self._snapshot.get("jobId") != job_id:
                return
            self._snapshot = {
                "jobId": job_id,
                "state": "cancelled",
                "stage": "cancelled",
                "message": "Gear scan cancelled.",
                "progress": float(self._snapshot.get("progress", 0.0)),
            }
            snapshot = copy.deepcopy(self._snapshot)
        self._emit(snapshot)

    def _finish_failed(self, job_id: str, error: str) -> None:
        with self._lock:
            if self._snapshot.get("jobId") != job_id:
                return
            self._snapshot = {
                "jobId": job_id,
                "state": "failed",
                "stage": "failed",
                "message": "Gear scan failed.",
                "progress": float(self._snapshot.get("progress", 0.0)),
                "error": error,
            }
            snapshot = copy.deepcopy(self._snapshot)
        self._emit(snapshot)

    def _emit(self, snapshot: dict[str, Any]) -> None:
        self.event_sink(copy.deepcopy(snapshot))
