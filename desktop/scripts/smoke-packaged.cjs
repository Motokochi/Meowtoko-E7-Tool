const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { repositoryRoot, resolveForgeOutDir } = require('./output-paths.cjs');

const packageRoot = path.join(resolveForgeOutDir(), 'Meowtoko E7 Tool-win32-x64');
const executable = path.join(packageRoot, 'E7Hub.exe');
const packagedBackend = path.join(packageRoot, 'resources', 'backend', 'e7-core.exe');
const manifest = path.join(packageRoot, 'resources', 'runtime', 'manifest.json');
const liveSettings = path.join(repositoryRoot, '.local', 'user-data', 'settings.json');

function hash(file) {
  return fs.existsSync(file)
    ? crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')
    : null;
}

function backendProcessIds() {
  const result = spawnSync(
    path.join(process.env.SystemRoot || String.raw`C:\Windows`, 'System32', 'tasklist.exe'),
    ['/FI', 'IMAGENAME eq e7-core.exe', '/FO', 'CSV', '/NH'],
    { encoding: 'utf8', windowsHide: true },
  );
  assert.equal(result.status, 0, `Could not inspect backend processes:\n${result.stderr}`);
  return new Set(
    result.stdout
      .split(/\r?\n/)
      .map((line) => /^"e7-core\.exe","(\d+)"/i.exec(line)?.[1])
      .filter(Boolean),
  );
}

for (const required of [executable, packagedBackend, manifest]) {
  assert.ok(fs.existsSync(required), `Packaged smoke prerequisite is missing: ${required}`);
}

const before = {
  backend: hash(packagedBackend),
  manifest: hash(manifest),
  settings: hash(liveSettings),
};
const preexistingBackendIds = backendProcessIds();
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'e7-packaged-smoke-'));
const userData = path.join(temporary, 'user-data');
fs.mkdirSync(userData, { recursive: true });

try {
  const environment = {
    ...process.env,
    PATH: path.join(process.env.SystemRoot || String.raw`C:\Windows`, 'System32'),
    E7_DESKTOP_SMOKE_TEST: '1',
    E7_BACKEND_EXECUTABLE: path.join(temporary, 'missing-backend.exe'),
    E7_PROJECT_ROOT: path.join(temporary, 'missing-project'),
    E7_PYTHON: path.join(temporary, 'missing-python.exe'),
    E7_SETTINGS_PATH: liveSettings,
  };
  // Electron's supported user-data switch isolates both the desktop process and
  // the packaged backend without replacing Windows' core profile variables.
  // Replacing APPDATA/LOCALAPPDATA/USERPROFILE can stall Chromium initialization
  // before app.whenReady() on otherwise supported Windows installations.
  const result = spawnSync(executable, [`--user-data-dir=${userData}`], {
    cwd: temporary,
    env: environment,
    encoding: 'utf8',
    timeout: 45_000,
    windowsHide: true,
  });
  if (result.error) throw result.error;
  assert.equal(result.status, 0, `Packaged Electron failed:\n${result.stdout}\n${result.stderr}`);
  assert.match(
    result.stdout,
    /E7_DESKTOP_SMOKE_OK protocol=1 backend=0\.6\.0 settings=1 analyzer=6 enhancement=1 inventory=empty/,
  );
  const unexpectedInventoryDatabases = fs.readdirSync(temporary, { recursive: true })
    .filter((entry) => path.basename(String(entry)).toLowerCase() === 'optimizer.db');
  assert.deepEqual(unexpectedInventoryDatabases, [], 'Status-only packaged smoke created an inventory database.');

  const after = {
    backend: hash(packagedBackend),
    manifest: hash(manifest),
    settings: hash(liveSettings),
  };
  assert.deepEqual(after, before, 'Packaged smoke modified immutable resources or live settings.');

  const orphanedBackendIds = [...backendProcessIds()]
    .filter((processId) => !preexistingBackendIds.has(processId));
  assert.deepEqual(orphanedBackendIds, [], 'Packaged backend was left orphaned.');
  console.log(`E7_PACKAGED_SMOKE_OK isolated=${temporary}`);
} finally {
  fs.rmSync(temporary, { recursive: true, force: true });
}
