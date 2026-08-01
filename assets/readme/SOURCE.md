# README screenshot source

These PNGs are direct Electron `capturePage()` images of the current React
renderer at 1440 by 900 in the dark/system theme:

- `overview.png`
- `analyzer.png`
- `enhancer.png`
- `optimizer.png`

They were captured on 2026-07-25 with `pnpm --dir desktop docs:capture`. The
capture uses newly created, isolated Electron and backend data directories.
No imported inventory, saved profile, optimizer result, ADB device serial,
local path, debug artifact, or account data is present.

The capture hook is development-only, writes only to this fixed semantic asset
directory, and is disabled in packaged builds. PNGs are losslessly optimized
after capture.
