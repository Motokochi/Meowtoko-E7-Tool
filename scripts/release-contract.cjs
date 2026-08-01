const assert = require('node:assert/strict');
const childProcess = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const STABLE_VERSION = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;
const STABLE_TAG = /^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;

function parseStableVersion(value) {
  assert.equal(typeof value, 'string', 'Version must be a string.');
  assert.match(value, STABLE_VERSION, `Stable version must be MAJOR.MINOR.PATCH: ${value}`);
  return value.split('.').map(Number);
}

function compareVersions(left, right) {
  const a = parseStableVersion(left);
  const b = parseStableVersion(right);
  for (let index = 0; index < 3; index += 1) {
    if (a[index] !== b[index]) {
      return Math.sign(a[index] - b[index]);
    }
  }
  return 0;
}

function packageVersion(root) {
  const metadata = JSON.parse(
    fs.readFileSync(path.join(root, 'desktop', 'package.json'), 'utf8'),
  );
  parseStableVersion(metadata.version);
  assert.equal(metadata.name, 'meowtoko-e7-tool-desktop');
  assert.equal(metadata.productName, 'Meowtoko E7 Tool');
  return metadata.version;
}

function runGit(root, args) {
  return childProcess.execFileSync('git', args, {
    cwd: root,
    encoding: 'utf8',
    windowsHide: true,
  }).trim();
}

function verifyReleaseContract({
  root,
  tag,
  requireClean = false,
  requireTagAbsent = false,
} = {}) {
  const version = packageVersion(root);
  const expectedTag = `v${version}`;
  if (tag !== undefined) {
    assert.match(tag, STABLE_TAG, `Stable tag must be vMAJOR.MINOR.PATCH: ${tag}`);
    assert.equal(tag, expectedTag, `Tag ${tag} does not match package version ${version}.`);
  }

  const changelog = fs.readFileSync(path.join(root, 'CHANGELOG.md'), 'utf8');
  assert.match(
    changelog,
    new RegExp(`^## \\[${version.replaceAll('.', '\\.')}\\] - \\d{4}-\\d{2}-\\d{2}$`, 'm'),
    `CHANGELOG.md has no release section for ${version}.`,
  );
  const notesPath = path.join(root, 'docs', 'releases', `v${version}.md`);
  assert.ok(
    fs.existsSync(notesPath) && fs.statSync(notesPath).isFile(),
    `Release notes are missing: ${notesPath}`,
  );
  const notes = fs.readFileSync(notesPath, 'utf8');
  assert.match(notes, new RegExp(`^# Meowtoko E7 Tool ${version.replaceAll('.', '\\.')}$`, 'm'));

  if (requireClean) {
    assert.equal(
      runGit(root, ['status', '--porcelain=v1', '--untracked-files=all']),
      '',
      'Stable release requires a clean working tree.',
    );
  }
  if (requireTagAbsent) {
    assert.equal(
      runGit(root, ['tag', '--list', expectedTag]),
      '',
      `Release tag already exists: ${expectedTag}`,
    );
  }
  return { notesPath, tag: expectedTag, version };
}

if (require.main === module) {
  const root = path.resolve(__dirname, '..');
  const tagIndex = process.argv.indexOf('--tag');
  const result = verifyReleaseContract({
    root,
    tag: tagIndex >= 0 ? process.argv[tagIndex + 1] : undefined,
    requireClean: process.argv.includes('--require-clean'),
    requireTagAbsent: process.argv.includes('--require-tag-absent'),
  });
  console.log(`E7_RELEASE_CONTRACT_OK version=${result.version} tag=${result.tag}`);
}

module.exports = {
  STABLE_TAG,
  STABLE_VERSION,
  compareVersions,
  packageVersion,
  parseStableVersion,
  verifyReleaseContract,
};
