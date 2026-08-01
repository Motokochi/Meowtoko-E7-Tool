import type { HealthSnapshot } from './health';

export const ENHANCEMENT_MODE_IDS = ['adb'] as const;
export const ENHANCEMENT_STATES = ['idle', 'running', 'cancelling', 'succeeded', 'failed', 'cancelled'] as const;

export type EnhancementModeId = typeof ENHANCEMENT_MODE_IDS[number];
export type EnhancementState = typeof ENHANCEMENT_STATES[number];

const TERMINAL_ENHANCEMENT_STATES = new Set<EnhancementState>(['succeeded', 'failed', 'cancelled']);

export interface EnhancementMode {
  id: EnhancementModeId;
  label: string;
  description: string;
  requiredCapabilities: Array<'packet' | 'adb'>;
}

export interface EnhancementOptions {
  modes: EnhancementMode[];
  maxRetainedLogs: number;
}

export interface EnhancementStartOptions {
  mode: EnhancementModeId;
  allowDestroy: boolean;
  maxPieces: number | null;
}

export interface EnhancementDecision {
  action: 'enhance' | 'lock' | 'destroy' | 'stop';
  reason: string;
  currentGs: number;
  potentialGs: number;
  enhancement: number;
  nextTarget: number | null;
}

export interface EnhancementResult {
  outcome: 'completed' | 'stopped';
  processedPieces: number;
  currentPiece: number;
  lastDecision: EnhancementDecision | null;
  debugAvailable: boolean;
}

export interface EnhancementSnapshot {
  state: EnhancementState;
  stage: string;
  message: string;
  progress: number;
  pieceNumber: number;
  logs: string[];
  jobId?: string;
  options?: EnhancementStartOptions;
  lastDecision?: EnhancementDecision;
  result?: EnhancementResult;
  error?: string;
}

export interface EnhancementDebug {
  available: boolean;
  artifacts: string[];
  jobId?: string;
  text?: string;
  details?: Record<string, unknown>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function hasOnlyKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return Object.keys(value).every((key) => keys.includes(key));
}

function isSafeArtifact(value: unknown): value is string {
  return typeof value === 'string'
    && value.length > 0
    && !value.startsWith('/')
    && !value.startsWith('\\')
    && !/^[A-Za-z]:/.test(value)
    && !value.split(/[\\/]/).includes('..');
}

export function isEnhancementModeId(value: unknown): value is EnhancementModeId {
  return typeof value === 'string' && (ENHANCEMENT_MODE_IDS as readonly string[]).includes(value);
}

export function isEnhancementStartOptions(value: unknown): value is EnhancementStartOptions {
  if (!isRecord(value) || !hasOnlyKeys(value, ['mode', 'allowDestroy', 'maxPieces'])) return false;
  return isEnhancementModeId(value.mode)
    && typeof value.allowDestroy === 'boolean'
    && (value.maxPieces === null
      || (typeof value.maxPieces === 'number'
        && Number.isSafeInteger(value.maxPieces)
        && value.maxPieces >= 1
        && value.maxPieces <= 1_000_000));
}

function isDecision(value: unknown): value is EnhancementDecision {
  if (!isRecord(value)) return false;
  return ['enhance', 'lock', 'destroy', 'stop'].includes(String(value.action))
    && typeof value.reason === 'string'
    && typeof value.currentGs === 'number'
    && typeof value.potentialGs === 'number'
    && typeof value.enhancement === 'number'
    && (value.nextTarget === null || typeof value.nextTarget === 'number');
}

function isResult(value: unknown): value is EnhancementResult {
  if (!isRecord(value)) return false;
  return (value.outcome === 'completed' || value.outcome === 'stopped')
    && Number.isSafeInteger(value.processedPieces) && Number(value.processedPieces) >= 0
    && Number.isSafeInteger(value.currentPiece) && Number(value.currentPiece) >= 0
    && (value.lastDecision === null || isDecision(value.lastDecision))
    && typeof value.debugAvailable === 'boolean';
}

export function isEnhancementOptions(value: unknown): value is EnhancementOptions {
  if (!isRecord(value) || !Array.isArray(value.modes)
    || !Number.isSafeInteger(value.maxRetainedLogs) || Number(value.maxRetainedLogs) < 1) return false;
  return value.modes.length === 1 && value.modes.every((mode) => isRecord(mode)
    && isEnhancementModeId(mode.id)
    && typeof mode.label === 'string'
    && typeof mode.description === 'string'
    && Array.isArray(mode.requiredCapabilities)
    && mode.requiredCapabilities.every((id) => id === 'packet' || id === 'adb'));
}

export function isEnhancementSnapshot(value: unknown): value is EnhancementSnapshot {
  if (!isRecord(value)
    || !(ENHANCEMENT_STATES as readonly unknown[]).includes(value.state)
    || typeof value.stage !== 'string'
    || typeof value.message !== 'string'
    || typeof value.progress !== 'number' || value.progress < 0 || value.progress > 1
    || !Number.isSafeInteger(value.pieceNumber) || Number(value.pieceNumber) < 0
    || !Array.isArray(value.logs) || !value.logs.every((log) => typeof log === 'string')) return false;
  if (value.state !== 'idle' && (typeof value.jobId !== 'string' || !value.jobId)) return false;
  if (value.options !== undefined && !isEnhancementStartOptions(value.options)) return false;
  if (value.lastDecision !== undefined && !isDecision(value.lastDecision)) return false;
  if (value.result !== undefined && !isResult(value.result)) return false;
  return value.error === undefined || typeof value.error === 'string';
}

export function shouldAcceptEnhancementSnapshot(
  current: EnhancementSnapshot | null,
  next: EnhancementSnapshot,
): boolean {
  if (!current || current.jobId !== next.jobId) return true;
  return !TERMINAL_ENHANCEMENT_STATES.has(current.state);
}

export function isEnhancementDebug(value: unknown): value is EnhancementDebug {
  if (!isRecord(value) || typeof value.available !== 'boolean'
    || !Array.isArray(value.artifacts) || !value.artifacts.every(isSafeArtifact)) return false;
  if (value.jobId !== undefined && typeof value.jobId !== 'string') return false;
  if (value.text !== undefined && typeof value.text !== 'string') return false;
  return value.details === undefined || isRecord(value.details);
}

export function parseMaxPieces(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed || trimmed === '0') return null;
  const parsed = Number(trimmed);
  return Number.isSafeInteger(parsed) && parsed >= 1 && parsed <= 1_000_000 ? parsed : null;
}

export function maxPiecesError(value: string): string | undefined {
  const trimmed = value.trim();
  if (!trimmed || trimmed === '0') return undefined;
  const parsed = Number(trimmed);
  return Number.isSafeInteger(parsed) && parsed >= 1 && parsed <= 1_000_000
    ? undefined
    : 'Enter 0 for unlimited or a whole number from 1 to 1,000,000.';
}

export function requiresDestroyConfirmation(allowDestroy: boolean): boolean {
  return allowDestroy;
}

export function enhancementReadiness(
  health: HealthSnapshot | null,
  options: EnhancementOptions,
  modeId: EnhancementModeId,
): { available: boolean; reason?: string } {
  const mode = options.modes.find((item) => item.id === modeId);
  if (!mode) return { available: false, reason: 'This automation mode is unavailable.' };
  if (!health) return { available: false, reason: 'Local capability checks are still loading.' };
  const missing = mode.requiredCapabilities.filter((id) =>
    health.capabilities.find((capability) => capability.id === id)?.state !== 'ready');
  if (missing.length === 0) return { available: true };
  return {
    available: false,
    reason: `${mode.label} needs ${missing.join(', ')}. Prepare ${missing.length === 1 ? 'it' : 'them'} in Health Center.`,
  };
}
