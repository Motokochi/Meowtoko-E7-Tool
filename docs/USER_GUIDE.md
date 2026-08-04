# Meowtoko E7 Tool user guide

Meowtoko E7 Tool is a free, unofficial Windows desktop companion for Epic Seven. Normal
use starts from the **Meowtoko E7 Tool** icon—there is no PowerShell window, system
Python setup, or separately launched backend.

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
   packet capture, Ollama, Tesseract, ADB, and GPU capabilities are reported
   separately, so a missing optional tool does not disable CPU optimization,
   manual Analyzer input, or unrelated pages.

Updates, uninstall/reinstall, and ordinary repair preserve app-owned data under
the compatibility path `%APPDATA%\E7 Hub`. This preserves data from E7 Hub
installations. Do not delete that whole folder as routine troubleshooting:
it contains settings, imported inventory, hero profiles, results, and any
optional GPU component. See the focused
[`RECOVERY.md`](RECOVERY.md) instructions before restoring data.

### ADB handles screenshots and taps; Npcap handles game data

Every in-app game screenshot is captured through Android Debug Bridge (ADB).
This includes Analyzer auto-detect, every Settings coordinate preview, and
Enhancement automation. Enhancement taps also use ADB; Meowtoko E7 Tool never captures
or clicks a visible Windows game window. Use **Browse for adb.exe** in Settings
and, when more than one device is connected, configure its serial. Confirm
that **ADB automation** is ready in **Health Center** before using these
features. Manual Analyzer input and CPU/GPU optimization do not require ADB.

Live inventory import and exact Enhancer stat reads require **Game packet
capture** to be ready in Health Center. On Windows this uses the bundled Scapy
runtime with a separately installed Npcap driver. Meowtoko E7 Tool opens the official
Npcap download page; it does not redistribute or silently install Npcap.
Restart Meowtoko E7 Tool after installing it.

### Compact Analyzer workflow

The Analyzer keeps the complete single-piece workflow in one compact
workspace. Gear identity and the four substats are entered in the left panel;
the latest Gear Score and archetype matches remain visible on the right.
**Auto-detect gear** fills the same fields from the configured ADB device.
The full Gear Score explanation remains available under **Calculation
details**, and OCR evidence remains available through **Debug** after a
successful automatic capture. Narrow windows stack the result cards below the
inputs without horizontal scrolling.

## 2. Import owned gear

Choose either import path:

- **Capture from game:** make sure Game packet capture is ready, choose
  **Start capturing from game**, fully exit Epic Seven (do not only minimize
  it), reopen it to the main screen, and wait until it fully loads. Choose
  **Done Capturing** to send bounded opaque game-response candidates over HTTPS
  to Meowtoko E7 Tool's stateless AWS service. The service identifies and normalizes the
  account snapshot, then Meowtoko E7 Tool saves it as
  `Documents\MeowtokoE7Hub\gear.txt` and imports it. The AWS service does not
  store the capture.
  Recognized gear is imported at every enhancement level; heroes are included
  only when their current grade is 5★ or 6★.
- **Fribbels file:** choose **Select gear.txt** (or **Import another gear.txt**
  after an import), then choose Fribbels' `gear.txt` in the native Windows file
  picker. Meowtoko E7 Tool neither searches for nor reads a file until you choose it.

Review **Import outcome** after either path. Valid rows are committed even when
other rows produce warnings, rejections, or stable-identity conflicts. A
structurally invalid document is rejected before an inventory database is
created.

The reader accepts strict JSON in UTF-8 with or without a UTF-8 BOM. It
supports the Fribbels scanner form, items-only form, and enriched records used
by the pinned offline format. Unknown or inconsistent row data is reported
instead of silently guessed.

Re-import is a stable merge: matching gear is updated, new gear is added,
previously imported gear that is absent from the new source is retained, and
Meowtoko E7 Tool-owned metadata is preserved. Equipped/locked state is retained when
the source supplies it. A lock is metadata—not an optimizer exclusion.
Reports and history do not retain the source path, raw `gear.txt` contents, or
raw packet traffic; the normalized inventory stays local. Live packet import
transmits only bounded response candidates from the two supported game ports
to the stateless service. Importing gear below `+15` lets Enhancer resolve a piece's set by exact
item ID, while Optimizer continues to search only `+15` gear.

## 3. Browse +15 gear

Open **Gear** after importing inventory. This read-only workspace lists only
`+15` equipment and uses the same slot, set, and equipped-character assets as
Optimizer result cards. Search by set, stat, or equipped hero; use the quick
slot and ownership controls; or open **Filters** for set, rarity, main stat,
required substats, level, lock state, and minimum score.

Select any row to inspect the complete current item card. The three sortable
scores always use the piece's reforged projection:

- **RGS** is Fribbels Gear Score across every substat.
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
`0` is neutral, and `-1` makes more of that stat rank lower. `Prio` uses the
Fribbels item-scoring model: each piece includes its main stat, is independently
rounded to a whole number, and the six whole-number scores are added. All 15
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

The selected-build cards include **Equip** beside **Close cards**. Like
Fribbels, this action changes equipment ownership only inside Meowtoko E7 Tool: it
reassigns the six selected pieces to the imported instance of the selected
hero and releases that hero's previous local build. It does not tap or change
Epic Seven. Import a fresh `gear.txt` after equipping in the game to replace
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

Import a fresh `gear.txt`, prepare a piece below `+15` on Epic Seven's
enhancement screen, and confirm both **Game packet capture** and **ADB
automation** are ready. Enhancer performs taps through ADB while Meowtoko E7 Tool's
stateless AWS service privately identifies and normalizes the matching game
response. Captured responses are not stored there. The item ID must exist in imported inventory so Meowtoko E7 Tool can obtain the
piece's previous enhancement level, rarity, and set. Missing or inconsistent
metadata stops the run; the Enhancer does not use OCR or AI as a fallback.

Enhancer spends one basic powder on each newly opened piece to obtain its exact
item ID and packet history. It then raises the piece to its next `+3`
checkpoint. If that one-powder identification already crossed a checkpoint,
Meowtoko E7 Tool uses the resulting roll and does not click the same target again. At
each of `+3`, `+6`, `+9`, `+12`, and `+15`, the newest `op` entry is counted as
that checkpoint's event. Earlier events are reconstructed from the same packet
using the imported rarity and previous enhancement level. Original substats
never enter the five-event count, so Heroic and other non-Epic starting layouts
are handled without pretending their initial substats were enhancement events.

There are two ways for a piece to survive:

- Its potential GS is at least 62 after the initial `+3` and remains at least
  58 as enhancement continues.
- At least four of the five enhancement rolls land on the same non-flat stat.

The second rule applies to every piece, including one that misses the GS path.
Enhancer stops spending as soon as neither outcome can still be reached. If a
checkpoint packet does not arrive within two seconds, it continues waiting
without repeating the enhancement clicks or reading approximate screen values.

**Allow destroy clicks** is off on first use. Its checkbox persists after you
change it and becomes off again only when you manually untick it. Starting a
run with destruction enabled still shows the destructive confirmation. With
destruction disabled, a rejected piece stops the run without tapping destroy.
Safe stop is checked before every automation action.

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
the bounded allocation probe.

## 10. Troubleshooting

- **Backend or Health Center unavailable:** close and reopen Meowtoko E7 Tool. If it
  persists, use the recovery guide; do not start old Python/Tk scripts.
- **Ollama unavailable:** automatic Analyzer interpretation is isolated; manual
  evaluation and the optimizer remain available. Install/start Ollama from
  Health Center and refresh.
- **Tesseract unavailable:** manual Analyzer input remains available. Install a
  trusted Windows build, configure it in Settings if needed, then refresh.
- **ADB capture or automation unavailable:** use **Browse for adb.exe** under
  Settings > Android connection, then save and refresh Health Center. Analyzer
  auto-detect, coordinate previews, and enhancement automation require a ready configured ADB device.
  Manual Analyzer input and optimization remain available. Never continue
  destructive taps when the ADB preview is wrong.
- **Game packet capture unavailable:** install Npcap from the official page
  opened by Health Center, restart Meowtoko E7 Tool, then refresh health.
- **Live import has no account packet:** fully reopen Epic Seven to the main
  screen, then choose **Start capturing from game** and retry.
- **Enhancer cannot confirm a checkpoint:** leave the game on the enhancement
  screen and verify packet capture. The app retries the same enhancement target
  and stops safely instead of falling back to approximate stat OCR.
- **File import rejected:** confirm you selected Fribbels `gear.txt`, not an export
  from another tool, and review row-specific warnings/rejections. Re-import is
  safe and does not delete unseen existing gear.
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
  `%APPDATA%\E7 Hub`. Follow `RECOVERY.md` if a validated backup is needed.

For source identity, legal context, and third-party terms, see
[Attribution](legal/ATTRIBUTION.md). For installer-specific details, see
[`INSTALLING.md`](INSTALLING.md).
