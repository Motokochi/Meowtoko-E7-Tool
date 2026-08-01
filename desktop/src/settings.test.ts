import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  DEFAULT_DESKTOP_SETTINGS,
  isSettingsPreview,
  isSettingsPreviewRequest,
  cloneDesktopSettings,
  isSettingsPatch,
  isSettingsSnapshot,
  validateDesktopSettings,
  withLocalThemeFallback,
  type SettingsSnapshot,
} from './shared/settings';

const SNAPSHOT: SettingsSnapshot = {
  schemaVersion: 1,
  revision: 'revision-1',
  source: 'file',
  readOnly: false,
  settings: DEFAULT_DESKTOP_SETTINGS,
};

test('validates complete snapshots and narrow partial updates', () => {
  assert.equal(isSettingsSnapshot(SNAPSHOT), true);
  assert.deepEqual(DEFAULT_DESKTOP_SETTINGS.clickPoints.probeIngredient, { x: 1060, y: 170 });
  assert.deepEqual(DEFAULT_DESKTOP_SETTINGS.clickPoints.probeSelect, { x: 640, y: 490 });
  assert.equal(isSettingsPatch({ appearance: { theme: 'dark' } }), true);
  assert.equal(isSettingsPatch({ adb: { commandTimeoutSeconds: 15 } }), true);
  assert.equal(isSettingsPatch({ shellCommand: 'anything' }), false);
  assert.equal(isSettingsPatch({ regions: { mainStat: { width: 0 } } }), false);
  assert.equal(isSettingsSnapshot({ ...SNAPSHOT, revision: '' }), false);
});

test('returns field-level errors without mutating the original settings', () => {
  const invalid = cloneDesktopSettings(DEFAULT_DESKTOP_SETTINGS);
  invalid.targetWindow = '';
  invalid.adb.coordinateWidth = 0;
  invalid.automation.afterEnhanceSeconds = 1.9;
  invalid.automation.enhancementReadRetries = 2.5;
  const issues = validateDesktopSettings(invalid);

  assert.equal(issues.targetWindow, 'Target window is required.');
  assert.match(issues['adb.coordinateWidth'], /between 1 and 100000/);
  assert.equal(issues['automation.afterEnhanceSeconds'], 'Must be between 2 and 300 seconds.');
  assert.equal(issues['automation.enhancementReadRetries'], 'Must be a whole number.');
  assert.equal(DEFAULT_DESKTOP_SETTINGS.targetWindow, 'Epic Seven');
});

test('hydrates a legacy default theme from the safe local fallback only', () => {
  const legacy: SettingsSnapshot = {
    ...SNAPSHOT,
    source: 'file',
    migratedFrom: 0,
  };
  const hydrated = withLocalThemeFallback(legacy, 'dark');
  const current = withLocalThemeFallback(SNAPSHOT, 'dark');

  assert.equal(hydrated.settings.appearance.theme, 'dark');
  assert.equal(current.settings.appearance.theme, 'system');
  assert.equal(SNAPSHOT.settings.appearance.theme, 'system');
});

test('validates bounded settings preview requests and image responses', () => {
  assert.equal(isSettingsPreviewRequest({
    source: 'adb',
    target: { kind: 'region', id: 'mainStat' },
  }), true);
  assert.equal(isSettingsPreviewRequest({
    source: 'adb',
    target: { kind: 'point', id: 'destroy' },
  }), true);
  assert.equal(isSettingsPreviewRequest({
    source: 'window',
    target: { kind: 'region', id: 'slot' },
  }), false);
  assert.equal(isSettingsPreview({
    source: 'adb',
    kind: 'region',
    itemId: 'slot',
    label: 'Capture region: slot',
    width: 10,
    height: 10,
    dataUrl: 'data:image/png;base64,AAAA',
  }), true);
  assert.equal(isSettingsPreview({
    source: 'adb',
    kind: 'region',
    itemId: 'slot',
    label: 'Capture region: slot',
    width: 10,
    height: 10,
    dataUrl: 'file:///private.png',
  }), false);
});
