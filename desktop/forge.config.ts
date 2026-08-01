import type { ForgeConfig } from '@electron-forge/shared-types';
import { MakerSquirrel } from '@electron-forge/maker-squirrel';
import { MakerZIP } from '@electron-forge/maker-zip';
import { FusesPlugin } from '@electron-forge/plugin-fuses';
import { WebpackPlugin } from '@electron-forge/plugin-webpack';
import { FuseV1Options, FuseVersion } from '@electron/fuses';
import path from 'node:path';

import { mainConfig } from './webpack.main.config';
import { rendererConfig } from './webpack.renderer.config';

import packageMetadata from './package.json';

const appAssetsRoot = path.resolve(__dirname, '..', 'assets', 'app');
const iconIco = path.join(appAssetsRoot, 'meowtoko-e7-tool.ico');
const iconPng = path.join(appAssetsRoot, 'meowtoko-e7-tool.png');
const configuredForgeOutDir = process.env.E7_FORGE_OUT_DIR?.trim();
const forgeOutDir = configuredForgeOutDir
  ? path.resolve(__dirname, configuredForgeOutDir)
  : path.resolve(__dirname, '..', '.build', 'forge', `v${packageMetadata.version}`);

const config: ForgeConfig = {
  outDir: forgeOutDir,
  packagerConfig: {
    asar: true,
    appCopyright: `Copyright (c) 2026 ${packageMetadata.author}`,
    executableName: 'E7Hub',
    icon: iconIco,
    extraResource: [
      path.resolve(__dirname, '..', 'dist', 'backend'),
      path.resolve(__dirname, '..', 'dist', 'runtime'),
      path.resolve(__dirname, '..', 'dist', 'cuda-installer'),
      path.resolve(__dirname, '..', 'dist', 'characters'),
      iconPng,
    ],
    win32metadata: {
      CompanyName: packageMetadata.author,
      FileDescription: packageMetadata.description,
      InternalName: 'E7Hub',
      OriginalFilename: 'E7Hub.exe',
      ProductName: packageMetadata.productName,
      'requested-execution-level': 'asInvoker',
    },
  },
  makers: [
    new MakerSquirrel({
      authors: packageMetadata.author,
      copyright: `Copyright (c) 2026 ${packageMetadata.author}`,
      description: packageMetadata.description,
      exe: 'E7Hub.exe',
      fixUpPaths: true,
      iconUrl: 'https://raw.githubusercontent.com/Motokochi/Meowtoko-E7-Tool/main/assets/app/meowtoko-e7-tool.ico',
      name: 'E7Hub',
      noDelta: true,
      noMsi: true,
      owners: packageMetadata.author,
      setupExe: `Meowtoko-E7-Tool-${packageMetadata.version}-Setup.exe`,
      setupIcon: iconIco,
      title: packageMetadata.productName,
    }),
    new MakerZIP({}, ['win32']),
  ],
  plugins: [
    new WebpackPlugin({
      mainConfig,
      renderer: {
        config: rendererConfig,
        entryPoints: [
          {
            html: './src/index.html',
            js: './src/renderer.tsx',
            name: 'main_window',
            preload: {
              js: './src/preload.ts',
            },
          },
        ],
      },
    }),
    new FusesPlugin({
      version: FuseVersion.V1,
      [FuseV1Options.RunAsNode]: false,
      [FuseV1Options.EnableCookieEncryption]: true,
      [FuseV1Options.EnableNodeOptionsEnvironmentVariable]: false,
      [FuseV1Options.EnableNodeCliInspectArguments]: false,
      [FuseV1Options.EnableEmbeddedAsarIntegrityValidation]: true,
      [FuseV1Options.OnlyLoadAppFromAsar]: true,
    }),
  ],
};

export default config;
