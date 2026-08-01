const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { afterEach, test } = require('node:test');

const {
  assertSafeRepositoryTarget,
  collectBuildTargets,
  collectReleaseTargets,
  executeCleanup,
  parseCleanupMode,
} = require('./cleanup-contract.cjs');

const temporaryRoots = [];

function fixtureRoot() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'e7-cleanup-contract-'));
  temporaryRoots.push(root);
  fs.mkdirSync(path.join(root, '.git'));
  fs.mkdirSync(path.join(root, '.local', 'user-data'), { recursive: true });
  fs.mkdirSync(path.join(root, '.pnpm-store'));
  fs.mkdirSync(path.join(root, 'desktop', 'node_modules'), { recursive: true });
  fs.mkdirSync(path.join(root, 'releases', 'v0.1.18'), { recursive: true });
  return root;
}

afterEach(() => {
  while (temporaryRoots.length) {
    fs.rmSync(temporaryRoots.pop(), { recursive: true, force: true });
  }
});

test('cleanup target validation accepts bounded output and rejects unsafe overlap', () => {
  const root = fixtureRoot();
  const build = path.join(root, '.build');
  const outside = path.resolve(root, '..', 'outside');
  const installed = path.join(root, 'installed', 'E7 Hub');

  assert.equal(assertSafeRepositoryTarget(build, { root }), build);
  assert.throws(() => assertSafeRepositoryTarget(root, { root }), /repository root/);
  assert.throws(() => assertSafeRepositoryTarget(outside, { root }), /stay within/);
  assert.throws(
    () => assertSafeRepositoryTarget(path.join(root, '.local'), { root }),
    /protected path/,
  );
  assert.throws(
    () => assertSafeRepositoryTarget(installed, {
      root,
      environment: { APPDATA: path.dirname(installed) },
    }),
    /installed application data/,
  );
});

test('build cleanup dry-run prints exact targets and changes nothing', () => {
  const root = fixtureRoot();
  const build = path.join(root, '.build');
  const cache = path.join(root, 'src', '__pycache__');
  const historical = path.join(root, 'desktop', 'out-0.1.1-final');
  fs.mkdirSync(build);
  fs.writeFileSync(path.join(build, 'artifact.txt'), 'generated');
  fs.mkdirSync(cache, { recursive: true });
  fs.writeFileSync(path.join(cache, 'module.pyc'), 'compiled');
  fs.mkdirSync(historical);

  const targets = collectBuildTargets(root);
  const lines = [];
  executeCleanup(targets, {
    root,
    dryRun: true,
    label: 'test-build',
    output: (line) => lines.push(line),
  });

  assert.ok(targets.includes(build));
  assert.ok(targets.includes(cache));
  assert.ok(targets.includes(historical));
  assert.ok(fs.existsSync(path.join(build, 'artifact.txt')));
  assert.ok(lines.some((line) => line.includes(`path=${build}`)));
  assert.match(lines.at(-1), /E7_CLEANUP_DRY_RUN_OK/);
});

test('release cleanup excludes the preserved v0.1.18 archive', () => {
  const root = fixtureRoot();
  const removable = path.join(root, 'releases', 'v0.1.19');
  fs.mkdirSync(removable);

  assert.deepEqual(collectReleaseTargets(root), [removable]);
  assert.throws(
    () => assertSafeRepositoryTarget(path.join(root, 'releases', 'v0.1.18'), { root }),
    /protected path/,
  );
});

test('apply mode removes only validated temporary fixture output', () => {
  const root = fixtureRoot();
  const build = path.join(root, '.build');
  fs.mkdirSync(build);
  fs.writeFileSync(path.join(build, 'artifact.txt'), 'generated');

  executeCleanup([build], {
    root,
    dryRun: false,
    label: 'test-apply',
    output: () => {},
  });

  assert.equal(fs.existsSync(build), false);
  assert.equal(fs.existsSync(path.join(root, '.local', 'user-data')), true);
});

test('apply mode continues after one target fails and reports the aggregate', () => {
  const root = fixtureRoot();
  const missing = path.join(root, '.build', 'missing');
  const removable = path.join(root, 'dist');
  fs.mkdirSync(removable);
  const lines = [];

  assert.throws(
    () => executeCleanup([missing, removable], {
      root,
      dryRun: false,
      label: 'test-partial-failure',
      output: (line) => lines.push(line),
    }),
    /could not remove 1 of 2/,
  );
  assert.equal(fs.existsSync(removable), false);
  assert.ok(lines.some((line) => line.includes(`E7_CLEANUP_FAILED path=${missing}`)));
  assert.ok(lines.some((line) => line.includes(`E7_CLEANUP_REMOVED path=${removable}`)));
});

test('cleanup CLI defaults to dry-run and rejects ambiguous arguments', () => {
  assert.deepEqual(parseCleanupMode([]), { dryRun: true });
  assert.deepEqual(parseCleanupMode(['--dry-run']), { dryRun: true });
  assert.deepEqual(parseCleanupMode(['--apply']), { dryRun: false });
  assert.throws(() => parseCleanupMode(['--apply', '--dry-run']), /either/);
  assert.throws(() => parseCleanupMode(['--force']), /Unknown cleanup argument/);
});
