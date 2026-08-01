# Shared optimizer fixtures

All files in this directory are synthetic and contain no account, game-capture,
or private inventory data.

The `*-v1.json` files are current persistence-schema fixtures and must be loaded
through the public functions in `src.optimizer.data`. The run manifest covers
the durable result-store schema independently from the catalog, inventory, and
profile fixtures.
