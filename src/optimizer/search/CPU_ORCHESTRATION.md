# Exact CPU search

`run_exact_cpu_search()` composes prepared slot arrays, exact numeric
evaluation, bounded Cartesian enumeration, and match counting. It has no
repository, desktop, result-store, or UI dependency.

Cancellation is checked before the first batch and after each completed
nonfinal batch. Progress contains counts only and never copies result rows.

The terminal state is one of:

- `completed`, which may expose ordered `ExactBuildRow` values;
- `overflowed`, which stops at cap+1 and exposes no partial rows; or
- `cancelled`, which also exposes no partial rows.

`scripts/benchmark_cpu_optimizer.py` is the opt-in performance harness.
