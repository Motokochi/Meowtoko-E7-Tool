const {
  collectReleaseTargets,
  executeCleanup,
  parseCleanupMode,
  repositoryRoot,
} = require('./cleanup-contract.cjs');

function main(argumentsList = process.argv.slice(2)) {
  const { dryRun } = parseCleanupMode(argumentsList);
  return executeCleanup(
    collectReleaseTargets(repositoryRoot),
    {
      root: repositoryRoot,
      dryRun,
      label: 'local-release-archives',
    },
  );
}

if (require.main === module) {
  main();
}

module.exports = { main };
