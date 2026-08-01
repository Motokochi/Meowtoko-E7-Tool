const { spawnSync } = require('node:child_process');
const path = require('node:path');

const repositoryRoot = path.resolve(__dirname, '..', '..');
const configuredPython = process.env.E7_PYTHON;
const python = configuredPython || (process.platform === 'win32' ? 'py' : 'python3');
const launcherArgs = !configuredPython && process.platform === 'win32' ? ['-3.12'] : [];
const result = spawnSync(
  python,
  [...launcherArgs, path.join(repositoryRoot, 'scripts', 'build_cuda_installer.py')],
  {
    cwd: repositoryRoot,
    env: process.env,
    stdio: 'inherit',
    windowsHide: true,
  },
);

if (result.error) throw result.error;
if (result.status !== 0) process.exit(result.status ?? 1);
