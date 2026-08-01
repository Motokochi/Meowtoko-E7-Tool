import assert from 'node:assert/strict';
import type { ChildProcessWithoutNullStreams } from 'node:child_process';
import { EventEmitter } from 'node:events';
import { PassThrough, Writable } from 'node:stream';
import { test } from 'node:test';

import {
  BackendClient,
  BackendClientError,
  type BackendLogRecord,
} from './backend-client';
import type { AnalyzerOptions, AnalyzerPiece, AnalyzerScanSnapshot } from './shared/analyzer';
import type { EnhancementOptions, EnhancementSnapshot } from './shared/enhancement';
import {
  DESKTOP_PROTOCOL_VERSION,
  type PingResult,
  type RpcRequest,
} from './shared/protocol';
import type { HealthSnapshot } from './shared/health';
import { EMPTY_INVENTORY, IMPORT_RESULT, RESET_RESULT } from './optimizer-inventory-fixtures';
import { ARTIFACT_SEARCH, HERO_DETAILS, HERO_DRAFT, HERO_SEARCH } from './optimizer-profile-fixtures';
import type { OptimizerSearchSnapshot } from './shared/optimizer-search';
import {
  DEFAULT_DESKTOP_SETTINGS,
  type SettingsPreview,
  type SettingsSnapshot,
} from './shared/settings';

type RequestHandler = (request: RpcRequest, process: FakeBackendProcess) => void;

const PING_RESULT: PingResult = {
  protocolVersion: DESKTOP_PROTOCOL_VERSION,
  backendVersion: 'test-backend',
  pythonVersion: '3.13.0',
  pid: 42,
};

const HEALTH_SNAPSHOT: HealthSnapshot = {
  overall: 'ready',
  checkedAt: '2026-07-19T00:00:00Z',
  capabilities: [{
    id: 'backend',
    title: 'Application backend',
    state: 'ready',
    summary: 'Ready',
    required: true,
    actions: [],
    metadata: {},
  }],
};

const SETTINGS_SNAPSHOT: SettingsSnapshot = {
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
  sequence: 7,
  jobId: 'optimizer-search-7',
  requestId: 'optimizer-request-7',
  state: 'running',
  backend: 'cpu',
  totalPermutations: '900719925474099312345',
  searchedPermutations: '123456789',
  categoryCounts: { exact: '10', oneAway: '20', twoAway: '30' },
  elapsedSeconds: 1.25,
  canCancel: true,
  resultAvailable: false,
  resultRunId: null,
  failure: null,
};
const ANALYZER_DEBUG = { available: true, jobId: 'job-1', text: 'debug', artifacts: ['crop.png'] };
const ENHANCEMENT_OPTIONS: EnhancementOptions = {
  modes: [
    { id: 'adb', label: 'ADB', description: 'Emulator', requiredCapabilities: ['packet', 'adb'] },
  ],
  maxRetainedLogs: 200,
};
const ENHANCEMENT_SNAPSHOT: EnhancementSnapshot = {
  jobId: 'enhance-1', state: 'running', stage: 'capture', message: 'Capturing', progress: 0.2,
  pieceNumber: 1, logs: ['Started'], options: { mode: 'adb', allowDestroy: false, maxPieces: 1 },
};
const ENHANCEMENT_DEBUG = { available: true, jobId: 'enhance-1', text: 'debug', artifacts: ['latest.json'] };

class FakeBackendProcess extends EventEmitter {
  readonly stdout = new PassThrough();
  readonly stderr = new PassThrough();
  readonly stdin: Writable;
  readonly requests: RpcRequest[] = [];
  killed = false;
  exitCode: number | null = null;
  killCount = 0;
  stdinEnded = false;
  private inputBuffer = '';

  constructor(private readonly handler: RequestHandler) {
    super();
    this.stdin = new Writable({
      write: (chunk, _encoding, callback) => {
        this.inputBuffer += chunk.toString();
        this.consumeInput();
        callback();
      },
      final: (callback) => {
        this.stdinEnded = true;
        callback();
      },
    });
  }

  asChildProcess(): ChildProcessWithoutNullStreams {
    return this as unknown as ChildProcessWithoutNullStreams;
  }

  reply(request: RpcRequest, result: unknown): void {
    this.stdout.write(`${JSON.stringify({
      protocol: DESKTOP_PROTOCOL_VERSION,
      id: request.id,
      ok: true,
      result,
    })}\n`);
  }

  fail(request: RpcRequest, code: string, message: string, data?: unknown): void {
    this.stdout.write(`${JSON.stringify({
      protocol: DESKTOP_PROTOCOL_VERSION,
      id: request.id,
      ok: false,
      error: { code, message, ...(data === undefined ? {} : { data }) },
    })}\n`);
  }

  emitExit(code: number | null, signal: NodeJS.Signals | null = null): void {
    if (this.exitCode !== null || this.killed && code !== null) {
      return;
    }
    this.exitCode = code;
    this.emit('exit', code, signal);
  }

  kill(): boolean {
    this.killed = true;
    this.killCount += 1;
    setImmediate(() => this.emitExit(null, 'SIGTERM'));
    return true;
  }

  private consumeInput(): void {
    let newline = this.inputBuffer.indexOf('\n');
    while (newline >= 0) {
      const line = this.inputBuffer.slice(0, newline).trim();
      this.inputBuffer = this.inputBuffer.slice(newline + 1);
      if (line) {
        const request = JSON.parse(line) as RpcRequest;
        this.requests.push(request);
        this.handler(request, this);
      }
      newline = this.inputBuffer.indexOf('\n');
    }
  }
}

function normalHandler(request: RpcRequest, process: FakeBackendProcess): void {
  if (request.method === 'system.ping') {
    process.reply(request, PING_RESULT);
  } else if (request.method === 'system.shutdown') {
    process.reply(request, { accepted: true });
    setImmediate(() => process.emitExit(0));
  }
}

function createClient(
  processes: FakeBackendProcess[],
  logs: BackendLogRecord[] = [],
  timeoutMs = 50,
): { client: BackendClient; spawnCount: () => number } {
  let spawned = 0;
  let requestId = 0;
  const client = new BackendClient({
    launch: { command: 'fake-backend', args: [] },
    spawnProcess: () => {
      const process = processes[spawned];
      if (!process) {
        throw new Error(`No fake process configured for spawn ${spawned + 1}.`);
      }
      spawned += 1;
      return process.asChildProcess();
    },
    logger: (record) => logs.push(record),
    requestId: () => `request-${++requestId}`,
    requestTimeoutMs: timeoutMs,
    shutdownTimeoutMs: timeoutMs,
    startupTimeoutMs: timeoutMs,
  });
  return { client, spawnCount: () => spawned };
}

function hasErrorCode(code: string): (error: unknown) => boolean {
  return (error) => error instanceof BackendClientError && error.code === code;
}

test('coalesces concurrent starts and creates exactly one process', async () => {
  const process = new FakeBackendProcess(normalHandler);
  const { client, spawnCount } = createClient([process]);

  const first = client.start();
  const second = client.start();

  assert.strictEqual(first, second);
  assert.deepEqual(await first, PING_RESULT);
  assert.deepEqual(await second, PING_RESULT);
  assert.equal(spawnCount(), 1);
  await client.stop();
});

test('stops gracefully through system.shutdown without force-killing', async () => {
  const process = new FakeBackendProcess(normalHandler);
  const { client } = createClient([process]);
  await client.start();

  const firstStop = client.stop();
  const secondStop = client.stop();

  assert.strictEqual(firstStop, secondStop);
  await firstStop;
  assert.equal(process.requests.at(-1)?.method, 'system.shutdown');
  assert.equal(process.stdinEnded, true);
  assert.equal(process.killCount, 0);
});

test('coalesces a restart requested while graceful shutdown is in progress', async () => {
  const stopping = new FakeBackendProcess((request, child) => {
    if (request.method === 'system.ping') {
      child.reply(request, PING_RESULT);
    } else if (request.method === 'system.shutdown') {
      child.reply(request, { accepted: true });
    }
  });
  const replacement = new FakeBackendProcess(normalHandler);
  const { client, spawnCount } = createClient([stopping, replacement]);
  await client.start();

  const stop = client.stop();
  const firstRestart = client.start();
  const secondRestart = client.start();

  assert.strictEqual(firstRestart, secondRestart);
  stopping.emitExit(0);
  await stop;
  assert.deepEqual(await firstRestart, PING_RESULT);
  assert.equal(spawnCount(), 2);
  await client.stop();
});

test('bounds startup time, terminates the hung process, and can restart', async () => {
  const hung = new FakeBackendProcess(() => undefined);
  const replacement = new FakeBackendProcess(normalHandler);
  const { client, spawnCount } = createClient([hung, replacement], [], 25);

  await assert.rejects(client.start(), hasErrorCode('startup_timeout'));
  assert.equal(hung.killCount, 1);

  assert.deepEqual(await client.start(), PING_RESULT);
  assert.equal(spawnCount(), 2);
  await client.stop();
});

test('applies a bounded timeout to requests after startup', async () => {
  let pingCount = 0;
  const process = new FakeBackendProcess((request, child) => {
    if (request.method === 'system.ping' && ++pingCount === 1) {
      child.reply(request, PING_RESULT);
    } else if (request.method === 'system.shutdown') {
      child.reply(request, { accepted: true });
      setImmediate(() => child.emitExit(0));
    }
  });
  const { client } = createClient([process], [], 25);
  await client.start();

  await assert.rejects(client.ping(), hasErrorCode('request_timeout'));
  await client.stop();
});

test('cancels pending requests when graceful shutdown begins', async () => {
  let pingCount = 0;
  const process = new FakeBackendProcess((request, child) => {
    if (request.method === 'system.ping') {
      pingCount += 1;
      if (pingCount === 1) {
        child.reply(request, PING_RESULT);
      }
    } else if (request.method === 'system.shutdown') {
      child.reply(request, { accepted: true });
      setImmediate(() => child.emitExit(0));
    }
  });
  const { client } = createClient([process]);
  await client.start();

  const pendingPing = client.ping();
  await new Promise<void>((resolve) => setImmediate(resolve));
  const stop = client.stop();

  await assert.rejects(pendingPing, hasErrorCode('backend_stopped'));
  await stop;
});

test('ignores malformed uncorrelated output and captures structured stderr', async () => {
  const logs: BackendLogRecord[] = [];
  const process = new FakeBackendProcess((request, child) => {
    if (request.method === 'system.ping') {
      child.stdout.write('not-json\n');
      child.reply(request, PING_RESULT);
    } else {
      normalHandler(request, child);
    }
  });
  const { client } = createClient([process], logs);

  assert.deepEqual(await client.start(), PING_RESULT);
  process.stderr.write(`${JSON.stringify({
    level: 'info',
    event: 'backend.test_log',
    message: 'Structured log received.',
    data: { value: 7 },
  })}\n`);
  await new Promise<void>((resolve) => setImmediate(resolve));

  assert.ok(logs.some((record) => record.event === 'protocol.invalid_json'));
  assert.ok(logs.some((record) => record.event === 'backend.test_log' && record.level === 'info'));
  await client.stop();
});

test('rejects a correlated incompatible response without killing the backend', async () => {
  let pingCount = 0;
  const process = new FakeBackendProcess((request, child) => {
    if (request.method === 'system.ping') {
      pingCount += 1;
      if (pingCount === 2) {
        child.stdout.write(`${JSON.stringify({
          protocol: DESKTOP_PROTOCOL_VERSION + 1,
          id: request.id,
          ok: true,
          result: PING_RESULT,
        })}\n`);
      } else {
        child.reply(request, PING_RESULT);
      }
    } else {
      normalHandler(request, child);
    }
  });
  const { client } = createClient([process]);
  await client.start();

  await assert.rejects(client.ping(), hasErrorCode('protocol_error'));
  assert.deepEqual(await client.ping(), PING_RESULT);
  assert.equal(process.killCount, 0);
  await client.stop();
});

test('rejects pending work on crash and starts a replacement process', async () => {
  let firstPing = true;
  const crashed = new FakeBackendProcess((request, child) => {
    if (request.method === 'system.ping' && firstPing) {
      firstPing = false;
      child.reply(request, PING_RESULT);
    }
  });
  const replacement = new FakeBackendProcess(normalHandler);
  const { client, spawnCount } = createClient([crashed, replacement]);
  await client.start();

  const pendingPing = client.ping();
  await new Promise<void>((resolve) => setImmediate(resolve));
  assert.equal(crashed.requests.filter((request) => request.method === 'system.ping').length, 2);
  crashed.emitExit(7);

  await assert.rejects(pendingPing, hasErrorCode('backend_exited'));
  assert.deepEqual(await client.start(), PING_RESULT);
  assert.equal(spawnCount(), 2);
  await client.stop();
});

test('validates health responses and forwards typed progress events', async () => {
  const process = new FakeBackendProcess((request, child) => {
    if (request.method === 'health.get') {
      child.reply(request, HEALTH_SNAPSHOT);
    } else {
      normalHandler(request, child);
    }
  });
  const { client } = createClient([process]);
  const updates: HealthSnapshot[] = [];
  const unsubscribe = client.onHealthUpdated((snapshot) => updates.push(snapshot));

  assert.deepEqual(await client.getHealth(), HEALTH_SNAPSHOT);
  process.stdout.write(`${JSON.stringify({
    protocol: DESKTOP_PROTOCOL_VERSION,
    event: 'health.updated',
    payload: HEALTH_SNAPSHOT,
  })}\n`);
  await new Promise<void>((resolve) => setImmediate(resolve));

  assert.deepEqual(updates, [HEALTH_SNAPSHOT]);
  unsubscribe();
  await client.stop();
});

test('validates settings responses and forwards typed update events', async () => {
  const process = new FakeBackendProcess((request, child) => {
    if (request.method === 'settings.get' || request.method === 'settings.update') {
      child.reply(request, SETTINGS_SNAPSHOT);
    } else {
      normalHandler(request, child);
    }
  });
  const { client } = createClient([process]);
  const updates: SettingsSnapshot[] = [];
  const unsubscribe = client.onSettingsUpdated((snapshot) => updates.push(snapshot));

  assert.deepEqual(await client.getSettings(), SETTINGS_SNAPSHOT);
  assert.deepEqual(
    await client.updateSettings('settings-r1', { appearance: { theme: 'dark' } }),
    SETTINGS_SNAPSHOT,
  );
  process.stdout.write(`${JSON.stringify({
    protocol: DESKTOP_PROTOCOL_VERSION,
    event: 'settings.updated',
    payload: SETTINGS_SNAPSHOT,
  })}\n`);
  await new Promise<void>((resolve) => setImmediate(resolve));

  assert.deepEqual(updates, [SETTINGS_SNAPSHOT]);
  unsubscribe();
  await client.stop();
});

test('validates ADB-only unsaved preview responses', async () => {
  const process = new FakeBackendProcess((request, child) => {
    if (request.method === 'settings.preview') child.reply(request, SETTINGS_PREVIEW);
    else normalHandler(request, child);
  });
  const { client } = createClient([process]);
  const request = { source: 'adb', target: { kind: 'region', id: 'slot' } } as const;

  assert.deepEqual(await client.previewSettings(DEFAULT_DESKTOP_SETTINGS, request), SETTINGS_PREVIEW);
  assert.deepEqual(
    process.requests.filter(({ method }) => method.startsWith('settings.')).map(({ method, params }) => ({ method, params })),
    [
      { method: 'settings.preview', params: { settings: DEFAULT_DESKTOP_SETTINGS, request } },
    ],
  );
  await client.stop();
});

test('preserves typed settings conflict details from the backend', async () => {
  const process = new FakeBackendProcess((request, child) => {
    if (request.method === 'settings.update') {
      child.fail(request, 'settings_conflict', 'Reload and try again.', { revision: 'new' });
    } else {
      normalHandler(request, child);
    }
  });
  const { client } = createClient([process]);

  await assert.rejects(
    client.updateSettings('old', { targetWindow: 'Test' }),
    (error: unknown) => error instanceof BackendClientError
      && error.code === 'settings_conflict'
      && (error.data as { revision: string }).revision === 'new',
  );
  await client.stop();
});

test('validates analyzer responses and forwards typed scan events', async () => {
  const process = new FakeBackendProcess((request, child) => {
    if (request.method === 'analyzer.options') child.reply(request, ANALYZER_OPTIONS);
    else if (request.method === 'analyzer.evaluate') child.reply(request, ANALYZER_EVALUATION);
    else if (request.method === 'analyzer.debug.get') child.reply(request, ANALYZER_DEBUG);
    else if (request.method.startsWith('analyzer.scan.')) child.reply(request, ANALYZER_SCAN);
    else normalHandler(request, child);
  });
  const { client } = createClient([process]);
  const updates: AnalyzerScanSnapshot[] = [];
  const unsubscribe = client.onAnalyzerUpdated((snapshot) => updates.push(snapshot));

  assert.deepEqual(await client.getAnalyzerOptions(), ANALYZER_OPTIONS);
  assert.deepEqual(await client.evaluateAnalyzerPiece(ANALYZER_PIECE), ANALYZER_EVALUATION);
  assert.deepEqual(await client.getAnalyzerScan(), ANALYZER_SCAN);
  assert.deepEqual(await client.startAnalyzerScan(), ANALYZER_SCAN);
  assert.deepEqual(await client.cancelAnalyzerScan('job-1'), ANALYZER_SCAN);
  assert.deepEqual(await client.getAnalyzerDebug(), ANALYZER_DEBUG);
  process.stdout.write(`${JSON.stringify({
    protocol: DESKTOP_PROTOCOL_VERSION,
    event: 'analyzer.updated',
    payload: ANALYZER_SCAN,
  })}\n`);
  await new Promise<void>((resolve) => setImmediate(resolve));

  assert.deepEqual(updates, [ANALYZER_SCAN]);
  unsubscribe();
  await client.stop();
});

test('preserves typed analyzer validation details from the backend', async () => {
  const process = new FakeBackendProcess((request, child) => {
    if (request.method === 'analyzer.evaluate') {
      child.fail(request, 'analyzer_validation', 'Invalid analyzer piece.', { issues: { slot: 'bad' } });
    } else {
      normalHandler(request, child);
    }
  });
  const { client } = createClient([process]);

  await assert.rejects(
    client.evaluateAnalyzerPiece(ANALYZER_PIECE),
    (error: unknown) => error instanceof BackendClientError
      && error.code === 'analyzer_validation'
      && (error.data as { issues: { slot: string } }).issues.slot === 'bad',
  );
  await client.stop();
});

test('validates enhancement responses and forwards typed job events', async () => {
  const process = new FakeBackendProcess((request, child) => {
    if (request.method === 'enhancement.options') child.reply(request, ENHANCEMENT_OPTIONS);
    else if (request.method === 'enhancement.debug.get') child.reply(request, ENHANCEMENT_DEBUG);
    else if (request.method.startsWith('enhancement.job.')) child.reply(request, ENHANCEMENT_SNAPSHOT);
    else normalHandler(request, child);
  });
  const { client } = createClient([process]);
  const updates: EnhancementSnapshot[] = [];
  const unsubscribe = client.onEnhancementUpdated((snapshot) => updates.push(snapshot));

  assert.deepEqual(await client.getEnhancementOptions(), ENHANCEMENT_OPTIONS);
  assert.deepEqual(await client.getEnhancementJob(), ENHANCEMENT_SNAPSHOT);
  assert.deepEqual(
    await client.startEnhancementJob({ mode: 'adb', allowDestroy: false, maxPieces: 1 }),
    ENHANCEMENT_SNAPSHOT,
  );
  assert.deepEqual(await client.cancelEnhancementJob('enhance-1'), ENHANCEMENT_SNAPSHOT);
  assert.deepEqual(await client.getEnhancementDebug(), ENHANCEMENT_DEBUG);
  process.stdout.write(`${JSON.stringify({
    protocol: DESKTOP_PROTOCOL_VERSION,
    event: 'enhancement.updated',
    payload: ENHANCEMENT_SNAPSHOT,
  })}\n`);
  await new Promise<void>((resolve) => setImmediate(resolve));

  assert.deepEqual(updates, [ENHANCEMENT_SNAPSHOT]);
  unsubscribe();
  await client.stop();
});

test('preserves typed enhancement validation details from the backend', async () => {
  const process = new FakeBackendProcess((request, child) => {
    if (request.method === 'enhancement.job.start') {
      child.fail(request, 'enhancement_validation', 'Invalid enhancement options.', { issues: { mode: 'bad' } });
    } else {
      normalHandler(request, child);
    }
  });
  const { client } = createClient([process]);
  await assert.rejects(
    client.startEnhancementJob({ mode: 'adb', allowDestroy: false, maxPieces: 1 }),
    (error: unknown) => error instanceof BackendClientError
      && error.code === 'enhancement_validation'
      && (error.data as { issues: { mode: string } }).issues.mode === 'bad',
  );
  await client.stop();
});

test('validates aggregate optimizer status and imports through bounded methods', async () => {
  const privatePath = 'C:/private/player-name/gear.txt';
  const process = new FakeBackendProcess((request, child) => {
    if (request.method === 'optimizer.inventory.get') child.reply(request, EMPTY_INVENTORY);
    else if (request.method === 'optimizer.inventory.import') child.reply(request, IMPORT_RESULT);
    else if (request.method === 'optimizer.inventory.capture.start') child.reply(request, { state: 'capturing' });
    else if (request.method === 'optimizer.inventory.capture.finish') child.reply(request, IMPORT_RESULT);
    else if (request.method === 'optimizer.inventory.reset') child.reply(request, RESET_RESULT);
    else normalHandler(request, child);
  });
  const { client } = createClient([process]);

  assert.deepEqual(await client.getOptimizerInventory(), EMPTY_INVENTORY);
  assert.deepEqual(await client.importOptimizerInventory(privatePath), IMPORT_RESULT);
  assert.deepEqual(await client.startOptimizerInventoryCapture(), { state: 'capturing' });
  assert.deepEqual(await client.finishOptimizerInventoryCapture(), IMPORT_RESULT);
  assert.deepEqual(await client.resetOptimizerData(), RESET_RESULT);
  assert.deepEqual(
    process.requests
      .filter(({ method }) => method.startsWith('optimizer.inventory.'))
      .map(({ method, params }) => ({ method, params })),
    [
      { method: 'optimizer.inventory.get', params: {} },
      { method: 'optimizer.inventory.import', params: { sourcePath: privatePath } },
      { method: 'optimizer.inventory.capture.start', params: {} },
      { method: 'optimizer.inventory.capture.finish', params: {} },
      { method: 'optimizer.inventory.reset', params: {} },
    ],
  );
  assert.doesNotMatch(JSON.stringify(IMPORT_RESULT), /player-name|gear\.txt|C:\//);
  await client.stop();
});

test('rejects invalid optimizer aggregates and preserves structured import failures', async () => {
  let importCount = 0;
  const process = new FakeBackendProcess((request, child) => {
    if (request.method === 'optimizer.inventory.get') {
      child.reply(request, { ...EMPTY_INVENTORY, sourcePath: 'C:/private/gear.txt' });
    } else if (request.method === 'optimizer.inventory.import') {
      importCount += 1;
      child.fail(
        request,
        'optimizer_inventory_import_failed',
        'The selected document is not valid JSON.',
        { category: 'document', issueCode: 'malformed-json', documentPath: '$' },
      );
    } else {
      normalHandler(request, child);
    }
  });
  const { client } = createClient([process]);

  await assert.rejects(client.getOptimizerInventory(), hasErrorCode('protocol_error'));
  await assert.rejects(
    client.importOptimizerInventory('C:/private/gear.txt'),
    (error: unknown) => error instanceof BackendClientError
      && error.code === 'optimizer_inventory_import_failed'
      && (error.data as { issueCode: string }).issueCode === 'malformed-json'
      && !error.message.includes('C:/private'),
  );
  await assert.rejects(client.importOptimizerInventory(''), hasErrorCode('protocol_error'));
  assert.equal(importCount, 1);
  await client.stop();
});

test('validates bounded optimizer profile operations and exact request parameters', async () => {
  const process = new FakeBackendProcess((request, child) => {
    if (request.method === 'optimizer.hero.search') child.reply(request, HERO_SEARCH);
    else if (request.method === 'optimizer.hero.details') child.reply(request, HERO_DETAILS);
    else if (request.method === 'optimizer.artifact.search') child.reply(request, ARTIFACT_SEARCH);
    else if (request.method === 'optimizer.profile.load' || request.method === 'optimizer.profile.save') child.reply(request, HERO_DRAFT);
    else normalHandler(request, child);
  });
  const { client } = createClient([process]);

  assert.deepEqual(await client.searchOptimizerHeroes('Achates', 20), HERO_SEARCH);
  assert.deepEqual(await client.getOptimizerHeroDetails(HERO_DETAILS.hero.heroId), HERO_DETAILS);
  assert.deepEqual(await client.searchOptimizerArtifacts('Rod', 20), ARTIFACT_SEARCH);
  assert.deepEqual(await client.loadOptimizerHeroDraft(HERO_DETAILS.hero.heroId), HERO_DRAFT);
  assert.deepEqual(await client.saveOptimizerHeroDraft(HERO_DRAFT.draft), HERO_DRAFT);
  assert.deepEqual(
    process.requests.filter(({ method }) => method.startsWith('optimizer.hero.') || method.startsWith('optimizer.artifact.') || method.startsWith('optimizer.profile.')).map(({ method, params }) => ({ method, params })),
    [
      { method: 'optimizer.hero.search', params: { query: 'Achates', limit: 20 } },
      { method: 'optimizer.hero.details', params: { heroId: HERO_DETAILS.hero.heroId } },
      { method: 'optimizer.artifact.search', params: { query: 'Rod', limit: 20 } },
      { method: 'optimizer.profile.load', params: { heroId: HERO_DETAILS.hero.heroId } },
      { method: 'optimizer.profile.save', params: { draft: HERO_DRAFT.draft } },
    ],
  );
  await client.stop();
});

test('rejects raw profile responses and preserves read-only future-schema failures', async () => {
  const process = new FakeBackendProcess((request, child) => {
    if (request.method === 'optimizer.hero.search') child.reply(request, { ...HERO_SEARCH, rawSource: {} });
    else if (request.method === 'optimizer.profile.load') child.fail(request, 'optimizer_profile_failed', 'Newer profile is read-only.', {
      category: 'storage', issueCode: 'profile-future-version', readOnly: true,
    });
    else normalHandler(request, child);
  });
  const { client } = createClient([process]);
  await assert.rejects(client.searchOptimizerHeroes('Achates'), hasErrorCode('protocol_error'));
  await assert.rejects(
    client.loadOptimizerHeroDraft(HERO_DETAILS.hero.heroId),
    (error: unknown) => error instanceof BackendClientError
      && error.code === 'optimizer_profile_failed'
      && (error.data as { readOnly: boolean }).readOnly,
  );
  await assert.rejects(client.saveOptimizerHeroDraft({ ...HERO_DRAFT.draft, sourcePath: 'private' } as never), hasErrorCode('protocol_error'));
  await client.stop();
});

test('validates narrow optimizer search methods and typed progress events', async () => {
  const process = new FakeBackendProcess((request, child) => {
    if (request.method.startsWith('optimizer.search.')) child.reply(request, OPTIMIZER_SEARCH);
    else normalHandler(request, child);
  });
  const { client } = createClient([process]);
  const updates: OptimizerSearchSnapshot[] = [];
  const unsubscribe = client.onOptimizerSearchUpdated((snapshot) => updates.push(snapshot));

  assert.deepEqual(await client.getOptimizerSearch(), OPTIMIZER_SEARCH);
  assert.deepEqual(await client.startOptimizerSearch(HERO_DRAFT.draft), OPTIMIZER_SEARCH);
  assert.deepEqual(await client.cancelOptimizerSearch('optimizer-search-7'), OPTIMIZER_SEARCH);
  assert.deepEqual(await client.retryOptimizerSearchWithCpu('optimizer-search-7'), OPTIMIZER_SEARCH);
  process.stdout.write(`${JSON.stringify({
    protocol: DESKTOP_PROTOCOL_VERSION,
    event: 'optimizer.search.updated',
    payload: OPTIMIZER_SEARCH,
  })}\n`);
  process.stdout.write(`${JSON.stringify({
    protocol: DESKTOP_PROTOCOL_VERSION,
    event: 'optimizer.search.updated',
    payload: { ...OPTIMIZER_SEARCH, rows: [] },
  })}\n`);
  await new Promise<void>((resolve) => setImmediate(resolve));

  assert.deepEqual(
    process.requests
      .filter(({ method }) => method.startsWith('optimizer.search.'))
      .map(({ method, params }) => ({ method, params })),
    [
      { method: 'optimizer.search.get', params: {} },
      { method: 'optimizer.search.start', params: { draft: HERO_DRAFT.draft } },
      { method: 'optimizer.search.cancel', params: { jobId: 'optimizer-search-7' } },
      { method: 'optimizer.search.retry-cpu', params: { jobId: 'optimizer-search-7' } },
    ],
  );
  assert.deepEqual(updates, [OPTIMIZER_SEARCH]);
  unsubscribe();
  await client.stop();
});

test('rejects invalid optimizer search responses and preserves typed recovery failures', async () => {
  const process = new FakeBackendProcess((request, child) => {
    if (request.method === 'optimizer.search.get') {
      child.reply(request, { ...OPTIMIZER_SEARCH, resultPath: 'C:/private/results' });
    } else if (request.method === 'optimizer.search.retry-cpu') {
      child.fail(request, 'optimizer_search_recovery_unavailable', 'CPU retry is not available.');
    } else {
      normalHandler(request, child);
    }
  });
  const { client } = createClient([process]);

  await assert.rejects(client.getOptimizerSearch(), hasErrorCode('protocol_error'));
  await assert.rejects(
    client.retryOptimizerSearchWithCpu('optimizer-search-7'),
    (error: unknown) => error instanceof BackendClientError
      && error.code === 'optimizer_search_recovery_unavailable',
  );
  await assert.rejects(client.cancelOptimizerSearch(''), hasErrorCode('protocol_error'));
  await assert.rejects(
    client.startOptimizerSearch({ ...HERO_DRAFT.draft, sourcePath: 'private' } as never),
    hasErrorCode('protocol_error'),
  );
  await client.stop();
});

test('validates the bounded optimizer build equip request and response', async () => {
  const equipResult = {
    state: 'equipped',
    heroName: 'Setsuka',
    equippedCount: 6,
    alreadyEquipped: 1,
    movedFromOtherHeroes: 1,
    newlyEquipped: 4,
    unequippedFromHero: 0,
    inventoryEquippedItems: 233,
  };
  const process = new FakeBackendProcess((request, child) => {
    if (request.method === 'optimizer.results.equip') child.reply(request, equipResult);
    else normalHandler(request, child);
  });
  const { client } = createClient([process]);
  const request = { runId: 'run-8', queryId: 'query-8', rowKey: 'query.0' };

  assert.deepEqual(await client.equipOptimizerResultBuild(request), equipResult);
  assert.deepEqual(
    process.requests.find(({ method }) => method === 'optimizer.results.equip')?.params,
    request,
  );
  await assert.rejects(
    client.equipOptimizerResultBuild({ ...request, rowKey: '' }),
    hasErrorCode('protocol_error'),
  );
  await client.stop();

  const invalidProcess = new FakeBackendProcess((backendRequest, child) => {
    if (backendRequest.method === 'optimizer.results.equip') {
      child.reply(backendRequest, { ...equipResult, stableItemIds: ['private'] });
    } else {
      normalHandler(backendRequest, child);
    }
  });
  const invalidClient = createClient([invalidProcess]).client;
  await assert.rejects(
    invalidClient.equipOptimizerResultBuild(request),
    hasErrorCode('protocol_error'),
  );
  await invalidClient.stop();
});
