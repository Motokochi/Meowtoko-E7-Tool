const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const {
  compareVersions,
  parseStableVersion,
  verifyReleaseContract,
} = require('./release-contract.cjs');

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'e7-release-contract-'));
  fs.mkdirSync(path.join(root, 'desktop'));
  fs.mkdirSync(path.join(root, 'docs', 'releases'), { recursive: true });
  fs.writeFileSync(
    path.join(root, 'desktop', 'package.json'),
    `${JSON.stringify({ name: 'meowtoko-e7-tool-desktop', productName: 'Meowtoko E7 Tool', version: '1.2.3' })}\n`,
  );
  fs.writeFileSync(path.join(root, 'CHANGELOG.md'), '# Changelog\n\n## [1.2.3] - 2026-07-25\n');
  fs.writeFileSync(path.join(root, 'docs', 'releases', 'v1.2.3.md'), '# Meowtoko E7 Tool 1.2.3\n');
  return root;
}

test('stable version parser rejects prereleases and noncanonical numbers', () => {
  assert.deepEqual(parseStableVersion('1.2.3'), [1, 2, 3]);
  for (const invalid of ['v1.2.3', '1.2', '1.2.3-beta.1', '01.2.3', '1.2.3.4']) {
    assert.throws(() => parseStableVersion(invalid));
  }
});

test('version comparison is mathematical', () => {
  assert.equal(compareVersions('1.10.0', '1.9.9'), 1);
  assert.equal(compareVersions('2.0.0', '2.0.0'), 0);
  assert.equal(compareVersions('0.9.9', '1.0.0'), -1);
});

test('source contract requires matching changelog, notes, and tag', () => {
  const root = fixture();
  try {
    assert.equal(verifyReleaseContract({ root, tag: 'v1.2.3' }).version, '1.2.3');
    assert.throws(() => verifyReleaseContract({ root, tag: 'v1.2.4' }), /does not match/);
    fs.rmSync(path.join(root, 'docs', 'releases', 'v1.2.3.md'));
    assert.throws(() => verifyReleaseContract({ root }), /Release notes are missing/);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
