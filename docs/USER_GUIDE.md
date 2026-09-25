# Meowtoko E7 Tool user guide

Meowtoko E7 Tool is a free, unofficial Windows desktop companion for Epic Seven. Normal
use starts from the **Meowtoko E7 Tool** icon—there is no PowerShell window, system
Python setup, or separately launched backend.

- [Install and start](#1-install-and-start)
- [Import owned gear](#2-import-owned-gear)
- [Configure a build](#4-select-a-hero-and-fixed-configuration)
- [Search and results](#6-run-the-local-search)
- [Enhancement](#8-enhance-gear)
- [GPU setup](#9-cpu-and-optional-gpu-setup)
- [Troubleshooting](#10-troubleshooting)
- [Updates and reinstalling](#updates-and-reinstalling)
- [Data recovery](#data-recovery)

## 1. Install and start

1. Open the project's
   [latest release](https://github.com/Motokochi/Meowtoko-E7-Tool/releases/latest), download
   the versioned `Setup.exe` asset, and double-click it.
2. This free build is unsigned. Windows may show **Windows protected your PC**.
   If the file came from the official release, choose **More info**,
   verify the app name **Meowtoko E7 Tool**, and choose **Run anyway**. Organization
   policy or Smart App Control can block it entirely; Meowtoko E7 Tool does not bypass
   Windows Application Control.
3. Open **Meowtoko E7 Tool** from its desktop or Start menu shortcut.
4. Open **Health Center**. The packaged local backend should be ready. Optional
   ADB, GPU, and other tool capabilities are reported
   separately, so a missing optional tool does not disable CPU optimization,
   manual Analyzer input, or unrelated pages.

Updates, uninstall/reinstall, and ordinary repair preserve app-owned data under
the compatibility path `%APPDATA%\E7 Hub`. This preserves data from E7 Hub
installations. Do not delete that whole folder as routine troubleshooting:
it contains settings, imported inventory, hero profiles, results, and any
optional GPU component. See [Data recovery](#data-recovery) before restoring data.

### Configure ADB

Use **Browse for adb.exe** in Settings and, when more than one device is
connected, configure its serial. Confirm that **ADB automation** is ready in
**Health Center** before using previews or enhancement taps. Manual Analyzer
input and CPU/GPU optimization do not require ADB.

### Compact Analyzer workflow

Enter gear identity and the four substats in the left panel. The latest Gear
Score and archetype matches remain visible on the right. The scoring explanation
is available under **Calculation details**. Narrow windows stack the result
cards below the inputs without horizontal scrolling.

![Analyzer workspace](../assets/readme/analyzer.png)

## 2. Import owned gear

Open **Importer** and choose **Select gear.txt** (or **Import another
gear.txt** after an import), then choose your inventory file in the native
Windows file picker. The app reads a file only after you select it.

Review **Import outcome**. Valid rows are committed even when other rows produce
warnings, rejections, or stable-identity conflicts. A structurally invalid
document is rejected before an inventory database is created.

The reader accepts strict JSON in UTF-8 with or without a UTF-8 BOM. It
supports items-and-heroes, items-only, and enriched records. Unknown or inconsistent row data is reported
instead of silently guessed.

Re-import is a stable merge: matching gear is updated, new gear is added,
previously imported gear that is absent from the new source is retained, and
Meowtoko E7 Tool-owned metadata is preserved. Equipped/locked state is retained when
the source supplies it. A lock is metadata—not an optimizer exclusion.
Reports and history do not retain the source path or raw `gear.txt` contents;
the normalized inventory stays local. Importing gear below `+15` lets Enhancer
resolve a piece by exact item ID, while Optimizer searches only `+15` gear.

## 3. Browse +15 gear

Open **Gear** after importing inventory. This read-only workspace lists only
`+15` equipment and uses the same slot, set, and equipped-character assets as
Optimizer result cards. Search by set, stat, or equipped hero; use the quick
slot and ownership controls; or open **Filters** for set, rarity, main stat,
required substats, level, lock state, and minimum score.

Select any row to inspect the complete current item card. The three sortable
scores always use the piece's reforged projection:

- **RGS** is Gear Score across every substat.
- **CGS** excludes Effectiveness and Effect Resistance.
- **SGS** counts Health, Defense, Effect Resistance, and Speed.

Import a fresh inventory to update this view; the Gear workspace does not edit
or equip items.

## 4. Select a hero and fixed configuration

Select a character above the portrait, then use **Add bonus stats** for the
less-frequently changed modifiers:

1. Search for and select a hero. Choose the exact **Base profile** available
   in the pinned snapshot (normally level 50/five-star fully awakened or level
   60/six-star fully awakened). Reloading resolves the stable profile ID, not a
   name guess.
2. Optionally choose an **Artifact**, set level 0–30, and leave Attack/Health/
   Defense overrides blank for calculated values. **No artifact** contributes
   nothing. Artifact limit-break effects are unavailable in the pinned data
   and are never invented.
3. Select **Imprint grade** or **No self imprint**. Only self-concentration is
   applied; team imprint is not.
4. Select **Exclusive equipment**, its exact stat roll, and optionally the
   independent EE skill slot. **No exclusive equipment** contributes nothing.
   The slot identity is retained, but the snapshot has no authoritative
   formula for its skill effect, so that effect is explicitly unavailable.
5. Enter any typed **Custom bonuses**: flat/percent Attack, flat/percent
   Health, flat/percent Defense, Speed, Critical Hit Chance, Effectiveness,
   Effect Resistance, and final Attack/Health/Defense percent. Blank means not
   applied. Percent fields use percentage points.
6. Expand S1/S2/S3 under **Damage context**. Each skill independently supports
   its base source skill or a listed source option, no hit override or a
   supported hit type, target-count override, penetration override, and target
   Defense. Blank overrides use source evidence. Non-damaging/passive or
   unavailable evidence is shown as such rather than converted into fictional
   damage.

Choose **Save** before searching. Validation stays beside the
offending field; an invalid or newer profile file is not overwritten.

## 5. Define the final build

### Stats and priorities

For each of the eight primary stats, enter an inclusive minimum, maximum, both,
or neither. Blank means **do not care about that boundary**; `0` is a real
value. Set each priority from `-1` through `3`: `3` favors more most strongly,
`0` is neutral, and `-1` makes more of that stat rank lower. `Prio` scores each
piece including its main stat, rounds it independently to a whole number, and
adds the six whole-number scores. All 15
derived metrics have independent inclusive min/max
filters and can also be filtered/sorted in results; they do not have priority
sliders. Every definition is in the [metric reference](METRICS.md).

### Requested sets

Use the three set selectors to choose a set or **None (any set)** in each
position. `None` leaves those gear slots unrestricted; it does not require
setless gear.

- Three `None` selections mean sets do not matter.
- One four-piece selection requires that completed four-piece set and leaves
  the other two pieces unrestricted.
- One four-piece plus one two-piece selection fully constrains all six pieces.
- Up to three two-piece selections can constrain all six pieces.

Stackable two-piece sets can be selected more than once. A selection represents
each required activation, not merely a unique set name. The selected
requirements cannot consume more than six pieces. These cover the traditional
`4+2` and `2+2+2` layouts without forcing either layout when sets do not matter.

### Inventory and projection filters

- **Include equipped** off includes unequipped gear plus gear currently on the
  selected hero; gear on other heroes is excluded. Turn it on to allow gear
  equipped by any hero. Locked gear is not automatically excluded.
- Leave **Use reforged stats** unchecked to evaluate imported current values.
  Check it to use the complete supported reforged projection; it does not
  partially invent missing projections.
- The desktop optimizer accepts **+15 gear only**. Minimum enhancement is no
  longer a user-configurable filter.
- For necklace, ring, and boots, checked **Right-side main stats** are the
  allowed values. Leaving every choice blank for a slot means unrestricted.
- The core request format supports explicit item-ID exclusions, but this
  desktop release does not expose an **Excluded items** control. Do not assume
  locks are exclusions; use equipped, enhancement, main-stat, and projection
  filters when preparing the search.

Every result must complete every selected set requirement. Unselected capacity
can contain any set, and stats are calculated from the sets that each candidate
actually completes. Primary and derived stat boundaries are hard requirements;
leaving a boundary blank is the only way to make it unrestricted.

![Optimizer workspace](../assets/readme/optimizer.png)

## 6. Run the local search

Choose **Start search**. The current desktop request uses **Auto** execution:
it selects CUDA only when the exact app-owned component and bounded readiness
probe succeed; otherwise it starts on CPU. The status badge reports **CUDA
GPU** or **CPU**. If a CUDA run fails, **Retry with CPU** restarts the complete
search from permutation zero—GPU partial results are never mixed with CPU
results. The underlying reproducibility contract also distinguishes explicit
CPU and GPU execution, although this release does not expose a three-way
execution selector in the profile editor.

Exact results share a limit of 5,000,000. The search checks one extra match:
if match 5,000,001 exists, it stops, keeps no partial run, and asks you to
tighten requirements. Exactly 5,000,000 valid matches can be stored, paged,
filtered, inspected, and exported. Useful ways to narrow an overflow are
tighter stat/metric bounds, requested sets, right-side main stats, or Include
equipped.

## 7. Understand and export results

Every result satisfies the selected set requirements with six pieces from your
owned inventory. Open a row to see all six pieces, final primary and derived
stats, requested-range checks, and the sets that build actually completes.
The **CR** column deliberately shows uncapped Critical Hit Chance, including
values above 100, so wasted Critical Hit Chance remains visible. CR filtering,
sorting, detail cards, and exports use that same raw value. Damage, CP, and
other combat metrics still use an effective maximum of 100% Critical Hit
Chance.

Use **Rank by**, direction, primary/derived ranges, priority score, equipped
count, and page size to form an active view. Ordering is stable. Tightening
filters can reuse the completed run. Pages contain at most 1,000 rows and
detail loads only the selected visible row.

The selected-build cards include **Equip** beside **Close cards**. This action
changes equipment ownership only inside Meowtoko E7 Tool: it
reassigns the six selected pieces to the imported instance of the selected
hero and releases that hero's previous local build. It does not tap or change
Epic Seven. If the import contains multiple copies of that hero, choose the
intended **Character copy** in the Equip dialog. Each copy lists its stars,
awakening, and currently equipped gear to help identify it.
Import a fresh `gear.txt` after equipping in the game to replace
the local assignment with current game state. A successful local equip keeps
the current results and selected gear cards visible so they can be used as an
in-game equipment checklist. Results are cleared when another character is
selected or a new search starts.

Choose CSV or JSON beside **Export full view**. The Windows Save dialog owns
the destination. Export streams the complete active filtered/sorted view—not
only the visible page—in bounded chunks. Progress and cancellation remain in
the app; completion reports row count, byte count, and content hash without
exposing the destination to the renderer. Cancellation or failure never
publishes a partial final file.

## 8. Enhance gear

Import a fresh `gear.txt`, prepare a piece below `+15` on the enhancement
screen, and confirm readiness in **Health Center**. The item must exist in your
imported inventory with a consistent enhancement level, rarity, and set.
Missing or inconsistent metadata stops the run.

Enhancer spends one basic powder on each newly opened piece, then raises it
toward the next `+3` checkpoint. If the first powder already crosses a checkpoint,
it does not repeat that upgrade. Only enhancement rolls count toward the five
events; original substats do not.

There are two ways for a piece to survive:

- Its potential GS is at least 58 at the current enhancement checkpoint.
- At least four of the five enhancement rolls land on Speed.

The second rule applies to every piece, including one that misses the GS path.
Enhancer stops spending as soon as neither outcome can still be reached. It
waits for a confirmed checkpoint before continuing.

**Allow destroy clicks** is off on first use. Its checkbox persists after you
change it and becomes off again only when you manually untick it. Starting a
run with destruction enabled still shows the destructive confirmation. With
destruction disabled, a rejected piece stops the run without tapping destroy.
Safe stop is checked before every automation action.

![Enhancer workspace](../assets/readme/enhancer.png)

## 9. CPU and optional GPU setup

CPU optimization is ready in the normal package. On a compatible NVIDIA PC,
Health Center offers **Install GPU components**. The fixed component is free,
downloads more than 1 GB, and can occupy several GB. It is installed only
after confirmation under Meowtoko E7 Tool's preserved app-data directory, along with its
kernel cache. Meowtoko E7 Tool never searches for system Python and does not require or
install a system CUDA Toolkit or `nvcc`; a compatible NVIDIA display driver is
still required.

Installation can be cancelled. A failed/cancelled install leaves CPU available.
If an installed component cannot pass readiness, use **Repair GPU components**.
Meowtoko E7 Tool does not modify the display driver. An RTX/NVIDIA name alone is not a
readiness guarantee: the pinned component must load, query a device, and pass
the bounded allocation probe. Close and reopen the app after repairing a loaded
GPU component before checking its final status.

## 10. Troubleshooting

- **Backend or Health Center unavailable:** close and reopen Meowtoko E7 Tool. If it
  persists, use the recovery guide; do not start old Python/Tk scripts.
- **ADB automation unavailable:** use **Browse for adb.exe** under Settings >
  Android connection, then save and refresh Health Center. Check the preview
  before starting enhancement automation. Manual Analyzer input and optimization
  remain available.
- **File import rejected:** select a supported `gear.txt` and review row-specific
  warnings or rejections. Re-import does not delete unseen existing gear.
- **Profile cannot save/search cannot start:** correct the field-level message;
  make sure each slot has eligible gear after equipped, enhancement, main-stat,
  and projection filters.
- **Results say rerun required:** the new view asks for data outside the
  completed search scope. Start a new search with that category/tolerance.
- **More than 5,000,000 matches:** tighten requirements; no partial rows were
  saved. This differs from a valid exactly-five-million run.
- **GPU not ready or failed:** update/check the NVIDIA driver outside Meowtoko E7 Tool,
  then use Health Center repair. Cancel or choose CPU-safe operation while
  diagnosing; no system toolkit is needed.
- **Unsigned Windows warning:** use **More info → Run anyway** only for the
  official release you intended to install. If Windows policy blocks the app,
  respect that policy—do not disable or bypass Application Control.
- **After reinstall:** data should still be present because uninstall preserves
  `%APPDATA%\E7 Hub`. Follow [Data recovery](#data-recovery) if a validated backup is needed.

For third-party credits and terms, see
[Attribution](legal/ATTRIBUTION.txt).

## Updates and reinstalling

The installed app checks public release metadata at startup and periodically.
When an update is available, it shows the version, release notes, and download
size. Nothing downloads until you choose **Download and restart**. Save your work
first: accepting the update stops active searches, exports, and enhancement jobs.
**Later** dismisses that release; manual checks remain available in Settings.
Offline operation or a failed update check does not disable the installed app.

Uninstall through Windows **Installed apps**. Uninstall removes the application
and shortcuts while preserving `%APPDATA%\E7 Hub` for reinstall. The same location
holds optional GPU components and their cache. Delete it only when you explicitly
want to reset all application data. Reinstalling from the official release does
not require a separate Python or Node.js setup.

## Data recovery

Exit Meowtoko E7 Tool and confirm `e7-core.exe` has stopped before restoring files.
Copy the entire `%APPDATA%\E7 Hub` directory to a separate backup first. Keep the
original backup and restore only the affected component; never copy or replace a
running SQLite database.

| Data | Location | Recovery |
|---|---|---|
| Settings | `settings.json` | Preserve the damaged file under another name, copy the validated `settings.json.bak` to `settings.json`, and restart. |
| Inventory | `optimizer.db` | Preserve the failed database, copy the integrity-checked `optimizer.db.backup-*` created for that migration into place, and restart. |
| Hero profiles | `optimizer_profiles` | Restore only the affected profile from a known-good backup. Ambiguous or newer profiles remain read-only. |
| Results | `optimizer_results\runs` | Preserve completed runs. The app validates them and retains corrupt runs for diagnosis; do not delete unknown folders. |
| GPU component | `components` | Use **Repair GPU components** in Health Center instead of deleting the app-data directory. |

Settings are upgraded in memory and saved explicitly. Before replacing a valid
settings file, the app preserves its previous bytes as `.bak`; damaged settings
can also be preserved as `.corrupt`. Inventory migrations create a separate
SQLite backup and commit all changes together. Restarting after a restore
validates and migrates the database again with a new backup. Compare inventory
totals before importing anything else.

An inventory or profile from a newer app must be opened with the matching or a
newer release. Do not force schema-version numbers or delete every profile to
repair one. Result sort caches are regenerable, but unknown files and completed
results should be preserved. A cancelled GPU repair leaves CPU mode available.
