import { mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

import {
  app,
  autoUpdater,
  BrowserWindow,
  dialog,
  ipcMain,
  net,
  protocol,
  shell,
} from 'electron';

import { BackendClient } from './backend-client';
import { resolveBackendLaunch } from './backend-launch';
import {
  CharacterArtworkResolver,
  resolveCharacterArtworkRoot,
} from './character-artwork';
import { OptimizerInventoryImportCoordinator } from './optimizer-inventory-dialog';
import {
  isAnalyzerPiece,
  type AnalyzerDebug,
  type AnalyzerEvaluation,
  type AnalyzerOptions,
  type AnalyzerScanSnapshot,
} from './shared/analyzer';
import {
  isEnhancementStartOptions,
  type EnhancementDebug,
  type EnhancementOptions,
  type EnhancementSnapshot,
} from './shared/enhancement';
import { isHealthActionId, type HealthActionId, type HealthSnapshot } from './shared/health';
import type { BackendConnectionState } from './shared/protocol';
import type {
  OptimizerDataResetResult,
  OptimizerInventoryCaptureState,
  OptimizerInventoryImportResult,
  OptimizerInventorySelectionResult,
  OptimizerInventorySnapshot,
} from './shared/optimizer-inventory';
import {
  isOptimizerHeroDraft,
  type OptimizerArtifactSearchResult,
  type OptimizerHeroDetails,
  type OptimizerHeroDraftEnvelope,
  type OptimizerHeroSearchResult,
} from './shared/optimizer-profile';
import {
  isOptimizerSearchSnapshot,
  type OptimizerSearchSnapshot,
} from './shared/optimizer-search';
import {
  isOptimizerResultQuery,
  isOptimizerResultSnapshot,
  type OptimizerResultOptions,
  type OptimizerResultSnapshot,
} from './shared/optimizer-results';
import {
  isOptimizerResultDetailRequest,
  isOptimizerResultDetailSnapshot,
  type OptimizerResultDetailSnapshot,
  type OptimizerResultEquipResult,
} from './shared/optimizer-result-detail';
import {
  isOptimizerResultExportRequest,
  isOptimizerResultExportSnapshot,
  type OptimizerResultExportSelection,
  type OptimizerResultExportSnapshot,
} from './shared/optimizer-result-export';
import {
  isDesktopSettings,
  isSettingsPatch,
  isSettingsPreviewRequest,
  type SettingsPatch,
  type SettingsPreview,
  type SettingsSnapshot,
} from './shared/settings';
import { APP_USER_MODEL_ID, handleSquirrelLifecycle, retireLegacyShortcuts } from './squirrel-lifecycle';
import { CHARACTER_ARTWORK_SCHEME } from './shared/character-artwork';
import { focusPrimaryWindow } from './window-lifecycle';
import {
  automaticUpdatesEnabled,
  UpdateService,
  type UpdateFeed,
} from './update-service';
import {
  isUpdateApplyRequest,
  type UpdateApplyResult,
  type UpdateSnapshot,
} from './shared/update';

declare const MAIN_WINDOW_WEBPACK_ENTRY: string;
declare const MAIN_WINDOW_PRELOAD_WEBPACK_ENTRY: string;

const INSTALL_URLS: Partial<Record<HealthActionId, string>> = {
  'ollama.install': 'https://ollama.com/download/windows',
  'tesseract.install': 'https://github.com/UB-Mannheim/tesseract/wiki',
  'packet.install': 'https://npcap.com/#download',
  'adb.install': 'https://developer.android.com/tools/releases/platform-tools',
};

const README_CAPTURE_ROUTES = ['overview', 'analyzer', 'enhancer', 'optimizer'] as const;

function waitForRenderer(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

protocol.registerSchemesAsPrivileged([{
  scheme: CHARACTER_ARTWORK_SCHEME,
  privileges: {
    secure: true,
    standard: true,
    supportFetchAPI: false,
  },
}]);

function installCharacterArtworkProtocol(): void {
  const root = resolveCharacterArtworkRoot({
    appPath: app.getAppPath(),
    isPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
  });
  let resolver: CharacterArtworkResolver | null = null;
  try {
    resolver = CharacterArtworkResolver.load(root);
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    console.warn(`E7_CHARACTER_ARTWORK_UNAVAILABLE ${message}`);
  }
  protocol.handle(CHARACTER_ARTWORK_SCHEME, (request) => {
    if (request.method !== 'GET') {
      return new Response(null, { status: 405 });
    }
    const artworkPath = resolver?.resolve(request.url);
    if (!artworkPath) {
      return new Response(null, { status: 404 });
    }
    return net.fetch(pathToFileURL(artworkPath).toString());
  });
}

function writeSingleInstanceSmokeMarker(name: string, value: string): void {
  const directory = process.env.E7_SINGLE_INSTANCE_SMOKE_DIR;
  if (process.env.E7_SINGLE_INSTANCE_SMOKE_TEST !== '1' || !directory) return;
  mkdirSync(directory, { recursive: true });
  writeFileSync(path.join(directory, name), value, 'utf8');
}

function resolveWindowIcon(): string {
  return app.isPackaged
    ? path.join(process.resourcesPath, 'meowtoko-e7-tool.png')
    : path.resolve(app.getAppPath(), '..', 'assets', 'app', 'meowtoko-e7-tool.png');
}

function runDesktopApplication(): void {
  const backend = new BackendClient({ launch: resolveBackendLaunch });
  let mainWindow: BrowserWindow | null = null;
  let shutdownComplete = false;
  let singleInstanceTimeout: NodeJS.Timeout | null = null;
  const updateFeed: UpdateFeed = {
    setFeedURL: (options) => autoUpdater.setFeedURL(options),
    checkForUpdates: () => autoUpdater.checkForUpdates(),
    quitAndInstall: () => autoUpdater.quitAndInstall(),
    on: (event, listener) => {
      if (event === 'error') autoUpdater.on('error', listener as (error: Error, message?: string) => void);
      else if (event === 'update-downloaded') autoUpdater.on('update-downloaded', listener);
      else autoUpdater.on('update-not-available', listener);
    },
    removeListener: (event, listener) => {
      if (event === 'error') autoUpdater.removeListener('error', listener as (error: Error, message?: string) => void);
      else if (event === 'update-downloaded') autoUpdater.removeListener('update-downloaded', listener);
      else autoUpdater.removeListener('update-not-available', listener);
    },
  };
  const activeUpdateWork = async (): Promise<string[]> => {
    const checks = await Promise.allSettled([
      backend.getOptimizerSearch(),
      backend.getOptimizerResultExport(),
      backend.getAnalyzerScan(),
      backend.getEnhancementJob(),
      backend.getHealth(),
    ]);
    const active: string[] = [];
    const [search, exportJob, analyzer, enhancement, health] = checks;
    if (search.status === 'fulfilled'
      && (search.value.state === 'preparing' || search.value.state === 'running')) {
      active.push('Optimizer search');
    }
    if (exportJob.status === 'fulfilled' && exportJob.value.state === 'running') {
      active.push('Result export');
    }
    if (analyzer.status === 'fulfilled'
      && (analyzer.value.state === 'running' || analyzer.value.state === 'cancelling')) {
      active.push('Analyzer scan');
    }
    if (enhancement.status === 'fulfilled'
      && (enhancement.value.state === 'running' || enhancement.value.state === 'cancelling')) {
      active.push('Enhancement job');
    }
    if (health.status === 'fulfilled' && health.value.operation?.state === 'running') {
      active.push('Health operation');
    }
    return active;
  };
  const updates = new UpdateService({
    currentVersion: app.getVersion(),
    enabled: automaticUpdatesEnabled({
      isPackaged: app.isPackaged,
      environment: process.env,
      argv: process.argv,
    }),
    fetch: async (url, options) => net.fetch(url, options),
    feed: updateFeed,
    activeWork: activeUpdateWork,
    stopBackend: () => backend.stop(),
    beforeInstall: () => { shutdownComplete = true; },
  });
  const optimizerInventoryImport = new OptimizerInventoryImportCoordinator(
    async (options) => (
      mainWindow && !mainWindow.isDestroyed()
        ? dialog.showOpenDialog(mainWindow, options)
        : dialog.showOpenDialog(options)
    ),
    async (sourcePath) => backend.importOptimizerInventory(sourcePath),
  );

  function createWindow(): void {
    mainWindow = new BrowserWindow({
      width: 1180,
      height: 760,
      minWidth: 900,
      minHeight: 620,
      backgroundColor: '#090d18',
      icon: resolveWindowIcon(),
      show: false,
      webPreferences: {
        contextIsolation: true,
        nodeIntegration: false,
        preload: MAIN_WINDOW_PRELOAD_WEBPACK_ENTRY,
        sandbox: true,
        webSecurity: true,
      },
    });
    mainWindow.setMenuBarVisibility(false);
    mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    mainWindow.once('ready-to-show', () => {
      mainWindow?.show();
      if (process.env.E7_README_CAPTURE === '1' && !app.isPackaged && mainWindow) {
        const captureWindow = mainWindow;
        void (async () => {
          const outputRoot = path.resolve(app.getAppPath(), '..', 'assets', 'readme');
          mkdirSync(outputRoot, { recursive: true });
          captureWindow.setSize(1440, 900);
          for (const route of README_CAPTURE_ROUTES) {
            await captureWindow.webContents.executeJavaScript(
              `window.location.hash = '#/${route}'`,
            );
            await waitForRenderer(route === 'overview' ? 2_500 : 1_500);
            const scrollState = await captureWindow.webContents.executeJavaScript(
              "(() => { const sidebar = document.querySelector('.sidebar'); if (sidebar) sidebar.scrollTop = 0; document.documentElement.scrollTop = 0; document.body.scrollTop = 0; window.scrollTo(0, 0); return { sidebar: sidebar?.scrollTop ?? null, window: window.scrollY }; })()",
            ) as { sidebar: number | null; window: number };
            await waitForRenderer(100);
            const image = await captureWindow.webContents.capturePage();
            const output = path.join(outputRoot, `${route}.png`);
            writeFileSync(output, image.toPNG());
            console.log(
              `E7_README_CAPTURE_OK route=${route} bytes=${image.toPNG().length} `
              + `sidebarScroll=${scrollState.sidebar} windowScroll=${scrollState.window}`,
            );
          }
          await backend.stop();
          shutdownComplete = true;
          app.exit(0);
        })().catch((error: unknown) => {
          const message = error instanceof Error ? error.message : String(error);
          console.error(`E7_README_CAPTURE_FAILED ${message}`);
          shutdownComplete = true;
          app.exit(1);
        });
      }
    });
    void mainWindow.loadURL(MAIN_WINDOW_WEBPACK_ENTRY);
  }

  ipcMain.handle('backend:ping', async (): Promise<BackendConnectionState> => {
    try {
      const details = await backend.start();
      return { state: 'ready', details };
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : 'Unknown backend startup error.';
      return { state: 'error', message };
    }
  });

  ipcMain.handle('health:get', async (): Promise<HealthSnapshot> => backend.getHealth());
  ipcMain.handle('health:refresh', async (): Promise<HealthSnapshot> => backend.refreshHealth());
  ipcMain.handle('health:action', async (_event, actionId: unknown): Promise<HealthSnapshot> => {
    if (!isHealthActionId(actionId)) throw new Error('Unsupported health action.');
    const installUrl = INSTALL_URLS[actionId];
    if (installUrl) {
      await shell.openExternal(installUrl);
      return backend.getHealth();
    }
    return backend.runHealthAction(actionId);
  });

  ipcMain.handle('settings:get', async (): Promise<SettingsSnapshot> => backend.getSettings());
  ipcMain.handle(
    'settings:update',
    async (_event, revision: unknown, patch: unknown): Promise<SettingsSnapshot> => {
      if (typeof revision !== 'string' || !revision || !isSettingsPatch(patch)) {
        throw new Error('Invalid settings update.');
      }
      return backend.updateSettings(revision, patch as SettingsPatch);
    },
  );
  ipcMain.handle(
    'settings:preview',
    async (_event, settings: unknown, request: unknown): Promise<SettingsPreview> => {
      if (!isDesktopSettings(settings) || !isSettingsPreviewRequest(request)) {
        throw new Error('Invalid settings preview request.');
      }
      return backend.previewSettings(settings, request);
    },
  );
  ipcMain.handle('settings:adb:select', async (): Promise<string | null> => {
    const options: Electron.OpenDialogOptions = {
      title: 'Locate adb.exe',
      filters: [{ name: 'ADB executable', extensions: ['exe'] }],
      properties: ['openFile'],
    };
    const selection = mainWindow && !mainWindow.isDestroyed()
      ? await dialog.showOpenDialog(mainWindow, options)
      : await dialog.showOpenDialog(options);
    if (selection.canceled || selection.filePaths.length === 0) return null;
    const selected = selection.filePaths[0];
    if (path.basename(selected).toLocaleLowerCase() !== 'adb.exe') {
      throw new Error('Select the adb.exe executable from Android platform tools.');
    }
    return selected;
  });

  ipcMain.handle('analyzer:options', async (): Promise<AnalyzerOptions> => backend.getAnalyzerOptions());
  ipcMain.handle('analyzer:evaluate', async (_event, piece: unknown): Promise<AnalyzerEvaluation> => {
    if (!isAnalyzerPiece(piece)) throw new Error('Invalid analyzer piece.');
    return backend.evaluateAnalyzerPiece(piece);
  });
  ipcMain.handle('analyzer:scan:get', async (): Promise<AnalyzerScanSnapshot> => backend.getAnalyzerScan());
  ipcMain.handle('analyzer:scan:start', async (): Promise<AnalyzerScanSnapshot> => backend.startAnalyzerScan());
  ipcMain.handle(
    'analyzer:scan:cancel',
    async (_event, jobId: unknown): Promise<AnalyzerScanSnapshot> => {
      if (typeof jobId !== 'string' || !jobId) throw new Error('Invalid analyzer job id.');
      return backend.cancelAnalyzerScan(jobId);
    },
  );
  ipcMain.handle('analyzer:debug:get', async (): Promise<AnalyzerDebug> => backend.getAnalyzerDebug());

  ipcMain.handle('enhancement:options', async (): Promise<EnhancementOptions> => backend.getEnhancementOptions());
  ipcMain.handle('enhancement:job:get', async (): Promise<EnhancementSnapshot> => backend.getEnhancementJob());
  ipcMain.handle(
    'enhancement:job:start',
    async (_event, options: unknown): Promise<EnhancementSnapshot> => {
      if (!isEnhancementStartOptions(options)) throw new Error('Invalid enhancement options.');
      return backend.startEnhancementJob(options);
    },
  );
  ipcMain.handle(
    'enhancement:job:cancel',
    async (_event, jobId: unknown): Promise<EnhancementSnapshot> => {
      if (typeof jobId !== 'string' || !jobId) throw new Error('Invalid enhancement job id.');
      return backend.cancelEnhancementJob(jobId);
    },
  );
  ipcMain.handle('enhancement:debug:get', async (): Promise<EnhancementDebug> => backend.getEnhancementDebug());

  ipcMain.handle(
    'optimizer:inventory:get',
    async (): Promise<OptimizerInventorySnapshot> => backend.getOptimizerInventory(),
  );
  ipcMain.handle(
    'optimizer:inventory:import',
    async (): Promise<OptimizerInventorySelectionResult> => optimizerInventoryImport.run(),
  );
  ipcMain.handle(
    'optimizer:inventory:capture:start',
    async (): Promise<OptimizerInventoryCaptureState> => backend.startOptimizerInventoryCapture(),
  );
  ipcMain.handle(
    'optimizer:inventory:capture:finish',
    async (): Promise<OptimizerInventoryImportResult> => backend.finishOptimizerInventoryCapture(),
  );
  ipcMain.handle(
    'optimizer:inventory:reset',
    async (): Promise<OptimizerDataResetResult> => backend.resetOptimizerData(),
  );
  ipcMain.handle('optimizer:hero:search', async (_event, query: unknown, limit: unknown): Promise<OptimizerHeroSearchResult> => {
    if (typeof query !== 'string' || !Number.isInteger(limit) || Number(limit) < 1 || Number(limit) > 50) throw new Error('Invalid hero search.');
    return backend.searchOptimizerHeroes(query, Number(limit));
  });
  ipcMain.handle('optimizer:hero:details', async (_event, heroId: unknown): Promise<OptimizerHeroDetails> => {
    if (typeof heroId !== 'string' || !heroId.trim()) throw new Error('Invalid hero id.');
    return backend.getOptimizerHeroDetails(heroId);
  });
  ipcMain.handle('optimizer:artifact:search', async (_event, query: unknown, limit: unknown): Promise<OptimizerArtifactSearchResult> => {
    if (typeof query !== 'string' || !Number.isInteger(limit) || Number(limit) < 1 || Number(limit) > 50) throw new Error('Invalid artifact search.');
    return backend.searchOptimizerArtifacts(query, Number(limit));
  });
  ipcMain.handle('optimizer:profile:load', async (_event, heroId: unknown): Promise<OptimizerHeroDraftEnvelope> => {
    if (typeof heroId !== 'string' || !heroId.trim()) throw new Error('Invalid hero id.');
    return backend.loadOptimizerHeroDraft(heroId);
  });
  ipcMain.handle('optimizer:profile:save', async (_event, draft: unknown): Promise<OptimizerHeroDraftEnvelope> => {
    if (!isOptimizerHeroDraft(draft)) throw new Error('Invalid optimizer hero draft.');
    return backend.saveOptimizerHeroDraft(draft);
  });
  ipcMain.handle('optimizer:search:get', async (): Promise<OptimizerSearchSnapshot> => backend.getOptimizerSearch());
  ipcMain.handle('optimizer:search:start', async (_event, draft: unknown): Promise<OptimizerSearchSnapshot> => {
    if (!isOptimizerHeroDraft(draft)) throw new Error('Invalid optimizer hero draft.');
    return backend.startOptimizerSearch(draft);
  });
  ipcMain.handle('optimizer:search:cancel', async (_event, jobId: unknown): Promise<OptimizerSearchSnapshot> => {
    if (typeof jobId !== 'string' || !jobId.trim()) throw new Error('Invalid optimizer search job id.');
    return backend.cancelOptimizerSearch(jobId);
  });
  ipcMain.handle('optimizer:search:retry-cpu', async (_event, jobId: unknown): Promise<OptimizerSearchSnapshot> => {
    if (typeof jobId !== 'string' || !jobId.trim()) throw new Error('Invalid optimizer search job id.');
    return backend.retryOptimizerSearchWithCpu(jobId);
  });
  ipcMain.handle('optimizer:results:options', async (): Promise<OptimizerResultOptions> => backend.getOptimizerResultOptions());
  ipcMain.handle('optimizer:results:get', async (): Promise<OptimizerResultSnapshot> => backend.getOptimizerResults());
  ipcMain.handle('optimizer:results:query', async (_event, query: unknown): Promise<OptimizerResultSnapshot> => {
    if (!isOptimizerResultQuery(query)) throw new Error('Invalid optimizer result query.');
    return backend.queryOptimizerResults(query);
  });
  ipcMain.handle('optimizer:results:cancel', async (_event, queryId: unknown): Promise<OptimizerResultSnapshot> => {
    if (typeof queryId !== 'string' || !queryId.trim()) throw new Error('Invalid optimizer result query id.');
    return backend.cancelOptimizerResults(queryId);
  });
  ipcMain.handle('optimizer:results:detail', async (_event, request: unknown): Promise<OptimizerResultDetailSnapshot> => {
    if (!isOptimizerResultDetailRequest(request)) throw new Error('Invalid optimizer result detail selection.');
    return backend.selectOptimizerResultDetail(request);
  });
  ipcMain.handle('optimizer:results:equip', async (_event, request: unknown): Promise<OptimizerResultEquipResult> => {
    if (!isOptimizerResultDetailRequest(request)) throw new Error('Invalid optimizer build equip selection.');
    return backend.equipOptimizerResultBuild(request);
  });
  ipcMain.handle('optimizer:results:export:get', async (): Promise<OptimizerResultExportSnapshot> => (
    backend.getOptimizerResultExport()
  ));
  ipcMain.handle('optimizer:results:export:select', async (_event, request: unknown): Promise<OptimizerResultExportSelection> => {
    if (!isOptimizerResultExportRequest(request)) throw new Error('Invalid optimizer result export request.');
    const extension = request.format;
    const selection = mainWindow && !mainWindow.isDestroyed()
      ? await dialog.showSaveDialog(mainWindow, {
        title: 'Export optimizer results',
        defaultPath: path.join(app.getPath('documents'), `e7-optimizer-results.${extension}`),
        filters: [{ name: extension === 'csv' ? 'CSV table' : 'JSON data', extensions: [extension] }],
        properties: ['showOverwriteConfirmation', 'createDirectory'],
      })
      : await dialog.showSaveDialog({
        title: 'Export optimizer results',
        defaultPath: path.join(app.getPath('documents'), `e7-optimizer-results.${extension}`),
        filters: [{ name: extension === 'csv' ? 'CSV table' : 'JSON data', extensions: [extension] }],
        properties: ['showOverwriteConfirmation', 'createDirectory'],
      });
    if (selection.canceled || !selection.filePath) return { status: 'cancelled' };
    const destination = selection.filePath.toLowerCase().endsWith(`.${extension}`)
      ? selection.filePath
      : `${selection.filePath}.${extension}`;
    return {
      status: 'started',
      snapshot: await backend.startOptimizerResultExport(request.runId, request.queryId, request.format, destination),
    };
  });
  ipcMain.handle('optimizer:results:export:cancel', async (_event, exportId: unknown): Promise<OptimizerResultExportSnapshot> => {
    if (typeof exportId !== 'string' || !exportId.trim()) throw new Error('Invalid optimizer result export id.');
    return backend.cancelOptimizerResultExport(exportId);
  });
  ipcMain.handle('update:get', async (): Promise<UpdateSnapshot> => updates.get());
  ipcMain.handle('update:check', async (): Promise<UpdateSnapshot> => updates.check());
  ipcMain.handle('update:download', async (): Promise<UpdateSnapshot> => updates.download());
  ipcMain.handle(
    'update:install-on-quit',
    async (): Promise<UpdateSnapshot> => updates.installLater(),
  );
  ipcMain.handle(
    'update:apply',
    async (_event, request: unknown): Promise<UpdateApplyResult> => {
      if (!isUpdateApplyRequest(request)) throw new Error('Invalid update apply request.');
      return updates.apply(request);
    },
  );
  ipcMain.handle('update:open-release', async (): Promise<null> => {
    const url = updates.releaseUrl();
    if (!url) throw new Error('No validated Meowtoko E7 Tool release is available.');
    await shell.openExternal(url);
    return null;
  });

  backend.onHealthUpdated((snapshot) => {
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('health:updated', snapshot);
  });
  backend.onSettingsUpdated((snapshot) => {
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('settings:updated', snapshot);
  });
  backend.onAnalyzerUpdated((snapshot) => {
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('analyzer:updated', snapshot);
  });
  backend.onEnhancementUpdated((snapshot) => {
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('enhancement:updated', snapshot);
  });
  backend.onOptimizerSearchUpdated((snapshot) => {
    if (mainWindow && !mainWindow.isDestroyed() && isOptimizerSearchSnapshot(snapshot)) {
      mainWindow.webContents.send('optimizer:search:updated', snapshot);
    }
  });
  backend.onOptimizerResultsUpdated((snapshot) => {
    if (mainWindow && !mainWindow.isDestroyed() && isOptimizerResultSnapshot(snapshot)) {
      mainWindow.webContents.send('optimizer:results:updated', snapshot);
    }
  });
  backend.onOptimizerResultDetailUpdated((snapshot) => {
    if (mainWindow && !mainWindow.isDestroyed() && isOptimizerResultDetailSnapshot(snapshot)) {
      mainWindow.webContents.send('optimizer:results:detail-updated', snapshot);
    }
  });
  backend.onOptimizerResultExportUpdated((snapshot) => {
    if (mainWindow && !mainWindow.isDestroyed() && isOptimizerResultExportSnapshot(snapshot)) {
      mainWindow.webContents.send('optimizer:results:export:updated', snapshot);
    }
  });
  updates.onChanged((snapshot) => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('update:changed', snapshot);
    }
  });

  app.on('second-instance', () => {
    focusPrimaryWindow(mainWindow);
    if (process.env.E7_SINGLE_INSTANCE_SMOKE_TEST === '1') {
      if (singleInstanceTimeout) clearTimeout(singleInstanceTimeout);
      writeSingleInstanceSmokeMarker('primary-success.txt', 'backend=1 secondaryBackend=0');
      console.log('E7_SINGLE_INSTANCE_SMOKE_OK backend=1 secondaryBackend=0');
      void backend.stop().finally(() => app.exit(0));
    }
  });

  void app.whenReady().then(async () => {
    installCharacterArtworkProtocol();
    if (process.env.E7_DESKTOP_SMOKE_TEST === '1') {
      try {
        const details = await backend.start();
        const settings = await backend.getSettings();
        const analyzer = await backend.getAnalyzerOptions();
        const enhancement = await backend.getEnhancementOptions();
        const inventory = await backend.getOptimizerInventory();
        console.log(
          `E7_DESKTOP_SMOKE_OK protocol=${details.protocolVersion} backend=${details.backendVersion} settings=${settings.schemaVersion} analyzer=${analyzer.slots.length} enhancement=${enhancement.modes.length} inventory=${inventory.state}`,
        );
        await backend.stop();
        app.exit(0);
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        console.error(`E7_DESKTOP_SMOKE_FAILED ${message}`);
        app.exit(1);
      }
      return;
    }

    if (process.env.E7_SINGLE_INSTANCE_SMOKE_TEST === '1') {
      try {
        const details = await backend.start();
        writeSingleInstanceSmokeMarker('primary-ready.txt', `backend=${details.backendVersion}`);
        console.log(`E7_SINGLE_INSTANCE_READY backend=${details.backendVersion}`);
        singleInstanceTimeout = setTimeout(() => {
          console.error('E7_SINGLE_INSTANCE_SMOKE_FAILED second instance was not observed');
          void backend.stop().finally(() => app.exit(1));
        }, 30_000);
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        console.error(`E7_SINGLE_INSTANCE_SMOKE_FAILED ${message}`);
        app.exit(1);
      }
      return;
    }

    createWindow();
    updates.start();
    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
  });

  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit();
  });

  app.on('before-quit', (event) => {
    if (shutdownComplete) return;
    event.preventDefault();
    void backend.stop().finally(() => {
      shutdownComplete = true;
      if (updates.shouldInstallOnQuit()) {
        updates.applyAfterShutdown();
      } else {
        app.quit();
      }
    });
  });
  app.on('will-quit', () => updates.dispose());
}

if (app.isPackaged && !process.argv.some((argument) => argument.startsWith('--user-data-dir='))) {
  app.setPath('userData', path.join(app.getPath('appData'), 'E7 Hub'));
}

if (process.platform === 'win32') {
  retireLegacyShortcuts(app.getPath('appData'), app.getPath('desktop'));
}

const squirrelMaintenance = handleSquirrelLifecycle({ quit: () => app.quit() });
if (!squirrelMaintenance) {
  if (process.platform === 'win32') {
    app.setAppUserModelId(APP_USER_MODEL_ID);
  }
  // Electron's Windows process singleton stores its lock beneath userData. Ensure the
  // isolated profile exists before requesting it (fresh installs and smoke profiles do
  // not have this directory yet).
  mkdirSync(app.getPath('userData'), { recursive: true });
  if (!app.requestSingleInstanceLock()) {
    if (process.env.E7_DESKTOP_SMOKE_TEST === '1') {
      console.error('E7_DESKTOP_SMOKE_FAILED single-instance lock unavailable');
    }
    if (process.env.E7_SINGLE_INSTANCE_SMOKE_TEST === '1') {
      writeSingleInstanceSmokeMarker('secondary-exit.txt', 'backend=0');
      console.log('E7_SINGLE_INSTANCE_SECONDARY_EXIT backend=0');
    }
    app.exit(process.env.E7_DESKTOP_SMOKE_TEST === '1' ? 1 : 0);
  } else {
    runDesktopApplication();
  }
}
