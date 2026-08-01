const fs = require('node:fs');
const path = require('node:path');

const SKIPPED_DIRECTORIES = new Set([
  '.build',
  '.git',
  '.idea',
  '.local',
  '.pnpm-store',
  '.pytest_cache',
  '.test-dist',
  '.webpack',
  'build',
  'dist',
  'node_modules',
  'out',
  'releases',
  'user_data',
]);

function markdownFiles(root) {
  const files = [];
  const visit = (directory) => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const absolute = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        if (!SKIPPED_DIRECTORIES.has(entry.name) && !entry.name.startsWith('out-')) {
          visit(absolute);
        }
      } else if (entry.isFile() && entry.name.toLowerCase().endsWith('.md')) {
        files.push(absolute);
      }
    }
  };
  visit(root);
  return files.sort();
}

function localTarget(rawTarget) {
  let target = rawTarget.trim();
  if (target.startsWith('<') && target.endsWith('>')) {
    target = target.slice(1, -1);
  } else {
    target = target.replace(/\s+(?:"[^"]*"|'[^']*')\s*$/, '');
  }
  if (
    target === ''
    || target.startsWith('#')
    || /^[a-z][a-z0-9+.-]*:/i.test(target)
    || target.startsWith('//')
  ) {
    return null;
  }
  const withoutFragment = target.split('#', 1)[0].split('?', 1)[0];
  try {
    return decodeURIComponent(withoutFragment);
  } catch {
    return withoutFragment;
  }
}

function validateDocumentation(root) {
  const failures = [];
  const files = markdownFiles(root);
  let links = 0;

  for (const file of files) {
    const source = fs.readFileSync(file, 'utf8');
    const matcher = /!?\[[^\]]*]\(([^)\r\n]+)\)/g;
    let match;
    while ((match = matcher.exec(source)) !== null) {
      const target = localTarget(match[1]);
      if (target === null) {
        continue;
      }
      links += 1;
      const absolute = path.resolve(path.dirname(file), target.replaceAll('/', path.sep));
      if (!fs.existsSync(absolute)) {
        failures.push({
          file: path.relative(root, file).replaceAll(path.sep, '/'),
          target,
        });
      }
    }
  }

  return { failures, files: files.length, links };
}

function validatePublicGuide(root) {
  const failures = [];
  const guideFiles = ['README.md', 'docs/INSTALLING.md', 'docs/USER_GUIDE.md'];
  const sources = new Map();
  for (const relative of guideFiles) {
    const absolute = path.join(root, relative.replaceAll('/', path.sep));
    if (!fs.existsSync(absolute)) {
      failures.push({ file: relative, target: 'required public guide file' });
      continue;
    }
    sources.set(relative, fs.readFileSync(absolute, 'utf8'));
  }

  const readme = sources.get('README.md') ?? '';
  const latestRelease = 'https://github.com/Motokochi/Meowtoko-E7-Tool/releases/latest';
  if (!readme.includes(latestRelease)) {
    failures.push({ file: 'README.md', target: 'permanent latest-release URL' });
  }
  if (!/Download the latest release/i.test(readme)) {
    failures.push({ file: 'README.md', target: 'prominent latest-release label' });
  }

  const staleInstaller = /\bMeowtoko-E7-Tool-\d+\.\d+\.\d+-Setup\.exe\b/i;
  const retiredLauncher = /\b(?:run_app\.ps1|launch\.ps1|python(?:3|\.exe)?\s+(?:-m\s+)?(?:gui|main)\.py)\b/i;
  const obsoleteImport = /Open\s+\*\*Optimizer\*\*[^.\n]{0,120}(?:gear\.txt|Import)/i;
  for (const [relative, source] of sources) {
    if (staleInstaller.test(source)) {
      failures.push({ file: relative, target: 'stale hard-coded installer version' });
    }
    if (retiredLauncher.test(source)) {
      failures.push({ file: relative, target: 'retired launcher instruction' });
    }
    if (obsoleteImport.test(source)) {
      failures.push({ file: relative, target: 'obsolete Optimizer import instruction' });
    }
  }

  for (const name of ['overview.png', 'analyzer.png', 'enhancer.png', 'optimizer.png']) {
    const relative = `assets/readme/${name}`;
    const absolute = path.join(root, ...relative.split('/'));
    if (!fs.existsSync(absolute)) {
      failures.push({ file: 'README.md', target: relative });
      continue;
    }
    const size = fs.statSync(absolute).size;
    if (size < 10_000 || size > 1_500_000) {
      failures.push({ file: relative, target: 'screenshot size must be 10 KB to 1.5 MB' });
    }
  }
  return failures;
}

if (require.main === module) {
  const root = path.resolve(__dirname, '..');
  const result = validateDocumentation(root);
  const failures = [...result.failures, ...validatePublicGuide(root)];
  if (failures.length > 0) {
    for (const failure of failures) {
      console.error(`Broken local documentation link: ${failure.file} -> ${failure.target}`);
    }
    process.exitCode = 1;
  } else {
    console.log(`E7_DOCUMENTATION_LINKS_OK files=${result.files} localLinks=${result.links}`);
  }
}

module.exports = {
  localTarget,
  markdownFiles,
  validatePublicGuide,
  validateDocumentation,
};
