import type { IconName } from './icons';

export type PageId = 'overview' | 'health' | 'gear' | 'analyzer' | 'enhancer' | 'importer' | 'optimizer' | 'settings';

export interface NavigationItem {
  id: PageId;
  label: string;
  description: string;
  icon: IconName;
  enabled: boolean;
}

export const NAVIGATION_ITEMS: readonly NavigationItem[] = [
  { id: 'overview', label: 'Overview', description: 'Application readiness', icon: 'overview', enabled: true },
  { id: 'health', label: 'Health Center', description: 'Local capabilities', icon: 'health', enabled: true },
  { id: 'gear', label: 'Gear', description: 'Owned +15 equipment', icon: 'gear', enabled: true },
  { id: 'analyzer', label: 'Analyzer', description: 'Gear capture and rating', icon: 'analyzer', enabled: true },
  { id: 'enhancer', label: 'Enhancer', description: 'Enhancement automation', icon: 'enhancer', enabled: true },
  { id: 'optimizer', label: 'Optimizer', description: 'Owned gear build search', icon: 'optimizer', enabled: true },
  { id: 'importer', label: 'Importer', description: 'Fribbels gear inventory', icon: 'importer', enabled: true },
  { id: 'settings', label: 'Settings', description: 'Application preferences', icon: 'settings', enabled: true },
];

const ENABLED_PAGE_IDS = new Set(
  NAVIGATION_ITEMS.filter((item) => item.enabled).map((item) => item.id),
);

export function isEnabledPageId(value: unknown): value is PageId {
  return typeof value === 'string' && ENABLED_PAGE_IDS.has(value as PageId);
}

export function pageIdFromHash(hash: string): PageId {
  const value = hash.replace(/^#\/?/, '').split(/[/?]/, 1)[0].toLowerCase();
  return isEnabledPageId(value) ? value : 'overview';
}

export function pageHash(pageId: PageId): string {
  return `#/${pageId}`;
}

export function navigationItem(pageId: PageId): NavigationItem {
  return NAVIGATION_ITEMS.find((item) => item.id === pageId) ?? NAVIGATION_ITEMS[0];
}
