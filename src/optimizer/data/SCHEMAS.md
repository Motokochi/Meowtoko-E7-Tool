# Optimizer persistence schemas

These JSON envelopes version persisted data without coupling unrelated data
families. Character catalog and inventory are currently version `1`; optimizer
profile and run manifest are version `7`. Each family has its own schema ID,
version constant, and sequential migration registry in `schemas.py`. There is
intentionally no fabricated pre-version-1 format. A document older than the
current version is accepted only when every intervening migration is
registered. A newer document is rejected with an update-the-application error.

All timestamps are caller-supplied ISO-8601 UTC strings ending in `Z`. Loaders
never read the clock. All domain objects use the strict serializers in
`src.optimizer.domain.records`, and envelope fields not listed below are
rejected. Source-specific fields that the app does not interpret belong in
`source.unknownFields`; that JSON object is recursively frozen after loading
and is emitted unchanged.

## Shared source metadata

Required: `sourceName`.

Optional: `sourceVersion`, `sourceRevision`, `unknownFields` (defaults to an
empty object). Optional string fields may be `null`. Unknown fields may contain
any finite JSON value, but must be nested under `unknownFields`.

## Character catalog

Schema ID: `e7.optimizer.character-catalog`.

Required: `schemaId`, `schemaVersion`, `catalogId`, `generatedAt`, `source`,
`heroes`, and `artifacts`.

`heroes` contains `HeroDefinition` payloads and `artifacts` contains
`ArtifactDefinition` payloads. Hero, profile, and artifact stable IDs must each
be unique across the entire catalog. Supplied dense IDs must also be unique
within their respective namespace. `catalogId` identifies the exact catalog
snapshot referenced by inventory and profile documents.

## Imported inventory

Schema ID: `e7.optimizer.inventory`.

Required: `schemaId`, `schemaVersion`, `inventoryId`, `importedAt`, `source`,
and `items`.

Optional: `characterCatalogId`.

`items` contains `GearItem` payloads. Stable item IDs and supplied dense IDs
must be unique. When a character catalog is supplied to the loader, a present
`characterCatalogId` must match it and every non-null `equippedHeroId` must
resolve to a hero in that catalog. Loading without catalog context remains
supported for offline inspection and migration.

## Optimizer profile

Schema ID: `e7.optimizer.optimizer-profile`.

Required: `schemaId`, `schemaVersion`, `profileId`, `name`, `savedAt`, `source`,
and `configuration`.

Optional: `description`, `characterCatalogId`.

`configuration` is an `OptimizationRequest` payload with `requestId` omitted.
It is reusable configuration, not a historical or future execution. Calling
`create_request` requires a new non-empty request ID. If a character catalog is
supplied, hero, base-profile, artifact, and optional catalog references are
validated.

Version 2 adds independent nullable artifact fields under `modifiers`:
`artifactLimitBreaks`, `artifactAttackOverride`, `artifactHealthOverride`, and
`artifactDefenseOverride`. Overrides are final flat artifact contributions;
they are not percentages and are not stored in `customBonuses`. The version-1
to version-2 migration adds these four fields as null without changing the
existing artifact ID or level. The currently bundled artifact source has no
limit-break effect table, so catalog-context validation rejects a non-null
`artifactLimitBreaks` instead of inventing an effect value.

Version 3 adds typed, independently nullable hero-modifier fields:
`imprintContribution`, `exclusiveEquipmentContribution`, and
`exclusiveEquipmentSkillOptionId`. Contributions contain `statType` plus a
canonical value; percentages are ratios while flat stats retain native units.
The older `imprintBonuses` and `exclusiveEquipmentBonuses` projections remain
for backward compatibility. The version-2-to-version-3 migration adds nulls
and deliberately does not guess typed meaning from an older final-stat map.
When a new typed selection is present, catalog-context validation resolves the
bundled rich hero record and verifies exact hero ownership, imprint grade,
contribution, EE roll, and scoped EE skill option.

Version 4 adds authoritative `customContributions` under `modifiers` and a
required three-record `skillContexts` collection under the optimization
request. Custom contributions preserve flat and percentage Attack, Health, and
Defense as distinct kinds; percentage values are canonical ratios. The older
`customBonuses` map remains a validated percentage-point compatibility
projection and is never used to guess typed meaning. Skill contexts have one
record each for S1, S2, and S3, with independent nullable source option, hit
type, target-count override, penetration override, and required target Defense.

The version-3-to-version-4 migration adds an empty typed custom collection and
copies the legacy global `targetDefense` into each new skill context exactly
once. It leaves every option and override null, so it invents no source
selection. Once migrated, the three explicit context values are authoritative;
changing the legacy global field does not overwrite them. Catalog-context
validation resolves any active source selection against the bundled rich skill
repository and rejects wrong-hero, wrong-skill, stale, EE-namespace, unsupported
hit-type, or invalid penetration state.

Version 5 adds nullable `itemProjectionMode` to the request. Its only concrete
values are `projection.current` and `projection.reforged`. Aggregation requires
one of them, but reusable or historical version-4 documents did not encode a
choice. The deterministic v4-to-v5 migration therefore adds `null`; it does not
infer a choice from item level, source metadata, or projection availability.
Old documents continue through the full sequential migration chain, and an
older version that already contains the newer field is rejected.

Version 6 adds required `gearFilters` to the request. It contains
`rightSideMainStats`, `minimumEnhance`, and `excludedItemIds`. Omitted
Necklace/Ring/Boots entries are unrestricted; supplied selections are non-empty
canonical legal main-stat ID arrays. `minimumEnhance` is inclusive from 0
through 15. Exclusions use source-stable item IDs and are canonicalized without
turning stale IDs into load failures. The v5-to-v6 migration adds an empty
main-stat object, minimum enhancement 0, and an empty exclusion list. It never
infers filters from inventory, item metadata, or the selected set pattern.

Version 7 adds required `maximumReplacementDistance` to the request. It is an
integer from 0 through 2. The v6-to-v7 migration wrote default 2 for historical
profiles. The current desktop normalizes this compatibility field to zero
because new searches are exact-only. Older documents that already contain the
newer field are rejected.

## Run manifest

Schema ID: `e7.optimizer.run-manifest`.

Required: `schemaId`, `schemaVersion`, `runId`, `createdAt`, `completedAt`,
`completionState`, `source`, `requestSnapshot`, `summary`, and `resultStore`.

There are no optional envelope fields. `completionState` is `completed`,
`overflowed`, or `cancelled` and must agree with the `SearchSummary` abort
flags. Request and summary IDs must match. A completed run requires
`resultStore` with a non-empty `reference` and a SHA-256 checksum. An overflowed
or cancelled run requires `resultStore: null`, preventing partial rows from
being represented as complete. Result rows are never embedded in the manifest.

`completedAt` cannot precede `createdAt`. Supplying a character catalog to the
loader validates the request snapshot's hero, base profile, and artifact.
Run-manifest version 2 applies the same four artifact configuration fields to
`requestSnapshot.modifiers`; its version-1 migration adds null defaults.
Version 3 applies the same typed imprint/EE fields and sequential v2-to-v3
migration as optimizer profiles. Version 4 applies the same typed custom and
three-skill-context fields and sequential v3-to-v4 migration. Version 5 adds
the same nullable explicit item-projection selection and sequential v4-to-v5
migration without inventing a run choice. Version 6 adds the same explicit
gear-filter object and sequential v5-to-v6 no-filter migration used by
optimizer profiles.
Version 7 adds the same maximum-replacement-distance selection and sequential
v6-to-v7 default-2 migration used by optimizer profiles.
