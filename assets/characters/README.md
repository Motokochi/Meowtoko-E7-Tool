# E7 Codex character artwork

This directory contains compact, pinned base-character images derived from
[E7 Codex](https://e7codex.com/). Each readable character folder contains up
to four standardized WebP files:

- `pose.webp` - transparent character pose, bounded to 1600 pixels
- `face_l.webp` - large face/banner variant
- `face_s.webp` - square face variant
- `face_su.webp` - alternate face variant

For example:

```text
assets/characters/Aube/
  pose.webp
  face_l.webp
  face_s.webp
  face_su.webp
```

`asset-manifest.json` records the optimizer character code, resolved E7 Codex
asset code, original source URL, compact local path, availability, dimensions,
byte size, and SHA-256 hash for every expected file. Each available record also
retains the original PNG path, dimensions, byte size, and SHA-256.
`raw-source-manifest.json` is the exact original download manifest authenticated
by `asset-manifest.json.packaging.sourceManifestSha256`. `index.csv` is a
compact human-readable inventory of the repository WebP assets.

The Fribbels catalog calls Archdemon Mercedes `Archdemon's Shadow` and assigns
her `c5004`; E7 Codex publishes the artwork under `m9194`. The downloader keeps
the readable optimizer folder name and records the resolved alias explicitly.
Entries that E7 Codex does not publish remain in the manifest with a `missing`
status instead of receiving unrelated placeholder art.

At the current pin, E7 Codex has no indexed artwork record for Desert Jewel
Basar (`c2053`), Mighty Scout (`m0063`), or Wild Angara (`m0171`). All four
expected variants for those three entries are therefore recorded as missing.

To refresh the raw source library from the pinned Meowtoko E7 Tool character catalog:

```powershell
python scripts/download_e7codex_character_assets.py
```

Raw downloads go to ignored `.build/downloads/e7codex-characters`; they never
replace the public asset baseline implicitly. Review that raw manifest, then
build a candidate without touching the repository assets:

```powershell
python scripts/build_packaged_character_assets.py `
  --source .build/downloads/e7codex-characters `
  --output .build/packaged-character-assets
```

The builder uses WebP quality 90/method 6, preserves transparency, and bounds
poses to 1600 pixels while retaining complete raw-source provenance. Promotion
into this directory is a reviewed repository change: retain this README,
`index.csv`, the raw manifest as `raw-source-manifest.json`, and the packaged
manifest's exact source-manifest hash.

Epic Seven and its characters and artwork are the intellectual property of
Smilegate Holdings, Inc. and Super Creative. E7 Codex is an independent fan
archive and does not claim ownership of the game artwork. These copies are
retained for the free, unofficial Meowtoko E7 Tool fan tool under the project owner's
stated fair-use assessment. Preserve this attribution when redistributing the
asset library.
