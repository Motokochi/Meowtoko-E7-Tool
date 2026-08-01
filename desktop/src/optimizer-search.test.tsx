import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { renderToStaticMarkup } from 'react-dom/server';

import { OptimizerSearchStatus } from './optimizer-search-status';
import {
  initialOptimizerSearchWorkspaceState,
  optimizerSearchWorkspaceReducer,
} from './optimizer-search-workspace';
import {
  formatOptimizerSearchCount,
  isOptimizerSearchSnapshot,
  optimizerSearchProgress,
  type OptimizerSearchSnapshot,
} from './shared/optimizer-search';

const RUNNING: OptimizerSearchSnapshot = {
  sequence: 7,
  jobId: 'job-7',
  requestId: 'request.desktop-search.7',
  state: 'running',
  backend: 'cuda',
  totalPermutations: '18014398509481984',
  searchedPermutations: '9007199254740992',
  categoryCounts: { exact: '123456789012345', oneAway: '2', twoAway: '3' },
  elapsedSeconds: 12.5,
  canCancel: true,
  resultAvailable: false,
  resultRunId: null,
  failure: null,
};

const noop = (): void => undefined;

function render(snapshot: OptimizerSearchSnapshot | null, disabled = false): string {
  return renderToStaticMarkup(
    <OptimizerSearchStatus
      disabled={disabled}
      disabledReason="Choose a valid hero."
      error={null}
      onCancel={noop}
      onRetryCpu={noop}
      onStart={noop}
      pending={false}
      snapshot={snapshot}
    />,
  );
}

test('strict snapshot guard preserves large decimals and rejects rows, numeric coercion, and paths', () => {
  assert.equal(isOptimizerSearchSnapshot(RUNNING), true);
  assert.equal(optimizerSearchProgress(RUNNING), 50);
  assert.equal(
    formatOptimizerSearchCount('9007199254740993').replace(/\D/g, ''),
    '9007199254740993',
  );
  assert.equal(isOptimizerSearchSnapshot({ ...RUNNING, totalPermutations: 10 }), false);
  assert.equal(isOptimizerSearchSnapshot({ ...RUNNING, searchedPermutations: '18014398509481985' }), false);
  assert.equal(isOptimizerSearchSnapshot({
    ...RUNNING,
    categoryCounts: { exact: RUNNING.searchedPermutations, oneAway: '1', twoAway: '0' },
  }), false);
  assert.equal(isOptimizerSearchSnapshot({ ...RUNNING, rows: [] }), false);
  assert.equal(isOptimizerSearchSnapshot({
    ...RUNNING,
    state: 'failed',
    canCancel: false,
    failure: {
      stage: 'cuda-search', code: 'launch-failed',
      message: 'C:\\Users\\Private\\driver.dll failed', cpuRecoveryAvailable: true,
    },
  }), false);
});

test('workspace reducer ignores stale events and resets sequence on backend restart', () => {
  const current = optimizerSearchWorkspaceReducer(initialOptimizerSearchWorkspaceState, {
    type: 'snapshot-received', snapshot: RUNNING,
  });
  const stale = optimizerSearchWorkspaceReducer(current, {
    type: 'snapshot-received', snapshot: { ...RUNNING, sequence: 6, searchedPermutations: '1' },
  });
  assert.equal(stale, current);
  assert.equal(stale.snapshot?.searchedPermutations, RUNNING.searchedPermutations);
  assert.deepEqual(
    optimizerSearchWorkspaceReducer(stale, { type: 'session-reset' }),
    initialOptimizerSearchWorkspaceState,
  );
});

test('running status exposes actual backend, exact counts, determinate progress, and cancel', () => {
  const markup = render(RUNNING);
  assert.match(markup, /CUDA GPU/);
  assert.match(markup, /Search running/);
  assert.match(markup, /<progress[^>]*aria-label="Optimizer search progress"[^>]*max="100"[^>]*value="50"/);
  assert.match(markup, /Cancel search/);
  assert.match(markup, /123(?:,|\.)456(?:,|\.)789(?:,|\.)012(?:,|\.)345/);
  assert.doesNotMatch(markup, /(?:dense_item_ids|itemIds|rows|sourcePath)/);
  const pending = renderToStaticMarkup(
    <OptimizerSearchStatus
      disabled={false}
      disabledReason=""
      error={null}
      onCancel={noop}
      onRetryCpu={noop}
      onStart={noop}
      pending
      snapshot={RUNNING}
    />,
  );
  assert.match(pending, /<button[^>]*disabled=""[^>]*>.*Cancel search/s);
});

test('overflow and CUDA failure render safe guidance and an explicit CPU recovery action', () => {
  const overflow = render({
    ...RUNNING,
    sequence: 8,
    state: 'overflowed',
    canCancel: false,
    searchedPermutations: '12000000',
    categoryCounts: { exact: '5000001', oneAway: '0', twoAway: '0' },
  });
  assert.match(overflow, /More than 5,000,000 exact completed-set builds matched/);
  assert.match(overflow, /No partial result set was kept/);
  assert.match(overflow, /primary ranges, sets or main stats/);

  const failed = render({
    ...RUNNING,
    sequence: 9,
    state: 'failed',
    canCancel: false,
    failure: {
      stage: 'cuda-search',
      code: 'launch-failed',
      message: 'GPU search stopped safely. No partial results were kept.',
      cpuRecoveryAvailable: true,
    },
  });
  assert.match(failed, /Retry with CPU/);
  assert.match(failed, /cuda-search · launch-failed/);
  assert.doesNotMatch(failed, /stack trace|\\Users\\|driver\.dll/i);
});

test('CPU fallback, completion, and cancellation remain explicit terminal states', () => {
  const cpu = render({
    ...RUNNING,
    sequence: 10,
    state: 'completed',
    backend: 'cpu',
    searchedPermutations: RUNNING.totalPermutations,
    canCancel: false,
    resultAvailable: true,
    resultRunId: 'run-cpu-10',
  });
  assert.match(cpu, />CPU</);
  assert.match(cpu, /Search complete/);
  assert.match(cpu, /Results are ready/);
  assert.match(cpu, /Start new search/);

  const cancelled = render({
    ...RUNNING,
    sequence: 11,
    state: 'cancelled',
    backend: 'cpu',
    canCancel: false,
  });
  assert.match(cancelled, /Search cancelled/);
  assert.match(cancelled, /No partial results kept/);
});

test('idle search is gated accessibly and desktop wiring remains narrow', () => {
  const markup = render(null, true);
  assert.match(markup, /<button[^>]*disabled=""[^>]*>.*Start search/s);
  assert.match(markup, /Choose a valid hero/);
  assert.match(markup, /aria-describedby="optimizer-search-disabled-reason"/);
  assert.match(markup, /aria-live="polite"[^>]*role="status"/);

  const main = readFileSync(path.join(__dirname, 'main.js'), 'utf8');
  const preload = readFileSync(path.join(__dirname, 'desktop-api.js'), 'utf8');
  for (const channel of [
    'optimizer:search:get', 'optimizer:search:start', 'optimizer:search:cancel',
    'optimizer:search:retry-cpu', 'optimizer:search:updated',
  ]) {
    assert.match(`${main}\n${preload}`, new RegExp(channel.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  }
  assert.doesNotMatch(`${main}\n${preload}`, /optimizer:search:(?:invoke|rows|items|raw)/);
});
