import assert from 'node:assert/strict';
import test from 'node:test';

import {
  automaticUpdatesEnabled,
  parseLatestRelease,
  UpdateService,
  type UpdateFeed,
  type UpdateFetch,
} from './update-service';
import {
  compareStableVersions,
  isUpdateApplyRequest,
  isUpdateSnapshot,
} from './shared/update';

class FakeFeed implements UpdateFeed {
  feedUrls: string[] = [];
  checks = 0;
  installs = 0;
  listeners = new Map<string, Set<(...args: unknown[]) => void>>();

  setFeedURL(options: { url: string }): void {
    this.feedUrls.push(options.url);
  }

  checkForUpdates(): void {
    this.checks += 1;
  }

  quitAndInstall(): void {
    this.installs += 1;
  }

  on(event: 'update-downloaded' | 'update-not-available' | 'error', listener: (...args: unknown[]) => void): void {
    const current = this.listeners.get(event) ?? new Set();
    current.add(listener);
    this.listeners.set(event, current);
  }

  removeListener(event: 'update-downloaded' | 'update-not-available' | 'error', listener: (...args: unknown[]) => void): void {
    this.listeners.get(event)?.delete(listener);
  }

  emit(event: 'update-downloaded' | 'update-not-available' | 'error'): void {
    for (const listener of this.listeners.get(event) ?? []) listener();
  }
}

function release(version = '1.2.4'): Record<string, unknown> {
  return {
    draft: false,
    prerelease: false,
    tag_name: `v${version}`,
    name: `Meowtoko E7 Tool ${version}`,
    body: 'A bounded stable release.',
    published_at: '2026-07-25T12:00:00Z',
    html_url: `https://github.com/Motokochi/Meowtoko-E7-Tool/releases/tag/v${version}`,
    assets: [
      { name: `Meowtoko-E7-Tool-${version}-Setup.exe`, size: 350_000_000 },
      { name: `E7Hub-${version}-full.nupkg`, size: 349_000_000 },
      { name: 'RELEASES', size: 77 },
    ],
  };
}

function fetchRelease(payload: unknown, ok = true): UpdateFetch {
  return async () => ({
    ok,
    status: ok ? 200 : 503,
    json: async () => payload,
  });
}

function service(options: {
  payload?: unknown;
  activeWork?: string[];
  enabled?: boolean;
} = {}): { feed: FakeFeed; service: UpdateService; stopped: { value: number }; before: { value: number } } {
  const feed = new FakeFeed();
  const stopped = { value: 0 };
  const before = { value: 0 };
  return {
    feed,
    stopped,
    before,
    service: new UpdateService({
      currentVersion: '1.2.3',
      enabled: options.enabled ?? true,
      fetch: fetchRelease(options.payload ?? release()),
      feed,
      activeWork: async () => options.activeWork ?? [],
      stopBackend: async () => { stopped.value += 1; },
      beforeInstall: () => { before.value += 1; },
      now: () => new Date('2026-07-25T13:00:00Z'),
    }),
  };
}

test('stable versions compare mathematically and reject prereleases', () => {
  assert.equal(compareStableVersions('1.10.0', '1.9.9'), 1);
  assert.equal(compareStableVersions('1.2.3', '1.2.3'), 0);
  assert.throws(() => compareStableVersions('1.2.4-beta.1', '1.2.3'));
});

test('latest release parser requires stable GitHub and Squirrel identities', () => {
  assert.equal(parseLatestRelease(release('1.2.3'), '1.2.3'), null);
  assert.equal(parseLatestRelease(release('1.2.2'), '1.2.3'), null);
  assert.equal(parseLatestRelease(release(), '1.2.3')?.metadata.downloadBytes, '349000000');
  assert.throws(() => parseLatestRelease({ ...release(), draft: true }, '1.2.3'));
  const missing = release();
  missing.assets = [{ name: 'README.txt', size: 10 }];
  assert.throws(() => parseLatestRelease(missing, '1.2.3'), /missing required Squirrel assets/);
});

test('metadata check never touches Squirrel before explicit download consent', async () => {
  const context = service();
  const checked = await context.service.check();
  assert.equal(checked.state, 'available');
  assert.equal(context.feed.checks, 0);
  assert.deepEqual(context.feed.feedUrls, []);

  const downloading = context.service.download();
  assert.equal(downloading.state, 'downloading');
  assert.equal(context.feed.checks, 1);
  assert.deepEqual(context.feed.feedUrls, [
    'https://github.com/Motokochi/Meowtoko-E7-Tool/releases/download/v1.2.4/',
  ]);
  assert.equal(context.service.download().state, 'downloading');
  assert.equal(context.feed.checks, 1);
  context.feed.emit('update-downloaded');
  assert.equal(context.service.get().state, 'applying');
  await new Promise<void>((resolve) => setImmediate(resolve));
  assert.equal(context.feed.checks, 1);
  assert.equal(context.service.get().state, 'applying');
  assert.equal(context.stopped.value, 1);
  assert.equal(context.before.value, 1);
  assert.equal(context.feed.installs, 1);
  context.service.dispose();
});

test('offline checks are recoverable and do not disable the app', async () => {
  const feed = new FakeFeed();
  const update = new UpdateService({
    currentVersion: '1.2.3',
    enabled: true,
    fetch: fetchRelease({}, false),
    feed,
    activeWork: async () => [],
    stopBackend: async () => undefined,
    beforeInstall: () => undefined,
  });
  const snapshot = await update.check();
  assert.equal(snapshot.state, 'error');
  assert.match(snapshot.error ?? '', /fully usable/);
  assert.equal(feed.checks, 0);
  update.dispose();
});

test('a bounded metadata timeout remains recoverable', async () => {
  const feed = new FakeFeed();
  let cancelled = 0;
  const update = new UpdateService({
    currentVersion: '1.2.3',
    enabled: true,
    fetch: async (_url, { signal }) => {
      if (signal.aborted) throw new Error('aborted');
      return new Promise((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
      });
    },
    feed,
    activeWork: async () => [],
    stopBackend: async () => undefined,
    beforeInstall: () => undefined,
    setTimeout: (callback) => {
      callback();
      return 7;
    },
    clearTimeout: () => { cancelled += 1; },
  });
  const snapshot = await update.check();
  assert.equal(snapshot.state, 'error');
  assert.match(snapshot.error ?? '', /timed out/);
  assert.equal(cancelled, 1);
  assert.equal(feed.checks, 0);
  update.dispose();
});

test('IPC update validators reject extra keys and forged actions', async () => {
  const context = service();
  assert.ok(isUpdateSnapshot(context.service.get()));
  assert.equal(isUpdateSnapshot({ ...context.service.get(), feedUrl: 'https://example.invalid' }), false);
  assert.equal(isUpdateApplyRequest({ unsavedChanges: false, confirmActiveWork: true }), true);
  assert.equal(isUpdateApplyRequest({ unsavedChanges: false, confirmActiveWork: true, feed: 'x' }), false);
  context.service.dispose();
});

test('development, smoke, audit, and Squirrel maintenance modes skip updates', () => {
  assert.equal(automaticUpdatesEnabled({ isPackaged: false, environment: {}, argv: [] }), false);
  assert.equal(automaticUpdatesEnabled({ isPackaged: true, environment: { NODE_ENV: 'test' }, argv: [] }), false);
  assert.equal(automaticUpdatesEnabled({ isPackaged: true, environment: { E7_DESKTOP_SMOKE_TEST: '1' }, argv: [] }), false);
  assert.equal(automaticUpdatesEnabled({ isPackaged: true, environment: {}, argv: ['--squirrel-updated'] }), false);
  assert.equal(automaticUpdatesEnabled({ isPackaged: true, environment: {}, argv: [] }), true);
});
