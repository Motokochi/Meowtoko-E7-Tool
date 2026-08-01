import { app } from 'electron';
import path from 'node:path';

import type { BackendLaunch } from './backend-client';

const PACKAGED_ENVIRONMENT_KEYS = [
  'E7_BACKEND_EXECUTABLE',
  'E7_CUDA_INSTALLER_PYTHON',
  'E7_DOCUMENTS_DIR',
  'E7_NVIDIA_SMI_PATH',
  'E7_PROJECT_ROOT',
  'E7_PYTHON',
  'E7_SETTINGS_PATH',
  'E7_USER_DATA_DIR',
  'E7_RESOURCES_PATH',
] as const;

export interface BackendLaunchContext {
  isPackaged: boolean;
  resourcesPath: string;
  userDataPath: string;
  documentsPath: string;
  environment: NodeJS.ProcessEnv;
  cwd: string;
  platform: NodeJS.Platform;
}

function defaultContext(): BackendLaunchContext {
  return {
    isPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    userDataPath: app.getPath('userData'),
    documentsPath: app.getPath('documents'),
    environment: process.env,
    cwd: process.cwd(),
    platform: process.platform,
  };
}

export function resolveBackendLaunch(context: BackendLaunchContext = defaultContext()): BackendLaunch {
  if (context.isPackaged) {
    return {
      command: path.join(context.resourcesPath, 'backend', 'e7-core.exe'),
      args: [],
      environment: {
        E7_USER_DATA_DIR: context.userDataPath,
        E7_DOCUMENTS_DIR: context.documentsPath,
        E7_RESOURCES_PATH: context.resourcesPath,
      },
      unsetEnvironment: PACKAGED_ENVIRONMENT_KEYS,
    };
  }

  const repositoryRoot = context.environment.E7_PROJECT_ROOT ?? path.resolve(context.cwd, '..');
  const developmentUserData = context.environment.E7_USER_DATA_DIR?.trim()
    || path.join(repositoryRoot, '.local', 'user-data');
  const developmentEnvironment = {
    E7_USER_DATA_DIR: developmentUserData,
    E7_DOCUMENTS_DIR: context.documentsPath,
  };
  const packagedExecutable = context.environment.E7_BACKEND_EXECUTABLE;
  if (packagedExecutable) {
    return {
      command: packagedExecutable,
      args: [],
      cwd: repositoryRoot,
      environment: developmentEnvironment,
    };
  }

  const python = context.environment.E7_PYTHON ?? (context.platform === 'win32' ? 'py' : 'python3');
  const pythonExecutable = path.basename(python).toLowerCase();
  const launcherArgs = pythonExecutable === 'py' || pythonExecutable === 'py.exe'
    ? ['-3.13']
    : [];

  return {
    command: python,
    args: [...launcherArgs, '-u', '-m', 'src.desktop.backend'],
    cwd: repositoryRoot,
    environment: developmentEnvironment,
  };
}
