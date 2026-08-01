import type { HealthActionId, HealthSnapshot } from './health';
import type {
  DesktopSettings,
  SettingsPatch,
  SettingsPreview,
  SettingsPreviewRequest,
  SettingsSnapshot,
} from './settings';
import type {
  AnalyzerDebug,
  AnalyzerEvaluation,
  AnalyzerOptions,
  AnalyzerPiece,
  AnalyzerScanSnapshot,
} from './analyzer';
import type {
  EnhancementDebug,
  EnhancementOptions,
  EnhancementSnapshot,
  EnhancementStartOptions,
} from './enhancement';
import type {
  OptimizerInventoryImportResult,
  OptimizerInventoryCaptureState,
  OptimizerDataResetResult,
  OptimizerInventorySelectionResult,
  OptimizerInventorySnapshot,
} from './optimizer-inventory';
import type {
  OptimizerArtifactSearchResult,
  OptimizerHeroDetails,
  OptimizerHeroDraft,
  OptimizerHeroDraftEnvelope,
  OptimizerHeroSearchResult,
} from './optimizer-profile';
import type { OptimizerSearchSnapshot } from './optimizer-search';
import type { OptimizerResultOptions, OptimizerResultQuery, OptimizerResultSnapshot } from './optimizer-results';
import type {
  OptimizerResultDetailRequest,
  OptimizerResultDetailSnapshot,
  OptimizerResultEquipResult,
} from './optimizer-result-detail';
import type {
  OptimizerResultExportRequest,
  OptimizerResultExportSelection,
  OptimizerResultExportSnapshot,
} from './optimizer-result-export';
import type {
  UpdateApplyRequest,
  UpdateApplyResult,
  UpdateSnapshot,
} from './update';

export const DESKTOP_PROTOCOL_VERSION = 1 as const;

export interface RpcRequest {
  protocol: typeof DESKTOP_PROTOCOL_VERSION;
  id: string;
  method: string;
  params: Record<string, unknown>;
}

export interface RpcError {
  code: string;
  message: string;
  data?: unknown;
}

export interface RpcSuccess {
  protocol: typeof DESKTOP_PROTOCOL_VERSION;
  id: string;
  ok: true;
  result: unknown;
}

export interface RpcFailure {
  protocol: typeof DESKTOP_PROTOCOL_VERSION;
  id: string | null;
  ok: false;
  error: RpcError;
}

export type RpcResponse = RpcSuccess | RpcFailure;

export interface HealthRpcEvent {
  protocol: typeof DESKTOP_PROTOCOL_VERSION;
  event: 'health.updated';
  payload: HealthSnapshot;
}

export interface SettingsRpcEvent {
  protocol: typeof DESKTOP_PROTOCOL_VERSION;
  event: 'settings.updated';
  payload: SettingsSnapshot;
}

export interface AnalyzerRpcEvent {
  protocol: typeof DESKTOP_PROTOCOL_VERSION;
  event: 'analyzer.updated';
  payload: AnalyzerScanSnapshot;
}

export interface EnhancementRpcEvent {
  protocol: typeof DESKTOP_PROTOCOL_VERSION;
  event: 'enhancement.updated';
  payload: EnhancementSnapshot;
}

export interface OptimizerSearchRpcEvent {
  protocol: typeof DESKTOP_PROTOCOL_VERSION;
  event: 'optimizer.search.updated';
  payload: OptimizerSearchSnapshot;
}

export interface OptimizerResultRpcEvent {
  protocol: typeof DESKTOP_PROTOCOL_VERSION;
  event: 'optimizer.results.updated';
  payload: OptimizerResultSnapshot;
}

export interface OptimizerResultDetailRpcEvent {
  protocol: typeof DESKTOP_PROTOCOL_VERSION;
  event: 'optimizer.results.detail-updated';
  payload: OptimizerResultDetailSnapshot;
}

export interface OptimizerResultExportRpcEvent {
  protocol: typeof DESKTOP_PROTOCOL_VERSION;
  event: 'optimizer.results.export-updated';
  payload: OptimizerResultExportSnapshot;
}

export type RpcEvent = HealthRpcEvent | SettingsRpcEvent | AnalyzerRpcEvent | EnhancementRpcEvent | OptimizerSearchRpcEvent | OptimizerResultRpcEvent | OptimizerResultDetailRpcEvent | OptimizerResultExportRpcEvent;

export interface PingResult {
  protocolVersion: typeof DESKTOP_PROTOCOL_VERSION;
  backendVersion: string;
  pythonVersion: string;
  pid: number;
}

export interface ShutdownResult {
  accepted: true;
}

export type BackendConnectionState =
  | { state: 'ready'; details: PingResult }
  | { state: 'error'; message: string };

export interface E7DesktopApi {
  pingBackend(): Promise<BackendConnectionState>;
  getHealth(): Promise<HealthSnapshot>;
  refreshHealth(): Promise<HealthSnapshot>;
  runHealthAction(actionId: HealthActionId): Promise<HealthSnapshot>;
  onHealthUpdated(listener: (snapshot: HealthSnapshot) => void): () => void;
  getSettings(): Promise<SettingsSnapshot>;
  updateSettings(revision: string, patch: SettingsPatch): Promise<SettingsSnapshot>;
  selectAdbExecutable(): Promise<string | null>;
  previewSettings(settings: DesktopSettings, request: SettingsPreviewRequest): Promise<SettingsPreview>;
  onSettingsUpdated(listener: (snapshot: SettingsSnapshot) => void): () => void;
  getAnalyzerOptions(): Promise<AnalyzerOptions>;
  evaluateAnalyzerPiece(piece: AnalyzerPiece): Promise<AnalyzerEvaluation>;
  getAnalyzerScan(): Promise<AnalyzerScanSnapshot>;
  startAnalyzerScan(): Promise<AnalyzerScanSnapshot>;
  cancelAnalyzerScan(jobId: string): Promise<AnalyzerScanSnapshot>;
  getAnalyzerDebug(): Promise<AnalyzerDebug>;
  onAnalyzerUpdated(listener: (snapshot: AnalyzerScanSnapshot) => void): () => void;
  getEnhancementOptions(): Promise<EnhancementOptions>;
  getEnhancementJob(): Promise<EnhancementSnapshot>;
  startEnhancementJob(options: EnhancementStartOptions): Promise<EnhancementSnapshot>;
  cancelEnhancementJob(jobId: string): Promise<EnhancementSnapshot>;
  getEnhancementDebug(): Promise<EnhancementDebug>;
  onEnhancementUpdated(listener: (snapshot: EnhancementSnapshot) => void): () => void;
  getOptimizerInventory(): Promise<OptimizerInventorySnapshot>;
  selectOptimizerInventoryFile(): Promise<OptimizerInventorySelectionResult>;
  startOptimizerInventoryCapture(): Promise<OptimizerInventoryCaptureState>;
  finishOptimizerInventoryCapture(): Promise<OptimizerInventoryImportResult>;
  resetOptimizerData(): Promise<OptimizerDataResetResult>;
  searchOptimizerHeroes(query: string, limit?: number): Promise<OptimizerHeroSearchResult>;
  getOptimizerHeroDetails(heroId: string): Promise<OptimizerHeroDetails>;
  searchOptimizerArtifacts(query: string, limit?: number): Promise<OptimizerArtifactSearchResult>;
  loadOptimizerHeroDraft(heroId: string): Promise<OptimizerHeroDraftEnvelope>;
  saveOptimizerHeroDraft(draft: OptimizerHeroDraft): Promise<OptimizerHeroDraftEnvelope>;
  getOptimizerSearch(): Promise<OptimizerSearchSnapshot>;
  startOptimizerSearch(draft: OptimizerHeroDraft): Promise<OptimizerSearchSnapshot>;
  cancelOptimizerSearch(jobId: string): Promise<OptimizerSearchSnapshot>;
  retryOptimizerSearchWithCpu(jobId: string): Promise<OptimizerSearchSnapshot>;
  onOptimizerSearchUpdated(listener: (snapshot: OptimizerSearchSnapshot) => void): () => void;
  getOptimizerResultOptions(): Promise<OptimizerResultOptions>;
  getOptimizerResults(): Promise<OptimizerResultSnapshot>;
  queryOptimizerResults(query: OptimizerResultQuery): Promise<OptimizerResultSnapshot>;
  cancelOptimizerResults(queryId: string): Promise<OptimizerResultSnapshot>;
  onOptimizerResultsUpdated(listener: (snapshot: OptimizerResultSnapshot) => void): () => void;
  selectOptimizerResultDetail(request: OptimizerResultDetailRequest): Promise<OptimizerResultDetailSnapshot>;
  equipOptimizerResultBuild(request: OptimizerResultDetailRequest): Promise<OptimizerResultEquipResult>;
  onOptimizerResultDetailUpdated(listener: (snapshot: OptimizerResultDetailSnapshot) => void): () => void;
  getOptimizerResultExport(): Promise<OptimizerResultExportSnapshot>;
  selectOptimizerResultExport(request: OptimizerResultExportRequest): Promise<OptimizerResultExportSelection>;
  cancelOptimizerResultExport(exportId: string): Promise<OptimizerResultExportSnapshot>;
  onOptimizerResultExportUpdated(listener: (snapshot: OptimizerResultExportSnapshot) => void): () => void;
  getUpdate(): Promise<UpdateSnapshot>;
  checkForUpdates(): Promise<UpdateSnapshot>;
  downloadUpdate(): Promise<UpdateSnapshot>;
  installUpdateOnQuit(): Promise<UpdateSnapshot>;
  applyUpdate(request: UpdateApplyRequest): Promise<UpdateApplyResult>;
  openUpdateRelease(): Promise<void>;
  onUpdateChanged(listener: (snapshot: UpdateSnapshot) => void): () => void;
}
