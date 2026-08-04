import assert from 'node:assert/strict';
import { test } from 'node:test';
import { renderToStaticMarkup } from 'react-dom/server';

import { GearCenter } from './gear-center';
import { EMPTY_INVENTORY, IMPORT_RESULT } from './optimizer-inventory-fixtures';

test('renders the compact +15 gear table and selected-row archetype verdict', () => {
  const markup = renderToStaticMarkup(
    <GearCenter inventory={IMPORT_RESULT.inventory} onOpenImporter={() => undefined} />,
  );

  assert.match(markup, /Browse and compare imported \+15 equipment/);
  assert.match(markup, />RGS</);
  assert.match(markup, />CGS</);
  assert.match(markup, />SGS</);
  assert.match(markup, />Fit</);
  assert.equal(markup.match(/>Destroy</g)?.length, 2);
  assert.match(markup, /ARCHETYPE ANALYSIS/);
  assert.match(markup, /Destroy/);
  assert.match(markup, /no compatible archetype or heroes/i);
  assert.match(markup, /geararmor\.png/);
  assert.match(markup, /setdefense\.png/);
  assert.match(markup, /HP 20%/);
  assert.doesNotMatch(markup, /HP 18%/);
  assert.match(markup, /variant=face_s/);
  assert.doesNotMatch(markup, /The card shows/);
  assert.doesNotMatch(markup, /View/);
});

test('lists matching archetypes and their heroes', () => {
  const item = IMPORT_RESULT.inventory.gear[0];
  const inventory = {
    ...IMPORT_RESULT.inventory,
    gear: [{
      ...item,
      archetypeAnalysis: {
        verdict: 'keep' as const,
        reason: 'Matches one archetype with acceptable off-stat rolls.',
        rollHistoryAvailable: true,
        matches: [{
          id: 'er-tank',
          name: 'ER Tank',
          heroes: ['Ivana', 'Schniel'],
          preferredStats: ['Defense', 'Effect Resistance', 'Health', 'Speed'],
          matchingSubstats: ['Health', 'Effect Resistance', 'Speed'],
          offStats: [{ statId: 'item_stat.critical_hit_chance_percent', label: 'Critical Hit Chance', rolls: 2 }],
          status: 'eligible' as const,
        }],
      },
    }],
  };

  const markup = renderToStaticMarkup(
    <GearCenter inventory={inventory} onOpenImporter={() => undefined} />,
  );

  assert.match(markup, /ER Tank/);
  assert.match(markup, /Ivana, Schniel/);
  assert.match(markup, /Critical Hit Chance[\s\S]*2 total rolls/);
});

test('links an empty gear workspace back to the Importer', () => {
  const markup = renderToStaticMarkup(
    <GearCenter inventory={EMPTY_INVENTORY} onOpenImporter={() => undefined} />,
  );

  assert.match(markup, /No imported gear/);
  assert.match(markup, /Open Importer/);
});
