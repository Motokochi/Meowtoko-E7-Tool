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
  const notesPath = path.join(root, 'docs', 'releases', `v${version}.md`);
  assert.ok(!fs.existsSync(notesPath), `Release notes already exist: ${notesPath}`);

  if (dryRun) {
    return { current, notesPath, version };
  }

  const metadata = JSON.parse(fs.readFileSync(packagePath, 'utf8'));
  metadata.version = version;
  fs.writeFileSync(packagePath, `${JSON.stringify(metadata, null, 2)}\n`, 'utf8');

  const changelog = fs.readFileSync(changelogPath, 'utf8');
  const section = `## [${version}] - ${date}\n\n- ${title.trim()}.\n\n`;
  fs.writeFileSync(changelogPath, changelog.replace(/^# Changelog\r?\n\r?\n/, `# Changelog\n\n${section}`), 'utf8');

  const notes = [
    `# Meowtoko E7 Tool ${version}`,
    '',
    title.trim(),
    '',
    '## Highlights',
    '',
    '- Replace this line with user-facing changes before tagging.',
    '',
    '## Install',
    '',
    `Download \`Meowtoko-E7-Tool-${version}-Setup.exe\` from this release. Close Meowtoko E7 Tool,`,
    'run the installer, and accept the expected unsigned-publisher warning only',
    'when your own Windows or organization policy allows it.',
    '',
    '## Integrity',
    '',
    'Verify downloaded files against `SHA256SUMS.txt` attached to this release.',
    '',
  ].join('\n');
  fs.writeFileSync(notesPath, notes, { encoding: 'utf8', flag: 'wx' });
  return { current, notesPath, version };
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
