const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const {
  desktopRoot,
  metadata,
  resolveInstallerRoot,
} = require('./output-paths.cjs');

const installerRoot = resolveInstallerRoot();
const setupName = `Meowtoko-E7-Tool-${metadata.version}-Setup.exe`;
const packageName = `E7Hub-${metadata.version}-full.nupkg`;
const setupPath = path.join(installerRoot, setupName);
const packagePath = path.join(installerRoot, packageName);
const releasesPath = path.join(installerRoot, 'RELEASES');

for (const required of [setupPath, packagePath, releasesPath]) {
  assert.ok(fs.statSync(required).isFile(), `Required installer artifact is missing: ${required}`);
}
assert.ok(
  fs.statSync(setupPath).size > fs.statSync(packagePath).size,
  'Setup.exe is only a bootstrap stub; the full package was not embedded.',
);

const artifactNames = fs.readdirSync(installerRoot);
assert.ok(!artifactNames.some((name) => /\.msi$/i.test(name)), 'Unexpected MSI artifact was created.');
assert.ok(!artifactNames.some((name) => /-delta\.nupkg$/i.test(name)), 'Unexpected delta package was created.');

const releases = fs.readFileSync(releasesPath, 'utf8').replace(/^\uFEFF/, '');
assert.match(releases, new RegExp(`^[A-F0-9]{40} ${packageName.replaceAll('.', '\\.') } \\d+$`, 'mi'));

const sevenZip = path.join(desktopRoot, 'node_modules', 'electron-winstaller', 'vendor', '7z.exe');
const nuspecResult = spawnSync(sevenZip, ['e', '-so', packagePath, '*.nuspec'], {
  encoding: 'utf8',
  windowsHide: true,
});
if (nuspecResult.error) throw nuspecResult.error;
assert.equal(nuspecResult.status, 0, `Could not inspect installer package:\n${nuspecResult.stderr}`);
const nuspec = nuspecResult.stdout;
assert.match(nuspec, /<id>E7Hub<\/id>/);
assert.match(nuspec, new RegExp(`<version>${metadata.version.replaceAll('.', '\\.') }<\\/version>`));
assert.match(nuspec, /<title>Meowtoko E7 Tool<\/title>/);
assert.match(nuspec, /<authors>Meowtoko E7 Tool contributors<\/authors>/);
assert.match(nuspec, /<iconUrl>https:\/\/raw\.githubusercontent\.com\/Motokochi\/Meowtoko-E7-Tool\/main\/assets\/app\/meowtoko-e7-tool\.ico<\/iconUrl>/);

const listResult = spawnSync(sevenZip, ['l', '-slt', packagePath], {
  encoding: 'utf8',
  windowsHide: true,
  maxBuffer: 32 * 1024 * 1024,
});
if (listResult.error) throw listResult.error;
assert.equal(listResult.status, 0, `Could not list installer package:\n${listResult.stderr}`);
const packageListing = listResult.stdout.replaceAll('\\', '/').toLowerCase();
for (const required of [
  'resources/backend/e7-core.exe',
  'resources/runtime/manifest.json',
  'resources/runtime/third_party_notices.md',
  'resources/cuda-installer/python.exe',
  'resources/cuda-installer/asset-manifest.json',
  'resources/characters/asset-manifest.json',
  'resources/characters/aube/pose.webp',
  'resources/characters/setsuka/pose.webp',
  "resources/characters/archdemon's shadow/pose.webp",
  'resources/backend/_internal/src/optimizer/data/character_data/character-catalog-v1.json',
  'resources/backend/_internal/src/optimizer/data/character_data/source/artifactdata.json',
]) {
  assert.match(packageListing, new RegExp(required.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
}
const characterWebpEntries = packageListing
  .split(/\r?\n/)
  .filter((line) => line.startsWith('path = ') && /\/resources\/characters\/.*\.webp$/.test(line));
assert.equal(characterWebpEntries.length, 1540, 'Installer character artwork count drifted.');
assert.doesNotMatch(packageListing, /\/resources\/characters\/.*\.png$/m);
for (const missing of [
  '/resources/characters/desert jewel basar/',
  '/resources/characters/mighty scout/',
  '/resources/characters/wild angara/',
]) {
  assert.doesNotMatch(packageListing, new RegExp(missing.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
}
for (const forbidden of [
  '/user_data/',
  '/debug_images/',
  '/benchmarks/',
  '/tests/',
  '/.pnpm-store/',
  '/run_e7_admin.bat',
  '/setup_e7_tool.ps1',
]) {
  assert.doesNotMatch(packageListing, new RegExp(forbidden.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
}

const iconPath = path.resolve(desktopRoot, '..', 'assets', 'app', 'meowtoko-e7-tool.ico');
const icon = fs.readFileSync(iconPath);
assert.equal(icon.readUInt16LE(0), 0, 'Invalid ICO reserved field.');
assert.equal(icon.readUInt16LE(2), 1, 'Invalid ICO image type.');
assert.ok(icon.readUInt16LE(4) >= 9, 'ICO does not contain the expected Windows size variants.');
assert.equal(
  crypto.createHash('sha256').update(icon).digest('hex'),
  '2331fedcc0c282cdcef31feac801a328e3892330a2b2dbb9b30a7f9d0b040e07',
  'Source application icon hash drifted.',
);

function sha256(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

console.log(
  `E7_INSTALLER_AUDIT_OK setup=${setupName} setupSha256=${sha256(setupPath)} `
  + `package=${packageName} packageSha256=${sha256(packagePath)}`,
);
