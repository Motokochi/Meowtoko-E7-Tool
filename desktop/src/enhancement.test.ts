import assert from 'node:assert/strict';
import { test } from 'node:test';

import { createDesktopApi } from './desktop-api';
import {
  enhancementReadiness,
  isEnhancementDebug,
  isEnhancementOptions,
  isEnhancementSnapshot,
  isEnhancementStartOptions,
  maxPiecesError,
  parseMaxPieces,
  requiresDestroyConfirmation,
  shouldAcceptEnhancementSnapshot,
  type EnhancementOptions,
  type EnhancementSnapshot,
} from './shared/enhancement';
import type { HealthCapability, HealthSnapshot } from './shared/health';

export const ENHANCEMENT_OPTIONS: EnhancementOptions = {
  modes: [
    { id: 'adb', label: 'Android emulator (ADB)', description: 'ADB', requiredCapabilities: ['packet', 'adb'] },
  ],
  maxRetainedLogs: 200,
};

export const ENHANCEMENT_SNAPSHOT: EnhancementSnapshot = {
  jobId: 'job-1', state: 'running', stage: 'capture', message: 'Capturing…', progress: 0.2,
  pieceNumber: 1, logs: ['Processing piece 1 via ADB.'],
  options: { mode: 'adb', allowDestroy: false, maxPieces: 1 },
};

function capability(id: HealthCapability['id'], state: HealthCapability['state']): HealthCapability {
  return { id, state, title: id, summary: state, required: false, actions: [], metadata: {} };
}

const HEALTH: HealthSnapshot = {
  overall: 'ready', checkedAt: 'now',
  capabilities: [
    capability('tesseract', 'ready'),
    capability('ollama', 'ready'),
    capability('packet', 'ready'),
    capability('adb', 'unavailable'),
  ],
};

test('validates enhancement options, job snapshots, and safe debug artifacts', () => {
  assert.equal(isEnhancementOptions(ENHANCEMENT_OPTIONS), true);
  assert.equal(isEnhancementSnapshot(ENHANCEMENT_SNAPSHOT), true);
  assert.equal(isEnhancementStartOptions({ mode: 'adb', allowDestroy: false, maxPieces: null }), true);
  assert.equal(isEnhancementStartOptions({ mode: 'adb', allowDestroy: false, maxPieces: 0 }), false);
  assert.equal(isEnhancementStartOptions({ mode: 'adb', allowDestroy: false, maxPieces: 1, command: 'tap' }), false);
  assert.equal(isEnhancementDebug({ available: true, artifacts: ['latest.json'] }), true);
  assert.equal(isEnhancementDebug({ available: true, artifacts: ['../private.json'] }), false);
  assert.equal(isEnhancementDebug({ available: true, artifacts: ['C:\\private.json'] }), false);
});

test('requires both packet capture and ADB for enhancement automation', () => {
  const adb = enhancementReadiness(HEALTH, ENHANCEMENT_OPTIONS, 'adb');
  assert.equal(adb.available, false);
  assert.match(adb.reason ?? '', /adb/);
  const packet = enhancementReadiness({
    ...HEALTH,
    capabilities: [
      capability('packet', 'unavailable'),
      capability('adb', 'ready'),
    ],
  }, ENHANCEMENT_OPTIONS, 'adb');
  assert.equal(packet.available, false);
  assert.match(packet.reason ?? '', /packet/);
});

test('validates piece limits and requires confirmation only for destructive runs', () => {
  assert.equal(parseMaxPieces('0'), null);
  assert.equal(parseMaxPieces(' 25 '), 25);
  assert.equal(maxPiecesError('0'), undefined);
  assert.match(maxPiecesError('-1') ?? '', /whole number/);
  assert.equal(requiresDestroyConfirmation(false), false);
  assert.equal(requiresDestroyConfirmation(true), true);
});

test('never lets a late command response move a finished job backwards', () => {
  const cancelled: EnhancementSnapshot = {
    ...ENHANCEMENT_SNAPSHOT,
    state: 'cancelled',
    stage: 'cancelled',
  };
  assert.equal(shouldAcceptEnhancementSnapshot(cancelled, {
    ...ENHANCEMENT_SNAPSHOT,
    state: 'cancelling',
  }), false);
  assert.equal(shouldAcceptEnhancementSnapshot(cancelled, {
    ...ENHANCEMENT_SNAPSHOT,
    state: 'succeeded',
  }), false);
  assert.equal(shouldAcceptEnhancementSnapshot(cancelled, {
    ...ENHANCEMENT_SNAPSHOT,
    jobId: 'job-2',
  }), true);
});

test('desktop API exposes only validated enhancement operations and events', async () => {
  const calls: Array<[string, ...unknown[]]> = [];
  let eventListener: ((payload: unknown) => void) | undefined;
  const debug = { available: true, artifacts: ['latest.json'] };
  const api = createDesktopApi(async (channel, ...args) => {
    calls.push([channel, ...args]);
    if (channel === 'enhancement:options') return ENHANCEMENT_OPTIONS;
    if (channel === 'enhancement:debug:get') return debug;
    return ENHANCEMENT_SNAPSHOT;
  }, (channel, listener) => {
    if (channel === 'enhancement:updated') eventListener = listener;
    return () => undefined;
  });
  const updates: EnhancementSnapshot[] = [];
  api.onEnhancementUpdated((snapshot) => updates.push(snapshot));

  assert.deepEqual(await api.getEnhancementOptions(), ENHANCEMENT_OPTIONS);
  assert.deepEqual(await api.getEnhancementJob(), ENHANCEMENT_SNAPSHOT);
  assert.deepEqual(await api.startEnhancementJob({ mode: 'adb', allowDestroy: false, maxPieces: 1 }), ENHANCEMENT_SNAPSHOT);
  assert.deepEqual(await api.cancelEnhancementJob('job-1'), ENHANCEMENT_SNAPSHOT);
  assert.deepEqual(await api.getEnhancementDebug(), debug);
  eventListener?.(ENHANCEMENT_SNAPSHOT);
  eventListener?.({ state: 'running', progress: 4 });
  assert.deepEqual(updates, [ENHANCEMENT_SNAPSHOT]);
  assert.deepEqual(calls, [
    ['enhancement:options'], ['enhancement:job:get'],
    ['enhancement:job:start', { mode: 'adb', allowDestroy: false, maxPieces: 1 }],
    ['enhancement:job:cancel', 'job-1'], ['enhancement:debug:get'],
  ]);

  const never = createDesktopApi(async () => { throw new Error('must not invoke'); }, () => () => undefined);
  await assert.rejects(
    never.startEnhancementJob({ mode: 'adb', allowDestroy: false, maxPieces: 1, command: 'click' } as never),
    /Unsupported enhancement options/,
  );
  await assert.rejects(never.cancelEnhancementJob(''), /job id is required/);
});
