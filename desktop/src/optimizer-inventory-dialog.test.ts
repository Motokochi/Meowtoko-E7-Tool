import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  OptimizerInventoryImportCoordinator,
  optimizerInventoryDialogOptions,
} from './optimizer-inventory-dialog';
import { IMPORT_RESULT } from './optimizer-inventory-fixtures';

test('uses a single-file txt picker and never imports after cancellation', async () => {
  let imported = false;
  const coordinator = new OptimizerInventoryImportCoordinator(
    async (options) => {
      assert.deepEqual(options, optimizerInventoryDialogOptions());
      assert.deepEqual(options.properties, ['openFile']);
      assert.deepEqual(options.filters, [{ name: 'Fribbels gear.txt', extensions: ['txt'] }]);
      return { canceled: true, filePaths: ['C:/private/gear.txt'] };
    },
    async () => { imported = true; return IMPORT_RESULT; },
  );

  assert.deepEqual(await coordinator.run(), { outcome: 'cancelled' });
  assert.equal(imported, false);
});

test('passes one selected path only to the backend importer and omits it from the result', async () => {
  const privatePath = 'C:/private/player-name/gear.txt';
  let importedPath = '';
  const coordinator = new OptimizerInventoryImportCoordinator(
    async () => ({ canceled: false, filePaths: [privatePath] }),
    async (sourcePath) => { importedPath = sourcePath; return IMPORT_RESULT; },
  );

  const result = await coordinator.run();

  assert.equal(importedPath, privatePath);
  assert.deepEqual(result, { outcome: 'imported', import: IMPORT_RESULT });
  assert.doesNotMatch(JSON.stringify(result), /player-name|gear\.txt|C:\//);
});

test('rejects invalid selection shapes and concurrent duplicate imports', async () => {
  const invalid = new OptimizerInventoryImportCoordinator(
    async () => ({ canceled: false, filePaths: ['C:/private/gear.json'] }),
    async () => IMPORT_RESULT,
  );
  await assert.rejects(invalid.run(), /Choose a Fribbels gear\.txt file/);

  let release: (() => void) | undefined;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  const busy = new OptimizerInventoryImportCoordinator(
    async () => { await pending; return { canceled: true, filePaths: [] }; },
    async () => IMPORT_RESULT,
  );
  const first = busy.run();
  await assert.rejects(busy.run(), /already in progress/);
  release?.();
  assert.deepEqual(await first, { outcome: 'cancelled' });
});
