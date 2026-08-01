# Optional CUDA Runtime and Diagnostics

`runtime.py` is the P06-T01 optimizer-owned authority for deciding whether a
future search may start in CUDA mode. It imports no CuPy module at process or
optimizer import time. A probe is requested explicitly, returns immutable
evidence, and always selects either a fully ready CUDA mode or a complete CPU
fallback. It does not start a search or retain partial GPU state.

## Optional CUDA 13 environment

The dependency entry points are intentionally separate. `requirements-core.txt`
is the pinned CPU/runtime graph and contains no CuPy, NVIDIA component, or
PyInstaller package. `requirements-build.txt` adds the pinned PyInstaller
toolchain, while `requirements.txt` remains the compatibility entry point for
core + build. GPU development uses the separate core + optional component
environment:

```powershell
python -m pip install -r requirements-cuda.txt
```

`requirements-cuda.txt` includes `requirements-core.txt` and the one-line
`requirements-cuda-component.txt` manifest. As of 2026-07-22, that component
pins `cupy-cuda13x[ctk]==14.1.1`. CuPy's official
[installation guide](https://docs.cupy.dev/en/stable/install.html) and
[14.1.1 PyPI release](https://pypi.org/project/cupy-cuda13x/14.1.1/) identify
`cupy-cuda13x` as the precompiled CUDA 13 wheel and the `[ctk]` extra as the
path that installs the needed NVIDIA CUDA component wheels. Only a compatible
NVIDIA driver is then required; a system CUDA Toolkit and `nvcc` are not.

Only one CuPy distribution may be installed in an environment. Do not combine
`cupy`, `cupy-cuda12x`, and `cupy-cuda13x`. The optional environment is a
developer/runtime input, not permission to copy its binaries into the standard
package. The standard package keeps those component wheels external and
installs them only into app-owned user data after confirmation.

## Desktop component setup

`src.desktop.cuda_setup` owns the optional desktop sidecar contract. It uses
only literal `cuda.install`, `cuda.repair`, and `health.cancel` actions. The
renderer cannot supply a package name, source URL, command, pip argument, or
destination. Installation invokes a fixed helper with pip isolated mode,
binary wheels only, the fixed PyPI index, a 30-minute bound, and the exact
component spec. It stages under app-owned user data, verifies CuPy 14.1.1 with
the matching Python ABI, writes an exact manifest, and atomically replaces the
active component. Cancellation or failure preserves the previous valid
component and leaves CPU mode available.

Development uses the interpreter already supervising the backend. A packaged
app accepts only `resources/cuda-installer/python.exe`; it never falls back to
system Python. The packaged helper is the official 64-bit CPython 3.12.10
embeddable runtime plus pip 26.1.2. Its upstream archives, full file list,
licenses, and hashes are recorded in
`resources/cuda-installer/asset-manifest.json`; the package audit rejects any
missing, orphaned, or changed file. It uses `-I -B`, runs windowless, and exists
only to install or verify the fixed sidecar. The adjacent
`component-requirements.txt` is copied from
`requirements-cuda-component-lock.txt`, contains 11 exact packages, and is
validated against SHA-256
`c39d7b64e59aa31e7125a6efebf4112f8591e42f114f72269f90dec7b0544ed4`.
Setup uses binary wheels plus `--no-deps`, so the graph cannot expand or select
new transitive versions at install time.

Before CuPy exists, Health Center may run the bounded read-only `nvidia-smi`
query for adapter name and driver version. Missing tools, timeout, nonzero exit,
malformed or oversized output, and non-NVIDIA PCs all remain safe CPU outcomes.
An RTX name is only setup guidance; the allocation-based diagnostic below is
still the sole CUDA readiness authority.

CUDA 13.x requires an NVIDIA driver from the 580 family or newer according to
NVIDIA's [minor-version compatibility table](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html).
The diagnostic reports the CUDA driver API and statically linked runtime
versions returned by CuPy. It requires a CUDA 13 runtime and a driver API
capable of the CUDA 13 major family. Successful runtime/device calls and the
allocation probe remain the operational compatibility proof; it does not
guess a display-driver branch from the CUDA API integer.

## Stable outcomes

`diagnose_cuda_runtime()` returns one frozen `CudaRuntimeDiagnostic`:

- `ready`: CUDA 13 is compatible, device zero is queryable, VRAM is reported,
  and a bounded allocation was released successfully;
- `disabled`: `E7_DISABLE_CUDA` is explicitly true;
- `cupy-unavailable`: the optional module or one of its component libraries
  cannot load;
- `no-device`: the runtime loads but exposes no CUDA device;
- `incompatible`: the discovered runtime is not CUDA 13 or the driver API does
  not support that major family;
- `query-failed`: runtime, device, memory, or device-restoration evidence could
  not be obtained safely; or
- `allocation-failed`: the bounded allocation or its cleanup failed.

Only `ready` has `mode=cuda` and `available=true`. Every other status has
`mode=cpu`, a user-readable summary, and failure detail where applicable.
Callers therefore never infer readiness from device count alone.

Ready evidence includes CuPy version, device count, selected device index and
name, free/total VRAM bytes, driver/runtime versions, and allocation-probe size
and success. The current probe is 1 MiB and the API refuses configurations over
64 MiB. It selects device zero temporarily, uses CUDA runtime `malloc/free`,
and restores the process's previous device. Allocation, cleanup, and restore
failures cannot produce a ready result.

Set `E7_DISABLE_CUDA=1` (also accepting `true`, `yes`, or `on`) to deliberately
skip module loading and select CPU mode. This process-local switch is useful
for troubleshooting and deterministic CPU validation; it does not mutate
saved optimizer profiles.

## Desktop and privacy behavior

`HealthSystem.cuda_info()` delegates directly to this authority, so desktop
health and future optimizer execution share the same status and metadata. A
non-ready CUDA capability is optional/degraded: all CPU workflows remain
usable, and no partial GPU work is mixed into a CPU run.

The probe is local and performs no network access, installation, telemetry,
repository access, or persistence. It does not enumerate gear, allocate result
buffers, compile kernels, invoke `nvcc`, modify the NVIDIA driver, or implement
automatic GPU-to-CPU recovery after a search begins. Those execution behaviors
belong to later Phase 06 tasks.
