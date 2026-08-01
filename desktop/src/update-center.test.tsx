import assert from 'node:assert/strict';
import { test } from 'node:test';
import { renderToStaticMarkup } from 'react-dom/server';

import type { UpdateApplyResult, UpdateSnapshot } from './shared/update';
import { UpdateBanner, UpdateSettingsCard } from './update-center';

const RELEASE = {
  version: '0.1.19',
  title: 'Meowtoko E7 Tool 0.1.19',
  notes: 'Consent-first update.',
  publishedAt: '2026-07-25T00:00:00.000Z',
  downloadBytes: '734003200',
};

function snapshot(
  state: UpdateSnapshot['state'],
  overrides: Partial<UpdateSnapshot> = {},
): UpdateSnapshot {
  return {
    state,
    currentVersion: '0.1.18',
    checkedAt: '2026-07-25T00:00:00.000Z',
    release: ['available', 'downloading', 'downloaded', 'applying'].includes(state)
      ? RELEASE
      : null,
    progress: state === 'downloading' ? { kind: 'indeterminate' } : null,
    installOnQuit: false,
    error: state === 'error' ? 'Update service is temporarily unavailable.' : null,
    ...overrides,
  };
}

const actions = {
  onApply: async (): Promise<UpdateApplyResult> => ({
    status: 'applying',
    activeWork: [],
    snapshot: snapshot('applying'),
  }),
  onCheck: async () => undefined,
  onDownload: async () => undefined,
  onInstallLater: async () => undefined,
  onOpenRelease: async () => undefined,
};

test('offers an available update without starting its download', () => {
  const markup = renderToStaticMarkup(
    <UpdateBanner {...actions} snapshot={snapshot('available')} />,
  );
  assert.match(markup, /Meowtoko E7 Tool 0\.1\.19 is available/);
  assert.match(markup, /save work first; Meowtoko E7 Tool restarts automatically/);
  assert.match(markup, /Download and restart/);
  assert.match(markup, /Release notes/);
  assert.match(markup, /Later/);
});

test('shows bounded downloading and downloaded states', () => {
  const downloading = renderToStaticMarkup(
    <UpdateBanner {...actions} snapshot={snapshot('downloading')} />,
  );
  assert.match(downloading, /role="progressbar"/);
  assert.match(downloading, /Meowtoko E7 Tool will restart when ready/);

  const downloaded = renderToStaticMarkup(
    <UpdateBanner {...actions} snapshot={snapshot('downloaded')} />,
  );
  assert.match(downloaded, /Restart and install/);
  assert.match(downloaded, /Install on close/);
});

test('keeps manual update controls and the installed version in Settings', () => {
  const current = renderToStaticMarkup(
    <UpdateSettingsCard
      snapshot={snapshot('current')}
      onCheck={actions.onCheck}
      onDownload={actions.onDownload}
      onOpenRelease={actions.onOpenRelease}
    />,
  );
  assert.match(current, /Meowtoko E7 Tool 0\.1\.18/);
  assert.match(current, /latest stable version/);
  assert.match(current, /Check for updates/);
  assert.doesNotMatch(current, /Download 700 MB/);

  const available = renderToStaticMarkup(
    <UpdateSettingsCard
      snapshot={snapshot('available')}
      onCheck={actions.onCheck}
      onDownload={actions.onDownload}
      onOpenRelease={actions.onOpenRelease}
    />,
  );
  assert.match(available, /Download and restart 700 MB/);
  assert.match(available, /View release notes/);
});
