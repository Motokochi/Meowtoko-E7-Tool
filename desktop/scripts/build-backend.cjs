const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const repositoryRoot = path.resolve(__dirname, '..', '..');
const configuredPython = process.env.E7_PYTHON;
const python = configuredPython || (process.platform === 'win32' ? 'py' : 'python3');
const launcherArgs = !configuredPython && process.platform === 'win32' ? ['-3.12'] : [];
const spec = path.join(repositoryRoot, 'packaging', 'e7-core.spec');
const distPath = path.join(repositoryRoot, 'dist');
const workPath = path.join(repositoryRoot, '.build', 'pyinstaller', 'e7-core');

const result = spawnSync(
  python,
  [
    ...launcherArgs,
    '-m',
    'PyInstaller',
    '--clean',
    '--noconfirm',
    '--distpath', distPath,
    '--workpath', workPath,
    spec,
  ],
  {
    cwd: repositoryRoot,
    env: process.env,
    stdio: 'inherit',
    windowsHide: true,
  },
);

if (result.error) {
  throw result.error;
}
if (result.status !== 0) {
  process.exit(result.status ?? 1);
}

const executable = path.join(distPath, 'backend', 'e7-core.exe');
if (!fs.existsSync(executable)) {
  throw new Error(`PyInstaller completed without producing ${executable}`);
}

const metadataResult = spawnSync(
  python,
  [
    ...launcherArgs,
    path.join(repositoryRoot, 'scripts', 'export_runtime_metadata.py'),
    path.join(distPath, 'runtime'),
  ],
  {
    cwd: repositoryRoot,
    env: process.env,
    stdio: 'inherit',
    windowsHide: true,
  },
);
if (metadataResult.error) {
  throw metadataResult.error;
}
if (metadataResult.status !== 0) {
  process.exit(metadataResult.status ?? 1);
}
console.log(`E7_BACKEND_BUILD_OK ${executable}`);
