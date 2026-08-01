import assert from 'node:assert/strict';
import path from 'node:path';
import { test } from 'node:test';

import { backendEnvironment } from './backend-client';
import { resolveBackendLaunch, type BackendLaunchContext } from './backend-launch';

function context(overrides: Partial<BackendLaunchContext> = {}): BackendLaunchContext {
  return {
    isPackaged: false,
    resourcesPath: path.join('C:', 'E7', 'resources'),
    userDataPath: path.join('C:', 'Users', 'test', 'AppData', 'Roaming', 'E7 Hub'),
    documentsPath: path.join('C:', 'Users', 'test', 'Documents'),
    environment: {},
    cwd: path.join('C:', 'repo', 'desktop'),
    platform: 'win32',
    ...overrides,
  };
}

test('packaged launch uses only the frozen backend and explicit private paths', () => {
  const selected = resolveBackendLaunch(context({
    isPackaged: true,
    environment: {
      E7_BACKEND_EXECUTABLE: 'C:\\malicious\\override.exe',
      E7_PYTHON: 'C:\\Python\\python.exe',
      E7_SETTINGS_PATH: 'C:\\repo\\custom-data\\settings.json',
      E7_CUDA_INSTALLER_PYTHON: 'C:\\malicious\\python.exe',
      E7_NVIDIA_SMI_PATH: 'C:\\malicious\\nvidia-smi.exe',
    },
  }));

  assert.equal(selected.command, path.join('C:', 'E7', 'resources', 'backend', 'e7-core.exe'));
  assert.deepEqual(selected.args, []);
  assert.deepEqual(selected.environment, {
    E7_USER_DATA_DIR: path.join('C:', 'Users', 'test', 'AppData', 'Roaming', 'E7 Hub'),
    E7_DOCUMENTS_DIR: path.join('C:', 'Users', 'test', 'Documents'),
    E7_RESOURCES_PATH: path.join('C:', 'E7', 'resources'),
  });
  const merged = backendEnvironment(selected, {
    PATH: 'C:\\Windows',
    E7_SETTINGS_PATH: 'C:\\repo\\custom-data\\settings.json',
    E7_PYTHON: 'python',
    E7_CUDA_INSTALLER_PYTHON: 'C:\\malicious\\python.exe',
    E7_NVIDIA_SMI_PATH: 'C:\\malicious\\nvidia-smi.exe',
  });
  assert.equal(merged.PATH, 'C:\\Windows');
  assert.equal(merged.E7_SETTINGS_PATH, undefined);
  assert.equal(merged.E7_PYTHON, undefined);
  assert.equal(merged.E7_CUDA_INSTALLER_PYTHON, undefined);
  assert.equal(merged.E7_NVIDIA_SMI_PATH, undefined);
  assert.equal(merged.E7_USER_DATA_DIR, selected.environment?.E7_USER_DATA_DIR);
  assert.equal(merged.E7_DOCUMENTS_DIR, selected.environment?.E7_DOCUMENTS_DIR);
});

test('development launch keeps interpreter and explicit executable overrides local to development', () => {
  const override = resolveBackendLaunch(context({ environment: { E7_BACKEND_EXECUTABLE: 'C:\\dev\\e7-core.exe' } }));
  assert.deepEqual(override, {
    command: 'C:\\dev\\e7-core.exe',
    args: [],
    cwd: path.join('C:', 'repo'),
    environment: {
      E7_USER_DATA_DIR: path.join('C:', 'repo', '.local', 'user-data'),
      E7_DOCUMENTS_DIR: path.join('C:', 'Users', 'test', 'Documents'),
    },
  });

  const interpreter = resolveBackendLaunch(context({
    environment: {
      E7_PYTHON: 'C:\\Python312\\python.exe',
      E7_PROJECT_ROOT: 'C:\\repo',
      E7_USER_DATA_DIR: 'C:\\isolated\\data',
    },
  }));
  assert.deepEqual(interpreter, {
    command: 'C:\\Python312\\python.exe',
    args: ['-u', '-m', 'src.desktop.backend'],
    cwd: 'C:\\repo',
    environment: {
      E7_USER_DATA_DIR: 'C:\\isolated\\data',
      E7_DOCUMENTS_DIR: path.join('C:', 'Users', 'test', 'Documents'),
    },
  });

  const launcher = resolveBackendLaunch(context());
  assert.deepEqual(launcher.args, ['-3.13', '-u', '-m', 'src.desktop.backend']);
  assert.deepEqual(launcher.environment, {
    E7_USER_DATA_DIR: path.join('C:', 'repo', '.local', 'user-data'),
    E7_DOCUMENTS_DIR: path.join('C:', 'Users', 'test', 'Documents'),
  });
});
