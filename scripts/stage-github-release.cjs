const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const {
  metadata,
  repositoryRoot,
  resolveForgeOutDir,
  resolveInstallerRoot,
} = require('../desktop/scripts/output-paths.cjs');
const { verifyReleaseContract } = require('./release-contract.cjs');

function sha256(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

function inside(parent, child) {
  const relative = path.relative(parent, child);
  return relative !== '' && relative !== '..' && !relative.startsWith(`..${path.sep}`)
    && !path.isAbsolute(relative);
}

function releaseAssetNames(version) {
  return {
    package: `E7Hub-${version}-full.nupkg`,
    setup: `Meowtoko-E7-Tool-${version}-Setup.exe`,
    sourceZip: `Meowtoko E7 Tool-win32-x64-${version}.zip`,
    zip: `Meowtoko.E7.Tool-win32-x64-${version}.zip`,
  };
}

function stageGitHubRelease({
  root = repositoryRoot,
  tag,
  destination = path.join(repositoryRoot, '.build', 'github-release', `v${metadata.version}`),
  installerRoot = resolveInstallerRoot(),
  forgeOutDir = resolveForgeOutDir(),
} = {}) {
  const contract = verifyReleaseContract({ root, tag });
  assert.equal(contract.version, metadata.version, 'Output metadata version drifted from source.');

  const buildRoot = path.join(root, '.build');
  const resolvedDestination = path.resolve(destination);
  assert.ok(inside(buildRoot, resolvedDestination), `Release staging must stay beneath ${buildRoot}.`);
  assert.ok(!fs.existsSync(resolvedDestination), `Release staging already exists: ${resolvedDestination}`);

  const names = releaseAssetNames(metadata.version);
  const inputs = [
    { name: names.setup, source: path.join(installerRoot, names.setup) },
    { name: names.package, source: path.join(installerRoot, names.package) },
    { name: 'RELEASES', source: path.join(installerRoot, 'RELEASES') },
    {
      name: names.zip,
      source: path.join(forgeOutDir, 'make', 'zip', 'win32', 'x64', names.sourceZip),
    },
  ];
  for (const input of inputs) {
    assert.ok(fs.existsSync(input.source) && fs.statSync(input.source).isFile(), `Missing release input: ${input.source}`);
    assert.ok(fs.statSync(input.source).size > 0, `Empty release input: ${input.source}`);
  }
  assert.ok(
    fs.statSync(inputs[0].source).size > fs.statSync(inputs[1].source).size,
    'Setup.exe does not embed the full Squirrel package.',
  );

  const temporary = `${resolvedDestination}.${crypto.randomUUID()}.tmp`;
  assert.ok(inside(buildRoot, temporary), `Unsafe release temporary directory: ${temporary}`);
  try {
    fs.mkdirSync(temporary, { recursive: true });
    const checksums = [];
    for (const input of inputs) {
      const target = path.join(temporary, input.name);
      fs.copyFileSync(input.source, target, fs.constants.COPYFILE_EXCL);
      const digest = sha256(target);
      assert.equal(digest, sha256(input.source), `Release input changed while copying: ${input.name}`);
      checksums.push(`${digest}  ${input.name}`);
    }
    fs.copyFileSync(contract.notesPath, path.join(temporary, 'release-notes.md'), fs.constants.COPYFILE_EXCL);
    fs.writeFileSync(
      path.join(temporary, 'SHA256SUMS.txt'),
      `${checksums.join('\n')}\n`,
      { encoding: 'utf8', flag: 'wx' },
    );
    fs.mkdirSync(path.dirname(resolvedDestination), { recursive: true });
    fs.renameSync(temporary, resolvedDestination);
  } finally {
    if (fs.existsSync(temporary)) {
      assert.ok(inside(buildRoot, temporary), `Unsafe release staging cleanup: ${temporary}`);
      fs.rmSync(temporary, { recursive: true, force: true });
    }
  }

  console.log(
    `E7_GITHUB_RELEASE_STAGING_OK version=${metadata.version} files=6 path=${resolvedDestination}`,
  );
  return resolvedDestination;
}

if (require.main === module) {
  const tagIndex = process.argv.indexOf('--tag');
  assert.ok(tagIndex >= 0 && process.argv[tagIndex + 1], '--tag vMAJOR.MINOR.PATCH is required.');
  stageGitHubRelease({ tag: process.argv[tagIndex + 1] });
}

module.exports = { inside, releaseAssetNames, sha256, stageGitHubRelease };
