import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import {
  SETTINGS_THEMES,
  type SettingsThemePreference,
} from './shared/settings';

export type ThemePreference = SettingsThemePreference;
export type ResolvedTheme = 'light' | 'dark';

export const THEME_STORAGE_KEY = 'e7-hub.theme-preference';
export const THEME_PREFERENCES: readonly ThemePreference[] = SETTINGS_THEMES;

export interface ThemeStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export function isThemePreference(value: unknown): value is ThemePreference {
  return typeof value === 'string' && THEME_PREFERENCES.includes(value as ThemePreference);
}

export function readThemePreference(storage: ThemeStorage | null): ThemePreference {
  if (!storage) {
    return 'system';
  }
  try {
    const value = storage.getItem(THEME_STORAGE_KEY);
    return isThemePreference(value) ? value : 'system';
  } catch {
    return 'system';
  }
}

export function writeThemePreference(storage: ThemeStorage | null, value: ThemePreference): void {
  if (!storage) {
    return;
  }
  try {
    storage.setItem(THEME_STORAGE_KEY, value);
  } catch {
    // A blocked storage provider must not prevent the application from rendering.
  }
}

export function resolveTheme(preference: ThemePreference, prefersDark: boolean): ResolvedTheme {
  return preference === 'system' ? (prefersDark ? 'dark' : 'light') : preference;
}

function browserStorage(): ThemeStorage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage;
  } catch {
    return null;
  }
}

function browserPrefersDark(): boolean {
  return typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-color-scheme: dark)').matches;
}

interface ThemeContextValue {
  preference: ThemePreference;
  resolved: ResolvedTheme;
  setPreference(value: ThemePreference): void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

interface ThemeProviderProps {
  children: ReactNode;
  storage?: ThemeStorage | null;
}

export function ThemeProvider({ children, storage }: ThemeProviderProps): React.JSX.Element {
  const selectedStorage = storage === undefined ? browserStorage() : storage;
  const [preference, setPreferenceState] = useState<ThemePreference>(
    () => readThemePreference(selectedStorage),
  );
  const [prefersDark, setPrefersDark] = useState(browserPrefersDark);
  const resolved = resolveTheme(preference, prefersDark);

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return undefined;
    }
    const query = window.matchMedia('(prefers-color-scheme: dark)');
    const update = (event: MediaQueryListEvent): void => setPrefersDark(event.matches);
    setPrefersDark(query.matches);
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);

  useEffect(() => {
    if (typeof document === 'undefined') {
      return;
    }
    document.documentElement.dataset.theme = resolved;
    document.documentElement.style.colorScheme = resolved;
  }, [resolved]);

  const setPreference = useCallback((next: ThemePreference) => {
    setPreferenceState(next);
    writeThemePreference(selectedStorage, next);
  }, [selectedStorage]);

  const value = useMemo<ThemeContextValue>(() => ({
    preference,
    resolved,
    setPreference,
  }), [preference, resolved, setPreference]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const value = useContext(ThemeContext);
  if (!value) {
    throw new Error('useTheme must be used inside ThemeProvider.');
  }
  return value;
}
