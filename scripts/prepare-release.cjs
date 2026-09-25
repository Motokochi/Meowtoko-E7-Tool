const assert = require('node:assert/strict');
const childProcess = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const {
  compareVersions,
  packageVersion,
  parseStableVersion,
} = require('./release-contract.cjs');

function git(root, args) {
  return childProcess.execFileSync('git', args, {
    cwd: root,
    encoding: 'utf8',
    windowsHide: true,
  }).trim();
}

function prepareRelease({ root, version, title, date, dryRun = false }) {
  parseStableVersion(version);
  assert.match(date, /^\d{4}-\d{2}-\d{2}$/, 'Release date must be YYYY-MM-DD.');
  assert.ok(title.trim().length >= 8 && title.trim().length <= 120, 'Release title must be 8-120 characters.');
  assert.equal(
    git(root, ['status', '--porcelain=v1', '--untracked-files=all']),
    '',
    'Release preparation requires a clean working tree.',
  );

  const current = packageVersion(root);
  assert.equal(
    compareVersions(version, current),
    1,
    `Next release ${version} must be newer than ${current}.`,
  );
  assert.equal(git(root, ['tag', '--list', `v${version}`]), '', `Tag v${version} already exists.`);

  const packagePath = path.join(root, 'desktop', 'package.json');
  const changelogPath = path.join(root, 'CHANGELOG.md');
  const changelog = fs.readFileSync(changelogPath, 'utf8');
  assert.match(changelog, /^# Changelog\r?\n\r?\n/, 'CHANGELOG.md must start with its title and a blank line.');
  assert.ok(!changelog.includes(`## [${version}]`), `Changelog already contains ${version}.`);

  if (dryRun) {
    return { current, changelogPath, version };
  }

  const metadata = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
  metadata.version = version;
  fs.writeFileSync(packagePath, `${JSON.stringify(metadata, null, 2)}\n`, 'utf8');

  const section = `## [${version}] - ${date}\n\n- ${title.trim()}.\n\n`;
  fs.writeFileSync(changelogPath, changelog.replace(/^# Changelog\r?\n\r?\n/, `# Changelog\n\n${section}`), 'utf8');
  return { current, changelogPath, version };
}

if (require.main === module) {
  const root = path.resolve(__dirname, '..');
  const version = process.argv[2];
  const titleIndex = process.argv.indexOf('--title');
  const dateIndex = process.argv.indexOf('--date');
  assert.ok(version, 'Usage: node scripts/prepare-release.cjs VERSION --title "Summary" [--date YYYY-MM-DD] [--dry-run]');
  assert.ok(titleIndex >= 0 && process.argv[titleIndex + 1], '--title is required.');
  const result = prepareRelease({
    root,
    version,
    title: process.argv[titleIndex + 1],
    date: dateIndex >= 0 ? process.argv[dateIndex + 1] : new Date().toISOString().slice(0, 10),
    dryRun: process.argv.includes('--dry-run'),
  });
  console.log(
    `E7_RELEASE_PREPARATION_${process.argv.includes('--dry-run') ? 'DRY_RUN_' : ''}OK `
    + `from=${result.current} to=${result.version}`,
  );
}

module.exports = { prepareRelease };
