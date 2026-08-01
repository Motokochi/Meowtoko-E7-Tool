import assert from 'node:assert/strict';
import { test } from 'node:test';
import { renderToStaticMarkup } from 'react-dom/server';

import { GearCenter } from './gear-center';
import { EMPTY_INVENTORY, IMPORT_RESULT } from './optimizer-inventory-fixtures';

test('renders the compact +15 gear table with reforged, combat, and support scores', () => {
  const markup = renderToStaticMarkup(
    <GearCenter inventory={IMPORT_RESULT.inventory} onOpenImporter={() => undefined} />,
  );

  assert.match(markup, /Browse and compare imported \+15 equipment/);
  assert.match(markup, />RGS</);
  assert.match(markup, />CGS</);
  assert.match(markup, />SGS</);
  assert.match(markup, /Reforged Gear Score[\s\S]*37/);
  assert.match(markup, /Combat GS[\s\S]*22/);
  assert.match(markup, /Support GS[\s\S]*37/);
  assert.match(markup, /geararmor\.png/);
  assert.match(markup, /variant=face_s/);
  assert.doesNotMatch(markup, /View/);
});

test('links an empty gear workspace back to the Importer', () => {
  const markup = renderToStaticMarkup(
    <GearCenter inventory={EMPTY_INVENTORY} onOpenImporter={() => undefined} />,
  );

  assert.match(markup, /No imported gear/);
  assert.match(markup, /Open Importer/);
});
