# Selectable character base profiles

P02-T03 adds an immutable, UI-independent selection layer over the offline
`CharacterRepository`. It exposes the canonical `HeroBaseProfile` records
without applying gear, sets, artifacts, imprints, exclusive equipment, custom
bonuses, or skill modifiers.

## Public API

`load_bundled_character_profile_selector()` constructs a
`CharacterProfileSelector` from the bundled repository. A selector can also be
constructed from an explicit repository or iterable of immutable
`CharacterHeroRecord` values for controlled tests.

- `profiles_for(hero_id)` returns every canonical option in ascending level,
  stars, case-folded label, and stable profile-ID order.
- `create_default_selection(hero_id)` is only for a new hero selection. It
  returns the unique level-60, six-star profile whose exact source-backed label
  is `Level 60 / 6 star / fully awakened`.
- `select(hero_id, profile_id)` resolves an explicit stable profile ID and
  never applies a default.

Both selection paths return a frozen `CharacterProfileSelection` containing
the canonical hero and profile objects. Stable hero and profile IDs remain the
public identity; dense IDs are metadata only.

Selector construction validates a unique default for every hero before it can
be used. A missing or ambiguous default, duplicate profile/hero ID, unknown
hero/profile, or cross-hero profile selection raises
`CharacterProfileSelectionError` with an actionable `code`, `path`, and
message. Explicit invalid IDs never fall back to the default.

## Persistence

No schema change is necessary. `OptimizationRequest.base_profile_id` is
already mandatory, `OptimizerConfiguration` preserves it as `baseProfileId`,
and `OptimizerProfileDocument` validates that it belongs to the saved hero.
New-selection defaulting happens before creating a request. Reloading a saved
configuration must call `select(hero_id, base_profile_id)` with the persisted
ID.

The bundled snapshot currently provides 772 profiles across 386 heroes: one
level-50/five-star and one level-60/six-star fully-awakened profile per hero.
The selector returns an arbitrary number of options and does not encode a
two-profile UI shape.

