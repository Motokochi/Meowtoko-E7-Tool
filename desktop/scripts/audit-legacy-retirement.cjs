const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const repositoryRoot = path.resolve(__dirname, '..', '..');

const retiredPaths = [
  'main.py',
  'Run_E7_Admin.bat',
  'Setup_E7_Tool.ps1',
  'src/ui/__init__.py',
  'src/ui/app_window.py',
  'src/ui/components/debug_popup.py',
  'src/ui/views/enhancer_ui.py',
  'src/ui/views/rater_ui.py',
  'src/ui/views/settings_ui.py',
  'src/vision/capture.py',
  'src/vision/clicks.py',
  'e7_hub.py',
  'e7_hub.spec',
];

for (const relative of retiredPaths) {
  assert.ok(!fs.existsSync(path.join(repositoryRoot, relative)), `Retired legacy path still exists: ${relative}`);
}

const requirements = fs.readFileSync(path.join(repositoryRoot, 'requirements.txt'), 'utf8');
const coreRequirements = fs.readFileSync(path.join(repositoryRoot, 'requirements-core.txt'), 'utf8');
const buildRequirements = fs.readFileSync(path.join(repositoryRoot, 'requirements-build.txt'), 'utf8');
const cudaRequirements = fs.readFileSync(path.join(repositoryRoot, 'requirements-cuda.txt'), 'utf8');
const cudaComponentRequirements = fs.readFileSync(path.join(repositoryRoot, 'requirements-cuda-component.txt'), 'utf8');
const backendSpec = fs.readFileSync(path.join(repositoryRoot, 'packaging', 'e7-core.spec'), 'utf8');
for (const [name, content] of [['requirements.txt', requirements], ['packaging/e7-core.spec', backendSpec]]) {
  assert.doesNotMatch(content, /customtkinter|darkdetect/i, `Obsolete UI dependency remains in ${name}`);
}
assert.equal(requirements.replaceAll('\r\n', '\n'), '-r requirements-build.txt\n');
assert.match(buildRequirements, /^-r requirements-core\.txt\r?\n/);
assert.doesNotMatch(coreRequirements, /(?:cupy|nvidia|pyinstaller)/i, 'CPU/core requirements contain optional or build packages.');
assert.doesNotMatch(coreRequirements, /(?:mss|pygetwindow|pyrect)/i, 'Retired Windows capture dependencies remain in core requirements.');
assert.doesNotMatch(backendSpec, /(?:src\.vision\.(?:capture|clicks)|mss|pygetwindow|pyrect)/i, 'Retired Windows capture code remains in the frozen backend graph.');
assert.equal(
  cudaRequirements.replaceAll('\r\n', '\n'),
  '-r requirements-core.txt\n-r requirements-cuda-component.txt\n',
);
assert.equal(cudaComponentRequirements.trim(), 'cupy-cuda13x[ctk]==14.1.1');

function pythonSources(directory) {
  const result = [];
  for (const item of fs.readdirSync(directory, { withFileTypes: true })) {
    if (item.name === '__pycache__') continue;
    const fullPath = path.join(directory, item.name);
    if (item.isDirectory()) result.push(...pythonSources(fullPath));
    if (item.isFile() && item.name.endsWith('.py')) result.push(fullPath);
  }
  return result;
}

const supportedPythonSources = pythonSources(path.join(repositoryRoot, 'src'));
for (const source of supportedPythonSources) {
  const content = fs.readFileSync(source, 'utf8');
  const relative = path.relative(repositoryRoot, source).replaceAll('\\', '/');
  assert.doesNotMatch(content, /customtkinter/i, `Legacy UI dependency imported or referenced by ${relative}`);
  assert.doesNotMatch(content, /(?:from|import)\s+src\.ui\b/i, `Legacy UI module imported by ${relative}`);
  assert.doesNotMatch(content, /WindowAutomationBackend|capture_game_region|click_game_point/i, `Retired Windows capture path remains in ${relative}`);
}

const readme = fs.readFileSync(path.join(repositoryRoot, 'README.md'), 'utf8');
const installing = fs.readFileSync(path.join(repositoryRoot, 'docs', 'INSTALLING.md'), 'utf8');
const development = fs.readFileSync(path.join(repositoryRoot, 'docs', 'development', 'DESKTOP.md'), 'utf8');
assert.match(readme, /installed Meowtoko E7 Tool shortcut/i, 'README must identify the supported release entry point.');
assert.match(installing, /desktop or Start menu shortcut/i, 'Installer guide must identify icon launch.');
assert.match(development, /pnpm start/i, 'Developer guide must identify the Electron Forge entry point.');
for (const [name, content] of [['README.md', readme], ['docs/INSTALLING.md', installing], ['docs/development/DESKTOP.md', development]]) {
  assert.doesNotMatch(content, /Run_E7_Admin|Setup_E7_Tool|python\s+main\.py/i, `Unsupported launcher documented in ${name}`);
}

console.log(`E7_LEGACY_RETIREMENT_AUDIT_OK retired=${retiredPaths.length} pythonSources=${supportedPythonSources.length}`);
