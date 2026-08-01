const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const net = require('node:net');
const path = require('node:path');

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      const port = typeof address === 'object' && address ? address.port : null;
      server.close((error) => (error ? reject(error) : resolve(port)));
    });
  });
}

async function debuggerPage(port) {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/list`);
      if (response.ok) {
        const pages = await response.json();
        const page = pages.find((entry) => entry.type === 'page' && entry.webSocketDebuggerUrl);
        if (page) return page;
      }
    } catch {
      // Chromium has not opened its local debugging endpoint yet.
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error('Electron debugging endpoint did not expose a renderer within 30 seconds.');
}

function connect(url) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(url);
    socket.addEventListener('open', () => resolve(socket), { once: true });
    socket.addEventListener('error', () => reject(new Error('Could not connect to Electron renderer.')), {
      once: true,
    });
  });
}

function rpc(socket, id, method, params = {}) {
  return new Promise((resolve, reject) => {
    const onMessage = (event) => {
      const message = JSON.parse(String(event.data));
      if (message.id !== id) return;
      socket.removeEventListener('message', onMessage);
      if (message.error) reject(new Error(`${method} failed: ${message.error.message}`));
      else resolve(message.result);
    };
    socket.addEventListener('message', onMessage);
    socket.send(JSON.stringify({ id, method, params }));
  });
}

function waitForExit(process, timeoutMs) {
  if (process.exitCode !== null) return Promise.resolve(process.exitCode);
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Meowtoko E7 Tool did not close within the timeout.')), timeoutMs);
    process.once('exit', (code) => {
      clearTimeout(timer);
      resolve(code);
    });
  });
}

async function main() {
  const [executableArgument, userDataArgument] = process.argv.slice(2);
  assert.ok(executableArgument && userDataArgument, 'Usage: verify-installed-cuda.cjs <E7Hub.exe> <user-data>');
  const executable = path.resolve(executableArgument);
  const userData = path.resolve(userDataArgument);
  const port = await freePort();
  const environment = { ...process.env };
  delete environment.E7_DESKTOP_SMOKE_TEST;
  delete environment.E7_SINGLE_INSTANCE_SMOKE_TEST;
  delete environment.E7_DISABLE_CUDA;

  const app = spawn(
    executable,
    [`--user-data-dir=${userData}`, `--remote-debugging-port=${port}`],
    {
      cwd: userData,
      env: environment,
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  );
  let stdout = '';
  let stderr = '';
  app.stdout.on('data', (chunk) => { stdout += chunk; });
  app.stderr.on('data', (chunk) => { stderr += chunk; });

  let socket;
  try {
    const page = await debuggerPage(port);
    socket = await connect(page.webSocketDebuggerUrl);
    const expression = `
      (async () => {
        const deadline = Date.now() + 60000;
        let snapshot = await window.e7.getHealth();
        while (snapshot.overall === 'checking' && Date.now() < deadline) {
          await new Promise((resolve) => setTimeout(resolve, 100));
          snapshot = await window.e7.getHealth();
        }
        return snapshot;
      })()
    `;
    const evaluated = await rpc(socket, 1, 'Runtime.evaluate', {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    assert.equal(evaluated.exceptionDetails, undefined, 'Health evaluation raised in the renderer.');
    const health = evaluated.result.value;
    const cuda = health.capabilities.find((capability) => capability.id === 'cuda');
    assert.ok(cuda, 'Health snapshot did not contain the CUDA capability.');
    assert.equal(cuda.state, 'ready', cuda.detail || cuda.summary);
    assert.equal(cuda.metadata.mode, 'cuda');
    assert.equal(cuda.metadata.allocationProbeSucceeded, true);
    assert.equal(cuda.metadata.component.installed, true);
    assert.match(String(cuda.metadata.deviceName), /RTX 5090/i);
    console.log(
      `E7_INSTALLED_CUDA_OK device=${JSON.stringify(cuda.metadata.deviceName)} `
      + `cupy=${cuda.version} runtime=${cuda.metadata.runtimeVersion} `
      + `driver=${cuda.metadata.driverVersion} probeBytes=${cuda.metadata.allocationProbeBytes}`,
    );
    await rpc(socket, 2, 'Runtime.evaluate', { expression: 'window.close()' });
    const exitCode = await waitForExit(app, 15_000);
    assert.equal(exitCode, 0, `Meowtoko E7 Tool exited ${exitCode}.\n${stdout}\n${stderr}`);
  } finally {
    if (socket) socket.close();
    if (app.exitCode === null) app.kill();
  }
}

main().catch((error) => {
  console.error(error.stack || error.message || String(error));
  process.exitCode = 1;
});
