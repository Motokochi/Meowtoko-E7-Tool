export const UPDATE_STATES = [
  'idle',
  'checking',
  'current',
  'available',
  'downloading',
  'downloaded',
  'applying',
  'error',
] as const;

export type UpdateState = typeof UPDATE_STATES[number];

export interface UpdateReleaseMetadata {
  version: string;
  title: string;
  notes: string;
  publishedAt: string;
  downloadBytes: string;
}

export interface UpdateProgress {
  kind: 'indeterminate' | 'determinate';
  percent?: number;
}

export interface UpdateSnapshot {
  state: UpdateState;
  currentVersion: string;
  checkedAt: string | null;
  release: UpdateReleaseMetadata | null;
  progress: UpdateProgress | null;
  installOnQuit: boolean;
  error: string | null;
}

export interface UpdateApplyRequest {
  unsavedChanges: boolean;
  confirmActiveWork: boolean;
}

export type UpdateApplyResult =
  | {
    status: 'confirmation-required';
    activeWork: string[];
    snapshot: UpdateSnapshot;
  }
  | {
    status: 'applying';
    activeWork: string[];
    snapshot: UpdateSnapshot;
  };

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function exact(value: Record<string, unknown>, fields: readonly string[]): boolean {
  const keys = Object.keys(value).sort();
  const expected = [...fields].sort();
  return keys.length === expected.length
    && keys.every((key, index) => key === expected[index]);
}

export function isStableVersion(value: unknown): value is string {
  return typeof value === 'string'
    && /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(value);
}

export function compareStableVersions(left: string, right: string): number {
  if (!isStableVersion(left) || !isStableVersion(right)) {
    throw new Error('Update version is not stable semantic versioning.');
  }
  const a = left.split('.').map(Number);
  const b = right.split('.').map(Number);
  for (let index = 0; index < 3; index += 1) {
    if (a[index] !== b[index]) return Math.sign(a[index] - b[index]);
  }
  return 0;
}

function isRelease(value: unknown): value is UpdateReleaseMetadata {
  return record(value)
    && exact(value, ['version', 'title', 'notes', 'publishedAt', 'downloadBytes'])
    && isStableVersion(value.version)
    && typeof value.title === 'string' && value.title.length > 0 && value.title.length <= 120
    && typeof value.notes === 'string' && value.notes.length <= 4_000
    && typeof value.publishedAt === 'string' && Number.isFinite(Date.parse(value.publishedAt))
    && typeof value.downloadBytes === 'string'
    && /^(?:[1-9]\d{0,15})$/.test(value.downloadBytes);
}

function isProgress(value: unknown): value is UpdateProgress {
  if (!record(value) || !['indeterminate', 'determinate'].includes(String(value.kind))) return false;
  if (value.kind === 'indeterminate') return exact(value, ['kind']);
  return exact(value, ['kind', 'percent'])
    && typeof value.percent === 'number'
    && Number.isFinite(value.percent)
    && value.percent >= 0
    && value.percent <= 100;
}

export function isUpdateSnapshot(value: unknown): value is UpdateSnapshot {
  if (!record(value) || !exact(value, [
    'state', 'currentVersion', 'checkedAt', 'release', 'progress', 'installOnQuit', 'error',
  ])) return false;
  if (!UPDATE_STATES.includes(value.state as UpdateState)
    || !isStableVersion(value.currentVersion)
    || (value.checkedAt !== null
      && (typeof value.checkedAt !== 'string' || !Number.isFinite(Date.parse(value.checkedAt))))
    || (value.release !== null && !isRelease(value.release))
    || (value.progress !== null && !isProgress(value.progress))
    || typeof value.installOnQuit !== 'boolean'
    || (value.error !== null && (typeof value.error !== 'string' || value.error.length > 240))) {
    return false;
  }
  const state = value.state as UpdateState;
  if (['available', 'downloading', 'downloaded', 'applying'].includes(state)
    && value.release === null) return false;
  if (state === 'downloading' && value.progress === null) return false;
  if (state !== 'downloading' && value.progress !== null) return false;
  return state === 'error' ? value.error !== null : value.error === null;
}

export function isUpdateApplyRequest(value: unknown): value is UpdateApplyRequest {
  return record(value)
    && exact(value, ['unsavedChanges', 'confirmActiveWork'])
    && typeof value.unsavedChanges === 'boolean'
    && typeof value.confirmActiveWork === 'boolean';
}

export function isUpdateApplyResult(value: unknown): value is UpdateApplyResult {
  if (!record(value) || !exact(value, ['status', 'activeWork', 'snapshot'])
    || !['confirmation-required', 'applying'].includes(String(value.status))
    || !Array.isArray(value.activeWork)
    || value.activeWork.length > 8
    || !value.activeWork.every((item) => typeof item === 'string' && item.length > 0 && item.length <= 80)
    || !isUpdateSnapshot(value.snapshot)) return false;
  return value.status !== 'applying' || value.snapshot.state === 'applying';
}

