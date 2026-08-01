"""Lazy mixed-radix enumeration of six prepared optimizer slot arrays."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from numbers import Real

from src.optimizer.domain.catalog import GEAR_SLOT_ORDER
from src.optimizer.search.slot_arrays import SearchReadySlotArrays


CARTESIAN_SLOT_COUNT = 6
Clock = Callable[[], float]


class CartesianEnumerationError(ValueError):
    """Actionable search-space, index, batch, clock, or summary failure."""

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code} at {path}: {message}")


def _error(code: str, path: str, message: str) -> CartesianEnumerationError:
    return CartesianEnumerationError(code, path, message)


def _integer(value: object, path: str, *, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _error("invalid-integer", path, "must be an integer; boolean values are not accepted.")
    if value < minimum:
        raise _error("integer-out-of-range", path, f"must be at least {minimum}; found {value}.")
    if maximum is not None and value > maximum:
        raise _error("integer-out-of-range", path, f"must be at most {maximum}; found {value}.")
    return value


def _integer_tuple(value: object, path: str) -> tuple[int, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise _error("invalid-integer-vector", path, "must be a sequence of integers.")
    try:
        values = tuple(value)  # type: ignore[arg-type]
    except TypeError:
        raise _error("invalid-integer-vector", path, "must be a sequence of integers.") from None
    if any(isinstance(item, bool) or not isinstance(item, int) for item in values):
        raise _error(
            "invalid-integer-vector",
            path,
            "must contain integers; boolean values are not accepted.",
        )
    return values


def _clock_value(clock: Clock, path: str) -> float:
    try:
        value = clock()
    except Exception as error:
        raise _error("clock-failed", path, f"monotonic clock failed: {error}") from error
    if isinstance(value, bool) or not isinstance(value, Real):
        raise _error("invalid-clock-value", path, "monotonic clock must return a finite number.")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise _error("invalid-clock-value", path, "monotonic clock must return a finite number.")
    return numeric


@dataclass(frozen=True, slots=True)
class CartesianSearchSpace:
    """Six canonical slot radices and their exact Cartesian product."""

    radices: tuple[int, ...]
    total_permutations: int

    def __post_init__(self) -> None:
        radices = _integer_tuple(self.radices, "CartesianSearchSpace.radices")
        if len(radices) != CARTESIAN_SLOT_COUNT:
            raise _error(
                "cartesian-slot-count",
                "CartesianSearchSpace.radices",
                f"must contain exactly {CARTESIAN_SLOT_COUNT} canonical slot lengths.",
            )
        if any(radix <= 0 for radix in radices):
            raise _error(
                "empty-cartesian-slot",
                "CartesianSearchSpace.radices",
                "every canonical slot length must be positive.",
            )
        total = _integer(
            self.total_permutations,
            "CartesianSearchSpace.total_permutations",
            minimum=1,
        )
        expected_total = math.prod(radices)
        if total != expected_total:
            raise _error(
                "cartesian-total-mismatch",
                "CartesianSearchSpace.total_permutations",
                f"must equal the exact radix product {expected_total}; found {total}.",
            )
        object.__setattr__(self, "radices", radices)
        object.__setattr__(self, "total_permutations", total)


def create_cartesian_search_space(slot_arrays: SearchReadySlotArrays) -> CartesianSearchSpace:
    """Retain only canonical slot lengths from validated prepared arrays."""

    if not isinstance(slot_arrays, SearchReadySlotArrays):
        raise _error(
            "invalid-search-arrays",
            "slot_arrays",
            "must be a validated SearchReadySlotArrays instance.",
        )
    if len(GEAR_SLOT_ORDER) != CARTESIAN_SLOT_COUNT or tuple(
        item.slot for item in slot_arrays.slots
    ) != GEAR_SLOT_ORDER:
        raise _error(
            "noncanonical-search-slots",
            "slot_arrays.slots",
            "must use canonical Weapon/Helmet/Armor/Necklace/Ring/Boots order.",
        )
    radices = tuple(len(item.dense_ids) for item in slot_arrays.slots)
    return CartesianSearchSpace(radices=radices, total_permutations=math.prod(radices))


def _require_space(value: object, path: str = "search_space") -> CartesianSearchSpace:
    if not isinstance(value, CartesianSearchSpace):
        raise _error(
            "invalid-search-space",
            path,
            "must be a validated CartesianSearchSpace instance.",
        )
    return value


def _decode_unchecked(search_space: CartesianSearchSpace, flat_index: int) -> tuple[int, ...]:
    remaining = flat_index
    offsets = [0] * CARTESIAN_SLOT_COUNT
    for slot_index in range(CARTESIAN_SLOT_COUNT - 1, -1, -1):
        remaining, offsets[slot_index] = divmod(remaining, search_space.radices[slot_index])
    return tuple(offsets)


def flat_index_to_slot_offsets(
    search_space: CartesianSearchSpace,
    flat_index: int,
) -> tuple[int, ...]:
    """Decode one flat index with the rightmost Boots radix changing fastest."""

    checked_space = _require_space(search_space)
    checked_index = _integer(
        flat_index,
        "flat_index",
        minimum=0,
        maximum=checked_space.total_permutations - 1,
    )
    return _decode_unchecked(checked_space, checked_index)


def slot_offsets_to_flat_index(
    search_space: CartesianSearchSpace,
    slot_offsets: tuple[int, ...],
) -> int:
    """Encode six canonical per-slot offsets into their unique flat index."""

    checked_space = _require_space(search_space)
    offsets = _integer_tuple(slot_offsets, "slot_offsets")
    if len(offsets) != CARTESIAN_SLOT_COUNT:
        raise _error(
            "slot-offset-count",
            "slot_offsets",
            f"must contain exactly {CARTESIAN_SLOT_COUNT} offsets.",
        )
    for slot_index, (offset, radix) in enumerate(zip(offsets, checked_space.radices, strict=True)):
        if offset < 0 or offset >= radix:
            raise _error(
                "slot-offset-out-of-range",
                f"slot_offsets[{slot_index}]",
                f"must be between 0 and {radix - 1}; found {offset}.",
            )
    flat_index = 0
    for offset, radix in zip(offsets, checked_space.radices, strict=True):
        flat_index = flat_index * radix + offset
    return flat_index


@dataclass(frozen=True, slots=True)
class CartesianBatch:
    """One nonempty immutable half-open range of canonical offset rows."""

    search_space: CartesianSearchSpace
    start_index: int
    stop_index: int
    slot_offsets: tuple[tuple[int, ...], ...]

    def __post_init__(self) -> None:
        search_space = _require_space(self.search_space, "CartesianBatch.search_space")
        start = _integer(
            self.start_index,
            "CartesianBatch.start_index",
            minimum=0,
            maximum=search_space.total_permutations - 1,
        )
        stop = _integer(
            self.stop_index,
            "CartesianBatch.stop_index",
            minimum=1,
            maximum=search_space.total_permutations,
        )
        if stop <= start:
            raise _error(
                "invalid-batch-range",
                "CartesianBatch.stop_index",
                "must be greater than start_index for a nonempty half-open batch.",
            )
        if isinstance(self.slot_offsets, (str, bytes, bytearray)):
            raise _error(
                "invalid-batch-rows",
                "CartesianBatch.slot_offsets",
                "must be a sequence of six-offset rows.",
            )
        try:
            rows = tuple(
                _integer_tuple(row, f"CartesianBatch.slot_offsets[{row_index}]")
                for row_index, row in enumerate(self.slot_offsets)
            )
        except TypeError:
            raise _error(
                "invalid-batch-rows",
                "CartesianBatch.slot_offsets",
                "must be a sequence of six-offset rows.",
            ) from None
        if len(rows) != stop - start:
            raise _error(
                "batch-row-count-mismatch",
                "CartesianBatch.slot_offsets",
                f"must contain exactly {stop - start} rows for [{start}, {stop}).",
            )
        for row_index, row in enumerate(rows):
            if len(row) != CARTESIAN_SLOT_COUNT:
                raise _error(
                    "batch-row-width",
                    f"CartesianBatch.slot_offsets[{row_index}]",
                    f"must contain exactly {CARTESIAN_SLOT_COUNT} offsets.",
                )
            for slot_index, (offset, radix) in enumerate(
                zip(row, search_space.radices, strict=True)
            ):
                if offset < 0 or offset >= radix:
                    raise _error(
                        "batch-slot-offset-out-of-range",
                        f"CartesianBatch.slot_offsets[{row_index}][{slot_index}]",
                        f"must be between 0 and {radix - 1}; found {offset}.",
                    )
            flat_index = start + row_index
            expected = _decode_unchecked(search_space, flat_index)
            if row != expected:
                raise _error(
                    "noncanonical-batch-row",
                    f"CartesianBatch.slot_offsets[{row_index}]",
                    f"must decode flat index {flat_index} as {expected!r}; found {row!r}.",
                )
        object.__setattr__(self, "search_space", search_space)
        object.__setattr__(self, "start_index", start)
        object.__setattr__(self, "stop_index", stop)
        object.__setattr__(self, "slot_offsets", rows)

    @property
    def count(self) -> int:
        return self.stop_index - self.start_index


@dataclass(frozen=True, slots=True)
class CartesianEnumerationSummary:
    """Immutable progress evidence for one full or partial enumeration."""

    total_permutations: int
    searched_count: int
    elapsed_seconds: float
    completed: bool
    cancelled: bool

    def __post_init__(self) -> None:
        total = _integer(
            self.total_permutations,
            "CartesianEnumerationSummary.total_permutations",
            minimum=1,
        )
        searched = _integer(
            self.searched_count,
            "CartesianEnumerationSummary.searched_count",
            minimum=0,
            maximum=total,
        )
        if isinstance(self.elapsed_seconds, bool) or not isinstance(self.elapsed_seconds, Real):
            raise _error(
                "invalid-elapsed-time",
                "CartesianEnumerationSummary.elapsed_seconds",
                "must be a finite nonnegative number.",
            )
        elapsed = float(self.elapsed_seconds)
        if not math.isfinite(elapsed) or elapsed < 0:
            raise _error(
                "invalid-elapsed-time",
                "CartesianEnumerationSummary.elapsed_seconds",
                "must be a finite nonnegative number.",
            )
        if not isinstance(self.completed, bool) or not isinstance(self.cancelled, bool):
            raise _error(
                "invalid-enumeration-state",
                "CartesianEnumerationSummary",
                "completed and cancelled must be boolean.",
            )
        if self.completed and (self.cancelled or searched != total):
            raise _error(
                "inconsistent-enumeration-state",
                "CartesianEnumerationSummary.completed",
                "completed requires every permutation searched and cancelled=false.",
            )
        if not self.completed and not self.cancelled and searched == total:
            raise _error(
                "inconsistent-enumeration-state",
                "CartesianEnumerationSummary.completed",
                "a non-cancelled fully searched run must be completed.",
            )
        object.__setattr__(self, "total_permutations", total)
        object.__setattr__(self, "searched_count", searched)
        object.__setattr__(self, "elapsed_seconds", elapsed)


class BatchedCartesianEnumerator(Iterator[CartesianBatch]):
    """Stateful lazy iterator with bounded rows and immutable progress snapshots."""

    __slots__ = (
        "_batch_size",
        "_clock",
        "_finished_at",
        "_next_index",
        "_range_start",
        "_range_stop",
        "_search_space",
        "_started_at",
    )

    def __init__(
        self,
        search_space: CartesianSearchSpace,
        batch_size: int,
        *,
        start_index: int = 0,
        stop_index: int | None = None,
        clock: Clock = time.perf_counter,
    ) -> None:
        self._search_space = _require_space(search_space)
        self._batch_size = _integer(batch_size, "batch_size", minimum=1)
        self._range_start = _integer(
            start_index,
            "start_index",
            minimum=0,
            maximum=self._search_space.total_permutations,
        )
        selected_stop = self._search_space.total_permutations if stop_index is None else stop_index
        self._range_stop = _integer(
            selected_stop,
            "stop_index",
            minimum=0,
            maximum=self._search_space.total_permutations,
        )
        if self._range_stop < self._range_start:
            raise _error(
                "invalid-enumeration-range",
                "stop_index",
                "must be greater than or equal to start_index.",
            )
        if not callable(clock):
            raise _error("invalid-clock", "clock", "must be a callable monotonic clock.")
        self._clock = clock
        self._started_at = _clock_value(self._clock, "clock.start")
        self._next_index = self._range_start
        self._finished_at: float | None = (
            self._started_at if self._range_start == self._range_stop else None
        )

    @property
    def search_space(self) -> CartesianSearchSpace:
        return self._search_space

    @property
    def batch_size(self) -> int:
        return self._batch_size

    @property
    def range_start(self) -> int:
        return self._range_start

    @property
    def range_stop(self) -> int:
        return self._range_stop

    @property
    def searched_count(self) -> int:
        return self._next_index

    def __iter__(self) -> BatchedCartesianEnumerator:
        return self

    def __next__(self) -> CartesianBatch:
        if self._next_index >= self._range_stop:
            raise StopIteration
        start = self._next_index
        stop = min(start + self._batch_size, self._range_stop)
        rows = tuple(
            _decode_unchecked(self._search_space, flat_index)
            for flat_index in range(start, stop)
        )
        batch = CartesianBatch(
            search_space=self._search_space,
            start_index=start,
            stop_index=stop,
            slot_offsets=rows,
        )
        finished_at = (
            _clock_value(self._clock, "clock.finish") if stop == self._range_stop else None
        )
        self._next_index = stop
        if finished_at is not None:
            if finished_at < self._started_at:
                raise _error(
                    "nonmonotonic-clock",
                    "clock.finish",
                    "must not be earlier than the enumeration start time.",
                )
            self._finished_at = finished_at
        return batch

    def snapshot(self, *, cancelled: bool = False) -> CartesianEnumerationSummary:
        """Report current state; cancellation is supplied by later orchestration."""

        if not isinstance(cancelled, bool):
            raise _error("invalid-cancelled-state", "cancelled", "must be boolean.")
        observed_at = (
            self._finished_at
            if self._finished_at is not None
            else _clock_value(self._clock, "clock.snapshot")
        )
        if observed_at < self._started_at:
            raise _error(
                "nonmonotonic-clock",
                "clock.snapshot",
                "must not be earlier than the enumeration start time.",
            )
        completed = self._next_index == self._search_space.total_permutations and not cancelled
        return CartesianEnumerationSummary(
            total_permutations=self._search_space.total_permutations,
            searched_count=self._next_index,
            elapsed_seconds=observed_at - self._started_at,
            completed=completed,
            cancelled=cancelled,
        )

    @property
    def summary(self) -> CartesianEnumerationSummary:
        return self.snapshot()


def iter_cartesian_batches(
    search_space: CartesianSearchSpace,
    batch_size: int,
    *,
    start_index: int = 0,
    stop_index: int | None = None,
    clock: Clock = time.perf_counter,
) -> BatchedCartesianEnumerator:
    """Create a validated lazy batch iterator without consuming any rows."""

    return BatchedCartesianEnumerator(
        search_space,
        batch_size,
        start_index=start_index,
        stop_index=stop_index,
        clock=clock,
    )


__all__ = [
    "CARTESIAN_SLOT_COUNT",
    "BatchedCartesianEnumerator",
    "CartesianBatch",
    "CartesianEnumerationError",
    "CartesianEnumerationSummary",
    "CartesianSearchSpace",
    "create_cartesian_search_space",
    "flat_index_to_slot_offsets",
    "iter_cartesian_batches",
    "slot_offsets_to_flat_index",
]
