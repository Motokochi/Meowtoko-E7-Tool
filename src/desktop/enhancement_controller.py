"""Single-job enhancement coordinator with bounded logs and stale-result guards."""

from __future__ import annotations

import copy
import threading
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from src.desktop.enhancement_service import EnhancementCancelledError, EnhancementService

EventSink = Callable[[dict[str, Any]], None]


class EnhancementBusyError(RuntimeError):
    pass


class EnhancementJobNotFoundError(RuntimeError):
    pass


class EnhancementController:
    def __init__(self, service: EnhancementService, event_sink: EventSink | None = None, *, max_logs: int = 200):
        self.service = service
        self.event_sink = event_sink or (lambda _snapshot: None)
        self.max_logs = max(10, int(max_logs))
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._snapshot: dict[str, Any] = {
            "state": "idle",
            "stage": "idle",
            "message": "Ready to enhance gear.",
            "progress": 0.0,
            "pieceNumber": 0,
            "logs": [],
        }
        self._debug: dict[str, Any] = {"available": False, "artifacts": []}

    def get_options(self) -> dict[str, Any]:
        return self.service.get_options()

    def get_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._snapshot)

    def start(self, raw_options: Mapping[str, Any]) -> dict[str, Any]:
        options, settings = self.service.prepare(raw_options)
        with self._lock:
            if self._snapshot.get("state") in {"running", "cancelling"}:
                raise EnhancementBusyError("Enhancement automation is already running.")
            job_id = uuid.uuid4().hex
            self._cancel = threading.Event()
            self._debug = {"available": False, "artifacts": []}
            self._snapshot = {
                "jobId": job_id,
                "state": "running",
                "stage": "starting",
                "message": f"Starting {options['mode'].upper()} enhancement automation…",
                "progress": 0.0,
                "pieceNumber": 0,
                "logs": [],
                "options": copy.deepcopy(options),
            }
            self._thread = threading.Thread(
                target=self._run,
                args=(job_id, options, settings, self._cancel),
                name=f"e7-enhancement-{job_id[:8]}",
                daemon=True,
            )
            thread = self._thread
            snapshot = copy.deepcopy(self._snapshot)
        self._emit(snapshot)
        thread.start()
        return snapshot

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if self._snapshot.get("jobId") != job_id:
                raise EnhancementJobNotFoundError("The enhancement job is no longer active.")
            if self._snapshot.get("state") in {"succeeded", "failed", "cancelled"}:
                return copy.deepcopy(self._snapshot)
            self._cancel.set()
            self._snapshot = {
                **self._snapshot,
                "state": "cancelling",
                "message": "Stopping safely before the next automation action…",
            }
            snapshot = copy.deepcopy(self._snapshot)
        self._emit(snapshot)
        return snapshot

    def get_debug(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._debug)

    def close(self) -> None:
        self._cancel.set()

    def _run(self, job_id: str, options: dict[str, Any], settings: dict[str, Any], cancel: threading.Event) -> None:
        try:
            result, debug = self.service.run(
                job_id,
                options,
                settings,
                cancel.is_set,
                lambda stage, message, progress, piece, decision: self._progress(
                    job_id, stage, message, progress, piece, decision
                ),
                lambda message: self._log(job_id, message),
            )
        except EnhancementCancelledError:
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
                **self._snapshot,
                "state": "succeeded",
                "stage": "complete",
                "message": "Enhancement run finished safely.",
                "progress": 1.0,
                "pieceNumber": result["currentPiece"],
                "result": copy.deepcopy(result),
                **({"lastDecision": copy.deepcopy(result["lastDecision"])} if result.get("lastDecision") else {}),
            }
            snapshot = copy.deepcopy(self._snapshot)
        self._emit(snapshot)

    def _progress(
        self,
        job_id: str,
        stage: str,
        message: str,
        progress: float,
        piece_number: int,
        decision: dict[str, Any] | None,
    ) -> None:
        with self._lock:
            if self._snapshot.get("jobId") != job_id or self._cancel.is_set():
                return
            if self._snapshot.get("state") != "running":
                return
            self._snapshot = {
                **self._snapshot,
                "stage": str(stage),
                "message": str(message),
                "progress": max(float(self._snapshot.get("progress", 0.0)), min(0.99, float(progress))),
                "pieceNumber": max(int(self._snapshot.get("pieceNumber", 0)), int(piece_number)),
                **({"lastDecision": copy.deepcopy(decision)} if decision else {}),
            }
            snapshot = copy.deepcopy(self._snapshot)
        self._emit(snapshot)

    def _log(self, job_id: str, message: str) -> None:
        with self._lock:
            if self._snapshot.get("jobId") != job_id or self._cancel.is_set():
                return
            logs = [*self._snapshot.get("logs", []), str(message)][-self.max_logs:]
            self._snapshot = {**self._snapshot, "logs": logs}
            snapshot = copy.deepcopy(self._snapshot)
        self._emit(snapshot)

    def _finish_cancelled(self, job_id: str) -> None:
        with self._lock:
            if self._snapshot.get("jobId") != job_id:
                return
            self._snapshot = {
                **self._snapshot,
                "state": "cancelled",
                "stage": "cancelled",
                "message": "Enhancement automation stopped safely.",
            }
            snapshot = copy.deepcopy(self._snapshot)
        self._emit(snapshot)

    def _finish_failed(self, job_id: str, error: str) -> None:
        with self._lock:
            if self._snapshot.get("jobId") != job_id:
                return
            self._snapshot = {
                **self._snapshot,
                "state": "failed",
                "stage": "failed",
                "message": "Enhancement automation failed.",
                "error": error,
            }
            snapshot = copy.deepcopy(self._snapshot)
        self._emit(snapshot)

    def _emit(self, snapshot: dict[str, Any]) -> None:
        self.event_sink(copy.deepcopy(snapshot))
