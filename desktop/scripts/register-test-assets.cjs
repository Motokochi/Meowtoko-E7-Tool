const Module = require('node:module');
const path = require('node:path');

const desktopModules = path.resolve(__dirname, '..', 'node_modules');
process.env.NODE_PATH = [desktopModules, process.env.NODE_PATH]
  .filter(Boolean)
  .join(path.delimiter);
Module._initPaths();

require.extensions['.png'] = (module, filename) => {
  module.exports = `test-asset://${path.basename(filename)}`;
};
