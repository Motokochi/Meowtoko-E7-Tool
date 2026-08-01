"""Synchronous CPU orchestration for the currently implemented exact-set path."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from numbers import Real

from src.optimizer.domain import ExecutionPreference, ResultCategory, SearchSummary
from src.optimizer.search.cartesian import (
    Clock,
    CartesianEnumerationSummary,
    create_cartesian_search_space,
    iter_cartesian_batches,
)
from src.optimizer.search.exact_evaluation import (
    ExactBuildEvaluationContext,
    ExactBuildRow,
    evaluate_exact_build_batch,
    validate_exact_build_search_context,
)
from src.optimizer.search.match_counting import (
    CombinedMatchCounter,
    MatchCountingContext,
    MatchCountingResult,
    MatchEvent,
)
from src.optimizer.search.slot_arrays import SearchReadySlotArrays


CancellationPredicate = Callable[[], bool]
ProgressCallback = Callable[["CpuExactSearchProgress"], None]


class CpuSearchOrchestrationError(ValueError):
    """Actionable coordinator argument, callback, predicate, or result failure."""

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code} at {path}: {message}")


def _error(code: str, path: str, message: str) -> CpuSearchOrchestrationError:
    return CpuSearchOrchestrationError(code, path, message)


def _integer(
    value: object,
    path: str,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _error("invalid-integer", path, "must be an integer; boolean values are not accepted.")
    if value < minimum or (maximum is not None and value > maximum):
        upper = "" if maximum is None else f" and at most {maximum}"
        raise _error("integer-out-of-range", path, f"must be at least {minimum}{upper}; found {value}.")
    return value


def _number(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise _error("invalid-number", path, "must be a finite nonnegative number.")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        raise _error("invalid-number", path, "must be a finite nonnegative number.")
    return numeric


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _error("invalid-stable-id", path, "must be a non-empty stable ID.")
    return value.strip()


def _terminal_state(value: object, path: str) -> "CpuSearchTerminalState":
    if isinstance(value, bool):
        raise _error("invalid-terminal-state", path, "must be completed, overflowed, or cancelled.")
    try:
        return value if isinstance(value, CpuSearchTerminalState) else CpuSearchTerminalState(value)
    except (TypeError, ValueError):
        raise _error("invalid-terminal-state", path, "must be completed, overflowed, or cancelled.") from None


def _rows(value: object, path: str) -> tuple[ExactBuildRow, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise _error("invalid-exact-rows", path, "must be a sequence of ExactBuildRow values.")
    try:
        rows = tuple(value)  # type: ignore[arg-type]
    except TypeError:
        raise _error("invalid-exact-rows", path, "must be a sequence of ExactBuildRow values.") from None
    if not all(isinstance(row, ExactBuildRow) for row in rows):
        raise _error("invalid-exact-rows", path, "must contain only ExactBuildRow values.")
    return rows


class CpuSearchTerminalState(StrEnum):
    """Mutually exclusive outcomes of one synchronous CPU search."""

    COMPLETED = "completed"
    OVERFLOWED = "overflowed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class CpuExactSearchProgress:
    """Immutable cumulative evidence emitted after one fully processed batch."""

    request_id: str
    total_permutations: int
    searched_permutations: int
    elapsed_seconds: float
    completed_batch_count: int
    exact_set_candidates: int
    hard_bound_rejected_count: int
    emitted_match_count: int

    def __post_init__(self) -> None:
        request_id = _text(self.request_id, "CpuExactSearchProgress.request_id")
        total = _integer(
            self.total_permutations,
            "CpuExactSearchProgress.total_permutations",
            minimum=1,
        )
        searched = _integer(
            self.searched_permutations,
            "CpuExactSearchProgress.searched_permutations",
            minimum=1,
            maximum=total,
        )
        elapsed = _number(self.elapsed_seconds, "CpuExactSearchProgress.elapsed_seconds")
        batches = _integer(
            self.completed_batch_count,
            "CpuExactSearchProgress.completed_batch_count",
            minimum=1,
        )
        exact = _integer(
            self.exact_set_candidates,
            "CpuExactSearchProgress.exact_set_candidates",
            maximum=searched,
        )
        rejected = _integer(
            self.hard_bound_rejected_count,
            "CpuExactSearchProgress.hard_bound_rejected_count",
            maximum=exact,
        )
        emitted = _integer(
            self.emitted_match_count,
            "CpuExactSearchProgress.emitted_match_count",
            maximum=exact,
        )
        if exact != rejected + emitted:
            raise _error(
                "inconsistent-progress-accounting",
                "CpuExactSearchProgress",
                "exact candidates must partition into rejected and emitted matches.",
            )
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "total_permutations", total)
        object.__setattr__(self, "searched_permutations", searched)
        object.__setattr__(self, "elapsed_seconds", elapsed)
        object.__setattr__(self, "completed_batch_count", batches)
        object.__setattr__(self, "exact_set_candidates", exact)
        object.__setattr__(self, "hard_bound_rejected_count", rejected)
        object.__setattr__(self, "emitted_match_count", emitted)


@dataclass(frozen=True, slots=True)
class CpuExactSearchResult:
    """Terminal CPU evidence; only completed results may expose exact rows."""

    state: CpuSearchTerminalState
    request_id: str
    total_permutations: int
    searched_permutations: int
    elapsed_seconds: float
    completed_batch_count: int
    exact_set_candidates: int
    hard_bound_rejected_count: int
    counting: MatchCountingResult
    rows: tuple[ExactBuildRow, ...]

    def __post_init__(self) -> None:
        state = _terminal_state(self.state, "CpuExactSearchResult.state")
        request_id = _text(self.request_id, "CpuExactSearchResult.request_id")
        total = _integer(
            self.total_permutations,
            "CpuExactSearchResult.total_permutations",
            minimum=1,
        )
        searched = _integer(
            self.searched_permutations,
            "CpuExactSearchResult.searched_permutations",
            maximum=total,
        )
        elapsed = _number(self.elapsed_seconds, "CpuExactSearchResult.elapsed_seconds")
        batches = _integer(
            self.completed_batch_count,
            "CpuExactSearchResult.completed_batch_count",
        )
        exact = _integer(
            self.exact_set_candidates,
            "CpuExactSearchResult.exact_set_candidates",
            maximum=searched,
        )
        rejected = _integer(
            self.hard_bound_rejected_count,
            "CpuExactSearchResult.hard_bound_rejected_count",
            maximum=exact,
        )
        if not isinstance(self.counting, MatchCountingResult):
            raise _error(
                "invalid-counting-result",
                "CpuExactSearchResult.counting",
                "must be a MatchCountingResult.",
            )
        if self.counting.request_id != request_id:
            raise _error(
                "request-context-mismatch",
                "CpuExactSearchResult.counting.request_id",
                "must match request_id.",
            )
        rows = _rows(self.rows, "CpuExactSearchResult.rows")
        if (batches == 0) is not (searched == 0):
            raise _error(
                "inconsistent-batch-accounting",
                "CpuExactSearchResult",
                "zero completed batches must correspond exactly to zero searched permutations.",
            )
        if any(
            self.counting.count_for(category)
            for category in (ResultCategory.ONE_AWAY, ResultCategory.TWO_AWAY)
        ):
            raise _error(
                "near-category-in-exact-result",
                "CpuExactSearchResult.counting",
                "the exact coordinator may count only exact-category rows.",
            )
        detected = self.counting.count_for(ResultCategory.EXACT)
        if state is CpuSearchTerminalState.COMPLETED:
            if searched != total or self.counting.overflowed:
                raise _error(
                    "inconsistent-completed-state",
                    "CpuExactSearchResult",
                    "completed requires every permutation searched without overflow.",
                )
            if len(rows) != detected or exact != rejected + len(rows):
                raise _error(
                    "inconsistent-exact-accounting",
                    "CpuExactSearchResult",
                    "completed exact candidates must partition into rejected and returned rows.",
                )
            flat_indices = tuple(row.flat_index for row in rows)
            if flat_indices != tuple(sorted(set(flat_indices))):
                raise _error(
                    "noncanonical-exact-row-order",
                    "CpuExactSearchResult.rows",
                    "flat indices must be unique and ascending.",
                )
        elif state is CpuSearchTerminalState.OVERFLOWED:
            if not self.counting.overflowed or rows:
                raise _error(
                    "inconsistent-overflow-state",
                    "CpuExactSearchResult",
                    "overflowed requires cap+1 evidence and no exposed rows.",
                )
            if exact < rejected + detected:
                raise _error(
                    "inconsistent-exact-accounting",
                    "CpuExactSearchResult",
                    "evaluated exact candidates must cover rejected rows and cap+1 evidence.",
                )
        else:
            if searched >= total or self.counting.overflowed or rows:
                raise _error(
                    "inconsistent-cancelled-state",
                    "CpuExactSearchResult",
                    "cancelled requires a partial nonoverflow search with no exposed rows.",
                )
            if exact != rejected + detected:
                raise _error(
                    "inconsistent-exact-accounting",
                    "CpuExactSearchResult",
                    "cancelled exact candidates must partition into rejected and detected matches.",
                )
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "total_permutations", total)
        object.__setattr__(self, "searched_permutations", searched)
        object.__setattr__(self, "elapsed_seconds", elapsed)
        object.__setattr__(self, "completed_batch_count", batches)
        object.__setattr__(self, "exact_set_candidates", exact)
        object.__setattr__(self, "hard_bound_rejected_count", rejected)
        object.__setattr__(self, "rows", rows)

    @property
    def completed(self) -> bool:
        return self.state is CpuSearchTerminalState.COMPLETED

    @property
    def overflowed(self) -> bool:
        return self.state is CpuSearchTerminalState.OVERFLOWED

    @property
    def cancelled(self) -> bool:
        return self.state is CpuSearchTerminalState.CANCELLED

    def to_search_summary(self) -> SearchSummary:
        """Map terminal evidence to the shared completed/aborted summary contract."""

        if self.cancelled:
            return SearchSummary(
                request_id=self.request_id,
                evaluated_permutations=self.searched_permutations,
                exact_count=0,
                one_away_count=0,
                two_away_count=0,
                duration_seconds=self.elapsed_seconds,
                execution_preference=ExecutionPreference.CPU,
                cancelled=True,
            )
        return self.counting.to_search_summary(
            evaluated_permutations=self.searched_permutations,
            duration_seconds=self.elapsed_seconds,
            execution_preference=ExecutionPreference.CPU,
        )


def _check_cancellation(predicate: CancellationPredicate) -> bool:
    try:
        cancelled = predicate()
    except Exception as error:
        raise _error(
            "cancellation-predicate-failed",
            "should_cancel",
            f"cancellation predicate failed: {error}",
        ) from error
    if not isinstance(cancelled, bool):
        raise _error(
            "invalid-cancellation-result",
            "should_cancel",
            "cancellation predicate must return a boolean.",
        )
    return cancelled


def _emit_progress(callback: ProgressCallback | None, progress: CpuExactSearchProgress) -> None:
    if callback is None:
        return
    try:
        callback(progress)
    except Exception as error:
        raise _error(
            "progress-callback-failed",
            "on_progress",
            f"progress callback failed: {error}",
        ) from error


def _terminal_result(
    state: CpuSearchTerminalState,
    request_id: str,
    summary: CartesianEnumerationSummary,
    completed_batch_count: int,
    exact_set_candidates: int,
    hard_bound_rejected_count: int,
    counting: MatchCountingResult,
    rows: tuple[ExactBuildRow, ...] = (),
) -> CpuExactSearchResult:
    return CpuExactSearchResult(
        state=state,
        request_id=request_id,
        total_permutations=summary.total_permutations,
        searched_permutations=summary.searched_count,
        elapsed_seconds=summary.elapsed_seconds,
        completed_batch_count=completed_batch_count,
        exact_set_candidates=exact_set_candidates,
        hard_bound_rejected_count=hard_bound_rejected_count,
        counting=counting,
        rows=rows,
    )


def run_exact_cpu_search(
    slot_arrays: SearchReadySlotArrays,
    evaluation_context: ExactBuildEvaluationContext,
    counting_context: MatchCountingContext,
    *,
    batch_size: int,
    should_cancel: CancellationPredicate,
    on_progress: ProgressCallback | None = None,
    clock: Clock | None = None,
) -> CpuExactSearchResult:
    """Run exact evaluation synchronously with cancellation between bounded batches."""

    checked_batch_size = _integer(batch_size, "batch_size", minimum=1)
    if not callable(should_cancel):
        raise _error("invalid-cancellation-predicate", "should_cancel", "must be callable.")
    if on_progress is not None and not callable(on_progress):
        raise _error("invalid-progress-callback", "on_progress", "must be callable or None.")
    if clock is not None and not callable(clock):
        raise _error("invalid-clock", "clock", "must be callable or None.")
    radices = validate_exact_build_search_context(evaluation_context, slot_arrays)
    if not isinstance(counting_context, MatchCountingContext):
        raise _error(
            "invalid-counting-context",
            "counting_context",
            "must be a MatchCountingContext.",
        )
    if counting_context.request_id != evaluation_context.request_id:
        raise _error(
            "request-context-mismatch",
            "counting_context.request_id",
            "must match evaluation_context.request_id.",
        )
    search_space = create_cartesian_search_space(slot_arrays)
    if search_space.radices != radices:
        raise _error(
            "cartesian-radix-mismatch",
            "slot_arrays.slots",
            "prepared slot lengths changed while constructing the search space.",
        )
    enumerator = (
        iter_cartesian_batches(search_space, checked_batch_size)
        if clock is None
        else iter_cartesian_batches(search_space, checked_batch_size, clock=clock)
    )
    counter = CombinedMatchCounter(counting_context)
    retained: list[ExactBuildRow] = []
    exact_candidates = 0
    rejected = 0
    completed_batches = 0

    if _check_cancellation(should_cancel):
        return _terminal_result(
            CpuSearchTerminalState.CANCELLED,
            evaluation_context.request_id,
            enumerator.snapshot(cancelled=True),
            completed_batches,
            exact_candidates,
            rejected,
            counter.finish(),
        )

    for batch in enumerator:
        evaluated = evaluate_exact_build_batch(evaluation_context, slot_arrays, batch)
        completed_batches += 1
        exact_candidates += evaluated.exact_set_count
        rejected += evaluated.hard_bound_rejected_count
        for row in evaluated.rows:
            if not counter.accept(MatchEvent(ResultCategory.EXACT, row.flat_index, row.dense_ids)):
                return _terminal_result(
                    CpuSearchTerminalState.OVERFLOWED,
                    evaluation_context.request_id,
                    enumerator.snapshot(),
                    completed_batches,
                    exact_candidates,
                    rejected,
                    counter.finish(),
                )
            retained.append(row)

        boundary = enumerator.snapshot()
        _emit_progress(
            on_progress,
            CpuExactSearchProgress(
                request_id=evaluation_context.request_id,
                total_permutations=boundary.total_permutations,
                searched_permutations=boundary.searched_count,
                elapsed_seconds=boundary.elapsed_seconds,
                completed_batch_count=completed_batches,
                exact_set_candidates=exact_candidates,
                hard_bound_rejected_count=rejected,
                emitted_match_count=len(retained),
            ),
        )
        if boundary.completed:
            return _terminal_result(
                CpuSearchTerminalState.COMPLETED,
                evaluation_context.request_id,
                boundary,
                completed_batches,
                exact_candidates,
                rejected,
                counter.finish(),
                tuple(retained),
            )
        if _check_cancellation(should_cancel):
            return _terminal_result(
                CpuSearchTerminalState.CANCELLED,
                evaluation_context.request_id,
                enumerator.snapshot(cancelled=True),
                completed_batches,
                exact_candidates,
                rejected,
                counter.finish(),
            )

    raise _error(
        "unexpected-enumerator-termination",
        "enumerator",
        "the full nonempty Cartesian search ended without a terminal result.",
    )


__all__ = [
    "CancellationPredicate",
    "CpuExactSearchProgress",
    "CpuExactSearchResult",
    "CpuSearchOrchestrationError",
    "CpuSearchTerminalState",
    "ProgressCallback",
    "run_exact_cpu_search",
]
