export const HEALTH_CAPABILITY_IDS = [
  'backend',
  'storage',
  'tesseract',
  'ollama',
  'cuda',
  'packet',
  'adb',
] as const;

export type HealthCapabilityId = typeof HEALTH_CAPABILITY_IDS[number];
export type HealthCapabilityState =
  | 'checking'
  | 'ready'
  | 'degraded'
  | 'unavailable'
  | 'error'
  | 'in_progress';
export type OverallHealthState = 'checking' | 'ready' | 'degraded' | 'error';
export type HealthOperationState = 'running' | 'succeeded' | 'failed' | 'cancelled';
export type HealthActionKind = 'retry' | 'start' | 'download' | 'install' | 'repair';

export const HEALTH_ACTION_IDS = [
  'ollama.start',
  'ollama.pull_model',
  'ollama.install',
  'tesseract.install',
  'packet.install',
  'adb.install',
  'cuda.install',
  'cuda.repair',
  'health.cancel',
] as const;

export type HealthActionId = typeof HEALTH_ACTION_IDS[number];

export interface HealthAction {
  id: HealthActionId;
  label: string;
  kind: HealthActionKind;
}

export interface HealthCapability {
  id: HealthCapabilityId;
  title: string;
  state: HealthCapabilityState;
  summary: string;
  required: boolean;
  detail?: string;
  version?: string;
  path?: string;
  actions: HealthAction[];
  metadata: Record<string, unknown>;
}

export interface HealthOperation {
  id: string;
  actionId: string;
  state: HealthOperationState;
  message: string;
  progress?: number;
  error?: string;
}

export interface HealthSnapshot {
  overall: OverallHealthState;
  checkedAt: string;
  capabilities: HealthCapability[];
  operation?: HealthOperation;
}

const CAPABILITY_STATES = new Set<HealthCapabilityState>([
  'checking', 'ready', 'degraded', 'unavailable', 'error', 'in_progress',
]);
const OVERALL_STATES = new Set<OverallHealthState>(['checking', 'ready', 'degraded', 'error']);
const OPERATION_STATES = new Set<HealthOperationState>(['running', 'succeeded', 'failed', 'cancelled']);
const ACTION_KINDS = new Set<HealthActionKind>(['retry', 'start', 'download', 'install', 'repair']);
const OPERATION_ACTION_IDS = new Set<string>(['health.refresh', ...HEALTH_ACTION_IDS]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function isHealthActionId(value: unknown): value is HealthActionId {
  return typeof value === 'string' && (HEALTH_ACTION_IDS as readonly string[]).includes(value);
}

export function aggregateHealthState(capabilities: readonly HealthCapability[]): OverallHealthState {
  if (capabilities.some((item) => item.state === 'checking' || item.state === 'in_progress')) {
    return 'checking';
  }
  if (capabilities.some((item) => item.required && (item.state === 'error' || item.state === 'unavailable'))) {
    return 'error';
  }
  if (capabilities.some((item) => item.state !== 'ready')) {
    return 'degraded';
  }
  return 'ready';
}

export function isHealthSnapshot(value: unknown): value is HealthSnapshot {
  if (!isRecord(value) || !OVERALL_STATES.has(value.overall as OverallHealthState)) {
    return false;
  }
  if (typeof value.checkedAt !== 'string'
    || !Number.isFinite(Date.parse(value.checkedAt))
    || !Array.isArray(value.capabilities)) {
    return false;
  }
  for (const item of value.capabilities) {
    if (!isRecord(item)
      || !(HEALTH_CAPABILITY_IDS as readonly unknown[]).includes(item.id)
      || typeof item.title !== 'string'
      || !CAPABILITY_STATES.has(item.state as HealthCapabilityState)
      || typeof item.summary !== 'string'
      || typeof item.required !== 'boolean'
      || !Array.isArray(item.actions)
      || !isRecord(item.metadata)) {
      return false;
    }
    if (!item.actions.every((action) => isRecord(action)
      && isHealthActionId(action.id)
      && typeof action.label === 'string'
      && ACTION_KINDS.has(action.kind as HealthActionKind))) {
      return false;
    }
  }
  if (value.operation !== undefined) {
    const operation = value.operation;
    if (!isRecord(operation)
      || typeof operation.id !== 'string'
      || typeof operation.actionId !== 'string'
      || !OPERATION_ACTION_IDS.has(operation.actionId)
      || !OPERATION_STATES.has(operation.state as HealthOperationState)
      || typeof operation.message !== 'string') {
      return false;
    }
    if (operation.progress !== undefined
      && (typeof operation.progress !== 'number' || operation.progress < 0 || operation.progress > 1)) {
      return false;
    }
    if (operation.error !== undefined && typeof operation.error !== 'string') {
      return false;
    }
  }
  return true;
}

const OPERATION_STATE_ORDER: Readonly<Record<HealthOperationState, number>> = {
  running: 0,
  succeeded: 1,
  failed: 1,
  cancelled: 1,
};

export function shouldAcceptHealthSnapshot(
  current: HealthSnapshot | null,
  incoming: HealthSnapshot,
): boolean {
  if (!current) return true;
  const currentTime = Date.parse(current.checkedAt);
  const incomingTime = Date.parse(incoming.checkedAt);
  if (incomingTime !== currentTime) return incomingTime > currentTime;
  if (current.operation?.id === incoming.operation?.id && current.operation && incoming.operation) {
    return OPERATION_STATE_ORDER[incoming.operation.state] >= OPERATION_STATE_ORDER[current.operation.state];
  }
  return incoming.operation !== undefined || current.operation === undefined;
}

export function overallHealthLabel(state: OverallHealthState): string {
  switch (state) {
    case 'ready': return 'All checked capabilities are ready';
    case 'degraded': return 'App ready with limited features';
    case 'error': return 'A required capability needs attention';
    default: return 'Checking this PC';
  }
}
