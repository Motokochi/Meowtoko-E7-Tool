import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  isOptimizerInventoryCaptureState,
  isOptimizerInventoryImportResult,
  isOptimizerInventorySelectionResult,
  isOptimizerInventorySnapshot,
  isOptimizerDataResetResult,
} from './shared/optimizer-inventory';
import { EMPTY_INVENTORY, IMPORT_RESULT, RESET_RESULT } from './optimizer-inventory-fixtures';

test('accepts canonical bounded aggregate inventory states and import results', () => {
  assert.equal(isOptimizerInventorySnapshot(EMPTY_INVENTORY), true);
  assert.equal(isOptimizerInventoryCaptureState({ state: 'capturing' }), true);
  assert.equal(isOptimizerInventoryImportResult(IMPORT_RESULT), true);
  assert.equal(isOptimizerInventorySelectionResult({ outcome: 'cancelled' }), true);
  assert.equal(isOptimizerInventorySelectionResult({ outcome: 'imported', import: IMPORT_RESULT }), true);
  assert.equal(isOptimizerDataResetResult(RESET_RESULT), true);
});

test('rejects extra keys, slot drift, inconsistent counts, and unbounded issue data', () => {
  assert.equal(isOptimizerInventorySnapshot({ ...EMPTY_INVENTORY, sourcePath: 'C:/private/gear.txt' }), false);
  assert.equal(isOptimizerInventoryCaptureState({ state: 'capturing', sourcePath: 'private' }), false);
  assert.equal(isOptimizerInventorySnapshot({
    ...EMPTY_INVENTORY,
    itemsBySlot: [...EMPTY_INVENTORY.itemsBySlot].reverse(),
  }), false);
  assert.equal(isOptimizerInventorySnapshot({
    ...IMPORT_RESULT.inventory,
    gear: [IMPORT_RESULT.inventory.gear[0], IMPORT_RESULT.inventory.gear[0]],
  }), false);
  assert.equal(isOptimizerInventorySnapshot({
    ...IMPORT_RESULT.inventory,
    gear: [{ ...IMPORT_RESULT.inventory.gear[0], enhance: 12 }],
  }), false);
  assert.equal(isOptimizerInventoryImportResult({
    ...IMPORT_RESULT,
    report: { ...IMPORT_RESULT.report, resultingInventoryCount: 3 },
  }), false);
  assert.equal(isOptimizerInventoryImportResult({
    ...IMPORT_RESULT,
    report: { ...IMPORT_RESULT.report, issues: Array.from({ length: 21 }, () => ({})) },
  }), false);
  assert.equal(isOptimizerInventorySelectionResult({ outcome: 'cancelled', inventory: EMPTY_INVENTORY }), false);
  assert.equal(isOptimizerDataResetResult({
    ...RESET_RESULT,
    removed: { ...RESET_RESULT.removed, profileFiles: -1 },
  }), false);
  assert.equal(isOptimizerDataResetResult({
    ...RESET_RESULT,
    removed: { ...RESET_RESULT.removed, privatePath: 'C:/private' },
  }), false);
});
