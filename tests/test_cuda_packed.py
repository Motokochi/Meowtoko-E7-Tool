from __future__ import annotations

import unittest

import numpy as np

from src.optimizer.cuda.packed import (
    CUDA_PACKED_FILTER_KERNEL_SOURCE,
    CudaPackedExactSignature,
    CudaPackedFilterError,
)


class _Inputs:
    def __init__(self, required: tuple[int, ...]) -> None:
        self.required = np.asarray(required, dtype="u1")
        self.maximum_replacement_distance = 0

    def host_array(self, name: str) -> np.ndarray:
        if name == "required_piece_counts":
            return self.required
        if name in {"derived_minimum_present", "derived_maximum_present"}:
            return np.zeros((15,), dtype="u1")
        raise KeyError(name)


class CudaPackedFlexibleSetTests(unittest.TestCase):
    def test_signature_accepts_no_set_and_partial_set_requirements(self) -> None:
        unrestricted = CudaPackedExactSignature.compile(_Inputs((0,) * 24))  # type: ignore[arg-type]
        self.assertEqual(0, unrestricted.set_count)
        self.assertEqual((0, 0, 0), unrestricted.piece_counts)

        partial = [0] * 24
        partial[10] = 4
        compiled = CudaPackedExactSignature.compile(_Inputs(tuple(partial)))  # type: ignore[arg-type]
        self.assertEqual(1, compiled.set_count)
        self.assertEqual((10, 0, 0), compiled.set_indices)
        self.assertEqual((4, 0, 0), compiled.piece_counts)

    def test_signature_rejects_requirements_over_six_pieces(self) -> None:
        impossible = [0] * 24
        impossible[2] = 4
        impossible[3] = 4
        with self.assertRaises(CudaPackedFilterError):
            CudaPackedExactSignature.compile(_Inputs(tuple(impossible)))  # type: ignore[arg-type]

    def test_kernel_derives_activations_from_each_builds_owned_sets(self) -> None:
        self.assertIn("e7_activation(", CUDA_PACKED_FILTER_KERNEL_SOURCE)
        self.assertIn("set_counts[0] >= target_pieces_0", CUDA_PACKED_FILTER_KERNEL_SOURCE)
        self.assertIn("set_unit_values[set_index * 8 + stat]", CUDA_PACKED_FILTER_KERNEL_SOURCE)
        self.assertNotIn("target_set_contributions", CUDA_PACKED_FILTER_KERNEL_SOURCE)
        self.assertNotIn("target_activations", CUDA_PACKED_FILTER_KERNEL_SOURCE)

    def test_kernel_bounds_critical_hit_chance_with_its_raw_value(self) -> None:
        self.assertIn(
            "const long long range_value = stat == 4 ? raw[stat] : bounded;",
            CUDA_PACKED_FILTER_KERNEL_SOURCE,
        )


if __name__ == "__main__":
    unittest.main()
