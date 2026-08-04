# Adding a new character

Future characters are supplied manually. Do not refresh or extend the frozen
Fribbels hero cache.

## What to provide

1. Copy `new-character-template.json` and name the copy after the character,
   for example `Example Hero.json`.
2. Replace every required `null` and `YYYY-MM-DD` value you can verify.
3. Set `status` to `ready` when the identity and both base-stat profiles are
   complete.
4. Create `assets/characters/Exact Character Name/` and place the image files
   extracted from the game there. PNG is expected; do not compress or convert
   the source files manually.

Use the exact in-game English character name for both `character.name` and the
artwork folder. `gameCode` is the internal code when known, such as `c5190`.

Accepted display values:

- `element`: `Fire`, `Ice`, `Earth`, `Light`, or `Dark`.
- `class`: `Warrior`, `Knight`, `Ranger`, `Thief`, `Mage`, or `Soul Weaver`.
- `zodiac`: `Aries`, `Taurus`, `Gemini`, `Cancer`, `Leo`, `Virgo`, `Libra`,
  `Scorpio`, `Sagittarius`, `Capricorn`, `Aquarius`, or `Pisces`.
- Base percentages use whole values: `15` means 15%, not `0.15`.
- `selfImprint.stat` uses a display name such as `Attack %`, `Health %`,
  `Defense %`, `Critical Hit Chance`, `Effectiveness`, or `Effect Resistance`.

Skill multipliers may remain `null` if they are not published yet. Record the
visible skill behavior in `notes`; incomplete multipliers mean skill-damage
preview cannot be considered final.

Build and archetype data are deliberately absent from this file. Supply the
Hero Journal Discord build screenshot separately when it becomes available.

## Release artwork check

Before a release containing the new character, the maintainer must inspect the
raw game files, identify the pose and face variants, validate their dimensions
and transparency, normalize their filenames, and update the packaged artwork
and manifest. Conversion to the app's optimized WebP format happens during
that reviewed release step; the original PNGs remain the intake evidence.

The completed intake JSON, raw artwork folder, and later Discord screenshot are
separate inputs for a reviewed app data update. The template itself is never
loaded by the application.
