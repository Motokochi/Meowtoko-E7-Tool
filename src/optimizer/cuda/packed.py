from __future__ import annotations

import math
from dataclasses import dataclass
from numbers import Integral
from typing import Any

import numpy as np

from src.optimizer.cuda.compaction import (
    CudaCompactionHostArray,
    CudaCompactionHostBatch,
)
from src.optimizer.cuda.inputs import CUDA_SIGNED_INT64_MAX, CudaDeviceInputs
from src.optimizer.cuda.runtime import CudaDiagnosticStatus, CudaRuntimeDiagnostic


CUDA_PACKED_FILTER_KERNEL_NAME = "e7_filter_exact_builds_packed"
CUDA_PACKED_MATERIALIZE_KERNEL_NAME = "e7_materialize_exact_builds"
CUDA_PACKED_THREADS = 256
CUDA_PACKED_PERMUTATIONS_PER_WORD = 32
CUDA_PACKED_DEFAULT_CHUNK_PERMUTATIONS = 67_108_864
CUDA_PACKED_TARGET_PERMUTATIONS_PER_SECOND = 10_917_870_164

_U4 = np.dtype("<u4")
_U8 = np.dtype("<u8")
_I4 = np.dtype("<i4")


class CudaPackedFilterError(ValueError):
    """Actionable exact-only packed-filter validation or CUDA failure."""

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code} at {path}: {message}")


def _error(code: str, path: str, message: str) -> CudaPackedFilterError:
    return CudaPackedFilterError(code, path, message)


def _integer(
    value: object,
    path: str,
    *,
    minimum: int = 0,
    maximum: int = CUDA_SIGNED_INT64_MAX,
) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise _error("invalid-integer", path, "must be an integer; booleans are not accepted.")
    numeric = int(value)
    if numeric < minimum or numeric > maximum:
        raise _error("integer-out-of-range", path, f"must be between {minimum} and {maximum}.")
    return numeric


@dataclass(frozen=True, slots=True)
class CudaPackedExactSignature:
    """Up to three optional completed-set requirements for the packed path."""

    set_indices: tuple[int, int, int]
    piece_counts: tuple[int, int, int]
    set_count: int
    derived_bounds_active: bool

    @classmethod
    def compile(cls, device_inputs: CudaDeviceInputs) -> "CudaPackedExactSignature":
        required = device_inputs.host_array("required_piece_counts")
        nonzero = tuple(
            (index, int(value))
            for index, value in enumerate(required)
            if int(value) != 0
        )
        if len(nonzero) > 3 or sum(value for _, value in nonzero) > 6:
            raise _error(
                "unsupported-exact-set-pattern",
                "required_piece_counts",
                "must describe zero to three set requirements using at most six pieces.",
            )
        if device_inputs.maximum_replacement_distance != 0:
            raise _error(
                "near-set-mode-removed",
                "device_inputs",
                "the packed optimizer supports exact completed sets only.",
            )
        padded = nonzero + ((0, 0),) * (3 - len(nonzero))
        derived_active = bool(
            np.any(device_inputs.host_array("derived_minimum_present"))
            or np.any(device_inputs.host_array("derived_maximum_present"))
        )
        return cls(
            tuple(index for index, _ in padded),
            tuple(value for _, value in padded),
            len(nonzero),
            derived_active,
        )


@dataclass(frozen=True, slots=True)
class CudaPackedFilterBatch:
    start_index: int
    stop_index: int
    exact_candidate_count: int
    accepted_count: int
    accepted_flat_indices: np.ndarray[Any, Any]

    def __post_init__(self) -> None:
        start = _integer(self.start_index, "CudaPackedFilterBatch.start_index")
        stop = _integer(self.stop_index, "CudaPackedFilterBatch.stop_index", minimum=1)
        if stop <= start:
            raise _error("empty-filter-batch", "stop_index", "must exceed start_index.")
        exact = _integer(
            self.exact_candidate_count,
            "CudaPackedFilterBatch.exact_candidate_count",
            maximum=stop - start,
        )
        accepted = _integer(
            self.accepted_count,
            "CudaPackedFilterBatch.accepted_count",
            maximum=exact,
        )
        flat = np.asarray(self.accepted_flat_indices)
        if (
            flat.dtype != np.dtype("<i8")
            or flat.ndim != 1
            or flat.shape[0] > accepted
        ):
            raise _error(
                "invalid-accepted-indices",
                "CudaPackedFilterBatch.accepted_flat_indices",
                "must contain an accepted signed-64 flat-index prefix.",
            )
        if flat.shape[0] and (
            np.any(flat < start)
            or np.any(flat >= stop)
            or np.any(flat[1:] <= flat[:-1])
        ):
            raise _error(
                "noncanonical-accepted-indices",
                "CudaPackedFilterBatch.accepted_flat_indices",
                "must be unique, ascending, and inside the filter batch.",
            )
        frozen = np.frombuffer(np.ascontiguousarray(flat).tobytes(), dtype="<i8")
        object.__setattr__(self, "start_index", start)
        object.__setattr__(self, "stop_index", stop)
        object.__setattr__(self, "exact_candidate_count", exact)
        object.__setattr__(self, "accepted_count", accepted)
        object.__setattr__(self, "accepted_flat_indices", frozen)

    @property
    def evaluated_count(self) -> int:
        return self.stop_index - self.start_index

    @property
    def out_of_scope_count(self) -> int:
        return self.evaluated_count - self.exact_candidate_count

    @property
    def hard_bound_rejected_count(self) -> int:
        return self.exact_candidate_count - self.accepted_count

    @property
    def captured_count(self) -> int:
        return int(self.accepted_flat_indices.shape[0])


CUDA_PACKED_FILTER_KERNEL_SOURCE = r"""
__device__ __forceinline__ float e7_add(float a, float b) { return __fadd_rn(a, b); }
__device__ __forceinline__ float e7_sub(float a, float b) { return __fsub_rn(a, b); }
__device__ __forceinline__ float e7_mul(float a, float b) { return __fmul_rn(a, b); }
__device__ __forceinline__ float e7_div(float a, float b) { return __fdiv_rn(a, b); }
__device__ __forceinline__ float e7_llf(long long value) { return __ll2float_rn(value); }

__device__ __forceinline__ void e7_error(int* error_code, int code) {
    atomicCAS(error_code, 0, code);
}

__device__ __forceinline__ long long e7_trunc_float(float value, int* error_code) {
    if (!isfinite(value)) {
        e7_error(error_code, 1);
        return 0;
    }
    if (value >= 9223372036854775808.0f || value < -9223372036854775808.0f) {
        e7_error(error_code, 2);
        return 0;
    }
    return __float2ll_rz(value);
}

__device__ __forceinline__ long long e7_trunc_double(double value, int* error_code) {
    if (!isfinite(value)) {
        e7_error(error_code, 1);
        return 0;
    }
    if (value >= 9223372036854775808.0 || value < -9223372036854775808.0) {
        e7_error(error_code, 2);
        return 0;
    }
    return __double2ll_rz(value);
}

__device__ __forceinline__ bool e7_below(long long actual, float boundary) {
    return __ll2double_rn(actual) < (double)boundary;
}

__device__ __forceinline__ bool e7_above(long long actual, float boundary) {
    return __ll2double_rn(actual) > (double)boundary;
}

__device__ __forceinline__ unsigned char e7_activation(
    const unsigned char* build_sets,
    const unsigned char* pieces_required,
    const unsigned char* stackable_flags,
    int set_index
) {
    unsigned char piece_count = 0;
    #pragma unroll
    for (int slot = 0; slot < 6; ++slot) {
        if ((int)build_sets[slot] == set_index) piece_count += 1;
    }
    unsigned char completed = piece_count / pieces_required[set_index];
    if (!stackable_flags[set_index] && completed > 1) completed = 1;
    return completed;
}

__device__ __forceinline__ float e7_set_value(
    const float* unit_values,
    const unsigned char* build_sets,
    const unsigned char* pieces_required,
    const unsigned char* stackable_flags,
    int set_index,
    int stat_index
) {
    return e7_mul(
        unit_values[set_index * 8 + stat_index],
        __int2float_rn((int)e7_activation(
            build_sets, pieces_required, stackable_flags, set_index
        ))
    );
}

__device__ long long e7_build_score(
    const float* unrounded,
    const long long* raw,
    const float* base_stats,
    const float* artifact_flat_stats,
    const float* set_unit_values,
    const unsigned char* build_sets,
    const unsigned char* pieces_required,
    const unsigned char* stackable_flags,
    int* error_code
) {
    float ratios[3];
    float value = e7_sub(unrounded[0], base_stats[0]);
    value = e7_sub(value, artifact_flat_stats[0]);
    value = e7_sub(value, e7_set_value(set_unit_values, build_sets, pieces_required, stackable_flags, 2, 0));
    ratios[0] = e7_mul(e7_div(value, base_stats[0]), 100.0f);

    value = e7_sub(unrounded[1], base_stats[1]);
    value = e7_sub(value, artifact_flat_stats[1]);
    value = e7_sub(value, e7_set_value(set_unit_values, build_sets, pieces_required, stackable_flags, 0, 1));
    value = e7_sub(value, e7_set_value(set_unit_values, build_sets, pieces_required, stackable_flags, 20, 1));
    value = e7_sub(value, e7_set_value(set_unit_values, build_sets, pieces_required, stackable_flags, 17, 1));
    ratios[1] = e7_mul(e7_div(value, base_stats[1]), 100.0f);

    value = e7_sub(unrounded[2], base_stats[2]);
    value = e7_sub(value, artifact_flat_stats[2]);
    value = e7_sub(value, e7_set_value(set_unit_values, build_sets, pieces_required, stackable_flags, 1, 2));
    ratios[2] = e7_mul(e7_div(value, base_stats[2]), 100.0f);

    float additive[8];
    #pragma unroll
    for (int index = 0; index < 8; ++index) additive[index] = 0.0f;
    value = e7_sub(e7_llf(raw[3]), base_stats[3]);
    value = e7_sub(value, e7_set_value(set_unit_values, build_sets, pieces_required, stackable_flags, 3, 3));
    value = e7_sub(value, e7_set_value(set_unit_values, build_sets, pieces_required, stackable_flags, 14, 3));
    value = e7_sub(value, e7_set_value(set_unit_values, build_sets, pieces_required, stackable_flags, 18, 3));
    value = e7_sub(value, e7_set_value(set_unit_values, build_sets, pieces_required, stackable_flags, 22, 3));
    additive[3] = value;
    value = e7_sub(e7_llf(raw[4]), base_stats[4]);
    additive[4] = e7_sub(value, e7_set_value(set_unit_values, build_sets, pieces_required, stackable_flags, 4, 4));
    value = e7_sub(e7_llf(raw[5]), base_stats[5]);
    additive[5] = e7_sub(value, e7_set_value(set_unit_values, build_sets, pieces_required, stackable_flags, 6, 5));
    value = e7_sub(e7_llf(raw[6]), base_stats[6]);
    additive[6] = e7_sub(value, e7_set_value(set_unit_values, build_sets, pieces_required, stackable_flags, 5, 6));
    value = e7_sub(e7_llf(raw[7]), base_stats[7]);
    additive[7] = e7_sub(value, e7_set_value(set_unit_values, build_sets, pieces_required, stackable_flags, 9, 7));

    float weighted = e7_add(e7_add(ratios[1], ratios[0]), ratios[2]);
    weighted = e7_add(weighted, e7_mul(additive[4], 1.6f));
    weighted = e7_add(weighted, e7_mul(additive[5], 1.14f));
    weighted = e7_add(weighted, additive[6]);
    weighted = e7_add(weighted, additive[7]);
    weighted = e7_add(weighted, e7_mul(additive[3], 2.0f));
    return e7_trunc_float(weighted, error_code);
}

__device__ long long e7_skill_value(
    int skill,
    const float* formula,
    const int pen_set_on,
    float percent_damage,
    const unsigned char* kind_codes,
    const signed char* hit_codes,
    const float* rates,
    const float* powers,
    const float* self_hp,
    const float* self_attack,
    const float* self_defense,
    const float* self_speed,
    const float* extra_attack,
    const float* extra_defense,
    const float* increased,
    const float* crit_increase,
    const float* target_defenses,
    const int* target_counts,
    const float* penetrations,
    int* error_code
) {
    const int kind = (int)kind_codes[skill];
    if (kind == 0) return 0;
    const float atk = formula[0];
    const float hp = formula[1];
    const float defense = formula[2];
    const float speed = formula[3];
    const float crit_damage = e7_div(formula[5], 100.0f);
    const float target_defense = target_defenses[skill];
    const int single_target = target_counts[skill] == 1 ? 1 : 0;
    const float real_penetration = e7_mul(
        e7_sub(1.0f, penetrations[skill]),
        e7_sub(1.0f, e7_mul(e7_mul(__int2float_rn(pen_set_on), 0.15f), __int2float_rn(single_target)))
    );
    float scaling = e7_add(e7_mul(self_hp[skill], hp), e7_mul(self_attack[skill], atk));
    scaling = e7_add(scaling, e7_mul(self_defense[skill], defense));
    scaling = e7_add(scaling, e7_mul(self_speed[skill], speed));
    float hit = 0.0f;
    const int hit_code = (int)hit_codes[skill];
    if (hit_code == 0) hit = e7_add(crit_damage, crit_increase[skill]);
    else if (hit_code == 1) hit = 1.3f;
    else if (hit_code == 2) hit = 1.0f;
    else if (hit_code == 3) hit = 0.75f;
    const float increased_multiplier = e7_add(1.0f, increased[skill]);
    const float damage_up = e7_add(1.0f, e7_mul(self_speed[skill], speed));
    const float extra_scaling = e7_add(
        e7_mul(extra_attack[skill], atk),
        e7_mul(extra_defense[skill], defense)
    );
    const float extra_denominator = e7_add(e7_div(e7_mul(target_defense, 0.3f), 300.0f), 1.0f);
    const float extra_damage = e7_div(e7_mul(extra_scaling, 1.871f), extra_denominator);
    float offensive = e7_add(e7_mul(atk, rates[skill]), scaling);
    offensive = e7_mul(offensive, 1.871f);
    offensive = e7_mul(offensive, powers[skill]);
    offensive = e7_mul(offensive, increased_multiplier);
    offensive = e7_mul(offensive, hit);
    offensive = e7_mul(offensive, damage_up);
    offensive = e7_mul(offensive, percent_damage);
    float support = e7_add(e7_mul(self_hp[skill], hp), e7_mul(self_attack[skill], atk));
    support = e7_add(support, e7_mul(self_defense[skill], defense));
    support = e7_mul(support, kind == 2 ? 1.0f : 0.0f);
    const float clamped_penetration = real_penetration > 0.0f ? real_penetration : 0.0f;
    const float defensive_denominator = e7_add(
        e7_div(e7_mul(target_defense, clamped_penetration), 300.0f),
        1.0f
    );
    return e7_trunc_float(
        e7_add(e7_add(e7_div(offensive, defensive_denominator), support), extra_damage),
        error_code
    );
}

extern "C" __global__
void e7_filter_exact_builds_packed(
    const long long start_index,
    const long long permutation_count,
    const long long word_count,
    const long long* __restrict__ slot_starts,
    const long long* __restrict__ slot_radices,
    const unsigned char* __restrict__ input_set_indices,
    const float* __restrict__ item_contributions,
    const int* __restrict__ item_gear_scores,
    const float* __restrict__ insertion_stats,
    const float* __restrict__ final_multipliers,
    const float* __restrict__ post_set_additions,
    const unsigned char* __restrict__ set_pieces_required,
    const unsigned char* __restrict__ numeric_operation_sets,
    const unsigned char* __restrict__ numeric_operation_stats,
    const float* __restrict__ base_stats,
    const float* __restrict__ artifact_flat_stats,
    const float* __restrict__ set_unit_values,
    const unsigned char* __restrict__ set_stackable_flags,
    const float* __restrict__ primary_minimum_values,
    const unsigned char* __restrict__ primary_minimum_present,
    const float* __restrict__ primary_maximum_values,
    const unsigned char* __restrict__ primary_maximum_present,
    const float* __restrict__ derived_minimum_values,
    const unsigned char* __restrict__ derived_minimum_present,
    const float* __restrict__ derived_maximum_values,
    const unsigned char* __restrict__ derived_maximum_present,
    const unsigned char* __restrict__ set_penetration_flags,
    const float* __restrict__ set_percent_damage_bonuses,
    const float* __restrict__ penetration_bonus_multiplier,
    const unsigned char* __restrict__ skill_kind_codes,
    const signed char* __restrict__ skill_hit_type_codes,
    const float* __restrict__ skill_rates,
    const float* __restrict__ skill_powers,
    const float* __restrict__ skill_self_hp_scaling,
    const float* __restrict__ skill_self_attack_scaling,
    const float* __restrict__ skill_self_defense_scaling,
    const float* __restrict__ skill_self_speed_scaling,
    const float* __restrict__ skill_extra_attack_scaling,
    const float* __restrict__ skill_extra_defense_scaling,
    const float* __restrict__ skill_increased_values,
    const float* __restrict__ skill_critical_damage_increases,
    const float* __restrict__ skill_target_defenses,
    const int* __restrict__ skill_target_counts,
    const float* __restrict__ skill_penetrations,
    const unsigned char target_set_count,
    const unsigned char target_set_0,
    const unsigned char target_set_1,
    const unsigned char target_set_2,
    const unsigned char target_pieces_0,
    const unsigned char target_pieces_1,
    const unsigned char target_pieces_2,
    const unsigned char derived_bounds_active,
    unsigned int* __restrict__ pass_words,
    unsigned long long* __restrict__ counters,
    int* __restrict__ error_code
) {
    const long long word =
        (long long)blockIdx.x * (long long)blockDim.x + (long long)threadIdx.x;
    unsigned int pass_mask = 0U;
    unsigned long long local_exact = 0ULL;
    unsigned long long local_accepted = 0ULL;

    if (word < word_count) {
        const long long first_local = word * 32LL;
        long long remaining = start_index + first_local;
        long long offsets[6];
        #pragma unroll
        for (int slot = 5; slot >= 0; --slot) {
            const long long radix = slot_radices[slot];
            offsets[slot] = remaining % radix;
            remaining /= radix;
        }

        #pragma unroll 1
        for (int lane = 0; lane < 32; ++lane) {
            const long long local_index = first_local + (long long)lane;
            if (local_index >= permutation_count) break;
            if (lane != 0) {
                offsets[5] += 1;
                #pragma unroll
                for (int slot = 5; slot > 0; --slot) {
                    if (offsets[slot] < slot_radices[slot]) break;
                    offsets[slot] = 0;
                    offsets[slot - 1] += 1;
                }
            }

            long long addresses[6];
            unsigned char set_counts[3] = {0, 0, 0};
            unsigned char build_sets[6];
            #pragma unroll
            for (int slot = 0; slot < 6; ++slot) {
                const long long address = slot_starts[slot] + offsets[slot];
                addresses[slot] = address;
                const unsigned char set_index = input_set_indices[address];
                build_sets[slot] = set_index;
                if (set_index == target_set_0) set_counts[0] += 1;
                else if (target_set_count > 1 && set_index == target_set_1) set_counts[1] += 1;
                else if (target_set_count > 2 && set_index == target_set_2) set_counts[2] += 1;
            }
            const bool required_sets_complete =
                set_counts[0] >= target_pieces_0
                && set_counts[1] >= target_pieces_1
                && set_counts[2] >= target_pieces_2;
            if (!required_sets_complete) continue;
            local_exact += 1ULL;

            float unrounded[8];
            long long raw[8];
            long long effective[8];
            bool primary_pass = true;
            #pragma unroll
            for (int stat = 0; stat < 8; ++stat) {
                float value = insertion_stats[stat];
                #pragma unroll
                for (int slot = 0; slot < 6; ++slot) {
                    value = e7_add(value, item_contributions[addresses[slot] * 8 + stat]);
                }
                #pragma unroll
                for (int operation = 0; operation < 13; ++operation) {
                    if ((int)numeric_operation_stats[operation] != stat) continue;
                    const int set_index = (int)numeric_operation_sets[operation];
                    const unsigned char activation = e7_activation(
                        build_sets, set_pieces_required, set_stackable_flags, set_index
                    );
                    const float contribution = e7_mul(
                        set_unit_values[set_index * 8 + stat],
                        __int2float_rn((int)activation)
                    );
                    if (contribution != 0.0f) value = e7_add(value, contribution);
                }
                if (final_multipliers[stat] != 1.0f) {
                    value = e7_mul(value, final_multipliers[stat]);
                }
                if (post_set_additions[stat] != 0.0f) {
                    value = e7_add(value, post_set_additions[stat]);
                }
                unrounded[stat] = value;
                raw[stat] = e7_trunc_float(value, error_code);
                long long bounded = raw[stat];
                if (stat == 4 && bounded > 100) bounded = 100;
                if (stat == 5 && bounded > 350) bounded = 350;
                effective[stat] = bounded;
                if (
                    (primary_minimum_present[stat] && e7_below(bounded, primary_minimum_values[stat]))
                    || (primary_maximum_present[stat] && e7_above(bounded, primary_maximum_values[stat]))
                ) {
                    primary_pass = false;
                    break;
                }
            }
            if (!primary_pass) continue;

            bool derived_pass = true;
            if (derived_bounds_active) {
                bool penetration_on = false;
                float percent_damage = 1.0f;
                #pragma unroll
                for (int set_index = 0; set_index < 24; ++set_index) {
                    const unsigned char activation = e7_activation(
                        build_sets, set_pieces_required, set_stackable_flags, set_index
                    );
                    if (set_penetration_flags[set_index] && activation) {
                        penetration_on = true;
                    }
                    const float bonus = e7_mul(
                        set_percent_damage_bonuses[set_index],
                        __int2float_rn((int)activation)
                    );
                    if (bonus != 0.0f) percent_damage = e7_add(percent_damage, bonus);
                }
                const float penetration_multiplier =
                    penetration_on ? penetration_bonus_multiplier[0] : 1.0f;
                float formula[8] = {
                    unrounded[0], unrounded[1], unrounded[2],
                    e7_llf(effective[3]), e7_llf(effective[4]), e7_llf(effective[5]),
                    e7_llf(effective[6]), e7_llf(effective[7])
                };
                long long metrics[15];
                long long gear_score_sum = 0;
                #pragma unroll
                for (int slot = 0; slot < 6; ++slot) {
                    gear_score_sum += (long long)item_gear_scores[addresses[slot]];
                }
                metrics[0] = e7_build_score(
                    unrounded, raw, base_stats, artifact_flat_stats,
                    set_unit_values, build_sets, set_pieces_required,
                    set_stackable_flags, error_code
                );
                const float atk = formula[0];
                const float hp = formula[1];
                const float defense = formula[2];
                const long long speed = effective[3];
                const float crit_rate = e7_div(formula[4], 100.0f);
                const float crit_damage = e7_div(formula[5], 100.0f);
                const float attack_cp = e7_add(
                    e7_mul(atk, 1.6f),
                    e7_mul(e7_mul(e7_mul(atk, 1.6f), crit_rate), crit_damage)
                );
                const float speed_cp = e7_mul(e7_sub(e7_llf(speed), 45.0f), 0.02f);
                double cp_inner = __dmul_rn((double)attack_cp, __dadd_rn(1.0, (double)speed_cp));
                cp_inner = __dadd_rn(cp_inner, (double)hp);
                cp_inner = __dadd_rn(cp_inner, (double)e7_mul(defense, 9.3f));
                const float cp_resist = e7_add(
                    1.0f,
                    e7_div(
                        e7_add(
                            e7_div(e7_llf(effective[7]), 100.0f),
                            e7_div(e7_llf(effective[6]), 100.0f)
                        ),
                        4.0f
                    )
                );
                metrics[1] = e7_trunc_double(
                    __dmul_rn(cp_inner, (double)cp_resist), error_code
                );
                const float speed_div_1000 = e7_div(e7_llf(speed), 1000.0f);
                metrics[6] = e7_trunc_float(
                    e7_mul(hp, e7_add(e7_div(defense, 300.0f), 1.0f)), error_code
                );
                metrics[9] = e7_trunc_float(e7_mul(hp, speed_div_1000), error_code);
                metrics[7] = e7_trunc_float(
                    e7_mul(e7_llf(metrics[6]), speed_div_1000), error_code
                );
                const float expected_crit = e7_add(
                    e7_mul(e7_mul(crit_rate, atk), crit_damage),
                    e7_mul(e7_sub(1.0f, crit_rate), atk)
                );
                metrics[2] = e7_trunc_float(
                    e7_mul(e7_mul(expected_crit, penetration_multiplier), percent_damage),
                    error_code
                );
                metrics[5] = e7_trunc_float(
                    e7_mul(e7_llf(metrics[2]), speed_div_1000), error_code
                );
                metrics[10] = e7_trunc_float(
                    e7_mul(
                        e7_mul(e7_mul(atk, crit_damage), penetration_multiplier),
                        percent_damage
                    ),
                    error_code
                );
                metrics[11] = e7_trunc_float(
                    e7_mul(e7_llf(metrics[10]), speed_div_1000), error_code
                );
                metrics[4] = e7_trunc_float(
                    e7_div(
                        e7_mul(
                            e7_mul(e7_mul(crit_damage, hp), penetration_multiplier),
                            percent_damage
                        ),
                        10.0f
                    ),
                    error_code
                );
                metrics[3] = e7_trunc_float(
                    e7_mul(
                        e7_mul(e7_mul(crit_damage, defense), penetration_multiplier),
                        percent_damage
                    ),
                    error_code
                );
                metrics[8] = gear_score_sum;
                #pragma unroll
                for (int skill = 0; skill < 3; ++skill) {
                    metrics[12 + skill] = e7_skill_value(
                        skill, formula, penetration_on ? 1 : 0, percent_damage,
                        skill_kind_codes, skill_hit_type_codes, skill_rates, skill_powers,
                        skill_self_hp_scaling, skill_self_attack_scaling,
                        skill_self_defense_scaling, skill_self_speed_scaling,
                        skill_extra_attack_scaling, skill_extra_defense_scaling,
                        skill_increased_values, skill_critical_damage_increases,
                        skill_target_defenses, skill_target_counts, skill_penetrations,
                        error_code
                    );
                }
                #pragma unroll
                for (int metric = 0; metric < 15; ++metric) {
                    if (
                        (derived_minimum_present[metric]
                            && e7_below(metrics[metric], derived_minimum_values[metric]))
                        || (derived_maximum_present[metric]
                            && e7_above(metrics[metric], derived_maximum_values[metric]))
                    ) {
                        derived_pass = false;
                    }
                }
            }
            if (!derived_pass) continue;
            pass_mask |= (1U << lane);
            local_accepted += 1ULL;
        }
        pass_words[word] = pass_mask;
    }

    #pragma unroll
    for (int delta = 16; delta > 0; delta >>= 1) {
        local_exact += __shfl_down_sync(0xffffffffU, local_exact, delta);
        local_accepted += __shfl_down_sync(0xffffffffU, local_accepted, delta);
    }
    if ((threadIdx.x & 31) == 0) {
        atomicAdd(counters, local_exact);
        atomicAdd(counters + 1, local_accepted);
    }
}
"""


CUDA_PACKED_MATERIALIZE_KERNEL_SOURCE = CUDA_PACKED_FILTER_KERNEL_SOURCE + r"""
extern "C" __global__
void e7_materialize_exact_builds(
    const long long row_count,
    const long long* __restrict__ accepted_flat_indices,
    const long long* __restrict__ slot_starts,
    const long long* __restrict__ slot_radices,
    const int* __restrict__ input_dense_ids,
    const unsigned char* __restrict__ input_set_indices,
    const float* __restrict__ item_contributions,
    const int* __restrict__ item_gear_scores,
    const int* __restrict__ item_priority_scores,
    const float* __restrict__ insertion_stats,
    const float* __restrict__ final_multipliers,
    const float* __restrict__ post_set_additions,
    const unsigned char* __restrict__ set_pieces_required,
    const unsigned char* __restrict__ numeric_operation_sets,
    const unsigned char* __restrict__ numeric_operation_stats,
    const float* __restrict__ base_stats,
    const float* __restrict__ artifact_flat_stats,
    const float* __restrict__ set_unit_values,
    const unsigned char* __restrict__ set_stackable_flags,
    const unsigned char* __restrict__ set_penetration_flags,
    const float* __restrict__ set_percent_damage_bonuses,
    const float* __restrict__ penetration_bonus_multiplier,
    const unsigned char* __restrict__ skill_kind_codes,
    const signed char* __restrict__ skill_hit_type_codes,
    const float* __restrict__ skill_rates,
    const float* __restrict__ skill_powers,
    const float* __restrict__ skill_self_hp_scaling,
    const float* __restrict__ skill_self_attack_scaling,
    const float* __restrict__ skill_self_defense_scaling,
    const float* __restrict__ skill_self_speed_scaling,
    const float* __restrict__ skill_extra_attack_scaling,
    const float* __restrict__ skill_extra_defense_scaling,
    const float* __restrict__ skill_increased_values,
    const float* __restrict__ skill_critical_damage_increases,
    const float* __restrict__ skill_target_defenses,
    const int* __restrict__ skill_target_counts,
    const float* __restrict__ skill_penetrations,
    int* __restrict__ output_dense_ids,
    unsigned char* __restrict__ output_set_indices,
    long long* __restrict__ output_effective_stats,
    long long* __restrict__ output_raw_critical_hit_chances,
    long long* __restrict__ output_derived_metrics,
    float* __restrict__ output_priority_scores,
    int* __restrict__ error_code
) {
    const long long row =
        (long long)blockIdx.x * (long long)blockDim.x + (long long)threadIdx.x;
    if (row >= row_count) return;

    long long remaining = accepted_flat_indices[row];
    long long addresses[6];
    #pragma unroll
    for (int slot = 5; slot >= 0; --slot) {
        const long long radix = slot_radices[slot];
        const long long offset = remaining % radix;
        remaining /= radix;
        addresses[slot] = slot_starts[slot] + offset;
    }
    long long gear_score_sum = 0;
    long long priority_score_sum = 0;
    unsigned char build_sets[6];
    #pragma unroll
    for (int slot = 0; slot < 6; ++slot) {
        const long long address = addresses[slot];
        build_sets[slot] = input_set_indices[address];
        output_dense_ids[row * 6 + slot] = input_dense_ids[address];
        output_set_indices[row * 6 + slot] = build_sets[slot];
        gear_score_sum += (long long)item_gear_scores[address];
        priority_score_sum += (long long)item_priority_scores[address];
    }

    float unrounded[8];
    long long raw[8];
    long long effective[8];
    #pragma unroll
    for (int stat = 0; stat < 8; ++stat) {
        float value = insertion_stats[stat];
        #pragma unroll
        for (int slot = 0; slot < 6; ++slot) {
            value = e7_add(value, item_contributions[addresses[slot] * 8 + stat]);
        }
        #pragma unroll
        for (int operation = 0; operation < 13; ++operation) {
            if ((int)numeric_operation_stats[operation] != stat) continue;
            const int set_index = (int)numeric_operation_sets[operation];
            const unsigned char activation = e7_activation(
                build_sets, set_pieces_required, set_stackable_flags, set_index
            );
            const float contribution = e7_mul(
                set_unit_values[set_index * 8 + stat],
                __int2float_rn((int)activation)
            );
            if (contribution != 0.0f) value = e7_add(value, contribution);
        }
        if (final_multipliers[stat] != 1.0f) {
            value = e7_mul(value, final_multipliers[stat]);
        }
        if (post_set_additions[stat] != 0.0f) {
            value = e7_add(value, post_set_additions[stat]);
        }
        unrounded[stat] = value;
        raw[stat] = e7_trunc_float(value, error_code);
        long long bounded = raw[stat];
        if (stat == 4 && bounded > 100) bounded = 100;
        if (stat == 5 && bounded > 350) bounded = 350;
        effective[stat] = bounded;
        output_effective_stats[row * 8 + stat] = bounded;
    }
    output_raw_critical_hit_chances[row] = raw[4];

    bool penetration_on = false;
    float percent_damage = 1.0f;
    #pragma unroll
    for (int set_index = 0; set_index < 24; ++set_index) {
        const unsigned char activation = e7_activation(
            build_sets, set_pieces_required, set_stackable_flags, set_index
        );
        if (set_penetration_flags[set_index] && activation) {
            penetration_on = true;
        }
        const float bonus = e7_mul(
            set_percent_damage_bonuses[set_index],
            __int2float_rn((int)activation)
        );
        if (bonus != 0.0f) percent_damage = e7_add(percent_damage, bonus);
    }
    const float penetration_multiplier =
        penetration_on ? penetration_bonus_multiplier[0] : 1.0f;
    float formula[8] = {
        unrounded[0], unrounded[1], unrounded[2],
        e7_llf(effective[3]), e7_llf(effective[4]), e7_llf(effective[5]),
        e7_llf(effective[6]), e7_llf(effective[7])
    };
    long long metrics[15];
    metrics[0] = e7_build_score(
        unrounded, raw, base_stats, artifact_flat_stats,
        set_unit_values, build_sets, set_pieces_required,
        set_stackable_flags, error_code
    );
    const float atk = formula[0];
    const float hp = formula[1];
    const float defense = formula[2];
    const long long speed = effective[3];
    const float crit_rate = e7_div(formula[4], 100.0f);
    const float crit_damage = e7_div(formula[5], 100.0f);
    const float attack_cp = e7_add(
        e7_mul(atk, 1.6f),
        e7_mul(e7_mul(e7_mul(atk, 1.6f), crit_rate), crit_damage)
    );
    const float speed_cp = e7_mul(e7_sub(e7_llf(speed), 45.0f), 0.02f);
    double cp_inner = __dmul_rn((double)attack_cp, __dadd_rn(1.0, (double)speed_cp));
    cp_inner = __dadd_rn(cp_inner, (double)hp);
    cp_inner = __dadd_rn(cp_inner, (double)e7_mul(defense, 9.3f));
    const float cp_resist = e7_add(
        1.0f,
        e7_div(
            e7_add(
                e7_div(e7_llf(effective[7]), 100.0f),
                e7_div(e7_llf(effective[6]), 100.0f)
            ),
            4.0f
        )
    );
    metrics[1] = e7_trunc_double(__dmul_rn(cp_inner, (double)cp_resist), error_code);
    const float speed_div_1000 = e7_div(e7_llf(speed), 1000.0f);
    metrics[6] = e7_trunc_float(
        e7_mul(hp, e7_add(e7_div(defense, 300.0f), 1.0f)), error_code
    );
    metrics[9] = e7_trunc_float(e7_mul(hp, speed_div_1000), error_code);
    metrics[7] = e7_trunc_float(
        e7_mul(e7_llf(metrics[6]), speed_div_1000), error_code
    );
    const float expected_crit = e7_add(
        e7_mul(e7_mul(crit_rate, atk), crit_damage),
        e7_mul(e7_sub(1.0f, crit_rate), atk)
    );
    metrics[2] = e7_trunc_float(
        e7_mul(e7_mul(expected_crit, penetration_multiplier), percent_damage),
        error_code
    );
    metrics[5] = e7_trunc_float(
        e7_mul(e7_llf(metrics[2]), speed_div_1000), error_code
    );
    metrics[10] = e7_trunc_float(
        e7_mul(
            e7_mul(e7_mul(atk, crit_damage), penetration_multiplier),
            percent_damage
        ),
        error_code
    );
    metrics[11] = e7_trunc_float(
        e7_mul(e7_llf(metrics[10]), speed_div_1000), error_code
    );
    metrics[4] = e7_trunc_float(
        e7_div(
            e7_mul(
                e7_mul(e7_mul(crit_damage, hp), penetration_multiplier),
                percent_damage
            ),
            10.0f
        ),
        error_code
    );
    metrics[3] = e7_trunc_float(
        e7_mul(
            e7_mul(e7_mul(crit_damage, defense), penetration_multiplier),
            percent_damage
        ),
        error_code
    );
    metrics[8] = gear_score_sum;
    #pragma unroll
    for (int skill = 0; skill < 3; ++skill) {
        metrics[12 + skill] = e7_skill_value(
            skill, formula, penetration_on ? 1 : 0, percent_damage,
            skill_kind_codes, skill_hit_type_codes, skill_rates, skill_powers,
            skill_self_hp_scaling, skill_self_attack_scaling,
            skill_self_defense_scaling, skill_self_speed_scaling,
            skill_extra_attack_scaling, skill_extra_defense_scaling,
            skill_increased_values, skill_critical_damage_increases,
            skill_target_defenses, skill_target_counts, skill_penetrations,
            error_code
        );
    }
    #pragma unroll
    for (int metric = 0; metric < 15; ++metric) {
        output_derived_metrics[row * 15 + metric] = metrics[metric];
    }

    output_priority_scores[row] = e7_llf(priority_score_sum);
}
"""


class CudaPackedExactFilterRunner:
    """Reusable one-bit-per-build CUDA filter for exact completed sets."""

    def __init__(self, *, threads_per_block: int = CUDA_PACKED_THREADS) -> None:
        self._threads = _integer(
            threads_per_block,
            "threads_per_block",
            minimum=32,
            maximum=1024,
        )
        if self._threads % 32:
            raise _error(
                "invalid-thread-count",
                "threads_per_block",
                "must be a multiple of CUDA warp size 32.",
            )
        self._api: object | None = None
        self._device_index: int | None = None
        self._kernel: object | None = None
        self._pass_words: object | None = None
        self._pass_capacity = 0
        self._counters: object | None = None
        self._error_code: object | None = None
        self._signature: CudaPackedExactSignature | None = None
        self._closed = False

    def _prepare(
        self,
        device_inputs: CudaDeviceInputs,
        diagnostic: CudaRuntimeDiagnostic,
        word_count: int,
    ) -> CudaPackedExactSignature:
        if self._closed:
            raise _error("runner-closed", "runner", "cannot launch a closed packed runner.")
        if diagnostic.status is not CudaDiagnosticStatus.READY:
            raise _error(
                "cuda-not-ready",
                "diagnostic",
                "packed filtering requires a ready CUDA runtime diagnostic.",
            )
        if diagnostic.selected_device_index != device_inputs.device_index:
            raise _error(
                "device-context-mismatch",
                "diagnostic.selected_device_index",
                "must match the device-input lease.",
            )
        api = device_inputs.array_api
        if self._api is not None and self._api is not api:
            raise _error(
                "array-api-mismatch",
                "device_inputs.array_api",
                "a runner cannot switch array APIs while cached.",
            )
        if self._device_index is not None and self._device_index != device_inputs.device_index:
            raise _error(
                "device-context-mismatch",
                "device_inputs.device_index",
                "a runner cannot switch CUDA devices while cached.",
            )
        self._api = api
        self._device_index = device_inputs.device_index
        signature = CudaPackedExactSignature.compile(device_inputs)
        if self._signature is not None and self._signature != signature:
            self._signature = signature
        elif self._signature is None:
            self._signature = signature
        if self._kernel is None:
            try:
                self._kernel = api.RawKernel(  # type: ignore[attr-defined]
                    CUDA_PACKED_FILTER_KERNEL_SOURCE,
                    CUDA_PACKED_FILTER_KERNEL_NAME,
                    options=("-std=c++17",),
                    backend="nvrtc",
                )
                self._kernel.compile()  # type: ignore[attr-defined]
            except Exception as error:
                raise _error("kernel-compile-failed", "kernel", str(error)) from error
        if self._counters is None:
            self._counters = api.zeros((2,), dtype=_U8)  # type: ignore[attr-defined]
            self._error_code = api.zeros((1,), dtype=_I4)  # type: ignore[attr-defined]
        if self._pass_words is None or word_count > self._pass_capacity:
            self._pass_words = api.empty((word_count,), dtype=_U4)  # type: ignore[attr-defined]
            self._pass_capacity = word_count
        return signature

    def filter(
        self,
        device_inputs: CudaDeviceInputs,
        diagnostic: CudaRuntimeDiagnostic,
        start_index: int,
        stop_index: int,
        *,
        capture_matches: bool = True,
        maximum_captured_matches: int | None = None,
    ) -> CudaPackedFilterBatch:
        if not isinstance(device_inputs, CudaDeviceInputs) or device_inputs.released:
            raise _error(
                "invalid-device-inputs",
                "device_inputs",
                "must be a live CudaDeviceInputs lease.",
            )
        start = _integer(start_index, "start_index")
        stop = _integer(stop_index, "stop_index", minimum=1)
        if stop <= start or stop > device_inputs.total_permutations:
            raise _error(
                "invalid-filter-range",
                "stop_index",
                "must exceed start_index and not exceed total permutations.",
            )
        if not isinstance(capture_matches, bool):
            raise _error("invalid-flag", "capture_matches", "must be a boolean.")
        capture_limit = None
        if maximum_captured_matches is not None:
            capture_limit = _integer(
                maximum_captured_matches,
                "maximum_captured_matches",
            )
        count = stop - start
        word_count = (
            count + CUDA_PACKED_PERMUTATIONS_PER_WORD - 1
        ) // CUDA_PACKED_PERMUTATIONS_PER_WORD
        signature = self._prepare(device_inputs, diagnostic, word_count)
        assert self._api is not None
        assert self._device_index is not None
        assert self._kernel is not None
        assert self._pass_words is not None
        assert self._counters is not None
        assert self._error_code is not None
        api = self._api
        try:
            with api.cuda.Device(self._device_index):  # type: ignore[attr-defined]
                self._counters.fill(0)  # type: ignore[attr-defined]
                self._error_code.fill(0)  # type: ignore[attr-defined]
                args = (
                    np.int64(start),
                    np.int64(count),
                    np.int64(word_count),
                    device_inputs.array("slot_offsets"),
                    device_inputs.array("slot_radices"),
                    device_inputs.array("item_set_indices"),
                    device_inputs.array("item_stat_contributions"),
                    device_inputs.array("item_gear_scores"),
                    device_inputs.array("set_insertion_base_stats"),
                    device_inputs.array("final_stat_multipliers"),
                    device_inputs.array("post_set_modifier_contributions"),
                    device_inputs.array("set_pieces_required"),
                    device_inputs.array("numeric_set_operation_indices"),
                    device_inputs.array("numeric_set_operation_stat_indices"),
                    device_inputs.array("base_stats"),
                    device_inputs.array("artifact_flat_stats"),
                    device_inputs.array("set_unit_numeric_contributions"),
                    device_inputs.array("set_stackable_flags"),
                    device_inputs.array("primary_minimum_values"),
                    device_inputs.array("primary_minimum_present"),
                    device_inputs.array("primary_maximum_values"),
                    device_inputs.array("primary_maximum_present"),
                    device_inputs.array("derived_minimum_values"),
                    device_inputs.array("derived_minimum_present"),
                    device_inputs.array("derived_maximum_values"),
                    device_inputs.array("derived_maximum_present"),
                    device_inputs.array("set_penetration_flags"),
                    device_inputs.array("set_percent_damage_bonuses"),
                    device_inputs.array("penetration_bonus_multiplier"),
                    device_inputs.array("skill_kind_codes"),
                    device_inputs.array("skill_hit_type_codes"),
                    device_inputs.array("skill_rates"),
                    device_inputs.array("skill_powers"),
                    device_inputs.array("skill_self_hp_scaling"),
                    device_inputs.array("skill_self_attack_scaling"),
                    device_inputs.array("skill_self_defense_scaling"),
                    device_inputs.array("skill_self_speed_scaling"),
                    device_inputs.array("skill_extra_attack_scaling"),
                    device_inputs.array("skill_extra_defense_scaling"),
                    device_inputs.array("skill_increased_values"),
                    device_inputs.array("skill_critical_damage_increases"),
                    device_inputs.array("skill_target_defenses"),
                    device_inputs.array("skill_target_counts"),
                    device_inputs.array("skill_penetrations"),
                    np.uint8(signature.set_count),
                    np.uint8(signature.set_indices[0]),
                    np.uint8(signature.set_indices[1]),
                    np.uint8(signature.set_indices[2]),
                    np.uint8(signature.piece_counts[0]),
                    np.uint8(signature.piece_counts[1]),
                    np.uint8(signature.piece_counts[2]),
                    np.uint8(signature.derived_bounds_active),
                    self._pass_words,
                    self._counters,
                    self._error_code,
                )
                blocks = (word_count + self._threads - 1) // self._threads
                self._kernel((blocks,), (self._threads,), args)  # type: ignore[operator]
                counters = np.asarray(api.asnumpy(self._counters), dtype=_U8)  # type: ignore[attr-defined]
                error_code = int(np.asarray(api.asnumpy(self._error_code))[0])  # type: ignore[attr-defined]
                exact = int(counters[0])
                accepted = int(counters[1])
                if error_code:
                    code = (
                        "nonfinite-result"
                        if error_code == 1
                        else "raw-stat-overflow"
                    )
                    raise _error(
                        code,
                        "kernel",
                        "a filtered stat or metric could not be represented.",
                    )
                flat = np.empty((0,), dtype="<i8")
                retained = accepted if capture_limit is None else min(accepted, capture_limit)
                if accepted and capture_matches and retained:
                    words = np.asarray(
                        api.asnumpy(self._pass_words[:word_count]),  # type: ignore[attr-defined,index]
                        dtype=_U4,
                    )
                    nonzero_words = np.flatnonzero(words)
                    selected_words = np.ascontiguousarray(words[nonzero_words])
                    byte_rows = selected_words.view(np.uint8).reshape(-1, 4)
                    popcount = np.unpackbits(
                        byte_rows,
                        axis=1,
                        bitorder="little",
                    ).sum(axis=1)
                    final_word = int(
                        np.searchsorted(
                            np.cumsum(popcount, dtype="<i8"),
                            retained,
                            side="left",
                        )
                    )
                    nonzero_words = nonzero_words[: final_word + 1]
                    byte_rows = np.ascontiguousarray(
                        words[nonzero_words]
                    ).view(np.uint8).reshape(-1, 4)
                    bits = np.unpackbits(byte_rows, axis=1, bitorder="little")
                    selected_rows, selected_bits = np.nonzero(bits)
                    flat = (
                        np.int64(start)
                        + nonzero_words[selected_rows].astype("<i8") * np.int64(32)
                        + selected_bits.astype("<i8")
                    )
                    flat = flat[flat < stop]
                    flat = flat[:retained]
                    if flat.shape[0] != retained:
                        raise _error(
                            "pass-mask-count-mismatch",
                            "pass_words",
                            f"requested {retained} builds but mask contained {flat.shape[0]}.",
                        )
        except CudaPackedFilterError:
            raise
        except Exception as error:
            raise _error("kernel-launch-failed", "kernel", str(error)) from error
        return CudaPackedFilterBatch(start, stop, exact, accepted, flat)

    def close(self) -> None:
        if self._closed:
            return
        self._kernel = None
        self._pass_words = None
        self._counters = None
        self._error_code = None
        self._pass_capacity = 0
        self._closed = True

    def __enter__(self) -> "CudaPackedExactFilterRunner":
        if self._closed:
            raise _error("runner-closed", "runner", "cannot enter a closed packed runner.")
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


class CudaPackedExactMaterializer:
    """Calculate full sortable rows only for flat indices accepted by the filter."""

    def __init__(self, *, threads_per_block: int = CUDA_PACKED_THREADS) -> None:
        self._threads = _integer(
            threads_per_block,
            "threads_per_block",
            minimum=32,
            maximum=1024,
        )
        self._kernel: object | None = None
        self._api: object | None = None
        self._device_index: int | None = None
        self._closed = False

    def materialize(
        self,
        device_inputs: CudaDeviceInputs,
        diagnostic: CudaRuntimeDiagnostic,
        batch: CudaPackedFilterBatch,
    ) -> CudaCompactionHostBatch:
        if self._closed:
            raise _error("runner-closed", "materializer", "cannot use a closed materializer.")
        if not isinstance(device_inputs, CudaDeviceInputs) or device_inputs.released:
            raise _error("invalid-device-inputs", "device_inputs", "must be a live lease.")
        if not isinstance(batch, CudaPackedFilterBatch):
            raise _error("invalid-filter-batch", "batch", "must be CudaPackedFilterBatch.")
        if batch.captured_count != batch.accepted_count:
            raise _error(
                "partial-filter-batch",
                "batch.accepted_flat_indices",
                "completed materialization requires every accepted flat index.",
            )
        if diagnostic.status is not CudaDiagnosticStatus.READY:
            raise _error("cuda-not-ready", "diagnostic", "materialization requires CUDA.")
        api = device_inputs.array_api
        device_index = device_inputs.device_index
        if self._api is not None and self._api is not api:
            raise _error("array-api-mismatch", "device_inputs.array_api", "cannot change APIs.")
        if self._device_index is not None and self._device_index != device_index:
            raise _error("device-context-mismatch", "device_inputs.device_index", "cannot change devices.")
        self._api = api
        self._device_index = device_index
        count = batch.accepted_count
        if count == 0:
            return _empty_compaction_batch(batch)
        try:
            with api.cuda.Device(device_index):  # type: ignore[attr-defined]
                if self._kernel is None:
                    self._kernel = api.RawKernel(  # type: ignore[attr-defined]
                        CUDA_PACKED_MATERIALIZE_KERNEL_SOURCE,
                        CUDA_PACKED_MATERIALIZE_KERNEL_NAME,
                        options=("-std=c++17",),
                        backend="nvrtc",
                    )
                    self._kernel.compile()  # type: ignore[attr-defined]
                accepted = api.asarray(batch.accepted_flat_indices)  # type: ignore[attr-defined]
                dense = api.empty((count, 6), dtype="<i4")  # type: ignore[attr-defined]
                sets = api.empty((count, 6), dtype="u1")  # type: ignore[attr-defined]
                stats = api.empty((count, 8), dtype="<i8")  # type: ignore[attr-defined]
                raw_crit = api.empty((count,), dtype="<i8")  # type: ignore[attr-defined]
                metrics = api.empty((count, 15), dtype="<i8")  # type: ignore[attr-defined]
                priorities = api.empty((count,), dtype="<f4")  # type: ignore[attr-defined]
                error_code = api.zeros((1,), dtype="<i4")  # type: ignore[attr-defined]
                args = (
                    np.int64(count),
                    accepted,
                    device_inputs.array("slot_offsets"),
                    device_inputs.array("slot_radices"),
                    device_inputs.array("dense_item_ids"),
                    device_inputs.array("item_set_indices"),
                    device_inputs.array("item_stat_contributions"),
                    device_inputs.array("item_gear_scores"),
                    device_inputs.array("item_priority_scores"),
                    device_inputs.array("set_insertion_base_stats"),
                    device_inputs.array("final_stat_multipliers"),
                    device_inputs.array("post_set_modifier_contributions"),
                    device_inputs.array("set_pieces_required"),
                    device_inputs.array("numeric_set_operation_indices"),
                    device_inputs.array("numeric_set_operation_stat_indices"),
                    device_inputs.array("base_stats"),
                    device_inputs.array("artifact_flat_stats"),
                    device_inputs.array("set_unit_numeric_contributions"),
                    device_inputs.array("set_stackable_flags"),
                    device_inputs.array("set_penetration_flags"),
                    device_inputs.array("set_percent_damage_bonuses"),
                    device_inputs.array("penetration_bonus_multiplier"),
                    device_inputs.array("skill_kind_codes"),
                    device_inputs.array("skill_hit_type_codes"),
                    device_inputs.array("skill_rates"),
                    device_inputs.array("skill_powers"),
                    device_inputs.array("skill_self_hp_scaling"),
                    device_inputs.array("skill_self_attack_scaling"),
                    device_inputs.array("skill_self_defense_scaling"),
                    device_inputs.array("skill_self_speed_scaling"),
                    device_inputs.array("skill_extra_attack_scaling"),
                    device_inputs.array("skill_extra_defense_scaling"),
                    device_inputs.array("skill_increased_values"),
                    device_inputs.array("skill_critical_damage_increases"),
                    device_inputs.array("skill_target_defenses"),
                    device_inputs.array("skill_target_counts"),
                    device_inputs.array("skill_penetrations"),
                    dense,
                    sets,
                    stats,
                    raw_crit,
                    metrics,
                    priorities,
                    error_code,
                )
                blocks = (count + self._threads - 1) // self._threads
                self._kernel((blocks,), (self._threads,), args)  # type: ignore[operator]
                host_dense = np.asarray(api.asnumpy(dense), dtype="<i4")  # type: ignore[attr-defined]
                host_sets = np.asarray(api.asnumpy(sets), dtype="u1")  # type: ignore[attr-defined]
                host_stats = np.asarray(api.asnumpy(stats), dtype="<i8")  # type: ignore[attr-defined]
                host_raw_crit = np.asarray(api.asnumpy(raw_crit), dtype="<i8")  # type: ignore[attr-defined]
                host_metrics = np.asarray(api.asnumpy(metrics), dtype="<i8")  # type: ignore[attr-defined]
                host_priorities = np.asarray(api.asnumpy(priorities), dtype="<f4")  # type: ignore[attr-defined]
                kernel_error = int(np.asarray(api.asnumpy(error_code))[0])  # type: ignore[attr-defined]
                if kernel_error:
                    code = "nonfinite-result" if kernel_error == 1 else "raw-stat-overflow"
                    raise _error(code, "materializer.kernel", "a result could not be represented.")
        except CudaPackedFilterError:
            raise
        except Exception as error:
            raise _error("materialization-failed", "materializer.kernel", str(error)) from error
        return _compaction_batch(
            batch,
            host_dense,
            host_sets,
            host_stats,
            host_raw_crit,
            host_metrics,
            host_priorities,
        )

    def close(self) -> None:
        self._kernel = None
        self._closed = True

    def __enter__(self) -> "CudaPackedExactMaterializer":
        if self._closed:
            raise _error("runner-closed", "materializer", "cannot enter a closed materializer.")
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


def _counter_arrays(batch: CudaPackedFilterBatch) -> tuple[CudaCompactionHostArray, ...]:
    return (
        CudaCompactionHostArray(
            "category_candidate_counts",
            np.asarray((batch.exact_candidate_count, 0, 0), dtype="<u8"),
        ),
        CudaCompactionHostArray(
            "out_of_scope_count",
            np.asarray((batch.out_of_scope_count,), dtype="<u8"),
        ),
        CudaCompactionHostArray("disabled_category_count", np.zeros((1,), dtype="<u8")),
        CudaCompactionHostArray(
            "hard_bound_rejected_count",
            np.asarray((batch.hard_bound_rejected_count,), dtype="<u8"),
        ),
        CudaCompactionHostArray("tolerance_rejected_counts", np.zeros((3,), dtype="<u8")),
        CudaCompactionHostArray(
            "emitted_counts",
            np.asarray((batch.accepted_count, 0, 0), dtype="<u8"),
        ),
    )


def _compaction_batch(
    batch: CudaPackedFilterBatch,
    dense: np.ndarray[Any, Any],
    sets: np.ndarray[Any, Any],
    stats: np.ndarray[Any, Any],
    raw_crit: np.ndarray[Any, Any],
    metrics: np.ndarray[Any, Any],
    priorities: np.ndarray[Any, Any],
) -> CudaCompactionHostBatch:
    count = batch.accepted_count
    rows = (
        CudaCompactionHostArray("flat_indices", batch.accepted_flat_indices),
        CudaCompactionHostArray("dense_item_ids", dense),
        CudaCompactionHostArray("set_indices", sets),
        CudaCompactionHostArray("category_codes", np.zeros((count,), dtype="u1")),
        CudaCompactionHostArray("replacement_distances", np.zeros((count,), dtype="u1")),
        CudaCompactionHostArray("effective_final_stats", stats),
        CudaCompactionHostArray("raw_critical_hit_chances", raw_crit),
        CudaCompactionHostArray("derived_metrics", metrics),
        CudaCompactionHostArray("priority_scores", priorities),
        CudaCompactionHostArray("constraint_distances", np.zeros((count,), dtype="<f4")),
    )
    return CudaCompactionHostBatch(
        batch.start_index,
        batch.stop_index,
        count,
        0,
        rows + _counter_arrays(batch),
    )


def _empty_compaction_batch(batch: CudaPackedFilterBatch) -> CudaCompactionHostBatch:
    return _compaction_batch(
        batch,
        np.empty((0, 6), dtype="<i4"),
        np.empty((0, 6), dtype="u1"),
        np.empty((0, 8), dtype="<i8"),
        np.empty((0,), dtype="<i8"),
        np.empty((0, 15), dtype="<i8"),
        np.empty((0,), dtype="<f4"),
    )


__all__ = [
    "CUDA_PACKED_DEFAULT_CHUNK_PERMUTATIONS",
    "CUDA_PACKED_FILTER_KERNEL_NAME",
    "CUDA_PACKED_FILTER_KERNEL_SOURCE",
    "CUDA_PACKED_MATERIALIZE_KERNEL_NAME",
    "CUDA_PACKED_MATERIALIZE_KERNEL_SOURCE",
    "CUDA_PACKED_PERMUTATIONS_PER_WORD",
    "CUDA_PACKED_TARGET_PERMUTATIONS_PER_SECOND",
    "CUDA_PACKED_THREADS",
    "CudaPackedExactFilterRunner",
    "CudaPackedExactMaterializer",
    "CudaPackedExactSignature",
    "CudaPackedFilterBatch",
    "CudaPackedFilterError",
]
