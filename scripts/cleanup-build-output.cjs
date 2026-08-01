const {
  collectBuildTargets,
  executeCleanup,
  parseCleanupMode,
  repositoryRoot,
} = require('./cleanup-contract.cjs');

function main(argumentsList = process.argv.slice(2)) {
  const { dryRun } = parseCleanupMode(argumentsList);
  return executeCleanup(
    collectBuildTargets(repositoryRoot),
    {
      root: repositoryRoot,
      dryRun,
      label: 'transient-build-output',
    },
  );
}

if (require.main === module) {
  main();
}

module.exports = { main };
