# CUDA search orchestration

`run_controlled_cuda_search()` is the production GPU run boundary. It accepts
validated device inputs and a ready CUDA diagnostic, filters exact builds in
packed 32-permutation masks, and materializes accepted rows only after the
search completes.

Progress is emitted at synchronized chunk boundaries. Cancellation discards
partial rows. The result cap stops at cap+1 without publishing rows. A CUDA
failure also discards partial rows and may expose a CPU recovery action that
starts again from permutation zero.

New runs are exact-only. Three-position category counters and zero-valued
replacement fields remain in the public result ABI solely so existing desktop
and stored-result contracts do not require an incompatible migration.

The focused hardware-independent tests are in
`tests/test_cuda_orchestration.py`; packed kernel tests are in
`tests/test_cuda_packed.py`.
