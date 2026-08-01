import assert from 'node:assert/strict';
import { test } from 'node:test';

import { focusPrimaryWindow } from './window-lifecycle';

function fakeWindow({ destroyed = false, minimized = false, visible = true } = {}) {
  const calls: string[] = [];
  return {
    calls,
    window: {
      focus: () => calls.push('focus'),
      isDestroyed: () => destroyed,
      isMinimized: () => minimized,
      isVisible: () => visible,
      restore: () => calls.push('restore'),
      show: () => calls.push('show'),
    },
  };
}

test('second-instance focus restores and shows the existing window', () => {
  const primary = fakeWindow({ minimized: true, visible: false });
  assert.equal(focusPrimaryWindow(primary.window), true);
  assert.deepEqual(primary.calls, ['restore', 'show', 'focus']);
});

test('second-instance focus safely ignores missing or destroyed windows', () => {
  assert.equal(focusPrimaryWindow(null), false);
  const destroyed = fakeWindow({ destroyed: true });
  assert.equal(focusPrimaryWindow(destroyed.window), false);
  assert.deepEqual(destroyed.calls, []);
});
