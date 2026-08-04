# Offline character snapshot

P02-T01 vendors and normalizes the English hero and artifact caches from the
Fribbels offline fork. Runtime loaders read only the bundled files in
`character_data/`; they do not contact GitHub, Amazon S3, Azure, EpicSevenDB,
or any other service.

## Immutable source and lineage

- Repository: `RexQian/Fribbels-Epic-7-Optimizer`
- Repository URL: <https://github.com/RexQian/Fribbels-Epic-7-Optimizer>
- Branch used for discovery: `feat/offline`
- Pinned revision: `f49b0676c27d893ae4aa1b69920e4c98f37eb3fb`
- Revision date: `2026-07-16T16:00:28Z`
- Cache update commit: `dab0509584b1405aa13f5e1ddbfea9d919269fe8`
  (`update: patch 20260716`, `2026-07-16T15:29:30Z`)
- Offline app version declared in `app/package.json`:
  `1.11.0-offline.20260108`

The authoritative inputs at that revision are:

| Record kind | Upstream path | Git blob SHA-1 | Raw SHA-256 | Records |
| --- | --- | --- | --- | ---: |
| Hero | `data/cache/herodata.json` | `2364040f3d9424653877745fc2883ecdd632afb5` | `a5ed0b641e578a2b290b75d6f75a866a93b91e40c1064a4f1a264630a745c349` | 386 |
| Artifact | `data/cache/artifactdata.json` | `3cf3f766b1117caac64e5d7da9f4fc8b42781054` | `ed1bb666ae7465560fbc1a163000966821174b0a48be826b28da16021f463ac0` | 283 |

At the pinned revision, `app/js/lib/heroData.js` first reads these local cache
files and can then replace them from Fribbels' Amazon S3 or Azure cache
endpoints when local-cache mode is disabled. Its dormant `manualFetchData`
helper documents `https://api.epicsevendb.com/hero/` as the hero API lineage;
the cache itself does not embed a separately pinned EpicSevenDB revision.
Therefore the immutable Fribbels Git blobs above, rather than a floating cache
endpoint or inferred EpicSevenDB state, are the inputs of record.

## Mapping to Phase 00

- Hero source key must equal `name`; `_id` becomes
  `hero.fribbels.<source-id>`.
- `lv50FiveStarFullyAwakened` becomes the explicit level-50/five-star profile;
  `lv60SixStarFullyAwakened` becomes the level-60/six-star profile.
- `atk`, `hp`, `def`, and `spd` map directly to the corresponding final stats.
- `chc`, `chd`, `eff`, and `efr` are fractions in the cache. They use the exact
  Fribbels `Math.round(value * 100)` conversion documented in
  `app/js/lib/heroData.js`.
- Artifact source key must equal `name`. Fribbels artifact `code` is not unique
  in this cache, so the canonical stable ID combines code, normalized name, and
  an eight-character SHA-256 name suffix.
- Artifact base Attack/Health come from `stats`. `app/js/lib/artifact.js`
  defines maximum level 30 and maximum stats as exactly 13 times base stats.

The Phase 00 contracts cannot carry hero assets, role, element, zodiac,
skills, self-imprint, exclusive equipment, source aliases, or artifact
Defense. The versioned `character-source-v1.json` sidecar preserves every raw
record and field recursively and immutably. Six nonzero artifact Defense
values and twelve records sharing six artifact codes are explicit warnings in
the validation report; no source record is dropped.

Dense IDs in the canonical catalog are deterministic implementation indexes:
successful heroes and artifacts are assigned in sorted source-key order, and
the two profiles for each successful hero receive contiguous IDs. They are not
copied into the source-rich snapshot and are not yet a search index.

## Bundled files

- `character-catalog-v1.json`: Phase 00 `CharacterCatalogDocument`.
- `character-source-v1.json`: versioned immutable, source-rich sidecar.
- `character-validation-v1.json`: one normalization outcome per source key,
  including warnings and structured rejection details.
- `source/herodata.json` and `source/artifactdata.json`: exact input bytes for
  offline reproduction.
- `manifest-v1.json`: generator identity plus SHA-256 and byte length for both
  inputs and all three generated outputs.

Generated JSON is UTF-8, compact, key-sorted, and contains no local absolute
paths. The pinned build/fetch timestamp is `2026-07-20T00:00:00Z`; the
generator never reads a clock.

## Future heroes

This Fribbels snapshot is a frozen legacy baseline and must not be refreshed to
add later characters. Future heroes originate from the user-maintained intake
contract in `support/New-Character/new-character-template.json` and a matching
`assets/characters/<Exact Character Name>/` raw artwork folder. Build evidence
is supplied separately as a Hero Journal Discord screenshot and is never
guessed from the character intake file.

## Attribution

The source project identifies Fribbels as its author, the offline fork is
maintained by RexQian, and `app/package.json` declares `MIT`. The pinned root
tree does not contain a standalone `LICENSE` file, so this provenance records
the package declaration without inventing missing license text. Epic Seven
names, statistics, and artwork remain the property of their respective rights
holders; this free application uses public catalog data with attribution under
the user's stated fair-use basis.

## Exact regeneration command

Run from the repository root. Inputs and every output path are explicit:

```powershell
python scripts/generate_character_snapshot.py `
  --heroes-input src/optimizer/data/character_data/source/herodata.json `
  --artifacts-input src/optimizer/data/character_data/source/artifactdata.json `
  --output-root src/optimizer/data/character_data `
  --catalog-output character-catalog-v1.json `
  --source-output character-source-v1.json `
  --validation-output character-validation-v1.json `
  --manifest-output manifest-v1.json `
  --vendored-heroes-output source/herodata.json `
  --vendored-artifacts-output source/artifactdata.json `
  --generated-at 2026-07-20T00:00:00Z `
  --fetched-at 2026-07-20T00:00:00Z
```

The command rejects source bytes whose SHA-256 differs from the pinned inputs.
`--allow-unpinned-inputs` exists only for structured-rejection fixture tests.
Never edit generated snapshot files by hand.
