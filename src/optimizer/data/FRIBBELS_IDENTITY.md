# Fribbels item identity and pure re-import merging

This document defines the P01-T03 identity and merge contract. It operates on
accepted `ParsedFribbelsItem` rows and does not read or write a database.

## Identity priority and namespaces

Every accepted item has an ordered candidate tuple:

1. `ingame`: normalized `ingameId` when present;
2. `source`: normalized Fribbels `id` when present;
3. `fingerprint`: the versioned content digest described below.

Identity values are pairs of kind and value. Their external keys and generated
stable IDs include the kind, so equal raw text in the in-game and source
namespaces cannot collide. Strong in-game/source aliases must be unique. A
fingerprint is deliberately a non-unique bucket because two owned ID-less
pieces can have genuinely identical content.

New strong-identity rows receive stable IDs of the form
`fribbels:item:<kind>:<percent-encoded-value>`. Fingerprint-only rows receive
`fribbels:item:fingerprint-v1:<digest>:<positive-occurrence>`. Occurrences are
allocated deterministically and never use a source row position as identity.

When a row matches existing state, its stable item ID never changes. New
in-game/source aliases are added, historical strong aliases are retained, and
the old content fingerprint is replaced by the current fingerprint. This
supports a later higher-priority ID without moving user metadata.

## Fingerprint version 1

Algorithm: SHA-256 over compact deterministic UTF-8 JSON with sorted object
keys. Integral floats normalize to integers. The payload is:

```text
fingerprintVersion
slot stable ID
set stable ID
rank stable ID
itemLevel
enhance
main: type stable ID, current value, optional per-stat reforgedValue
substats sorted by stat-type stable ID:
  type, current value, rolls, ingameRolls, modified, reforgedValue
```

Explicit optional values are represented as JSON null in the canonical
payload. Substat source-array order does not affect the digest.

The fingerprint excludes in-game/source IDs, source row position, owner ID and
name, lock state, item display name, material classification, augmented or
reforged validation status, full projection objects, and all unknown raw
metadata. Those fields can change without manufacturing a different physical
identity. Contribution-defining level, enhancement, main/substat values, and
per-stat roll/reforge evidence do change the digest.

## Collision and matching behavior

Strong aliases are resolved first. A source row whose aliases point to two
different existing items is a conflict. A second row claiming an already
claimed strong alias is also a conflict; neither condition silently combines
owned pieces.

Rows without a strong match use their fingerprint bucket. Within a bucket,
the merge first pairs exact normalized source-owned state, then pairs remaining
compatible rows deterministically. Rows with conflicting in-game or source
aliases of the same kind are not weakly paired. This lets a fingerprint-only
or source-only item gain a later stronger alias while preventing two differently
identified but stat-identical items from collapsing.

Two identical fingerprint-only rows retain multiplicity through occurrence
stable IDs. Reordering the import does not change the set of stable IDs; exact
normalized state (including owner/lock) is used to retain the best association
when otherwise-identical pieces differ in volatile fields. Completely
indistinguishable ID-less duplicates have no source evidence that can bind one
physical copy to one occurrence, but they still remain separate items and keep
the same stable-ID set across re-imports.

## Merge ownership boundaries

`FribbelsInventoryItem` separates source-owned state from user-owned metadata.
On update, normalized gear, current IDs, rank/material/name, ownership, lock,
projection, and frozen raw source metadata are replaced. The stable item ID,
historical strong aliases, and recursively frozen `user_metadata` remain.

`merge_fribbels_inventory` returns immutable inserted, updated, unchanged, and
conflict outcomes in source-index order. It also carries parser warnings and
rejections unchanged. Rejected parser rows are never inventory candidates.

Existing items absent from the current import are retained and listed in
`unseen_existing_ids`. Deletion/snapshot policy belongs to P01-T04/P01-T06.
