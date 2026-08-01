const fs = require('node:fs');
const path = require('node:path');

const desktopRoot = path.resolve(__dirname, '..');
const repositoryRoot = path.resolve(desktopRoot, '..');
const metadata = JSON.parse(
  fs.readFileSync(path.join(desktopRoot, 'package.json'), 'utf8'),
);

function resolveForgeOutDir(environment = process.env) {
  const configured = environment.E7_FORGE_OUT_DIR?.trim();
  return configured
    ? path.resolve(desktopRoot, configured)
    : path.join(repositoryRoot, '.build', 'forge', `v${metadata.version}`);
}

function resolveLocalReleaseDir(environment = process.env) {
  const configured = environment.E7_LOCAL_RELEASE_DIR?.trim();
  return configured
    ? path.resolve(repositoryRoot, configured)
    : path.join(repositoryRoot, 'releases', `v${metadata.version}`);
}

function resolveInstallerRoot(environment = process.env) {
  return path.join(
    resolveForgeOutDir(environment),
    'make',
    'squirrel.windows',
    'x64',
  );
}

module.exports = {
  desktopRoot,
  metadata,
  repositoryRoot,
  resolveForgeOutDir,
  resolveInstallerRoot,
  resolveLocalReleaseDir,
};
