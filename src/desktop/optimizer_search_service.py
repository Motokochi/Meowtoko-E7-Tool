"""Private inventory-to-result orchestration for one desktop optimizer search."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.desktop.optimizer_profile_service import (
    OptimizerProfileService,
    OptimizerProfileServiceError,
)
from src.optimizer.cuda.runtime import (
    CudaDiagnosticStatus,
    CudaRuntimeDiagnostic,
    cuda_disabled_from_environment,
    diagnose_cuda_runtime,
)
from src.optimizer.data import InventoryRepository
from src.optimizer.domain import ExecutionPreference, OptimizationRequest
from src.optimizer.result_store import (
    DenseItemEquippedLookup,
    ResultLifecycleManager,
    ResultLifecycleRequest,
    ResultRunStore,
    result_columns_from_cpu_rows,
    result_columns_from_cuda_batch,
)
from src.optimizer.search import (
    CpuExactSearchProgress,
    ExactBuildEvaluationContext,
    MatchCountingContext,
    SearchReadySlotArrays,
    compile_exact_build_context,
    compile_match_counting_context,
    compile_set_pattern,
    create_cartesian_search_space,
    prepare_search_slot_arrays,
    run_exact_cpu_search,
)


DESKTOP_RESULT_DIRECTORY = "optimizer_results"
DESKTOP_RESULT_SORT_CACHE_DIRECTORY = "optimizer_result_sort_cache"
DEFAULT_CPU_BATCH_SIZE = 131_072
DEFAULT_RESULT_WRITE_BATCH_SIZE = 131_072

ProgressSink = Callable[[str, int, int, tuple[int, int, int], float], None]
CancellationPredicate = Callable[[], bool]


class OptimizerSearchServiceError(RuntimeError):
    """A compact, path-free failure that may cross the desktop boundary."""

    def __init__(self, stage: str, code: str, message: str) -> None:
        self.stage = stage
        self.code = code
        super().__init__(message)


class OptimizerSearchCancelled(RuntimeError):
    """Raised only while converting a completed search into durable storage."""


@dataclass(frozen=True, slots=True)
class PreparedOptimizerSearch:
    request: OptimizationRequest
    inventory_snapshot: object
    slot_arrays: SearchReadySlotArrays
    evaluation_context: ExactBuildEvaluationContext
    target_pattern: object
    counting_context: MatchCountingContext
    equipped_lookup: DenseItemEquippedLookup
    backend: str
    cuda_diagnostic: CudaRuntimeDiagnostic
    cuda_host_inputs: Any | None
    total_permutations: int


@dataclass(frozen=True, slots=True)
class OptimizerSearchExecution:
    state: str
    total_permutations: int
    searched_permutations: int
    category_counts: tuple[int, int, int]
    elapsed_seconds: float
    result_run_id: str | None = None
    failure_stage: str | None = None
    failure_code: str | None = None


class OptimizerSearchService:
    """Compose the completed search authorities without exposing private rows."""

    def __init__(
        self,
        user_data_dir: str | Path,
        *,
        profile_service: OptimizerProfileService | None = None,
        cuda_diagnostic: Callable[[], CudaRuntimeDiagnostic] | None = None,
        cuda_input_compiler: Callable[..., Any] | None = None,
        cuda_transfer: Callable[..., Any] | None = None,
        cuda_runner: Callable[..., Any] | None = None,
        cpu_runner: Callable[..., Any] = run_exact_cpu_search,
        cpu_batch_size: int = DEFAULT_CPU_BATCH_SIZE,
        result_write_batch_size: int = DEFAULT_RESULT_WRITE_BATCH_SIZE,
    ) -> None:
        self.user_data_dir = Path(user_data_dir)
        self.database_path = self.user_data_dir / "optimizer.db"
        self.result_store = ResultRunStore(self.user_data_dir / DESKTOP_RESULT_DIRECTORY)
        self.result_lifecycle = ResultLifecycleManager(
            self.user_data_dir / DESKTOP_RESULT_DIRECTORY,
            self.user_data_dir / DESKTOP_RESULT_SORT_CACHE_DIRECTORY,
        )
        self.profile_service = profile_service or OptimizerProfileService(self.user_data_dir)
        self.cuda_diagnostic = cuda_diagnostic or self._diagnose_cuda
        self.cuda_input_compiler = cuda_input_compiler or self._compile_cuda_inputs
        self.cuda_transfer = cuda_transfer or self._transfer_cuda_inputs
        self.cuda_runner = cuda_runner or self._run_controlled_cuda
        self.cpu_runner = cpu_runner
        self.cpu_batch_size = max(1, int(cpu_batch_size))
        self.result_write_batch_size = max(1, int(result_write_batch_size))

    @staticmethod
    def _diagnose_cuda() -> CudaRuntimeDiagnostic:
        return diagnose_cuda_runtime(disabled=cuda_disabled_from_environment(os.environ))

    @staticmethod
    def _compile_cuda_inputs(slot_arrays: Any, evaluation_context: Any) -> Any:
        from src.optimizer.cuda.inputs import compile_cuda_host_inputs

        return compile_cuda_host_inputs(slot_arrays, evaluation_context)

    @staticmethod
    def _transfer_cuda_inputs(host_inputs: Any, diagnostic: CudaRuntimeDiagnostic) -> Any:
        from src.optimizer.cuda.inputs import transfer_cuda_inputs

        return transfer_cuda_inputs(host_inputs, diagnostic)

    @staticmethod
    def _run_controlled_cuda(device_inputs: Any, diagnostic: CudaRuntimeDiagnostic, counting_context: Any, **kwargs: Any) -> Any:
        from src.optimizer.cuda.orchestration import run_controlled_cuda_search

        return run_controlled_cuda_search(device_inputs, diagnostic, counting_context, **kwargs)

    def cleanup_stale_results(self) -> int:
        """Apply the Phase 07 policy to owned stale transactions and result artifacts."""

        report = self.result_lifecycle.clean(
            ResultLifecycleRequest(datetime.now(UTC), dry_run=False)
        )
        return report.removed_artifacts

    def prepare(
        self,
        draft: Mapping[str, object],
        request_id: str,
        should_cancel: CancellationPredicate,
    ) -> PreparedOptimizerSearch:
        if should_cancel():
            raise OptimizerSearchCancelled()
        try:
            request = self.profile_service.create_request(draft, request_id)
        except OptimizerProfileServiceError as error:
            raise OptimizerSearchServiceError(
                "validation",
                error.code,
                str(error),
            ) from error
        if not self.database_path.is_file():
            raise OptimizerSearchServiceError(
                "inventory",
                "inventory-missing",
                "Import a Fribbels gear.txt inventory before starting a search.",
            )
        try:
            repository = InventoryRepository(self.database_path)
            repository.initialize()
            inventory = repository.load_inventory()
            imported_heroes = repository.load_heroes()
            inventory_snapshot = repository.dense_snapshot()
        except Exception as error:
            raise OptimizerSearchServiceError(
                "inventory",
                "inventory-unavailable",
                "The saved optimizer inventory could not be read safely.",
            ) from error
        if not inventory:
            raise OptimizerSearchServiceError(
                "inventory",
                "inventory-empty",
                "Import owned gear before starting a search.",
            )
        if should_cancel():
            raise OptimizerSearchCancelled()
        try:
            profile = self.profile_service.profiles.select(
                request.hero_id,
                request.base_profile_id,
            )
            artifact = self.profile_service.artifacts.select_from_modifiers(request.modifiers)
            skills = self.profile_service.skill_contexts.select(
                request.hero_id,
                request.skill_contexts,
            )
            pattern = compile_set_pattern(request.set_pattern)
            selected_name = profile.hero.name.strip().casefold()
            selected_hero_alias_ids = tuple(
                hero.hero_id
                for hero in imported_heroes
                if hero.name is not None
                and hero.name.strip().casefold() == selected_name
            )
            slot_arrays = prepare_search_slot_arrays(
                request,
                profile,
                inventory,
                prefilter_fully_constrained_sets=True,
                selected_hero_alias_ids=selected_hero_alias_ids,
            )
            exact_context = compile_exact_build_context(
                request,
                profile,
                artifact,
                skills,
                pattern,
            )
            counting_context = compile_match_counting_context(request)
            total = create_cartesian_search_space(slot_arrays).total_permutations
        except Exception as error:
            code = getattr(error, "code", "search-preparation-failed")
            safe_code = code if isinstance(code, str) and code else "search-preparation-failed"
            if safe_code == "empty-search-slots":
                message = (
                    "Search filters leave at least one gear slot empty. Broaden the main-stat, "
                    "enhancement, equipped-item, or projection choices."
                )
            else:
                message = "The selected hero and search settings could not be prepared safely."
            raise OptimizerSearchServiceError("preparation", safe_code, message) from error

        equipped_by_id = {
            item.stable_item_id: item.gear_item.equipped_hero_id is not None
            for item in inventory
        }
        equipped_lookup = DenseItemEquippedLookup.from_pairs(
            (dense_id, equipped_by_id[stable_id])
            for dense_id, stable_id in slot_arrays.dense_id_to_stable_id
        )
        diagnostic = self.cuda_diagnostic()
        cuda_host_inputs = None
        if diagnostic.status is CudaDiagnosticStatus.READY and request.execution_preference is not ExecutionPreference.CPU:
            try:
                cuda_host_inputs = self.cuda_input_compiler(slot_arrays, exact_context)
            except Exception as error:
                if request.execution_preference is ExecutionPreference.GPU:
                    code = getattr(error, "code", "cuda-inputs-unavailable")
                    raise OptimizerSearchServiceError(
                        "cuda-inputs",
                        code if isinstance(code, str) and code else "cuda-inputs-unavailable",
                        "This search cannot be represented safely for CUDA execution.",
                    ) from error
        if request.execution_preference is ExecutionPreference.CPU:
            backend = "cpu"
        elif request.execution_preference is ExecutionPreference.GPU:
            if diagnostic.status is not CudaDiagnosticStatus.READY:
                raise OptimizerSearchServiceError(
                    "cuda-readiness",
                    diagnostic.status.value,
                    "CUDA was requested but is not ready. Choose automatic or CPU execution.",
                )
            backend = "cuda"
        else:
            backend = "cuda" if cuda_host_inputs is not None else "cpu"
        return PreparedOptimizerSearch(
            request=request,
            inventory_snapshot=inventory_snapshot,
            slot_arrays=slot_arrays,
            evaluation_context=exact_context,
            target_pattern=pattern,
            counting_context=counting_context,
            equipped_lookup=equipped_lookup,
            backend=backend,
            cuda_diagnostic=diagnostic,
            cuda_host_inputs=cuda_host_inputs,
            total_permutations=total,
        )

    def run(
        self,
        prepared: PreparedOptimizerSearch,
        run_id: str,
        should_cancel: CancellationPredicate,
        on_progress: ProgressSink,
        *,
        force_cpu: bool = False,
    ) -> OptimizerSearchExecution:
        if force_cpu or prepared.backend == "cpu":
            return self._run_cpu(prepared, run_id, should_cancel, on_progress)
        return self._run_cuda(prepared, run_id, should_cancel, on_progress)

    def _run_cpu(
        self,
        prepared: PreparedOptimizerSearch,
        run_id: str,
        should_cancel: CancellationPredicate,
        on_progress: ProgressSink,
    ) -> OptimizerSearchExecution:
        def progress(value: CpuExactSearchProgress) -> None:
            on_progress(
                "cpu",
                value.total_permutations,
                value.searched_permutations,
                (value.emitted_match_count, 0, 0),
                value.elapsed_seconds,
            )

        result = self.cpu_runner(
            prepared.slot_arrays,
            prepared.evaluation_context,
            prepared.counting_context,
            batch_size=self.cpu_batch_size,
            should_cancel=should_cancel,
            on_progress=progress,
        )
        counts = tuple(result.counting.category_counts)
        if result.completed:
            try:
                self._publish_cpu(prepared, run_id, result.rows, should_cancel)
            except OptimizerSearchCancelled:
                return OptimizerSearchExecution(
                    "cancelled",
                    result.total_permutations,
                    result.searched_permutations,
                    counts,
                    result.elapsed_seconds,
                )
            return OptimizerSearchExecution(
                "completed",
                result.total_permutations,
                result.searched_permutations,
                counts,
                result.elapsed_seconds,
                result_run_id=run_id,
            )
        return OptimizerSearchExecution(
            "overflowed" if result.overflowed else "cancelled",
            result.total_permutations,
            result.searched_permutations,
            counts,
            result.elapsed_seconds,
        )

    def _run_cuda(
        self,
        prepared: PreparedOptimizerSearch,
        run_id: str,
        should_cancel: CancellationPredicate,
        on_progress: ProgressSink,
    ) -> OptimizerSearchExecution:
        last_total = prepared.total_permutations
        last_searched = 0
        last_counts = (0, 0, 0)
        last_elapsed = 0.0
        try:
            host_inputs = prepared.cuda_host_inputs
            if host_inputs is None:
                raise RuntimeError("CUDA host inputs were not prepared.")

            def progress(value: Any) -> None:
                nonlocal last_total, last_searched, last_counts, last_elapsed
                last_total = value.total_permutations
                last_searched = value.evaluated_permutations
                last_counts = tuple(value.accepted_category_counts)
                last_elapsed = value.elapsed_seconds
                on_progress(
                    "cuda",
                    value.total_permutations,
                    value.evaluated_permutations,
                    value.accepted_category_counts,
                    value.elapsed_seconds,
                )

            with self.cuda_transfer(host_inputs, prepared.cuda_diagnostic) as device_inputs:
                result = self.cuda_runner(
                    device_inputs,
                    prepared.cuda_diagnostic,
                    prepared.counting_context,
                    should_cancel=should_cancel,
                    on_progress=progress,
                )
        except Exception as error:
            code = getattr(error, "code", "cuda-stage-failed")
            return OptimizerSearchExecution(
                "failed",
                last_total,
                last_searched,
                last_counts,
                last_elapsed,
                failure_stage="cuda-search" if last_searched else "cuda-setup",
                failure_code=code if isinstance(code, str) and code else "cuda-stage-failed",
            )

        counts = tuple(result.counting.category_counts)
        if result.completed:
            try:
                self._publish_cuda(prepared, run_id, result.batches, should_cancel)
            except OptimizerSearchCancelled:
                return OptimizerSearchExecution(
                    "cancelled",
                    result.total_permutations,
                    result.evaluated_permutations,
                    counts,
                    result.elapsed_seconds,
                )
            return OptimizerSearchExecution(
                "completed",
                result.total_permutations,
                result.evaluated_permutations,
                counts,
                result.elapsed_seconds,
                result_run_id=run_id,
            )
        if result.failed:
            failure = result.failure
            return OptimizerSearchExecution(
                "failed",
                result.total_permutations,
                result.evaluated_permutations,
                counts,
                result.elapsed_seconds,
                failure_stage="cuda-search" if failure is None else failure.stage,
                failure_code="cuda-stage-failed" if failure is None else failure.code,
            )
        return OptimizerSearchExecution(
            "overflowed" if result.overflowed else "cancelled",
            result.total_permutations,
            result.evaluated_permutations,
            counts,
            result.elapsed_seconds,
        )

    def _publish_cpu(
        self,
        prepared: PreparedOptimizerSearch,
        run_id: str,
        rows: tuple[Any, ...],
        should_cancel: CancellationPredicate,
    ) -> None:
        writer = self.result_store.begin_run(
            run_id,
            maximum_rows=prepared.request.result_cap,
        )
        try:
            for start in range(0, len(rows), self.result_write_batch_size):
                if should_cancel():
                    raise OptimizerSearchCancelled()
                batch = rows[start : start + self.result_write_batch_size]
                writer.append(
                    writer.row_count,
                    result_columns_from_cpu_rows(
                        batch,
                        prepared.slot_arrays,
                        prepared.equipped_lookup,
                    ),
                )
            if should_cancel():
                raise OptimizerSearchCancelled()
            writer.complete(len(rows))
        finally:
            if writer.state.value != "published":
                writer.abort("optimizer-search-not-completed")

    def _publish_cuda(
        self,
        prepared: PreparedOptimizerSearch,
        run_id: str,
        batches: tuple[Any, ...],
        should_cancel: CancellationPredicate,
    ) -> None:
        writer = self.result_store.begin_run(
            run_id,
            maximum_rows=prepared.request.result_cap,
        )
        try:
            for batch in batches:
                if should_cancel():
                    raise OptimizerSearchCancelled()
                writer.append(
                    writer.row_count,
                    result_columns_from_cuda_batch(batch, prepared.equipped_lookup),
                )
            if should_cancel():
                raise OptimizerSearchCancelled()
            writer.complete(writer.row_count)
        finally:
            if writer.state.value != "published":
                writer.abort("optimizer-search-not-completed")


__all__ = [
    "DESKTOP_RESULT_DIRECTORY",
    "DESKTOP_RESULT_SORT_CACHE_DIRECTORY",
    "OptimizerSearchCancelled",
    "OptimizerSearchExecution",
    "OptimizerSearchService",
    "OptimizerSearchServiceError",
    "PreparedOptimizerSearch",
]
