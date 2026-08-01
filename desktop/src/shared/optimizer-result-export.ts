export type OptimizerResultExportFormat = 'csv' | 'json';
export type OptimizerResultExportState = 'idle' | 'running' | 'completed' | 'cancelled' | 'failed';

export interface OptimizerResultExportRequest {
  runId: string;
  queryId: string;
  format: OptimizerResultExportFormat;
}

export interface OptimizerResultExportSnapshot {
  sequence: number;
  state: OptimizerResultExportState;
  exportId: string | null;
  runId: string | null;
  queryId: string | null;
  format: OptimizerResultExportFormat | null;
  rowCount: string;
  writtenRows: string;
  fileBytes: string | null;
  sha256: string | null;
  canCancel: boolean;
  failure: { code: string; message: string } | null;
}

export type OptimizerResultExportSelection =
  | { status: 'cancelled' }
  | { status: 'started'; snapshot: OptimizerResultExportSnapshot };

const DECIMAL = /^(?:0|[1-9][0-9]*)$/;
const SHA256 = /^[0-9a-f]{64}$/;
const PRIVATE_PATH = /(?:[A-Za-z]:[\\/]|\\\\|file:|(?:^|\s)\/\S+)/i;

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function exact(value: Record<string, unknown>, fields: readonly string[]): boolean {
  const keys = Object.keys(value);
  return keys.length === fields.length && fields.every((field) => Object.hasOwn(value, field));
}

export function isOptimizerResultExportFormat(value: unknown): value is OptimizerResultExportFormat {
  return value === 'csv' || value === 'json';
}

export function isOptimizerResultExportRequest(value: unknown): value is OptimizerResultExportRequest {
  return record(value) && exact(value, ['runId', 'queryId', 'format'])
    && typeof value.runId === 'string' && value.runId.length > 0
    && typeof value.queryId === 'string' && value.queryId.length > 0
    && isOptimizerResultExportFormat(value.format);
}

export function isOptimizerResultExportSnapshot(value: unknown): value is OptimizerResultExportSnapshot {
  if (!record(value) || !exact(value, [
    'sequence', 'state', 'exportId', 'runId', 'queryId', 'format', 'rowCount',
    'writtenRows', 'fileBytes', 'sha256', 'canCancel', 'failure',
  ])) return false;
  const state = String(value.state);
  const failure = value.failure;
  const idle = state === 'idle';
  return Number.isInteger(value.sequence) && Number(value.sequence) >= 0
    && ['idle', 'running', 'completed', 'cancelled', 'failed'].includes(state)
    && (idle ? value.exportId === null && value.runId === null && value.queryId === null && value.format === null
      : typeof value.exportId === 'string' && !!value.exportId
        && typeof value.runId === 'string' && !!value.runId
        && typeof value.queryId === 'string' && !!value.queryId
        && isOptimizerResultExportFormat(value.format))
    && typeof value.rowCount === 'string' && DECIMAL.test(value.rowCount)
    && typeof value.writtenRows === 'string' && DECIMAL.test(value.writtenRows)
    && (value.fileBytes === null || (typeof value.fileBytes === 'string' && DECIMAL.test(value.fileBytes)))
    && (value.sha256 === null || (typeof value.sha256 === 'string' && SHA256.test(value.sha256)))
    && typeof value.canCancel === 'boolean'
    && (failure === null || (record(failure) && exact(failure, ['code', 'message'])
      && typeof failure.code === 'string' && typeof failure.message === 'string'
      && !PRIVATE_PATH.test(failure.message)))
    && (state !== 'completed' || (value.fileBytes !== null && value.sha256 !== null && failure === null));
}

export function isOptimizerResultExportSelection(value: unknown): value is OptimizerResultExportSelection {
  return record(value) && ((exact(value, ['status']) && value.status === 'cancelled')
    || (exact(value, ['status', 'snapshot']) && value.status === 'started'
      && isOptimizerResultExportSnapshot(value.snapshot)));
}
