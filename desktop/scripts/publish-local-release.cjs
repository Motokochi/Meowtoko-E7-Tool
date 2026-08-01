const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const {
  metadata,
  repositoryRoot,
  resolveInstallerRoot,
  resolveLocalReleaseDir,
} = require('./output-paths.cjs');

function sha256(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

function isWithin(parent, child) {
  const relative = path.relative(parent, child);
  return relative !== '' && !relative.startsWith(`..${path.sep}`) && relative !== '..'
    && !path.isAbsolute(relative);
}

function publishLocalRelease({
  environment = process.env,
  dryRun = false,
} = {}) {
  const releasesRoot = path.join(repositoryRoot, 'releases');
  const sourceRoot = resolveInstallerRoot(environment);
  const destination = resolveLocalReleaseDir(environment);
  assert.ok(
    isWithin(releasesRoot, destination),
    `Local release destination must stay beneath ${releasesRoot}: ${destination}`,
  );

  const artifacts = [
    `Meowtoko-E7-Tool-${metadata.version}-Setup.exe`,
    `E7Hub-${metadata.version}-full.nupkg`,
    'RELEASES',
  ].map((name) => ({
    name,
    source: path.join(sourceRoot, name),
  }));
  for (const artifact of artifacts) {
    assert.ok(
      fs.statSync(artifact.source).isFile(),
      `Audited release artifact is missing: ${artifact.source}`,
    );
  }
  assert.ok(
    !fs.existsSync(destination),
    `Local release destination already exists and will not be overwritten: ${destination}`,
  );

  console.log(`E7_LOCAL_RELEASE_SOURCE ${sourceRoot}`);
  console.log(`E7_LOCAL_RELEASE_DESTINATION ${destination}`);
  for (const artifact of artifacts) {
    console.log(
      `E7_LOCAL_RELEASE_ARTIFACT ${artifact.name} bytes=${fs.statSync(artifact.source).size} `
      + `sha256=${sha256(artifact.source)}`,
    );
  }
  if (dryRun) {
    console.log(`E7_LOCAL_RELEASE_DRY_RUN_OK version=${metadata.version}`);
    return destination;
  }

  fs.mkdirSync(releasesRoot, { recursive: true });
  const staging = path.join(
    releasesRoot,
    `.${path.basename(destination)}-publish-${crypto.randomUUID()}.tmp`,
  );
  assert.ok(isWithin(releasesRoot, staging), `Unsafe release staging path: ${staging}`);
  try {
    fs.mkdirSync(staging);
    const hashLines = [];
    for (const artifact of artifacts) {
      const target = path.join(staging, artifact.name);
      fs.copyFileSync(artifact.source, target, fs.constants.COPYFILE_EXCL);
      assert.equal(
        sha256(target),
        sha256(artifact.source),
        `Release artifact changed while copying: ${artifact.name}`,
      );
      hashLines.push(`${sha256(target)}  ${artifact.name}`);
    }
    fs.writeFileSync(
      path.join(staging, 'SHA256SUMS.txt'),
      `${hashLines.join('\n')}\n`,
      { encoding: 'utf8', flag: 'wx' },
    );
    fs.renameSync(staging, destination);
  } finally {
    if (fs.existsSync(staging)) {
      assert.ok(isWithin(releasesRoot, staging), `Unsafe cleanup path: ${staging}`);
      fs.rmSync(staging, { recursive: true, force: true });
    }
  }
  console.log(`E7_LOCAL_RELEASE_PUBLISH_OK version=${metadata.version} path=${destination}`);
  return destination;
}

if (require.main === module) {
  publishLocalRelease({ dryRun: process.argv.includes('--dry-run') });
}

module.exports = { publishLocalRelease };
