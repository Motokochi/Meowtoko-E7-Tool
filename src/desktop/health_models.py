"""Typed health-state models shared by probes, protocol handlers, and tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class CapabilityState(str, Enum):
    CHECKING = "checking"
    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    ERROR = "error"
    IN_PROGRESS = "in_progress"


class OverallHealthState(str, Enum):
    CHECKING = "checking"
    READY = "ready"
    DEGRADED = "degraded"
    ERROR = "error"


class OperationState(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class HealthAction:
    id: str
    label: str
    kind: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "label": self.label, "kind": self.kind}


@dataclass(frozen=True)
class HealthCapability:
    id: str
    title: str
    state: CapabilityState
    summary: str
    required: bool = False
    detail: str | None = None
    version: str | None = None
    path: str | None = None
    actions: tuple[HealthAction, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "state": self.state.value,
            "summary": self.summary,
            "required": self.required,
            "actions": [action.to_dict() for action in self.actions],
            "metadata": dict(self.metadata),
        }
        for key, value in (("detail", self.detail), ("version", self.version), ("path", self.path)):
            if value is not None:
                result[key] = value
        return result


@dataclass(frozen=True)
class HealthOperation:
    id: str
    action_id: str
    state: OperationState
    message: str
    progress: float | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "actionId": self.action_id,
            "state": self.state.value,
            "message": self.message,
        }
        if self.progress is not None:
            result["progress"] = max(0.0, min(1.0, float(self.progress)))
        if self.error is not None:
            result["error"] = self.error
        return result


@dataclass(frozen=True)
class HealthSnapshot:
    overall: OverallHealthState
    capabilities: tuple[HealthCapability, ...]
    checked_at: str
    operation: HealthOperation | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "overall": self.overall.value,
            "checkedAt": self.checked_at,
            "capabilities": [capability.to_dict() for capability in self.capabilities],
        }
        if self.operation is not None:
            result["operation"] = self.operation.to_dict()
        return result


CAPABILITY_SPECS: tuple[tuple[str, str, bool], ...] = (
    ("backend", "Application backend", True),
    ("storage", "Local data", True),
    ("tesseract", "Tesseract OCR", False),
    ("ollama", "Ollama vision", False),
    ("cuda", "GPU acceleration", False),
    ("packet", "Game packet capture", False),
    ("adb", "ADB automation", False),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def aggregate_overall(capabilities: tuple[HealthCapability, ...]) -> OverallHealthState:
    if any(capability.state in {CapabilityState.CHECKING, CapabilityState.IN_PROGRESS} for capability in capabilities):
        return OverallHealthState.CHECKING
    if any(
        capability.required and capability.state in {CapabilityState.UNAVAILABLE, CapabilityState.ERROR}
        for capability in capabilities
    ):
        return OverallHealthState.ERROR
    if any(capability.state is not CapabilityState.READY for capability in capabilities):
        return OverallHealthState.DEGRADED
    return OverallHealthState.READY


def checking_snapshot(operation: HealthOperation | None = None) -> HealthSnapshot:
    capabilities = tuple(
        HealthCapability(
            id=capability_id,
            title=title,
            state=CapabilityState.CHECKING,
            summary="Checking…",
            required=required,
        )
        for capability_id, title, required in CAPABILITY_SPECS
    )
    return HealthSnapshot(
        overall=OverallHealthState.CHECKING,
        capabilities=capabilities,
        checked_at=utc_now(),
        operation=operation,
    )
