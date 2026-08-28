const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const asar = require('@electron/asar');
const yaml = require('js-yaml');
const { resolveForgeOutDir } = require('./output-paths.cjs');

const repositoryRoot = path.resolve(__dirname, '..', '..');
const packageRoot = path.resolve(
  process.argv[2] || path.join(resolveForgeOutDir(), 'Meowtoko E7 Tool-win32-x64'),
);
const resourcesRoot = path.join(packageRoot, 'resources');
const backendRoot = path.join(resourcesRoot, 'backend');
const runtimeRoot = path.join(resourcesRoot, 'runtime');
const cudaInstallerRoot = path.join(resourcesRoot, 'cuda-installer');
const characterArtworkRoot = path.join(resourcesRoot, 'characters');
const backendExecutable = path.join(backendRoot, 'e7-core.exe');
const manifestPath = path.join(runtimeRoot, 'manifest.json');
const cudaInstallerManifestPath = path.join(cudaInstallerRoot, 'asset-manifest.json');
const cudaInstallerExecutable = path.join(cudaInstallerRoot, 'python.exe');
const characterArtworkManifestPath = path.join(characterArtworkRoot, 'asset-manifest.json');
const noticesPath = path.join(runtimeRoot, 'THIRD_PARTY_NOTICES.md');
const asarPath = path.join(resourcesRoot, 'app.asar');
const windowIconPath = path.join(resourcesRoot, 'meowtoko-e7-tool.png');
const sourceWindowIconPath = path.join(repositoryRoot, 'assets', 'app', 'meowtoko-e7-tool.png');
const gearSlotAssetRoot = path.join(repositoryRoot, 'assets', 'equipment', 'slots');
const setAssetRoot = path.join(repositoryRoot, 'assets', 'equipment', 'sets');
const desktopExecutable = path.join(packageRoot, 'E7Hub.exe');

for (const required of [
  desktopExecutable,
  backendExecutable,
  manifestPath,
  noticesPath,
  cudaInstallerManifestPath,
  cudaInstallerExecutable,
  characterArtworkManifestPath,
  asarPath,
  windowIconPath,
]) {
  assert.ok(fs.statSync(required).isFile(), `Required packaged file is missing: ${required}`);
}

function sha256(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

function textSha256Candidates(file) {
  const content = fs.readFileSync(file, 'utf8').replace(/\r\n?/g, '\n');
  return new Set([
    crypto.createHash('sha256').update(content, 'utf8').digest('hex'),
    crypto.createHash('sha256').update(content.replace(/\n/g, '\r\n'), 'utf8').digest('hex'),
  ]);
}

function walk(directory) {
  const entries = [];
  for (const item of fs.readdirSync(directory, { withFileTypes: true })) {
    const fullPath = path.join(directory, item.name);
    entries.push(fullPath);
    if (item.isDirectory()) entries.push(...walk(fullPath));
  }
  return entries;
}

const resourceEntries = walk(resourcesRoot);
const resourceFiles = resourceEntries.filter((entry) => fs.statSync(entry).isFile());
const relativeResources = resourceEntries.map((entry) => path.relative(resourcesRoot, entry).replaceAll('\\', '/'));
const forbiddenSegments = new Set(['__pycache__', 'debug_images', 'tests', 'user_data', 'customtkinter']);
for (const relative of relativeResources) {
  const lower = relative.toLowerCase();
  const isCudaInstaller = lower.startsWith('cuda-installer/');
  const segments = relative.toLowerCase().split('/');
  assert.ok(!segments.some((segment) => forbiddenSegments.has(segment)), `Forbidden package path: ${relative}`);
  assert.ok(!/\.(?:pyc|pyo|map|ts|tsx)$/i.test(relative), `Forbidden package artifact: ${relative}`);
  assert.ok(
    isCudaInstaller || !/(?:app_window|debug_popup|enhancer_ui|rater_ui|settings_ui|main)\.py$/i.test(relative),
    `Legacy UI leaked: ${relative}`,
  );
  assert.ok(!/(?:run_e7_admin\.bat|setup_e7_tool\.ps1)$/i.test(relative), `Legacy launcher leaked: ${relative}`);
  assert.ok(!/\.(?:bat|ps1)$/i.test(relative), `Shell launcher leaked: ${relative}`);
  assert.ok(!/build-backend\.cjs$/i.test(relative), `Development build script leaked: ${relative}`);
  if (/\.py$/i.test(relative)) {
    assert.ok(
      (isCudaInstaller && /^cuda-installer\/lib\/site-packages\/pip(?:-|\/)/i.test(relative))
      || /^backend\/_internal\/cv2\//i.test(relative),
      `Application Python source leaked outside the pinned pip helper: ${relative}`,
    );
  }
  assert.ok(
    !/(?:^|\/)(?:cupy(?:x|\/|$|[-_.])|nvidia(?:\/|$|[-_.]))/i.test(relative),
    `Optional CUDA runtime leaked into CPU package: ${relative}`,
  );
}

const packagedExecutables = resourceFiles
  .filter((entry) => path.extname(entry).toLowerCase() === '.exe')
  .map((entry) => path.relative(resourcesRoot, entry).replaceAll('\\', '/').toLowerCase());
assert.deepEqual(packagedExecutables, [
  'backend/e7-core.exe',
  'cuda-installer/lib/site-packages/pip/_vendor/distlib/t64.exe',
  'cuda-installer/python.exe',
]);

const helperManifest = JSON.parse(fs.readFileSync(cudaInstallerManifestPath, 'utf8'));
assert.equal(helperManifest.schemaVersion, 1);
assert.equal(helperManifest.assetId, 'e7.cuda-installer');
assert.equal(helperManifest.layout, 'cuda-installer/python.exe');
assert.equal(helperManifest.architecture, 'x64');
assert.equal(helperManifest.python.version, '3.12.10');
assert.equal(helperManifest.python.abiTag, 'cp312');
assert.equal(helperManifest.python.license, 'PSF-2.0');
assert.equal(
  helperManifest.python.sourceSha256,
  '4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3',
);
assert.equal(helperManifest.installer.name, 'pip');
assert.equal(helperManifest.installer.version, '26.1.2');
assert.equal(helperManifest.installer.license, 'MIT');
assert.equal(
  helperManifest.installer.consoleLauncherResource,
  'Lib/site-packages/pip/_vendor/distlib/t64.exe',
);
assert.equal(
  helperManifest.installer.sourceSha256,
  '382ff9f685ee3bc25864f820aa50505825f10f5458ffff07e30a6d96e5715cab',
);
assert.equal(helperManifest.component.displayPackage, 'cupy-cuda13x[ctk]==14.1.1');
assert.equal(helperManifest.component.requirements, 'component-requirements.txt');
assert.equal(
  helperManifest.component.requirementsSha256,
  'c39d7b64e59aa31e7125a6efebf4112f8591e42f114f72269f90dec7b0544ed4',
);
assert.equal(helperManifest.component.dependencyResolution, 'disabled-with-pip-no-deps');
const componentLock = fs.readFileSync(path.join(repositoryRoot, 'requirements-cuda-component-lock.txt'), 'utf8');
assert.equal(sha256(path.join(cudaInstallerRoot, 'component-requirements.txt')), helperManifest.component.requirementsSha256);
assert.equal(fs.readFileSync(path.join(cudaInstallerRoot, 'component-requirements.txt'), 'utf8'), componentLock);
assert.deepEqual(
  helperManifest.component.packages.map(({ name, version }) => `${name}==${version}`),
  componentLock.trimEnd().split(/\r?\n/),
);
const helperFiles = resourceFiles
  .filter((entry) => entry.startsWith(`${cudaInstallerRoot}${path.sep}`) && entry !== cudaInstallerManifestPath);
const declaredHelperFiles = new Map(helperManifest.files.map((record) => [record.path, record]));
const actualHelperFiles = new Map(helperFiles.map((entry) => [
  path.relative(cudaInstallerRoot, entry).replaceAll('\\', '/'),
  entry,
]));
assert.deepEqual([...actualHelperFiles.keys()].sort(), [...declaredHelperFiles.keys()].sort());
for (const [relative, entry] of actualHelperFiles) {
  const declared = declaredHelperFiles.get(relative);
  assert.equal(fs.statSync(entry).size, declared.size, `CUDA helper size drift: ${relative}`);
  assert.equal(sha256(entry), declared.sha256, `CUDA helper hash drift: ${relative}`);
}
assert.ok(fs.statSync(path.join(cudaInstallerRoot, 'LICENSE.txt')).isFile(), 'CPython helper license missing.');
assert.ok(
  fs.statSync(path.join(cudaInstallerRoot, 'Lib', 'site-packages', 'pip-26.1.2.dist-info', 'licenses', 'LICENSE.txt')).isFile(),
  'pip helper license missing.',
);

const iconPng = fs.readFileSync(windowIconPath);
assert.equal(iconPng.subarray(1, 4).toString('ascii'), 'PNG', 'Packaged window icon is not a PNG.');
assert.equal(
  sha256(sourceWindowIconPath),
  '85af8c28a917dbc53fbe00deac3b53cfbcad8e26ae3bddfa3fc3e1b68e350608',
  'Source window icon hash drifted.',
);
assert.equal(
  sha256(windowIconPath),
  sha256(sourceWindowIconPath),
  'Packaged window icon drifted from assets/app/meowtoko-e7-tool.png.',
);

const repositoryArtworkManifestPath = path.join(
  repositoryRoot,
  'assets',
  'characters',
  'asset-manifest.json',
);
const sourceArtworkManifestPath = path.join(
  repositoryRoot,
  'assets',
  'characters',
  'raw-source-manifest.json',
);
const sourceArtworkManifest = JSON.parse(fs.readFileSync(sourceArtworkManifestPath, 'utf8'));
const repositoryArtworkManifest = JSON.parse(
  fs.readFileSync(repositoryArtworkManifestPath, 'utf8'),
);
const characterArtworkManifest = JSON.parse(fs.readFileSync(characterArtworkManifestPath, 'utf8'));
assert.equal(characterArtworkManifest.schemaId, 'e7hub.e7codex-character-assets');
assert.equal(characterArtworkManifest.schemaVersion, 1);
assert.equal(characterArtworkManifest.packaging.format, 'webp');
assert.equal(characterArtworkManifest.packaging.quality, 90);
assert.equal(characterArtworkManifest.packaging.method, 6);
assert.equal(characterArtworkManifest.packaging.poseMaxDimension, 1600);
assert.equal(characterArtworkManifest.packaging.sourceManifestSha256, sha256(sourceArtworkManifestPath));
assert.equal(
  sha256(characterArtworkManifestPath),
  sha256(repositoryArtworkManifestPath),
  'Packaged artwork manifest drifted from the repository-optimized source.',
);
assert.deepEqual(characterArtworkManifest, repositoryArtworkManifest);
assert.equal(characterArtworkManifest.summary.characters, 386);
assert.equal(characterArtworkManifest.summary.availableFiles, 1532);
assert.equal(characterArtworkManifest.summary.missingFiles, 12);
assert.equal(characterArtworkManifest.summary.errorFiles, 0);
assert.equal(
  characterArtworkManifest.summary.sourceTotalBytes,
  sourceArtworkManifest.summary.totalBytes,
);
assert.ok(
  characterArtworkManifest.summary.totalBytes < sourceArtworkManifest.summary.totalBytes / 2,
  'Packaged artwork was not compacted enough for a self-contained installer.',
);
const sourceArtwork = new Map();
for (const character of sourceArtworkManifest.characters) {
  for (const [variant, record] of Object.entries(character.files)) {
    sourceArtwork.set(`${character.name}\0${variant}`, record);
  }
}
const declaredArtwork = new Map();
const missingArtwork = [];
for (const character of characterArtworkManifest.characters) {
  for (const [variant, record] of Object.entries(character.files)) {
    if (record.status === 'available') {
      const sourceRecord = sourceArtwork.get(`${character.name}\0${variant}`);
      assert.deepEqual(record.sourceFile, {
        path: sourceRecord.path,
        bytes: sourceRecord.bytes,
        sha256: sourceRecord.sha256,
        width: sourceRecord.width,
        height: sourceRecord.height,
      });
      assert.equal(path.extname(record.path).toLowerCase(), '.webp');
      if (variant === 'pose') {
        assert.ok(
          Math.max(record.width, record.height) <= characterArtworkManifest.packaging.poseMaxDimension,
          `Packaged pose exceeds its maximum dimension: ${record.path}`,
        );
      }
      declaredArtwork.set(record.path, record);
    } else {
      missingArtwork.push(`${character.name}:${variant}`);
    }
  }
}
assert.equal(declaredArtwork.size, characterArtworkManifest.summary.availableFiles);
assert.equal(missingArtwork.length, characterArtworkManifest.summary.missingFiles);
const actualArtworkFiles = walk(characterArtworkRoot)
  .filter((entry) => fs.statSync(entry).isFile() && path.extname(entry).toLowerCase() === '.webp');
const actualArtwork = new Map(actualArtworkFiles.map((entry) => [
  path.relative(characterArtworkRoot, entry).replaceAll('\\', '/'),
  entry,
]));
assert.deepEqual([...actualArtwork.keys()].sort(), [...declaredArtwork.keys()].sort());
for (const [relative, entry] of actualArtwork) {
  const declared = declaredArtwork.get(relative);
  assert.equal(fs.statSync(entry).size, declared.bytes, `Character artwork size drift: ${relative}`);
  assert.equal(sha256(entry), declared.sha256, `Character artwork hash drift: ${relative}`);
  const header = fs.readFileSync(entry).subarray(0, 12);
  assert.equal(header.subarray(0, 4).toString('ascii'), 'RIFF', `Invalid WebP header: ${relative}`);
  assert.equal(header.subarray(8, 12).toString('ascii'), 'WEBP', `Invalid WebP header: ${relative}`);
}
assert.equal(
  walk(characterArtworkRoot)
    .filter((entry) => fs.statSync(entry).isFile() && path.extname(entry).toLowerCase() === '.png')
    .length,
  0,
  'Source PNG artwork leaked into the desktop package.',
);
const archdemon = characterArtworkManifest.characters.find(({ code }) => code === 'c5004');
assert.equal(archdemon.assetCode, 'm9194');
assert.equal(archdemon.files.pose.path, "Archdemon's Shadow/pose.webp");

const asarEntries = asar.listPackage(asarPath).map((entry) => entry.replaceAll('\\', '/').toLowerCase());
for (const entry of asarEntries) {
  assert.ok(!/\.(?:map|ts|tsx|py|pyc|pyo)$/.test(entry), `Source artifact leaked into app.asar: ${entry}`);
  assert.ok(!/(?:user_data|debug_images|tests|scripts|customtkinter)(?:\/|$)/.test(entry), `Forbidden app.asar path: ${entry}`);
  assert.ok(!/(?:run_e7_admin\.bat|setup_e7_tool\.ps1|main\.py)$/.test(entry), `Legacy launcher leaked into app.asar: ${entry}`);
  assert.ok(!/\/assets\/fribbels\//.test(entry), `Retired asset layout leaked into app.asar: ${entry}`);
}
assert.match(
  fs.readFileSync(path.join(gearSlotAssetRoot, 'SOURCE.md'), 'utf8'),
  /b291cbbc415f11abede146859edc7b67d26e9c4b/,
);
assert.match(
  fs.readFileSync(path.join(setAssetRoot, 'SOURCE.md'), 'utf8'),
  /b291cbbc415f11abede146859edc7b67d26e9c4b/,
);
const expectedGearSlotIcons = new Map([
  ['geararmor.png', '1949c8b7426590634fe38b8cfd412ddd86bd5e2f9cc0fe0b5602e5d2c3e93911'],
  ['gearboots.png', '73e144889877dfbb462a385c4c251faeb973d111c8eee1a87bb8af0037ce7168'],
  ['gearhelmet.png', '740fff8b6bb0f3cc12a9a6f1813e4a9f69f6e674e0acdb570df93dae272eac9f'],
  ['gearnecklace.png', '1849fe9d536ee8fda61ed8708ed556dc69530dc9968399aabae13014fb597d5a'],
  ['gearring.png', '91d3e48e1537ffd8ca61883ca4c9a1f464ff40872c40f41cb784d43c6d18fbf9'],
  ['gearweapon.png', '455f4ab5e75251caafc232f67e114411238023fd6e520b5065352b4c232731ca'],
]);
for (const [filename, expectedHash] of expectedGearSlotIcons) {
  assert.equal(
    sha256(path.join(gearSlotAssetRoot, filename)),
    expectedHash,
    `Fribbels source gear-slot icon hash drifted: ${filename}`,
  );
  const packagedPath = path.join(
    '.webpack', 'renderer', 'assets', 'equipment', 'slots', filename,
  );
  assert.equal(
    asarEntries.filter((entry) => entry.endsWith(`/${packagedPath.replaceAll('\\', '/').toLowerCase()}`)).length,
    1,
    `Fribbels gear-slot icon was not packaged exactly once: ${filename}`,
  );
  const contents = asar.extractFile(asarPath, packagedPath);
  assert.equal(
    crypto.createHash('sha256').update(contents).digest('hex'),
    expectedHash,
    `Fribbels gear-slot icon hash drifted: ${filename}`,
  );
}
const expectedSetIcons = new Map([
  ['setattack.png', 'f1f4804c490338e31a69649ffc00d23aa40e47984e1460bb60dd275eb06d5154'],
  ['setcounter.png', 'a5db4519415d8c4d74fa0aa06f3ffb477e7fa82e2589658a0b97c6c98fe5d480'],
  ['setcritical.png', '003e3e8b64a9fda8e1d8b0a5e4a49a8bdeadcc83dc6c4e80f465cb896a28bfec'],
  ['setdefense.png', 'dc7934f0ddc46fdbc5f64d02621c7e1040b07bbce95e8246cc93d001e2fb9451'],
  ['setdestruction.png', '10cd7589d158ac8c5a0eb0a9d1b9669773b7bf2835799c3614b3636782575626'],
  ['setfervor.png', 'f918daddfec1923650f3be5bfd5079a2cf20bc723c3b69e2f903dac07e5b6dac'],
  ['sethealth.png', 'bd66bf8398a88496c1da49f2fb19ba8aa1eaed1d5f934424cfa7667966b8d460'],
  ['sethit.png', '62e4e0db143b537eb76f89784469ca1794e3af662080c501a28a629f15ca033d'],
  ['setimmunity.png', 'e6d24f7eb4304e370f8da180e3203495858d3b8c5ec805b6f9f156a2aadb0717'],
  ['setinjury.png', '865b4e0162d319a8e4021570a055a73387385a796171f47805e5eab8f92d77a3'],
  ['setlifesteal.png', '2f92a11314d2abbb687fa675b5787a7eb7ebba40c81aacd761c83daaf51acb4b'],
  ['setpenetration.png', '8ea7266314cc69d3b1dababdb55b2a72ab911a775b9337852d1e732946a09f05'],
  ['setprotection.png', '812ae832e2e5e8c9bb45b518ab83799468631f7063a89b554653bb605cef7d21'],
  ['setpursuit.png', '1a6972b56ca4d9332e4953a72ad18c649550a8076e960b9d99373adf74508835'],
  ['setrage.png', '263b01925ed73a7b2a644d33aff13af2db46d6c1721cf1ddb2ec443d9722717c'],
  ['setresist.png', 'af10443b018972f014ebb5fcbf3dea241d09643e5cbaf94650d9e38a7087baa6'],
  ['setrevenge.png', '5b254556682db3582af02a6091d1766fbe73318bab6d326a94d89caf5463ec08'],
  ['setreversal.png', '34b0c2c9d46d14613dd9dbb72538976f96ce2dc90e9235a6e2d8a621a1d4d51c'],
  ['setriposte.png', '56163cce905fcebacd4b8445ffb19eea40737e3c000677d1dee87c1ce586ecd3'],
  ['setspeed.png', 'b8f6d40e73a9005b923be5132f243780db0fecdaa29a83e6c9dc11a103286677'],
  ['settorrent.png', '3414d48a37871faba5c22d7ac44e07c03950914ffe3bccb0be1724cdb82fa3f8'],
  ['setunity.png', 'b800c49afe73a19d243f805d25a4d62ba81e6944180616fd747587872d890090'],
  ['setwarfare.png', '71f68b966f9962646de3436f988ea4de6d623c0f8f75dc84bb6dcd4f2a820e00'],
  ['setweakening.png', '4f7ef6fc685dc2d677990c5c6f5c9a3a8dc2fd3f061287d9a74d8d83ebc48bd7'],
]);
for (const [filename, expectedHash] of expectedSetIcons) {
  assert.equal(
    sha256(path.join(setAssetRoot, filename)),
    expectedHash,
    `Fribbels source set icon hash drifted: ${filename}`,
  );
  const packagedPath = path.join(
    '.webpack', 'renderer', 'assets', 'equipment', 'sets', filename,
  );
  assert.equal(
    asarEntries.filter((entry) => entry.endsWith(`/${packagedPath.replaceAll('\\', '/').toLowerCase()}`)).length,
    1,
    `Fribbels set icon was not packaged exactly once: ${filename}`,
  );
  const contents = asar.extractFile(asarPath, packagedPath);
  assert.equal(
    crypto.createHash('sha256').update(contents).digest('hex'),
    expectedHash,
    `Fribbels set icon hash drifted: ${filename}`,
  );
}

const packagedMain = asar.extractFile(
  asarPath,
  path.join('.webpack', 'main', 'index.js'),
).toString('utf8');
const packagedRenderer = asar.extractFile(
  asarPath,
  path.join('.webpack', 'renderer', 'main_window', 'index.js'),
).toString('utf8');
const packagedPreload = asar.extractFile(
  asarPath,
  path.join('.webpack', 'renderer', 'main_window', 'preload.js'),
).toString('utf8');
for (const channel of [
  'optimizer:inventory:reset',
  'optimizer:search:get',
  'optimizer:search:start',
  'optimizer:search:cancel',
  'optimizer:search:retry-cpu',
  'optimizer:search:updated',
  'optimizer:results:options',
  'optimizer:results:get',
  'optimizer:results:query',
  'optimizer:results:cancel',
  'optimizer:results:updated',
  'optimizer:results:detail',
  'optimizer:results:detail-updated',
  'optimizer:results:equip',
  'optimizer:results:export:get',
  'optimizer:results:export:select',
  'optimizer:results:export:cancel',
  'optimizer:results:export:updated',
]) {
  assert.match(`${packagedMain}\n${packagedPreload}`, new RegExp(channel.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
}
assert.match(packagedRenderer, /More than 5,000,000 exact completed-set builds matched/);
assert.match(packagedRenderer, /Clear secondary filters/);
assert.match(packagedRenderer, /Open build/);
assert.match(packagedRenderer, /No completed sets/);
assert.match(packagedRenderer, /Retry with CPU/);
assert.match(packagedRenderer, /Install GPU components/);
assert.match(packagedRenderer, /No CUDA Toolkit/);
assert.match(packagedRenderer, /nvcc/);
assert.match(packagedRenderer, /Compare matching builds/);
assert.match(packagedRenderer, /Secondary stat filters/);
assert.match(packagedRenderer, /Recommended gear/);
assert.match(packagedRenderer, /Equip these six pieces/);
assert.match(packagedRenderer, /Close cards/);
assert.match(packagedRenderer, /Equip this build locally/);
assert.match(packagedRenderer, /This does not tap or change/);
assert.doesNotMatch(packagedRenderer, /priorityScore\.toFixed\(3\)/);
assert.match(packagedRenderer, /assets\/equipment\/slots\/gearweapon\.png/);
assert.match(packagedRenderer, /equippedHeroName/);
assert.match(packagedRenderer, /["']face_s["']/);
assert.doesNotMatch(packagedRenderer, /optimizer-result-tabs/);
assert.match(packagedRenderer, /e7-character/);
assert.match(packagedMain, /E7_CHARACTER_ARTWORK_UNAVAILABLE/);
assert.match(packagedMain, /asset-manifest\.json/);
assert.doesNotMatch(packagedRenderer, /Future pieces are not owned gear/);
assert.doesNotMatch(packagedRenderer, /Maximum future replacements/);
assert.doesNotMatch(packagedRenderer, /Near-build normalized-distance tolerance/);
assert.doesNotMatch(packagedRenderer, /\b1-away\b|\b2-away\b|\bone-away\b|\btwo-away\b/i);
assert.match(packagedRenderer, /Exact enhancement stats come from game packets; every tap uses ADB with a stop check first/);
assert.match(packagedRenderer, /Every preview is captured from the configured ADB device/);
assert.match(packagedRenderer, /Erase all Optimizer data/);
assert.match(packagedRenderer, /GAME INVENTORY/);
assert.match(packagedRenderer, /gear\.txt/);
assert.doesNotMatch(packagedRenderer, /Derived metric ranges/);
assert.match(fs.readFileSync(noticesPath, 'utf8'), /six Optimizer equipment-slot icons[\s\S]*b291cbbc415f11abede146859edc7b67d26e9c4b/);
assert.doesNotMatch(`${packagedMain}\n${packagedPreload}`, /settings:windows/);
assert.doesNotMatch(`${packagedMain}\n${packagedPreload}`, /optimizer:search:(?:invoke|rows|items|raw)/);
assert.doesNotMatch(`${packagedMain}\n${packagedPreload}`, /optimizer:results:(?:raw|all|memmap|path|ordinals)/);
assert.doesNotMatch(packagedRenderer, /(?:dense_item_ids|private-result-item|optimizer:search:rows)/);
for (const actionId of ['cuda.install', 'cuda.repair', 'health.cancel']) {
  assert.match(`${packagedMain}\n${packagedPreload}`, new RegExp(actionId.replace('.', '\\.')));
}

const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
assert.equal(manifest.schemaVersion, 2);
assert.equal(manifest.backend.version, '0.6.0');
assert.equal(manifest.backend.protocolVersion, 1);
assert.equal(manifest.backend.layout, 'backend/e7-core.exe');
assert.deepEqual(manifest.bundledExecutables.map(({ id }) => id), ['e7.cuda-installer']);
assert.equal(manifest.bundledExecutables[0].manifest, 'cuda-installer/asset-manifest.json');
assert.deepEqual(manifest.bundledExecutables[0].python, helperManifest.python);
assert.deepEqual(manifest.bundledExecutables[0].installer, helperManifest.installer);
assert.deepEqual(manifest.bundledExecutables[0].component, helperManifest.component);
assert.deepEqual(manifest.externalCapabilities.map(({ id }) => id), ['ollama', 'tesseract', 'adb']);
assert.deepEqual(manifest.optionalComponents.map(({ id }) => id), ['cuda']);
assert.equal(manifest.optionalComponents[0].package, 'cupy-cuda13x[ctk]==14.1.1');
assert.deepEqual(manifest.optionalComponents[0].resolvedGraph, helperManifest.component);
assert.match(manifest.externalCapabilities.find(({ id }) => id === 'tesseract').reason, /no official modern Windows installer/i);
assert.match(manifest.runtime.python.version, /^3\.12\./);
assert.equal(manifest.runtime.python.abiTag, 'cp312');
assert.equal(manifest.runtime.python.architecture.toLowerCase(), 'amd64');
assert.equal(manifest.runtime.buildTool.name, 'pyinstaller');
assert.ok(manifest.runtime.dependencies.length >= 10, 'Frozen dependency inventory is incomplete.');
assert.ok(
  !manifest.runtime.dependencies.some(({ name }) => /^(?:cupy|nvidia|mss|pygetwindow|pyrect)(?:-|$)/i.test(name)),
  'Optional CUDA or retired Windows capture runtime leaked into CPU metadata.',
);
const corePins = new Map(
  fs.readFileSync(path.join(repositoryRoot, 'requirements-core.txt'), 'utf8')
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => {
      const [name, version] = line.split('==');
      assert.ok(name && version, `Invalid core dependency pin: ${line}`);
      return [name.toLowerCase(), version];
    }),
);
const packagedPins = new Map(
  manifest.runtime.dependencies.map(({ name, version }) => [name.toLowerCase(), version]),
);
assert.deepEqual(packagedPins, corePins, 'Frozen runtime dependencies drifted from requirements-core.txt.');
const buildPins = new Map(
  fs.readFileSync(path.join(repositoryRoot, 'requirements-build.txt'), 'utf8')
    .split(/\r?\n/)
    .filter((line) => line && !line.startsWith('-r '))
    .map((line) => line.split('==').map((value) => value.toLowerCase())),
);
const packagedBuildPins = new Map(
  manifest.runtime.buildDependencies.map(({ name, version }) => [name.toLowerCase(), version.toLowerCase()]),
);
assert.deepEqual(packagedBuildPins, buildPins, 'Packaging toolchain drifted from requirements-build.txt.');
assert.ok(fs.statSync(path.join(runtimeRoot, manifest.runtime.licensesPath, 'Python', 'LICENSE.txt')).isFile());

assert.equal(manifest.desktopBuild.platform, 'win32');
assert.equal(manifest.desktopBuild.architecture, 'x64');
const packageMetadata = JSON.parse(fs.readFileSync(path.join(repositoryRoot, 'desktop', 'package.json'), 'utf8'));
assert.equal(manifest.desktopBuild.packageManager, packageMetadata.packageManager);
assert.equal(manifest.desktopBuild.electronVersion, packageMetadata.devDependencies.electron);
assert.equal(manifest.desktopBuild.forgeVersion, packageMetadata.devDependencies['@electron-forge/cli']);
const lockPath = path.join(repositoryRoot, 'desktop', 'pnpm-lock.yaml');
assert.ok(
  textSha256Candidates(lockPath).has(manifest.desktopBuild.lockSha256),
  'Packaged dependency-lock hash does not match the source lockfile.',
);
const lock = yaml.load(fs.readFileSync(lockPath, 'utf8'));
assert.equal(String(lock.lockfileVersion), '9.0');
const importer = lock.importers['.'];
for (const [name, version] of Object.entries({
  ...packageMetadata.dependencies,
  ...packageMetadata.devDependencies,
})) {
  const locked = importer.dependencies?.[name] || importer.devDependencies?.[name];
  assert.ok(locked, `pnpm lock is missing direct dependency ${name}`);
  assert.equal(String(locked.specifier), version, `pnpm lock specifier drift for ${name}`);
}

assert.equal(manifest.bundledData.length, 1);
const bundledData = manifest.bundledData[0];
assert.equal(bundledData.id, 'e7.optimizer.character-artifact-snapshot');
assert.equal(bundledData.classification, 'immutable-bundled-data');
const expectedDataHashes = new Map([
  ['character-catalog-v1.json', 'bfeac97701bad6147665ef905168eedfe5e218d1941eaaf5d2b47edf50ccd4a6'],
  ['character-source-v1.json', 'b41a7b8ab2805f1be42d15f53deac777deb83ee732bf9d56010ef2072846a7aa'],
  ['character-validation-v1.json', '048e99aa2d99fdf02505edcd9c6fd247aa0c5825fd1abc07c913dfa614c15ebe'],
  ['manual-heroes-v1.json', '17e3221824c703b439930a9587f5b33122364f6f58e97899c6612aec7a3dcd2b'],
  ['manifest-v1.json', '5dd39b4fae32380bb3c5345a8590b7e5ac7b78abd6e5272b2bde6211624947ae'],
  ['source/artifactdata.json', 'ed1bb666ae7465560fbc1a163000966821174b0a48be826b28da16021f463ac0'],
  ['source/herodata.json', 'a5ed0b641e578a2b290b75d6f75a866a93b91e40c1064a4f1a264630a745c349'],
]);
assert.equal(bundledData.files.length, expectedDataHashes.size);
for (const record of bundledData.files) {
  const relative = record.path.slice(`${bundledData.layout}/`.length);
  assert.equal(record.sha256, expectedDataHashes.get(relative), `Bundled data source hash drift: ${relative}`);
  const packagedPath = path.join(resourcesRoot, ...record.path.split('/'));
  assert.ok(fs.statSync(packagedPath).isFile(), `Bundled data file is missing: ${record.path}`);
  assert.equal(fs.statSync(packagedPath).size, record.size, `Bundled data size drift: ${record.path}`);
  assert.equal(sha256(packagedPath), record.sha256, `Bundled data hash drift: ${record.path}`);
  assert.equal(
    relativeResources.filter((entry) => entry.endsWith(`/character_data/${relative}`)).length,
    1,
    `Bundled data was not included exactly once: ${relative}`,
  );
}

const configuredPython = process.env.E7_PYTHON;
const archivePython = configuredPython || (process.platform === 'win32' ? 'py' : 'python3');
const archiveArgs = !configuredPython && process.platform === 'win32' ? ['-3.12'] : [];
const archiveResult = spawnSync(
  archivePython,
  [...archiveArgs, '-m', 'PyInstaller.utils.cliutils.archive_viewer', '-r', '-l', backendExecutable],
  { cwd: repositoryRoot, encoding: 'utf8', windowsHide: true },
);
if (archiveResult.error) throw archiveResult.error;
assert.equal(archiveResult.status, 0, `Could not recursively inspect frozen backend:\n${archiveResult.stderr}`);
const archiveListing = archiveResult.stdout.toLowerCase();
assert.equal(
  archiveListing.split("'graphlib'").length - 1,
  1,
  'Frozen backend is missing the graphlib stdlib dependency required by cuda-pathfinder.',
);
assert.equal(
  archiveListing.split("'ctypes.wintypes'").length - 1,
  1,
  'Frozen backend is missing the ctypes.wintypes stdlib dependency required by cuda-pathfinder.',
);
for (const authority of manifest.migrationAuthorities) {
  const occurrences = archiveListing.split(`'${authority.module.toLowerCase()}'`).length - 1;
  assert.equal(occurrences, 1, `Frozen migration authority count drift: ${authority.module}=${occurrences}`);
}
for (const forbidden of ["'cupy'", "'cupyx'", "'nvidia'", "'src.vision.capture'", "'src.vision.clicks'"]) {
  assert.equal(archiveListing.split(forbidden).length - 1, 0, `Optional CUDA archive entry leaked: ${forbidden}`);
}

const size = resourceFiles.reduce((total, entry) => total + fs.statSync(entry).size, 0);
console.log(
  `E7_PACKAGE_AUDIT_OK files=${resourceFiles.length} asar=${asarEntries.length} `
  + `executables=${packagedExecutables.length} helperFiles=${helperManifest.files.length} `
  + `artworkFiles=${actualArtwork.size} `
  + `dataFiles=${bundledData.files.length} migrations=${manifest.migrationAuthorities.length} `
  + `optimizerChannels=18 bytes=${size}`,
);
