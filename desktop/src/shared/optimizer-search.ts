import type { OptimizerHeroDraft } from './optimizer-profile';

export const OPTIMIZER_SEARCH_STATES = [
  'idle',
  'preparing',
  'running',
  'completed',
  'overflowed',
  'cancelled',
  'failed',
] as const;

export type OptimizerSearchState = typeof OPTIMIZER_SEARCH_STATES[number];
export type OptimizerSearchBackend = 'cpu' | 'cuda';

export interface OptimizerSearchCategoryCounts {
  exact: string;
  oneAway: string;
  twoAway: string;
}

export interface OptimizerSearchFailure {
  stage: string;
  code: string;
  message: string;
  cpuRecoveryAvailable: boolean;
}

export interface OptimizerSearchSnapshot {
  sequence: number;
  jobId: string | null;
  requestId: string | null;
  state: OptimizerSearchState;
  backend: OptimizerSearchBackend | null;
  totalPermutations: string;
  searchedPermutations: string;
  categoryCounts: OptimizerSearchCategoryCounts;
  elapsedSeconds: number;
  canCancel: boolean;
  resultAvailable: boolean;
  resultRunId: string | null;
  failure: OptimizerSearchFailure | null;
}

export type OptimizerSearchStartDraft = OptimizerHeroDraft;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length
    && actual.every((key, index) => key === expected[index]);
}

function isText(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0 && value.length <= 240;
}

function isNullableText(value: unknown): value is string | null {
  return value === null || isText(value);
}

function isIdentifier(value: unknown): value is string {
  return typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(value);
}

function isNullableIdentifier(value: unknown): value is string | null {
  return value === null || isIdentifier(value);
}

function isSafeFailureMessage(value: unknown): value is string {
  return isText(value)
    && !/[\r\n]/.test(value)
    && !/\\/.test(value)
    && !/(?:^|\s)\/\S+/.test(value)
    && !/[A-Za-z]:[\\/]/.test(value)
    && !/(?:file:|\/Users\/|\/home\/)/i.test(value);
}

function isDecimal(value: unknown): value is string {
  return typeof value === 'string'
    && /^(0|[1-9][0-9]{0,77})$/.test(value);
}

function isCounts(value: unknown): value is OptimizerSearchCategoryCounts {
  return isRecord(value)
    && hasExactKeys(value, ['exact', 'oneAway', 'twoAway'])
    && isDecimal(value.exact)
    && isDecimal(value.oneAway)
    && isDecimal(value.twoAway);
}

function isFailure(value: unknown): value is OptimizerSearchFailure {
  return isRecord(value)
    && hasExactKeys(value, ['stage', 'code', 'message', 'cpuRecoveryAvailable'])
    && typeof value.stage === 'string'
    && /^[a-z0-9][a-z0-9._-]{0,63}$/.test(value.stage)
    && typeof value.code === 'string'
    && /^[a-z0-9][a-z0-9._-]{0,63}$/.test(value.code)
    && isSafeFailureMessage(value.message)
    && typeof value.cpuRecoveryAvailable === 'boolean';
}

export function isOptimizerSearchSnapshot(value: unknown): value is OptimizerSearchSnapshot {
  if (!isRecord(value)
    || !hasExactKeys(value, [
      'sequence', 'jobId', 'requestId', 'state', 'backend',
      'totalPermutations', 'searchedPermutations', 'categoryCounts',
      'elapsedSeconds', 'canCancel', 'resultAvailable', 'resultRunId', 'failure',
    ])
    || !Number.isSafeInteger(value.sequence)
    || Number(value.sequence) < 0
    || !isNullableIdentifier(value.jobId)
    || !isNullableIdentifier(value.requestId)
    || !OPTIMIZER_SEARCH_STATES.includes(value.state as OptimizerSearchState)
    || (value.backend !== null && value.backend !== 'cpu' && value.backend !== 'cuda')
    || !isDecimal(value.totalPermutations)
    || !isDecimal(value.searchedPermutations)
    || !isCounts(value.categoryCounts)
    || typeof value.elapsedSeconds !== 'number'
    || !Number.isFinite(value.elapsedSeconds)
    || value.elapsedSeconds < 0
    || typeof value.canCancel !== 'boolean'
    || typeof value.resultAvailable !== 'boolean'
    || !isNullableIdentifier(value.resultRunId)
    || (value.failure !== null && !isFailure(value.failure))) {
    return false;
  }
  const state = value.state as OptimizerSearchState;
  const total = BigInt(value.totalPermutations as string);
  const searched = BigInt(value.searchedPermutations as string);
  const accepted = BigInt((value.categoryCounts as OptimizerSearchCategoryCounts).exact)
    + BigInt((value.categoryCounts as OptimizerSearchCategoryCounts).oneAway)
    + BigInt((value.categoryCounts as OptimizerSearchCategoryCounts).twoAway);
  if (searched > total || accepted > searched) return false;
  const active = state === 'preparing' || state === 'running';
  const completed = state === 'completed';
  const failed = state === 'failed';
  if (state === 'idle') {
    return value.sequence === 0
      && total === 0n
      && searched === 0n
      && accepted === 0n
      && value.jobId === null
      && value.requestId === null
      && value.backend === null
      && value.canCancel === false
      && value.resultAvailable === false
      && value.resultRunId === null
      && value.failure === null;
  }
  return value.jobId !== null
    && value.requestId !== null
    && (!active || value.failure === null)
    && (!completed || (value.resultAvailable && value.resultRunId !== null))
    && (completed || (!value.resultAvailable && value.resultRunId === null))
    && (failed ? value.failure !== null : value.failure === null)
    && (!value.canCancel || active);
}

export function formatOptimizerSearchCount(value: string): string {
  return new Intl.NumberFormat().format(BigInt(value));
}

export function optimizerSearchProgress(snapshot: OptimizerSearchSnapshot): number {
  const total = BigInt(snapshot.totalPermutations);
  if (total === 0n) return 0;
  const searched = BigInt(snapshot.searchedPermutations);
  const basisPoints = (searched > total ? total : searched) * 10_000n / total;
  return Number(basisPoints) / 100;
}
