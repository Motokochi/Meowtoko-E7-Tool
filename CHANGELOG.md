# Changelog

## [0.1.48] - 2026-09-10

- Fix local Equip failing when the imported roster contains multiple copies
  of the selected hero. The Equip dialog now lets you choose the intended
  copy using its stars, awakening, and currently equipped gear.
- Recognize imported character codes when matching heroes for optimization
  and local equipment assignment, including missing or localized names.
- Show Uncharted Pioneer Politis and other recognized heroes by name in Gear
  when an import contains a character code as the owner label.
- Keep equipment changes limited to the chosen copy and selected pieces,
  and preserve the result cards as an in-game checklist.

## [0.1.47] - 2026-09-03

- Add Uncharted Pioneer Politis as a selectable Ice Ranger with reviewed
  level-50 and level-60 stats, imprint progression, skill data, and artwork.
- Leave Uncharted Pioneer Politis unassigned to a gear archetype until a
  reviewed Hero Journal build is supplied.
- Correct Lisette's self-imprint progression to 7%, 11%, 15%, 18%, and 21%.
- Enforce the Optimizer's requested Critical Hit Chance upper limit against
  the displayed value, including the exact and CUDA search paths.
- Make bundled-data integrity checks independent of Windows checkout newline
  conversion so the audited installer can be published reliably.

## [0.1.46] - 2026-09-03

- Add Uncharted Pioneer Politis as a selectable Ice Ranger with reviewed
  level-50 and level-60 stats, imprint progression, skill data, and artwork.
- Leave Uncharted Pioneer Politis unassigned to a gear archetype until a
  reviewed Hero Journal build is supplied.
- Correct Lisette's self-imprint progression to 7%, 11%, 15%, 18%, and 21%.
- Enforce the Optimizer's requested Critical Hit Chance upper limit against
  the displayed value, including the exact and CUDA search paths.

## [0.1.45] - 2026-08-28

- Add Lisette as a selectable Light Soul Weaver with reviewed base profiles,
  imprint values, and skill data.
- Include Lisette's verified pose and portrait artwork throughout the app.
- Add the reviewed manual-hero overlay so future releases can extend the
  roster without changing the bundled baseline.
- Leave Lisette unassigned to a gear archetype until a reviewed Hero Journal
  build is supplied.

## [0.1.44] - 2026-08-28

- Add Lisette as a selectable Light Soul Weaver with reviewed base profiles,
  imprint values, and skill data.
- Include Lisette's verified pose and portrait artwork throughout the app.
- Add the reviewed manual-hero overlay so future releases can extend the
  roster without changing the bundled baseline.
- Leave Lisette unassigned to a gear archetype until a reviewed Hero Journal
  build is supplied.

## [0.1.43] - 2026-08-28

- Add Lisette as a selectable Light Soul Weaver with reviewed base profiles,
  imprint values, and skill data.
- Include Lisette's verified pose and portrait artwork throughout the app.
- Add the reviewed manual-hero overlay so future releases can extend the
  roster without changing the bundled baseline.
- Leave Lisette unassigned to a gear archetype until a reviewed Hero Journal
  build is supplied.

## [0.1.42] - 2026-08-28

- Add Lisette as a selectable Light Soul Weaver with reviewed base profiles,
  imprint values, and skill data.
- Include Lisette's verified pose and portrait artwork throughout the app.
- Add the reviewed manual-hero overlay so future releases can extend the
  roster without changing the bundled baseline.
- Leave Lisette unassigned to a gear archetype until a reviewed Hero Journal
  build is supplied.

## [0.1.41] - 2026-08-05

- Continue enhancing whenever the current Potential GS is at least 58; below
  that threshold, continue only while four Speed enhancement rolls remain
  possible.
- Remove Critical Set from the Effectiveness Non-Crit-Chance Attack-Scaling DPS
  archetype so filler sets no longer qualify gear for that build.

## [0.1.40] - 2026-08-05

- Restore Gear and Optimizer inventory loading after the reviewed-archetype
  update caused populated inventories to fail the desktop response check.

## [0.1.39] - 2026-08-05

- Keep low-GS gear for exceptional roll concentration only when at least four
  enhancement rolls land on Speed; repeated rolls into every other stat now
  follow the normal Gear Score and archetype rules.

## [0.1.38] - 2026-08-05

- Rebuild gear archetypes from reviewed Hero Journal screenshots instead of population-wide inferred stat signatures.
- Consolidate overlapping builds into 28 functional archetypes with hero- and equipment-set-specific recommendations.
- Respect alternative bulk-stat slots and manual build corrections so incidental or obsolete stats no longer make gear look desirable.

## [0.1.37] - 2026-08-04

- Quadroll and pentaroll protection now applies only to non-flat substats. Low-GS
  pieces cannot survive solely because enhancement rolls repeatedly hit flat
  Attack, Defense, or Health.

## [0.1.36] - 2026-08-04

- Correct level-85 reforge projections for imported equipment that
  contains roll counts but no precomputed reforged values. Gear scoring and
  both CPU and CUDA optimizer searches now consume the same projection.
- Preserve the substat order shown in Epic Seven while associating roll
  evidence by stat type, preventing one substat's rolls from being attributed
  to another during archetype analysis.
- Show projected substat values and Keep, Review, or Destroy tags in Gear.
  Set filters now use icons, and Optimizer cards show current to projected
  values alongside the equipment-set icon.
- Prefer an available alternate character pose, with the base pose as fallback.

## [0.1.35] - 2026-08-04

- Only continue enhancing gear that matches at least three desired stats from a
  supported archetype. An off-stat may have its base roll and one additional
  roll; the piece is rejected when that off-stat reaches three total rolls.
- Replace the old selected-gear card with a clear Keep, Review, or Destroy
  recommendation, including matching archetypes, desired stats, off-stat roll
  evidence, and compatible heroes.
- Refresh the current character pose assets for Aubade Ludwig, Aube, Estelle,
  Eye of the Abyss Fumyr, Monarch of the Sword Iseria, Notos, Rhianna and
  Luciella, Ruiza, Salome, Shepherd of the Dark Diene, and Tidal Rift Elvira.
- Reject fully transparent character artwork during packaging and add a manual
  intake template for future heroes.

## [0.1.34] - 2026-08-04

- Clarify Importer instructions, completion timing, and retry messages.

## [0.1.33] - 2026-08-03

- Improve compatibility with LDPlayer and make failure diagnostics more actionable.

## [0.1.32] - 2026-08-01

- Remove obsolete E7 Hub Start-menu and desktop shortcuts during installation
  and updates so Windows no longer presents the retired name beside Meowtoko E7 Tool.
- Preserve the existing application data, inventory, profiles, results, and
  in-place update compatibility while cleaning up the old display entry.

## [0.1.31] - 2026-08-01

- Rename the visible application, installer, shortcuts, and documentation to
  Meowtoko E7 Tool.
- Move consent-first update checks and downloads to the new public repository.
- Preserve the existing installation identity and local data so E7 Hub users
  can upgrade in place by running this installer once.
