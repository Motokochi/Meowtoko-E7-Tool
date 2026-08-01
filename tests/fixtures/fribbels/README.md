# Sanitized Fribbels source corpus

Every `.txt` file in this directory is a synthetic raw-source document. No
account export, hero roster, item identity, owner identity, or stat roll from a
real player is included.

`manifest.json` is authoritative for the corpus membership, encoding, variant,
and expected validity of each file. Valid documents cover the current scanner,
UTF-8 BOM, items-only, and enriched/full-save shapes. Invalid documents cover
JSON syntax, root shape, required `items`, and container-type errors.

These fixtures deliberately retain scanner-native keys that the app does not
interpret. Contract tests inspect bytes and JSON structure only. Normalization
into optimizer domain records begins in P01-T02.
