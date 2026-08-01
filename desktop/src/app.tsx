import { useCallback, useEffect, useReducer, useRef, useState } from 'react';

import { AnalyzerCenter } from './analyzer-center';
import { AppShell } from './app-shell';
import { EnhancerCenter } from './enhancer-center';
import { GearCenter } from './gear-center';
import { HealthCenter } from './health-center';
import { ImporterCenter, type OptimizerInventoryNotice } from './importer-center';
import { pageHash, pageIdFromHash, type PageId } from './navigation';
import { Overview } from './overview';
import { OptimizerCenter } from './optimizer-center';
import {
  initialOptimizerProfileWorkspaceState,
  optimizerProfileWorkspaceReducer,
} from './optimizer-profile-workspace';
import {
  initialOptimizerSearchWorkspaceState,
  optimizerSearchWorkspaceReducer,
} from './optimizer-search-workspace';
import {
  initialOptimizerResultWorkspaceState,
  optimizerResultWorkspaceReducer,
} from './optimizer-result-workspace';
import {
  initialOptimizerResultDetailWorkspaceState,
  optimizerResultDetailWorkspaceReducer,
} from './optimizer-result-detail-workspace';
import type { OptimizerResultQuery } from './shared/optimizer-results';
import type { OptimizerResultDetailRequest } from './shared/optimizer-result-detail';
import type {
  OptimizerResultExportFormat,
  OptimizerResultExportSnapshot,
} from './shared/optimizer-result-export';
import { SettingsCenter } from './settings-center';
import { UpdateBanner } from './update-center';
import type {
  AnalyzerDebug,
  AnalyzerEvaluation,
  AnalyzerOptions,
  AnalyzerPiece,
  AnalyzerScanSnapshot,
} from './shared/analyzer';
import {
  shouldAcceptEnhancementSnapshot,
  type EnhancementDebug,
  type EnhancementOptions,
  type EnhancementSnapshot,
  type EnhancementStartOptions,
} from './shared/enhancement';
import {
  shouldAcceptHealthSnapshot,
  type HealthActionId,
  type HealthSnapshot,
  type OverallHealthState,
} from './shared/health';
import type { BackendConnectionState } from './shared/protocol';
import type {
  OptimizerInventoryImportResult,
  OptimizerInventoryImportReport,
  OptimizerInventorySnapshot,
} from './shared/optimizer-inventory';
import {
  validateOptimizerHeroDraft,
  type OptimizerArtifactSummary,
  type OptimizerHeroDraft,
} from './shared/optimizer-profile';
import {
  withLocalThemeFallback,
  type DesktopSettings,
  type SettingsPreview,
  type SettingsPreviewRequest,
  type SettingsSnapshot,
  type SettingsThemePreference,
} from './shared/settings';
import { useTheme } from './theme';
import type {
  UpdateApplyResult,
  UpdateSnapshot,
} from './shared/update';
import { Alert, Button, EmptyState, Skeleton, ToastRegion, type ToastNotice } from './ui';

type ViewState = BackendConnectionState | { state: 'connecting' };

function initialPage(): PageId {
  return typeof window === 'undefined' ? 'overview' : pageIdFromHash(window.location.hash);
}

function healthState(backend: ViewState, health: HealthSnapshot | null): OverallHealthState | null {
  if (health) {
    return health.overall;
  }
  return backend.state === 'error' ? 'error' : null;
}

function analyzerReadiness(
  health: HealthSnapshot | null,
  options: AnalyzerOptions | null,
): { available: boolean; reason?: string } {
  if (!health || !options) {
    return { available: false, reason: 'Auto-detect readiness is still being checked.' };
  }
  const unavailable = options.autoDetectCapabilities
    .map((id) => health.capabilities.find((capability) => capability.id === id))
    .filter((capability) => !capability || capability.state !== 'ready');
  if (unavailable.length === 0) return { available: true };
  const names = unavailable.map((capability) => capability?.title ?? 'a required local capability');
  return {
    available: false,
    reason: `Auto-detect needs ${names.join(' and ')}. Manual evaluation remains available.`,
  };
}

export function App(): React.JSX.Element {
  const { preference, setPreference } = useTheme();
  const [activePage, setActivePage] = useState<PageId>(initialPage);
  const [backend, setBackend] = useState<ViewState>({ state: 'connecting' });
  const [health, setHealth] = useState<HealthSnapshot | null>(null);
  const [settings, setSettings] = useState<SettingsSnapshot | null>(null);
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [settingsDraftDirty, setSettingsDraftDirty] = useState(false);
  const [update, setUpdate] = useState<UpdateSnapshot | null>(null);
  const [analyzerOptions, setAnalyzerOptions] = useState<AnalyzerOptions | null>(null);
  const [analyzerScan, setAnalyzerScan] = useState<AnalyzerScanSnapshot | null>(null);
  const [analyzerEvaluating, setAnalyzerEvaluating] = useState(false);
  const [enhancementOptions, setEnhancementOptions] = useState<EnhancementOptions | null>(null);
  const [enhancementJob, setEnhancementJob] = useState<EnhancementSnapshot | null>(null);
  const [optimizerInventory, setOptimizerInventory] = useState<OptimizerInventorySnapshot | null>(null);
  const [optimizerReport, setOptimizerReport] = useState<OptimizerInventoryImportReport | null>(null);
  const [optimizerCapturing, setOptimizerCapturing] = useState(false);
  const [optimizerImporting, setOptimizerImporting] = useState(false);
  const [optimizerResetting, setOptimizerResetting] = useState(false);
  const [optimizerEquipping, setOptimizerEquipping] = useState(false);
  const [optimizerNotice, setOptimizerNotice] = useState<OptimizerInventoryNotice | null>(null);
  const [optimizerLoadError, setOptimizerLoadError] = useState<string | null>(null);
  const [optimizerProfile, dispatchOptimizerProfile] = useReducer(
    optimizerProfileWorkspaceReducer,
    initialOptimizerProfileWorkspaceState,
  );
  const [optimizerSearch, dispatchOptimizerSearch] = useReducer(
    optimizerSearchWorkspaceReducer,
    initialOptimizerSearchWorkspaceState,
  );
  const [optimizerResults, dispatchOptimizerResults] = useReducer(
    optimizerResultWorkspaceReducer,
    initialOptimizerResultWorkspaceState,
  );
  const [optimizerResultDetail, dispatchOptimizerResultDetail] = useReducer(
    optimizerResultDetailWorkspaceReducer,
    initialOptimizerResultDetailWorkspaceState,
  );
  const [optimizerResultExport, setOptimizerResultExport] = useState<OptimizerResultExportSnapshot | null>(null);
  const [notices, setNotices] = useState<ToastNotice[]>([]);
  const noticeCounter = useRef(0);
  const settingsRef = useRef<SettingsSnapshot | null>(null);
  const initialThemeRef = useRef(preference);
  const backendConnectionGeneration = useRef(0);
  const optimizerImportGeneration = useRef(0);
  const optimizerHeroSearchGeneration = useRef(0);
  const optimizerArtifactSearchGeneration = useRef(0);
  const optimizerSelectionGeneration = useRef(0);
  const optimizerSaveGeneration = useRef(0);
  const optimizerSearchGeneration = useRef(0);
  const optimizerResultQueryGeneration = useRef(0);
  const optimizerResultDetailGeneration = useRef(0);
  const optimizerEquipGeneration = useRef(0);
  const optimizerActiveSearchJobId = useRef<string | null>(null);
  const optimizerSearchStartPending = useRef(false);
  const optimizerActiveResultRunId = useRef<string | null>(null);
  const optimizerActiveResultQueryId = useRef<string | null>(null);
  const optimizerActiveDetailRequest = useRef<OptimizerResultDetailRequest | null>(null);
  const previousActivePage = useRef(activePage);

  const notify = useCallback((message: string, tone: ToastNotice['tone'] = 'danger') => {
    const id = `notice-${++noticeCounter.current}`;
    setNotices((current) => [...current, { id, message, tone }].slice(-3));
  }, []);

  const applySettings = useCallback((snapshot: SettingsSnapshot) => {
    const effective = withLocalThemeFallback(snapshot, initialThemeRef.current);
    settingsRef.current = effective;
    setSettings(effective);
    setPreference(effective.settings.appearance.theme);
  }, [setPreference]);

  const acceptHealth = useCallback((snapshot: HealthSnapshot) => {
    setHealth((current) => (shouldAcceptHealthSnapshot(current, snapshot) ? snapshot : current));
  }, []);

  const acceptEnhancement = useCallback((snapshot: EnhancementSnapshot) => {
    setEnhancementJob((current) =>
      shouldAcceptEnhancementSnapshot(current, snapshot) ? snapshot : current);
  }, []);

  const connect = useCallback(async () => {
    const connectionGeneration = ++backendConnectionGeneration.current;
    ++optimizerImportGeneration.current;
    ++optimizerHeroSearchGeneration.current;
    ++optimizerArtifactSearchGeneration.current;
    ++optimizerSelectionGeneration.current;
    ++optimizerSaveGeneration.current;
    dispatchOptimizerProfile({ type: 'async-reset' });
    ++optimizerSearchGeneration.current;
    ++optimizerResultQueryGeneration.current;
    dispatchOptimizerSearch({ type: 'session-reset' });
    dispatchOptimizerResults({ type: 'session-reset' });
    ++optimizerResultDetailGeneration.current;
    ++optimizerEquipGeneration.current;
    dispatchOptimizerResultDetail({ type: 'session-reset' });
    setOptimizerResultExport(null);
    optimizerActiveSearchJobId.current = null;
    optimizerSearchStartPending.current = false;
    optimizerActiveResultRunId.current = null;
    optimizerActiveResultQueryId.current = null;
    optimizerActiveDetailRequest.current = null;
    setOptimizerCapturing(false);
    setOptimizerImporting(false);
    setOptimizerEquipping(false);
    setOptimizerResetting(false);
    setHealth(null);
    setBackend({ state: 'connecting' });
    try {
      const connection = await window.e7.pingBackend();
      if (connectionGeneration !== backendConnectionGeneration.current) return;
      setBackend(connection);
      if (connection.state === 'ready') {
        const [
          healthResult,
          settingsResult,
          analyzerOptionsResult,
          analyzerScanResult,
          enhancementOptionsResult,
          enhancementJobResult,
          optimizerInventoryResult,
          optimizerHeroesResult,
          optimizerSearchResult,
          optimizerResultOptionsResult,
          optimizerResultResult,
          optimizerResultExportResult,
        ] = await Promise.allSettled([
          window.e7.getHealth(),
          window.e7.getSettings(),
          window.e7.getAnalyzerOptions(),
          window.e7.getAnalyzerScan(),
          window.e7.getEnhancementOptions(),
          window.e7.getEnhancementJob(),
          window.e7.getOptimizerInventory(),
          window.e7.searchOptimizerHeroes('', 20),
          window.e7.getOptimizerSearch(),
          window.e7.getOptimizerResultOptions(),
          window.e7.getOptimizerResults(),
          window.e7.getOptimizerResultExport(),
        ]);
        if (connectionGeneration !== backendConnectionGeneration.current) return;
        if (healthResult.status === 'fulfilled') {
          acceptHealth(healthResult.value);
        } else {
          notify(healthResult.reason instanceof Error ? healthResult.reason.message : 'Health Center could not load.');
        }
        if (settingsResult.status === 'fulfilled') {
          applySettings(settingsResult.value);
        } else {
          notify(settingsResult.reason instanceof Error ? settingsResult.reason.message : 'Settings could not load.');
        }
        if (analyzerOptionsResult.status === 'fulfilled') {
          setAnalyzerOptions(analyzerOptionsResult.value);
        } else {
          notify(analyzerOptionsResult.reason instanceof Error
            ? analyzerOptionsResult.reason.message
            : 'Analyzer options could not load.');
        }
        if (analyzerScanResult.status === 'fulfilled') {
          setAnalyzerScan(analyzerScanResult.value);
        }
        if (enhancementOptionsResult.status === 'fulfilled') {
          setEnhancementOptions(enhancementOptionsResult.value);
        } else {
          notify(enhancementOptionsResult.reason instanceof Error
            ? enhancementOptionsResult.reason.message
            : 'Enhancement options could not load.');
        }
        if (enhancementJobResult.status === 'fulfilled') {
          acceptEnhancement(enhancementJobResult.value);
        }
        if (optimizerInventoryResult.status === 'fulfilled') {
          setOptimizerInventory(optimizerInventoryResult.value);
          setOptimizerLoadError(null);
        } else {
          const message = optimizerInventoryResult.reason instanceof Error
            ? optimizerInventoryResult.reason.message
            : 'Optimizer inventory could not load.';
          setOptimizerLoadError(message);
          notify(message);
        }
        if (optimizerHeroesResult.status === 'fulfilled') {
          dispatchOptimizerProfile({
            type: 'hero-search-completed',
            query: optimizerHeroesResult.value.query,
            results: optimizerHeroesResult.value.results,
          });
        } else {
          dispatchOptimizerProfile({
            type: 'hero-search-failed',
            query: '',
            message: optimizerHeroesResult.reason instanceof Error
              ? optimizerHeroesResult.reason.message
              : 'Hero search could not load.',
          });
        }
        if (optimizerSearchResult.status === 'fulfilled') {
          optimizerActiveSearchJobId.current = optimizerSearchResult.value.jobId;
          dispatchOptimizerSearch({ type: 'snapshot-received', snapshot: optimizerSearchResult.value });
        } else {
          dispatchOptimizerSearch({
            type: 'command-failed',
            message: optimizerSearchResult.reason instanceof Error
              ? optimizerSearchResult.reason.message
              : 'Optimizer search status could not load.',
          });
        }
        if (optimizerResultOptionsResult.status === 'fulfilled') {
          dispatchOptimizerResults({ type: 'options-received', options: optimizerResultOptionsResult.value });
        } else {
          dispatchOptimizerResults({
            type: 'command-failed',
            message: optimizerResultOptionsResult.reason instanceof Error
              ? optimizerResultOptionsResult.reason.message
              : 'Result explorer options could not load.',
          });
        }
        if (optimizerResultResult.status === 'fulfilled') {
          optimizerActiveResultRunId.current = optimizerResultResult.value.runId;
          optimizerActiveResultQueryId.current = optimizerResultResult.value.queryId;
          dispatchOptimizerResults({ type: 'snapshot-received', snapshot: optimizerResultResult.value });
        }
        if (optimizerResultExportResult.status === 'fulfilled') {
          setOptimizerResultExport(optimizerResultExportResult.value);
        }
      }
    } catch (error: unknown) {
      if (connectionGeneration !== backendConnectionGeneration.current) return;
      const message = error instanceof Error ? error.message : 'The application backend did not answer.';
      setBackend({ state: 'error', message });
      notify(message);
    }
  }, [acceptEnhancement, acceptHealth, applySettings, notify]);

  useEffect(() => {
    const unsubscribeHealth = window.e7.onHealthUpdated(acceptHealth);
    const unsubscribeSettings = window.e7.onSettingsUpdated(applySettings);
    const unsubscribeAnalyzer = window.e7.onAnalyzerUpdated(setAnalyzerScan);
    const unsubscribeEnhancement = window.e7.onEnhancementUpdated(acceptEnhancement);
    const unsubscribeOptimizerSearch = window.e7.onOptimizerSearchUpdated((snapshot) => {
      if (snapshot.state !== 'idle') {
        if (optimizerActiveSearchJobId.current !== null && snapshot.jobId !== optimizerActiveSearchJobId.current) return;
        if (optimizerActiveSearchJobId.current === null && !optimizerSearchStartPending.current) return;
        optimizerActiveSearchJobId.current = snapshot.jobId;
        optimizerSearchStartPending.current = false;
      } else if (optimizerActiveSearchJobId.current !== null) {
        return;
      }
      dispatchOptimizerSearch({ type: 'snapshot-received', snapshot });
      if (snapshot.state !== 'completed') {
        ++optimizerResultQueryGeneration.current;
        dispatchOptimizerResults({ type: 'session-reset' });
        ++optimizerResultDetailGeneration.current;
        dispatchOptimizerResultDetail({ type: 'session-reset' });
        optimizerActiveResultRunId.current = null;
        optimizerActiveResultQueryId.current = null;
        optimizerActiveDetailRequest.current = null;
      }
    });
    const unsubscribeOptimizerResults = window.e7.onOptimizerResultsUpdated((snapshot) => {
      if (snapshot.state !== 'idle') {
        if (optimizerActiveResultRunId.current === null || snapshot.runId !== optimizerActiveResultRunId.current) return;
        if (optimizerActiveResultQueryId.current !== null && snapshot.queryId !== optimizerActiveResultQueryId.current) return;
        optimizerActiveResultQueryId.current = snapshot.queryId;
      } else if (optimizerActiveResultRunId.current !== null) {
        return;
      }
      dispatchOptimizerResults({ type: 'snapshot-received', snapshot });
    });
    const unsubscribeOptimizerResultDetail = window.e7.onOptimizerResultDetailUpdated((snapshot) => {
      const expected = optimizerActiveDetailRequest.current;
      if (!expected
        || snapshot.runId !== expected.runId
        || snapshot.queryId !== expected.queryId
        || snapshot.rowKey !== expected.rowKey) return;
      dispatchOptimizerResultDetail({ type: 'snapshot-received', snapshot });
    });
    const unsubscribeOptimizerResultExport = window.e7.onOptimizerResultExportUpdated(setOptimizerResultExport);
    const unsubscribeUpdate = window.e7.onUpdateChanged(setUpdate);
    const syncHash = (): void => setActivePage(pageIdFromHash(window.location.hash));
    window.addEventListener('hashchange', syncHash);
    void connect();
    void window.e7.getUpdate().then(setUpdate).catch(() => undefined);
    return () => {
      unsubscribeHealth();
      unsubscribeSettings();
      unsubscribeAnalyzer();
      unsubscribeEnhancement();
      unsubscribeOptimizerSearch();
      unsubscribeOptimizerResults();
      unsubscribeOptimizerResultDetail();
      unsubscribeOptimizerResultExport();
      unsubscribeUpdate();
      window.removeEventListener('hashchange', syncHash);
    };
  }, [acceptEnhancement, acceptHealth, applySettings, connect]);

  useEffect(() => {
    if (previousActivePage.current === 'optimizer' && activePage !== 'optimizer') {
      ++optimizerResultDetailGeneration.current;
      optimizerActiveDetailRequest.current = null;
      dispatchOptimizerResultDetail({ type: 'closed' });
    }
    previousActivePage.current = activePage;
  }, [activePage]);

  const navigate = useCallback((pageId: PageId) => {
    setActivePage(pageId);
    if (window.location.hash !== pageHash(pageId)) {
      window.location.hash = pageHash(pageId);
    }
    window.requestAnimationFrame(() => document.getElementById('main-content')?.focus());
  }, []);

  const checkUpdate = useCallback(async (): Promise<void> => {
    try {
      setUpdate(await window.e7.checkForUpdates());
    } catch (error: unknown) {
      notify(error instanceof Error ? error.message : 'Update checking could not start.');
    }
  }, [notify]);

  const downloadUpdate = useCallback(async (): Promise<void> => {
    try {
      setUpdate(await window.e7.downloadUpdate());
    } catch (error: unknown) {
      notify(error instanceof Error ? error.message : 'The update download could not start.');
    }
  }, [notify]);

  const installUpdateLater = useCallback(async (): Promise<void> => {
    try {
      setUpdate(await window.e7.installUpdateOnQuit());
    } catch (error: unknown) {
      notify(error instanceof Error ? error.message : 'Install-on-close could not be scheduled.');
    }
  }, [notify]);

  const openUpdateRelease = useCallback(async (): Promise<void> => {
    try {
      await window.e7.openUpdateRelease();
    } catch (error: unknown) {
      notify(error instanceof Error ? error.message : 'Release notes could not be opened.');
    }
  }, [notify]);

  const applyUpdate = useCallback(async (confirmActiveWork: boolean): Promise<UpdateApplyResult> => {
    try {
      const result = await window.e7.applyUpdate({
        unsavedChanges: optimizerProfile.dirty || settingsDraftDirty,
        confirmActiveWork,
      });
      setUpdate(result.snapshot);
      return result;
    } catch (error: unknown) {
      notify(error instanceof Error ? error.message : 'The downloaded update could not be applied.');
      throw error;
    }
  }, [notify, optimizerProfile.dirty, settingsDraftDirty]);

  const refresh = useCallback(async () => {
    try {
      acceptHealth(await window.e7.refreshHealth());
    } catch (error: unknown) {
      notify(error instanceof Error ? error.message : 'System health could not be refreshed.');
    }
  }, [acceptHealth, notify]);

  const runAction = useCallback(async (actionId: HealthActionId) => {
    try {
      acceptHealth(await window.e7.runHealthAction(actionId));
    } catch (error: unknown) {
      notify(error instanceof Error ? error.message : 'The health action could not be completed.');
    }
  }, [acceptHealth, notify]);

  const previewTheme = useCallback((theme: SettingsThemePreference) => {
    setPreference(theme);
  }, [setPreference]);

  const changeTheme = useCallback((theme: SettingsThemePreference) => {
    setPreference(theme);
    const current = settingsRef.current;
    if (!current || current.readOnly || current.settings.appearance.theme === theme) {
      return;
    }
    void window.e7.updateSettings(current.revision, { appearance: { theme } })
      .then(applySettings)
      .catch((error: unknown) => {
        notify(error instanceof Error ? error.message : 'Theme preference could not be saved.');
      });
  }, [applySettings, notify, setPreference]);

  const saveSettings = useCallback(async (value: DesktopSettings): Promise<SettingsSnapshot> => {
    const current = settingsRef.current;
    if (!current) {
      throw new Error('Settings are not loaded.');
    }
    setSettingsSaving(true);
    try {
      const saved = await window.e7.updateSettings(current.revision, value);
      applySettings(saved);
      notify('Settings saved safely.', 'success');
      return saved;
    } catch (error: unknown) {
      notify(error instanceof Error ? error.message : 'Settings could not be saved.');
      throw error;
    } finally {
      setSettingsSaving(false);
    }
  }, [applySettings, notify]);

  const reloadSettings = useCallback(async (): Promise<SettingsSnapshot> => {
    const loaded = await window.e7.getSettings();
    applySettings(loaded);
    notify('Settings reloaded from disk.', 'info');
    return loaded;
  }, [applySettings, notify]);

  const previewSettings = useCallback(
    async (draft: DesktopSettings, request: SettingsPreviewRequest): Promise<SettingsPreview> => (
      window.e7.previewSettings(draft, request)
    ),
    [],
  );

  const evaluateAnalyzer = useCallback(async (piece: AnalyzerPiece): Promise<AnalyzerEvaluation> => {
    setAnalyzerEvaluating(true);
    try {
      const result = await window.e7.evaluateAnalyzerPiece(piece);
      notify('Gear evaluation complete.', 'success');
      return result;
    } catch (error: unknown) {
      notify(error instanceof Error ? error.message : 'Gear evaluation failed.');
      throw error;
    } finally {
      setAnalyzerEvaluating(false);
    }
  }, [notify]);

  const startAnalyzerScan = useCallback(async (): Promise<AnalyzerScanSnapshot> => {
    try {
      const started = await window.e7.startAnalyzerScan();
      setAnalyzerScan(started);
      return started;
    } catch (error: unknown) {
      notify(error instanceof Error ? error.message : 'Gear scan could not start.');
      throw error;
    }
  }, [notify]);

  const cancelAnalyzerScan = useCallback(async (jobId: string): Promise<AnalyzerScanSnapshot> => {
    try {
      const cancelled = await window.e7.cancelAnalyzerScan(jobId);
      setAnalyzerScan(cancelled);
      notify('Cancelling the gear scan.', 'info');
      return cancelled;
    } catch (error: unknown) {
      notify(error instanceof Error ? error.message : 'Gear scan could not be cancelled.');
      throw error;
    }
  }, [notify]);

  const getAnalyzerDebug = useCallback(async (): Promise<AnalyzerDebug> => window.e7.getAnalyzerDebug(), []);

  const startEnhancement = useCallback(async (options: EnhancementStartOptions): Promise<EnhancementSnapshot> => {
    try {
      const started = await window.e7.startEnhancementJob(options);
      acceptEnhancement(started);
      notify('Enhancement automation started.', 'success');
      return started;
    } catch (error: unknown) {
      notify(error instanceof Error ? error.message : 'Enhancement automation could not start.');
      throw error;
    }
  }, [acceptEnhancement, notify]);

  const cancelEnhancement = useCallback(async (jobId: string): Promise<EnhancementSnapshot> => {
    try {
      const cancelling = await window.e7.cancelEnhancementJob(jobId);
      acceptEnhancement(cancelling);
      notify('Stopping before the next automation action.', 'info');
      return cancelling;
    } catch (error: unknown) {
      notify(error instanceof Error ? error.message : 'Enhancement automation could not stop.');
      throw error;
    }
  }, [acceptEnhancement, notify]);

  const getEnhancementDebug = useCallback(async (): Promise<EnhancementDebug> => window.e7.getEnhancementDebug(), []);

  const refreshOptimizerInventory = useCallback(async () => {
    setOptimizerLoadError(null);
    try {
      setOptimizerInventory(await window.e7.getOptimizerInventory());
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : 'Optimizer inventory could not load.';
      setOptimizerLoadError(message);
      notify(message);
    }
  }, [notify]);

  const acceptOptimizerImport = useCallback((
    result: OptimizerInventoryImportResult,
    source: 'file' | 'game',
  ): void => {
    const { inventory, report } = result;
    setOptimizerInventory(inventory);
    ++optimizerSearchGeneration.current;
    ++optimizerResultQueryGeneration.current;
    dispatchOptimizerSearch({ type: 'session-reset' });
    dispatchOptimizerResults({ type: 'session-reset' });
    ++optimizerResultDetailGeneration.current;
    dispatchOptimizerResultDetail({ type: 'session-reset' });
    optimizerActiveSearchJobId.current = null;
    optimizerSearchStartPending.current = false;
    optimizerActiveResultRunId.current = null;
    optimizerActiveResultQueryId.current = null;
    optimizerActiveDetailRequest.current = null;
    setOptimizerReport(report);
    setOptimizerCapturing(false);
    setOptimizerLoadError(null);
    const needsReview = report.warningCount + report.rejectedCount + report.conflictCount > 0;
    setOptimizerNotice(needsReview ? {
      tone: 'warning',
      title: 'Inventory imported with warnings',
      message: 'Accepted gear was saved. Review the bounded warning and skipped-row summary below.',
    } : {
      tone: 'success',
      title: source === 'game' ? 'Game inventory captured' : 'Inventory imported',
      message: `${report.resultingInventoryCount.toLocaleString()} owned pieces and `
        + `${report.importedHeroCount.toLocaleString()} heroes are ready.`
        + (source === 'game'
          ? ' A reusable copy was saved to Documents\\MeowtokoE7Hub\\gear.txt.'
          : ''),
    });
  }, []);

  const selectOptimizerInventory = useCallback(async () => {
    if (optimizerImporting || optimizerCapturing) return;
    const generation = ++optimizerImportGeneration.current;
    setOptimizerImporting(true);
    setOptimizerNotice(null);
    try {
      const selection = await window.e7.selectOptimizerInventoryFile();
      if (generation !== optimizerImportGeneration.current) return;
      if (selection.outcome === 'cancelled') {
        setOptimizerNotice({
          tone: 'info',
          title: 'Import cancelled',
          message: 'Your previous inventory and import report were left unchanged.',
        });
        return;
      }
      acceptOptimizerImport(selection.import, 'file');
    } catch (error: unknown) {
      if (generation !== optimizerImportGeneration.current) return;
      setOptimizerNotice({
        tone: 'danger',
        title: 'Inventory import failed',
        message: error instanceof Error ? error.message : 'The selected file could not be imported safely.',
      });
    } finally {
      if (generation === optimizerImportGeneration.current) setOptimizerImporting(false);
    }
  }, [acceptOptimizerImport, optimizerCapturing, optimizerImporting]);

  const startOptimizerInventoryCapture = useCallback(async () => {
    if (optimizerImporting || optimizerCapturing) return;
    const generation = ++optimizerImportGeneration.current;
    setOptimizerImporting(true);
    setOptimizerNotice(null);
    try {
      await window.e7.startOptimizerInventoryCapture();
      if (generation !== optimizerImportGeneration.current) return;
      setOptimizerCapturing(true);
      setOptimizerNotice({
        tone: 'info',
        title: 'Listening for your game account',
        message: 'Follow the capture instructions above, then click Done Capturing.',
      });
    } catch (error: unknown) {
      if (generation !== optimizerImportGeneration.current) return;
      setOptimizerNotice({
        tone: 'danger',
        title: 'Game capture failed',
        message: error instanceof Error ? error.message : 'The account packet could not be captured safely.',
      });
    } finally {
      if (generation === optimizerImportGeneration.current) setOptimizerImporting(false);
    }
  }, [optimizerCapturing, optimizerImporting]);

  const finishOptimizerInventoryCapture = useCallback(async () => {
    if (optimizerImporting || !optimizerCapturing) return;
    const generation = ++optimizerImportGeneration.current;
    setOptimizerImporting(true);
    try {
      const result = await window.e7.finishOptimizerInventoryCapture();
      if (generation !== optimizerImportGeneration.current) return;
      acceptOptimizerImport(result, 'game');
    } catch (error: unknown) {
      if (generation !== optimizerImportGeneration.current) return;
      setOptimizerCapturing(false);
      setOptimizerNotice({
        tone: 'danger',
        title: 'Game capture not ready',
        message: error instanceof Error ? error.message : 'The account packet could not be captured safely.',
      });
    } finally {
      if (generation === optimizerImportGeneration.current) setOptimizerImporting(false);
    }
  }, [acceptOptimizerImport, optimizerCapturing, optimizerImporting]);

  const resetOptimizerData = useCallback(async (): Promise<void> => {
    if (optimizerResetting || optimizerImporting || optimizerCapturing) return;
    const generation = ++optimizerImportGeneration.current;
    setOptimizerResetting(true);
    setOptimizerNotice(null);
    try {
      const result = await window.e7.resetOptimizerData();
      if (generation !== optimizerImportGeneration.current) return;
      ++optimizerHeroSearchGeneration.current;
      ++optimizerArtifactSearchGeneration.current;
      ++optimizerSelectionGeneration.current;
      ++optimizerSaveGeneration.current;
      ++optimizerSearchGeneration.current;
      ++optimizerResultQueryGeneration.current;
      ++optimizerResultDetailGeneration.current;
      dispatchOptimizerProfile({ type: 'data-reset' });
      dispatchOptimizerSearch({ type: 'session-reset' });
      dispatchOptimizerResults({ type: 'session-reset' });
      dispatchOptimizerResultDetail({ type: 'session-reset' });
      setOptimizerResultExport(null);
      optimizerActiveSearchJobId.current = null;
      optimizerSearchStartPending.current = false;
      optimizerActiveResultRunId.current = null;
      optimizerActiveResultQueryId.current = null;
      optimizerActiveDetailRequest.current = null;
      setOptimizerInventory(result.inventory);
      setOptimizerReport(null);
      setOptimizerLoadError(null);
      setOptimizerNotice({
        tone: 'success',
        title: 'Optimizer data erased',
        message: 'Gear, results, caches, and saved hero profiles were permanently removed.',
      });
      notify('All Optimizer data was erased.', 'success');
    } catch (error: unknown) {
      if (generation !== optimizerImportGeneration.current) return;
      setOptimizerNotice({
        tone: 'danger',
        title: 'Optimizer data was not erased',
        message: error instanceof Error ? error.message : 'The reset could not finish safely.',
      });
      throw error;
    } finally {
      if (generation === optimizerImportGeneration.current) setOptimizerResetting(false);
    }
  }, [notify, optimizerCapturing, optimizerImporting, optimizerResetting]);

  const searchOptimizerHeroes = useCallback(async (query: string) => {
    const generation = ++optimizerHeroSearchGeneration.current;
    dispatchOptimizerProfile({ type: 'hero-search-started', query });
    try {
      const result = await window.e7.searchOptimizerHeroes(query, 20);
      if (generation !== optimizerHeroSearchGeneration.current) return;
      dispatchOptimizerProfile({ type: 'hero-search-completed', query: result.query, results: result.results });
    } catch (error: unknown) {
      if (generation !== optimizerHeroSearchGeneration.current) return;
      dispatchOptimizerProfile({
        type: 'hero-search-failed',
        query,
        message: error instanceof Error ? error.message : 'Hero search failed.',
      });
    }
  }, []);

  const searchOptimizerArtifacts = useCallback(async (query: string) => {
    const generation = ++optimizerArtifactSearchGeneration.current;
    dispatchOptimizerProfile({ type: 'artifact-search-started', query });
    try {
      const result = await window.e7.searchOptimizerArtifacts(query, 20);
      if (generation !== optimizerArtifactSearchGeneration.current) return;
      dispatchOptimizerProfile({ type: 'artifact-search-completed', result });
    } catch (error: unknown) {
      if (generation !== optimizerArtifactSearchGeneration.current) return;
      dispatchOptimizerProfile({
        type: 'artifact-search-failed',
        query,
        message: error instanceof Error ? error.message : 'Artifact search failed.',
      });
    }
  }, []);

  const saveOptimizerDraft = useCallback(async (): Promise<boolean> => {
    const draft = optimizerProfile.envelope?.draft;
    if (!draft) return false;
    const issues = validateOptimizerHeroDraft(draft, optimizerProfile.details);
    if (issues.length > 0) {
      dispatchOptimizerProfile({
        type: 'save-failed',
        message: 'Correct the highlighted hero fields before saving.',
        issues,
      });
      return false;
    }
    const generation = ++optimizerSaveGeneration.current;
    dispatchOptimizerProfile({ type: 'save-started' });
    try {
      const envelope = await window.e7.saveOptimizerHeroDraft(draft);
      if (generation !== optimizerSaveGeneration.current) return false;
      dispatchOptimizerProfile({ type: 'save-completed', envelope });
      return true;
    } catch (error: unknown) {
      if (generation !== optimizerSaveGeneration.current) return false;
      dispatchOptimizerProfile({
        type: 'save-failed',
        message: error instanceof Error ? error.message : 'The hero draft could not be saved.',
      });
      return false;
    }
  }, [optimizerProfile.details, optimizerProfile.envelope]);

  const selectOptimizerHero = useCallback(async (heroId: string) => {
    if (!heroId || optimizerProfile.loading || optimizerProfile.saving) return;
    if (optimizerProfile.envelope?.draft.heroId === heroId) return;
    const generation = ++optimizerSelectionGeneration.current;
    ++optimizerSaveGeneration.current;
    dispatchOptimizerProfile({ type: 'selection-started' });
    if (optimizerProfile.dirty && optimizerProfile.envelope) {
      const issues = validateOptimizerHeroDraft(optimizerProfile.envelope.draft, optimizerProfile.details);
      if (issues.length > 0) {
        dispatchOptimizerProfile({
          type: 'save-failed',
          message: 'The current hero has invalid fields. Correct them before switching heroes.',
          issues,
        });
        dispatchOptimizerProfile({ type: 'selection-failed', message: 'Hero switch stopped so the current draft is not lost.' });
        return;
      }
      try {
        await window.e7.saveOptimizerHeroDraft(optimizerProfile.envelope.draft);
      } catch (error: unknown) {
        dispatchOptimizerProfile({
          type: 'selection-failed',
          message: error instanceof Error ? error.message : 'The current hero could not be saved before switching.',
        });
        return;
      }
    }
    try {
      const [details, envelope] = await Promise.all([
        window.e7.getOptimizerHeroDetails(heroId),
        window.e7.loadOptimizerHeroDraft(heroId),
      ]);
      if (generation !== optimizerSelectionGeneration.current) return;
      dispatchOptimizerProfile({ type: 'selection-completed', details, envelope });
      ++optimizerSearchGeneration.current;
      ++optimizerResultQueryGeneration.current;
      ++optimizerResultDetailGeneration.current;
      optimizerActiveSearchJobId.current = null;
      optimizerSearchStartPending.current = false;
      optimizerActiveResultRunId.current = null;
      optimizerActiveResultQueryId.current = null;
      optimizerActiveDetailRequest.current = null;
      dispatchOptimizerSearch({ type: 'session-reset' });
      dispatchOptimizerResults({ type: 'session-reset' });
      dispatchOptimizerResultDetail({ type: 'session-reset' });
      setOptimizerResultExport(null);
    } catch (error: unknown) {
      if (generation !== optimizerSelectionGeneration.current) return;
      dispatchOptimizerProfile({
        type: 'selection-failed',
        message: error instanceof Error ? error.message : 'The selected hero could not be loaded.',
      });
    }
  }, [optimizerProfile.details, optimizerProfile.dirty, optimizerProfile.envelope, optimizerProfile.loading, optimizerProfile.saving]);

  const updateOptimizerDraft = useCallback((draft: OptimizerHeroDraft) => {
    dispatchOptimizerProfile({ type: 'draft-updated', draft });
  }, []);

  const chooseOptimizerArtifact = useCallback((artifact: OptimizerArtifactSummary | null) => {
    dispatchOptimizerProfile({ type: 'artifact-selected', artifact });
  }, []);

  const startOptimizerSearch = useCallback(async () => {
    const draft = optimizerProfile.envelope?.draft;
    const validInventory = optimizerInventory?.state === 'ready';
    const issues = draft ? validateOptimizerHeroDraft(draft, optimizerProfile.details) : [];
    if (!draft || !validInventory || optimizerProfile.loading || optimizerProfile.saving || issues.length > 0) {
      dispatchOptimizerSearch({
        type: 'command-failed',
        message: 'Choose imported gear and a valid hero build before searching.',
      });
      return;
    }
    const generation = ++optimizerSearchGeneration.current;
    ++optimizerResultQueryGeneration.current;
    dispatchOptimizerResults({ type: 'session-reset' });
    ++optimizerResultDetailGeneration.current;
    dispatchOptimizerResultDetail({ type: 'session-reset' });
    optimizerActiveSearchJobId.current = null;
    optimizerSearchStartPending.current = true;
    optimizerActiveResultRunId.current = null;
    optimizerActiveResultQueryId.current = null;
    optimizerActiveDetailRequest.current = null;
    dispatchOptimizerSearch({ type: 'command-started' });
    try {
      const snapshot = await window.e7.startOptimizerSearch(draft);
      if (generation !== optimizerSearchGeneration.current) return;
      optimizerActiveSearchJobId.current = snapshot.jobId;
      optimizerSearchStartPending.current = false;
      dispatchOptimizerSearch({ type: 'snapshot-received', snapshot });
    } catch (error: unknown) {
      if (generation !== optimizerSearchGeneration.current) return;
      optimizerSearchStartPending.current = false;
      dispatchOptimizerSearch({
        type: 'command-failed',
        message: error instanceof Error ? error.message : 'Optimizer search could not start.',
      });
    }
  }, [optimizerInventory, optimizerProfile.details, optimizerProfile.envelope, optimizerProfile.loading, optimizerProfile.saving]);

  const queryOptimizerResults = useCallback(async (query: OptimizerResultQuery) => {
    const generation = ++optimizerResultQueryGeneration.current;
    ++optimizerResultDetailGeneration.current;
    dispatchOptimizerResultDetail({ type: 'session-reset' });
    optimizerActiveResultRunId.current = query.runId;
    optimizerActiveResultQueryId.current = null;
    optimizerActiveDetailRequest.current = null;
    dispatchOptimizerResults({ type: 'query-started', query });
    try {
      const snapshot = await window.e7.queryOptimizerResults(query);
      if (generation !== optimizerResultQueryGeneration.current) return;
      optimizerActiveResultRunId.current = snapshot.runId;
      optimizerActiveResultQueryId.current = snapshot.queryId;
      dispatchOptimizerResults({ type: 'snapshot-received', snapshot });
    } catch (error: unknown) {
      if (generation !== optimizerResultQueryGeneration.current) return;
      dispatchOptimizerResults({
        type: 'command-failed',
        message: error instanceof Error ? error.message : 'Result query could not start.',
      });
    }
  }, []);

  const inspectOptimizerResult = useCallback(async (request: OptimizerResultDetailRequest) => {
    const generation = ++optimizerResultDetailGeneration.current;
    optimizerActiveDetailRequest.current = request;
    dispatchOptimizerResultDetail({ type: 'selection-started', rowKey: request.rowKey });
    try {
      const snapshot = await window.e7.selectOptimizerResultDetail(request);
      if (generation !== optimizerResultDetailGeneration.current) return;
      dispatchOptimizerResultDetail({ type: 'snapshot-received', snapshot });
    } catch (error: unknown) {
      if (generation !== optimizerResultDetailGeneration.current) return;
      optimizerActiveDetailRequest.current = null;
      dispatchOptimizerResultDetail({
        type: 'selection-failed',
        message: error instanceof Error ? error.message : 'Build detail could not be opened.',
      });
    }
  }, []);

  const closeOptimizerResultDetail = useCallback(() => {
    ++optimizerResultDetailGeneration.current;
    optimizerActiveDetailRequest.current = null;
    dispatchOptimizerResultDetail({ type: 'closed' });
  }, []);

  const equipOptimizerResultBuild = useCallback(async (request: OptimizerResultDetailRequest) => {
    if (optimizerEquipping) return;
    const generation = ++optimizerEquipGeneration.current;
    setOptimizerEquipping(true);
    try {
      const result = await window.e7.equipOptimizerResultBuild(request);
      if (generation !== optimizerEquipGeneration.current) return;
      setOptimizerInventory((current) => current?.state === 'ready'
        ? { ...current, equippedItems: result.inventoryEquippedItems }
        : current);
      dispatchOptimizerResultDetail({
        type: 'local-equip-completed',
        heroName: result.heroName,
      });
      notify(
        `${result.heroName} now has this six-piece build in Meowtoko E7 Tool. The results and gear cards remain open for reference.`,
        'success',
      );
    } catch (error: unknown) {
      if (generation !== optimizerEquipGeneration.current) return;
      notify(error instanceof Error ? error.message : 'The selected build could not be equipped locally.');
    } finally {
      if (generation === optimizerEquipGeneration.current) setOptimizerEquipping(false);
    }
  }, [notify, optimizerEquipping]);

  const exportOptimizerResults = useCallback(async (
    runId: string,
    queryId: string,
    format: OptimizerResultExportFormat,
  ) => {
    try {
      const selection = await window.e7.selectOptimizerResultExport({ runId, queryId, format });
      if (selection.status === 'started') setOptimizerResultExport(selection.snapshot);
    } catch (error: unknown) {
      notify(error instanceof Error ? error.message : 'Result export could not start.');
    }
  }, [notify]);

  const cancelOptimizerResultExport = useCallback(async (exportId: string) => {
    try {
      setOptimizerResultExport(await window.e7.cancelOptimizerResultExport(exportId));
    } catch (error: unknown) {
      notify(error instanceof Error ? error.message : 'Result export could not be cancelled.');
    }
  }, [notify]);

  const cancelOptimizerResults = useCallback(async (queryId: string) => {
    const generation = ++optimizerResultQueryGeneration.current;
    dispatchOptimizerResults({ type: 'command-started' });
    try {
      const snapshot = await window.e7.cancelOptimizerResults(queryId);
      if (generation !== optimizerResultQueryGeneration.current) return;
      optimizerActiveResultRunId.current = snapshot.runId;
      optimizerActiveResultQueryId.current = snapshot.queryId;
      dispatchOptimizerResults({ type: 'snapshot-received', snapshot });
    } catch (error: unknown) {
      if (generation !== optimizerResultQueryGeneration.current) return;
      dispatchOptimizerResults({
        type: 'command-failed',
        message: error instanceof Error ? error.message : 'Result query could not be cancelled.',
      });
    }
  }, []);

  const cancelOptimizerSearch = useCallback(async (jobId: string) => {
    const generation = ++optimizerSearchGeneration.current;
    dispatchOptimizerSearch({ type: 'command-started' });
    try {
      const snapshot = await window.e7.cancelOptimizerSearch(jobId);
      if (generation !== optimizerSearchGeneration.current) return;
      dispatchOptimizerSearch({ type: 'snapshot-received', snapshot });
    } catch (error: unknown) {
      if (generation !== optimizerSearchGeneration.current) return;
      dispatchOptimizerSearch({
        type: 'command-failed',
        message: error instanceof Error ? error.message : 'Optimizer search could not be cancelled.',
      });
    }
  }, []);

  const retryOptimizerSearchWithCpu = useCallback(async (jobId: string) => {
    const generation = ++optimizerSearchGeneration.current;
    optimizerActiveSearchJobId.current = null;
    optimizerSearchStartPending.current = true;
    dispatchOptimizerSearch({ type: 'command-started' });
    try {
      const snapshot = await window.e7.retryOptimizerSearchWithCpu(jobId);
      if (generation !== optimizerSearchGeneration.current) return;
      optimizerActiveSearchJobId.current = snapshot.jobId;
      optimizerSearchStartPending.current = false;
      dispatchOptimizerSearch({ type: 'snapshot-received', snapshot });
    } catch (error: unknown) {
      if (generation !== optimizerSearchGeneration.current) return;
      optimizerSearchStartPending.current = false;
      dispatchOptimizerSearch({
        type: 'command-failed',
        message: error instanceof Error ? error.message : 'CPU recovery could not start.',
      });
    }
  }, []);

  let page: React.ReactNode;
  if (activePage === 'enhancer') {
    if (enhancementOptions && enhancementJob) {
      page = (
        <EnhancerCenter
          health={health}
          onCancel={cancelEnhancement}
          onGetDebug={getEnhancementDebug}
          onStart={startEnhancement}
          options={enhancementOptions}
          snapshot={enhancementJob}
        />
      );
    } else if (backend.state === 'error') {
      page = (
        <EmptyState
          action={<Button onClick={() => void connect()}>Reconnect backend</Button>}
          description="Reconnect the local backend to use ADB automation."
          icon="enhancer"
          title="Enhancer is unavailable"
        />
      );
    } else {
      page = <Skeleton label="Loading enhancement automation" lines={8} />;
    }
  } else if (activePage === 'gear') {
    if (backend.state === 'error') {
      page = (
        <EmptyState
          action={<Button onClick={() => void connect()}>Reconnect backend</Button>}
          description="Reconnect the local backend to browse imported equipment."
          icon="gear"
          title="Gear is unavailable"
        />
      );
    } else if (backend.state !== 'ready') {
      page = <Skeleton label="Loading gear inventory" lines={8} />;
    } else if (optimizerInventory) {
      page = (
        <GearCenter
          inventory={optimizerInventory}
          onOpenImporter={() => navigate('importer')}
        />
      );
    } else if (optimizerLoadError) {
      page = (
        <EmptyState
          action={<Button onClick={() => void refreshOptimizerInventory()}>Retry inventory status</Button>}
          description={optimizerLoadError}
          icon="alert"
          title="Gear inventory could not be loaded"
        />
      );
    } else {
      page = <Skeleton label="Loading gear inventory" lines={8} />;
    }
  } else if (activePage === 'analyzer') {
    if (analyzerOptions && analyzerScan) {
      const readiness = analyzerReadiness(health, analyzerOptions);
      page = (
        <AnalyzerCenter
          autoDetectAvailable={readiness.available}
          autoDetectReason={readiness.reason}
          evaluating={analyzerEvaluating}
          onCancelScan={cancelAnalyzerScan}
          onEvaluate={evaluateAnalyzer}
          onGetDebug={getAnalyzerDebug}
          onStartScan={startAnalyzerScan}
          options={analyzerOptions}
          snapshot={analyzerScan}
        />
      );
    } else if (backend.state === 'error') {
      page = (
        <EmptyState
          action={<Button onClick={() => void connect()}>Reconnect backend</Button>}
          icon="analyzer"
          title="Analyzer is unavailable"
          description="Reconnect the local backend to use manual rating and gear capture."
        />
      );
    } else {
      page = <Skeleton label="Loading gear analyzer" lines={8} />;
    }
  } else if (activePage === 'importer') {
    if (backend.state === 'error') {
      page = (
        <EmptyState
          action={<Button onClick={() => void connect()}>Reconnect backend</Button>}
          description="Reconnect the local backend to import or erase Optimizer data."
          icon="importer"
          title="Importer is unavailable"
        />
      );
    } else if (backend.state !== 'ready') {
      page = <Skeleton label="Loading importer backend" lines={6} />;
    } else if (optimizerInventory) {
      page = (
        <ImporterCenter
          capturing={optimizerCapturing}
          importing={optimizerImporting}
          inventory={optimizerInventory}
          lastReport={optimizerReport}
          notice={optimizerNotice}
          onFinishCapture={() => void finishOptimizerInventoryCapture()}
          onImport={() => void selectOptimizerInventory()}
          onReset={resetOptimizerData}
          onStartCapture={() => void startOptimizerInventoryCapture()}
          packetReady={health?.capabilities.some(
            (capability) => capability.id === 'packet' && capability.state === 'ready',
          ) ?? false}
          resetting={optimizerResetting}
        />
      );
    } else if (optimizerLoadError) {
      page = (
        <EmptyState
          action={<Button onClick={() => void refreshOptimizerInventory()}>Retry inventory status</Button>}
          description={optimizerLoadError}
          icon="alert"
          title="Inventory status could not be loaded"
        />
      );
    } else {
      page = <Skeleton label="Loading importer inventory" lines={6} />;
    }
  } else if (activePage === 'optimizer') {
    if (backend.state === 'error') {
      page = (
        <EmptyState
          action={<Button onClick={() => void connect()}>Reconnect backend</Button>}
          description="Reconnect the local application backend to safely restore Optimizer controls. Pending searches and selected-build details stay invalidated."
          icon="optimizer"
          title="Optimizer backend is unavailable"
        />
      );
    } else if (backend.state !== 'ready') {
      page = <Skeleton label="Loading optimizer backend" lines={8} />;
    } else if (optimizerInventory) {
      page = (
        <OptimizerCenter
          inventory={optimizerInventory}
          profile={optimizerProfile}
          onArtifactSearch={(query) => void searchOptimizerArtifacts(query)}
          onChooseArtifact={chooseOptimizerArtifact}
          onDraftChange={updateOptimizerDraft}
          onHeroSearch={(query) => void searchOptimizerHeroes(query)}
          onSaveDraft={() => void saveOptimizerDraft()}
          onSelectHero={(heroId) => void selectOptimizerHero(heroId)}
          search={optimizerSearch}
          onStartSearch={() => void startOptimizerSearch()}
          onCancelSearch={(jobId) => void cancelOptimizerSearch(jobId)}
          onRetrySearchWithCpu={(jobId) => void retryOptimizerSearchWithCpu(jobId)}
          results={optimizerResults}
          onQueryResults={(query) => void queryOptimizerResults(query)}
          onCancelResults={(queryId) => void cancelOptimizerResults(queryId)}
          resultDetail={optimizerResultDetail}
          optimizerEquipping={optimizerEquipping}
          onInspectResult={(request) => void inspectOptimizerResult(request)}
          onEquipResult={(request) => void equipOptimizerResultBuild(request)}
          onCloseResultDetail={closeOptimizerResultDetail}
          resultExport={optimizerResultExport}
          onExportResults={(runId, queryId, format) => void exportOptimizerResults(runId, queryId, format)}
          onCancelResultExport={(exportId) => void cancelOptimizerResultExport(exportId)}
        />
      );
    } else if (optimizerLoadError) {
      page = (
        <EmptyState
          action={<Button onClick={() => void refreshOptimizerInventory()}>Retry inventory status</Button>}
          description={optimizerLoadError}
          icon="alert"
          title="Inventory status could not be loaded"
        />
      );
    } else {
      page = <Skeleton label="Loading optimizer inventory" lines={8} />;
    }
  } else if (activePage === 'health') {
    if (health) {
      page = (
        <HealthCenter
          onAction={(actionId) => void runAction(actionId)}
          onRefresh={() => void refresh()}
          snapshot={health}
        />
      );
    } else if (backend.state === 'error') {
      page = (
        <EmptyState
          action={<Button onClick={() => void connect()}>Reconnect backend</Button>}
          icon="alert"
          title="Health checks are unavailable"
          description="Reconnect the local application backend, then retry the health check."
        />
      );
    } else {
      page = (
        <div className="page-stack">
          <Alert title="Preparing Health Center">The backend is checking local capabilities.</Alert>
          <Skeleton label="Loading system health" lines={6} />
        </div>
      );
    }
  } else if (activePage === 'settings') {
    if (settings && update) {
      page = (
        <SettingsCenter
          onPreviewTheme={previewTheme}
          onSelectAdbExecutable={() => window.e7.selectAdbExecutable()}
          onPreview={previewSettings}
          onReload={reloadSettings}
          onSave={saveSettings}
          onCheckUpdate={checkUpdate}
          onDirtyChange={setSettingsDraftDirty}
          onDownloadUpdate={downloadUpdate}
          onOpenUpdateRelease={openUpdateRelease}
          saving={settingsSaving}
          snapshot={settings}
          update={update}
        />
      );
    } else if (backend.state === 'error') {
      page = (
        <EmptyState
          action={<Button onClick={() => void connect()}>Reconnect backend</Button>}
          icon="settings"
          title="Settings are unavailable"
          description="Reconnect the local application backend to load your preferences."
        />
      );
    } else {
      page = <Skeleton label="Loading application settings" lines={8} />;
    }
  } else {
    page = (
      <Overview
        backend={backend}
        health={health}
        onOpenHealth={() => navigate('health')}
        onReconnect={() => void connect()}
      />
    );
  }

  return (
    <>
      <AppShell
        activePage={activePage}
        banner={update ? (
          <UpdateBanner
            onApply={applyUpdate}
            onCheck={checkUpdate}
            onDownload={downloadUpdate}
            onInstallLater={installUpdateLater}
            onOpenRelease={openUpdateRelease}
            snapshot={update}
          />
        ) : undefined}
        healthState={healthState(backend, health)}
        onNavigate={navigate}
        onThemeChange={changeTheme}
        themePreference={preference}
      >
        {page}
      </AppShell>
      <ToastRegion
        notices={notices}
        onDismiss={(id) => setNotices((current) => current.filter((notice) => notice.id !== id))}
      />
    </>
  );
}
