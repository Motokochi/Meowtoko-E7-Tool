# Result resolution

`resolution.py` resolves only the rows visible on the requested result page, or
one selected build. It validates the active session, run, sort index, page, and
inventory snapshot before mapping dense item IDs back to full gear records.

An empty page opens no columns. A nonempty page opens each fixed-width result
column once. The full run stays memory-mapped, so page resolution never creates
a Python object graph for every stored result.

New optimizer runs contain exact builds only. Legacy category and replacement
columns remain readable as part of the versioned result schema, but detail
resolution does not rebuild replacement plans or future-piece guidance.

Failures use stable `ResultResolutionError` codes and fail closed on stale
identity, forged pages, corrupt columns, unknown gear, or inventory drift.
