const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { repositoryRoot, resolveForgeOutDir } = require('./output-paths.cjs');

const desktopRoot = path.resolve(__dirname, '..');
const development = process.argv.includes('--development');
const executable = development
  ? path.join(desktopRoot, 'node_modules', 'electron', 'dist', 'electron.exe')
  : path.join(resolveForgeOutDir(), 'Meowtoko E7 Tool-win32-x64', 'E7Hub.exe');
const executableArguments = development ? [desktopRoot] : [];
const liveSettings = path.join(repositoryRoot, '.local', 'user-data', 'settings.json');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'e7-single-instance-'));
const userData = path.join(temporary, 'user-data');
const markers = path.join(temporary, 'markers');
fs.mkdirSync(userData, { recursive: true });
const spawnErrors = new WeakMap();

function hash(file) {
  return fs.existsSync(file)
    ? crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')
    : null;
}

function waitForExit(child, timeoutMs) {
  return new Promise((resolve, reject) => {
    if (spawnErrors.has(child)) {
      reject(spawnErrors.get(child));
      return;
    }
    if (child.exitCode !== null) {
      resolve(child.exitCode);
      return;
    }
    const timeout = setTimeout(() => reject(new Error('Timed out waiting for process exit.')), timeoutMs);
    child.once('error', reject);
    child.once('exit', (code) => {
      clearTimeout(timeout);
      resolve(code);
    });
  });
}

async function waitForMarker(child, name, expected, timeoutMs) {
  const marker = path.join(markers, name);
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (spawnErrors.has(child)) throw spawnErrors.get(child);
    if (fs.existsSync(marker)) {
      const value = fs.readFileSync(marker, 'utf8');
      assert.equal(value, expected, `Unexpected ${name} marker.`);
      return value;
    }
    if (child.exitCode !== null) {
      throw new Error(`Process exited ${child.exitCode} before writing ${name}.`);
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`Timed out waiting for marker ${name}.`);
}

function launch(executablePath, args, options) {
  const child = spawn(executablePath, args, options);
  child.once('error', (error) => spawnErrors.set(child, error));
  return child;
}

async function main() {
  assert.ok(fs.statSync(executable).isFile(), `Packaged executable is missing: ${executable}`);
  const settingsBefore = hash(liveSettings);
  const environment = {
    ...process.env,
    E7_SINGLE_INSTANCE_SMOKE_TEST: '1',
    E7_SINGLE_INSTANCE_SMOKE_DIR: markers,
    E7_PROJECT_ROOT: repositoryRoot,
    E7_SETTINGS_PATH: liveSettings,
  };
  const spawnOptions = {
    cwd: temporary,
    env: environment,
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  };
  // Keep Windows' profile environment intact and isolate Electron/E7 storage
  // through Chromium's supported switch. Replacing the core profile variables
  // can prevent app.whenReady() from resolving on supported Windows hosts.
  const isolatedArguments = [...executableArguments, `--user-data-dir=${userData}`];
  const primary = launch(executable, isolatedArguments, spawnOptions);
  let secondary;
  try {
    await waitForMarker(primary, 'primary-ready.txt', 'backend=0.6.0', 45_000);
    secondary = launch(executable, isolatedArguments, spawnOptions);
    await waitForMarker(secondary, 'secondary-exit.txt', 'backend=0', 15_000);
    const secondaryCode = await waitForExit(secondary, 15_000);
    assert.equal(secondaryCode, 0);
    await waitForMarker(primary, 'primary-success.txt', 'backend=1 secondaryBackend=0', 15_000);
    const primaryCode = await waitForExit(primary, 15_000);
    assert.equal(primaryCode, 0);
    assert.equal(hash(liveSettings), settingsBefore, 'Single-instance smoke modified live settings.');
    console.log(`E7_SINGLE_INSTANCE_PROCESS_SMOKE_OK mode=${development ? 'development' : 'packaged'} backendProcesses=1`);
  } finally {
    const running = [secondary, primary].filter((child) => child?.pid && child.exitCode === null);
    for (const child of running) child.kill();
    await Promise.all(running.map((child) => waitForExit(child, 5_000).catch(() => undefined)));
    try {
      fs.rmSync(temporary, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
    } catch (cleanupError) {
      console.warn(`Could not remove isolated smoke directory ${temporary}: ${cleanupError}`);
    }
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
