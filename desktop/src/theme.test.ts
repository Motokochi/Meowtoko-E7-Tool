import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  readThemePreference,
  resolveTheme,
  THEME_STORAGE_KEY,
  writeThemePreference,
  type ThemeStorage,
} from './theme';

class MemoryStorage implements ThemeStorage {
  readonly values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }
}

test('defaults to system and resolves the operating-system preference', () => {
  assert.equal(readThemePreference(null), 'system');
  assert.equal(resolveTheme('system', true), 'dark');
  assert.equal(resolveTheme('system', false), 'light');
  assert.equal(resolveTheme('light', true), 'light');
});

test('persists only supported theme preferences through the storage contract', () => {
  const storage = new MemoryStorage();
  writeThemePreference(storage, 'dark');

  assert.equal(storage.values.get(THEME_STORAGE_KEY), 'dark');
  assert.equal(readThemePreference(storage), 'dark');

  storage.values.set(THEME_STORAGE_KEY, 'neon');
  assert.equal(readThemePreference(storage), 'system');
});

test('blocked storage falls back without preventing startup', () => {
  const blocked: ThemeStorage = {
    getItem: () => { throw new Error('blocked'); },
    setItem: () => { throw new Error('blocked'); },
  };

  assert.equal(readThemePreference(blocked), 'system');
  assert.doesNotThrow(() => writeThemePreference(blocked, 'light'));
});
