# CUDA numeric input contract

`inputs.py` is the P06-T02 boundary between the validated CPU oracle and later
CUDA kernels. It compiles `SearchReadySlotArrays` and a matching
`ExactBuildEvaluationContext` into deeply immutable, C-contiguous NumPy
structure-of-arrays fields. It does not decode flat indices, aggregate a build,
apply bounds, execute a search, or retain results.

Stable item IDs, request/profile IDs, diagnostics, repositories, gear/domain
objects, and result records never enter the host record or device transfer.
Identity, base-stat, projection-mode, and context agreement is checked before
the numeric record is created. Dense IDs are the only item identity used by a
future kernel; the existing CPU reverse map resolves them after results return.

## Widths and dimensions

All formula values are IEEE-754 binary32 (`<f4`). Narrowing rejects a finite
source that would become infinity. Integer conversion rejects booleans,
negative values where prohibited, and any value outside its documented width.
All arrays use C order and immutable `bytes` backing on the host, so callers
cannot re-enable writes through NumPy flags.

Symbols in the table are: `N` = total retained items across six slots, `F=8`
primary stats, `D=15` derived metrics, `S=24` Fribbels sets, `K=3` skills, and
`O=13` numeric-set application operations. Shapes include scalar controls as
one-entry arrays so every transferred field has one uniform array contract.

| Field | dtype | shape | Meaning |
|---|---:|---:|---|
| `slot_offsets` | `<i8` | `(6,)` | Start of each slot in flattened item arrays |
| `slot_radices` | `<i8` | `(6,)` | Weapon through Boots candidate counts |
| `total_permutations` | `<i8` | `(1,)` | Exact six-radix product |
| `dense_item_ids` | `<i4` | `(N,)` | Contiguous numeric item IDs |
| `item_set_indices` | `u1` | `(N,)` | Fribbels set index `0..23` |
| `item_stat_contributions` | `<f4` | `(N,F)` | Pre-set item contributions |
| `item_gear_scores` | `<i4` | `(N,)` | Substat-only gear score |
| `item_priority_scores` | `<i4` | `(N,)` | Independently rounded Fribbels item priority |
| `projection_mode_code` | `u1` | `(1,)` | Current/reforged code |
| `base_stats` | `<f4` | `(F,)` | Selected naked profile base |
| `final_stat_multipliers` | `<f4` | `(F,)` | Final Attack/Health/Defense multipliers |
| `set_insertion_base_stats` | `<f4` | `(F,)` | Pre-item/set aggregation start |
| `post_set_modifier_contributions` | `<f4` | `(F,)` | Additions applied after sets |
| `artifact_flat_stats` | `<f4` | `(F,)` | Artifact Attack/Health/Defense flats |
| `required_piece_counts` | `u1` | `(S,)` | Requested exact set-piece vector |
| `target_activation_counts` | `u1` | `(S,)` | Requested completed-set activations |
| `target_numeric_set_contributions` | `<f4` | `(S,F)` | Requested pattern's primary bonuses |
| `set_pieces_required` | `u1` | `(S,)` | Canonical two/four-piece thresholds |
| `set_stackable_flags` | `u1` | `(S,)` | Whether completed groups retain multiplicity |
| `set_unit_numeric_contributions` | `<f4` | `(S,F)` | One activation against selected base stats |
| `numeric_set_operation_indices` | `u1` | `(O,)` | Set index in CPU expression order |
| `numeric_set_operation_stat_indices` | `u1` | `(O,)` | Destination stat for each ordered operation |
| `set_penetration_flags` | `u1` | `(S,)` | Set supplies the damage penetration effect |
| `set_percent_damage_bonuses` | `<f4` | `(S,)` | Per-activation Rage/Torrent/Fervor bonus |
| `primary_minimum_values` | `<f4` | `(F,)` | Numeric minimum values; zero when absent |
| `primary_minimum_present` | `u1` | `(F,)` | Explicit minimum-presence flags |
| `primary_maximum_values` | `<f4` | `(F,)` | Numeric maximum values; zero when absent |
| `primary_maximum_present` | `u1` | `(F,)` | Explicit maximum-presence flags |
| `derived_minimum_values` | `<f4` | `(D,)` | Numeric derived minimums |
| `derived_minimum_present` | `u1` | `(D,)` | Explicit derived-minimum flags |
| `derived_maximum_values` | `<f4` | `(D,)` | Numeric derived maximums |
| `derived_maximum_present` | `u1` | `(D,)` | Explicit derived-maximum flags |
| `metric_target_defense` | `<f4` | `(1,)` | Shared derived-metric target Defense |
| `penetration_bonus_multiplier` | `<f4` | `(1,)` | Active Penetration multiplier for this target |
| `target_penetration_set_multiplier` | `<f4` | `(1,)` | Requested pattern's applied multiplier |
| `target_percent_damage_multiplier` | `<f4` | `(1,)` | Requested Rage/Torrent/Fervor multiplier |
| `maximum_replacement_distance` | `u1` | `(1,)` | Zero-valued compatibility field |
| `near_set_tolerance` | `<f4` | `(1,)` | Zero-valued compatibility field |
| `skill_indices` | `u1` | `(K,)` | Canonical S1/S2/S3 indices |
| `skill_kind_codes` | `u1` | `(K,)` | Unavailable/damage/support codes |
| `skill_hit_type_codes` | `i1` | `(K,)` | `-1` none or canonical hit code |
| `skill_rates` | `<f4` | `(K,)` | Skill rates |
| `skill_powers` | `<f4` | `(K,)` | Skill powers |
| `skill_self_hp_scaling` | `<f4` | `(K,)` | Self-Health scaling |
| `skill_self_attack_scaling` | `<f4` | `(K,)` | Self-Attack scaling |
| `skill_self_defense_scaling` | `<f4` | `(K,)` | Self-Defense scaling |
| `skill_self_speed_scaling` | `<f4` | `(K,)` | Self-Speed scaling |
| `skill_extra_attack_scaling` | `<f4` | `(K,)` | Extra Attack scaling |
| `skill_extra_defense_scaling` | `<f4` | `(K,)` | Extra Defense scaling |
| `skill_increased_values` | `<f4` | `(K,)` | Skill increased-value term |
| `skill_critical_damage_increases` | `<f4` | `(K,)` | Skill critical-damage increase |
| `skill_target_defenses` | `<f4` | `(K,)` | Per-skill target Defense |
| `skill_target_counts` | `<i4` | `(K,)` | Per-skill target count |
| `skill_penetrations` | `<f4` | `(K,)` | Per-skill penetration |

Blank range sides are represented only by their `*_present` flag. The paired
numeric zero is padding and is never a sentinel. This preserves the semantic
difference between a blank side and a real zero bound.

The host byte count is exact and equals `2,499 + 41N`: four item arrays use
41 bytes per retained item and all shared context arrays use 2,499 bytes.
`CudaHostInputs.byte_count` calculates rather than estimates this value.

## Overflow boundary

CPU Cartesian enumeration intentionally keeps Python's arbitrary-precision
integers. `validate_cuda_search_dimensions()` introduces the narrower CUDA
boundary before any NumPy or device allocation:

- every one of the six radices must be positive;
- the exact product and therefore every global flat index must fit signed
  64-bit (`product <= 2^63-1`);
- slot starts/radices use signed 64-bit; and
- contiguous dense IDs must fit signed 32-bit (`N-1 <= 2^31-1`).

Failures use actionable `permutation-total-overflow`, `dense-id-overflow`, or
field-specific narrowing codes. The caller may retain CPU execution or ask the
user for stricter gear filters; no wrapped or saturated CUDA value is emitted.

## Lazy device ownership and reuse

`CudaDeviceBufferCache` accepts an injected CuPy-compatible array API for
hardware-independent testing. The default loader imports `cupy` only after a
transfer is requested and only after an immutable P06-T01 `ready` diagnostic
authorizes device work. Importing `src.optimizer.cuda` or running CPU search
does not load CuPy.

The cache owns allocations. `CudaDeviceInputs` is one explicit borrowing lease:
use it as a context manager or call `release()`. A live lease blocks every copy,
replacement, and cache close because copying would mutate a running consumer's
inputs. Once released, an identical device index plus exact field-name,
shape, and dtype signature reuses allocations and copies new values into them.
A real CuPy destination uses its direct `ndarray.set()` host-to-device path;
the injected adapter fallback uses `copyto`. This avoids an unnecessary device
staging array and accepts NumPy sources on the actual runtime. A changed layout
allocates and fully copies replacement buffers before releasing
the old layout. Allocation/copy failures release all newly created buffers;
a failed compatible copy discards that cache because its contents may be mixed.
`close()` drops all owned references and calls an injected `release` hook when
one exists. CuPy's own memory-pool retention policy remains CuPy-owned.

The standard frozen desktop package includes this Python boundary and the
P06-T03 through P06-T05 kernel sources, but still excludes CuPy, `cupyx`, and
NVIDIA component packages. Progress, cancellation, recovery, final parity and
tuning, and optional GPU release packaging remain later Phase 06/09 work.
