import {
  compareStableVersions,
  type UpdateApplyRequest,
  type UpdateApplyResult,
  type UpdateReleaseMetadata,
  type UpdateSnapshot,
} from './shared/update';

const RELEASE_API = 'https://api.github.com/repos/Motokochi/Meowtoko-E7-Tool/releases/latest';
const RELEASE_BASE = 'https://github.com/Motokochi/Meowtoko-E7-Tool/releases/download';
const RELEASE_PAGE_BASE = 'https://github.com/Motokochi/Meowtoko-E7-Tool/releases/tag';
const CHECK_INTERVAL_MS = 6 * 60 * 60 * 1_000;
const FIRST_CHECK_DELAY_MS = 30_000;
const CHECK_TIMEOUT_MS = 15_000;
type TimerHandle = number | NodeJS.Timeout;

interface GitHubAsset {
  name: string;
  size: number;
}

interface Candidate {
  metadata: UpdateReleaseMetadata;
  feedUrl: string;
  releaseUrl: string;
}

export interface UpdateFeed {
  setFeedURL(options: { url: string }): void;
  checkForUpdates(): void;
  quitAndInstall(): void;
  on(event: 'update-downloaded' | 'update-not-available' | 'error', listener: (...args: unknown[]) => void): void;
  removeListener(event: 'update-downloaded' | 'update-not-available' | 'error', listener: (...args: unknown[]) => void): void;
}

interface FetchResponse {
  ok: boolean;
  status: number;
  json(): Promise<unknown>;
}

export type UpdateFetch = (
  url: string,
  options: { headers: Record<string, string>; signal: AbortSignal },
) => Promise<FetchResponse>;

export interface UpdateServiceOptions {
  currentVersion: string;
  enabled: boolean;
  fetch: UpdateFetch;
  feed: UpdateFeed;
  activeWork(): Promise<string[]>;
  stopBackend(): Promise<void>;
  beforeInstall(): void;
  now?(): Date;
  setTimeout?(callback: () => void, milliseconds: number): TimerHandle;
  clearTimeout?(handle: TimerHandle): void;
  setInterval?(callback: () => void, milliseconds: number): TimerHandle;
  clearInterval?(handle: TimerHandle): void;
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function safeText(value: unknown, maximum: number, fallback: string): string {
  if (typeof value !== 'string') return fallback;
  return value.replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, maximum) || fallback;
}

function releaseAsset(value: unknown): value is GitHubAsset {
  return record(value)
    && typeof value.name === 'string'
    && value.name.length > 0
    && Number.isSafeInteger(value.size)
    && Number(value.size) > 0;
}

export function parseLatestRelease(value: unknown, currentVersion: string): Candidate | null {
  if (!record(value)
    || value.draft !== false
    || value.prerelease !== false
    || typeof value.tag_name !== 'string'
    || !/^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(value.tag_name)
    || typeof value.published_at !== 'string'
    || !Number.isFinite(Date.parse(value.published_at))
    || typeof value.html_url !== 'string'
    || !Array.isArray(value.assets)
    || !value.assets.every(releaseAsset)) {
    throw new Error('GitHub returned release metadata outside the Meowtoko E7 Tool update contract.');
  }
  const version = value.tag_name.slice(1);
  if (compareStableVersions(version, currentVersion) <= 0) return null;
  const expectedPage = `${RELEASE_PAGE_BASE}/v${version}`;
  if (value.html_url !== expectedPage) {
    throw new Error('GitHub returned an unexpected Meowtoko E7 Tool release page.');
  }
  const packageName = `E7Hub-${version}-full.nupkg`;
  const setupName = `Meowtoko-E7-Tool-${version}-Setup.exe`;
  const assets = value.assets as GitHubAsset[];
  const updatePackage = assets.find((asset) => asset.name === packageName);
  if (!updatePackage
    || !assets.some((asset) => asset.name === setupName)
    || !assets.some((asset) => asset.name === 'RELEASES')) {
    throw new Error('The latest Meowtoko E7 Tool release is missing required Squirrel assets.');
  }
  return {
    metadata: {
      version,
      title: safeText(value.name, 120, `Meowtoko E7 Tool ${version}`),
      notes: safeText(value.body, 4_000, 'See the release page for details.'),
      publishedAt: value.published_at,
      downloadBytes: String(updatePackage.size),
    },
    feedUrl: `${RELEASE_BASE}/v${version}/`,
    releaseUrl: expectedPage,
  };
}

function baseSnapshot(currentVersion: string): UpdateSnapshot {
  return {
    state: 'idle',
    currentVersion,
    checkedAt: null,
    release: null,
    progress: null,
    installOnQuit: false,
    error: null,
  };
}

export class UpdateService {
  private snapshot: UpdateSnapshot;
  private candidate: Candidate | null = null;
  private listeners = new Set<(snapshot: UpdateSnapshot) => void>();
  private checkPromise: Promise<UpdateSnapshot> | null = null;
  private firstTimer: TimerHandle | null = null;
  private intervalTimer: TimerHandle | null = null;
  private checkTimer: TimerHandle | null = null;
  private controller: AbortController | null = null;
  private disposed = false;

  private readonly onDownloaded = (): void => {
    if (this.snapshot.state !== 'downloading' || !this.snapshot.release) return;
    this.publish({
      ...this.snapshot,
      state: 'applying',
      progress: null,
      installOnQuit: false,
      error: null,
    });
    void this.restartAndInstall().catch(() => {
      this.fail('The downloaded update could not restart Meowtoko E7 Tool. Reopen the app to finish installing.');
    });
  };

  private readonly onUnavailable = (): void => {
    if (this.snapshot.state !== 'downloading') return;
    this.fail('The accepted update is no longer available from its release feed.');
  };

  private readonly onFeedError = (): void => {
    if (this.snapshot.state !== 'downloading') return;
    this.fail('The update download failed. Your installed version was not changed.');
  };

  constructor(private readonly options: UpdateServiceOptions) {
    this.snapshot = baseSnapshot(options.currentVersion);
    options.feed.on('update-downloaded', this.onDownloaded);
    options.feed.on('update-not-available', this.onUnavailable);
    options.feed.on('error', this.onFeedError);
  }

  get(): UpdateSnapshot {
    return structuredClone(this.snapshot);
  }

  onChanged(listener: (snapshot: UpdateSnapshot) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  start(): void {
    if (!this.options.enabled || this.disposed || this.firstTimer || this.intervalTimer) return;
    const scheduleTimeout = this.options.setTimeout
      ?? ((callback: () => void, milliseconds: number) => setTimeout(callback, milliseconds));
    const scheduleInterval = this.options.setInterval
      ?? ((callback: () => void, milliseconds: number) => setInterval(callback, milliseconds));
    this.firstTimer = scheduleTimeout(() => {
      this.firstTimer = null;
      void this.check();
    }, FIRST_CHECK_DELAY_MS);
    this.intervalTimer = scheduleInterval(() => void this.check(), CHECK_INTERVAL_MS);
  }

  async check(): Promise<UpdateSnapshot> {
    if (!this.options.enabled || this.disposed) return this.get();
    if (this.checkPromise) return this.checkPromise;
    if (this.snapshot.state === 'downloading' || this.snapshot.state === 'downloaded' || this.snapshot.state === 'applying') {
      return this.get();
    }
    this.publish({ ...this.snapshot, state: 'checking', progress: null, error: null });
    this.controller?.abort();
    this.controller = new AbortController();
    this.checkPromise = this.performCheck(this.controller.signal).finally(() => {
      this.checkPromise = null;
    });
    return this.checkPromise;
  }

  private async performCheck(signal: AbortSignal): Promise<UpdateSnapshot> {
    let timedOut = false;
    const scheduleTimeout = this.options.setTimeout
      ?? ((callback: () => void, milliseconds: number) => setTimeout(callback, milliseconds));
    const cancelTimeout = this.options.clearTimeout
      ?? ((handle: TimerHandle) => clearTimeout(handle));
    this.checkTimer = scheduleTimeout(() => {
      timedOut = true;
      this.controller?.abort();
    }, CHECK_TIMEOUT_MS);
    try {
      const response = await this.options.fetch(RELEASE_API, {
        headers: {
          Accept: 'application/vnd.github+json',
          'User-Agent': `Meowtoko-E7-Tool/${this.options.currentVersion}`,
          'X-GitHub-Api-Version': '2022-11-28',
        },
        signal,
      });
      if (!response.ok) {
        throw new Error(`GitHub status ${response.status}`);
      }
      const candidate = parseLatestRelease(await response.json(), this.options.currentVersion);
      const checkedAt = (this.options.now?.() ?? new Date()).toISOString();
      this.candidate = candidate;
      this.publish(candidate ? {
        ...this.snapshot,
        state: 'available',
        checkedAt,
        release: candidate.metadata,
        progress: null,
        installOnQuit: false,
        error: null,
      } : {
        ...baseSnapshot(this.options.currentVersion),
        state: 'current',
        checkedAt,
      });
    } catch {
      if (!this.disposed && (!signal.aborted || timedOut)) {
        this.fail(timedOut
          ? 'Update checking timed out. Meowtoko E7 Tool remains fully usable.'
          : 'Update checking is temporarily unavailable. Meowtoko E7 Tool remains fully usable.');
      }
    } finally {
      if (this.checkTimer) cancelTimeout(this.checkTimer);
      this.checkTimer = null;
    }
    return this.get();
  }

  download(): UpdateSnapshot {
    if (this.snapshot.state === 'downloading' || this.snapshot.state === 'downloaded') {
      return this.get();
    }
    if (!this.options.enabled || !this.candidate || this.snapshot.state !== 'available') {
      throw new Error('No validated Meowtoko E7 Tool update is ready to download.');
    }
    this.options.feed.setFeedURL({ url: this.candidate.feedUrl });
    this.publish({
      ...this.snapshot,
      state: 'downloading',
      progress: { kind: 'indeterminate' },
      installOnQuit: false,
      error: null,
    });
    this.options.feed.checkForUpdates();
    return this.get();
  }

  installLater(): UpdateSnapshot {
    if (this.snapshot.state !== 'downloaded') {
      throw new Error('No downloaded update is ready to install.');
    }
    this.publish({ ...this.snapshot, installOnQuit: true });
    return this.get();
  }

  async apply(request: UpdateApplyRequest): Promise<UpdateApplyResult> {
    if (this.snapshot.state !== 'downloaded') {
      throw new Error('No downloaded update is ready to install.');
    }
    const activeWork = await this.activeWork(request.unsavedChanges);
    if (activeWork.length > 0 && !request.confirmActiveWork) {
      return { status: 'confirmation-required', activeWork, snapshot: this.get() };
    }
    this.publish({ ...this.snapshot, state: 'applying', installOnQuit: false, error: null });
    await this.restartAndInstall();
    return { status: 'applying', activeWork, snapshot: this.get() };
  }

  shouldInstallOnQuit(): boolean {
    return this.snapshot.state === 'downloaded' && this.snapshot.installOnQuit;
  }

  applyAfterShutdown(): void {
    if (!this.shouldInstallOnQuit()) return;
    this.publish({ ...this.snapshot, state: 'applying', installOnQuit: false, error: null });
    this.options.beforeInstall();
    this.options.feed.quitAndInstall();
  }

  releaseUrl(): string | null {
    return this.candidate?.releaseUrl ?? null;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.controller?.abort();
    const cancelTimeout = this.options.clearTimeout
      ?? ((handle: TimerHandle) => clearTimeout(handle));
    const cancelInterval = this.options.clearInterval
      ?? ((handle: TimerHandle) => clearInterval(handle));
    if (this.firstTimer) cancelTimeout(this.firstTimer);
    if (this.checkTimer) cancelTimeout(this.checkTimer);
    if (this.intervalTimer) cancelInterval(this.intervalTimer);
    this.firstTimer = null;
    this.checkTimer = null;
    this.intervalTimer = null;
    this.options.feed.removeListener('update-downloaded', this.onDownloaded);
    this.options.feed.removeListener('update-not-available', this.onUnavailable);
    this.options.feed.removeListener('error', this.onFeedError);
    this.listeners.clear();
  }

  private async activeWork(unsavedChanges: boolean): Promise<string[]> {
    const active = await this.options.activeWork();
    const normalized = [...new Set(active.map((item) => safeText(item, 80, 'Active work')))].slice(0, 7);
    if (unsavedChanges) normalized.push('Unsaved hero or settings changes');
    return normalized.slice(0, 8);
  }

  private async restartAndInstall(): Promise<void> {
    await this.options.stopBackend();
    this.options.beforeInstall();
    this.options.feed.quitAndInstall();
  }

  private fail(message: string): void {
    this.publish({
      ...this.snapshot,
      state: 'error',
      progress: null,
      installOnQuit: false,
      error: safeText(message, 240, 'Update operation failed.'),
    });
  }

  private publish(snapshot: UpdateSnapshot): void {
    if (this.disposed) return;
    this.snapshot = snapshot;
    const copy = this.get();
    for (const listener of this.listeners) listener(copy);
  }
}

export function automaticUpdatesEnabled(options: {
  isPackaged: boolean;
  environment: NodeJS.ProcessEnv;
  argv: readonly string[];
}): boolean {
  if (!options.isPackaged
    || options.environment.NODE_ENV === 'test'
    || options.environment.E7_DISABLE_UPDATES === '1'
    || options.environment.E7_DESKTOP_SMOKE_TEST === '1'
    || options.environment.E7_SINGLE_INSTANCE_SMOKE_TEST === '1'
    || options.environment.E7_PACKAGE_AUDIT === '1') return false;
  return !options.argv.some((argument) => argument.startsWith('--squirrel-'));
}
