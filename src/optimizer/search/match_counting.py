"""Shared streaming match counting and cap+1 overflow detection."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from src.optimizer.domain import (
    FINAL_STAT_ORDER,
    MAX_RESULT_CAP,
    RESULT_CATEGORY_ORDER,
    ExecutionPreference,
    OptimizationRequest,
    ResultCategory,
    SearchSummary,
)
from src.optimizer.engine.derived_metrics import DERIVED_METRIC_IDS
from src.optimizer.search.exact_evaluation import (
    ExactBuildBatchResult,
    ExactBuildEvaluationContext,
    ExactBuildRow,
)


MATCH_ID_WIDTH = 6
OVERFLOW_GUIDANCE_CODE = "overflow.tighten_stat_requirements"
_PRIMARY_FILTER_IDS = tuple(stat.value for stat in FINAL_STAT_ORDER)
_DERIVED_FILTER_IDS = DERIVED_METRIC_IDS


class MatchCountingError(ValueError):
    """Actionable context, event, state, or exact-adapter failure."""

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code} at {path}: {message}")


def _error(code: str, path: str, message: str) -> MatchCountingError:
    return MatchCountingError(code, path, message)


def _integer(
    value: object,
    path: str,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _error("invalid-integer", path, "must be an integer; booleans are not accepted.")
    if value < minimum or (maximum is not None and value > maximum):
        upper = "" if maximum is None else f" and at most {maximum}"
        raise _error("integer-out-of-range", path, f"must be at least {minimum}{upper}; found {value}.")
    return value


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _error("invalid-stable-id", path, "must be a non-empty stable ID.")
    return value.strip()


def _sequence(value: object, path: str) -> tuple[object, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise _error("invalid-sequence", path, "must be a sequence.")
    try:
        return tuple(value)  # type: ignore[arg-type]
    except TypeError:
        raise _error("invalid-sequence", path, "must be a sequence.") from None


def _category(value: object, path: str) -> ResultCategory:
    if isinstance(value, bool):
        raise _error("invalid-result-category", path, "must be a canonical ResultCategory.")
    try:
        return value if isinstance(value, ResultCategory) else ResultCategory(value)
    except (TypeError, ValueError):
        raise _error("invalid-result-category", path, "must be exact, one-away, or two-away.") from None


def _category_counts(value: object, path: str) -> tuple[int, ...]:
    supplied = _sequence(value, path)
    if len(supplied) != len(RESULT_CATEGORY_ORDER):
        raise _error(
            "category-count-length",
            path,
            f"must contain {len(RESULT_CATEGORY_ORDER)} counts in canonical category order.",
        )
    return tuple(_integer(item, f"{path}[{index}]") for index, item in enumerate(supplied))


def _filter_ids(value: object, path: str) -> tuple[str, ...]:
    supplied = _sequence(value, path)
    result = tuple(_text(item, f"{path}[{index}]") for index, item in enumerate(supplied))
    if len(result) != len(set(result)):
        raise _error("duplicate-filter-id", path, "must contain unique stable filter IDs.")
    return result


def _validate_filter_partition(
    unrestricted: tuple[str, ...],
    bounded: tuple[str, ...],
    canonical: tuple[str, ...],
    path: str,
) -> None:
    if set(unrestricted) & set(bounded) or set(unrestricted) | set(bounded) != set(canonical):
        raise _error("filter-partition-mismatch", path, "must partition the complete canonical filter catalog.")
    if unrestricted != tuple(item for item in canonical if item in unrestricted):
        raise _error("noncanonical-filter-order", path, "unrestricted IDs must retain canonical order.")
    if bounded != tuple(item for item in canonical if item in bounded):
        raise _error("noncanonical-filter-order", path, "bounded IDs must retain canonical order.")


@dataclass(frozen=True, slots=True)
class OverflowGuidance:
    """Cold stable filter evidence for explaining an overflowed search."""

    unrestricted_primary_filter_ids: tuple[str, ...]
    bounded_primary_filter_ids: tuple[str, ...]
    unrestricted_derived_filter_ids: tuple[str, ...]
    bounded_derived_filter_ids: tuple[str, ...]
    code: str = OVERFLOW_GUIDANCE_CODE

    def __post_init__(self) -> None:
        code = _text(self.code, "OverflowGuidance.code")
        if code != OVERFLOW_GUIDANCE_CODE:
            raise _error(
                "invalid-guidance-code",
                "OverflowGuidance.code",
                f"must be {OVERFLOW_GUIDANCE_CODE!r}.",
            )
        unrestricted_primary = _filter_ids(
            self.unrestricted_primary_filter_ids,
            "OverflowGuidance.unrestricted_primary_filter_ids",
        )
        bounded_primary = _filter_ids(
            self.bounded_primary_filter_ids,
            "OverflowGuidance.bounded_primary_filter_ids",
        )
        unrestricted_derived = _filter_ids(
            self.unrestricted_derived_filter_ids,
            "OverflowGuidance.unrestricted_derived_filter_ids",
        )
        bounded_derived = _filter_ids(
            self.bounded_derived_filter_ids,
            "OverflowGuidance.bounded_derived_filter_ids",
        )
        _validate_filter_partition(
            unrestricted_primary,
            bounded_primary,
            _PRIMARY_FILTER_IDS,
            "OverflowGuidance.primary_filter_ids",
        )
        _validate_filter_partition(
            unrestricted_derived,
            bounded_derived,
            _DERIVED_FILTER_IDS,
            "OverflowGuidance.derived_filter_ids",
        )
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "unrestricted_primary_filter_ids", unrestricted_primary)
        object.__setattr__(self, "bounded_primary_filter_ids", bounded_primary)
        object.__setattr__(self, "unrestricted_derived_filter_ids", unrestricted_derived)
        object.__setattr__(self, "bounded_derived_filter_ids", bounded_derived)

    @property
    def recommended_filter_ids(self) -> tuple[str, ...]:
        unrestricted = (
            self.unrestricted_primary_filter_ids + self.unrestricted_derived_filter_ids
        )
        if unrestricted:
            return unrestricted
        return self.bounded_primary_filter_ids + self.bounded_derived_filter_ids


@dataclass(frozen=True, slots=True)
class MatchCountingContext:
    """Immutable request identity, cap, and precompiled overflow guidance."""

    request_id: str
    result_cap: int
    overflow_guidance: OverflowGuidance

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _text(self.request_id, "MatchCountingContext.request_id"))
        object.__setattr__(
            self,
            "result_cap",
            _integer(
                self.result_cap,
                "MatchCountingContext.result_cap",
                minimum=1,
                maximum=MAX_RESULT_CAP,
            ),
        )
        if not isinstance(self.overflow_guidance, OverflowGuidance):
            raise _error(
                "invalid-overflow-guidance",
                "MatchCountingContext.overflow_guidance",
                "must be OverflowGuidance.",
            )


@dataclass(frozen=True, slots=True)
class MatchEvent:
    """Category and compact permutation identity for one accepted match."""

    category: ResultCategory
    flat_index: int
    dense_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        category = _category(self.category, "MatchEvent.category")
        flat_index = _integer(self.flat_index, "MatchEvent.flat_index")
        supplied_ids = _sequence(self.dense_ids, "MatchEvent.dense_ids")
        if len(supplied_ids) != MATCH_ID_WIDTH:
            raise _error(
                "dense-id-length",
                "MatchEvent.dense_ids",
                f"must contain exactly {MATCH_ID_WIDTH} canonical slot IDs.",
            )
        dense_ids = tuple(
            _integer(value, f"MatchEvent.dense_ids[{index}]")
            for index, value in enumerate(supplied_ids)
        )
        if len(set(dense_ids)) != MATCH_ID_WIDTH:
            raise _error("duplicate-dense-id", "MatchEvent.dense_ids", "must contain six unique item IDs.")
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "flat_index", flat_index)
        object.__setattr__(self, "dense_ids", dense_ids)


@dataclass(frozen=True, slots=True)
class MatchCountingResult:
    """Terminal cap evidence; overflow counts include the cap+1 sentinel."""

    request_id: str
    result_cap: int
    detected_count: int
    category_counts: tuple[int, ...]
    overflowed: bool
    guidance: OverflowGuidance | None

    def __post_init__(self) -> None:
        request_id = _text(self.request_id, "MatchCountingResult.request_id")
        cap = _integer(
            self.result_cap,
            "MatchCountingResult.result_cap",
            minimum=1,
            maximum=MAX_RESULT_CAP,
        )
        detected = _integer(
            self.detected_count,
            "MatchCountingResult.detected_count",
            maximum=cap + 1,
        )
        counts = _category_counts(self.category_counts, "MatchCountingResult.category_counts")
        if sum(counts) != detected:
            raise _error(
                "match-count-mismatch",
                "MatchCountingResult.category_counts",
                "category counts must sum to detected_count.",
            )
        if not isinstance(self.overflowed, bool):
            raise _error("invalid-overflow-state", "MatchCountingResult.overflowed", "must be boolean.")
        if self.overflowed is not (detected == cap + 1):
            raise _error(
                "inconsistent-overflow-state",
                "MatchCountingResult",
                "overflowed must be true exactly when detected_count is cap + 1.",
            )
        if self.overflowed:
            if not isinstance(self.guidance, OverflowGuidance):
                raise _error(
                    "overflow-guidance-required",
                    "MatchCountingResult.guidance",
                    "overflowed results require stable tightening guidance.",
                )
        elif self.guidance is not None:
            raise _error(
                "unexpected-overflow-guidance",
                "MatchCountingResult.guidance",
                "completed counting results must not include overflow guidance.",
            )
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "result_cap", cap)
        object.__setattr__(self, "detected_count", detected)
        object.__setattr__(self, "category_counts", counts)

    @property
    def completed(self) -> bool:
        return not self.overflowed

    @property
    def retained_count(self) -> int:
        return 0 if self.overflowed else self.detected_count

    def count_for(self, category: ResultCategory) -> int:
        normalized = _category(category, "category")
        return self.category_counts[RESULT_CATEGORY_ORDER.index(normalized)]

    def to_search_summary(
        self,
        *,
        evaluated_permutations: int,
        duration_seconds: int | float,
        execution_preference: ExecutionPreference,
    ) -> SearchSummary:
        counts = (0, 0, 0) if self.overflowed else self.category_counts
        return SearchSummary(
            request_id=self.request_id,
            evaluated_permutations=evaluated_permutations,
            exact_count=counts[0],
            one_away_count=counts[1],
            two_away_count=counts[2],
            duration_seconds=duration_seconds,
            execution_preference=execution_preference,
            overflowed=self.overflowed,
        )


class CombinedMatchCounter:
    """O(1)-memory state machine shared by every result category."""

    __slots__ = (
        "_context",
        "_counts",
        "_detected_count",
        "_finished_result",
        "_last_flat_index",
        "_overflowed",
    )

    def __init__(self, context: MatchCountingContext) -> None:
        if not isinstance(context, MatchCountingContext):
            raise _error(
                "invalid-counting-context",
                "context",
                "must be a MatchCountingContext.",
            )
        self._context = context
        self._counts = [0] * len(RESULT_CATEGORY_ORDER)
        self._detected_count = 0
        self._finished_result: MatchCountingResult | None = None
        self._last_flat_index: int | None = None
        self._overflowed = False

    def accept(self, event: MatchEvent) -> bool:
        """Count one event and return false exactly for the cap+1 sentinel."""

        if self._finished_result is not None or self._overflowed:
            raise _error("counter-terminal", "counter", "cannot accept matches after terminal state.")
        if not isinstance(event, MatchEvent):
            raise _error("invalid-match-event", "event", "must be a MatchEvent.")
        if self._last_flat_index is not None and event.flat_index <= self._last_flat_index:
            raise _error(
                "noncanonical-match-order",
                "event.flat_index",
                "match flat indices must be strictly ascending.",
            )
        self._last_flat_index = event.flat_index
        category_index = RESULT_CATEGORY_ORDER.index(event.category)
        self._counts[category_index] += 1
        self._detected_count += 1
        self._overflowed = self._detected_count == self._context.result_cap + 1
        return not self._overflowed

    def finish(self) -> MatchCountingResult:
        if self._finished_result is None:
            self._finished_result = MatchCountingResult(
                request_id=self._context.request_id,
                result_cap=self._context.result_cap,
                detected_count=self._detected_count,
                category_counts=tuple(self._counts),
                overflowed=self._overflowed,
                guidance=self._context.overflow_guidance if self._overflowed else None,
            )
        return self._finished_result


@dataclass(frozen=True, slots=True)
class ExactMatchCollection:
    """Exact-batch accounting with rows exposed only for a completed count."""

    counting: MatchCountingResult
    evaluated_permutations: int
    exact_set_candidates: int
    hard_bound_rejected_count: int
    consumed_batch_count: int
    rows: tuple[ExactBuildRow, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.counting, MatchCountingResult):
            raise _error("invalid-counting-result", "ExactMatchCollection.counting", "must be MatchCountingResult.")
        evaluated = _integer(self.evaluated_permutations, "ExactMatchCollection.evaluated_permutations")
        exact = _integer(self.exact_set_candidates, "ExactMatchCollection.exact_set_candidates")
        rejected = _integer(
            self.hard_bound_rejected_count,
            "ExactMatchCollection.hard_bound_rejected_count",
        )
        consumed = _integer(self.consumed_batch_count, "ExactMatchCollection.consumed_batch_count")
        rows = _sequence(self.rows, "ExactMatchCollection.rows")
        if not all(isinstance(row, ExactBuildRow) for row in rows):
            raise _error("invalid-exact-rows", "ExactMatchCollection.rows", "must contain ExactBuildRow values.")
        if exact > evaluated or rejected > exact:
            raise _error(
                "inconsistent-exact-accounting",
                "ExactMatchCollection",
                "evaluated, exact-set, and rejected counts are inconsistent.",
            )
        if (consumed == 0) is not (evaluated == 0):
            raise _error(
                "inconsistent-batch-accounting",
                "ExactMatchCollection",
                "zero consumed batches must correspond exactly to zero evaluated permutations.",
            )
        exact_count = self.counting.count_for(ResultCategory.EXACT)
        if any(self.counting.count_for(category) for category in RESULT_CATEGORY_ORDER[1:]):
            raise _error(
                "near-category-in-exact-collection",
                "ExactMatchCollection.counting",
                "the exact adapter may count only exact-category rows.",
            )
        if self.counting.overflowed:
            if rows:
                raise _error(
                    "partial-overflow-rows",
                    "ExactMatchCollection.rows",
                    "overflowed collections must not expose partial rows.",
                )
            if exact < rejected + exact_count:
                raise _error(
                    "inconsistent-exact-accounting",
                    "ExactMatchCollection",
                    "consumed exact candidates must cover rejected rows and cap+1 evidence.",
                )
        else:
            if len(rows) != exact_count or exact != rejected + len(rows):
                raise _error(
                    "inconsistent-exact-accounting",
                    "ExactMatchCollection",
                    "completed exact candidates must partition into rejected and retained rows.",
                )
            flat_indices = tuple(row.flat_index for row in rows)
            if flat_indices != tuple(sorted(set(flat_indices))):
                raise _error(
                    "noncanonical-exact-row-order",
                    "ExactMatchCollection.rows",
                    "flat indices must be unique and ascending.",
                )
        object.__setattr__(self, "evaluated_permutations", evaluated)
        object.__setattr__(self, "exact_set_candidates", exact)
        object.__setattr__(self, "hard_bound_rejected_count", rejected)
        object.__setattr__(self, "consumed_batch_count", consumed)
        object.__setattr__(self, "rows", tuple(rows))


def compile_match_counting_context(request: OptimizationRequest) -> MatchCountingContext:
    """Compile one request's shared cap and deterministic tightening evidence."""

    if not isinstance(request, OptimizationRequest):
        raise _error("invalid-request", "request", "must be an OptimizationRequest.")
    primary_ranges = dict(request.stat_ranges)
    derived_ranges = dict(request.derived_metric_ranges)
    unknown_metrics = tuple(sorted(set(derived_ranges) - set(_DERIVED_FILTER_IDS)))
    if unknown_metrics:
        raise _error(
            "unknown-derived-metric",
            "request.derived_metric_ranges",
            "unsupported metric IDs: " + ", ".join(unknown_metrics) + ".",
        )

    bounded_primary = tuple(
        stat.value
        for stat in FINAL_STAT_ORDER
        if stat in primary_ranges
        and (
            primary_ranges[stat].minimum is not None
            or primary_ranges[stat].maximum is not None
        )
    )
    unrestricted_primary = tuple(item for item in _PRIMARY_FILTER_IDS if item not in bounded_primary)
    bounded_derived = tuple(
        metric_id
        for metric_id in _DERIVED_FILTER_IDS
        if metric_id in derived_ranges
        and (
            derived_ranges[metric_id].minimum is not None
            or derived_ranges[metric_id].maximum is not None
        )
    )
    unrestricted_derived = tuple(item for item in _DERIVED_FILTER_IDS if item not in bounded_derived)
    return MatchCountingContext(
        request_id=request.request_id,
        result_cap=request.result_cap,
        overflow_guidance=OverflowGuidance(
            unrestricted_primary_filter_ids=unrestricted_primary,
            bounded_primary_filter_ids=bounded_primary,
            unrestricted_derived_filter_ids=unrestricted_derived,
            bounded_derived_filter_ids=bounded_derived,
        ),
    )


def count_match_events(
    context: MatchCountingContext,
    events: Iterable[MatchEvent],
) -> MatchCountingResult:
    """Consume through cap+1 and never request another event after overflow."""

    if isinstance(events, (str, bytes, bytearray)):
        raise _error("invalid-match-events", "events", "must be an iterable of MatchEvent values.")
    try:
        iterator = iter(events)
    except TypeError:
        raise _error("invalid-match-events", "events", "must be an iterable of MatchEvent values.") from None
    counter = CombinedMatchCounter(context)
    for event in iterator:
        if not counter.accept(event):
            break
    return counter.finish()


def collect_exact_build_matches(
    counting_context: MatchCountingContext,
    evaluation_context: ExactBuildEvaluationContext,
    batches: Iterable[ExactBuildBatchResult],
) -> ExactMatchCollection:
    """Route retained exact rows through the shared counter without storing on overflow."""

    if not isinstance(counting_context, MatchCountingContext):
        raise _error("invalid-counting-context", "counting_context", "must be MatchCountingContext.")
    if not isinstance(evaluation_context, ExactBuildEvaluationContext):
        raise _error(
            "invalid-evaluation-context",
            "evaluation_context",
            "must be ExactBuildEvaluationContext.",
        )
    if counting_context.request_id != evaluation_context.request_id:
        raise _error(
            "request-context-mismatch",
            "evaluation_context.request_id",
            "must match counting_context.request_id.",
        )
    if isinstance(batches, (str, bytes, bytearray)):
        raise _error("invalid-exact-batches", "batches", "must be an iterable of exact batch results.")
    try:
        iterator = iter(batches)
    except TypeError:
        raise _error("invalid-exact-batches", "batches", "must be an iterable of exact batch results.") from None

    counter = CombinedMatchCounter(counting_context)
    retained: list[ExactBuildRow] = []
    evaluated = 0
    exact_candidates = 0
    rejected = 0
    consumed_batches = 0
    previous_stop: int | None = None
    for batch in iterator:
        if not isinstance(batch, ExactBuildBatchResult):
            raise _error("invalid-exact-batch", "batches", "must contain ExactBuildBatchResult values.")
        if previous_stop is not None and batch.start_index < previous_stop:
            raise _error(
                "overlapping-exact-batches",
                "batches",
                "batch ranges must be ascending and non-overlapping.",
            )
        previous_stop = batch.stop_index
        consumed_batches += 1
        evaluated += batch.evaluated_count
        exact_candidates += batch.exact_set_count
        rejected += batch.hard_bound_rejected_count
        for row in batch.rows:
            event = MatchEvent(ResultCategory.EXACT, row.flat_index, row.dense_ids)
            if not counter.accept(event):
                counting = counter.finish()
                return ExactMatchCollection(
                    counting=counting,
                    evaluated_permutations=evaluated,
                    exact_set_candidates=exact_candidates,
                    hard_bound_rejected_count=rejected,
                    consumed_batch_count=consumed_batches,
                    rows=(),
                )
            retained.append(row)

    return ExactMatchCollection(
        counting=counter.finish(),
        evaluated_permutations=evaluated,
        exact_set_candidates=exact_candidates,
        hard_bound_rejected_count=rejected,
        consumed_batch_count=consumed_batches,
        rows=tuple(retained),
    )


__all__ = [
    "MATCH_ID_WIDTH",
    "OVERFLOW_GUIDANCE_CODE",
    "CombinedMatchCounter",
    "ExactMatchCollection",
    "MatchCountingContext",
    "MatchCountingError",
    "MatchCountingResult",
    "MatchEvent",
    "OverflowGuidance",
    "collect_exact_build_matches",
    "compile_match_counting_context",
    "count_match_events",
]
