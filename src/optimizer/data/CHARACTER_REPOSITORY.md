# Offline character repository

P02-T02 adds a read-only character repository over the versioned canonical
catalog and source-rich sidecar. Constructing the repository performs no
network requests and no work occurs when the module is imported.

## Public contract

`load_bundled_character_repository()` loads and cross-checks both bundled
documents. `CharacterRepository(catalog, source_snapshot)` accepts explicit
typed documents for tests and future controlled refreshes. Construction
rejects provenance, timestamp, count, stable-ID, name, profile, rich-field, or
alias drift with a `CharacterRepositoryError` carrying a stable `code` and
source `path`.

Each immutable `CharacterHeroRecord` exposes:

- canonical hero and profile definitions, stable and dense IDs;
- source name, `_id`, `code`, role, element, rarity, and zodiac;
- name, source-ID, and source-code aliases only;
- source and effective icon, image, and thumbnail references;
- recursively immutable skills, self-imprint, exclusive-equipment, unknown,
  and complete raw source data.

`Arunka` demonstrates the lossless unknown-field boundary: its unexpected
top-level `S2` remains in `unknown_fields` and `raw_source`; only `skills.S2`
is treated as canonical skill data.

## Lookup and ranking

Search text is Unicode NFKC-normalized, case-folded, and every run of
whitespace or punctuation becomes one space. This means `ae-GISELLE`,
`AE GISELLE`, and `ae___giselle` share a lookup form. No unofficial nicknames
are invented.

`get()` resolves a stable hero ID. `find_exact()` resolves a stable ID, name,
source `_id`, or source code. `search()` ranks matches in this fixed order:

1. exact name;
2. exact source ID, source code, or stable ID;
3. name prefix;
4. source-alias prefix;
5. name substring;
6. source-alias substring.

Rank ties use normalized name and then stable hero ID. Blank or
punctuation-only queries return the first 20 heroes in normalized-name order
by default. Callers may request 1 through 100 results; no repository search is
unbounded. Construction rejects normalized aliases claimed by different
heroes and reports every involved stable ID.

## Portrait degradation

Missing, empty, or structurally malformed asset references resolve to the
built-in `HERO_PLACEHOLDER_IMAGE_REFERENCE` SVG data URI while their raw
source values remain available. A caller may supply
`usable_asset_reference(reference)` when it has an offline asset inventory;
false results and callback failures also degrade to the placeholder. The
repository itself never probes a URL or checks image-file existence.

## Representative evidence

Coverage includes `Ras` (old), `Seaside Bellona` (limited), `ae-GISELLE`
(collaboration), and `Adventurer Ras` (specialty change). The exact parent
comparison for cache update commit
`dab0509584b1405aa13f5e1ddbfea9d919269fe8` added two records to
`data/cache/herodata.json`: `Aube` (`c5190`) and `Tidal Rift Elvira` (`c2148`).
Both are tested as the newest representatives; this category is tied to pinned
commit evidence rather than an inferred release date.

