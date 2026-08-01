import assert from 'node:assert/strict';
import { test } from 'node:test';

import { ARTIFACT_SEARCH, HERO_DETAILS, HERO_DRAFT, HERO_SEARCH } from './optimizer-profile-fixtures';
import {
  initialOptimizerProfileWorkspaceState,
  optimizerProfileWorkspaceReducer,
} from './optimizer-profile-workspace';
import {
  isOptimizerArtifactSearchResult,
  isOptimizerHeroDetails,
  isOptimizerHeroDraftEnvelope,
  isOptimizerHeroSearchResult,
  validateOptimizerHeroDraft,
} from './shared/optimizer-profile';

test('accepts exact bounded profile contracts and rejects raw, extra, and unbounded data', () => {
  assert.equal(isOptimizerHeroSearchResult(HERO_SEARCH), true);
  assert.equal(isOptimizerArtifactSearchResult(ARTIFACT_SEARCH), true);
  assert.equal(isOptimizerHeroDetails(HERO_DETAILS), true);
  assert.equal(isOptimizerHeroDraftEnvelope(HERO_DRAFT), true);
  assert.equal(isOptimizerHeroSearchResult({ ...HERO_SEARCH, rawSource: {} }), false);
  assert.equal(isOptimizerHeroSearchResult({ ...HERO_SEARCH, results: Array.from({ length: 51 }, () => HERO_SEARCH.results[0]) }), false);
  assert.equal(isOptimizerHeroDetails({ ...HERO_DETAILS, portraits: ['private'] }), false);
  assert.equal(isOptimizerHeroDraftEnvelope({ ...HERO_DRAFT, sourcePath: 'C:/private/profile.json' }), false);
});

test('validates required numerical draft fields without converting blanks to zero', () => {
  assert.deepEqual(validateOptimizerHeroDraft(HERO_DRAFT.draft, HERO_DETAILS), []);
  const invalid = structuredClone(HERO_DRAFT.draft);
  invalid.artifact.level = null;
  invalid.skills[0].targetDefense = Number.NaN;
  invalid.skills[1].penetrationPercent = 101;
  assert.deepEqual(validateOptimizerHeroDraft(invalid, HERO_DETAILS).map((issue) => issue.path), [
    'draft.artifact.level',
    'draft.skills[0].targetDefense',
    'draft.skills[1].penetrationPercent',
  ]);
});

test('validates all primary bounds and priorities while retaining blank, zero, and one-sided values', () => {
  const valid = structuredClone(HERO_DRAFT.draft);
  valid.primaryStats.attack = { minimum: 0, maximum: null, priority: -1 };
  valid.primaryStats.health = { minimum: null, maximum: 20000, priority: 0 };
  valid.primaryStats.defense = { minimum: 1500, maximum: 1500, priority: 1 };
  valid.primaryStats.speed.priority = 2;
  valid.primaryStats.criticalHitChancePercent.priority = 3;
  assert.deepEqual(validateOptimizerHeroDraft(valid, HERO_DETAILS), []);

  valid.primaryStats.attack = { minimum: 3000, maximum: 2999, priority: -1 };
  assert.deepEqual(validateOptimizerHeroDraft(valid, HERO_DETAILS), [{
    path: 'draft.primaryStats.attack.maximum',
    message: 'Attack maximum must be greater than or equal to its minimum.',
  }]);

  const invalidPriority = structuredClone(HERO_DRAFT.draft) as unknown as { primaryStats: { speed: { priority: number } } };
  invalidPriority.primaryStats.speed.priority = 4;
  assert.equal(isOptimizerHeroDraftEnvelope({ ...HERO_DRAFT, draft: invalidPriority }), false);
});

test('rejects derived target ranges at the desktop profile boundary', () => {
  assert.equal(isOptimizerHeroDraftEnvelope({
    ...HERO_DRAFT,
    draft: {
      ...HERO_DRAFT.draft,
      derivedMetrics: { 'metric.ehp': { minimum: 1, maximum: 2 } },
    },
  }), false);
});

test('validates set layouts, inventory options, projection, and legal right-side filters', () => {
  const valid = structuredClone(HERO_DRAFT.draft);
  valid.setPattern = { kind: '2+2+2', sets: ['set.health', 'set.health', 'set.defense'] };
  valid.includeEquipped = true;
  valid.maximumReplacementDistance = 0;
  valid.nearSetTolerancePercent = 0;
  valid.itemProjectionMode = 'projection.reforged';
  valid.gearFilters.minimumEnhance = 15;
  valid.gearFilters.rightSideMainStats = {
    'slot.necklace': ['item_stat.critical_hit_chance_percent', 'item_stat.critical_hit_damage_percent'],
    'slot.ring': ['item_stat.effectiveness_percent', 'item_stat.effect_resistance_percent'],
    'slot.boots': ['item_stat.speed'],
  };
  assert.deepEqual(validateOptimizerHeroDraft(valid, HERO_DETAILS), []);
  assert.equal(isOptimizerHeroDraftEnvelope({ ...HERO_DRAFT, draft: valid }), true);

  const partial = structuredClone(valid);
  partial.setPattern = { kind: 'flexible', sets: ['set.riposte', null, null] };
  assert.deepEqual(validateOptimizerHeroDraft(partial, HERO_DETAILS), []);
  assert.equal(isOptimizerHeroDraftEnvelope({ ...HERO_DRAFT, draft: partial }), true);

  const anySets = structuredClone(valid);
  anySets.setPattern = { kind: 'flexible', sets: [null, null, null] };
  assert.deepEqual(validateOptimizerHeroDraft(anySets, HERO_DETAILS), []);

  const impossible = structuredClone(valid);
  impossible.setPattern = { kind: 'flexible', sets: ['set.speed', 'set.rage', null] };
  assert.deepEqual(validateOptimizerHeroDraft(impossible, HERO_DETAILS), [{
    path: 'draft.setPattern.sets',
    message: 'Selected sets require more than the six gear pieces a hero can equip.',
  }]);

  const nonstackable = structuredClone(valid);
  nonstackable.setPattern.sets = ['set.immunity', 'set.immunity', 'set.defense'];
  assert.deepEqual(validateOptimizerHeroDraft(nonstackable, HERO_DETAILS), [{
    path: 'draft.setPattern.sets[1]',
    message: 'Immunity Set cannot be selected more than once.',
  }]);

  const wrongSize = structuredClone(HERO_DRAFT.draft);
  wrongSize.setPattern.sets[0] = 'set.health';
  assert.deepEqual(validateOptimizerHeroDraft(wrongSize, HERO_DETAILS), [{
    path: 'draft.setPattern.sets[0]',
    message: 'This selector requires a 4-piece set.',
  }]);

  const invalidFilters = structuredClone(HERO_DRAFT.draft);
  (invalidFilters as unknown as { nearSetTolerancePercent: number }).nearSetTolerancePercent = 100.1;
  invalidFilters.gearFilters.minimumEnhance = 16;
  invalidFilters.gearFilters.rightSideMainStats['slot.necklace'] = ['item_stat.speed'];
  assert.deepEqual(validateOptimizerHeroDraft(invalidFilters, HERO_DETAILS).map((issue) => issue.path), [
    'draft.nearSetTolerancePercent',
    'draft.gearFilters.minimumEnhance',
    'draft.gearFilters.rightSideMainStats.slot.necklace[0]',
  ]);

  const missing = structuredClone(HERO_DRAFT) as unknown as { draft: Record<string, unknown> };
  delete missing.draft.setPattern;
  assert.equal(isOptimizerHeroDraftEnvelope(missing), false);
  assert.equal(isOptimizerHeroDraftEnvelope({
    ...HERO_DRAFT,
    draft: { ...HERO_DRAFT.draft, privateInventory: ['raw-item-id'] },
  }), false);
});

test('reducer reports an invalid non-stackable set repeat immediately and retains it for correction', () => {
  const selected = optimizerProfileWorkspaceReducer(initialOptimizerProfileWorkspaceState, {
    type: 'selection-completed', details: HERO_DETAILS, envelope: HERO_DRAFT,
  });
  const invalid = structuredClone(HERO_DRAFT.draft);
  invalid.setPattern = { kind: '2+2+2', sets: ['set.immunity', 'set.immunity', 'set.health'] };
  const edited = optimizerProfileWorkspaceReducer(selected, { type: 'draft-updated', draft: invalid });
  assert.equal(edited.dirty, true);
  assert.deepEqual(edited.envelope?.draft.setPattern, invalid.setPattern);
  assert.deepEqual(edited.issues, [{
    path: 'draft.setPattern.sets[1]',
    message: 'Immunity Set cannot be selected more than once.',
  }]);
});

test('reducer preserves the last valid hero on load failure and restores a saved hero atomically', () => {
  const selected = optimizerProfileWorkspaceReducer(initialOptimizerProfileWorkspaceState, {
    type: 'selection-completed', details: HERO_DETAILS, envelope: HERO_DRAFT,
  });
  const editedDraft = { ...HERO_DRAFT.draft, imprintGrade: 'D' };
  const edited = optimizerProfileWorkspaceReducer(selected, { type: 'draft-updated', draft: editedDraft });
  assert.equal(edited.dirty, true);
  assert.deepEqual(edited.issues, []);
  const loading = optimizerProfileWorkspaceReducer(edited, { type: 'selection-started' });
  const failed = optimizerProfileWorkspaceReducer(loading, { type: 'selection-failed', message: 'Future schema is read-only.' });
  assert.equal(failed.envelope?.draft.imprintGrade, 'D');
  assert.equal(failed.details?.hero.name, 'Achates');
  assert.match(failed.notice?.message ?? '', /read-only/);

  const saved = optimizerProfileWorkspaceReducer(edited, { type: 'save-completed', envelope: { ...HERO_DRAFT, draft: editedDraft } });
  assert.equal(saved.dirty, false);
  assert.equal(saved.envelope?.draft.imprintGrade, 'D');
});

test('reducer reports an invalid primary range immediately without replacing the saved envelope', () => {
  const selected = optimizerProfileWorkspaceReducer(initialOptimizerProfileWorkspaceState, {
    type: 'selection-completed', details: HERO_DETAILS, envelope: HERO_DRAFT,
  });
  const invalid = structuredClone(HERO_DRAFT.draft);
  invalid.primaryStats.speed = { minimum: 251, maximum: 250, priority: 3 };
  const edited = optimizerProfileWorkspaceReducer(selected, { type: 'draft-updated', draft: invalid });
  assert.equal(edited.dirty, true);
  assert.equal(edited.envelope?.draft.primaryStats.speed.minimum, 251);
  assert.deepEqual(edited.issues, [{
    path: 'draft.primaryStats.speed.maximum',
    message: 'Speed maximum must be greater than or equal to its minimum.',
  }]);
  assert.equal(HERO_DRAFT.draft.primaryStats.speed.minimum, null);
});

test('full optimizer data reset discards the selected saved profile', () => {
  const selected = optimizerProfileWorkspaceReducer(initialOptimizerProfileWorkspaceState, {
    type: 'selection-completed', details: HERO_DETAILS, envelope: HERO_DRAFT,
  });
  const reset = optimizerProfileWorkspaceReducer(selected, { type: 'data-reset' });
  assert.deepEqual(reset, initialOptimizerProfileWorkspaceState);
});

test('artifact selection has explicit no-artifact state and never fabricates a level', () => {
  const selected = optimizerProfileWorkspaceReducer(
    { ...initialOptimizerProfileWorkspaceState, details: HERO_DETAILS, envelope: HERO_DRAFT },
    { type: 'artifact-selected', artifact: null },
  );
  assert.deepEqual(selected.envelope?.draft.artifact, {
    artifactId: null, level: null, attackOverride: null, healthOverride: null, defenseOverride: null,
  });
  assert.equal(selected.envelope?.selectedArtifact, null);
});

test('backend async reset stops pending profile work without discarding the editable hero', () => {
  const pending = {
    ...initialOptimizerProfileWorkspaceState,
    details: HERO_DETAILS,
    envelope: HERO_DRAFT,
    dirty: true,
    heroSearching: true,
    artifactSearching: true,
    loading: true,
    saving: true,
  };
  const reset = optimizerProfileWorkspaceReducer(pending, { type: 'async-reset' });
  assert.equal(reset.heroSearching, false);
  assert.equal(reset.artifactSearching, false);
  assert.equal(reset.loading, false);
  assert.equal(reset.saving, false);
  assert.equal(reset.dirty, true);
  assert.equal(reset.envelope, HERO_DRAFT);
});
