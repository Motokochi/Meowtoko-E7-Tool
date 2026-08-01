import type { E7DesktopApi } from './shared/protocol';

declare global {
  interface Window {
    e7: E7DesktopApi;
  }
}

export {};
