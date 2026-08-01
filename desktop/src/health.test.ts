import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  aggregateHealthState,
  isHealthSnapshot,
  overallHealthLabel,
  shouldAcceptHealthSnapshot,
  type HealthCapability,
  type HealthSnapshot,
} from './shared/health';

function item(
  id: HealthCapability['id'],
  state: HealthCapability['state'],
  required = false,
): HealthCapability {
  return {
    id,
    title: id,
    state,
    summary: `${id} is ${state}`,
    required,
    actions: [],
    metadata: {},
  };
}

test('aggregates ready, checking, degraded, and required-error states', () => {
  assert.equal(aggregateHealthState([item('backend', 'ready', true)]), 'ready');
  assert.equal(aggregateHealthState([item('backend', 'checking', true)]), 'checking');
  assert.equal(aggregateHealthState([
    item('backend', 'ready', true),
    item('cuda', 'unavailable'),
  ]), 'degraded');
  assert.equal(aggregateHealthState([
    item('backend', 'ready', true),
    item('storage', 'error', true),
  ]), 'error');
});

test('validates typed snapshots and rejects invalid progress', () => {
  const valid: HealthSnapshot = {
    overall: 'degraded',
    checkedAt: '2026-07-19T00:00:00Z',
    capabilities: [item('cuda', 'degraded')],
    operation: {
      id: 'operation-1',
      actionId: 'ollama.pull_model',
      state: 'running',
      message: 'Downloading',
      progress: 0.5,
    },
  };

  assert.equal(isHealthSnapshot(valid), true);
  assert.equal(isHealthSnapshot({
    ...valid,
    operation: { ...valid.operation, progress: 2 },
  }), false);
  assert.equal(isHealthSnapshot({
    ...valid,
    capabilities: [{
      ...valid.capabilities[0],
      actions: [{ id: 'cuda.install', label: 'Install GPU components', kind: 'install' }],
    }],
    operation: { ...valid.operation, actionId: 'cuda.install', state: 'cancelled' },
  }), true);
  assert.equal(isHealthSnapshot({
    ...valid,
    capabilities: [{
      ...valid.capabilities[0],
      actions: [{ id: 'cuda.install', label: 'Install GPU components', kind: 'anything' }],
    }],
  }), false);
});

test('rejects late health responses after a newer terminal operation snapshot', () => {
  const completed: HealthSnapshot = {
    overall: 'degraded',
    checkedAt: '2026-07-22T12:00:01Z',
    capabilities: [item('cuda', 'degraded')],
    operation: {
      id: 'cuda-setup-1',
      actionId: 'cuda.install',
      state: 'succeeded',
      message: 'GPU setup complete',
      progress: 1,
    },
  };
  const stale: HealthSnapshot = {
    ...completed,
    checkedAt: '2026-07-22T12:00:00Z',
    operation: { ...completed.operation!, state: 'running', progress: 0.05 },
  };

  assert.equal(shouldAcceptHealthSnapshot(completed, stale), false);
  assert.equal(shouldAcceptHealthSnapshot(stale, completed), true);
  assert.equal(shouldAcceptHealthSnapshot(null, stale), true);
});

test('presents degraded mode as usable rather than failed', () => {
  assert.equal(overallHealthLabel('degraded'), 'App ready with limited features');
  assert.equal(overallHealthLabel('error'), 'A required capability needs attention');
});
