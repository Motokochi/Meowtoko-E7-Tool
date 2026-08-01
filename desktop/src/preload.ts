import { contextBridge, ipcRenderer } from 'electron';

import { createDesktopApi } from './desktop-api';

const api = createDesktopApi(
  (channel, ...args) => ipcRenderer.invoke(channel, ...args) as Promise<unknown>,
  (channel, listener) => {
    const wrapped = (_event: Electron.IpcRendererEvent, payload: unknown) => listener(payload);
    ipcRenderer.on(channel, wrapped);
    return () => ipcRenderer.removeListener(channel, wrapped);
  },
);

contextBridge.exposeInMainWorld('e7', api);
