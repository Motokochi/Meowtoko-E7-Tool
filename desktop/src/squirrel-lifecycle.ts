import { spawn } from 'node:child_process';
import { rmdirSync, rmSync } from 'node:fs';
import path from 'node:path';

export const APP_USER_MODEL_ID = 'com.squirrel.E7Hub.E7Hub';

const SQUIRREL_EVENTS = new Set([
  '--squirrel-install',
  '--squirrel-updated',
  '--squirrel-uninstall',
  '--squirrel-obsolete',
]);

interface DetachedProcess {
  once?(event: 'error', listener: () => void): void;
  unref(): void;
}

interface SpawnOptions {
  detached: true;
  stdio: 'ignore';
  windowsHide: true;
}

export interface SquirrelLifecycleOptions {
  argv?: readonly string[];
  execPath?: string;
  platform?: NodeJS.Platform;
  spawnDetached?: (command: string, args: readonly string[], options: SpawnOptions) => DetachedProcess;
  scheduleQuit?: (callback: () => void, delayMs: number) => unknown;
  quit?: () => void;
}

export function retireLegacyShortcuts(appDataPath: string, desktopPath: string): void {
  const legacyStartMenuDirectory = path.join(
    appDataPath,
    'Microsoft',
    'Windows',
    'Start Menu',
    'Programs',
    'E7 Hub contributors',
  );
  try {
    rmSync(path.join(legacyStartMenuDirectory, 'E7 Hub.lnk'), { force: true });
    rmSync(path.join(desktopPath, 'E7 Hub.lnk'), { force: true });
  } catch {
    // Shortcut retirement must never prevent the application from launching.
  }
  try {
    rmdirSync(legacyStartMenuDirectory);
  } catch {
    // Preserve the legacy folder if it contains anything besides the retired shortcut.
  }
}

/**
 * Handles Squirrel.Windows maintenance launches before any backend or UI work begins.
 * User data is intentionally untouched during uninstall.
 */
export function handleSquirrelLifecycle(options: SquirrelLifecycleOptions = {}): boolean {
  const argv = options.argv ?? process.argv;
  const execPath = options.execPath ?? process.execPath;
  const platform = options.platform ?? process.platform;
  const event = argv[1];

  if (platform !== 'win32' || !event || !SQUIRREL_EVENTS.has(event)) {
    return false;
  }

  const quit = options.quit ?? (() => undefined);
  const scheduleQuit = options.scheduleQuit ?? ((callback, delayMs) => setTimeout(callback, delayMs));

  if (event === '--squirrel-obsolete') {
    scheduleQuit(quit, 0);
    return true;
  }

  const updateExecutable = path.resolve(path.dirname(execPath), '..', 'Update.exe');
  const shortcutAction = event === '--squirrel-uninstall' ? '--removeShortcut' : '--createShortcut';
  const executableName = path.basename(execPath);
  const spawnDetached = options.spawnDetached ?? ((command, args, spawnOptions) => (
    spawn(command, [...args], spawnOptions)
  ));

  try {
    const child = spawnDetached(
      updateExecutable,
      [shortcutAction, executableName],
      { detached: true, stdio: 'ignore', windowsHide: true },
    );
    child.once?.('error', () => undefined);
    child.unref();
  } catch {
    // A damaged installation may be missing Update.exe. Never fall through into the
    // normal UI/backend during a maintenance launch, even if shortcut repair fails.
  } finally {
    // Give Update.exe a brief head start before Electron releases the maintenance process.
    scheduleQuit(quit, 1_000);
  }
  return true;
}
