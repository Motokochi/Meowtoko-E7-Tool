"""Single-flight background controller for desktop optimizer execution."""

from __future__ import annotations

import copy
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from src.desktop.optimizer_search_service import (
    OptimizerSearchCancelled,
    OptimizerSearchExecution,
    OptimizerSearchService,
    OptimizerSearchServiceError,
    PreparedOptimizerSearch,
)


EventSink = Callable[[dict[str, Any]], None]
Clock = Callable[[], float]
_SAFE_TOKEN = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
_PRIVATE_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|(?:^|\s)/\S+|file:)", re.IGNORECASE)
_ACTIVE_STATES = frozenset({"preparing", "running"})
_TERMINAL_STATES = frozenset({"completed", "overflowed", "cancelled", "failed"})


class OptimizerSearchBusyError(RuntimeError):
    pass


class OptimizerSearchJobNotFoundError(RuntimeError):
    pass


def _token(value: object, fallback: str) -> str:
    candidate = str(value).strip().lower()
    return candidate if _SAFE_TOKEN.fullmatch(candidate) else fallback


def _category_counts(values: tuple[int, int, int]) -> dict[str, str]:
    return {
        "exact": str(values[0]),
        "oneAway": str(values[1]),
        "twoAway": str(values[2]),
    }


def _monotonic_counts(current: Mapping[str, object], values: tuple[int, int, int]) -> tuple[int, int, int]:
    return tuple(
        max(int(current[key]), value)
        for key, value in zip(("exact", "oneAway", "twoAway"), values, strict=True)
    )  # type: ignore[return-value]


def _safe_message(value: object) -> str:
    candidate = " ".join(str(value).split())[:240]
    if not candidate or _PRIVATE_PATH.search(candidate):
        return "The optimizer search stopped safely. No partial results were kept."
    return candidate


def _idle_snapshot() -> dict[str, Any]:
    return {
        "sequence": 0,
        "jobId": None,
        "requestId": None,
        "state": "idle",
        "backend": None,
        "totalPermutations": "0",
        "searchedPermutations": "0",
        "categoryCounts": _category_counts((0, 0, 0)),
        "elapsedSeconds": 0.0,
        "canCancel": False,
        "resultAvailable": False,
        "resultRunId": None,
        "failure": None,
    }


class OptimizerSearchController:
    def __init__(
        self,
        service: OptimizerSearchService,
        event_sink: EventSink | None = None,
        *,
        clock: Clock = time.monotonic,
        event_interval_seconds: float = 0.1,
        job_id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
        request_id_factory: Callable[[], str] = lambda: f"request.desktop-search.{uuid.uuid4().hex}",
        run_id_factory: Callable[[], str] = lambda: f"run-{uuid.uuid4().hex}",
    ) -> None:
        self.service = service
        self.event_sink = event_sink or (lambda _snapshot: None)
        self.clock = clock
        self.event_interval_seconds = max(0.0, float(event_interval_seconds))
        self.job_id_factory = job_id_factory
        self.request_id_factory = request_id_factory
        self.run_id_factory = run_id_factory
        self._lock = threading.RLock()
        self._snapshot = _idle_snapshot()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._last_emitted_at = float("-inf")
        self._recovery: PreparedOptimizerSearch | None = None
        self._completed: tuple[str, PreparedOptimizerSearch] | None = None
        self._result_invalidator: Callable[[], None] = lambda: None

    def set_result_invalidator(self, invalidator: Callable[[], None]) -> None:
        if not callable(invalidator):
            raise TypeError("result invalidator must be callable")
        with self._lock:
            self._result_invalidator = invalidator

    def get_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._snapshot)

    def get_completed_context(
        self, run_id: str | None = None
    ) -> tuple[str, PreparedOptimizerSearch] | None:
        """Return the private active result authority without projecting it to JSON."""

        with self._lock:
            completed = self._completed
            if completed is None or (run_id is not None and completed[0] != run_id):
                return None
            return completed

    def start(self, draft: Mapping[str, object]) -> dict[str, Any]:
        with self._lock:
            if self._snapshot["state"] in _ACTIVE_STATES:
                raise OptimizerSearchBusyError("An optimizer search is already running.")
            job_id = self.job_id_factory()
            request_id = self.request_id_factory()
            run_id = self.run_id_factory()
            self._cancel = threading.Event()
            self._recovery = None
            self._completed = None
            self._snapshot = {
                **_idle_snapshot(),
                "sequence": int(self._snapshot["sequence"]) + 1,
                "jobId": job_id,
                "requestId": request_id,
                "state": "preparing",
                "canCancel": True,
            }
            self._thread = threading.Thread(
                target=self._run_initial,
                args=(job_id, request_id, run_id, copy.deepcopy(dict(draft)), self._cancel),
                name=f"e7-optimizer-{job_id[:8]}",
                daemon=True,
            )
            thread = self._thread
            snapshot = copy.deepcopy(self._snapshot)
        self._emit(snapshot, force=True)
        self._result_invalidator()
        thread.start()
        return snapshot

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if self._snapshot["jobId"] != job_id:
                raise OptimizerSearchJobNotFoundError("The optimizer search is no longer active.")
            if self._snapshot["state"] not in _ACTIVE_STATES:
                return copy.deepcopy(self._snapshot)
            if self._snapshot["canCancel"] is False:
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

    def retry_cpu(self, failed_job_id: str) -> dict[str, Any]:
        with self._lock:
            failure = self._snapshot.get("failure")
            if (
                self._snapshot["jobId"] != failed_job_id
                or self._snapshot["state"] != "failed"
                or not isinstance(failure, Mapping)
                or failure.get("cpuRecoveryAvailable") is not True
                or self._recovery is None
            ):
                raise OptimizerSearchJobNotFoundError("CPU recovery is not available for this search.")
            prepared = self._recovery
            job_id = self.job_id_factory()
            run_id = self.run_id_factory()
            self._cancel = threading.Event()
            self._recovery = None
            self._snapshot = {
                **_idle_snapshot(),
                "sequence": int(self._snapshot["sequence"]) + 1,
                "jobId": job_id,
                "requestId": prepared.request.request_id,
                "state": "running",
                "backend": "cpu",
                "totalPermutations": str(prepared.total_permutations),
                "canCancel": True,
            }
            self._thread = threading.Thread(
                target=self._run_prepared,
                args=(job_id, run_id, prepared, self._cancel, True),
                name=f"e7-optimizer-cpu-{job_id[:8]}",
                daemon=True,
            )
            thread = self._thread
            snapshot = copy.deepcopy(self._snapshot)
        self._emit(snapshot, force=True)
        thread.start()
        return snapshot

    def close(self, timeout_seconds: float = 2.5) -> None:
        """Cancel owned work and give its transactional writer time to abort."""

        self._cancel.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(timeout_seconds)))

    def reset_for_data_erasure(self, timeout_seconds: float = 15.0) -> dict[str, Any]:
        """Quiesce search work and discard every in-memory result authority."""

        self.close(timeout_seconds)
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise OptimizerSearchBusyError(
                    "The optimizer search is still stopping. Wait a moment and erase again."
                )
            sequence = int(self._snapshot["sequence"]) + 1
            self._thread = None
            self._cancel = threading.Event()
            self._recovery = None
            self._completed = None
            self._snapshot = {**_idle_snapshot(), "sequence": sequence}
            snapshot = copy.deepcopy(self._snapshot)
        self._result_invalidator()
        self._emit(snapshot, force=True)
        return snapshot

    def _run_initial(
        self,
        job_id: str,
        request_id: str,
        run_id: str,
        draft: Mapping[str, object],
        cancel: threading.Event,
    ) -> None:
        try:
            prepared = self.service.prepare(draft, request_id, cancel.is_set)
        except OptimizerSearchCancelled:
            self._finish_cancelled(job_id)
            return
        except OptimizerSearchServiceError as error:
            if cancel.is_set():
                self._finish_cancelled(job_id)
            else:
                self._finish_failed(job_id, error.stage, error.code, str(error), False)
            return
        except Exception:
            if cancel.is_set():
                self._finish_cancelled(job_id)
            else:
                self._finish_failed(
                    job_id,
                    "preparation",
                    "search-preparation-failed",
                    "The optimizer search could not be prepared safely.",
                    False,
                )
            return
        with self._lock:
            if self._snapshot["jobId"] != job_id:
                return
            self._snapshot = {
                **self._snapshot,
                "sequence": int(self._snapshot["sequence"]) + 1,
                "state": "running",
                "backend": prepared.backend,
                "totalPermutations": str(prepared.total_permutations),
                "canCancel": not cancel.is_set(),
            }
            snapshot = copy.deepcopy(self._snapshot)
        self._emit(snapshot, force=True)
        self._run_prepared(job_id, run_id, prepared, cancel, False)

    def _run_prepared(
        self,
        job_id: str,
        run_id: str,
        prepared: PreparedOptimizerSearch,
        cancel: threading.Event,
        force_cpu: bool,
    ) -> None:
        try:
            result = self.service.run(
                prepared,
                run_id,
                cancel.is_set,
                lambda backend, total, searched, counts, elapsed: self._progress(
                    job_id, backend, total, searched, counts, elapsed
                ),
                force_cpu=force_cpu,
            )
        except Exception:
            if cancel.is_set():
                self._finish_cancelled(job_id)
            else:
                self._finish_failed(
                    job_id,
                    "execution",
                    "search-execution-failed",
                    "The optimizer search could not finish safely.",
                    False,
                )
            return
        if cancel.is_set() and result.state == "failed":
            self._finish_cancelled(job_id)
            return
        if result.state == "failed":
            with self._lock:
                if self._snapshot["jobId"] == job_id and not force_cpu:
                    self._recovery = prepared
            self._finish_execution(job_id, result, cpu_recovery=not force_cpu)
            return
        if result.state == "completed" and result.result_run_id is not None:
            with self._lock:
                if self._snapshot["jobId"] == job_id:
                    self._completed = (
                        result.result_run_id,
                        replace(prepared, backend="cpu")
                        if force_cpu and isinstance(prepared, PreparedOptimizerSearch)
                        else prepared,
                    )
        self._finish_execution(job_id, result, cpu_recovery=False)

    def _progress(
        self,
        job_id: str,
        backend: str,
        total: int,
        searched: int,
        counts: tuple[int, int, int],
        elapsed: float,
    ) -> None:
        with self._lock:
            if self._snapshot["jobId"] != job_id or self._snapshot["state"] != "running":
                return
            previous_searched = int(self._snapshot["searchedPermutations"])
            previous_elapsed = float(self._snapshot["elapsedSeconds"])
            previous_counts = self._snapshot["categoryCounts"]
            monotonic_counts = _monotonic_counts(previous_counts, counts)
            self._snapshot = {
                **self._snapshot,
                "sequence": int(self._snapshot["sequence"]) + 1,
                "backend": backend,
                "totalPermutations": str(max(int(self._snapshot["totalPermutations"]), total)),
                "searchedPermutations": str(max(previous_searched, searched)),
                "categoryCounts": _category_counts(monotonic_counts),
                "elapsedSeconds": max(previous_elapsed, float(elapsed)),
            }
            snapshot = copy.deepcopy(self._snapshot)
        self._emit(snapshot)

    def _finish_execution(
        self,
        job_id: str,
        result: OptimizerSearchExecution,
        *,
        cpu_recovery: bool,
    ) -> None:
        if result.state == "failed":
            self._finish_failed(
                job_id,
                result.failure_stage or "cuda-search",
                result.failure_code or "cuda-stage-failed",
                "GPU search stopped safely. No partial results were kept.",
                cpu_recovery,
                result=result,
            )
            return
        with self._lock:
            if self._snapshot["jobId"] != job_id:
                return
            terminal_counts = _monotonic_counts(self._snapshot["categoryCounts"], result.category_counts)
            self._snapshot = {
                **self._snapshot,
                "sequence": int(self._snapshot["sequence"]) + 1,
                "state": result.state,
                "totalPermutations": str(result.total_permutations),
                "searchedPermutations": str(result.searched_permutations),
                "categoryCounts": _category_counts(terminal_counts),
                "elapsedSeconds": max(float(self._snapshot["elapsedSeconds"]), result.elapsed_seconds),
                "canCancel": False,
                "resultAvailable": result.state == "completed",
                "resultRunId": result.result_run_id if result.state == "completed" else None,
                "failure": None,
            }
            snapshot = copy.deepcopy(self._snapshot)
        self._emit(snapshot, force=True)

    def _finish_cancelled(self, job_id: str) -> None:
        with self._lock:
            if self._snapshot["jobId"] != job_id:
                return
            self._snapshot = {
                **self._snapshot,
                "sequence": int(self._snapshot["sequence"]) + 1,
                "state": "cancelled",
                "canCancel": False,
                "resultAvailable": False,
                "resultRunId": None,
                "failure": None,
            }
            snapshot = copy.deepcopy(self._snapshot)
        self._emit(snapshot, force=True)

    def _finish_failed(
        self,
        job_id: str,
        stage: str,
        code: str,
        message: str,
        cpu_recovery: bool,
        *,
        result: OptimizerSearchExecution | None = None,
    ) -> None:
        with self._lock:
            if self._snapshot["jobId"] != job_id:
                return
            failed_counts = (
                None
                if result is None
                else _monotonic_counts(self._snapshot["categoryCounts"], result.category_counts)
            )
            self._snapshot = {
                **self._snapshot,
                "sequence": int(self._snapshot["sequence"]) + 1,
                "state": "failed",
                "totalPermutations": (
                    self._snapshot["totalPermutations"]
                    if result is None else str(result.total_permutations)
                ),
                "searchedPermutations": (
                    self._snapshot["searchedPermutations"]
                    if result is None else str(result.searched_permutations)
                ),
                "categoryCounts": (
                    self._snapshot["categoryCounts"]
                    if failed_counts is None else _category_counts(failed_counts)
                ),
                "elapsedSeconds": (
                    self._snapshot["elapsedSeconds"]
                    if result is None
                    else max(float(self._snapshot["elapsedSeconds"]), result.elapsed_seconds)
                ),
                "canCancel": False,
                "resultAvailable": False,
                "resultRunId": None,
                "failure": {
                    "stage": _token(stage, "search"),
                    "code": _token(code, "search-failed"),
                    "message": _safe_message(message),
                    "cpuRecoveryAvailable": bool(cpu_recovery),
                },
            }
            snapshot = copy.deepcopy(self._snapshot)
        self._emit(snapshot, force=True)

    def _emit(self, snapshot: dict[str, Any], *, force: bool = False) -> None:
        now = self.clock()
        with self._lock:
            if not force and now - self._last_emitted_at < self.event_interval_seconds:
                return
            self._last_emitted_at = now
        self.event_sink(copy.deepcopy(snapshot))


__all__ = [
    "OptimizerSearchBusyError",
    "OptimizerSearchController",
    "OptimizerSearchJobNotFoundError",
]
