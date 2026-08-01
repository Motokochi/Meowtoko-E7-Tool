"""Background health refresh/action coordinator with snapshot progress events."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import replace

from src.desktop.health_models import (
    CapabilityState,
    HealthCapability,
    HealthOperation,
    HealthSnapshot,
    OperationState,
    aggregate_overall,
    checking_snapshot,
    utc_now,
)
from src.desktop.health_service import HealthService, OperationCancelled

EventSink = Callable[[dict], None]


class HealthController:
    ALLOWED_ACTIONS = frozenset({
        "ollama.start",
        "ollama.pull_model",
        "cuda.install",
        "cuda.repair",
    })
    CANCELLABLE_ACTIONS = frozenset({"cuda.install", "cuda.repair"})

    def __init__(self, service: HealthService, event_sink: EventSink | None = None):
        self.service = service
        self.event_sink = event_sink or (lambda _snapshot: None)
        self._lock = threading.RLock()
        self._snapshot = checking_snapshot()
        self._operation: HealthOperation | None = None
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._has_checked = False

    def get_snapshot(self, *, ensure_refresh: bool = True) -> dict:
        should_start = False
        with self._lock:
            if ensure_refresh and not self._has_checked and not self._is_running_locked():
                should_start = True
            snapshot = self._snapshot.to_dict()
        if should_start:
            return self.refresh()
        return snapshot

    def refresh(self) -> dict:
        with self._lock:
            if self._is_running_locked():
                return self._snapshot.to_dict()
            operation = HealthOperation(
                id=uuid.uuid4().hex,
                action_id="health.refresh",
                state=OperationState.RUNNING,
                message="Checking local capabilities…",
                progress=0.0,
            )
            self._operation = operation
            self._snapshot = checking_snapshot(operation)
            self._cancel.clear()
            self._thread = threading.Thread(
                target=self._run_refresh,
                args=(True,),
                name="e7-health-refresh",
                daemon=True,
            )
            self._thread.start()
            snapshot = self._snapshot.to_dict()
        self._emit(snapshot)
        return snapshot

    def run_action(self, action_id: str) -> dict:
        if action_id == "health.cancel":
            return self._cancel_action()
        if action_id not in self.ALLOWED_ACTIONS:
            raise ValueError(f"Unsupported health action: {action_id}")
        with self._lock:
            if self._is_running_locked():
                return self._snapshot.to_dict()
            operation = HealthOperation(
                id=uuid.uuid4().hex,
                action_id=action_id,
                state=OperationState.RUNNING,
                message={
                    "ollama.start": "Starting Ollama…",
                    "ollama.pull_model": "Preparing model download…",
                    "cuda.install": "Preparing optional GPU component setup…",
                    "cuda.repair": "Preparing optional GPU component repair…",
                }[action_id],
                progress=0.0 if action_id in {"ollama.pull_model", "cuda.install", "cuda.repair"} else None,
            )
            self._operation = operation
            capability_id = "cuda" if action_id.startswith("cuda.") else "ollama"
            capabilities = tuple(
                replace(
                    capability,
                    state=CapabilityState.IN_PROGRESS,
                    summary=operation.message,
                )
                if capability.id == capability_id
                else capability
                for capability in self._snapshot.capabilities
            )
            self._snapshot = HealthSnapshot(
                overall=aggregate_overall(capabilities),
                capabilities=capabilities,
                checked_at=utc_now(),
                operation=operation,
            )
            self._cancel.clear()
            self._thread = threading.Thread(
                target=self._run_action,
                args=(action_id,),
                name=f"e7-health-{action_id.replace('.', '-')}",
                daemon=True,
            )
            self._thread.start()
            snapshot = self._snapshot.to_dict()
        self._emit(snapshot)
        return snapshot

    def _cancel_action(self) -> dict:
        with self._lock:
            operation = self._operation
            if (
                not operation
                or operation.action_id not in self.CANCELLABLE_ACTIONS
                or operation.state is not OperationState.RUNNING
                or not self._is_running_locked()
            ):
                raise ValueError("No cancellable health action is running.")
            self._cancel.set()
            operation = HealthOperation(
                id=operation.id,
                action_id=operation.action_id,
                state=OperationState.RUNNING,
                message="Cancelling optional GPU component setup…",
                progress=operation.progress,
            )
            self._operation = operation
            self._snapshot = HealthSnapshot(
                overall=self._snapshot.overall,
                capabilities=self._snapshot.capabilities,
                checked_at=utc_now(),
                operation=operation,
            )
            snapshot = self._snapshot.to_dict()
        self._emit(snapshot)
        return snapshot

    def close(self) -> None:
        self._cancel.set()

    def _run_refresh(self, auto_start_ollama: bool) -> None:
        try:
            capabilities = self.service.check_all(
                auto_start_ollama=auto_start_ollama,
                on_capability=self._capability_progress,
            )
        except Exception as error:
            self._finish_failed(str(error))
            return
        self._finish_success(capabilities, "Local capability check complete.")

    def _run_action(self, action_id: str) -> None:
        try:
            if action_id == "ollama.start":
                self.service.start_ollama()
            elif action_id == "ollama.pull_model":
                self.service.pull_ollama_model(self._operation_progress, self._cancel.is_set)
            else:
                self.service.setup_cuda_components(self._operation_progress, self._cancel.is_set)
            capabilities = self.service.check_all(
                auto_start_ollama=False,
                on_capability=self._capability_progress,
            )
        except OperationCancelled:
            self._finish_cancelled()
            return
        except Exception as error:
            self._finish_failed(str(error))
            return
        if action_id.startswith("cuda."):
            cuda = next(capability for capability in capabilities if capability.id == "cuda")
            message = (
                "Optional GPU components are installed and CUDA is ready."
                if cuda.state is CapabilityState.READY
                else "Optional GPU components are installed, but CUDA is not ready. CPU mode remains available."
            )
        else:
            message = {
                "ollama.start": "Ollama started successfully.",
                "ollama.pull_model": "Vision model download complete.",
            }[action_id]
        self._finish_success(capabilities, message)

    def _capability_progress(self, capability: HealthCapability, index: int, total: int) -> None:
        with self._lock:
            existing = {item.id: item for item in self._snapshot.capabilities}
            existing[capability.id] = capability
            ordered = tuple(existing[item.id] for item in self._snapshot.capabilities)
            operation = self._operation
            if operation and operation.action_id == "health.refresh":
                operation = HealthOperation(
                    id=operation.id,
                    action_id=operation.action_id,
                    state=OperationState.RUNNING,
                    message=f"Checked {capability.title}.",
                    progress=index / total,
                )
                self._operation = operation
            self._snapshot = HealthSnapshot(
                overall=aggregate_overall(ordered),
                capabilities=ordered,
                checked_at=utc_now(),
                operation=operation,
            )
            snapshot = self._snapshot.to_dict()
        self._emit(snapshot)

    def _operation_progress(self, progress: float | None, message: str) -> None:
        with self._lock:
            operation = self._operation
            if not operation:
                return
            operation = HealthOperation(
                id=operation.id,
                action_id=operation.action_id,
                state=OperationState.RUNNING,
                message=message,
                progress=progress,
            )
            self._operation = operation
            self._snapshot = HealthSnapshot(
                overall=self._snapshot.overall,
                capabilities=self._snapshot.capabilities,
                checked_at=utc_now(),
                operation=operation,
            )
            snapshot = self._snapshot.to_dict()
        self._emit(snapshot)

    def _finish_success(self, capabilities: tuple[HealthCapability, ...], message: str) -> None:
        with self._lock:
            operation = self._operation
            completed = HealthOperation(
                id=operation.id if operation else uuid.uuid4().hex,
                action_id=operation.action_id if operation else "health.refresh",
                state=OperationState.SUCCEEDED,
                message=message,
                progress=1.0,
            )
            self._operation = completed
            self._has_checked = True
            self._snapshot = HealthSnapshot(
                overall=aggregate_overall(capabilities),
                capabilities=capabilities,
                checked_at=utc_now(),
                operation=completed,
            )
            snapshot = self._snapshot.to_dict()
        self._emit(snapshot)

    def _finish_failed(self, error: str) -> None:
        with self._lock:
            operation = self._operation
            failed = HealthOperation(
                id=operation.id if operation else uuid.uuid4().hex,
                action_id=operation.action_id if operation else "health.refresh",
                state=OperationState.FAILED,
                message="Health operation failed.",
                error=error,
            )
            self._operation = failed
            self._has_checked = True
            capabilities = tuple(
                replace(
                    capability,
                    state=CapabilityState.ERROR if capability.required else CapabilityState.DEGRADED,
                    summary="Health operation failed.",
                    detail=error,
                )
                if capability.state in {CapabilityState.CHECKING, CapabilityState.IN_PROGRESS}
                else capability
                for capability in self._snapshot.capabilities
            )
            self._snapshot = HealthSnapshot(
                overall=aggregate_overall(capabilities),
                capabilities=capabilities,
                checked_at=utc_now(),
                operation=failed,
            )
            snapshot = self._snapshot.to_dict()
        self._emit(snapshot)

    def _finish_cancelled(self) -> None:
        with self._lock:
            operation = self._operation
            cancelled = HealthOperation(
                id=operation.id if operation else uuid.uuid4().hex,
                action_id=operation.action_id if operation else "health.cancel",
                state=OperationState.CANCELLED,
                message="GPU component setup was cancelled. CPU mode remains available.",
                progress=operation.progress if operation else None,
            )
            self._operation = cancelled
            self._has_checked = True
            capabilities = tuple(
                replace(
                    capability,
                    state=CapabilityState.DEGRADED,
                    summary="GPU component setup was cancelled; CPU mode remains available.",
                    detail=None,
                )
                if capability.id == "cuda" and capability.state is CapabilityState.IN_PROGRESS
                else capability
                for capability in self._snapshot.capabilities
            )
            self._snapshot = HealthSnapshot(
                overall=aggregate_overall(capabilities),
                capabilities=capabilities,
                checked_at=utc_now(),
                operation=cancelled,
            )
            snapshot = self._snapshot.to_dict()
        self._emit(snapshot)

    def _is_running_locked(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _emit(self, snapshot: dict) -> None:
        self.event_sink(snapshot)
