# Synthetic migration and recovery corpus

`manifest.json` is the versioned index for P09-T03 persistence acceptance. It
contains no copied user database, profile, settings, result, path, identifier,
or gear data.

Tests build the indexed historical states in temporary directories from the
production schema serializers and the existing synthetic optimizer/Fribbels
fixtures. Keeping the state builders in tests avoids checking binary SQLite
files or twelve near-duplicate JSON documents into the repository, while the
manifest makes every supported historical version and expected recovery case
an explicit, reviewable contract.

The corpus is invalid if a production current version changes without a
matching manifest update. All destructive recovery rehearsal is restricted to
the test-owned temporary root.
