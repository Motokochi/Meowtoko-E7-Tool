import assert from 'node:assert/strict';
import { renderToStaticMarkup } from 'react-dom/server';
import { test } from 'node:test';

import { HealthCenter, requiresHealthActionConfirmation } from './health-center';
import type { HealthSnapshot } from './shared/health';

const DEGRADED: HealthSnapshot = {
  overall: 'degraded',
  checkedAt: '2026-07-19T00:00:00Z',
  capabilities: [
    {
      id: 'ollama',
      title: 'Ollama vision',
      state: 'degraded',
      summary: 'The required vision model is missing.',
      required: false,
      actions: [{ id: 'ollama.pull_model', label: 'Download vision model', kind: 'download' }],
      metadata: {},
    },
    {
      id: 'cuda',
      title: 'GPU acceleration',
      state: 'degraded',
      summary: 'CPU mode is ready. NVIDIA GeForce RTX 5090 was detected.',
      required: false,
      actions: [{ id: 'cuda.install', label: 'Install GPU components', kind: 'install' }],
      metadata: {
        mode: 'cpu',
        nvidia: {
          detected: true,
          adapters: [{ name: 'NVIDIA GeForce RTX 5090', driverVersion: '591.44' }],
        },
      },
    },
  ],
};

test('renders degraded capabilities as usable with actionable repair controls', () => {
  const markup = renderToStaticMarkup(
    <HealthCenter snapshot={DEGRADED} onRefresh={() => undefined} onAction={() => undefined} />,
  );

  assert.match(markup, /App ready with limited features/);
  assert.match(markup, /Download vision model/);
  assert.match(markup, /Install GPU components/);
  assert.match(markup, /NVIDIA path detected/);
  assert.match(markup, /CPU mode remains/);
  assert.doesNotMatch(markup, /A required capability needs attention/);
  assert.equal(requiresHealthActionConfirmation('cuda.install'), true);
  assert.equal(requiresHealthActionConfirmation('ollama.start'), false);
});

test('renders cancellation as a safe action during GPU component setup', () => {
  const running: HealthSnapshot = {
    ...DEGRADED,
    overall: 'checking',
    operation: {
      id: 'cuda-setup-1',
      actionId: 'cuda.install',
      state: 'running',
      message: 'Installing optional GPU components…',
      progress: 0.25,
    },
  };

  const markup = renderToStaticMarkup(
    <HealthCenter snapshot={running} onRefresh={() => undefined} onAction={() => undefined} />,
  );

  assert.match(markup, /Cancel GPU setup/);
  assert.match(markup, /value="0.25"/);
});

test('renders determinate operation progress and disables competing actions', () => {
  const running: HealthSnapshot = {
    ...DEGRADED,
    overall: 'checking',
    operation: {
      id: 'pull-1',
      actionId: 'ollama.pull_model',
      state: 'running',
      message: 'Downloading model layers…',
      progress: 0.75,
    },
  };
  const markup = renderToStaticMarkup(
    <HealthCenter snapshot={running} onRefresh={() => undefined} onAction={() => undefined} />,
  );

  assert.match(markup, /Downloading model layers/);
  assert.match(markup, /<progress[^>]*value="0.75"/);
  assert.match(markup, /<button[^>]*disabled=""/);
});
