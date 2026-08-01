const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const {
  localTarget,
  validatePublicGuide,
  validateDocumentation,
} = require('./validate-documentation.cjs');

test('documentation validator accepts local files and ignores external URLs and fragments', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'e7-docs-valid-'));
  try {
    fs.mkdirSync(path.join(root, 'docs'));
    fs.writeFileSync(path.join(root, 'README.md'), [
      '[guide](docs/guide.md)',
      '[section](#section)',
      '[GitHub](https://github.com/Motokochi/Meowtoko-E7-Tool)',
    ].join('\n'));
    fs.writeFileSync(path.join(root, 'docs', 'guide.md'), '# Guide\n');
    assert.deepEqual(validateDocumentation(root).failures, []);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('documentation validator reports a missing local target', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'e7-docs-broken-'));
  try {
    fs.writeFileSync(path.join(root, 'README.md'), '[missing](docs/missing.md)\n');
    assert.deepEqual(validateDocumentation(root).failures, [
      { file: 'README.md', target: 'docs/missing.md' },
    ]);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('local target parsing handles angle paths, titles, queries, and URL schemes', () => {
  assert.equal(localTarget('<docs/My Guide.md#install>'), 'docs/My Guide.md');
  assert.equal(localTarget('docs/guide.md "Guide"'), 'docs/guide.md');
  assert.equal(localTarget('docs/guide.md?plain=1'), 'docs/guide.md');
  assert.equal(localTarget('mailto:hello@example.com'), null);
});

test('public guide contract rejects stale installers, retired launchers, and old import placement', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'e7-public-guide-'));
  try {
    fs.mkdirSync(path.join(root, 'docs'));
    fs.mkdirSync(path.join(root, 'assets', 'readme'), { recursive: true });
    fs.writeFileSync(
      path.join(root, 'README.md'),
      'Download the latest release at https://github.com/Motokochi/Meowtoko-E7-Tool/releases/latest\n',
    );
    fs.writeFileSync(path.join(root, 'docs', 'INSTALLING.md'), 'Run Meowtoko-E7-Tool-0.1.5-Setup.exe\n');
    fs.writeFileSync(
      path.join(root, 'docs', 'USER_GUIDE.md'),
      'Open **Optimizer** and choose gear.txt. Then run launch.ps1.\n',
    );
    for (const name of ['overview.png', 'analyzer.png', 'enhancer.png', 'optimizer.png']) {
      fs.writeFileSync(path.join(root, 'assets', 'readme', name), Buffer.alloc(20_000));
    }
    assert.deepEqual(validatePublicGuide(root), [
      { file: 'docs/INSTALLING.md', target: 'stale hard-coded installer version' },
      { file: 'docs/USER_GUIDE.md', target: 'retired launcher instruction' },
      { file: 'docs/USER_GUIDE.md', target: 'obsolete Optimizer import instruction' },
    ]);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
