import assert from 'node:assert/strict';
import { test } from 'node:test';
import { renderToStaticMarkup } from 'react-dom/server';

import {
  OptimizerBonusConfiguration,
  OptimizerProfileEditor,
} from './optimizer-profile-editor';
import { HERO_DETAILS, HERO_DRAFT, HERO_SEARCH } from './optimizer-profile-fixtures';
import { initialOptimizerProfileWorkspaceState } from './optimizer-profile-workspace';

const noop = (): void => undefined;
const props = {
  enabled: true,
  onArtifactSearch: noop,
  onChooseArtifact: noop,
  onDraftChange: noop,
  onHeroSearch: noop,
  onSaveDraft: noop,
  onSelectHero: noop,
};

function profileWithHero() {
  return {
    ...initialOptimizerProfileWorkspaceState,
    heroQuery: 'Achates',
    heroResults: HERO_SEARCH.results,
    details: HERO_DETAILS,
    envelope: HERO_DRAFT,
  };
}

test('renders a bounded keyboard combobox and no artwork before a character is selected', () => {
  const markup = renderToStaticMarkup(<OptimizerProfileEditor {...props} profile={{
    ...initialOptimizerProfileWorkspaceState,
    heroResults: HERO_SEARCH.results,
  }} />);
  assert.match(markup, /id="optimizer-hero-search"[^>]*role="combobox"/);
  assert.match(markup, /aria-controls="optimizer-hero-search-results"/);
  assert.match(markup, /1 bounded results/);
  assert.match(markup, /Select a character/);
  assert.doesNotMatch(markup, /<img/);
  assert.doesNotMatch(markup, /sourcePath|rawSource/);
});

test('renders the compact character, primary-stat, set, and projection workspace', () => {
  const markup = renderToStaticMarkup(
    <OptimizerProfileEditor {...props} profile={profileWithHero()} />,
  );

  assert.match(markup, /optimizer-compact-workspace/);
  assert.match(markup, /Achates character artwork/);
  assert.match(markup, /src="e7-character:\/\/artwork\/image\?name=Achates&amp;variant=pose"/);
  assert.doesNotMatch(markup, /raw\.githubusercontent\.com/);
  assert.match(markup, /Level 60 \/ 6 star \/ fully awakened/);
  assert.match(markup, /Add bonus stats/);
  assert.match(markup, /Primary stats and priorities/);
  assert.equal((markup.match(/type="range"/g) ?? []).length, 8);
  assert.match(markup, /Blank = any/);
  assert.match(markup, /0 · Neutral/);
  assert.doesNotMatch(markup, /Derived metric ranges|data-metric-id=/);
  assert.match(markup, /Choose up to three sets/);
  assert.equal((markup.match(/None \(any set\)/g) ?? []).length, 3);
  assert.match(markup, /Include equipped/);
  assert.match(markup, /Use reforged stats/);
  assert.match(markup, /type="checkbox"/);
  assert.match(markup, /Advanced gear filters/);
  assert.match(markup, /Right-side main stats/);
  assert.match(markup, /Necklace/);
  assert.match(markup, /Ring/);
  assert.match(markup, /Boots/);
  assert.match(markup, /\+15 only/);
  assert.doesNotMatch(markup, /Minimum enhancement/);
  assert.doesNotMatch(markup, /future replacements|normalized-distance tolerance/i);
  assert.doesNotMatch(markup, /limitBreaks|excludedItemIds|raw-item/i);
});

test('renders every bonus-stat source and independent skill context in the modal body', () => {
  const profile = profileWithHero();
  const markup = renderToStaticMarkup(
    <OptimizerBonusConfiguration
      busy={false}
      details={HERO_DETAILS}
      draft={HERO_DRAFT.draft}
      onArtifactSearch={noop}
      onChooseArtifact={noop}
      onUpdate={noop}
      profile={profile}
    />,
  );

  assert.match(markup, /Artifact contribution/);
  assert.match(markup, /No self imprint/);
  assert.match(markup, /team imprint is not applied/i);
  assert.match(markup, /Independent EE skill slot/);
  assert.match(markup, /effect unavailable/);
  assert.match(markup, /13 supported/);
  assert.match(markup, /Target Defense/);
  assert.match(markup, /non-damaging/);
  assert.match(markup, /Limit-break effects are unavailable/);
  assert.equal((markup.match(/<details/g) ?? []).length, 3);
});

test('associates an invalid repeated set with its selector and blocks saving', () => {
  const invalid = structuredClone(HERO_DRAFT);
  invalid.draft.setPattern = { kind: '2+2+2', sets: ['set.immunity', 'set.immunity', 'set.health'] };
  const message = 'Immunity Set cannot be selected more than once.';
  const markup = renderToStaticMarkup(<OptimizerProfileEditor {...props} profile={{
    ...initialOptimizerProfileWorkspaceState,
    details: HERO_DETAILS,
    envelope: invalid,
    dirty: true,
    issues: [{ path: 'draft.setPattern.sets[1]', message }],
  }} />);
  assert.match(markup, /id="optimizer-set-1"/);
  assert.match(markup, /aria-describedby="optimizer-set-1-error"/);
  assert.match(markup, /aria-invalid="true"/);
  assert.match(markup, /Immunity Set cannot be selected more than once/);
  assert.match(markup, /<button[^>]*disabled=""[^>]*><span>Save<\/span>/);
});

test('associates an invalid primary range with its maximum field', () => {
  const invalid = structuredClone(HERO_DRAFT);
  invalid.draft.primaryStats.attack = { minimum: 3000, maximum: 2999, priority: 3 };
  const message = 'Attack maximum must be greater than or equal to its minimum.';
  const markup = renderToStaticMarkup(<OptimizerProfileEditor {...props} profile={{
    ...initialOptimizerProfileWorkspaceState,
    details: HERO_DETAILS,
    envelope: invalid,
    issues: [{ path: 'draft.primaryStats.attack.maximum', message }],
  }} />);
  assert.match(markup, /id="optimizer-primary-attack-maximum"/);
  assert.match(markup, /aria-describedby="optimizer-primary-attack-maximum-error"/);
  assert.match(markup, /aria-invalid="true"/);
  assert.match(markup, /role="alert"/);
  assert.match(markup, /Attack maximum must be greater than or equal/);
});

test('renders no-EE semantics and accessible skill validation in bonus configuration', () => {
  const invalid = structuredClone(HERO_DRAFT);
  invalid.draft.skills[0].targetDefense = Number.NaN;
  const details = { ...HERO_DETAILS, exclusiveEquipment: null };
  const profile = {
    ...initialOptimizerProfileWorkspaceState,
    details,
    envelope: {
      ...invalid,
      draft: {
        ...invalid.draft,
        exclusiveEquipment: { equipmentId: null, statValue: null, skillOptionId: null },
      },
    },
    issues: [{
      path: 'draft.skills[0].targetDefense',
      message: 'S1 target Defense must be zero or greater.',
    }],
  };
  const markup = renderToStaticMarkup(
    <OptimizerBonusConfiguration
      busy={false}
      details={details}
      draft={profile.envelope.draft}
      onArtifactSearch={noop}
      onChooseArtifact={noop}
      onUpdate={noop}
      profile={profile}
    />,
  );
  assert.match(markup, /No exclusive equipment exists for this hero/);
  assert.match(markup, /aria-invalid="true"/);
  assert.match(markup, /S1 target Defense must be zero or greater/);
  assert.match(markup, /aria-describedby="optimizer-s1-defense-error"/);
});
