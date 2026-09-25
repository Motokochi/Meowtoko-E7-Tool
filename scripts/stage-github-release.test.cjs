const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const { inside, releaseAssetNames, stageGitHubRelease } = require('./stage-github-release.cjs');
const metadata = require('../desktop/package.json');

test('GitHub release staging accepts only strict descendants of .build', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'e7-release-stage-'));
  try {
    const build = path.join(root, '.build');
    assert.equal(inside(build, path.join(build, 'github-release', 'v1.0.0')), true);
    assert.equal(inside(build, build), false);
    assert.equal(inside(build, root), false);
    assert.equal(inside(build, path.join(root, 'releases')), false);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('public release asset names match GitHub filename normalization', () => {
  assert.deepEqual(releaseAssetNames('1.2.3'), {
    package: 'E7Hub-1.2.3-full.nupkg',
    setup: 'Meowtoko-E7-Tool-1.2.3-Setup.exe',
    sourceZip: 'Meowtoko E7 Tool-win32-x64-1.2.3.zip',
    zip: 'Meowtoko.E7.Tool-win32-x64-1.2.3.zip',
  });
});

test('staging publishes changelog notes with the checksummed assets without per-version documents', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'e7-release-stage-'));
  try {
    fs.mkdirSync(path.join(root, 'desktop'));
    fs.writeFileSync(path.join(root, 'desktop', 'package.json'), JSON.stringify(metadata));
    fs.writeFileSync(path.join(root, 'CHANGELOG.md'), `# Changelog\n\n## [${metadata.version}] - 2026-09-25\n\n- Staged change.\n`);
    const installerRoot = path.join(root, 'installer');
    const forgeOutDir = path.join(root, 'forge');
    const zipRoot = path.join(forgeOutDir, 'make', 'zip', 'win32', 'x64');
    fs.mkdirSync(installerRoot);
    fs.mkdirSync(zipRoot, { recursive: true });
    const names = releaseAssetNames(metadata.version);
    fs.writeFileSync(path.join(installerRoot, names.setup), 'fake-setup-with-embedded-package');
    fs.writeFileSync(path.join(installerRoot, names.package), 'fake-package');
    fs.writeFileSync(path.join(installerRoot, 'RELEASES'), 'fake-release-index');
    fs.writeFileSync(path.join(zipRoot, names.sourceZip), 'fake-zip');
    const destination = path.join(root, '.build', 'release');
    stageGitHubRelease({ root, tag: `v${metadata.version}`, destination, installerRoot, forgeOutDir });
    assert.equal(fs.readFileSync(path.join(destination, 'release-notes.md'), 'utf8'),
      `# Meowtoko E7 Tool ${metadata.version}\n\n- Staged change.\n`);
    assert.equal(fs.readFileSync(path.join(destination, 'SHA256SUMS.txt'), 'utf8').trim().split('\n').length, 4);
    assert.equal(fs.readdirSync(destination).length, 6);
    assert.equal(fs.existsSync(path.join(root, 'docs')), false);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
