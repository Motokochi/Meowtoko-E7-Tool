import {
  OPTIMIZER_DERIVED_METRICS,
  OPTIMIZER_PRIMARY_STATS,
  type OptimizerDerivedMetricId,
  type OptimizerPrimaryStatKey,
} from './optimizer-profile';

export type OptimizerResultCategoryFilter = 'all' | 'exact';
export type OptimizerResultCategory = Exclude<OptimizerResultCategoryFilter, 'all'>;
export type OptimizerResultDirection = 'ascending' | 'descending';
export type DecimalEndpoint = string | null;
export interface DecimalRange { minimum: DecimalEndpoint; maximum: DecimalEndpoint }
export interface FloatRange { minimum: number | null; maximum: number | null }

export interface OptimizerResultQuery {
  runId: string;
  category: OptimizerResultCategoryFilter;
  sortKey: string;
  direction: OptimizerResultDirection;
  pageIndex: number;
  pageSize: number;
  primaryRanges: Record<OptimizerPrimaryStatKey, DecimalRange>;
  derivedRanges: Record<OptimizerDerivedMetricId, DecimalRange>;
  priorityScore: FloatRange;
  constraintDistance: FloatRange;
  replacementCount: DecimalRange;
  equippedCount: DecimalRange;
}

export interface OptimizerResultFieldOption { fieldId: string; label: string; sortKey: string }
export interface OptimizerResultSortOption { sortKey: string; label: string }
export interface OptimizerResultOptions {
  maxPageSize: number;
  primaryFields: OptimizerResultFieldOption[];
  derivedFields: OptimizerResultFieldOption[];
  sortOptions: OptimizerResultSortOption[];
}

export interface OptimizerResultSetSummary {
  setId: string;
  label: string;
  pieces: number;
  activations: number;
}

export interface OptimizerResultRow {
  rowKey: string;
  category: OptimizerResultCategory;
  replacementCount: number;
  equippedCount: number;
  priorityScore: number;
  constraintDistance: number;
  primaryStats: Record<OptimizerPrimaryStatKey, string>;
  derivedMetrics: Record<OptimizerDerivedMetricId, string>;
  sets: OptimizerResultSetSummary[];
}

export type OptimizerResultState = 'idle' | 'running' | 'completed' | 'rerun-required' | 'cancelled' | 'failed';
export interface OptimizerResultSnapshot {
  sequence: number;
  state: OptimizerResultState;
  queryId: string | null;
  runId: string | null;
  stage: 'queued' | 'filtering' | 'sorting' | 'resolving' | null;
  scannedRows: string;
  totalRows: string;
  canCancel: boolean;
  categoryCounts: { exact: string; oneAway: string; twoAway: string };
  filteredRows: string | null;
  pageIndex: number;
  pageSize: number;
  pageCount: number;
  startOffset: string;
  endOffset: string;
  hasPrevious: boolean;
  hasNext: boolean;
  outOfRange: boolean;
  rows: OptimizerResultRow[];
  rerunReasons: string[];
  failure: { code: string; message: string } | null;
}

const DECIMAL = /^-?(?:0|[1-9][0-9]*)$/;
const NONNEGATIVE_DECIMAL = /^(?:0|[1-9][0-9]*)$/;
const PRIVATE_PATH = /(?:[A-Za-z]:[\\/]|\\\\|file:|(?:^|\s)\/\S+)/i;
const RESULT_KEYS = [
  'sequence', 'state', 'queryId', 'runId', 'stage', 'scannedRows', 'totalRows',
  'canCancel', 'categoryCounts', 'filteredRows', 'pageIndex', 'pageSize', 'pageCount',
  'startOffset', 'endOffset', 'hasPrevious', 'hasNext', 'outOfRange', 'rows',
  'rerunReasons', 'failure',
] as const;

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function exact(value: Record<string, unknown>, fields: readonly string[]): boolean {
  const keys = Object.keys(value);
  return keys.length === fields.length && fields.every((field) => Object.hasOwn(value, field));
}

function decimal(value: unknown): value is string {
  return typeof value === 'string' && DECIMAL.test(value);
}

function decimalRange(value: unknown): value is DecimalRange {
  return record(value) && exact(value, ['minimum', 'maximum'])
    && (value.minimum === null || decimal(value.minimum))
    && (value.maximum === null || decimal(value.maximum));
}

function floatRange(value: unknown): value is FloatRange {
  return record(value) && exact(value, ['minimum', 'maximum'])
    && (value.minimum === null || (typeof value.minimum === 'number' && Number.isFinite(value.minimum)))
    && (value.maximum === null || (typeof value.maximum === 'number' && Number.isFinite(value.maximum)));
}

function stringMap(value: unknown, keys: readonly string[]): boolean {
  return record(value) && exact(value, keys) && keys.every((key) => decimal(value[key]));
}

function rangeMap(value: unknown, keys: readonly string[]): boolean {
  return record(value) && exact(value, keys) && keys.every((key) => decimalRange(value[key]));
}

function isRow(value: unknown): value is OptimizerResultRow {
  if (!record(value) || !exact(value, [
    'rowKey', 'category', 'replacementCount', 'equippedCount', 'priorityScore',
    'constraintDistance', 'primaryStats', 'derivedMetrics', 'sets',
  ])) return false;
  const primaryKeys = OPTIMIZER_PRIMARY_STATS.map(({ key }) => key);
  const derivedKeys = OPTIMIZER_DERIVED_METRICS.map(({ key }) => key);
  return typeof value.rowKey === 'string' && value.rowKey.length > 0
    && value.category === 'exact'
    && Number.isInteger(value.replacementCount) && Number(value.replacementCount) >= 0 && Number(value.replacementCount) <= 2
    && Number.isInteger(value.equippedCount) && Number(value.equippedCount) >= 0 && Number(value.equippedCount) <= 6
    && Number.isInteger(value.priorityScore)
    && typeof value.constraintDistance === 'number' && Number.isFinite(value.constraintDistance)
    && stringMap(value.primaryStats, primaryKeys)
    && stringMap(value.derivedMetrics, derivedKeys)
    && Array.isArray(value.sets) && value.sets.length <= 6
    && value.sets.every((set) => record(set) && exact(set, ['setId', 'label', 'pieces', 'activations'])
      && typeof set.setId === 'string' && typeof set.label === 'string'
      && Number.isInteger(set.pieces) && Number(set.pieces) >= 1 && Number(set.pieces) <= 6
      && Number.isInteger(set.activations) && Number(set.activations) >= 0 && Number(set.activations) <= 3);
}

export function isOptimizerResultOptions(value: unknown): value is OptimizerResultOptions {
  if (!record(value) || !exact(value, ['maxPageSize', 'primaryFields', 'derivedFields', 'sortOptions'])) return false;
  const field = (item: unknown): boolean => record(item) && exact(item, ['fieldId', 'label', 'sortKey'])
    && typeof item.fieldId === 'string' && typeof item.label === 'string' && typeof item.sortKey === 'string';
  const sort = (item: unknown): boolean => record(item) && exact(item, ['sortKey', 'label'])
    && typeof item.sortKey === 'string' && typeof item.label === 'string';
  return value.maxPageSize === 1000
    && Array.isArray(value.primaryFields) && value.primaryFields.length === 8 && value.primaryFields.every(field)
    && Array.isArray(value.derivedFields) && value.derivedFields.length === 15 && value.derivedFields.every(field)
    && value.primaryFields.every((item, index) => item.fieldId === OPTIMIZER_PRIMARY_STATS[index].key)
    && value.derivedFields.every((item, index) => item.fieldId === OPTIMIZER_DERIVED_METRICS[index].key)
    && Array.isArray(value.sortOptions) && value.sortOptions.length === 25 && value.sortOptions.every(sort)
    && new Set(value.sortOptions.map((item) => item.sortKey)).size === 25;
}

export function isOptimizerResultQuery(value: unknown): value is OptimizerResultQuery {
  if (!record(value) || !exact(value, [
    'runId', 'category', 'sortKey', 'direction', 'pageIndex', 'pageSize',
    'primaryRanges', 'derivedRanges', 'priorityScore', 'constraintDistance',
    'replacementCount', 'equippedCount',
  ])) return false;
  return typeof value.runId === 'string' && value.runId.length > 0
    && ['all', 'exact'].includes(String(value.category))
    && ['ascending', 'descending'].includes(String(value.direction))
    && typeof value.sortKey === 'string' && value.sortKey.length > 0
    && Number.isInteger(value.pageIndex) && Number(value.pageIndex) >= 0
    && Number.isInteger(value.pageSize) && Number(value.pageSize) >= 1 && Number(value.pageSize) <= 1000
    && rangeMap(value.primaryRanges, OPTIMIZER_PRIMARY_STATS.map(({ key }) => key))
    && rangeMap(value.derivedRanges, OPTIMIZER_DERIVED_METRICS.map(({ key }) => key))
    && floatRange(value.priorityScore) && floatRange(value.constraintDistance)
    && decimalRange(value.replacementCount) && decimalRange(value.equippedCount);
}

export function isOptimizerResultSnapshot(value: unknown): value is OptimizerResultSnapshot {
  if (!record(value) || !exact(value, RESULT_KEYS)) return false;
  const counts = value.categoryCounts;
  const failure = value.failure;
  const state = String(value.state);
  const rows = Array.isArray(value.rows) ? value.rows : [];
  const rowsValid = Array.isArray(value.rows) && rows.length <= Number(value.pageSize) && rows.every(isRow);
  const identityValid = state === 'idle'
    ? value.queryId === null && value.runId === null
    : typeof value.queryId === 'string' && value.queryId.length > 0 && typeof value.runId === 'string' && value.runId.length > 0;
  const stateValid = (state === 'idle' && value.stage === null && value.canCancel === false && value.filteredRows === null && rows.length === 0 && failure === null)
    || (state === 'running' && value.stage !== null && value.filteredRows === null && rows.length === 0 && failure === null)
    || (state === 'completed' && value.stage === null && value.canCancel === false && typeof value.filteredRows === 'string' && failure === null)
    || (state === 'rerun-required' && value.stage === null && value.canCancel === false && value.filteredRows === null && rows.length === 0 && failure === null)
    || (state === 'cancelled' && value.stage === null && value.canCancel === false && rows.length === 0 && failure === null)
    || (state === 'failed' && value.stage === null && value.canCancel === false && rows.length === 0 && failure !== null);
  return Number.isInteger(value.sequence) && Number(value.sequence) >= 0
    && ['idle', 'running', 'completed', 'rerun-required', 'cancelled', 'failed'].includes(String(value.state))
    && (value.queryId === null || (typeof value.queryId === 'string' && value.queryId.length > 0))
    && (value.runId === null || (typeof value.runId === 'string' && value.runId.length > 0))
    && (value.stage === null || ['queued', 'filtering', 'sorting', 'resolving'].includes(String(value.stage)))
    && typeof value.scannedRows === 'string' && NONNEGATIVE_DECIMAL.test(value.scannedRows)
    && typeof value.totalRows === 'string' && NONNEGATIVE_DECIMAL.test(value.totalRows)
    && typeof value.canCancel === 'boolean'
    && record(counts) && exact(counts, ['exact', 'oneAway', 'twoAway'])
    && typeof counts.exact === 'string' && NONNEGATIVE_DECIMAL.test(counts.exact)
    && typeof counts.oneAway === 'string' && NONNEGATIVE_DECIMAL.test(counts.oneAway)
    && typeof counts.twoAway === 'string' && NONNEGATIVE_DECIMAL.test(counts.twoAway)
    && (value.filteredRows === null || (typeof value.filteredRows === 'string' && NONNEGATIVE_DECIMAL.test(value.filteredRows)))
    && Number.isInteger(value.pageIndex) && Number(value.pageIndex) >= 0
    && Number.isInteger(value.pageSize) && Number(value.pageSize) >= 1 && Number(value.pageSize) <= 1000
    && Number.isInteger(value.pageCount) && Number(value.pageCount) >= 0
    && typeof value.startOffset === 'string' && NONNEGATIVE_DECIMAL.test(value.startOffset)
    && typeof value.endOffset === 'string' && NONNEGATIVE_DECIMAL.test(value.endOffset)
    && typeof value.hasPrevious === 'boolean' && typeof value.hasNext === 'boolean' && typeof value.outOfRange === 'boolean'
    && rowsValid && identityValid && stateValid
    && Array.isArray(value.rerunReasons) && value.rerunReasons.length <= 32 && value.rerunReasons.every((item) => typeof item === 'string')
    && (failure === null || (record(failure) && exact(failure, ['code', 'message'])
      && typeof failure.code === 'string' && typeof failure.message === 'string'
      && !PRIVATE_PATH.test(failure.message)));
}

const emptyDecimalRange = (): DecimalRange => ({ minimum: null, maximum: null });

export function defaultOptimizerResultQuery(runId: string): OptimizerResultQuery {
  return {
    runId,
    category: 'all',
    sortKey: 'priority-score',
    direction: 'descending',
    pageIndex: 0,
    pageSize: 100,
    primaryRanges: Object.fromEntries(OPTIMIZER_PRIMARY_STATS.map(({ key }) => [key, emptyDecimalRange()])) as Record<OptimizerPrimaryStatKey, DecimalRange>,
    derivedRanges: Object.fromEntries(OPTIMIZER_DERIVED_METRICS.map(({ key }) => [key, emptyDecimalRange()])) as Record<OptimizerDerivedMetricId, DecimalRange>,
    priorityScore: { minimum: null, maximum: null },
    constraintDistance: { minimum: null, maximum: null },
    replacementCount: emptyDecimalRange(),
    equippedCount: emptyDecimalRange(),
  };
}
