const { spawnSync } = require('node:child_process');
const path = require('node:path');

const desktopRoot = path.resolve(__dirname, '..');
const repositoryRoot = path.resolve(desktopRoot, '..');
const configuredPython = process.env.E7_PYTHON;
const python = configuredPython || (process.platform === 'win32' ? 'py' : 'python3');
const pythonArgs = !configuredPython && process.platform === 'win32' ? ['-3.12'] : [];
const result = spawnSync(
  python,
  [
    ...pythonArgs,
    path.join(repositoryRoot, 'scripts', 'build_packaged_character_assets.py'),
    '--source',
    path.join(repositoryRoot, 'assets', 'characters'),
    '--output',
    path.join(repositoryRoot, 'dist', 'characters'),
  ],
  {
    cwd: repositoryRoot,
    encoding: 'utf8',
    stdio: 'inherit',
    windowsHide: true,
  },
);
if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
