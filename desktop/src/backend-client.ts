import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import readline from 'node:readline';

import {
  isAnalyzerDebug,
  isAnalyzerEvaluation,
  isAnalyzerOptions,
  isAnalyzerScanSnapshot,
  type AnalyzerDebug,
  type AnalyzerEvaluation,
  type AnalyzerOptions,
  type AnalyzerPiece,
  type AnalyzerScanSnapshot,
} from './shared/analyzer';
import {
  isEnhancementDebug,
  isEnhancementOptions,
  isEnhancementSnapshot,
  type EnhancementDebug,
  type EnhancementOptions,
  type EnhancementSnapshot,
  type EnhancementStartOptions,
} from './shared/enhancement';
import {
  isHealthSnapshot,
  type HealthActionId,
  type HealthSnapshot,
} from './shared/health';
import {
  isSettingsPreview,
  isSettingsSnapshot,
  type DesktopSettings,
  type SettingsPatch,
  type SettingsPreview,
  type SettingsPreviewRequest,
  type SettingsSnapshot,
} from './shared/settings';
import {
  isOptimizerDataResetResult,
  isOptimizerInventoryCaptureState,
  isOptimizerInventoryImportResult,
  isOptimizerInventorySnapshot,
  type OptimizerDataResetResult,
  type OptimizerInventoryCaptureState,
  type OptimizerInventoryImportResult,
  type OptimizerInventorySnapshot,
} from './shared/optimizer-inventory';
import {
  isOptimizerArtifactSearchResult,
  isOptimizerHeroDetails,
  isOptimizerHeroDraft,
  isOptimizerHeroDraftEnvelope,
  isOptimizerHeroSearchResult,
  type OptimizerArtifactSearchResult,
  type OptimizerHeroDetails,
  type OptimizerHeroDraft,
  type OptimizerHeroDraftEnvelope,
  type OptimizerHeroSearchResult,
} from './shared/optimizer-profile';
import {
  isOptimizerSearchSnapshot,
  type OptimizerSearchSnapshot,
} from './shared/optimizer-search';
import {
  isOptimizerResultOptions,
  isOptimizerResultQuery,
  isOptimizerResultSnapshot,
  type OptimizerResultOptions,
  type OptimizerResultQuery,
  type OptimizerResultSnapshot,
} from './shared/optimizer-results';
import {
  isOptimizerResultDetailRequest,
  isOptimizerResultDetailSnapshot,
  isOptimizerResultEquipResult,
  type OptimizerResultDetailRequest,
  type OptimizerResultDetailSnapshot,
  type OptimizerResultEquipResult,
} from './shared/optimizer-result-detail';
import {
  isOptimizerResultExportSnapshot,
  type OptimizerResultExportFormat,
  type OptimizerResultExportSnapshot,
} from './shared/optimizer-result-export';
import {
  DESKTOP_PROTOCOL_VERSION,
  type PingResult,
  type RpcRequest,
  type RpcResponse,
  type ShutdownResult,
} from './shared/protocol';

export interface BackendLaunch {
  command: string;
  args: string[];
  cwd?: string;
  environment?: Readonly<Record<string, string>>;
  unsetEnvironment?: readonly string[];
}

export interface BackendLogRecord {
  level: 'debug' | 'info' | 'warning' | 'error';
  event: string;
  message: string;
  data?: unknown;
}

export type BackendLogger = (record: BackendLogRecord) => void;
export type BackendSpawner = (launch: BackendLaunch) => ChildProcessWithoutNullStreams;

export type BackendClientErrorCode =
  | 'backend_exited'
  | 'backend_io_error'
  | 'backend_not_running'
  | 'backend_stopped'
  | 'protocol_error'
  | 'request_timeout'
  | 'startup_timeout'
  | 'settings_conflict'
  | 'settings_validation'
  | 'settings_read_only'
  | 'settings_preview_failed'
  | 'settings_write_failed'
  | 'analyzer_validation'
  | 'analyzer_busy'
  | 'analyzer_job_not_found'
  | 'enhancement_validation'
  | 'enhancement_busy'
  | 'enhancement_job_not_found'
  | 'optimizer_inventory_status_failed'
  | 'optimizer_inventory_import_failed'
  | 'optimizer_inventory_capture_failed'
  | 'optimizer_data_reset_busy'
  | 'optimizer_data_reset_failed'
  | 'optimizer_profile_failed'
  | 'optimizer_search_busy'
  | 'optimizer_search_job_not_found'
  | 'optimizer_search_recovery_unavailable'
  | 'optimizer_result_session_unavailable'
  | 'optimizer_result_query_not_found'
  | 'optimizer_result_equip_unavailable';

export class BackendClientError extends Error {
  constructor(
    readonly code: BackendClientErrorCode,
    message: string,
    readonly data?: unknown,
  ) {
    super(message);
    this.name = 'BackendClientError';
  }
}

interface PendingRequest {
  method: string;
  resolve(value: unknown): void;
  reject(reason: Error): void;
  timeout: NodeJS.Timeout;
}

export interface BackendClientOptions {
  launch: BackendLaunch | (() => BackendLaunch);
  spawnProcess?: BackendSpawner;
  logger?: BackendLogger;
  requestId?: () => string;
  requestTimeoutMs?: number;
  shutdownTimeoutMs?: number;
  startupTimeoutMs?: number;
}

interface ParsedResponseSuccess {
  valid: true;
  response: RpcResponse;
}

interface ParsedResponseFailure {
  valid: false;
  requestId?: string;
  reason: string;
}

type ParsedResponse = ParsedResponseSuccess | ParsedResponseFailure;

const DEFAULT_REQUEST_TIMEOUT_MS = 10_000;
const DEFAULT_SHUTDOWN_TIMEOUT_MS = 3_000;
const DEFAULT_STARTUP_TIMEOUT_MS = 15_000;

export function backendEnvironment(
  launch: BackendLaunch,
  base: NodeJS.ProcessEnv = process.env,
): NodeJS.ProcessEnv {
  const environment: NodeJS.ProcessEnv = { ...base };
  for (const key of launch.unsetEnvironment ?? []) {
    delete environment[key];
  }
  Object.assign(environment, launch.environment ?? {});
  return environment;
}

function defaultSpawner(launch: BackendLaunch): ChildProcessWithoutNullStreams {
  return spawn(launch.command, launch.args, {
    cwd: launch.cwd,
    env: backendEnvironment(launch),
    stdio: 'pipe',
    windowsHide: true,
  });
}

function defaultLogger(record: BackendLogRecord): void {
  const prefix = `[python-backend:${record.event}]`;
  const details = record.data === undefined ? '' : record.data;
  if (record.level === 'error') {
    console.error(prefix, record.message, details);
  } else if (record.level === 'warning') {
    console.warn(prefix, record.message, details);
  } else if (record.level === 'debug') {
    console.debug(prefix, record.message, details);
  } else {
    console.info(prefix, record.message, details);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function requestIdFrom(value: unknown): string | undefined {
  if (!isRecord(value)) {
    return undefined;
  }
  const id = value.id;
  return typeof id === 'string' && id.length > 0 ? id : undefined;
}

function parseResponse(value: unknown): ParsedResponse {
  const requestId = requestIdFrom(value);
  if (!isRecord(value)) {
    return { valid: false, reason: 'Response must be a JSON object.' };
  }
  if (value.protocol !== DESKTOP_PROTOCOL_VERSION) {
    return {
      valid: false,
      requestId,
      reason: `Expected protocol ${DESKTOP_PROTOCOL_VERSION}, received ${String(value.protocol)}.`,
    };
  }
  if (!requestId) {
    return { valid: false, reason: 'Response id must be a non-empty string.' };
  }
  if (typeof value.ok !== 'boolean') {
    return { valid: false, requestId, reason: 'Response ok field must be boolean.' };
  }
  if (value.ok) {
    return {
      valid: true,
      response: value as unknown as RpcResponse,
    };
  }
  if (!isRecord(value.error)) {
    return { valid: false, requestId, reason: 'Error response must contain an error object.' };
  }
  if (typeof value.error.code !== 'string' || typeof value.error.message !== 'string') {
    return { valid: false, requestId, reason: 'Error response requires string code and message fields.' };
  }
  return {
    valid: true,
    response: value as unknown as RpcResponse,
  };
}

function parseBackendLog(line: string): BackendLogRecord | undefined {
  let value: unknown;
  try {
    value = JSON.parse(line);
  } catch {
    return undefined;
  }
  if (!isRecord(value)) {
    return undefined;
  }
  const level = value.level;
  if (level !== 'debug' && level !== 'info' && level !== 'warning' && level !== 'error') {
    return undefined;
  }
  if (typeof value.event !== 'string' || typeof value.message !== 'string') {
    return undefined;
  }
  return {
    level,
    event: value.event,
    message: value.message,
    ...(value.data === undefined ? {} : { data: value.data }),
  };
}

function isPingResult(value: unknown): value is PingResult {
  return isRecord(value)
    && value.protocolVersion === DESKTOP_PROTOCOL_VERSION
    && typeof value.backendVersion === 'string'
    && typeof value.pythonVersion === 'string'
    && typeof value.pid === 'number';
}

export class BackendClient {
  private child: ChildProcessWithoutNullStreams | undefined;
  private pending = new Map<string, PendingRequest>();
  private ready: PingResult | undefined;
  private startPromise: Promise<PingResult> | undefined;
  private stopPromise: Promise<void> | undefined;
  private stoppingChild: ChildProcessWithoutNullStreams | undefined;
  private readonly expectedExits = new WeakSet<ChildProcessWithoutNullStreams>();
  private readonly launch: () => BackendLaunch;
  private readonly logger: BackendLogger;
  private readonly requestId: () => string;
  private readonly requestTimeoutMs: number;
  private readonly shutdownTimeoutMs: number;
  private readonly spawnProcess: BackendSpawner;
  private readonly startupTimeoutMs: number;
  private readonly healthListeners = new Set<(snapshot: HealthSnapshot) => void>();
  private readonly settingsListeners = new Set<(snapshot: SettingsSnapshot) => void>();
  private readonly analyzerListeners = new Set<(snapshot: AnalyzerScanSnapshot) => void>();
  private readonly enhancementListeners = new Set<(snapshot: EnhancementSnapshot) => void>();
  private readonly optimizerSearchListeners = new Set<(snapshot: OptimizerSearchSnapshot) => void>();
  private readonly optimizerResultListeners = new Set<(snapshot: OptimizerResultSnapshot) => void>();
  private readonly optimizerResultDetailListeners = new Set<(snapshot: OptimizerResultDetailSnapshot) => void>();
  private readonly optimizerResultExportListeners = new Set<(snapshot: OptimizerResultExportSnapshot) => void>();

  constructor(options: BackendClientOptions) {
    this.launch = typeof options.launch === 'function' ? options.launch : () => options.launch as BackendLaunch;
    this.logger = options.logger ?? defaultLogger;
    this.requestId = options.requestId ?? randomUUID;
    this.requestTimeoutMs = options.requestTimeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS;
    this.shutdownTimeoutMs = options.shutdownTimeoutMs ?? DEFAULT_SHUTDOWN_TIMEOUT_MS;
    this.spawnProcess = options.spawnProcess ?? defaultSpawner;
    this.startupTimeoutMs = options.startupTimeoutMs ?? DEFAULT_STARTUP_TIMEOUT_MS;
  }

  start(): Promise<PingResult> {
    if (this.startPromise) {
      return this.startPromise;
    }

    const begin = this.stopPromise
      ? this.stopPromise.then(() => this.startInternal())
      : this.startInternal();
    const tracked = begin.catch((error: unknown) => {
      if (this.startPromise === tracked) {
        this.startPromise = undefined;
      }
      throw error;
    });
    this.startPromise = tracked;
    return tracked;
  }

  async ping(): Promise<PingResult> {
    await this.start();
    const result = await this.sendRequest<unknown>(
      'system.ping',
      {},
      this.requestTimeoutMs,
      'request_timeout',
    );
    if (!isPingResult(result)) {
      throw new BackendClientError('protocol_error', 'Python backend returned an invalid ping result.');
    }
    return result;
  }

  async getHealth(): Promise<HealthSnapshot> {
    await this.start();
    return this.requireHealthSnapshot(await this.sendRequest<unknown>(
      'health.get',
      {},
      this.requestTimeoutMs,
      'request_timeout',
    ));
  }

  async refreshHealth(): Promise<HealthSnapshot> {
    await this.start();
    return this.requireHealthSnapshot(await this.sendRequest<unknown>(
      'health.refresh',
      {},
      this.requestTimeoutMs,
      'request_timeout',
    ));
  }

  async runHealthAction(actionId: HealthActionId): Promise<HealthSnapshot> {
    await this.start();
    return this.requireHealthSnapshot(await this.sendRequest<unknown>(
      'health.action',
      { actionId },
      this.requestTimeoutMs,
      'request_timeout',
    ));
  }

  onHealthUpdated(listener: (snapshot: HealthSnapshot) => void): () => void {
    this.healthListeners.add(listener);
    return () => this.healthListeners.delete(listener);
  }

  async getSettings(): Promise<SettingsSnapshot> {
    await this.start();
    return this.requireSettingsSnapshot(await this.sendRequest<unknown>(
      'settings.get',
      {},
      this.requestTimeoutMs,
      'request_timeout',
    ));
  }

  async updateSettings(revision: string, patch: SettingsPatch): Promise<SettingsSnapshot> {
    await this.start();
    return this.requireSettingsSnapshot(await this.sendRequest<unknown>(
      'settings.update',
      { revision, patch },
      this.requestTimeoutMs,
      'request_timeout',
    ));
  }

  async previewSettings(
    settings: DesktopSettings,
    request: SettingsPreviewRequest,
  ): Promise<SettingsPreview> {
    await this.start();
    return this.requireSettingsPreview(await this.sendRequest<unknown>(
      'settings.preview',
      { settings, request },
      Math.max(this.requestTimeoutMs, 30_000),
      'request_timeout',
    ));
  }

  onSettingsUpdated(listener: (snapshot: SettingsSnapshot) => void): () => void {
    this.settingsListeners.add(listener);
    return () => this.settingsListeners.delete(listener);
  }

  async getAnalyzerOptions(): Promise<AnalyzerOptions> {
    await this.start();
    return this.requireAnalyzerOptions(await this.sendRequest<unknown>(
      'analyzer.options', {}, this.requestTimeoutMs, 'request_timeout',
    ));
  }

  async evaluateAnalyzerPiece(piece: AnalyzerPiece): Promise<AnalyzerEvaluation> {
    await this.start();
    return this.requireAnalyzerEvaluation(await this.sendRequest<unknown>(
      'analyzer.evaluate', { piece }, this.requestTimeoutMs, 'request_timeout',
    ));
  }

  async getAnalyzerScan(): Promise<AnalyzerScanSnapshot> {
    await this.start();
    return this.requireAnalyzerScan(await this.sendRequest<unknown>(
      'analyzer.scan.get', {}, this.requestTimeoutMs, 'request_timeout',
    ));
  }

  async startAnalyzerScan(): Promise<AnalyzerScanSnapshot> {
    await this.start();
    return this.requireAnalyzerScan(await this.sendRequest<unknown>(
      'analyzer.scan.start', {}, this.requestTimeoutMs, 'request_timeout',
    ));
  }

  async cancelAnalyzerScan(jobId: string): Promise<AnalyzerScanSnapshot> {
    await this.start();
    return this.requireAnalyzerScan(await this.sendRequest<unknown>(
      'analyzer.scan.cancel', { jobId }, this.requestTimeoutMs, 'request_timeout',
    ));
  }

  async getAnalyzerDebug(): Promise<AnalyzerDebug> {
    await this.start();
    return this.requireAnalyzerDebug(await this.sendRequest<unknown>(
      'analyzer.debug.get', {}, this.requestTimeoutMs, 'request_timeout',
    ));
  }

  onAnalyzerUpdated(listener: (snapshot: AnalyzerScanSnapshot) => void): () => void {
    this.analyzerListeners.add(listener);
    return () => this.analyzerListeners.delete(listener);
  }

  async getEnhancementOptions(): Promise<EnhancementOptions> {
    await this.start();
    return this.requireEnhancementOptions(await this.sendRequest<unknown>(
      'enhancement.options', {}, this.requestTimeoutMs, 'request_timeout',
    ));
  }

  async getEnhancementJob(): Promise<EnhancementSnapshot> {
    await this.start();
    return this.requireEnhancementSnapshot(await this.sendRequest<unknown>(
      'enhancement.job.get', {}, this.requestTimeoutMs, 'request_timeout',
    ));
  }

  async startEnhancementJob(options: EnhancementStartOptions): Promise<EnhancementSnapshot> {
    await this.start();
    return this.requireEnhancementSnapshot(await this.sendRequest<unknown>(
      'enhancement.job.start', { options }, this.requestTimeoutMs, 'request_timeout',
    ));
  }

  async cancelEnhancementJob(jobId: string): Promise<EnhancementSnapshot> {
    await this.start();
    return this.requireEnhancementSnapshot(await this.sendRequest<unknown>(
      'enhancement.job.cancel', { jobId }, this.requestTimeoutMs, 'request_timeout',
    ));
  }

  async getEnhancementDebug(): Promise<EnhancementDebug> {
    await this.start();
    return this.requireEnhancementDebug(await this.sendRequest<unknown>(
      'enhancement.debug.get', {}, this.requestTimeoutMs, 'request_timeout',
    ));
  }

  async getOptimizerInventory(): Promise<OptimizerInventorySnapshot> {
    await this.start();
    return this.requireOptimizerInventory(await this.sendRequest<unknown>(
      'optimizer.inventory.get', {}, this.requestTimeoutMs, 'request_timeout',
    ));
  }

  async importOptimizerInventory(sourcePath: string): Promise<OptimizerInventoryImportResult> {
    if (!sourcePath.trim()) {
      throw new BackendClientError('protocol_error', 'A selected inventory source path is required.');
    }
    await this.start();
    return this.requireOptimizerInventoryImport(await this.sendRequest<unknown>(
      'optimizer.inventory.import',
      { sourcePath },
      Math.max(this.requestTimeoutMs, 60_000),
      'request_timeout',
    ));
  }

  async startOptimizerInventoryCapture(): Promise<OptimizerInventoryCaptureState> {
    await this.start();
    return this.requireOptimizerInventoryCapture(await this.sendRequest<unknown>(
      'optimizer.inventory.capture.start',
      {},
      this.requestTimeoutMs,
      'request_timeout',
    ));
  }

  async finishOptimizerInventoryCapture(): Promise<OptimizerInventoryImportResult> {
    await this.start();
    return this.requireOptimizerInventoryImport(await this.sendRequest<unknown>(
      'optimizer.inventory.capture.finish',
      {},
      Math.max(this.requestTimeoutMs, 60_000),
      'request_timeout',
    ));
  }

  async resetOptimizerData(): Promise<OptimizerDataResetResult> {
    await this.start();
    return this.requireOptimizerDataReset(await this.sendRequest<unknown>(
      'optimizer.inventory.reset',
      {},
      Math.max(this.requestTimeoutMs, 45_000),
      'request_timeout',
    ));
  }

  async searchOptimizerHeroes(query: string, limit = 20): Promise<OptimizerHeroSearchResult> {
    await this.start();
    return this.requireOptimizerHeroSearch(await this.sendRequest<unknown>(
      'optimizer.hero.search', { query, limit }, this.requestTimeoutMs, 'request_timeout',
    ));
  }

  async getOptimizerHeroDetails(heroId: string): Promise<OptimizerHeroDetails> {
    if (!heroId.trim()) throw new BackendClientError('protocol_error', 'Optimizer hero id is required.');
    await this.start();
    return this.requireOptimizerHeroDetails(await this.sendRequest<unknown>(
      'optimizer.hero.details', { heroId }, this.requestTimeoutMs, 'request_timeout',
    ));
  }

  async searchOptimizerArtifacts(query: string, limit = 20): Promise<OptimizerArtifactSearchResult> {
    await this.start();
    return this.requireOptimizerArtifactSearch(await this.sendRequest<unknown>(
      'optimizer.artifact.search', { query, limit }, this.requestTimeoutMs, 'request_timeout',
    ));
  }

  async loadOptimizerHeroDraft(heroId: string): Promise<OptimizerHeroDraftEnvelope> {
    if (!heroId.trim()) throw new BackendClientError('protocol_error', 'Optimizer hero id is required.');
    await this.start();
    return this.requireOptimizerHeroDraft(await this.sendRequest<unknown>(
      'optimizer.profile.load', { heroId }, this.requestTimeoutMs, 'request_timeout',
    ));
  }

  async saveOptimizerHeroDraft(draft: OptimizerHeroDraft): Promise<OptimizerHeroDraftEnvelope> {
    if (!isOptimizerHeroDraft(draft)) throw new BackendClientError('protocol_error', 'Optimizer hero draft is invalid.');
    await this.start();
    return this.requireOptimizerHeroDraft(await this.sendRequest<unknown>(
      'optimizer.profile.save', { draft }, this.requestTimeoutMs, 'request_timeout',
    ));
  }

  async getOptimizerSearch(): Promise<OptimizerSearchSnapshot> {
    await this.start();
    return this.requireOptimizerSearch(await this.sendRequest<unknown>(
      'optimizer.search.get', {}, this.requestTimeoutMs, 'request_timeout',
    ));
  }

  async startOptimizerSearch(draft: OptimizerHeroDraft): Promise<OptimizerSearchSnapshot> {
    if (!isOptimizerHeroDraft(draft)) throw new BackendClientError('protocol_error', 'Optimizer hero draft is invalid.');
    await this.start();
    return this.requireOptimizerSearch(await this.sendRequest<unknown>(
      'optimizer.search.start', { draft }, this.requestTimeoutMs, 'request_timeout',
    ));
  }

  async cancelOptimizerSearch(jobId: string): Promise<OptimizerSearchSnapshot> {
    if (!jobId.trim()) throw new BackendClientError('protocol_error', 'Optimizer search job id is required.');
    await this.start();
    return this.requireOptimizerSearch(await this.sendRequest<unknown>(
      'optimizer.search.cancel', { jobId }, this.requestTimeoutMs, 'request_timeout',
    ));
  }

  async retryOptimizerSearchWithCpu(jobId: string): Promise<OptimizerSearchSnapshot> {
    if (!jobId.trim()) throw new BackendClientError('protocol_error', 'Optimizer search job id is required.');
    await this.start();
    return this.requireOptimizerSearch(await this.sendRequest<unknown>(
      'optimizer.search.retry-cpu', { jobId }, this.requestTimeoutMs, 'request_timeout',
    ));
  }

  onOptimizerSearchUpdated(listener: (snapshot: OptimizerSearchSnapshot) => void): () => void {
    this.optimizerSearchListeners.add(listener);
    return () => this.optimizerSearchListeners.delete(listener);
  }

  async getOptimizerResultOptions(): Promise<OptimizerResultOptions> {
    await this.start();
    return this.requireOptimizerResultOptions(await this.sendRequest<unknown>(
      'optimizer.results.options', {}, this.requestTimeoutMs, 'request_timeout',
    ));
  }

  async getOptimizerResults(): Promise<OptimizerResultSnapshot> {
    await this.start();
    return this.requireOptimizerResults(await this.sendRequest<unknown>(
      'optimizer.results.get', {}, this.requestTimeoutMs, 'request_timeout',
    ));
  }

  async queryOptimizerResults(query: OptimizerResultQuery): Promise<OptimizerResultSnapshot> {
    if (!isOptimizerResultQuery(query)) throw new BackendClientError('protocol_error', 'Optimizer result query is invalid.');
    await this.start();
    return this.requireOptimizerResults(await this.sendRequest<unknown>(
      'optimizer.results.query', { query }, this.requestTimeoutMs, 'request_timeout',
    ));
  }

  async cancelOptimizerResults(queryId: string): Promise<OptimizerResultSnapshot> {
    if (!queryId.trim()) throw new BackendClientError('protocol_error', 'Optimizer result query id is required.');
    await this.start();
    return this.requireOptimizerResults(await this.sendRequest<unknown>(
      'optimizer.results.cancel', { queryId }, this.requestTimeoutMs, 'request_timeout',
    ));
  }

  onOptimizerResultsUpdated(listener: (snapshot: OptimizerResultSnapshot) => void): () => void {
    this.optimizerResultListeners.add(listener);
    return () => this.optimizerResultListeners.delete(listener);
  }

  async selectOptimizerResultDetail(request: OptimizerResultDetailRequest): Promise<OptimizerResultDetailSnapshot> {
    if (!isOptimizerResultDetailRequest(request)) {
      throw new BackendClientError('protocol_error', 'Optimizer result detail selection is invalid.');
    }
    await this.start();
    return this.requireOptimizerResultDetail(await this.sendRequest<unknown>(
      'optimizer.results.detail',
      { runId: request.runId, queryId: request.queryId, rowKey: request.rowKey },
      this.requestTimeoutMs,
      'request_timeout',
    ));
  }

  async equipOptimizerResultBuild(request: OptimizerResultDetailRequest): Promise<OptimizerResultEquipResult> {
    if (!isOptimizerResultDetailRequest(request)) {
      throw new BackendClientError('protocol_error', 'Optimizer build equip selection is invalid.');
    }
    await this.start();
    return this.requireOptimizerResultEquip(await this.sendRequest<unknown>(
      'optimizer.results.equip',
      { runId: request.runId, queryId: request.queryId, rowKey: request.rowKey },
      this.requestTimeoutMs,
      'request_timeout',
    ));
  }

  onOptimizerResultDetailUpdated(listener: (snapshot: OptimizerResultDetailSnapshot) => void): () => void {
    this.optimizerResultDetailListeners.add(listener);
    return () => this.optimizerResultDetailListeners.delete(listener);
  }

  async getOptimizerResultExport(): Promise<OptimizerResultExportSnapshot> {
    await this.start();
    return this.requireOptimizerResultExport(await this.sendRequest<unknown>(
      'optimizer.results.export.get', {}, this.requestTimeoutMs, 'request_timeout',
    ));
  }

  async startOptimizerResultExport(
    runId: string,
    queryId: string,
    format: OptimizerResultExportFormat,
    destination: string,
  ): Promise<OptimizerResultExportSnapshot> {
    if (!runId || !queryId || !destination || !['csv', 'json'].includes(format)) {
      throw new BackendClientError('protocol_error', 'Optimizer result export request is invalid.');
    }
    await this.start();
    return this.requireOptimizerResultExport(await this.sendRequest<unknown>(
      'optimizer.results.export.start', { runId, queryId, format, destination }, this.requestTimeoutMs, 'request_timeout',
    ));
  }

  async cancelOptimizerResultExport(exportId: string): Promise<OptimizerResultExportSnapshot> {
    if (!exportId) throw new BackendClientError('protocol_error', 'Optimizer result export id is required.');
    await this.start();
    return this.requireOptimizerResultExport(await this.sendRequest<unknown>(
      'optimizer.results.export.cancel', { exportId }, this.requestTimeoutMs, 'request_timeout',
    ));
  }

  onOptimizerResultExportUpdated(listener: (snapshot: OptimizerResultExportSnapshot) => void): () => void {
    this.optimizerResultExportListeners.add(listener);
    return () => this.optimizerResultExportListeners.delete(listener);
  }

  onEnhancementUpdated(listener: (snapshot: EnhancementSnapshot) => void): () => void {
    this.enhancementListeners.add(listener);
    return () => this.enhancementListeners.delete(listener);
  }

  stop(): Promise<void> {
    if (this.stopPromise) {
      return this.stopPromise;
    }

    const child = this.child;
    if (!child) {
      this.ready = undefined;
      this.startPromise = undefined;
      return Promise.resolve();
    }

    const operation = this.stopInternal(child);
    const tracked = operation.finally(() => {
      if (this.stopPromise === tracked) {
        this.stopPromise = undefined;
      }
    });
    this.stopPromise = tracked;
    return tracked;
  }

  private async startInternal(): Promise<PingResult> {
    const child = this.spawnProcess(this.launch());
    this.child = child;
    this.ready = undefined;
    this.attachProcess(child);

    try {
      const result = await this.sendRequest<unknown>(
        'system.ping',
        {},
        this.startupTimeoutMs,
        'startup_timeout',
      );
      if (!isPingResult(result)) {
        throw new BackendClientError('protocol_error', 'Python backend returned an invalid startup handshake.');
      }
      this.ready = result;
      return result;
    } catch (error: unknown) {
      this.forceTerminate(child);
      throw error;
    }
  }

  private attachProcess(child: ChildProcessWithoutNullStreams): void {
    readline.createInterface({ input: child.stdout }).on('line', (line) => {
      this.handleLine(line);
    });
    readline.createInterface({ input: child.stderr }).on('line', (line) => {
      const structured = parseBackendLog(line);
      this.logger(structured ?? {
        level: 'error',
        event: 'backend.stderr',
        message: line,
      });
    });

    child.once('error', (error) => {
      this.handleProcessFailure(
        child,
        new BackendClientError('backend_io_error', `Unable to run the Python backend: ${error.message}`),
      );
    });
    child.stdout.once('error', (error) => {
      this.handleProcessFailure(
        child,
        new BackendClientError('backend_io_error', `Python backend output failed: ${error.message}`),
      );
    });
    child.once('exit', (code, signal) => {
      this.handleExit(child, code, signal);
    });
  }

  private async stopInternal(child: ChildProcessWithoutNullStreams): Promise<void> {
    this.stoppingChild = child;
    this.ready = undefined;
    this.startPromise = undefined;
    this.expectedExits.add(child);
    this.failAll(new BackendClientError('backend_stopped', 'Python backend is shutting down.'));

    const exitPromise = this.waitForExit(child, this.shutdownTimeoutMs);
    try {
      const result = await this.sendRequest<ShutdownResult>(
        'system.shutdown',
        {},
        this.shutdownTimeoutMs,
        'request_timeout',
        true,
      );
      if (result.accepted !== true) {
        this.logger({
          level: 'warning',
          event: 'backend.shutdown_invalid_response',
          message: 'Python backend did not acknowledge shutdown correctly.',
        });
      }
    } catch (error: unknown) {
      this.logger({
        level: 'warning',
        event: 'backend.shutdown_request_failed',
        message: error instanceof Error ? error.message : String(error),
      });
    }

    if (child.stdin.writable) {
      child.stdin.end();
    }

    const exited = await exitPromise;
    if (!exited && child.exitCode === null && !child.killed) {
      this.logger({
        level: 'warning',
        event: 'backend.shutdown_forced',
        message: 'Python backend did not exit before the shutdown deadline.',
      });
      child.kill();
    }

    if (this.child === child) {
      this.child = undefined;
    }
    if (this.stoppingChild === child) {
      this.stoppingChild = undefined;
    }
  }

  private sendRequest<T>(
    method: string,
    params: Record<string, unknown>,
    timeoutMs: number,
    timeoutCode: 'request_timeout' | 'startup_timeout',
    allowDuringStop = false,
  ): Promise<T> {
    const child = this.child;
    if (!child || child.killed || !child.stdin.writable) {
      return Promise.reject(new BackendClientError('backend_not_running', 'Python backend is not running.'));
    }
    if (this.stoppingChild && !allowDuringStop) {
      return Promise.reject(new BackendClientError('backend_stopped', 'Python backend is shutting down.'));
    }

    const id = this.requestId();
    const message: RpcRequest = {
      protocol: DESKTOP_PROTOCOL_VERSION,
      id,
      method,
      params,
    };

    return new Promise<T>((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.pending.delete(id);
        reject(new BackendClientError(timeoutCode, `Python backend timed out during ${method}.`));
      }, timeoutMs);

      this.pending.set(id, {
        method,
        resolve: (value) => resolve(value as T),
        reject,
        timeout,
      });

      child.stdin.write(`${JSON.stringify(message)}\n`, (error) => {
        if (!error) {
          return;
        }
        const pending = this.pending.get(id);
        if (pending) {
          clearTimeout(pending.timeout);
          this.pending.delete(id);
          pending.reject(new BackendClientError('backend_io_error', error.message));
        }
      });
    });
  }

  private handleLine(line: string): void {
    let value: unknown;
    try {
      value = JSON.parse(line);
    } catch {
      this.logger({
        level: 'warning',
        event: 'protocol.invalid_json',
        message: 'Ignored malformed JSON from the Python backend.',
      });
      return;
    }

    if (isRecord(value)
      && value.protocol === DESKTOP_PROTOCOL_VERSION
      && value.event === 'health.updated') {
      if (!isHealthSnapshot(value.payload)) {
        this.logger({
          level: 'warning',
          event: 'protocol.invalid_event',
          message: 'Ignored an invalid health update event.',
        });
        return;
      }
      for (const listener of this.healthListeners) {
        listener(value.payload);
      }
      return;
    }

    if (isRecord(value)
      && value.protocol === DESKTOP_PROTOCOL_VERSION
      && value.event === 'enhancement.updated') {
      if (!isEnhancementSnapshot(value.payload)) {
        this.logger({
          level: 'warning',
          event: 'protocol.invalid_event',
          message: 'Ignored an invalid enhancement update event.',
        });
        return;
      }
      for (const listener of this.enhancementListeners) {
        listener(value.payload);
      }
      return;
    }

    if (isRecord(value)
      && value.protocol === DESKTOP_PROTOCOL_VERSION
      && value.event === 'settings.updated') {
      if (!isSettingsSnapshot(value.payload)) {
        this.logger({
          level: 'warning',
          event: 'protocol.invalid_event',
          message: 'Ignored an invalid settings update event.',
        });
        return;
      }
      for (const listener of this.settingsListeners) {
        listener(value.payload);
      }
      return;
    }

    if (isRecord(value)
      && value.protocol === DESKTOP_PROTOCOL_VERSION
      && value.event === 'analyzer.updated') {
      if (!isAnalyzerScanSnapshot(value.payload)) {
        this.logger({
          level: 'warning',
          event: 'protocol.invalid_event',
          message: 'Ignored an invalid analyzer update event.',
        });
        return;
      }
      for (const listener of this.analyzerListeners) {
        listener(value.payload);
      }
      return;
    }

    if (isRecord(value)
      && value.protocol === DESKTOP_PROTOCOL_VERSION
      && value.event === 'optimizer.search.updated') {
      if (!isOptimizerSearchSnapshot(value.payload)) {
        this.logger({
          level: 'warning',
          event: 'protocol.invalid_event',
          message: 'Ignored an invalid optimizer search update event.',
        });
        return;
      }
      for (const listener of this.optimizerSearchListeners) listener(value.payload);
      return;
    }

    if (isRecord(value)
      && value.protocol === DESKTOP_PROTOCOL_VERSION
      && value.event === 'optimizer.results.updated') {
      if (!isOptimizerResultSnapshot(value.payload)) {
        this.logger({ level: 'warning', event: 'protocol.invalid_event', message: 'Ignored an invalid optimizer result update event.' });
        return;
      }
      for (const listener of this.optimizerResultListeners) listener(value.payload);
      return;
    }

    if (isRecord(value)
      && value.protocol === DESKTOP_PROTOCOL_VERSION
      && value.event === 'optimizer.results.detail-updated') {
      if (!isOptimizerResultDetailSnapshot(value.payload)) {
        this.logger({ level: 'warning', event: 'protocol.invalid_event', message: 'Ignored an invalid optimizer result detail update event.' });
        return;
      }
      for (const listener of this.optimizerResultDetailListeners) listener(value.payload);
      return;
    }

    if (isRecord(value)
      && value.protocol === DESKTOP_PROTOCOL_VERSION
      && value.event === 'optimizer.results.export-updated') {
      if (!isOptimizerResultExportSnapshot(value.payload)) {
        this.logger({ level: 'warning', event: 'protocol.invalid_event', message: 'Ignored an invalid optimizer result export update event.' });
        return;
      }
      for (const listener of this.optimizerResultExportListeners) listener(value.payload);
      return;
    }

    const parsed = parseResponse(value);
    if (!parsed.valid) {
      this.logger({
        level: 'warning',
        event: 'protocol.invalid_response',
        message: parsed.reason,
      });
      if (parsed.requestId) {
        this.rejectPending(
          parsed.requestId,
          new BackendClientError('protocol_error', parsed.reason),
        );
      }
      return;
    }

    const response = parsed.response;
    if (!response.id) {
      return;
    }
    const pending = this.pending.get(response.id);
    if (!pending) {
      this.logger({
        level: 'debug',
        event: 'protocol.uncorrelated_response',
        message: `Ignored response for unknown request ${response.id}.`,
      });
      return;
    }

    clearTimeout(pending.timeout);
    this.pending.delete(response.id);
    if (response.ok) {
      pending.resolve(response.result);
    } else {
      const typedCodes: readonly BackendClientErrorCode[] = [
        'settings_conflict',
        'settings_validation',
        'settings_read_only',
        'settings_preview_failed',
        'settings_write_failed',
        'analyzer_validation',
        'analyzer_busy',
        'analyzer_job_not_found',
        'enhancement_validation',
        'enhancement_busy',
        'enhancement_job_not_found',
        'optimizer_inventory_status_failed',
        'optimizer_inventory_import_failed',
        'optimizer_data_reset_busy',
        'optimizer_data_reset_failed',
        'optimizer_profile_failed',
        'optimizer_search_busy',
        'optimizer_search_job_not_found',
        'optimizer_search_recovery_unavailable',
        'optimizer_result_session_unavailable',
        'optimizer_result_query_not_found',
      ];
      if (typedCodes.includes(response.error.code as BackendClientErrorCode)) {
        pending.reject(new BackendClientError(
          response.error.code as BackendClientErrorCode,
          response.error.message,
          response.error.data,
        ));
      } else {
        pending.reject(new Error(`${response.error.code}: ${response.error.message}`));
      }
    }
  }

  private rejectPending(id: string, error: Error): void {
    const pending = this.pending.get(id);
    if (!pending) {
      return;
    }
    clearTimeout(pending.timeout);
    this.pending.delete(id);
    pending.reject(error);
  }

  private handleProcessFailure(child: ChildProcessWithoutNullStreams, error: BackendClientError): void {
    if (this.child !== child) {
      return;
    }
    const stopping = this.stoppingChild === child;
    this.child = undefined;
    this.ready = undefined;
    if (!stopping) {
      this.startPromise = undefined;
    }
    this.failAll(error);
    this.expectedExits.add(child);
    if (!child.killed && child.exitCode === null) {
      child.kill();
    }
    this.logger({ level: 'error', event: 'backend.process_failure', message: error.message });
  }

  private handleExit(
    child: ChildProcessWithoutNullStreams,
    code: number | null,
    signal: NodeJS.Signals | null,
  ): void {
    const expected = this.expectedExits.has(child);
    this.expectedExits.delete(child);

    if (this.child === child) {
      this.child = undefined;
      this.ready = undefined;
      if (!expected) {
        this.startPromise = undefined;
      }
    }
    if (this.stoppingChild === child) {
      this.stoppingChild = undefined;
    }

    if (expected) {
      this.failAll(new BackendClientError('backend_stopped', 'Python backend exited during shutdown.'));
      this.logger({
        level: 'info',
        event: 'backend.stopped',
        message: 'Python backend exited.',
        data: { code, signal },
      });
      return;
    }

    const error = new BackendClientError(
      'backend_exited',
      `Python backend exited unexpectedly (code=${String(code)}, signal=${String(signal)}).`,
    );
    this.failAll(error);
    this.logger({
      level: 'error',
      event: 'backend.crashed',
      message: error.message,
      data: { code, signal },
    });
  }

  private forceTerminate(child: ChildProcessWithoutNullStreams): void {
    this.expectedExits.add(child);
    if (this.child === child) {
      this.child = undefined;
      this.ready = undefined;
    }
    if (!child.killed && child.exitCode === null) {
      child.kill();
    }
  }

  private waitForExit(child: ChildProcessWithoutNullStreams, timeoutMs: number): Promise<boolean> {
    if (child.exitCode !== null) {
      return Promise.resolve(true);
    }
    return new Promise((resolve) => {
      const timeout = setTimeout(() => resolve(false), timeoutMs);
      child.once('exit', () => {
        clearTimeout(timeout);
        resolve(true);
      });
    });
  }

  private failAll(error: Error): void {
    for (const request of this.pending.values()) {
      clearTimeout(request.timeout);
      request.reject(error);
    }
    this.pending.clear();
  }

  private requireHealthSnapshot(value: unknown): HealthSnapshot {
    if (!isHealthSnapshot(value)) {
      throw new BackendClientError('protocol_error', 'Python backend returned an invalid health snapshot.');
    }
    return value;
  }

  private requireSettingsSnapshot(value: unknown): SettingsSnapshot {
    if (!isSettingsSnapshot(value)) {
      throw new BackendClientError('protocol_error', 'Python backend returned an invalid settings snapshot.');
    }
    return value;
  }

  private requireSettingsPreview(value: unknown): SettingsPreview {
    if (!isSettingsPreview(value)) {
      throw new BackendClientError('protocol_error', 'Python backend returned an invalid settings preview.');
    }
    return value;
  }

  private requireAnalyzerOptions(value: unknown): AnalyzerOptions {
    if (!isAnalyzerOptions(value)) {
      throw new BackendClientError('protocol_error', 'Python backend returned invalid analyzer options.');
    }
    return value;
  }

  private requireAnalyzerEvaluation(value: unknown): AnalyzerEvaluation {
    if (!isAnalyzerEvaluation(value)) {
      throw new BackendClientError('protocol_error', 'Python backend returned an invalid analyzer evaluation.');
    }
    return value;
  }

  private requireAnalyzerScan(value: unknown): AnalyzerScanSnapshot {
    if (!isAnalyzerScanSnapshot(value)) {
      throw new BackendClientError('protocol_error', 'Python backend returned an invalid analyzer scan.');
    }
    return value;
  }

  private requireAnalyzerDebug(value: unknown): AnalyzerDebug {
    if (!isAnalyzerDebug(value)) {
      throw new BackendClientError('protocol_error', 'Python backend returned invalid analyzer debug details.');
    }
    return value;
  }

  private requireEnhancementOptions(value: unknown): EnhancementOptions {
    if (!isEnhancementOptions(value)) {
      throw new BackendClientError('protocol_error', 'Python backend returned invalid enhancement options.');
    }
    return value;
  }

  private requireEnhancementSnapshot(value: unknown): EnhancementSnapshot {
    if (!isEnhancementSnapshot(value)) {
      throw new BackendClientError('protocol_error', 'Python backend returned an invalid enhancement job.');
    }
    return value;
  }

  private requireEnhancementDebug(value: unknown): EnhancementDebug {
    if (!isEnhancementDebug(value)) {
      throw new BackendClientError('protocol_error', 'Python backend returned invalid enhancement debug details.');
    }
    return value;
  }

  private requireOptimizerInventory(value: unknown): OptimizerInventorySnapshot {
    if (!isOptimizerInventorySnapshot(value)) {
      throw new BackendClientError('protocol_error', 'Python backend returned an invalid optimizer inventory snapshot.');
    }
    return value;
  }

  private requireOptimizerInventoryImport(value: unknown): OptimizerInventoryImportResult {
    if (!isOptimizerInventoryImportResult(value)) {
      throw new BackendClientError('protocol_error', 'Python backend returned an invalid optimizer inventory import result.');
    }
    return value;
  }

  private requireOptimizerInventoryCapture(value: unknown): OptimizerInventoryCaptureState {
    if (!isOptimizerInventoryCaptureState(value)) {
      throw new BackendClientError('protocol_error', 'Python backend returned an invalid optimizer capture state.');
    }
    return value;
  }

  private requireOptimizerDataReset(value: unknown): OptimizerDataResetResult {
    if (!isOptimizerDataResetResult(value)) {
      throw new BackendClientError('protocol_error', 'Python backend returned an invalid optimizer data reset result.');
    }
    return value;
  }

  private requireOptimizerHeroSearch(value: unknown): OptimizerHeroSearchResult {
    if (!isOptimizerHeroSearchResult(value)) throw new BackendClientError('protocol_error', 'Python backend returned invalid hero search results.');
    return value;
  }

  private requireOptimizerHeroDetails(value: unknown): OptimizerHeroDetails {
    if (!isOptimizerHeroDetails(value)) throw new BackendClientError('protocol_error', 'Python backend returned invalid hero details.');
    return value;
  }

  private requireOptimizerArtifactSearch(value: unknown): OptimizerArtifactSearchResult {
    if (!isOptimizerArtifactSearchResult(value)) throw new BackendClientError('protocol_error', 'Python backend returned invalid artifact search results.');
    return value;
  }

  private requireOptimizerHeroDraft(value: unknown): OptimizerHeroDraftEnvelope {
    if (!isOptimizerHeroDraftEnvelope(value)) throw new BackendClientError('protocol_error', 'Python backend returned an invalid hero draft.');
    return value;
  }


  private requireOptimizerSearch(value: unknown): OptimizerSearchSnapshot {
    if (!isOptimizerSearchSnapshot(value)) {
      throw new BackendClientError('protocol_error', 'Python backend returned an invalid optimizer search snapshot.');
    }
    return value;
  }


  private requireOptimizerResultOptions(value: unknown): OptimizerResultOptions {
    if (!isOptimizerResultOptions(value)) {
      throw new BackendClientError('protocol_error', 'Python backend returned invalid optimizer result options.');
    }
    return value;
  }

  private requireOptimizerResults(value: unknown): OptimizerResultSnapshot {
    if (!isOptimizerResultSnapshot(value)) {
      throw new BackendClientError('protocol_error', 'Python backend returned an invalid optimizer result snapshot.');
    }
    return value;
  }

  private requireOptimizerResultDetail(value: unknown): OptimizerResultDetailSnapshot {
    if (!isOptimizerResultDetailSnapshot(value)) {
      throw new BackendClientError('protocol_error', 'Python backend returned an invalid optimizer result detail snapshot.');
    }
    return value;
  }

  private requireOptimizerResultEquip(value: unknown): OptimizerResultEquipResult {
    if (!isOptimizerResultEquipResult(value)) {
      throw new BackendClientError('protocol_error', 'Python backend returned an invalid optimizer build equip result.');
    }
    return value;
  }

  private requireOptimizerResultExport(value: unknown): OptimizerResultExportSnapshot {
    if (!isOptimizerResultExportSnapshot(value)) {
      throw new BackendClientError('protocol_error', 'Backend returned an invalid optimizer result export snapshot.');
    }
    return value;
  }
}
