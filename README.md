<p align="center">
  <img src="assets/app/meowtoko-e7-tool.png" width="112" alt="Meowtoko E7 Tool violet crystal icon">
</p>

# Meowtoko E7 Tool

A free, unofficial Windows companion for Epic Seven. Manage owned gear, compare
builds, analyze equipment, and automate enhancement from one desktop app.

**[Download the latest release](https://github.com/Motokochi/Meowtoko-E7-Tool/releases/latest)**

![Meowtoko E7 Tool desktop workspace](assets/readme/overview.png)

## Get started

1. Download the `Setup.exe` asset from the latest release and run it.
2. Open the installed Meowtoko E7 Tool shortcut.
3. Check **Health Center**, then use **Importer → Select gear.txt** to load your
   inventory.
4. Open **Optimizer**, choose a character and build requirements, and start a search.

Requires Windows 10 or 11 on x64 hardware. The installer includes the app runtime;
you do not need Python, Node.js, or a terminal. This build is unsigned: if Windows
shows **Windows protected your PC**, verify the download and follow your device's
security policy.

## Features

| Workspace | What it does |
|---|---|
| **Gear** | Browse and filter owned `+15` equipment, inspect stats, and compare scores. |
| **Optimizer** | Search six-piece builds on CPU or optional NVIDIA CUDA; filter, sort, and export results. |
| **Analyzer** | Evaluate gear from manual input with Gear Score and archetype recommendations. |
| **Enhancer** | Run bounded ADB enhancement with progress, safe stop, and explicit destruction confirmation. |
| **Settings & Health Center** | Configure appearance and device controls; check and repair optional tools. |

CPU optimization is included. ADB is needed for device controls; CUDA acceleration
requires a compatible NVIDIA GPU and an optional component installation. Missing
optional tools do not block unrelated features.

## Documentation

- [User guide](docs/USER_GUIDE.md): installation, workflows, updates, troubleshooting,
  and recovery.
- [Metric reference](docs/METRICS.md): scores, formulas, and stat limits.
- [Development](docs/DEVELOPMENT.md): setup, architecture, data updates, tests, and releases.
- [Changelog](CHANGELOG.md): changes by version.

Updates and reinstalls preserve settings, inventory, and profiles under
`%APPDATA%\E7 Hub`. See [data recovery](docs/USER_GUIDE.md#data-recovery) before
replacing or deleting app data.

## Project status

Meowtoko E7 Tool is not affiliated with or endorsed by Smilegate, Super Creative,
or the Epic Seven rights holders. Game names, characters, statistics, and artwork
remain the property of their respective owners.

The application package declares `UNLICENSED`. Third-party components retain
their own licenses; see [attribution](docs/legal/ATTRIBUTION.txt) and
[runtime notices](docs/legal/THIRD_PARTY_NOTICES.txt).
