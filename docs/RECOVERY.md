# Meowtoko E7 Tool data recovery

Meowtoko E7 Tool updates preserve app-owned data under the E7 Hub-compatible path
`%APPDATA%\E7 Hub`. Never delete
that whole directory as a troubleshooting shortcut: it can contain settings,
owned gear, hero optimizer profiles, completed result runs, and the optional
GPU component.

## Before recovering anything

1. Exit Meowtoko E7 Tool normally.
2. In Task Manager, confirm that neither **Meowtoko E7 Tool** nor `e7-core.exe` remains.
3. Copy `%APPDATA%\E7 Hub` to a separate recovery location. Do not work on the
   only copy.
4. Restore only the affected file described below. Never restore or copy an
   SQLite database while Meowtoko E7 Tool is running.

The authoritative personal data is:

- `settings.json` and its adjacent `.bak`/`.corrupt` recovery copies;
- `optimizer.db` and adjacent `optimizer.db.backup-*` migration backups;
- the JSON files below `optimizer_profiles`;
- completed directories below `optimizer_results\runs`; and
- `components`, if the optional GPU component was installed.

`optimizer_result_sort_cache` is regenerable. Result writer, lock, staged,
cache, and export temporaries are disposable only when Meowtoko E7 Tool can prove their
exact owned structure and stale age. Unknown files, malformed completed runs,
links, and directories with extra files are preserved rather than guessed at.

## Settings

Settings upgrades are applied in memory and are written only after an explicit
save. Before replacing a valid primary, Meowtoko E7 Tool atomically publishes its exact
previous bytes as `settings.json.bak`. If the primary is malformed, Meowtoko E7 Tool
loads a valid backup when possible. Saving recovered defaults preserves the
malformed primary once as `settings.json.corrupt`.

If automatic backup loading is not enough, keep the recovery copy made above,
rename the stopped app's current `settings.json` to a diagnostic name, copy
`settings.json.bak` to `settings.json`, and restart. A newer-schema settings
file is deliberately read-only; update Meowtoko E7 Tool instead of overwriting it.

## Owned inventory database

Before upgrading an older `optimizer.db`, Meowtoko E7 Tool creates an adjacent,
integrity-checked `optimizer.db.backup-*` with SQLite's backup API. The schema
transaction either commits completely or leaves the original at its old
version. Existing backup names are never overwritten.

To restore after a reported migration failure:

1. Keep Meowtoko E7 Tool stopped and retain the full recovery copy.
2. Rename the failed `optimizer.db` rather than deleting it.
3. Copy the backup path created for that failed migration to `optimizer.db` in
   the same directory. Do not move the only backup.
4. Restart Meowtoko E7 Tool. It validates both schema markers, replays the migration, and
   creates another non-overwriting backup before committing.
5. Open Optimizer and compare its total and six slot counts before importing
   anything new.

If the database is from a newer Meowtoko E7 Tool or its two schema markers disagree, it
is not modified. Install the matching/newer app or retain the files for
diagnosis; do not force a version number.

## Optimizer profiles

Each hero profile is an atomic JSON document. Reading an older v1–v6 profile
migrates it in memory without changing the file. An explicit save publishes
the current v7 document through a same-directory temporary replacement. A
failed replacement retains the last good primary and removes only its owned
temporary.

For v1/v2 profiles, legacy imprint and exclusive-equipment projections are
recovered only when the bundled hero catalog resolves them to exactly one
current selection and roll. Ambiguous, tampered, malformed, catalog-invalid,
or future-version profiles remain byte-for-byte unchanged and read-only. Keep
such a file and restore a known-good copy; deleting `optimizer_profiles` would
discard every hero configuration.

## Optimizer results and optional GPU data

Completed result runs are immutable and hash checked. They survive updates and
backend restarts, but a new process does not pretend an old result page is an
active UI session. Corrupt completed runs are hidden and preserved for
diagnosis. Meowtoko E7 Tool removes only proven stale incomplete artifacts according to
its ownership and retention policy; do not manually remove unknown result
directories based only on age or name.

Use Health Center's **Repair GPU components** action for a broken optional CUDA
component. A cancelled or failed repair leaves CPU mode usable and restores a
previous valid component when available. Do not delete the whole app-data
directory to repair CUDA.

## Verified recovery rehearsal

The synthetic recovery corpus is indexed by
`tests/fixtures/recovery/manifest.json`. Tests cover empty first run, settings
v0→v1, inventory v0→v1 with non-overwriting SQLite backup restore, every
profile and run-manifest version v1→v7, interrupted writes, future/mismatched
schemas, immutable completed results with reproduction evidence, stale owned
cleanup, and fail-closed corrupt-run preservation.

`python scripts/verify_update_recovery.py <path-to-e7-core.exe>` performs the
same update rehearsal through a frozen backend. `--source` uses the identical
private protocol when local Application Control blocks an unsigned executable.
Both modes use temporary `APPDATA`, `LOCALAPPDATA`, `USERPROFILE`, user-data,
settings, inventory, profile, and result paths. They never launch Electron or
touch live Meowtoko E7 Tool data.
