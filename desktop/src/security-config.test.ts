import assert from 'node:assert/strict';
import { existsSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';

test('development renderer remains compatible with the no-eval content security policy', () => {
  const rendererConfig = readFileSync(path.resolve('webpack.renderer.config.ts'), 'utf8');
  const indexHtml = readFileSync(path.resolve('src/index.html'), 'utf8');

  assert.match(rendererConfig, /devtool:\s*['"]source-map['"]/);
  assert.doesNotMatch(indexHtml, /unsafe-eval/);
  assert.match(indexHtml, /script-src 'self'/);
  assert.match(indexHtml, /img-src 'self' data: e7-character:/);
  assert.doesNotMatch(indexHtml, /raw\.githubusercontent\.com/);
});

test('permanent desktop visuals resolve from the repository asset root', () => {
  const forgeConfig = readFileSync(path.resolve('forge.config.ts'), 'utf8');
  const webpackRules = readFileSync(path.resolve('webpack.rules.ts'), 'utf8');
  const repositoryAssets = path.resolve('..', 'assets');

  for (const relative of [
    'app/meowtoko-e7-tool.ico',
    'app/meowtoko-e7-tool.png',
    'equipment/slots/gearweapon.png',
    'equipment/sets/setspeed.png',
    'characters/asset-manifest.json',
    'characters/raw-source-manifest.json',
    'characters/index.csv',
    'characters/README.md',
  ]) {
    assert.ok(
      statSync(path.join(repositoryAssets, ...relative.split('/'))).isFile(),
      `Repository asset is missing: ${relative}`,
    );
  }
  assert.equal(existsSync(path.resolve('assets')), false);
  assert.match(forgeConfig, /path\.resolve\(__dirname, '\.\.', 'assets', 'app'\)/);
  assert.match(forgeConfig, /main\/assets\/app\/meowtoko-e7-tool\.ico/);
  assert.doesNotMatch(forgeConfig, /main\/desktop\/assets/);
  assert.match(webpackRules, /assets\[\\\\\/\]equipment/);
  assert.doesNotMatch(webpackRules, /assets\/fribbels/);
});

test('visible rebrand preserves the installed app and data identities', () => {
  const packageConfig = readFileSync(path.resolve('package.json'), 'utf8');
  const forgeConfig = readFileSync(path.resolve('forge.config.ts'), 'utf8');
  const main = readFileSync(path.resolve('src/main.ts'), 'utf8');
  const updates = readFileSync(path.resolve('src/update-service.ts'), 'utf8');

  assert.match(packageConfig, /"productName": "Meowtoko E7 Tool"/);
  assert.match(forgeConfig, /name: 'E7Hub'/);
  assert.match(forgeConfig, /exe: 'E7Hub\.exe'/);
  assert.match(forgeConfig, /setupExe: `Meowtoko-E7-Tool-/);
  assert.match(main, /app\.isPackaged/);
  assert.match(main, /startsWith\('--user-data-dir='\)/);
  assert.match(main, /app\.setPath\('userData', path\.join\(app\.getPath\('appData'\), 'E7 Hub'\)\)/);
  assert.match(updates, /Motokochi\/Meowtoko-E7-Tool\/releases\/latest/);
});

test('generated desktop output uses the repository semantic roots', () => {
  const forgeConfig = readFileSync(path.resolve('forge.config.ts'), 'utf8');
  const testsConfig = readFileSync(path.resolve('tsconfig.tests.json'), 'utf8');
  const backendBuild = readFileSync(path.resolve('scripts/build-backend.cjs'), 'utf8');
  const outputPaths = readFileSync(path.resolve('scripts/output-paths.cjs'), 'utf8');
  const releasePublisher = readFileSync(
    path.resolve('scripts/publish-local-release.cjs'),
    'utf8',
  );

  assert.match(forgeConfig, /'\.build', 'forge'/);
  assert.match(testsConfig, /\.\.\/\.build\/desktop-tests/);
  assert.match(backendBuild, /'\.build', 'pyinstaller', 'e7-core'/);
  assert.match(outputPaths, /'releases', `v\$\{metadata\.version\}`/);
  assert.match(releasePublisher, /Local release destination already exists and will not be overwritten/);
  assert.doesNotMatch(forgeConfig, /\|\| 'out'/);
  assert.doesNotMatch(testsConfig, /\.test-dist/);
});

test('cleanup commands are separate, dry-run by default, and protect local data', () => {
  const packageConfig = readFileSync(path.resolve('package.json'), 'utf8');
  const cleanupContract = readFileSync(
    path.resolve('..', 'scripts', 'cleanup-contract.cjs'),
    'utf8',
  );
  const buildCleanup = readFileSync(
    path.resolve('..', 'scripts', 'cleanup-build-output.cjs'),
    'utf8',
  );
  const releaseCleanup = readFileSync(
    path.resolve('..', 'scripts', 'cleanup-release-archives.cjs'),
    'utf8',
  );

  assert.match(packageConfig, /"cleanup:build": "node \.\.\/scripts\/cleanup-build-output\.cjs"/);
  assert.match(packageConfig, /"cleanup:releases": "node \.\.\/scripts\/cleanup-release-archives\.cjs"/);
  assert.match(cleanupContract, /dryRun: !argumentsList\.includes\('--apply'\)/);
  assert.match(cleanupContract, /installed application data/);
  assert.match(cleanupContract, /releases', 'v0\.1\.18/);
  assert.match(buildCleanup, /collectBuildTargets/);
  assert.doesNotMatch(buildCleanup, /collectReleaseTargets/);
  assert.match(releaseCleanup, /collectReleaseTargets/);
  assert.doesNotMatch(releaseCleanup, /collectBuildTargets/);
});
