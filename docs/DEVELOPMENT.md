# Development

## Setup

Use Python 3.12, Node.js, and the pnpm version pinned in
[`desktop/package.json`](../desktop/package.json). Release builds use the exact
versions in [the release workflow](../.github/workflows/release.yml).

From the repository root:

```powershell
python -m pip install -r requirements-dev.txt
pnpm --dir desktop install --frozen-lockfile
$env:E7_PYTHON = "C:\path\to\python.exe"
pnpm --dir desktop start
```

`E7_PYTHON` is optional if `python` resolves to the intended interpreter. The
desktop launches and supervises its backend. The equivalent command from the
`desktop` directory is `pnpm start`.

The dependency files separate CPU runtime (`requirements-core.txt`), packaging
(`requirements-build.txt`), and tests (`requirements-dev.txt`). `requirements.txt`
combines runtime and packaging. GPU development uses `requirements-cuda.txt`;
install only one CuPy distribution in an environment.

Development data lives under ignored `.local/user-data`; `E7_USER_DATA_DIR` can
select an explicit isolated location. Installed apps use `%APPDATA%\E7 Hub`.

## Code map

| Location | Responsibility |
|---|---|
| [`desktop/src`](../desktop/src) | Electron lifecycle, typed preload bridge, and React interface |
| [`src/desktop`](../src/desktop) | Backend services and desktop protocol |
| [`src/optimizer/domain`](../src/optimizer/domain) | Canonical types, validation, and equipment eligibility |
| [`src/optimizer/data`](../src/optimizer/data) | Catalogs, inventory, profiles, and schema migrations |
| [`src/optimizer/engine`](../src/optimizer/engine) | Stat aggregation, completed sets, metrics, and priority scoring |
| [`src/optimizer/search`](../src/optimizer/search) | Search preparation and deterministic CPU execution |
| [`src/optimizer/cuda`](../src/optimizer/cuda) | Optional GPU runtime and numeric kernels |
| [`src/optimizer/result_store`](../src/optimizer/result_store) | Transactional result storage, paging, filtering, and export |
| [`assets`](../assets) | Application, character, equipment, and documentation artwork |
| [`tests`](../tests) and [`scripts`](../scripts) | Regression fixtures, validation, packaging, and release tools |

The renderer is sandboxed with Node integration disabled. Validate requests and
responses at the preload/backend boundary. Keep domain calculations independent
of UI, persistence, clocks, and device state. Stable IDs identify stored records;
dense numeric IDs belong only to a particular search snapshot.

## Inventory format and persistence

`gear.txt` contains one strict JSON object. UTF-8 with the byte sequence `EF BB BF`
and UTF-8 without a BOM are supported. Duplicate keys and non-finite numbers are
rejected. `items` is required and must be an array; `heroes` is optional, and when
present must also be an array. Items-only files have no inferred ownership.

Each usable item supplies `gear`, `rank`, `set`, `enhance`, `level`, `main`, and
`substats`. Main/substat entries contain `type` and numeric `value`. Optional IDs,
owner references, lock state, roll metadata, and current/reforged summaries are
validated independently. The raw `l` is **not** interpreted as lock state;
`locked` is the explicit lock field. Unknown root, item, stat, and hero keys are
retained as immutable metadata. Missing fields, JSON null, and the `"undefined"`
owner sentinel remain distinguishable.

Fatal document errors precede database creation. Recoverable row failures leave
valid rows usable and produce indexed issues. Re-import matches strong IDs before
content fingerprints, preserves distinct identical items, keeps user metadata,
and retains existing items absent from the new file. Imports persist items,
aliases, owners, and history in one transaction. Reports contain structural
errors and aggregate counts, without raw inventory or file paths.

[`schemas.py`](../src/optimizer/data/schemas.py) owns versioned JSON envelopes;
[`inventory_repository.py`](../src/optimizer/data/inventory_repository.py) owns
SQLite migrations and backups. Newer schemas fail without being overwritten.
Migrations are sequential and must preserve stable identities and optional-value
meaning. Tests use synthetic fixtures and temporary databases, never live data.

## Optimizer invariants

Search preparation applies ownership, enhancement, main-stat, and projection
filters before assigning dense IDs. The desktop searches `+15` gear and supports
optional sets, `4+2`, and `2+2+2` requirements. Item lock state is metadata, not an
automatic exclusion. Each candidate must satisfy every requested set and bound.

The CPU evaluator is the numeric reference. Preserve the defined operation order,
binary32 boundaries, and final rounding in CUDA. Displayed Critical Hit Chance
remains uncapped for filtering and inspection; gameplay formulas use the cap.
Priority sums six independently rounded item scores. Formula details are in the
[metric reference](METRICS.md); the engine and its fixtures define exact arithmetic.

Searches retain at most 5,000,000 exact results. Detect the extra match and report
overflow without publishing a partial run. Cancellation and GPU failure also
discard partial results; CPU recovery starts from the beginning. Results use
fixed-width columns and bounded filtering/export operations. A completed run is
published atomically after validation and hashing. Clean up only proven app-owned
temporary artifacts; preserve unknown and corrupt completed data for diagnosis.

## Catalog and artwork updates

Keep the frozen catalog baseline unchanged. Start new character intake with
[`new-character-template.json`](../support/New-Character/new-character-template.json),
record verified identity, both awakened base-stat profiles, imprints, and skills,
and use the exact English name for the artwork folder. Percentages in the intake
use whole values: `15` means 15%. Leave unsupported skill coefficients unset and
document preview limitations. Build archetypes require separate reviewed build
evidence.

Reviewed additions belong in `manual-heroes-v1.json` and `manual-artifacts-v1.json`
under [`character_data`](../src/optimizer/data/character_data). Artifact records
include code, name, rarity, class restriction, and level-zero Attack/Health/Defense.
Keep runtime metadata, package hashes, and catalog tests aligned with both overlays.

Each character folder uses `pose.webp`, `face_l.webp`, `face_s.webp`, and
`face_su.webp`. Validate image dimensions and transparency; the packaging pipeline
uses WebP quality 90/method 6 and limits poses to 1600 pixels. Retain and update the
asset manifests and `index.csv`. Missing variants remain explicitly marked missing.
Equipment-icon notices remain beside the PNGs; third-party credits are in
[`docs/legal`](legal).

Application icons live in `assets/app`. Documentation screenshots live in
`assets/readme` and can be refreshed with `pnpm --dir desktop docs:capture`.
Use an isolated, empty application state for screenshots.

## Validation

From the repository root:

```powershell
python -m pytest -q
python scripts/smoke_setup_flows.py
python scripts/verify_update_recovery.py --source
pnpm --dir desktop test
pnpm --dir desktop typecheck
node scripts/validate-documentation.cjs
```

`pnpm test` includes retirement, repository, release-tool, and desktop checks.
For documentation or release-tool changes alone, run the existing repository
checks with `pnpm --dir desktop run test:repository` plus documentation validation.
The recovery rehearsal uses temporary paths and verifies backup restore, schema
migration, immutable results, and safe cleanup. Developer benchmarks remain under
`scripts/benchmark_*.py`, with their evidence ignored locally.

## Packaging and releases

`pnpm --dir desktop package` assembles the app; `pnpm --dir desktop make` builds
the Windows installer. Both run the packaging audits. Work is placed under
`.build` and `dist`; audited installers are published locally to `releases/v<version>`
without overwriting an existing version directory.

The CPU package excludes CuPy and NVIDIA component wheels. Its optional installer
helper uses verified CPython/pip archives and an exact hashed dependency graph.
GPU setup runs only after confirmation in app-owned data, without system Python,
the system CUDA Toolkit, or a display-driver installation. After building the
helper, run `python scripts/verify_cuda_installer.py`.

To prepare a release from a clean working tree:

```powershell
node scripts/prepare-release.cjs MAJOR.MINOR.PATCH --title "User-visible change"
```

This updates the desktop version and adds a section to [CHANGELOG.md](../CHANGELOG.md).
Complete that section with the user-visible changes, run validation, and commit
the release. The matching `vMAJOR.MINOR.PATCH` tag triggers the Windows workflow.
The workflow verifies the tag and main-branch ancestry, builds and audits the app,
and publishes the release only after the checks pass. Release notes are generated
from the matching changelog section; no separate per-version document is needed.

Keep design history, investigation notes, and machine-specific evidence under
ignored `.local/`, `phases/`, or `benchmarks/`. Public documentation consists of
the README, user guide, metric reference, this guide, and the changelog.
