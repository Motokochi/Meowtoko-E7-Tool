import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { renderToStaticMarkup } from 'react-dom/server';

import { ImporterCenter } from './importer-center';
import { EMPTY_INVENTORY, IMPORT_RESULT } from './optimizer-inventory-fixtures';

const noop = (): void => undefined;
const reset = async (): Promise<void> => undefined;

test('renders Fribbels import and full optimizer reset in the dedicated workspace', () => {
  const empty = renderToStaticMarkup(
    <ImporterCenter
      capturing={false}
      importing={false}
      inventory={EMPTY_INVENTORY}
      lastReport={null}
      notice={null}
      onFinishCapture={noop}
      onImport={noop}
      onReset={reset}
      onStartCapture={noop}
      packetReady
      resetting={false}
    />,
  );
  assert.match(empty, /GAME INVENTORY/);
  assert.match(
    empty,
    /Close Epic Seven in your emulator[\s\S]*Start capturing from game[\s\S]*Open Epic Seven[\s\S]*Epic Seven Lobby is fully loaded[\s\S]*Done Capturing/,
  );
  assert.match(empty, /Otherwise, import an existing Fribbels <strong>gear\.txt/);
  assert.match(empty, /Start capturing from game/);
  assert.match(empty, /Select gear\.txt/);
  assert.match(empty, /Erase all Optimizer data/);
  assert.match(empty, /saved hero profile/);
  assert.doesNotMatch(empty, /sourcePath|itemId|ownerId/);

  const ready = renderToStaticMarkup(
    <ImporterCenter
      capturing
      importing={false}
      inventory={IMPORT_RESULT.inventory}
      lastReport={IMPORT_RESULT.report}
      notice={{ tone: 'success', title: 'Inventory imported', message: 'Two pieces are ready.' }}
      onFinishCapture={noop}
      onImport={noop}
      onReset={reset}
      onStartCapture={noop}
      packetReady
      resetting={false}
    />,
  );
  assert.match(ready, /Import another gear\.txt/);
  assert.match(ready, /2<\/strong><span>pieces/);
  assert.match(ready, /Latest import/);
  assert.match(ready, /Inventory imported/);
  assert.match(ready, /Done Capturing/);
  assert.match(ready, /Capture is running\. Continue with step 3\./);
  assert.match(ready, /Documents\\MeowtokoE7Hub\\gear\.txt/);
});

test('requires the explicit ERASE confirmation before invoking the reset', () => {
  const source = readFileSync(path.resolve('src', 'importer-center.tsx'), 'utf8');
  assert.match(source, /confirmation !== 'ERASE'/);
  assert.match(source, /Type ERASE to confirm/);
  assert.match(source, /This cannot be undone/);
  assert.match(source, /Permanently erase data/);
});
