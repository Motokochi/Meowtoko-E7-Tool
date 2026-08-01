export type SettingsThemePreference = 'system' | 'light' | 'dark';

export const SETTINGS_THEMES: readonly SettingsThemePreference[] = ['system', 'light', 'dark'];
export const REGION_IDS = ['enhance', 'slot', 'mainStat', 'set', 'subs'] as const;
export const CLICK_POINT_IDS = [
  'lock', 'back', 'nextPiece', 'openEnhance', 'destroy', 'destroyConfirm', 'enhance', 'autoSelect',
  'probeIngredient', 'probeSelect',
] as const;
export const LEVEL_IDS = ['+3', '+6', '+9', '+12', '+15'] as const;
export const AUTOMATION_IDS = [
  'afterAutoSelectSeconds',
  'afterLevelSelectSeconds',
  'afterEnhanceSeconds',
  'afterDestroySeconds',
  'afterDestroyConfirmSeconds',
  'afterLockSeconds',
  'afterBackSeconds',
  'afterNextPieceSeconds',
  'afterOpenEnhanceSeconds',
  'afterRewardPopupSeconds',
  'enhancementPacketTimeoutSeconds',
  'afterEnhancementRetrySeconds',
  'enhancementReadRetries',
] as const;

export type RegionId = typeof REGION_IDS[number];
export type ClickPointId = typeof CLICK_POINT_IDS[number];
export type LevelId = typeof LEVEL_IDS[number];
export type AutomationId = typeof AUTOMATION_IDS[number];

export interface CoordinatePoint {
  x: number;
  y: number;
}

export interface CaptureRegion extends CoordinatePoint {
  width: number;
  height: number;
}

export interface DesktopSettings {
  targetWindow: string;
  appearance: {
    theme: SettingsThemePreference;
  };
  regions: Record<RegionId, CaptureRegion>;
  clickPoints: Record<ClickPointId, CoordinatePoint> & {
    levels: Record<LevelId, CoordinatePoint>;
  };
  automation: Record<AutomationId, number>;
  adb: {
    adbPath: string;
    deviceSerial: string;
    coordinateWidth: number;
    coordinateHeight: number;
    commandTimeoutSeconds: number;
  };
}

export type SettingsSource = 'file' | 'backup' | 'defaults';

export interface SettingsSnapshot {
  schemaVersion: number;
  revision: string;
  source: SettingsSource;
  readOnly: boolean;
  migratedFrom?: number;
  warning?: string;
  settings: DesktopSettings;
}

export type DeepPartial<T> = {
  [Key in keyof T]?: T[Key] extends Record<string, unknown>
    ? DeepPartial<T[Key]>
    : T[Key];
};

export type SettingsPatch = DeepPartial<DesktopSettings>;
export type SettingsValidationIssues = Record<string, string>;
export type SettingsPreviewSource = 'adb';
export type SettingsPreviewTarget =
  | { kind: 'region'; id: RegionId }
  | { kind: 'point'; id: ClickPointId }
  | { kind: 'level'; id: LevelId };

export interface SettingsPreviewRequest {
  source: SettingsPreviewSource;
  target: SettingsPreviewTarget;
}

export interface SettingsPreview {
  source: SettingsPreviewSource;
  kind: SettingsPreviewTarget['kind'];
  itemId: string;
  label: string;
  width: number;
  height: number;
  dataUrl: string;
}

export const DEFAULT_DESKTOP_SETTINGS: DesktopSettings = {
  targetWindow: 'Epic Seven',
  appearance: { theme: 'system' },
  regions: {
    enhance: { x: 105, y: 110, width: 35, height: 30 },
    slot: { x: 140, y: 135, width: 250, height: 30 },
    mainStat: { x: 70, y: 235, width: 300, height: 40 },
    set: { x: 80, y: 460, width: 250, height: 35 },
    subs: { x: 40, y: 300, width: 330, height: 100 },
  },
  clickPoints: {
    lock: { x: 203, y: 680 },
    back: { x: 35, y: 45 },
    nextPiece: { x: 200, y: 220 },
    openEnhance: { x: 1150, y: 700 },
    destroy: { x: 346, y: 680 },
    destroyConfirm: { x: 760, y: 550 },
    enhance: { x: 695, y: 680 },
    autoSelect: { x: 1060, y: 680 },
    probeIngredient: { x: 1060, y: 170 },
    probeSelect: { x: 640, y: 490 },
    levels: {
      '+3': { x: 1060, y: 600 },
      '+6': { x: 1060, y: 550 },
      '+9': { x: 1060, y: 490 },
      '+12': { x: 1060, y: 430 },
      '+15': { x: 1060, y: 370 },
    },
  },
  automation: {
    afterAutoSelectSeconds: 0.6,
    afterLevelSelectSeconds: 0.4,
    afterEnhanceSeconds: 2,
    afterDestroySeconds: 0.6,
    afterDestroyConfirmSeconds: 1,
    afterLockSeconds: 0.4,
    afterBackSeconds: 0.8,
    afterNextPieceSeconds: 0.6,
    afterOpenEnhanceSeconds: 0.8,
    afterRewardPopupSeconds: 0.6,
    enhancementPacketTimeoutSeconds: 2,
    afterEnhancementRetrySeconds: 0.8,
    enhancementReadRetries: 2,
  },
  adb: {
    adbPath: 'adb',
    deviceSerial: '',
    coordinateWidth: 1280,
    coordinateHeight: 720,
    commandTimeoutSeconds: 10,
  },
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function unsupportedKeys(
  value: Record<string, unknown>,
  allowed: readonly string[],
  path: string,
  issues: SettingsValidationIssues,
): void {
  for (const key of Object.keys(value)) {
    if (!allowed.includes(key)) {
      issues[`${path}${key}`] = 'Unsupported field.';
    }
  }
}

function integerIssue(value: unknown, minimum: number, maximum: number): string | undefined {
  if (typeof value !== 'number' || !Number.isInteger(value)) {
    return 'Must be a whole number.';
  }
  return value < minimum || value > maximum
    ? `Must be between ${minimum} and ${maximum}.`
    : undefined;
}

function validatePoint(
  value: unknown,
  path: string,
  issues: SettingsValidationIssues,
  region = false,
): void {
  const allowed = region ? ['x', 'y', 'width', 'height'] : ['x', 'y'];
  if (!isRecord(value)) {
    issues[path] = 'Coordinates are required.';
    return;
  }
  unsupportedKeys(value, allowed, `${path}.`, issues);
  for (const axis of ['x', 'y'] as const) {
    const issue = integerIssue(value[axis], 0, 100_000);
    if (issue) issues[`${path}.${axis}`] = issue;
  }
  if (region) {
    for (const axis of ['width', 'height'] as const) {
      const issue = integerIssue(value[axis], 1, 100_000);
      if (issue) issues[`${path}.${axis}`] = issue;
    }
  }
}

export function validateDesktopSettings(value: unknown): SettingsValidationIssues {
  const issues: SettingsValidationIssues = {};
  if (!isRecord(value)) {
    return { settings: 'Settings must be an object.' };
  }
  unsupportedKeys(value, ['targetWindow', 'appearance', 'regions', 'clickPoints', 'automation', 'adb'], '', issues);

  if (typeof value.targetWindow !== 'string' || !value.targetWindow.trim()) {
    issues.targetWindow = 'Target window is required.';
  } else if (value.targetWindow.length > 200) {
    issues.targetWindow = 'Use 200 characters or fewer.';
  }

  if (!isRecord(value.appearance)) {
    issues.appearance = 'Appearance settings are required.';
  } else {
    unsupportedKeys(value.appearance, ['theme'], 'appearance.', issues);
    if (!SETTINGS_THEMES.includes(value.appearance.theme as SettingsThemePreference)) {
      issues['appearance.theme'] = 'Choose system, light, or dark.';
    }
  }

  if (!isRecord(value.regions)) {
    issues.regions = 'Capture regions are required.';
  } else {
    unsupportedKeys(value.regions, REGION_IDS, 'regions.', issues);
    for (const id of REGION_IDS) validatePoint(value.regions[id], `regions.${id}`, issues, true);
  }

  if (!isRecord(value.clickPoints)) {
    issues.clickPoints = 'Click points are required.';
  } else {
    unsupportedKeys(value.clickPoints, [...CLICK_POINT_IDS, 'levels'], 'clickPoints.', issues);
    for (const id of CLICK_POINT_IDS) validatePoint(value.clickPoints[id], `clickPoints.${id}`, issues);
    const levels = value.clickPoints.levels;
    if (!isRecord(levels)) {
      issues['clickPoints.levels'] = 'Enhancement level points are required.';
    } else {
      unsupportedKeys(levels, LEVEL_IDS, 'clickPoints.levels.', issues);
      for (const id of LEVEL_IDS) validatePoint(levels[id], `clickPoints.levels.${id}`, issues);
    }
  }

  if (!isRecord(value.automation)) {
    issues.automation = 'Automation settings are required.';
  } else {
    unsupportedKeys(value.automation, AUTOMATION_IDS, 'automation.', issues);
    for (const id of AUTOMATION_IDS) {
      const item = value.automation[id];
      if (id === 'enhancementReadRetries') {
        const issue = integerIssue(item, 0, 20);
        if (issue) issues[`automation.${id}`] = issue;
      } else if (
        id === 'afterEnhanceSeconds'
        && (typeof item !== 'number' || !Number.isFinite(item) || item < 2 || item > 300)
      ) {
        issues[`automation.${id}`] = 'Must be between 2 and 300 seconds.';
      } else if (typeof item !== 'number' || !Number.isFinite(item) || item < 0 || item > 300) {
        issues[`automation.${id}`] = 'Must be between 0 and 300 seconds.';
      }
    }
  }

  if (!isRecord(value.adb)) {
    issues.adb = 'ADB settings are required.';
  } else {
    unsupportedKeys(
      value.adb,
      ['adbPath', 'deviceSerial', 'coordinateWidth', 'coordinateHeight', 'commandTimeoutSeconds'],
      'adb.',
      issues,
    );
    if (typeof value.adb.adbPath !== 'string' || !value.adb.adbPath.trim()) {
      issues['adb.adbPath'] = 'ADB path is required.';
    }
    if (typeof value.adb.deviceSerial !== 'string') {
      issues['adb.deviceSerial'] = 'Device serial must be text.';
    }
    for (const key of ['coordinateWidth', 'coordinateHeight'] as const) {
      const issue = integerIssue(value.adb[key], 1, 100_000);
      if (issue) issues[`adb.${key}`] = issue;
    }
    const timeout = value.adb.commandTimeoutSeconds;
    if (typeof timeout !== 'number' || !Number.isFinite(timeout) || timeout < 0.1 || timeout > 300) {
      issues['adb.commandTimeoutSeconds'] = 'Must be between 0.1 and 300 seconds.';
    }
  }
  return issues;
}

export function isDesktopSettings(value: unknown): value is DesktopSettings {
  return Object.keys(validateDesktopSettings(value)).length === 0;
}

function deepMerge(base: unknown, patch: unknown): unknown {
  if (!isRecord(base) || !isRecord(patch)) {
    return patch;
  }
  const result: Record<string, unknown> = { ...base };
  for (const [key, value] of Object.entries(patch)) {
    result[key] = key in base ? deepMerge(base[key], value) : value;
  }
  return result;
}

export function isSettingsPatch(value: unknown): value is SettingsPatch {
  return isRecord(value)
    && Object.keys(validateDesktopSettings(deepMerge(DEFAULT_DESKTOP_SETTINGS, value))).length === 0;
}

export function isSettingsSnapshot(value: unknown): value is SettingsSnapshot {
  return isRecord(value)
    && typeof value.schemaVersion === 'number'
    && Number.isInteger(value.schemaVersion)
    && value.schemaVersion >= 1
    && typeof value.revision === 'string'
    && value.revision.length > 0
    && (value.source === 'file' || value.source === 'backup' || value.source === 'defaults')
    && typeof value.readOnly === 'boolean'
    && (value.migratedFrom === undefined || (typeof value.migratedFrom === 'number' && Number.isInteger(value.migratedFrom)))
    && (value.warning === undefined || typeof value.warning === 'string')
    && isDesktopSettings(value.settings);
}

export function isSettingsPreviewRequest(value: unknown): value is SettingsPreviewRequest {
  if (!isRecord(value)
    || Object.keys(value).length !== 2
    || value.source !== 'adb'
    || !isRecord(value.target)
    || Object.keys(value.target).length !== 2) {
    return false;
  }
  const kind = value.target.kind;
  const id = value.target.id;
  return (kind === 'region' && REGION_IDS.includes(id as RegionId))
    || (kind === 'point' && CLICK_POINT_IDS.includes(id as ClickPointId))
    || (kind === 'level' && LEVEL_IDS.includes(id as LevelId));
}

export function isSettingsPreview(value: unknown): value is SettingsPreview {
  return isRecord(value)
    && value.source === 'adb'
    && (value.kind === 'region' || value.kind === 'point' || value.kind === 'level')
    && typeof value.itemId === 'string'
    && value.itemId.length > 0
    && typeof value.label === 'string'
    && value.label.length > 0
    && typeof value.width === 'number'
    && Number.isInteger(value.width)
    && value.width > 0
    && typeof value.height === 'number'
    && Number.isInteger(value.height)
    && value.height > 0
    && typeof value.dataUrl === 'string'
    && /^data:image\/png;base64,[A-Za-z0-9+/]+={0,2}$/.test(value.dataUrl);
}

export function cloneDesktopSettings(settings: DesktopSettings): DesktopSettings {
  return structuredClone(settings);
}

export function withLocalThemeFallback(
  snapshot: SettingsSnapshot,
  localTheme: SettingsThemePreference,
): SettingsSnapshot {
  if (snapshot.migratedFrom !== 0
    || snapshot.settings.appearance.theme !== 'system'
    || localTheme === 'system') {
    return snapshot;
  }
  return {
    ...snapshot,
    settings: {
      ...snapshot.settings,
      appearance: { theme: localTheme },
    },
  };
}
