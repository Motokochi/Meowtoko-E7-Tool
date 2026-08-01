# Meowtoko E7 Tool desktop development

The supported development entry point is Electron Forge. Release users should use the installed application shortcut instead.

## Prerequisites

- Node.js and pnpm compatible with `desktop/package.json`.
- Python 3.12. The dependency entry points are deliberately separate:
  - `requirements-core.txt` is the exactly pinned CPU/runtime graph;
  - `requirements-build.txt` adds the exactly pinned PyInstaller toolchain;
  - `requirements.txt` is the compatibility entry point for core + build; and
  - `requirements-dev.txt` adds the pinned test runner to that graph; and
  - `requirements-cuda.txt` is core + the optional pinned CUDA component.
- Optional Ollama, Tesseract, and ADB installations only for workflows that use them.
- Optional CUDA 13 development environment from `requirements-cuda.txt` for GPU optimizer work; see `src/optimizer/cuda/CUDA_RUNTIME.md`, `src/optimizer/cuda/CUDA_INPUTS.md`, and `src/optimizer/cuda/CUDA_ORCHESTRATION.md`.

Create a CPU-only runtime with `python -m pip install -r requirements-core.txt`.
For normal desktop development and packaging, install `requirements.txt`. For
an isolated GPU runtime, install `requirements-cuda.txt`; it includes the core
graph and `cupy-cuda13x[ctk]==14.1.1`, but not PyInstaller. Never install more
than one CuPy distribution in the same environment.

Developer-only performance harnesses live under `scripts/benchmark_*.py`.
Their reports and machine-specific evidence are local-only and are never
bundled into the desktop package.

For a clean verification environment, install
`python -m pip install -r requirements-dev.txt`.

## Start the desktop application

From the `desktop` directory, install JavaScript dependencies and launch Forge:

```powershell
pnpm install
$env:E7_PYTHON = "C:\path\to\python.exe"
pnpm start
```

`E7_PYTHON` is optional when `python` resolves to the intended interpreter. Forge starts the development backend through the supervised desktop lifecycle; do not start a separate backend process.

Development settings, optimizer data, debug captures, logs, and optional CUDA
components live under ignored `.local/user-data`. An explicit
`E7_USER_DATA_DIR` still selects an isolated development location. Packaged
launches do not use this fallback: Electron supplies the installed
`%APPDATA%\E7 Hub` directory explicitly.

In development, Health Center can use the exact running Python interpreter as
its trusted optional-component helper. It installs the fixed CuPy component
atomically under the isolated E7 user-data directory. The renderer cannot send
a package, URL, command, or destination. Packaged builds use only a helper
under `resources/cuda-installer`; they never fall back to system Python. That
helper is built from the official 64-bit CPython 3.12.10 embeddable archive and
the pip 26.1.2 wheel. Both source archives and every resulting helper file are
SHA-256 verified. The standard backend and ASAR still contain no CuPy or NVIDIA
component wheel. `requirements-cuda-component-lock.txt` is the exact
11-package Windows sidecar graph copied into the helper; setup validates its
SHA-256 and invokes pip with `--no-deps`. The helper retains pip's one required
hash-inventoried x64 console-launcher resource so those fixed wheels can create
their declared entry points. Activation binds the unified CUDA 13 wheel root,
dependent DLL path, headers, and CuPy kernel cache to the validated app-owned
component/user-data directories; it does not use a system CUDA toolkit or the
global user `.cupy` cache.

## Verify changes

From the repository root, run the Python tests:

```powershell
python -m pytest -q
python scripts/smoke_setup_flows.py
python scripts/verify_update_recovery.py --source
```

From `desktop`, run the renderer/backend bridge checks:

```powershell
pnpm test
pnpm typecheck
pnpm audit:legacy
pnpm run build:cuda-installer
```

The first helper build downloads its two fixed archives from Python.org and
PyPI into ignored `.build/downloads`; later builds reuse them only after their
hashes match. Run `python scripts/verify_cuda_installer.py` for the isolated
helper/runtime/inventory smoke.

Create a release package with `pnpm package` or the Squirrel installer with
`pnpm make`. Both build the helper, freeze the backend, and run retirement and
package audits. Packaging fails on Python ABI/tool pins, pnpm lock, helper
manifest, character/artifact hashes, migration authorities, or resource-tree
drift.

Generated work is rooted semantically:

- TypeScript test compilation uses `.build/desktop-tests`;
- PyInstaller work uses `.build/pyinstaller`;
- pinned download cache uses `.build/downloads`;
- Forge package/maker work uses `.build/forge/v<version>`;
- assembled backend, runtime, CUDA-helper, and character resources use `dist`;
  and
- after every audit passes, `pnpm make` atomically publishes the three
  Squirrel update artifacts and `SHA256SUMS.txt` to
  `releases/v<version>`.

The local release publisher never overwrites an existing version directory.
Increment `desktop/package.json` before creating a new release. For an
isolated verification build, `E7_FORGE_OUT_DIR` may select another work
directory; `E7_LOCAL_RELEASE_DIR` must still resolve beneath root `releases`.

The update-recovery verifier launches only the frozen backend with isolated
temporary Windows and E7 user-data roots when an executable path is supplied;
`--source` exercises the identical private protocol when local Application
Control blocks the unsigned executable. It proves migration, explicit
resave, semantic inventory preservation, valid-result retention, stale owned
cleanup, corrupt-run preservation, and graceful process shutdown. Recovery
ownership and the user procedure are documented in [`RECOVERY.md`](../RECOVERY.md).
