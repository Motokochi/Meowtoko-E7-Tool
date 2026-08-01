const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const { inside, releaseAssetNames } = require('./stage-github-release.cjs');

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
