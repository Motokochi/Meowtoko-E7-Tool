import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  NAVIGATION_ITEMS,
  navigationItem,
  pageHash,
  pageIdFromHash,
} from './navigation';

test('parses only enabled page hashes and falls back safely', () => {
  assert.equal(pageIdFromHash('#/health'), 'health');
  assert.equal(pageIdFromHash('#/gear'), 'gear');
  assert.equal(pageIdFromHash('#/analyzer'), 'analyzer');
  assert.equal(pageIdFromHash('#/enhancer'), 'enhancer');
  assert.equal(pageIdFromHash('#/optimizer'), 'optimizer');
  assert.equal(pageIdFromHash('#/importer'), 'importer');
  assert.equal(pageIdFromHash('#/OPTIMIZER?source=sidebar'), 'optimizer');
  assert.equal(pageIdFromHash('#overview'), 'overview');
  assert.equal(pageIdFromHash('#/unknown'), 'overview');
  assert.equal(pageIdFromHash('#/optimizer-extra'), 'overview');
  assert.equal(pageHash('health'), '#/health');
  assert.equal(pageHash('gear'), '#/gear');
  assert.equal(pageHash('optimizer'), '#/optimizer');
  assert.equal(pageHash('importer'), '#/importer');
});

test('enables every current destination and retains stable navigation metadata', () => {
  const enabled = NAVIGATION_ITEMS.filter((item) => item.enabled).map((item) => item.id);
  const future = NAVIGATION_ITEMS.filter((item) => !item.enabled).map((item) => item.id);

  assert.deepEqual(enabled, ['overview', 'health', 'gear', 'analyzer', 'enhancer', 'optimizer', 'importer', 'settings']);
  assert.deepEqual(future, []);
  assert.equal(navigationItem('health').label, 'Health Center');
  assert.equal(navigationItem('gear').description, 'Owned +15 equipment');
  assert.equal(navigationItem('optimizer').description, 'Owned gear build search');
  assert.equal(navigationItem('importer').description, 'Fribbels gear inventory');
});
