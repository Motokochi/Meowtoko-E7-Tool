import type { E7DesktopApi } from './shared/protocol';
import {
  isAnalyzerDebug,
  isAnalyzerEvaluation,
  isAnalyzerOptions,
  isAnalyzerPiece,
  isAnalyzerScanSnapshot,
  type AnalyzerDebug,
  type AnalyzerEvaluation,
  type AnalyzerOptions,
  type AnalyzerScanSnapshot,
} from './shared/analyzer';
import {
  isEnhancementDebug,
  isEnhancementOptions,
  isEnhancementSnapshot,
  isEnhancementStartOptions,
  type EnhancementDebug,
  type EnhancementOptions,
  type EnhancementSnapshot,
} from './shared/enhancement';
import {
  isHealthActionId,
  isHealthSnapshot,
  type HealthActionId,
  type HealthSnapshot,
} from './shared/health';
import {
  isDesktopSettings,
  isSettingsPatch,
  isSettingsPreview,
  isSettingsPreviewRequest,
  isSettingsSnapshot,
  type SettingsPatch,
  type SettingsSnapshot,
} from './shared/settings';
import {
  isOptimizerDataResetResult,
  isOptimizerInventoryCaptureState,
  isOptimizerInventoryImportResult,
  isOptimizerInventorySelectionResult,
  isOptimizerInventorySnapshot,
  type OptimizerInventoryCaptureState,
  type OptimizerInventoryImportResult,
  type OptimizerInventorySelectionResult,
  type OptimizerInventorySnapshot,
} from './shared/optimizer-inventory';
import {
  isOptimizerArtifactSearchResult,
  isOptimizerHeroDetails,
  isOptimizerHeroDraft,
  isOptimizerHeroDraftEnvelope,
  isOptimizerHeroSearchResult,
} from './shared/optimizer-profile';
import { isOptimizerSearchSnapshot } from './shared/optimizer-search';
import {
  isOptimizerResultOptions,
  isOptimizerResultQuery,
  isOptimizerResultSnapshot,
} from './shared/optimizer-results';
import {
  isOptimizerResultDetailRequest,
  isOptimizerResultDetailSnapshot,
  isOptimizerResultEquipResult,
} from './shared/optimizer-result-detail';
import {
  isOptimizerResultExportRequest,
  isOptimizerResultExportSelection,
  isOptimizerResultExportSnapshot,
} from './shared/optimizer-result-export';
import {
  isUpdateApplyRequest,
  isUpdateApplyResult,
  isUpdateSnapshot,
  type UpdateApplyResult,
  type UpdateSnapshot,
} from './shared/update';

export type DesktopInvoke = (channel: string, ...args: unknown[]) => Promise<unknown>;
export type DesktopSubscribe = (
  channel: string,
  listener: (payload: unknown) => void,
) => () => void;

function requireHealthSnapshot(value: unknown): HealthSnapshot {
  if (!isHealthSnapshot(value)) {
    throw new Error('Desktop bridge returned an invalid health snapshot.');
  }
  return value;
}

function requireSettingsSnapshot(value: unknown): SettingsSnapshot {
  if (!isSettingsSnapshot(value)) {
    throw new Error('Desktop bridge returned an invalid settings snapshot.');
  }
  return value;
}

function requireSettingsPreview(value: unknown) {
  if (!isSettingsPreview(value)) {
    throw new Error('Desktop bridge returned an invalid settings preview.');
  }
  return value;
}

function requireAnalyzerOptions(value: unknown): AnalyzerOptions {
  if (!isAnalyzerOptions(value)) throw new Error('Desktop bridge returned invalid analyzer options.');
  return value;
}

function requireAnalyzerEvaluation(value: unknown): AnalyzerEvaluation {
  if (!isAnalyzerEvaluation(value)) throw new Error('Desktop bridge returned an invalid analyzer evaluation.');
  return value;
}

function requireAnalyzerScan(value: unknown): AnalyzerScanSnapshot {
  if (!isAnalyzerScanSnapshot(value)) throw new Error('Desktop bridge returned an invalid analyzer scan.');
  return value;
}

function requireAnalyzerDebug(value: unknown): AnalyzerDebug {
  if (!isAnalyzerDebug(value)) throw new Error('Desktop bridge returned invalid analyzer debug details.');
  return value;
}

function requireEnhancementOptions(value: unknown): EnhancementOptions {
  if (!isEnhancementOptions(value)) throw new Error('Desktop bridge returned invalid enhancement options.');
  return value;
}

function requireEnhancementSnapshot(value: unknown): EnhancementSnapshot {
  if (!isEnhancementSnapshot(value)) throw new Error('Desktop bridge returned an invalid enhancement job.');
  return value;
}

function requireEnhancementDebug(value: unknown): EnhancementDebug {
  if (!isEnhancementDebug(value)) throw new Error('Desktop bridge returned invalid enhancement debug details.');
  return value;
}

function requireOptimizerInventory(value: unknown): OptimizerInventorySnapshot {
  if (!isOptimizerInventorySnapshot(value)) {
    throw new Error('Desktop bridge returned an invalid optimizer inventory snapshot.');
  }
  return value;
}

function requireOptimizerInventorySelection(value: unknown): OptimizerInventorySelectionResult {
  if (!isOptimizerInventorySelectionResult(value)) {
    throw new Error('Desktop bridge returned an invalid optimizer inventory import result.');
  }
  return value;
}

function requireOptimizerDataReset(value: unknown) {
  if (!isOptimizerDataResetResult(value)) {
    throw new Error('Desktop bridge returned an invalid optimizer data reset result.');
  }
  return value;
}

function requireOptimizerSearch(value: unknown) {
  if (!isOptimizerSearchSnapshot(value)) {
    throw new Error('Desktop bridge returned an invalid optimizer search snapshot.');
  }
  return value;
}

function requireOptimizerResultOptions(value: unknown) {
  if (!isOptimizerResultOptions(value)) throw new Error('Desktop bridge returned invalid optimizer result options.');
  return value;
}

function requireOptimizerResults(value: unknown) {
  if (!isOptimizerResultSnapshot(value)) throw new Error('Desktop bridge returned an invalid optimizer result snapshot.');
  return value;
}

function requireOptimizerResultDetail(value: unknown) {
  if (!isOptimizerResultDetailSnapshot(value)) throw new Error('Desktop bridge returned an invalid optimizer result detail snapshot.');
  return value;
}

function requireOptimizerResultEquip(value: unknown) {
  if (!isOptimizerResultEquipResult(value)) throw new Error('Desktop bridge returned an invalid optimizer build equip result.');
  return value;
}

function requireOptimizerResultExport(value: unknown) {
  if (!isOptimizerResultExportSnapshot(value)) throw new Error('Desktop bridge returned an invalid optimizer result export snapshot.');
  return value;
}

function requireUpdateSnapshot(value: unknown): UpdateSnapshot {
  if (!isUpdateSnapshot(value)) throw new Error('Desktop bridge returned an invalid update snapshot.');
  return value;
}

function requireUpdateApplyResult(value: unknown): UpdateApplyResult {
  if (!isUpdateApplyResult(value)) throw new Error('Desktop bridge returned an invalid update apply result.');
  return value;
}

export function createDesktopApi(
  invoke: DesktopInvoke,
  subscribe: DesktopSubscribe,
): E7DesktopApi {
  const api: E7DesktopApi = {
    pingBackend: () => invoke('backend:ping') as ReturnType<E7DesktopApi['pingBackend']>,
    getHealth: async () => requireHealthSnapshot(await invoke('health:get')),
    refreshHealth: async () => requireHealthSnapshot(await invoke('health:refresh')),
    runHealthAction: async (actionId: HealthActionId) => {
      if (!isHealthActionId(actionId)) {
        throw new Error('Unsupported health action.');
      }
      return requireHealthSnapshot(await invoke('health:action', actionId));
    },
    onHealthUpdated: (listener) => subscribe('health:updated', (payload) => {
      if (isHealthSnapshot(payload)) {
        listener(payload);
      }
    }),
    getSettings: async () => requireSettingsSnapshot(await invoke('settings:get')),
    updateSettings: async (revision: string, patch: SettingsPatch) => {
      if (!revision) {
        throw new Error('Settings revision is required.');
      }
      if (!isSettingsPatch(patch)) {
        throw new Error('Unsupported settings patch.');
      }
      return requireSettingsSnapshot(await invoke('settings:update', revision, patch));
    },
    selectAdbExecutable: async () => {
      const value = await invoke('settings:adb:select');
      if (value !== null && typeof value !== 'string') {
        throw new Error('Desktop bridge returned an invalid ADB executable selection.');
      }
      return value;
    },
    previewSettings: async (settings, request) => {
      if (!isDesktopSettings(settings) || !isSettingsPreviewRequest(request)) {
        throw new Error('Unsupported settings preview request.');
      }
      return requireSettingsPreview(await invoke('settings:preview', settings, request));
    },
    onSettingsUpdated: (listener) => subscribe('settings:updated', (payload) => {
      if (isSettingsSnapshot(payload)) {
        listener(payload);
      }
    }),
    getAnalyzerOptions: async () => requireAnalyzerOptions(await invoke('analyzer:options')),
    evaluateAnalyzerPiece: async (piece) => {
      if (!isAnalyzerPiece(piece)) throw new Error('Unsupported analyzer piece.');
      return requireAnalyzerEvaluation(await invoke('analyzer:evaluate', piece));
    },
    getAnalyzerScan: async () => requireAnalyzerScan(await invoke('analyzer:scan:get')),
    startAnalyzerScan: async () => requireAnalyzerScan(await invoke('analyzer:scan:start')),
    cancelAnalyzerScan: async (jobId) => {
      if (!jobId) throw new Error('Analyzer job id is required.');
      return requireAnalyzerScan(await invoke('analyzer:scan:cancel', jobId));
    },
    getAnalyzerDebug: async () => requireAnalyzerDebug(await invoke('analyzer:debug:get')),
    onAnalyzerUpdated: (listener) => subscribe('analyzer:updated', (payload) => {
      if (isAnalyzerScanSnapshot(payload)) listener(payload);
    }),
    getEnhancementOptions: async () => requireEnhancementOptions(await invoke('enhancement:options')),
    getEnhancementJob: async () => requireEnhancementSnapshot(await invoke('enhancement:job:get')),
    startEnhancementJob: async (options) => {
      if (!isEnhancementStartOptions(options)) throw new Error('Unsupported enhancement options.');
      return requireEnhancementSnapshot(await invoke('enhancement:job:start', options));
    },
    cancelEnhancementJob: async (jobId) => {
      if (!jobId) throw new Error('Enhancement job id is required.');
      return requireEnhancementSnapshot(await invoke('enhancement:job:cancel', jobId));
    },
    getEnhancementDebug: async () => requireEnhancementDebug(await invoke('enhancement:debug:get')),
    onEnhancementUpdated: (listener) => subscribe('enhancement:updated', (payload) => {
      if (isEnhancementSnapshot(payload)) listener(payload);
    }),
    getOptimizerInventory: async () => requireOptimizerInventory(await invoke('optimizer:inventory:get')),
    selectOptimizerInventoryFile: async () => requireOptimizerInventorySelection(
      await invoke('optimizer:inventory:import'),
    ),
    startOptimizerInventoryCapture: async (): Promise<OptimizerInventoryCaptureState> => {
      const value = await invoke('optimizer:inventory:capture:start');
      if (!isOptimizerInventoryCaptureState(value)) {
        throw new Error('Desktop bridge returned invalid optimizer inventory capture state.');
      }
      return value;
    },
    finishOptimizerInventoryCapture: async (): Promise<OptimizerInventoryImportResult> => {
      const value = await invoke('optimizer:inventory:capture:finish');
      if (!isOptimizerInventoryImportResult(value)) {
        throw new Error('Desktop bridge returned invalid optimizer inventory import result.');
      }
      return value;
    },
    resetOptimizerData: async () => requireOptimizerDataReset(
      await invoke('optimizer:inventory:reset'),
    ),
    searchOptimizerHeroes: async (query, limit = 20) => {
      if (typeof query !== 'string' || !Number.isInteger(limit) || limit < 1 || limit > 50) {
        throw new Error('Unsupported optimizer hero search.');
      }
      const value = await invoke('optimizer:hero:search', query, limit);
      if (!isOptimizerHeroSearchResult(value)) throw new Error('Desktop bridge returned invalid hero search results.');
      return value;
    },
    getOptimizerHeroDetails: async (heroId) => {
      if (!heroId.trim()) throw new Error('Optimizer hero id is required.');
      const value = await invoke('optimizer:hero:details', heroId);
      if (!isOptimizerHeroDetails(value)) throw new Error('Desktop bridge returned invalid hero details.');
      return value;
    },
    searchOptimizerArtifacts: async (query, limit = 20) => {
      if (typeof query !== 'string' || !Number.isInteger(limit) || limit < 1 || limit > 50) {
        throw new Error('Unsupported optimizer artifact search.');
      }
      const value = await invoke('optimizer:artifact:search', query, limit);
      if (!isOptimizerArtifactSearchResult(value)) throw new Error('Desktop bridge returned invalid artifact search results.');
      return value;
    },
    loadOptimizerHeroDraft: async (heroId) => {
      if (!heroId.trim()) throw new Error('Optimizer hero id is required.');
      const value = await invoke('optimizer:profile:load', heroId);
      if (!isOptimizerHeroDraftEnvelope(value)) throw new Error('Desktop bridge returned an invalid hero draft.');
      return value;
    },
    saveOptimizerHeroDraft: async (draft) => {
      if (!isOptimizerHeroDraft(draft)) throw new Error('Unsupported optimizer hero draft.');
      const value = await invoke('optimizer:profile:save', draft);
      if (!isOptimizerHeroDraftEnvelope(value)) throw new Error('Desktop bridge returned an invalid saved hero draft.');
      return value;
    },
    getOptimizerSearch: async () => requireOptimizerSearch(await invoke('optimizer:search:get')),
    startOptimizerSearch: async (draft) => {
      if (!isOptimizerHeroDraft(draft)) throw new Error('Unsupported optimizer hero draft.');
      return requireOptimizerSearch(await invoke('optimizer:search:start', draft));
    },
    cancelOptimizerSearch: async (jobId) => {
      if (!jobId.trim()) throw new Error('Optimizer search job id is required.');
      return requireOptimizerSearch(await invoke('optimizer:search:cancel', jobId));
    },
    retryOptimizerSearchWithCpu: async (jobId) => {
      if (!jobId.trim()) throw new Error('Optimizer search job id is required.');
      return requireOptimizerSearch(await invoke('optimizer:search:retry-cpu', jobId));
    },
    onOptimizerSearchUpdated: (listener) => subscribe('optimizer:search:updated', (payload) => {
      if (isOptimizerSearchSnapshot(payload)) listener(payload);
    }),
    getOptimizerResultOptions: async () => requireOptimizerResultOptions(await invoke('optimizer:results:options')),
    getOptimizerResults: async () => requireOptimizerResults(await invoke('optimizer:results:get')),
    queryOptimizerResults: async (query) => {
      if (!isOptimizerResultQuery(query)) throw new Error('Unsupported optimizer result query.');
      return requireOptimizerResults(await invoke('optimizer:results:query', query));
    },
    cancelOptimizerResults: async (queryId) => {
      if (!queryId.trim()) throw new Error('Optimizer result query id is required.');
      return requireOptimizerResults(await invoke('optimizer:results:cancel', queryId));
    },
    onOptimizerResultsUpdated: (listener) => subscribe('optimizer:results:updated', (payload) => {
      if (isOptimizerResultSnapshot(payload)) listener(payload);
    }),
    selectOptimizerResultDetail: async (request) => {
      if (!isOptimizerResultDetailRequest(request)) throw new Error('Unsupported optimizer result detail selection.');
      return requireOptimizerResultDetail(await invoke('optimizer:results:detail', request));
    },
    equipOptimizerResultBuild: async (request) => {
      if (!isOptimizerResultDetailRequest(request)) throw new Error('Unsupported optimizer build equip selection.');
      return requireOptimizerResultEquip(await invoke('optimizer:results:equip', request));
    },
    onOptimizerResultDetailUpdated: (listener) => subscribe('optimizer:results:detail-updated', (payload) => {
      if (isOptimizerResultDetailSnapshot(payload)) listener(payload);
    }),
    getOptimizerResultExport: async () => requireOptimizerResultExport(await invoke('optimizer:results:export:get')),
    selectOptimizerResultExport: async (request) => {
      if (!isOptimizerResultExportRequest(request)) throw new Error('Unsupported optimizer result export request.');
      const value = await invoke('optimizer:results:export:select', request);
      if (!isOptimizerResultExportSelection(value)) throw new Error('Desktop bridge returned an invalid optimizer result export selection.');
      return value;
    },
    cancelOptimizerResultExport: async (exportId) => {
      if (!exportId.trim()) throw new Error('Optimizer result export id is required.');
      return requireOptimizerResultExport(await invoke('optimizer:results:export:cancel', exportId));
    },
    onOptimizerResultExportUpdated: (listener) => subscribe('optimizer:results:export:updated', (payload) => {
      if (isOptimizerResultExportSnapshot(payload)) listener(payload);
    }),
    getUpdate: async () => requireUpdateSnapshot(await invoke('update:get')),
    checkForUpdates: async () => requireUpdateSnapshot(await invoke('update:check')),
    downloadUpdate: async () => requireUpdateSnapshot(await invoke('update:download')),
    installUpdateOnQuit: async () => requireUpdateSnapshot(await invoke('update:install-on-quit')),
    applyUpdate: async (request) => {
      if (!isUpdateApplyRequest(request)) throw new Error('Unsupported update apply request.');
      return requireUpdateApplyResult(await invoke('update:apply', request));
    },
    openUpdateRelease: async () => {
      const value = await invoke('update:open-release');
      if (value !== null) throw new Error('Desktop bridge returned an invalid release-open response.');
    },
    onUpdateChanged: (listener) => subscribe('update:changed', (payload) => {
      if (isUpdateSnapshot(payload)) listener(payload);
    }),
  };
  return Object.freeze(api);
}
