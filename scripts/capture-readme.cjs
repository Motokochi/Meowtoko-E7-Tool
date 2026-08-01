const assert = require('node:assert/strict');
const childProcess = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const desktop = path.join(root, 'desktop');
const outputRoot = path.join(root, 'assets', 'readme');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'e7-readme-capture-'));
const backendData = path.join(temporary, 'backend');
const electronData = path.join(temporary, 'electron');
const forge = path.join(desktop, 'node_modules', '@electron-forge', 'cli', 'dist', 'electron-forge.js');
const routes = ['overview', 'analyzer', 'enhancer', 'optimizer'];

assert.ok(fs.existsSync(forge), 'Install the pinned desktop dependencies before capturing screenshots.');
fs.mkdirSync(backendData, { recursive: true });
fs.mkdirSync(electronData, { recursive: true });

try {
  const result = childProcess.spawnSync(
    process.execPath,
    [forge, 'start', '--', `--user-data-dir=${electronData}`],
    {
      cwd: desktop,
      encoding: 'utf8',
      env: {
        ...process.env,
        E7_PROJECT_ROOT: root,
        E7_PYTHON: process.env.E7_PYTHON || (process.platform === 'win32' ? 'python' : 'python3'),
        E7_README_CAPTURE: '1',
        E7_USER_DATA_DIR: backendData,
      },
      stdio: 'inherit',
      windowsHide: false,
    },
  );
  assert.equal(result.status, 0, 'The isolated Electron screenshot run failed.');
  for (const route of routes) {
    const image = path.join(outputRoot, `${route}.png`);
    assert.ok(fs.existsSync(image), `Screenshot was not created: ${image}`);
    const bytes = fs.readFileSync(image);
    assert.equal(bytes.subarray(1, 4).toString('ascii'), 'PNG', `${image} is not a PNG.`);
    assert.ok(bytes.length > 10_000 && bytes.length < 1_500_000, `${image} has an unexpected size.`);
  }
  console.log(`E7_README_SCREENSHOTS_OK images=${routes.length}`);
} finally {
  const relative = path.relative(os.tmpdir(), temporary);
  if (relative && !relative.startsWith('..') && !path.isAbsolute(relative)) {
    fs.rmSync(temporary, { recursive: true, force: true });
  }
}
