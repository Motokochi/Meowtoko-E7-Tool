import assert from 'node:assert/strict';
import { test } from 'node:test';

import { createDesktopApi } from './desktop-api';
import type { AnalyzerOptions, AnalyzerPiece, AnalyzerScanSnapshot } from './shared/analyzer';
import type { HealthActionId, HealthSnapshot } from './shared/health';
import { EMPTY_INVENTORY, IMPORT_RESULT, RESET_RESULT } from './optimizer-inventory-fixtures';
import { ARTIFACT_SEARCH, HERO_DETAILS, HERO_DRAFT, HERO_SEARCH } from './optimizer-profile-fixtures';
import type { OptimizerSearchSnapshot } from './shared/optimizer-search';
import type { UpdateApplyResult, UpdateSnapshot } from './shared/update';
import {
  DEFAULT_DESKTOP_SETTINGS,
  type SettingsPreview,
  type SettingsSnapshot,
} from './shared/settings';

const SNAPSHOT: HealthSnapshot = {
  overall: 'degraded',
  checkedAt: '2026-07-19T00:00:00Z',
  capabilities: [{
    id: 'cuda',
    title: 'GPU acceleration',
    state: 'degraded',
    summary: 'CPU fallback is active.',
    required: false,
    actions: [],
    metadata: { mode: 'cpu' },
  }],
};

const SETTINGS: SettingsSnapshot = {
  schemaVersion: 1,
  revision: 'settings-r1',
  source: 'file',
  readOnly: false,
  settings: DEFAULT_DESKTOP_SETTINGS,
};
const SETTINGS_PREVIEW: SettingsPreview = {
  source: 'adb',
  kind: 'region',
  itemId: 'slot',
  label: 'Capture region: slot',
  width: 10,
  height: 10,
  dataUrl: 'data:image/png;base64,AAAA',
};

const ANALYZER_OPTIONS: AnalyzerOptions = {
  enhancements: Array.from({ length: 16 }, (_item, index) => `+${index}`),
  slots: ['Weapon'],
  sets: ['Speed Set'],
  stats: ['Flat Attack', 'Attack', 'Health', 'Speed', 'Critical Hit Chance'],
  slotMainStats: { Weapon: ['Flat Attack'] },
  restrictedSubstats: { Weapon: [] },
  autoDetectCapabilities: ['tesseract', 'ollama', 'adb'],
};
const ANALYZER_PIECE: AnalyzerPiece = {
  enhancement: '+0', slot: 'Weapon', set: 'Speed Set', mainStat: 'Flat Attack',
  substats: [
    { stat: 'Attack', value: '0' }, { stat: 'Health', value: '0' },
    { stat: 'Speed', value: '0' }, { stat: 'Critical Hit Chance', value: '0' },
  ],
};
const ANALYZER_EVALUATION = {
  piece: ANALYZER_PIECE, archetypeText: 'NO MATCH', gearScoreText: 'Error', gearScore: null,
};
const ANALYZER_SCAN: AnalyzerScanSnapshot = {
  jobId: 'job-1', state: 'running', stage: 'capture', message: 'Capturing', progress: 0.1,
};
const OPTIMIZER_SEARCH: OptimizerSearchSnapshot = {
  sequence: 9,
  jobId: 'optimizer-search-9',
  requestId: 'optimizer-request-9',
  state: 'running',
  backend: 'cuda',
  totalPermutations: '900719925474099312345',
  searchedPermutations: '5000000',
  categoryCounts: { exact: '100', oneAway: '200', twoAway: '300' },
  elapsedSeconds: 2,
  canCancel: true,
  resultAvailable: false,
  resultRunId: null,
  failure: null,
};
const ANALYZER_DEBUG = { available: true, jobId: 'job-1', text: 'debug', artifacts: ['crop.png'] };
const UPDATE: UpdateSnapshot = {
  state: 'available',
  currentVersion: '0.1.18',
  checkedAt: '2026-07-25T00:00:00.000Z',
  release: {
    version: '0.1.19',
    title: 'Meowtoko E7 Tool 0.1.19',
    notes: 'Consent-first update.',
    publishedAt: '2026-07-25T00:00:00.000Z',
    downloadBytes: '734003200',
  },
  progress: null,
  installOnQuit: false,
  error: null,
};
const UPDATE_APPLY: UpdateApplyResult = {
  status: 'confirmation-required',
  activeWork: ['Optimizer search'],
  snapshot: UPDATE,
};

test('maps only the narrow health channels and action identifier', async () => {
  const calls: Array<[string, ...unknown[]]> = [];
  const api = createDesktopApi(async (channel, ...args) => {
    calls.push([channel, ...args]);
    return SNAPSHOT;
  }, () => () => undefined);

  assert.deepEqual(await api.getHealth(), SNAPSHOT);
  assert.deepEqual(await api.refreshHealth(), SNAPSHOT);
  assert.deepEqual(await api.runHealthAction('ollama.start'), SNAPSHOT);
  assert.deepEqual(await api.runHealthAction('health.cancel'), SNAPSHOT);
  assert.deepEqual(calls, [
    ['health:get'],
    ['health:refresh'],
    ['health:action', 'ollama.start'],
    ['health:action', 'health.cancel'],
  ]);
});

test('rejects an arbitrary health action before invoking Electron', async () => {
  let invoked = false;
  const api = createDesktopApi(async () => {
    invoked = true;
    return SNAPSHOT;
  }, () => () => undefined);

  await assert.rejects(
    api.runHealthAction('shell.run_anything' as HealthActionId),
    /Unsupported health action/,
  );
  assert.equal(invoked, false);
});

test('forwards valid progress events and removes the exact subscription', () => {
  let subscribedChannel = '';
  let eventListener: ((payload: unknown) => void) | undefined;
  let removed = false;
  const api = createDesktopApi(async () => SNAPSHOT, (channel, listener) => {
    subscribedChannel = channel;
    eventListener = listener;
    return () => { removed = true; };
  });
  const received: HealthSnapshot[] = [];

  const unsubscribe = api.onHealthUpdated((snapshot) => received.push(snapshot));
  eventListener?.(SNAPSHOT);
  eventListener?.({ bad: 'payload' });
  unsubscribe();

  assert.equal(subscribedChannel, 'health:updated');
  assert.deepEqual(received, [SNAPSHOT]);
  assert.equal(removed, true);
});

test('maps only validated settings snapshots, revisions, patches, and events', async () => {
  const calls: Array<[string, ...unknown[]]> = [];
  let settingsListener: ((payload: unknown) => void) | undefined;
  const api = createDesktopApi(async (channel, ...args) => {
    calls.push([channel, ...args]);
    return SETTINGS;
  }, (channel, listener) => {
    if (channel === 'settings:updated') settingsListener = listener;
    return () => undefined;
  });
  const updates: SettingsSnapshot[] = [];
  api.onSettingsUpdated((snapshot) => updates.push(snapshot));

  assert.deepEqual(await api.getSettings(), SETTINGS);
  assert.deepEqual(
    await api.updateSettings('settings-r1', { appearance: { theme: 'dark' } }),
    SETTINGS,
  );
  settingsListener?.(SETTINGS);
  settingsListener?.({ invalid: true });

  assert.deepEqual(calls, [
    ['settings:get'],
    ['settings:update', 'settings-r1', { appearance: { theme: 'dark' } }],
  ]);
  assert.deepEqual(updates, [SETTINGS]);
});

test('rejects arbitrary settings data before invoking Electron', async () => {
  let invoked = false;
  const api = createDesktopApi(async () => {
    invoked = true;
    return SETTINGS;
  }, () => () => undefined);

  await assert.rejects(
    api.updateSettings('settings-r1', { shellCommand: 'anything' } as never),
    /Unsupported settings patch/,
  );
  await assert.rejects(
    api.updateSettings('', { appearance: { theme: 'light' } }),
    /revision is required/,
  );
  assert.equal(invoked, false);
});

test('maps only validated ADB preview operations', async () => {
  const calls: Array<[string, ...unknown[]]> = [];
  const request = { source: 'adb', target: { kind: 'region', id: 'slot' } } as const;
  const api = createDesktopApi(async (channel, ...args) => {
    calls.push([channel, ...args]);
    return SETTINGS_PREVIEW;
  }, () => () => undefined);

  assert.deepEqual(await api.previewSettings(DEFAULT_DESKTOP_SETTINGS, request), SETTINGS_PREVIEW);
  assert.deepEqual(calls, [
    ['settings:preview', DEFAULT_DESKTOP_SETTINGS, request],
  ]);
});

test('opens the native ADB executable picker through the desktop boundary', async () => {
  const calls: Array<[string, ...unknown[]]> = [];
  const api = createDesktopApi(async (channel, ...args) => {
    calls.push([channel, ...args]);
    return 'C:\\Android\\platform-tools\\adb.exe';
  }, () => () => undefined);

  assert.equal(await api.selectAdbExecutable(), 'C:\\Android\\platform-tools\\adb.exe');
  assert.deepEqual(calls, [['settings:adb:select']]);
});

test('rejects invalid ADB executable picker responses', async () => {
  const api = createDesktopApi(async () => ({ path: 'C:\\untrusted.exe' }), () => () => undefined);
  await assert.rejects(api.selectAdbExecutable(), /invalid ADB executable selection/);
});

test('rejects arbitrary preview requests before invoking Electron', async () => {
  let invoked = false;
  const api = createDesktopApi(async () => {
    invoked = true;
    return SETTINGS_PREVIEW;
  }, () => () => undefined);

  await assert.rejects(
    api.previewSettings(DEFAULT_DESKTOP_SETTINGS, {
      source: 'adb', target: { kind: 'region', id: 'slot' }, shellCommand: 'anything',
    } as never),
    /Unsupported settings preview request/,
  );
  assert.equal(invoked, false);
});

test('maps only validated analyzer operations and forwards typed progress events', async () => {
  const calls: Array<[string, ...unknown[]]> = [];
  let analyzerListener: ((payload: unknown) => void) | undefined;
  const api = createDesktopApi(async (channel, ...args) => {
    calls.push([channel, ...args]);
    if (channel === 'analyzer:options') return ANALYZER_OPTIONS;
    if (channel === 'analyzer:evaluate') return ANALYZER_EVALUATION;
    if (channel === 'analyzer:debug:get') return ANALYZER_DEBUG;
    return ANALYZER_SCAN;
  }, (channel, listener) => {
    if (channel === 'analyzer:updated') analyzerListener = listener;
    return () => undefined;
  });
  const updates: AnalyzerScanSnapshot[] = [];
  api.onAnalyzerUpdated((snapshot) => updates.push(snapshot));

  assert.deepEqual(await api.getAnalyzerOptions(), ANALYZER_OPTIONS);
  assert.deepEqual(await api.evaluateAnalyzerPiece(ANALYZER_PIECE), ANALYZER_EVALUATION);
  assert.deepEqual(await api.getAnalyzerScan(), ANALYZER_SCAN);
  assert.deepEqual(await api.startAnalyzerScan(), ANALYZER_SCAN);
  assert.deepEqual(await api.cancelAnalyzerScan('job-1'), ANALYZER_SCAN);
  assert.deepEqual(await api.getAnalyzerDebug(), ANALYZER_DEBUG);
  analyzerListener?.(ANALYZER_SCAN);
  analyzerListener?.({ state: 'running', progress: 9 });

  assert.deepEqual(calls, [
    ['analyzer:options'],
    ['analyzer:evaluate', ANALYZER_PIECE],
    ['analyzer:scan:get'],
    ['analyzer:scan:start'],
    ['analyzer:scan:cancel', 'job-1'],
    ['analyzer:debug:get'],
  ]);
  assert.deepEqual(updates, [ANALYZER_SCAN]);
});

test('rejects arbitrary analyzer input before invoking Electron', async () => {
  let invoked = false;
  const api = createDesktopApi(async () => {
    invoked = true;
    return ANALYZER_SCAN;
  }, () => () => undefined);

  await assert.rejects(api.evaluateAnalyzerPiece({ ...ANALYZER_PIECE, shellCommand: 'anything' } as never), /Unsupported analyzer piece/);
  await assert.rejects(api.cancelAnalyzerScan(''), /job id is required/);
  assert.equal(invoked, false);
});

test('maps only validated aggregate optimizer inventory operations', async () => {
  const calls: Array<[string, ...unknown[]]> = [];
  const api = createDesktopApi(async (channel, ...args) => {
    calls.push([channel, ...args]);
    if (channel === 'optimizer:inventory:get') return EMPTY_INVENTORY;
    if (channel === 'optimizer:inventory:capture:start') return { state: 'capturing' };
    if (channel === 'optimizer:inventory:capture:finish') return IMPORT_RESULT;
    if (channel === 'optimizer:inventory:reset') return RESET_RESULT;
    return { outcome: 'imported', import: IMPORT_RESULT };
  }, () => () => undefined);

  assert.deepEqual(await api.getOptimizerInventory(), EMPTY_INVENTORY);
  assert.deepEqual(await api.selectOptimizerInventoryFile(), {
    outcome: 'imported', import: IMPORT_RESULT,
  });
  assert.deepEqual(await api.startOptimizerInventoryCapture(), { state: 'capturing' });
  assert.deepEqual(await api.finishOptimizerInventoryCapture(), IMPORT_RESULT);
  assert.deepEqual(await api.resetOptimizerData(), RESET_RESULT);
  assert.deepEqual(calls, [
    ['optimizer:inventory:get'],
    ['optimizer:inventory:import'],
    ['optimizer:inventory:capture:start'],
    ['optimizer:inventory:capture:finish'],
    ['optimizer:inventory:reset'],
  ]);
});

test('rejects optimizer inventory payloads that expose paths or raw data', async () => {
  const api = createDesktopApi(async (channel) => (
    channel === 'optimizer:inventory:get'
      ? { ...EMPTY_INVENTORY, sourcePath: 'C:/private/gear.txt' }
      : { outcome: 'cancelled', sourcePath: 'C:/private/gear.txt' }
  ), () => () => undefined);

  await assert.rejects(api.getOptimizerInventory(), /invalid optimizer inventory snapshot/);
  await assert.rejects(api.selectOptimizerInventoryFile(), /invalid optimizer inventory import result/);

  const invalidReset = createDesktopApi(
    async () => ({ ...RESET_RESULT, privatePath: 'C:/private/results' }),
    () => () => undefined,
  );
  await assert.rejects(invalidReset.resetOptimizerData(), /invalid optimizer data reset result/i);
});

test('maps only validated optimizer hero and profile channels', async () => {
  const calls: Array<[string, ...unknown[]]> = [];
  const api = createDesktopApi(async (channel, ...args) => {
    calls.push([channel, ...args]);
    if (channel === 'optimizer:hero:search') return HERO_SEARCH;
    if (channel === 'optimizer:hero:details') return HERO_DETAILS;
    if (channel === 'optimizer:artifact:search') return ARTIFACT_SEARCH;
    return HERO_DRAFT;
  }, () => () => undefined);
  assert.deepEqual(await api.searchOptimizerHeroes('Achates'), HERO_SEARCH);
  assert.deepEqual(await api.getOptimizerHeroDetails(HERO_DETAILS.hero.heroId), HERO_DETAILS);
  assert.deepEqual(await api.searchOptimizerArtifacts('Rod'), ARTIFACT_SEARCH);
  assert.deepEqual(await api.loadOptimizerHeroDraft(HERO_DETAILS.hero.heroId), HERO_DRAFT);
  assert.deepEqual(await api.saveOptimizerHeroDraft(HERO_DRAFT.draft), HERO_DRAFT);
  assert.deepEqual(calls.map(([channel]) => channel), [
    'optimizer:hero:search', 'optimizer:hero:details', 'optimizer:artifact:search',
    'optimizer:profile:load', 'optimizer:profile:save',
  ]);
});

test('rejects unbounded searches, arbitrary drafts, and raw profile responses before rendering', async () => {
  let invoked = false;
  const rejecting = createDesktopApi(async () => {
    invoked = true;
    return HERO_DRAFT;
  }, () => () => undefined);
  await assert.rejects(rejecting.searchOptimizerHeroes('', 51), /Unsupported optimizer hero search/);
  await assert.rejects(rejecting.searchOptimizerArtifacts('', 0), /Unsupported optimizer artifact search/);
  await assert.rejects(rejecting.saveOptimizerHeroDraft({ ...HERO_DRAFT.draft, shellCommand: 'anything' } as never), /Unsupported optimizer hero draft/);
  assert.equal(invoked, false);

  const raw = createDesktopApi(async () => ({ ...HERO_DRAFT, sourcePath: 'C:/private/profile.json' }), () => () => undefined);
  await assert.rejects(raw.loadOptimizerHeroDraft(HERO_DETAILS.hero.heroId), /invalid hero draft/);
});

test('maps only strict optimizer search channels and progress events', async () => {
  const calls: Array<[string, ...unknown[]]> = [];
  let listener: ((payload: unknown) => void) | undefined;
  const api = createDesktopApi(async (channel, ...args) => {
    calls.push([channel, ...args]);
    return OPTIMIZER_SEARCH;
  }, (channel, eventListener) => {
    if (channel === 'optimizer:search:updated') listener = eventListener;
    return () => undefined;
  });
  const updates: OptimizerSearchSnapshot[] = [];
  api.onOptimizerSearchUpdated((snapshot) => updates.push(snapshot));

  assert.deepEqual(await api.getOptimizerSearch(), OPTIMIZER_SEARCH);
  assert.deepEqual(await api.startOptimizerSearch(HERO_DRAFT.draft), OPTIMIZER_SEARCH);
  assert.deepEqual(await api.cancelOptimizerSearch('optimizer-search-9'), OPTIMIZER_SEARCH);
  assert.deepEqual(await api.retryOptimizerSearchWithCpu('optimizer-search-9'), OPTIMIZER_SEARCH);
  listener?.(OPTIMIZER_SEARCH);
  listener?.({ ...OPTIMIZER_SEARCH, pieceIds: ['private-id'] });

  assert.deepEqual(calls, [
    ['optimizer:search:get'],
    ['optimizer:search:start', HERO_DRAFT.draft],
    ['optimizer:search:cancel', 'optimizer-search-9'],
    ['optimizer:search:retry-cpu', 'optimizer-search-9'],
  ]);
  assert.deepEqual(updates, [OPTIMIZER_SEARCH]);
});

test('rejects arbitrary optimizer search input and raw responses', async () => {
  let invoked = false;
  const rejecting = createDesktopApi(async () => {
    invoked = true;
    return OPTIMIZER_SEARCH;
  }, () => () => undefined);
  await assert.rejects(rejecting.cancelOptimizerSearch(''), /job id is required/);
  await assert.rejects(rejecting.retryOptimizerSearchWithCpu(''), /job id is required/);
  await assert.rejects(
    rejecting.startOptimizerSearch({ ...HERO_DRAFT.draft, shellCommand: 'anything' } as never),
    /Unsupported optimizer hero draft/,
  );
  assert.equal(invoked, false);

  const raw = createDesktopApi(
    async () => ({ ...OPTIMIZER_SEARCH, resultPath: 'C:/private/results' }),
    () => () => undefined,
  );
  await assert.rejects(raw.getOptimizerSearch(), /invalid optimizer search snapshot/);
});

test('maps only the consent-first update channels and typed events', async () => {
  const calls: Array<[string, ...unknown[]]> = [];
  let listener: ((payload: unknown) => void) | undefined;
  const api = createDesktopApi(async (channel, ...args) => {
    calls.push([channel, ...args]);
    if (channel === 'update:apply') return UPDATE_APPLY;
    if (channel === 'update:open-release') return null;
    return UPDATE;
  }, (channel, eventListener) => {
    if (channel === 'update:changed') listener = eventListener;
    return () => undefined;
  });
  const updates: UpdateSnapshot[] = [];
  api.onUpdateChanged((snapshot) => updates.push(snapshot));

  assert.deepEqual(await api.getUpdate(), UPDATE);
  assert.deepEqual(await api.checkForUpdates(), UPDATE);
  assert.deepEqual(await api.downloadUpdate(), UPDATE);
  assert.deepEqual(await api.installUpdateOnQuit(), UPDATE);
  assert.deepEqual(
    await api.applyUpdate({ unsavedChanges: true, confirmActiveWork: false }),
    UPDATE_APPLY,
  );
  assert.equal(await api.openUpdateRelease(), undefined);
  listener?.(UPDATE);
  listener?.({ ...UPDATE, privatePath: 'C:/private/update' });

  assert.deepEqual(calls, [
    ['update:get'],
    ['update:check'],
    ['update:download'],
    ['update:install-on-quit'],
    ['update:apply', { unsavedChanges: true, confirmActiveWork: false }],
    ['update:open-release'],
  ]);
  assert.deepEqual(updates, [UPDATE]);
});

test('rejects arbitrary update requests and malformed bridge responses', async () => {
  let invoked = false;
  const rejecting = createDesktopApi(async () => {
    invoked = true;
    return UPDATE_APPLY;
  }, () => () => undefined);
  await assert.rejects(
    rejecting.applyUpdate({ unsavedChanges: true, confirmActiveWork: false, command: 'anything' } as never),
    /Unsupported update apply request/,
  );
  assert.equal(invoked, false);

  const raw = createDesktopApi(
    async () => ({ ...UPDATE, downloadPath: 'C:/private/update' }),
    () => () => undefined,
  );
  await assert.rejects(raw.getUpdate(), /invalid update snapshot/);
});
