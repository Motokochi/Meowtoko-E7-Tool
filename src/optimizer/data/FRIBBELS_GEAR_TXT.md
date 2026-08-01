# Fribbels `gear.txt` source contract

This document defines the raw Fribbels JSON shapes covered by the Phase 01
import corpus. It is a source-format contract, not an optimizer persistence
schema and not a normalization implementation.

## Provenance

The contract was inspected at the `feat/offline` branch revision
[`f49b0676c27d893ae4aa1b69920e4c98f37eb3fb`](https://github.com/RexQian/Fribbels-Epic-7-Optimizer/tree/f49b0676c27d893ae4aa1b69920e4c98f37eb3fb).
The relevant upstream evidence is:

- [`scanner.js`](https://github.com/RexQian/Fribbels-Epic-7-Optimizer/blob/f49b0676c27d893ae4aa1b69920e4c98f37eb3fb/app/js/lib/scanner.js#L342-L343)
  writes an object whose `items` and `heroes` properties are JSON arrays. The
  conversion routines later in that file add the normalized item and ownership
  fields while retaining scanner-native keys.
- [`importer.js`](https://github.com/RexQian/Fribbels-Epic-7-Optimizer/blob/f49b0676c27d893ae4aa1b69920e4c98f37eb3fb/app/js/lib/importer.js#L309-L336)
  reads a selected `.txt` file, parses JSON, and consumes `items` and `heroes`.
  Its screenshot import paths also establish an older items-only form.
- [`files.js`](https://github.com/RexQian/Fribbels-Epic-7-Optimizer/blob/f49b0676c27d893ae4aa1b69920e4c98f37eb3fb/app/js/lib/files.js#L19-L56)
  reads and writes UTF-8 text and creates an empty `{items, heroes}` save.
- [`itemSerializer.js`](https://github.com/RexQian/Fribbels-Epic-7-Optimizer/blob/f49b0676c27d893ae4aa1b69920e4c98f37eb3fb/app/js/lib/itemSerializer.js)
  serializes the full item objects, so scanner-native and future unknown keys
  can coexist with Fribbels' normalized fields.
- [`itemAugmenter.js`](https://github.com/RexQian/Fribbels-Epic-7-Optimizer/blob/f49b0676c27d893ae4aa1b69920e4c98f37eb3fb/app/js/lib/itemAugmenter.js)
  adds generated IDs, augmented totals, and reforge projections.
- [`Item.java`](https://github.com/RexQian/Fribbels-Epic-7-Optimizer/blob/f49b0676c27d893ae4aa1b69920e4c98f37eb3fb/backend/src/main/java/com/fribbels/model/Item.java#L25-L54)
  and [`Stat.java`](https://github.com/RexQian/Fribbels-Epic-7-Optimizer/blob/f49b0676c27d893ae4aa1b69920e4c98f37eb3fb/backend/src/main/java/com/fribbels/model/Stat.java)
  enumerate the enriched item/stat fields.
- [`MergeHero.java`](https://github.com/RexQian/Fribbels-Epic-7-Optimizer/blob/f49b0676c27d893ae4aa1b69920e4c98f37eb3fb/backend/src/main/java/com/fribbels/model/MergeHero.java)
  supplies the minimal hero ownership fields, while
  [`ItemsRequestHandler.java`](https://github.com/RexQian/Fribbels-Epic-7-Optimizer/blob/f49b0676c27d893ae4aa1b69920e4c98f37eb3fb/backend/src/main/java/com/fribbels/handler/ItemsRequestHandler.java)
  matches `ingameEquippedId` to a hero `id` and prefers `ingameId` during merge.
- [`saves.js`](https://github.com/RexQian/Fribbels-Epic-7-Optimizer/blob/f49b0676c27d893ae4aa1b69920e4c98f37eb3fb/app/js/lib/saves.js#L24-L35)
  establishes that full optimizer saves can contain enriched items and heroes.

A current local export was also inspected using a privacy-preserving structure
probe. Only key names, JSON types, nullability, encoding, and non-identifying
enumerations were examined; no names, IDs, owners, or stat values were copied
or retained. It confirmed the current scanner shape represented by the corpus.

## File and root rules

- Input is a `.txt` file containing one serialized JSON document.
- UTF-8 without a byte-order mark is the native upstream form.
- UTF-8 with the byte sequence `EF BB BF` is also supported. No other encoding
  is part of this contract.
- The root must be a JSON object.
- `items` is required and must be an array, including for an empty inventory.
- `heroes` is optional for compatibility with items-only screenshot exports.
  When present it must be an array. A missing `heroes` property means that
  equipment-owner resolution is unavailable; it does not invalidate the items.
- Array roots, missing `items`, and object/null/scalar item or hero containers
  are structural errors. Malformed JSON is a document error.

## Supported item variants

All variants use Fribbels' normalized item core:

| Field | Status | Shape and meaning |
| --- | --- | --- |
| `gear` | required for a usable item | Slot name such as `Weapon` or `Ring`. |
| `rank` | required for a usable item | Rank name such as `Heroic` or `Epic`. |
| `set` | required for a usable item | Fribbels set name such as `SpeedSet`. |
| `enhance` | required for a usable item | Integer enhancement level. |
| `level` | required for a usable item | Integer item level. |
| `main` | required for a usable item | Object with required `type` and numeric `value`. |
| `substats` | required for a usable item | Array of stat objects with required `type` and numeric `value`. |
| `name` | optional | Display label; it is not an identity. |

Stat objects may additionally contain `rolls`, `ingameRolls`, `modified`, and
`reforgedValue`. Their presence varies by source and augmentation stage.

### Current scanner export

The scanner adds the normalized core without removing scanner-native fields
such as `code`, `ct`, `e`, `f`, `g`, `l`, `mg`, `op`, `p`, `s`, `type`, and the
raw main-stat fields. These keys are source metadata and must remain available
for round-trip diagnostics. In particular, raw `l` is **not** interpreted as
the application lock state; the explicit enriched field `locked` is the only
documented lock field.

The current form may contain:

- `ingameId`: preferred in-game item identity, represented by upstream data as
  either a JSON string or integer;
- `id`: source identity, also represented as a string or integer. Current
  scanner output commonly leaves the scanner ID here and copies it to
  `ingameId`;
- `ingameEquippedId`: string-form hero ID, or the literal scanner sentinel
  `"undefined"` for an unequipped item;
- raw `p`: present on scanner rows that identify an equipped owner.

### Items-only export

An older screenshot/OCR export can contain only `items`, with each item limited
to the normalized core and an optional name. IDs, heroes, ownership, lock
state, and augmentation are unavailable in this form. The importer will accept
the document and report those fields as absent rather than inventing values.

### Enriched/full-save export

Optimizer processing or a full save can add:

- `augmentedStats`: normalized substat totals plus separate `mainType` and
  `mainValue` fields;
- `reforgedStats`: projected reforged substat totals plus separate main-stat
  data;
- `material`: optional reforge material classification;
- `locked`: optional boolean application lock state;
- `equippedById` and `equippedByName`: optional, nullable enriched ownership
  fields;
- `modId`: optional source modification identity.

Augmented/reforged values are evidence supplied by the source, not implicitly
trusted canonical totals. P01-T02 will validate consistency before using them.

## Minimal hero ownership shape

For Phase 01, an imported hero exists only to preserve equipment ownership.
The relevant fields are:

| Field | Status | Shape and meaning |
| --- | --- | --- |
| `id` | required to resolve an owner | String or integer source hero ID. Comparison uses its string representation. |
| `name` | optional | Display metadata only. |
| `stars` | optional | Scanner-derived grade. |
| `awaken` | optional | Scanner-derived awakening count. |

Scanner-native hero fields such as `code`, `g`, and `z` are preserved as raw
source metadata. They are not a Phase 02 character database and are not used to
derive combat stats here.

## Nullability and unknown fields

Absent optional fields and explicit JSON `null` are distinct raw-source states
and must remain distinguishable in metadata. The enriched owner name/ID and
optional material/modification fields may be null. Required containers and the
required members of a usable item/stat cannot be null.

Unknown root, item, stat, and hero keys are accepted as raw source metadata.
P01-T02 must preserve them losslessly under the source metadata boundary while
normalizing only documented fields.

## Parser and normalization behavior

`src.optimizer.data.fribbels` exposes byte- and path-based entry points. It
decodes strict UTF-8, detects the actual BOM bytes, rejects duplicate JSON
keys/non-finite numbers, validates document containers, and then handles each
item independently. Fatal document errors raise `FribbelsDocumentError` with a
stable category and source path. Recoverable item problems produce one indexed
rejection without discarding other valid rows; optional metadata problems and
stale/conflicting owners produce indexed warnings.

Slots, sets, and item stats resolve through the canonical source catalogs.
Ranks normalize to `GearRank` (`Normal`, `Good`, `Rare`, `Heroic`, `Epic`) and
materials normalize to `ReforgeMaterial` (`Hunt`, `Conversion`, `Unknown`). UI
aliases are not accepted as source values. Slot/main-stat legality,
weapon/armor substat restrictions, duplicate stat types, numeric bounds, and
roll/modification types are validated before a row is accepted.

An accepted `ParsedFribbelsItem` deliberately keeps `ingame_id` and
`source_id` separate. Items-only rows may have neither. P01-T02 does not choose
identity precedence or invent a fingerprint; P01-T03 supplies the stable ID
when calling `to_gear_item(item_id)`. Dense search IDs are never assigned by
the parser.

Each accepted item, stat, hero, and unknown root field is recursively frozen.
The complete raw item/stat/hero object is retained, so an absent property,
explicit JSON null, and the scanner's `"undefined"` owner sentinel remain
distinguishable even after their normalized optional value becomes `None`.

Projection validation treats the stat-named properties inside
`augmentedStats`/`reforgedStats` as substat totals and validates their separate
`mainType`/`mainValue` against the raw main/substat evidence. Consistent
objects supply the normalized totals. Missing objects use a quiet,
conservative per-stat derivation; inconsistent or malformed objects use the
same deterministic fallback and emit a warning. A missing per-stat
`reforgedValue` conservatively retains that stat's current value rather than
guessing Fribbels' roll history.

## Sanitized corpus

`tests/fixtures/fribbels/manifest.json` is the machine-readable index for every
supported and rejected example. All names, IDs, owners, and values in that
directory are synthetic and intentionally small.
