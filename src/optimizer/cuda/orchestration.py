"""Exact packed-CUDA progress, cancellation, and CPU recovery."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from numbers import Integral, Real

from src.optimizer.cuda.compaction import (
    CudaCompactionChunkPlan,
    CudaCompactionHostBatch,
)
from src.optimizer.cuda.inputs import CudaDeviceInputs
from src.optimizer.cuda.packed import (
    CUDA_PACKED_DEFAULT_CHUNK_PERMUTATIONS,
    CUDA_PACKED_PERMUTATIONS_PER_WORD,
    CudaPackedExactFilterRunner,
    CudaPackedExactMaterializer,
    CudaPackedFilterBatch,
)
from src.optimizer.cuda.runtime import CudaDiagnosticStatus, CudaRuntimeDiagnostic
from src.optimizer.search.cpu_orchestration import CpuExactSearchResult
from src.optimizer.search.match_counting import MatchCountingContext, MatchCountingResult


CudaCancellationPredicate = Callable[[], bool]
CudaProgressCallback = Callable[["CudaSearchProgress"], None]
CudaClock = Callable[[], float]
CudaCpuRecoveryAdapter = Callable[["CudaCpuRecoveryRequest"], CpuExactSearchResult]


class CudaSearchOrchestrationError(ValueError):
    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code} at {path}: {message}")


def _error(code: str, path: str, message: str) -> CudaSearchOrchestrationError:
    return CudaSearchOrchestrationError(code, path, message)


def _integer(
    value: object,
    path: str,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise _error("invalid-integer", path, "must be an integer; booleans are not accepted.")
    normalized = int(value)
    if normalized < minimum or (maximum is not None and normalized > maximum):
        raise _error("integer-out-of-range", path, "is outside the permitted range.")
    return normalized


def _number(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise _error("invalid-number", path, "must be a finite nonnegative number.")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0:
        raise _error("invalid-number", path, "must be a finite nonnegative number.")
    return normalized


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise _error("invalid-text", path, "must be a non-empty canonical string.")
    return value


def _counts(value: object, path: str) -> tuple[int, int, int]:
    try:
        supplied = tuple(value)  # type: ignore[arg-type]
    except TypeError:
        raise _error("invalid-category-counts", path, "must be a three-count sequence.") from None
    if len(supplied) != 3:
        raise _error("category-count-length", path, "must contain exactly three counts.")
    return tuple(_integer(item, f"{path}[{index}]") for index, item in enumerate(supplied))  # type: ignore[return-value]


def _clock_value(clock: CudaClock, path: str) -> float:
    try:
        value = clock()
    except Exception as error:
        raise _error("clock-failed", path, f"clock failed: {error}") from error
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
        raise _error("invalid-clock-value", path, "clock must return a finite number.")
    return float(value)


def _check_cancellation(predicate: CudaCancellationPredicate) -> bool:
    try:
        value = predicate()
    except Exception as error:
        raise _error("cancellation-predicate-failed", "should_cancel", str(error)) from error
    if not isinstance(value, bool):
        raise _error("invalid-cancellation-result", "should_cancel", "must return boolean.")
    return value


def _emit_progress(callback: CudaProgressCallback | None, progress: "CudaSearchProgress") -> None:
    if callback is None:
        return
    try:
        callback(progress)
    except Exception as error:
        raise _error("progress-callback-failed", "on_progress", str(error)) from error


class CudaSearchTerminalState(StrEnum):
    COMPLETED = "completed"
    OVERFLOWED = "overflowed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CudaSearchFailure:
    stage: str
    code: str
    exception_type: str
    message: str
    cpu_recovery_action: str = "rerun-cpu-from-permutation-zero"
    partial_gpu_rows_discarded: bool = True

    def __post_init__(self) -> None:
        for name in ("stage", "code", "exception_type", "message"):
            object.__setattr__(self, name, _text(getattr(self, name), f"CudaSearchFailure.{name}"))
        if self.cpu_recovery_action != "rerun-cpu-from-permutation-zero":
            raise _error("invalid-recovery-action", "CudaSearchFailure.cpu_recovery_action", "must require a fresh CPU rerun.")
        if self.partial_gpu_rows_discarded is not True:
            raise _error("unsafe-partial-row-state", "CudaSearchFailure.partial_gpu_rows_discarded", "failed runs discard partial rows.")


def _failure(stage: str, error: BaseException) -> CudaSearchFailure:
    code = getattr(error, "code", None)
    return CudaSearchFailure(
        stage,
        code if isinstance(code, str) and code.strip() else "cuda-stage-failed",
        type(error).__name__,
        str(error).strip() or repr(error),
    )


@dataclass(frozen=True, slots=True)
class CudaCpuRecoveryRequest:
    request_id: str
    cuda_failure: CudaSearchFailure
    start_index: int = 0
    allow_gpu_partial_rows: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _text(self.request_id, "CudaCpuRecoveryRequest.request_id"))
        if not isinstance(self.cuda_failure, CudaSearchFailure):
            raise _error("invalid-cuda-failure", "CudaCpuRecoveryRequest.cuda_failure", "must be CudaSearchFailure.")
        if self.start_index != 0 or isinstance(self.start_index, bool):
            raise _error("invalid-recovery-start", "CudaCpuRecoveryRequest.start_index", "must be zero.")
        if self.allow_gpu_partial_rows is not False:
            raise _error("unsafe-recovery-input", "CudaCpuRecoveryRequest.allow_gpu_partial_rows", "cannot consume GPU partial rows.")


@dataclass(frozen=True, slots=True)
class CudaCpuRecoveryResult:
    cuda_failure: CudaSearchFailure
    cpu_result: CpuExactSearchResult

    def __post_init__(self) -> None:
        if not isinstance(self.cuda_failure, CudaSearchFailure):
            raise _error("invalid-cuda-failure", "CudaCpuRecoveryResult.cuda_failure", "must be CudaSearchFailure.")
        if not isinstance(self.cpu_result, CpuExactSearchResult):
            raise _error("invalid-cpu-recovery-result", "CudaCpuRecoveryResult.cpu_result", "must be CpuExactSearchResult.")


@dataclass(frozen=True, slots=True)
class CudaCpuRecoveryOffer:
    request: CudaCpuRecoveryRequest
    _adapter: CudaCpuRecoveryAdapter | None = None

    @property
    def available(self) -> bool:
        return self._adapter is not None

    def run(self) -> CudaCpuRecoveryResult:
        if self._adapter is None:
            raise _error("cpu-recovery-adapter-unavailable", "CudaCpuRecoveryOffer", "no CPU recovery adapter is configured.")
        try:
            result = self._adapter(self.request)
        except Exception as error:
            raise _error("cpu-recovery-failed", "cpu_recovery_adapter", str(error)) from error
        if not isinstance(result, CpuExactSearchResult) or result.request_id != self.request.request_id:
            raise _error("invalid-cpu-recovery-result", "cpu_recovery_adapter", "must return the same request's CpuExactSearchResult.")
        return CudaCpuRecoveryResult(self.request.cuda_failure, result)


def _validate_exact_accounting(
    evaluated: int,
    candidates: tuple[int, int, int],
    out_of_scope: int,
    hard: int,
    accepted: tuple[int, int, int],
    path: str,
) -> None:
    if (
        candidates[1:] != (0, 0)
        or accepted[1:] != (0, 0)
        or candidates[0] + out_of_scope != evaluated
        or hard + accepted[0] != candidates[0]
    ):
        raise _error("exact-accounting-mismatch", path, "exact candidates must partition into rejects and accepted rows.")


@dataclass(frozen=True, slots=True)
class CudaSearchProgress:
    request_id: str
    total_permutations: int
    evaluated_permutations: int
    elapsed_seconds: float
    completed_chunk_count: int
    maximum_replacement_distance: int
    category_candidate_counts: tuple[int, int, int]
    out_of_scope_count: int
    disabled_category_count: int
    hard_bound_rejected_count: int
    tolerance_rejected_counts: tuple[int, int, int]
    accepted_category_counts: tuple[int, int, int]
    selected_batch_width: int
    device_diagnostic: CudaRuntimeDiagnostic

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _text(self.request_id, "CudaSearchProgress.request_id"))
        total = _integer(self.total_permutations, "CudaSearchProgress.total_permutations", minimum=1)
        evaluated = _integer(self.evaluated_permutations, "CudaSearchProgress.evaluated_permutations", minimum=1, maximum=total)
        candidates = _counts(self.category_candidate_counts, "CudaSearchProgress.category_candidate_counts")
        accepted = _counts(self.accepted_category_counts, "CudaSearchProgress.accepted_category_counts")
        out_of_scope = _integer(self.out_of_scope_count, "CudaSearchProgress.out_of_scope_count")
        hard = _integer(self.hard_bound_rejected_count, "CudaSearchProgress.hard_bound_rejected_count")
        if self.maximum_replacement_distance != 0 or self.disabled_category_count != 0 or tuple(self.tolerance_rejected_counts) != (0, 0, 0):
            raise _error("near-set-mode-removed", "CudaSearchProgress", "progress is exact-only.")
        _validate_exact_accounting(evaluated, candidates, out_of_scope, hard, accepted, "CudaSearchProgress")
        if not isinstance(self.device_diagnostic, CudaRuntimeDiagnostic) or self.device_diagnostic.status is not CudaDiagnosticStatus.READY:
            raise _error("cuda-not-ready", "CudaSearchProgress.device_diagnostic", "requires ready CUDA evidence.")
        object.__setattr__(self, "total_permutations", total)
        object.__setattr__(self, "evaluated_permutations", evaluated)
        object.__setattr__(self, "elapsed_seconds", _number(self.elapsed_seconds, "CudaSearchProgress.elapsed_seconds"))
        object.__setattr__(self, "completed_chunk_count", _integer(self.completed_chunk_count, "CudaSearchProgress.completed_chunk_count", minimum=1))
        object.__setattr__(self, "category_candidate_counts", candidates)
        object.__setattr__(self, "out_of_scope_count", out_of_scope)
        object.__setattr__(self, "hard_bound_rejected_count", hard)
        object.__setattr__(self, "accepted_category_counts", accepted)
        object.__setattr__(self, "selected_batch_width", _integer(self.selected_batch_width, "CudaSearchProgress.selected_batch_width", minimum=1))


@dataclass(frozen=True, slots=True)
class CudaSearchResult:
    state: CudaSearchTerminalState
    request_id: str
    total_permutations: int
    evaluated_permutations: int
    elapsed_seconds: float
    completed_chunk_count: int
    maximum_replacement_distance: int
    chunk_plan: CudaCompactionChunkPlan | None
    device_diagnostic: CudaRuntimeDiagnostic
    category_candidate_counts: tuple[int, int, int]
    out_of_scope_count: int
    disabled_category_count: int
    hard_bound_rejected_count: int
    tolerance_rejected_counts: tuple[int, int, int]
    emitted_counts: tuple[int, int, int]
    counting: MatchCountingResult
    terminal_flat_index: int | None
    batches: tuple[CudaCompactionHostBatch, ...]
    failure: CudaSearchFailure | None = None
    recovery_offer: CudaCpuRecoveryOffer | None = None

    def __post_init__(self) -> None:
        try:
            state = self.state if isinstance(self.state, CudaSearchTerminalState) else CudaSearchTerminalState(self.state)
        except (TypeError, ValueError):
            raise _error("invalid-terminal-state", "CudaSearchResult.state", "is not a CUDA terminal state.") from None
        request_id = _text(self.request_id, "CudaSearchResult.request_id")
        total = _integer(self.total_permutations, "CudaSearchResult.total_permutations", minimum=1)
        evaluated = _integer(self.evaluated_permutations, "CudaSearchResult.evaluated_permutations", maximum=total)
        candidates = _counts(self.category_candidate_counts, "CudaSearchResult.category_candidate_counts")
        emitted = _counts(self.emitted_counts, "CudaSearchResult.emitted_counts")
        out_of_scope = _integer(self.out_of_scope_count, "CudaSearchResult.out_of_scope_count")
        hard = _integer(self.hard_bound_rejected_count, "CudaSearchResult.hard_bound_rejected_count")
        if self.maximum_replacement_distance != 0 or self.disabled_category_count != 0 or tuple(self.tolerance_rejected_counts) != (0, 0, 0):
            raise _error("near-set-mode-removed", "CudaSearchResult", "results are exact-only.")
        _validate_exact_accounting(evaluated, candidates, out_of_scope, hard, emitted, "CudaSearchResult")
        if self.chunk_plan is not None and (
            not isinstance(self.chunk_plan, CudaCompactionChunkPlan)
            or self.chunk_plan.total_permutations != total
        ):
            raise _error("invalid-chunk-plan", "CudaSearchResult.chunk_plan", "must match total_permutations.")
        if evaluated and self.chunk_plan is None:
            raise _error("missing-chunk-plan", "CudaSearchResult.chunk_plan", "evaluated work requires a plan.")
        if not isinstance(self.counting, MatchCountingResult) or self.counting.request_id != request_id:
            raise _error("invalid-counting-result", "CudaSearchResult.counting", "must match request_id.")
        if self.counting.detected_count > emitted[0]:
            raise _error("counting-emission-mismatch", "CudaSearchResult.counting", "cannot exceed emitted rows.")
        batches = tuple(self.batches)
        if not all(isinstance(batch, CudaCompactionHostBatch) for batch in batches):
            raise _error("invalid-compact-batches", "CudaSearchResult.batches", "must contain compact host batches.")
        if state is CudaSearchTerminalState.COMPLETED:
            transferred = 0
            previous_last: int | None = None
            ordered = True
            for batch in batches:
                flat = batch.array("flat_indices")
                if flat.size:
                    first = int(flat[0])
                    ordered = ordered and (previous_last is None or previous_last < first)
                    previous_last = int(flat[-1])
                transferred += batch.transferred_count
            if (
                evaluated != total
                or self.counting.overflowed
                or self.terminal_flat_index is not None
                or self.failure is not None
                or self.recovery_offer is not None
                or not ordered
                or any(batch.transferred_count != batch.accepted_count for batch in batches)
                or transferred != self.counting.detected_count
                or emitted[0] != self.counting.detected_count
            ):
                raise _error("inconsistent-completed-state", "CudaSearchResult", "completion evidence is inconsistent.")
        elif state is CudaSearchTerminalState.OVERFLOWED:
            if not self.counting.overflowed or batches or self.terminal_flat_index is None or self.failure is not None:
                raise _error("inconsistent-overflow-state", "CudaSearchResult", "overflow requires cap+1 evidence and no rows.")
        elif state is CudaSearchTerminalState.CANCELLED:
            if evaluated >= total or self.counting.overflowed or batches or self.terminal_flat_index is not None or self.failure is not None:
                raise _error("inconsistent-cancelled-state", "CudaSearchResult", "cancellation evidence is inconsistent.")
        elif batches or not isinstance(self.failure, CudaSearchFailure) or not isinstance(self.recovery_offer, CudaCpuRecoveryOffer):
            raise _error("inconsistent-failed-state", "CudaSearchResult", "failure requires cold failure and recovery evidence.")
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "total_permutations", total)
        object.__setattr__(self, "evaluated_permutations", evaluated)
        object.__setattr__(self, "elapsed_seconds", _number(self.elapsed_seconds, "CudaSearchResult.elapsed_seconds"))
        object.__setattr__(self, "completed_chunk_count", _integer(self.completed_chunk_count, "CudaSearchResult.completed_chunk_count"))
        object.__setattr__(self, "category_candidate_counts", candidates)
        object.__setattr__(self, "out_of_scope_count", out_of_scope)
        object.__setattr__(self, "hard_bound_rejected_count", hard)
        object.__setattr__(self, "emitted_counts", emitted)
        object.__setattr__(self, "batches", batches)

    @property
    def completed(self) -> bool:
        return self.state is CudaSearchTerminalState.COMPLETED

    @property
    def overflowed(self) -> bool:
        return self.state is CudaSearchTerminalState.OVERFLOWED

    @property
    def cancelled(self) -> bool:
        return self.state is CudaSearchTerminalState.CANCELLED

    @property
    def failed(self) -> bool:
        return self.state is CudaSearchTerminalState.FAILED

    @property
    def retained_count(self) -> int:
        return self.counting.detected_count if self.completed else 0


def _counting(context: MatchCountingContext, detected: int, *, overflowed: bool = False) -> MatchCountingResult:
    return MatchCountingResult(
        request_id=context.request_id,
        result_cap=context.result_cap,
        detected_count=detected,
        category_counts=(detected, 0, 0),
        overflowed=overflowed,
        guidance=context.overflow_guidance if overflowed else None,
    )


def _plan(inputs: CudaDeviceInputs, diagnostic: CudaRuntimeDiagnostic) -> CudaCompactionChunkPlan:
    if diagnostic.free_vram_bytes is None:
        raise _error("missing-vram-evidence", "diagnostic.free_vram_bytes", "packed planning requires free-memory evidence.")
    free = diagnostic.free_vram_bytes
    total = inputs.total_permutations
    available = free - inputs.byte_count
    if available <= 0:
        raise _error("insufficient-cuda-memory", "diagnostic.free_vram_bytes", "device inputs leave no room for packed masks.")
    memory_limited = available // 8 * CUDA_PACKED_PERMUTATIONS_PER_WORD
    batch = min(total, CUDA_PACKED_DEFAULT_CHUNK_PERMUTATIONS, memory_limited)
    batch -= batch % CUDA_PACKED_PERMUTATIONS_PER_WORD
    if batch == 0:
        batch = min(total, CUDA_PACKED_PERMUTATIONS_PER_WORD)
    generation = math.ceil(batch / CUDA_PACKED_PERMUTATIONS_PER_WORD) * 4 + 20
    return CudaCompactionChunkPlan(
        total,
        batch,
        math.ceil(total / batch),
        inputs.byte_count,
        free,
        inputs.byte_count + generation * 2,
    )


def _terminal(
    state: CudaSearchTerminalState,
    context: MatchCountingContext,
    diagnostic: CudaRuntimeDiagnostic,
    total: int,
    plan: CudaCompactionChunkPlan | None,
    evaluated: int,
    elapsed: float,
    chunks: int,
    candidates: int,
    out_of_scope: int,
    hard: int,
    emitted: int,
    *,
    detected: int | None = None,
    batches: tuple[CudaCompactionHostBatch, ...] = (),
    terminal_flat_index: int | None = None,
    failure: CudaSearchFailure | None = None,
    recovery_adapter: CudaCpuRecoveryAdapter | None = None,
    overflowed: bool = False,
) -> CudaSearchResult:
    recovery = None
    if failure is not None:
        recovery = CudaCpuRecoveryOffer(CudaCpuRecoveryRequest(context.request_id, failure), recovery_adapter)
    return CudaSearchResult(
        state,
        context.request_id,
        total,
        evaluated,
        elapsed,
        chunks,
        0,
        plan,
        diagnostic,
        (candidates, 0, 0),
        out_of_scope,
        0,
        hard,
        (0, 0, 0),
        (emitted, 0, 0),
        _counting(
            context,
            emitted if detected is None else detected,
            overflowed=overflowed,
        ),
        terminal_flat_index,
        batches,
        failure,
        recovery,
    )


def run_controlled_cuda_search(
    device_inputs: CudaDeviceInputs,
    diagnostic: CudaRuntimeDiagnostic,
    counting_context: MatchCountingContext,
    *,
    should_cancel: CudaCancellationPredicate,
    on_progress: CudaProgressCallback | None = None,
    clock: CudaClock | None = None,
    cpu_recovery_adapter: CudaCpuRecoveryAdapter | None = None,
) -> CudaSearchResult:
    """Filter exact builds in packed GPU masks, then materialize matches once."""

    if not isinstance(device_inputs, CudaDeviceInputs) or device_inputs.released:
        raise _error("invalid-device-inputs", "device_inputs", "must be a live device-input lease.")
    if not isinstance(diagnostic, CudaRuntimeDiagnostic) or diagnostic.status is not CudaDiagnosticStatus.READY:
        raise _error("cuda-not-ready", "diagnostic", "must contain ready CUDA evidence.")
    if not isinstance(counting_context, MatchCountingContext):
        raise _error("invalid-counting-context", "counting_context", "must be MatchCountingContext.")
    if not callable(should_cancel):
        raise _error("invalid-cancellation-predicate", "should_cancel", "must be callable.")
    if on_progress is not None and not callable(on_progress):
        raise _error("invalid-progress-callback", "on_progress", "must be callable or None.")
    if clock is not None and not callable(clock):
        raise _error("invalid-clock", "clock", "must be callable or None.")
    if cpu_recovery_adapter is not None and not callable(cpu_recovery_adapter):
        raise _error("invalid-cpu-recovery-adapter", "cpu_recovery_adapter", "must be callable or None.")

    selected_clock = time.perf_counter if clock is None else clock
    started = _clock_value(selected_clock, "clock.start")
    total = device_inputs.total_permutations
    if _check_cancellation(should_cancel):
        return _terminal(
            CudaSearchTerminalState.CANCELLED,
            counting_context,
            diagnostic,
            total,
            None,
            0,
            _clock_value(selected_clock, "clock.cancelled") - started,
            0,
            0,
            0,
            0,
            0,
        )
    try:
        plan = _plan(device_inputs, diagnostic)
    except Exception as error:
        failure = _failure("packed-chunk-planning", error)
        return _terminal(
            CudaSearchTerminalState.FAILED,
            counting_context,
            diagnostic,
            total,
            None,
            0,
            _clock_value(selected_clock, "clock.failed") - started,
            0,
            0,
            0,
            0,
            0,
            failure=failure,
            recovery_adapter=cpu_recovery_adapter,
        )

    filter_runner = CudaPackedExactFilterRunner()
    materializer = CudaPackedExactMaterializer()
    filter_batches: list[CudaPackedFilterBatch] = []
    evaluated = chunks = candidates = out_of_scope = hard = accepted = 0
    last_progress = started
    stage = "packed-filter"
    try:
        for start in range(0, total, plan.batch_size):
            stop = min(total, start + plan.batch_size)
            remaining = counting_context.result_cap + 1 - accepted
            batch = filter_runner.filter(
                device_inputs,
                diagnostic,
                start,
                stop,
                maximum_captured_matches=remaining,
            )
            evaluated = stop
            chunks += 1
            candidates += batch.exact_candidate_count
            out_of_scope += batch.out_of_scope_count
            hard += batch.hard_bound_rejected_count
            if batch.accepted_count >= remaining:
                detected = counting_context.result_cap + 1
                terminal_index = int(batch.accepted_flat_indices[remaining - 1])
                return _terminal(
                    CudaSearchTerminalState.OVERFLOWED,
                    counting_context,
                    diagnostic,
                    total,
                    plan,
                    evaluated,
                    _clock_value(selected_clock, "clock.overflowed") - started,
                    chunks,
                    candidates,
                    out_of_scope,
                    hard,
                    accepted + batch.accepted_count,
                    detected=detected,
                    terminal_flat_index=terminal_index,
                    overflowed=True,
                )
            accepted += batch.accepted_count
            if batch.accepted_count:
                if batch.captured_count != batch.accepted_count:
                    raise _error("partial-packed-mask", "packed_filter", "nonoverflow batches must capture every accepted row.")
                filter_batches.append(batch)
            observed = _clock_value(selected_clock, "clock.progress")
            final = stop == total
            if final or observed - last_progress >= 0.1:
                _emit_progress(
                    on_progress,
                    CudaSearchProgress(
                        counting_context.request_id,
                        total,
                        evaluated,
                        observed - started,
                        chunks,
                        0,
                        (candidates, 0, 0),
                        out_of_scope,
                        0,
                        hard,
                        (0, 0, 0),
                        (accepted, 0, 0),
                        plan.batch_size,
                        diagnostic,
                    ),
                )
                last_progress = observed
            if not final and _check_cancellation(should_cancel):
                return _terminal(
                    CudaSearchTerminalState.CANCELLED,
                    counting_context,
                    diagnostic,
                    total,
                    plan,
                    evaluated,
                    _clock_value(selected_clock, "clock.cancelled") - started,
                    chunks,
                    candidates,
                    out_of_scope,
                    hard,
                    accepted,
                )
        stage = "packed-materialization"
        batches = tuple(
            materializer.materialize(device_inputs, diagnostic, batch)
            for batch in filter_batches
        )
        return _terminal(
            CudaSearchTerminalState.COMPLETED,
            counting_context,
            diagnostic,
            total,
            plan,
            total,
            _clock_value(selected_clock, "clock.completed") - started,
            chunks,
            candidates,
            out_of_scope,
            hard,
            accepted,
            batches=batches,
        )
    except CudaSearchOrchestrationError:
        raise
    except Exception as error:
        return _terminal(
            CudaSearchTerminalState.FAILED,
            counting_context,
            diagnostic,
            total,
            plan,
            evaluated,
            _clock_value(selected_clock, "clock.failed") - started,
            chunks,
            candidates,
            out_of_scope,
            hard,
            accepted,
            failure=_failure(stage, error),
            recovery_adapter=cpu_recovery_adapter,
        )
    finally:
        filter_runner.close()
        materializer.close()


__all__ = [
    "CudaCancellationPredicate",
    "CudaClock",
    "CudaCpuRecoveryAdapter",
    "CudaCpuRecoveryOffer",
    "CudaCpuRecoveryRequest",
    "CudaCpuRecoveryResult",
    "CudaProgressCallback",
    "CudaSearchFailure",
    "CudaSearchOrchestrationError",
    "CudaSearchProgress",
    "CudaSearchResult",
    "CudaSearchTerminalState",
    "run_controlled_cuda_search",
]
