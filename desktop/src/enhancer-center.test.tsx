import assert from 'node:assert/strict';
import { renderToStaticMarkup } from 'react-dom/server';
import { test } from 'node:test';

import {
  ALLOW_DESTROY_STORAGE_KEY,
  EnhancerCenter,
  readAllowDestroy,
  writeAllowDestroy,
} from './enhancer-center';
import type { EnhancementOptions, EnhancementSnapshot } from './shared/enhancement';
import type { HealthCapability, HealthSnapshot } from './shared/health';

const ENHANCEMENT_OPTIONS: EnhancementOptions = {
  modes: [
    { id: 'adb', label: 'Android device or emulator (ADB)', description: 'ADB', requiredCapabilities: ['packet', 'adb'] },
  ],
  maxRetainedLogs: 200,
};

const ENHANCEMENT_SNAPSHOT: EnhancementSnapshot = {
  jobId: 'job-1', state: 'running', stage: 'capture', message: 'Capturing…', progress: 0.2,
  pieceNumber: 1, logs: ['Processing piece 1 via ADB.'],
  options: { mode: 'adb', allowDestroy: false, maxPieces: 1 },
};

function capability(id: HealthCapability['id'], state: HealthCapability['state']): HealthCapability {
  return { id, state, title: id, summary: state, required: false, actions: [], metadata: {} };
}

const HEALTH: HealthSnapshot = {
  overall: 'ready', checkedAt: 'now', capabilities: [
    capability('tesseract', 'ready'), capability('ollama', 'ready'), capability('adb', 'unavailable'),
  ],
};

const callbacks = {
  onStart: async () => ENHANCEMENT_SNAPSHOT,
  onCancel: async () => ({ ...ENHANCEMENT_SNAPSHOT, state: 'cancelling' as const }),
  onGetDebug: async () => ({ available: true, artifacts: ['latest.json'] }),
};

test('renders the required ADB backend, bounded inputs, and explicit destructive permission', () => {
  const markup = renderToStaticMarkup(
    <EnhancerCenter
      {...callbacks}
      health={HEALTH}
      options={ENHANCEMENT_OPTIONS}
      snapshot={{ state: 'idle', stage: 'idle', message: 'Ready', progress: 0, pieceNumber: 0, logs: [] }}
    />,
  );
  assert.match(markup, /Enhance gear through ADB/);
  assert.match(markup, /Exact enhancement stats come from game packets; every tap uses ADB with a stop check first/);
  assert.match(markup, /class="enhancer-dashboard"/);
  assert.match(markup, /Run setup/);
  assert.match(markup, /ADB automation/);
  assert.match(markup, /Android device or emulator \(ADB\)/);
  assert.match(markup, /Maximum pieces/);
  assert.match(markup, /Fresh gear\.txt required/);
  assert.match(markup, /Import your latest gear\.txt in Importer before every run/);
  assert.match(markup, /consumes one basic powder to identify it/);
  assert.match(markup, /Allow destroy clicks/);
  assert.match(markup, /Requires confirmation for every run/);
  assert.match(markup, /No automation activity/);
  assert.match(markup, /<button class="button button-primary button-small" disabled="">[^]*Start automation/);
});

test('renders progress, bounded logs, safe stop, and the last decision', () => {
  const snapshot = {
    ...ENHANCEMENT_SNAPSHOT,
    progress: 0.55,
    logs: ['Processing piece 1 via ADB.', '+0 | ENHANCE'],
    lastDecision: {
      action: 'enhance' as const, reason: 'Enhancement rules passed.', currentGs: 40,
      potentialGs: 65, enhancement: 0, nextTarget: 3,
    },
  };
  const markup = renderToStaticMarkup(
    <EnhancerCenter {...callbacks} health={HEALTH} options={ENHANCEMENT_OPTIONS} snapshot={snapshot} />,
  );
  assert.match(markup, /Stop safely/);
  assert.match(markup, /<progress[^>]*value="0.55"/);
  assert.match(markup, /Enhancement run/);
  assert.match(markup, /Piece 1 of 1/);
  assert.match(markup, /Run evidence/);
  assert.match(markup, /Processing piece 1 via ADB/);
  assert.match(markup, /LAST DECISION/);
  assert.match(markup, /Enhancement rules passed/);
  assert.match(markup, /Stop is checked before every action/);
});

test('shows ADB as the required backend and reports setup status', () => {
  const markup = renderToStaticMarkup(
    <EnhancerCenter
      {...callbacks}
      health={HEALTH}
      options={ENHANCEMENT_OPTIONS}
      snapshot={{ state: 'idle', stage: 'idle', message: 'Ready', progress: 0, pieceNumber: 0, logs: [] }}
    />,
  );
  assert.match(markup, /ADB automation/);
  assert.match(markup, /Android device or emulator \(ADB\)[^]*NEEDS SETUP/);
});

test('persists the destructive-click preference until it is manually changed', () => {
  const values = new Map<string, string>();
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => {
      values.set(key, value);
    },
  };

  assert.equal(readAllowDestroy(storage), false);
  writeAllowDestroy(storage, true);
  assert.equal(values.get(ALLOW_DESTROY_STORAGE_KEY), 'true');
  assert.equal(readAllowDestroy(storage), true);
  writeAllowDestroy(storage, false);
  assert.equal(readAllowDestroy(storage), false);
});
