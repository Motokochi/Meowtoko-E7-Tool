const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const repositoryRoot = path.resolve(__dirname, '..');
const PRESERVED_RELEASE = path.join('releases', 'v0.1.18');
const ALWAYS_PROTECTED = [
  '.git',
  '.local',
  '.pnpm-store',
  path.join('desktop', 'node_modules'),
  PRESERVED_RELEASE,
];
const FIXED_BUILD_TARGETS = [
  '.build',
  'build',
  'dist',
  '.pytest_cache',
  path.join('desktop', '.webpack'),
  path.join('desktop', '.test-dist'),
];
const CACHE_SCAN_ROOTS = ['src', 'tests', 'scripts', 'packaging'];

function canonicalForComparison(candidate) {
  const resolved = path.resolve(candidate);
  return process.platform === 'win32' ? resolved.toLowerCase() : resolved;
}

function isSameOrWithin(parent, child) {
  const relative = path.relative(parent, child);
  return relative === ''
    || (!relative.startsWith(`..${path.sep}`)
      && relative !== '..'
      && !path.isAbsolute(relative));
}

function pathsOverlap(left, right) {
  const normalizedLeft = canonicalForComparison(left);
  const normalizedRight = canonicalForComparison(right);
  return isSameOrWithin(normalizedLeft, normalizedRight)
    || isSameOrWithin(normalizedRight, normalizedLeft);
}

function installedDataDirectory(environment = process.env) {
  const appData = environment.APPDATA?.trim();
  return appData ? path.resolve(appData, 'E7 Hub') : undefined;
}

function assertSafeRepositoryTarget(
  target,
  {
    root = repositoryRoot,
    environment = process.env,
    protectedRelativePaths = ALWAYS_PROTECTED,
  } = {},
) {
  const resolvedRoot = path.resolve(root);
  const resolvedTarget = path.resolve(target);
  assert.notEqual(
    canonicalForComparison(resolvedTarget),
    canonicalForComparison(resolvedRoot),
    'Cleanup must never target the repository root.',
  );
  assert.ok(
    isSameOrWithin(
      canonicalForComparison(resolvedRoot),
      canonicalForComparison(resolvedTarget),
    ),
    `Cleanup target must stay within the repository: ${resolvedTarget}`,
  );

  const installed = installedDataDirectory(environment);
  if (installed) {
    assert.ok(
      !pathsOverlap(resolvedTarget, installed),
      `Cleanup target overlaps installed application data: ${resolvedTarget}`,
    );
  }

  for (const relative of protectedRelativePaths) {
    const protectedPath = path.resolve(resolvedRoot, relative);
    assert.ok(
      !pathsOverlap(resolvedTarget, protectedPath),
      `Cleanup target overlaps protected path ${protectedPath}: ${resolvedTarget}`,
    );
  }
  return resolvedTarget;
}

function addIfPresent(targets, candidate) {
  if (fs.existsSync(candidate)) {
    targets.add(path.resolve(candidate));
  }
}

function collectCacheTargets(root, targets) {
  const visit = (directory) => {
    let entries;
    try {
      entries = fs.readdirSync(directory, { withFileTypes: true });
    } catch (error) {
      error.message = `Unable to inspect cleanup candidate ${directory}: ${error.message}`;
      throw error;
    }
    for (const entry of entries) {
      const candidate = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        if (entry.name === '__pycache__') {
          targets.add(path.resolve(candidate));
        } else if (!entry.isSymbolicLink()) {
          visit(candidate);
        }
      } else if (entry.isFile() && /\.(?:pyc|pyo)$/i.test(entry.name)) {
        targets.add(path.resolve(candidate));
      }
    }
  };

  addIfPresent(targets, path.join(root, '__pycache__'));
  for (const relative of CACHE_SCAN_ROOTS) {
    const candidate = path.join(root, relative);
    if (fs.existsSync(candidate)) {
      visit(candidate);
    }
  }
}

function collectBuildTargets(root = repositoryRoot) {
  const resolvedRoot = path.resolve(root);
  const targets = new Set();
  for (const relative of FIXED_BUILD_TARGETS) {
    addIfPresent(targets, path.join(resolvedRoot, relative));
  }

  const desktop = path.join(resolvedRoot, 'desktop');
  if (fs.existsSync(desktop)) {
    for (const entry of fs.readdirSync(desktop, { withFileTypes: true })) {
      if (entry.name === 'out' || entry.name.startsWith('out-')) {
        targets.add(path.resolve(desktop, entry.name));
      }
    }
  }
  collectCacheTargets(resolvedRoot, targets);
  return [...targets].sort((left, right) => left.localeCompare(right));
}

function collectReleaseTargets(root = repositoryRoot) {
  const resolvedRoot = path.resolve(root);
  const releases = path.join(resolvedRoot, 'releases');
  if (!fs.existsSync(releases)) {
    return [];
  }
  return fs.readdirSync(releases, { withFileTypes: true })
    .map((entry) => path.resolve(releases, entry.name))
    .filter((candidate) => (
      canonicalForComparison(candidate)
      !== canonicalForComparison(path.join(resolvedRoot, PRESERVED_RELEASE))
    ))
    .sort((left, right) => left.localeCompare(right));
}

function targetKind(target) {
  if (!fs.existsSync(target)) {
    return 'missing';
  }
  const stats = fs.lstatSync(target);
  if (stats.isSymbolicLink()) {
    return 'symbolic-link';
  }
  return stats.isDirectory() ? 'directory' : 'file';
}

function executeCleanup(
  targets,
  {
    root = repositoryRoot,
    environment = process.env,
    dryRun = true,
    label,
    output = console.log,
  },
) {
  assert.ok(label, 'Cleanup label is required.');
  const uniqueTargets = [...new Set(targets.map((target) => path.resolve(target)))]
    .sort((left, right) => left.localeCompare(right));
  const validated = uniqueTargets.map((target) => assertSafeRepositoryTarget(
    target,
    { root, environment },
  ));

  output(`E7_CLEANUP_PLAN label=${label} mode=${dryRun ? 'dry-run' : 'apply'} targets=${validated.length}`);
  for (const target of validated) {
    const kind = targetKind(target);
    output(`E7_CLEANUP_TARGET kind=${kind} path=${target}`);
  }
  if (dryRun) {
    output(`E7_CLEANUP_DRY_RUN_OK label=${label} targets=${validated.length}`);
    return validated;
  }

  const failures = [];
  for (const target of validated) {
    try {
      fs.rmSync(target, { recursive: true, force: false, maxRetries: 3, retryDelay: 200 });
      assert.ok(!fs.existsSync(target), `Cleanup target still exists after removal: ${target}`);
      output(`E7_CLEANUP_REMOVED path=${target}`);
    } catch (error) {
      failures.push({ target, error });
      output(`E7_CLEANUP_FAILED path=${target} error=${error.code || error.name}`);
    }
  }
  if (failures.length) {
    throw new AggregateError(
      failures.map(({ error }) => error),
      `Cleanup could not remove ${failures.length} of ${validated.length} validated targets.`,
    );
  }
  output(`E7_CLEANUP_APPLY_OK label=${label} targets=${validated.length}`);
  return validated;
}

function parseCleanupMode(argumentsList) {
  const allowed = new Set(['--dry-run', '--apply']);
  for (const argument of argumentsList) {
    assert.ok(allowed.has(argument), `Unknown cleanup argument: ${argument}`);
  }
  assert.ok(
    !(argumentsList.includes('--dry-run') && argumentsList.includes('--apply')),
    'Choose either --dry-run or --apply, not both.',
  );
  return { dryRun: !argumentsList.includes('--apply') };
}

module.exports = {
  ALWAYS_PROTECTED,
  PRESERVED_RELEASE,
  assertSafeRepositoryTarget,
  collectBuildTargets,
  collectReleaseTargets,
  executeCleanup,
  installedDataDirectory,
  isSameOrWithin,
  parseCleanupMode,
  pathsOverlap,
  repositoryRoot,
};
