const assert = require('node:assert/strict');
const childProcess = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const {
  compareVersions,
  parseStableVersion,
  verifyReleaseContract,
} = require('./release-contract.cjs');
const { prepareRelease } = require('./prepare-release.cjs');

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'e7-release-contract-'));
  fs.mkdirSync(path.join(root, 'desktop'));
  fs.writeFileSync(
    path.join(root, 'desktop', 'package.json'),
    `${JSON.stringify({ name: 'meowtoko-e7-tool-desktop', productName: 'Meowtoko E7 Tool', version: '1.2.3' })}\n`,
  );
  fs.writeFileSync(path.join(root, 'CHANGELOG.md'), '# Changelog\n\n## [1.2.3] - 2026-07-25\n\n- Current change.\n\n## [1.2.2] - 2026-07-24\n\n- Older change.\n');
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

test('source contract generates only the matching changelog section and checks the tag', () => {
  const root = fixture();
  try {
    const contract = verifyReleaseContract({ root, tag: 'v1.2.3' });
    assert.equal(contract.version, '1.2.3');
    assert.equal(contract.notes, '# Meowtoko E7 Tool 1.2.3\n\n- Current change.\n');
    assert.throws(() => verifyReleaseContract({ root, tag: 'v1.2.4' }), /does not match/);
    fs.writeFileSync(path.join(root, 'CHANGELOG.md'), '# Changelog\n\n## [1.2.3] - 2026-07-25\n\n## [1.2.2] - 2026-07-24\n\n- Older change.\n');
    assert.throws(() => verifyReleaseContract({ root }), /no release notes/);
    fs.writeFileSync(path.join(root, 'CHANGELOG.md'), '# Changelog\n');
    assert.throws(() => verifyReleaseContract({ root }), /no release section/);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('release notes accept Windows newlines and preserve nested sections for the final version', () => {
  const root = fixture();
  try {
    fs.writeFileSync(path.join(root, 'CHANGELOG.md'), '# Changelog\r\n\r\n## [1.2.4] - 2026-07-26\r\n\r\n- Later.\r\n\r\n## [1.2.3] - 2026-07-25\r\n\r\n### Fixes\r\n\r\n- Selected change.\r\n');
    assert.equal(verifyReleaseContract({ root }).notes,
      '# Meowtoko E7 Tool 1.2.3\n\n### Fixes\r\n\r\n- Selected change.\n');
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('release preparation updates only version and changelog without a private notes dependency', () => {
  const root = fixture();
  const git = (args) => childProcess.execFileSync('git', args, { cwd: root, windowsHide: true, stdio: 'pipe' });
  try {
    git(['init']);
    git(['add', '.']);
    git(['-c', 'user.name=Release Test', '-c', 'user.email=release-test@example.invalid',
      '-c', 'commit.gpgsign=false', 'commit', '-m', 'fixture']);
    const changelog = fs.readFileSync(path.join(root, 'CHANGELOG.md'), 'utf8');
    const args = { root, version: '1.2.4', title: 'Improve inventory display', date: '2026-07-26' };
    prepareRelease({ ...args, dryRun: true });
    assert.equal(fs.readFileSync(path.join(root, 'CHANGELOG.md'), 'utf8'), changelog);
    assert.equal(verifyReleaseContract({ root }).version, '1.2.3');
    prepareRelease(args);
    assert.equal(verifyReleaseContract({ root, tag: 'v1.2.4' }).notes,
      '# Meowtoko E7 Tool 1.2.4\n\n- Improve inventory display.\n');
    assert.ok(fs.readFileSync(path.join(root, 'CHANGELOG.md'), 'utf8').includes(changelog.slice('# Changelog\n\n'.length)));
    assert.equal(fs.existsSync(path.join(root, 'docs')), false);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
