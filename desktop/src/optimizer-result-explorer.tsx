import { useEffect, useMemo, useRef, useState } from 'react';

import {
  OPTIMIZER_DERIVED_METRICS,
  OPTIMIZER_PRIMARY_STATS,
  type OptimizerDerivedMetricId,
  type OptimizerPrimaryStatKey,
} from './shared/optimizer-profile';
import {
  defaultOptimizerResultQuery,
  isOptimizerResultQuery,
  type DecimalRange,
  type FloatRange,
  type OptimizerResultOptions,
  type OptimizerResultQuery,
  type OptimizerResultSnapshot,
} from './shared/optimizer-results';
import type { OptimizerResultDetailRequest } from './shared/optimizer-result-detail';
import type {
  OptimizerResultExportFormat,
  OptimizerResultExportSnapshot,
} from './shared/optimizer-result-export';
import { OptimizerResultDetail } from './optimizer-result-detail';
import {
  initialOptimizerResultDetailWorkspaceState,
  type OptimizerResultDetailWorkspaceState,
} from './optimizer-result-detail-workspace';
import { completedSetIcons } from './fribbels-set-icons';
import { Alert, Badge, Button, Card, Dialog } from './ui';

interface OptimizerResultExplorerProps {
  runId: string | null;
  options: OptimizerResultOptions | null;
  snapshot: OptimizerResultSnapshot | null;
  pending: boolean;
  error: string | null;
  initialQuery?: OptimizerResultQuery | null;
  detail?: OptimizerResultDetailWorkspaceState;
  onQuery(query: OptimizerResultQuery): void;
  onCancel(queryId: string): void;
  onInspect?(request: OptimizerResultDetailRequest): void;
  heroName?: string;
  equipping?: boolean;
  onEquip?(request: OptimizerResultDetailRequest): void;
  onCloseDetail?(): void;
  exportSnapshot?: OptimizerResultExportSnapshot | null;
  onExport?(runId: string, queryId: string, format: OptimizerResultExportFormat): void;
  onCancelExport?(exportId: string): void;
}

const ROW_HEIGHT = 46;
const TABLE_HEADER_HEIGHT = 32;
const VIEWPORT_HEIGHT = 230;
const OVERSCAN = 6;
const RESULT_COLUMN_COUNT = 4 + OPTIMIZER_PRIMARY_STATS.length + OPTIMIZER_DERIVED_METRICS.length;

type ResultMetricColumn =
  | { source: 'primary'; key: OptimizerPrimaryStatKey; abbreviation: string; label: string }
  | { source: 'derived'; key: OptimizerDerivedMetricId; abbreviation: string; label: string };

const RESULT_METRIC_COLUMNS: readonly ResultMetricColumn[] = [
  { source: 'primary', key: 'attack', abbreviation: 'ATK', label: 'Attack' },
  { source: 'primary', key: 'defense', abbreviation: 'DEF', label: 'Defense' },
  { source: 'primary', key: 'health', abbreviation: 'HP', label: 'Health' },
  { source: 'primary', key: 'speed', abbreviation: 'SPD', label: 'Speed' },
  { source: 'primary', key: 'criticalHitChancePercent', abbreviation: 'CR', label: 'Critical Hit Chance' },
  { source: 'primary', key: 'criticalHitDamagePercent', abbreviation: 'CD', label: 'Critical Hit Damage' },
  { source: 'primary', key: 'effectivenessPercent', abbreviation: 'EFF', label: 'Effectiveness' },
  { source: 'primary', key: 'effectResistancePercent', abbreviation: 'RES', label: 'Effect Resistance' },
  { source: 'derived', key: 'metric.cp', abbreviation: 'CP', label: 'Combat Power' },
  { source: 'derived', key: 'metric.hp_speed', abbreviation: 'HPS', label: 'Health × Speed' },
  { source: 'derived', key: 'metric.ehp', abbreviation: 'EHP', label: 'Effective Health' },
  { source: 'derived', key: 'metric.ehp_speed', abbreviation: 'EHPS', label: 'EHP × Speed' },
  { source: 'derived', key: 'metric.damage', abbreviation: 'DMG', label: 'Average Damage' },
  { source: 'derived', key: 'metric.damage_speed', abbreviation: 'DMGS', label: 'Damage × Speed' },
  { source: 'derived', key: 'metric.mcd', abbreviation: 'MCD', label: 'Max Critical Damage' },
  { source: 'derived', key: 'metric.mcd_speed', abbreviation: 'MCDS', label: 'MCD × Speed' },
  { source: 'derived', key: 'metric.damage_health', abbreviation: 'DMGH', label: 'Damage × Health' },
  { source: 'derived', key: 'metric.damage_defense', abbreviation: 'DMGD', label: 'Damage × Defense' },
  { source: 'derived', key: 'metric.s1', abbreviation: 'S1', label: 'Skill 1 Damage' },
  { source: 'derived', key: 'metric.s2', abbreviation: 'S2', label: 'Skill 2 Damage' },
  { source: 'derived', key: 'metric.s3', abbreviation: 'S3', label: 'Skill 3 Damage' },
  { source: 'derived', key: 'metric.gear_score', abbreviation: 'GS', label: 'Gear Score' },
  { source: 'derived', key: 'metric.build_score', abbreviation: 'BS', label: 'Build Score' },
] as const;

function formatCount(value: string): string {
  try { return BigInt(value).toLocaleString(); } catch { return value; }
}

function resultRangeIssue(label: string, value: DecimalRange | FloatRange): string | null {
  if (value.minimum === null || value.maximum === null) return null;
  if (typeof value.minimum === 'string'
    && (!/^-?[0-9]+$/.test(value.minimum) || !/^-?[0-9]+$/.test(value.maximum as string))) return null;
  const invalid = typeof value.minimum === 'string'
    ? BigInt(value.minimum) > BigInt(value.maximum as string)
    : value.minimum > (value.maximum as number);
  return invalid ? `${label} maximum must be greater than or equal to its minimum.` : null;
}

export function optimizerResultRangeIssues(query: OptimizerResultQuery): string[] {
  return [
    ...OPTIMIZER_PRIMARY_STATS.map((field) => resultRangeIssue(field.label, query.primaryRanges[field.key])),
    ...OPTIMIZER_DERIVED_METRICS.map((field) => resultRangeIssue(field.label, query.derivedRanges[field.key])),
    resultRangeIssue('Priority score', query.priorityScore),
    resultRangeIssue('Equipped count', query.equippedCount),
  ].filter((issue): issue is string => issue !== null);
}

function rangeSlug(label: string): string {
  return label.toLocaleLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}

function cloneFilterQuery(query: OptimizerResultQuery): OptimizerResultQuery {
  return {
    ...query,
    primaryRanges: Object.fromEntries(
      Object.entries(query.primaryRanges).map(([key, value]) => [key, { ...value }]),
    ) as OptimizerResultQuery['primaryRanges'],
    derivedRanges: Object.fromEntries(
      Object.entries(query.derivedRanges).map(([key, value]) => [key, { ...value }]),
    ) as OptimizerResultQuery['derivedRanges'],
    priorityScore: { ...query.priorityScore },
    constraintDistance: { ...query.constraintDistance },
    replacementCount: { ...query.replacementCount },
    equippedCount: { ...query.equippedCount },
  };
}

function activeSecondaryFilterCount(query: OptimizerResultQuery): number {
  const ranges = [
    ...Object.values(query.primaryRanges),
    ...Object.values(query.derivedRanges),
    query.priorityScore,
    query.equippedCount,
  ];
  return ranges.reduce(
    (count, range) => count + (range.minimum !== null || range.maximum !== null ? 1 : 0),
    0,
  );
}

function metricValue(
  row: OptimizerResultSnapshot['rows'][number],
  column: ResultMetricColumn,
): string {
  return column.source === 'primary'
    ? row.primaryStats[column.key]
    : row.derivedMetrics[column.key];
}

function DecimalRangeInputs({
  label,
  value,
  onChange,
}: {
  label: string;
  value: DecimalRange;
  onChange(value: DecimalRange): void;
}): React.JSX.Element {
  const issue = resultRangeIssue(label, value);
  const errorId = `optimizer-result-${rangeSlug(label)}-error`;
  const update = (field: keyof DecimalRange, raw: string): void => {
    if (!/^-?[0-9]*$/.test(raw)) return;
    onChange({ ...value, [field]: raw === '' ? null : raw });
  };
  return (
    <div className="optimizer-result-range">
      <strong>{label}</strong>
      <input aria-describedby={issue ? errorId : undefined} aria-invalid={Boolean(issue)} aria-label={`${label} minimum`} inputMode="numeric" onChange={(event) => update('minimum', event.currentTarget.value)} placeholder="Min" value={value.minimum ?? ''} />
      <input aria-describedby={issue ? errorId : undefined} aria-invalid={Boolean(issue)} aria-label={`${label} maximum`} inputMode="numeric" onChange={(event) => update('maximum', event.currentTarget.value)} placeholder="Max" value={value.maximum ?? ''} />
      {issue && <span className="field-error-message optimizer-result-range-error" id={errorId}>{issue}</span>}
    </div>
  );
}

function FloatRangeInputs({
  label,
  value,
  onChange,
}: {
  label: string;
  value: FloatRange;
  onChange(value: FloatRange): void;
}): React.JSX.Element {
  const issue = resultRangeIssue(label, value);
  const errorId = `optimizer-result-${rangeSlug(label)}-error`;
  const update = (field: keyof FloatRange, raw: string): void => {
    const parsed = raw === '' ? null : Number(raw);
    if (parsed !== null && !Number.isFinite(parsed)) return;
    onChange({ ...value, [field]: parsed });
  };
  return (
    <div className="optimizer-result-range">
      <strong>{label}</strong>
      <input aria-describedby={issue ? errorId : undefined} aria-invalid={Boolean(issue)} aria-label={`${label} minimum`} onChange={(event) => update('minimum', event.currentTarget.value)} placeholder="Min" step="any" type="number" value={value.minimum ?? ''} />
      <input aria-describedby={issue ? errorId : undefined} aria-invalid={Boolean(issue)} aria-label={`${label} maximum`} onChange={(event) => update('maximum', event.currentTarget.value)} placeholder="Max" step="any" type="number" value={value.maximum ?? ''} />
      {issue && <span className="field-error-message optimizer-result-range-error" id={errorId}>{issue}</span>}
    </div>
  );
}

export function OptimizerResultExplorer({
  runId,
  options,
  snapshot,
  pending,
  error,
  initialQuery = null,
  detail = initialOptimizerResultDetailWorkspaceState,
  onQuery,
  onCancel,
  onInspect = () => undefined,
  heroName = 'the selected character',
  equipping = false,
  onEquip = () => undefined,
  onCloseDetail = () => undefined,
  exportSnapshot = null,
  onExport = () => undefined,
  onCancelExport = () => undefined,
}: OptimizerResultExplorerProps): React.JSX.Element | null {
  const [query, setQuery] = useState<OptimizerResultQuery | null>(
    () => runId ? (initialQuery?.runId === runId ? initialQuery : defaultOptimizerResultQuery(runId)) : null,
  );
  const [scrollTop, setScrollTop] = useState(0);
  const [exportFormat, setExportFormat] = useState<OptimizerResultExportFormat>('csv');
  const [filterOpen, setFilterOpen] = useState(false);
  const [filterDraft, setFilterDraft] = useState<OptimizerResultQuery | null>(null);
  const [selectedRowKey, setSelectedRowKey] = useState<string | null>(
    () => detail.open ? detail.snapshot?.rowKey ?? null : null,
  );
  const lastSubmitted = useRef(
    runId && initialQuery?.runId === runId && snapshot?.state === 'completed' && snapshot.runId === runId
      ? JSON.stringify(initialQuery)
      : '',
  );
  const detailOrigin = useRef<HTMLDivElement | null>(null);
  const queryIssues = useMemo(() => query ? optimizerResultRangeIssues(query) : [], [query]);
  const filterIssues = useMemo(
    () => filterDraft ? optimizerResultRangeIssues(filterDraft) : [],
    [filterDraft],
  );
  const queryIsSubmitted = query !== null && JSON.stringify(query) === lastSubmitted.current;

  useEffect(() => {
    if (!runId) {
      setQuery(null);
      setFilterOpen(false);
      setFilterDraft(null);
      setSelectedRowKey(null);
      lastSubmitted.current = '';
      return;
    }
    const initial = initialQuery?.runId === runId ? initialQuery : defaultOptimizerResultQuery(runId);
    setQuery(initial);
    lastSubmitted.current = initialQuery?.runId === runId && snapshot?.state === 'completed' && snapshot.runId === runId
      ? JSON.stringify(initial)
      : '';
    // The active query is App-owned so route reopen does not invalidate a valid selected detail.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  useEffect(() => {
    if (!detail.open) {
      setSelectedRowKey(null);
      return;
    }
    if (!detail.pending && detail.snapshot?.rowKey) {
      setSelectedRowKey(detail.snapshot.rowKey);
    }
  }, [detail.open, detail.pending, detail.snapshot?.rowKey]);

  useEffect(() => {
    if (!query || !options || !isOptimizerResultQuery(query) || queryIssues.length > 0) return undefined;
    const encoded = JSON.stringify(query);
    if (encoded === lastSubmitted.current) return undefined;
    const timeout = window.setTimeout(() => {
      lastSubmitted.current = encoded;
      onQuery(query);
    }, 300);
    return () => window.clearTimeout(timeout);
  }, [onQuery, options, query, queryIssues]);

  const rows = queryIssues.length === 0 && queryIsSubmitted && snapshot?.state === 'completed' && snapshot.runId === runId ? snapshot.rows : [];
  const visible = useMemo(() => {
    const first = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN);
    const count = Math.ceil(VIEWPORT_HEIGHT / ROW_HEIGHT) + OVERSCAN * 2;
    return { first, rows: rows.slice(first, first + count) };
  }, [rows, scrollTop]);

  if (!runId) return null;
  if (!options || !query) {
    return <section aria-label="Loading optimizer results" className="optimizer-result-panel"><Card>Preparing result explorer…</Card></section>;
  }

  const updateQuery = (patch: Partial<OptimizerResultQuery>): void => {
    setQuery((current) => current ? { ...current, ...patch, pageIndex: patch.pageIndex ?? 0 } : current);
    setScrollTop(0);
  };
  const active = snapshot?.state === 'running' && snapshot.runId === runId;
  const exportRunning = exportSnapshot?.state === 'running';
  const exportMatchesView = snapshot?.state === 'completed'
    && !!snapshot.runId && !!snapshot.queryId
    && exportSnapshot?.runId === snapshot.runId && exportSnapshot.queryId === snapshot.queryId;
  const filteredRows = snapshot?.filteredRows ?? '0';
  const closeDetail = (): void => {
    onCloseDetail();
    setSelectedRowKey(null);
    window.requestAnimationFrame(() => {
      if (detailOrigin.current?.isConnected) detailOrigin.current.focus();
    });
  };
  const clearSecondaryFilters = (): void => {
    const defaults = defaultOptimizerResultQuery(runId);
    updateQuery({
      primaryRanges: defaults.primaryRanges,
      derivedRanges: defaults.derivedRanges,
      priorityScore: defaults.priorityScore,
      constraintDistance: defaults.constraintDistance,
      replacementCount: defaults.replacementCount,
      equippedCount: defaults.equippedCount,
    });
  };
  const openSecondaryFilters = (): void => {
    setFilterDraft(cloneFilterQuery(query));
    setFilterOpen(true);
  };
  const closeSecondaryFilters = (): void => {
    setFilterOpen(false);
    setFilterDraft(null);
  };
  const clearFilterDraft = (): void => {
    const defaults = defaultOptimizerResultQuery(runId);
    setFilterDraft((current) => current ? {
      ...current,
      primaryRanges: defaults.primaryRanges,
      derivedRanges: defaults.derivedRanges,
      priorityScore: defaults.priorityScore,
      constraintDistance: defaults.constraintDistance,
      replacementCount: defaults.replacementCount,
      equippedCount: defaults.equippedCount,
    } : current);
  };
  const applyFilterDraft = (): void => {
    if (!filterDraft || filterIssues.length > 0) return;
    updateQuery({
      primaryRanges: filterDraft.primaryRanges,
      derivedRanges: filterDraft.derivedRanges,
      priorityScore: filterDraft.priorityScore,
      constraintDistance: filterDraft.constraintDistance,
      replacementCount: filterDraft.replacementCount,
      equippedCount: filterDraft.equippedCount,
    });
    closeSecondaryFilters();
  };
  const secondaryFilterCount = activeSecondaryFilterCount(query);

  return (
    <section aria-labelledby="optimizer-results-title" className="optimizer-result-panel">
      <div className="section-heading optimizer-result-heading">
        <div>
          <span className="card-kicker">STEP 04 · RESULTS</span>
          <h2 id="optimizer-results-title">Compare matching builds</h2>
          <p>Filtering and ranking stay in the local backend; this table receives only the visible page.</p>
        </div>
        <div className="optimizer-result-export-actions">
          <Button onClick={openSecondaryFilters} type="button" variant="secondary">
            Secondary filters{secondaryFilterCount > 0 ? ` (${secondaryFilterCount})` : ''}
          </Button>
          {snapshot?.state === 'completed' && snapshot.runId && snapshot.queryId && (
            <>
              <label>Export
                <select aria-label="Result export format" disabled={exportRunning} onChange={(event) => setExportFormat(event.currentTarget.value as OptimizerResultExportFormat)} value={exportFormat}>
                  <option value="csv">CSV</option><option value="json">JSON</option>
                </select>
              </label>
              <Button disabled={exportRunning} onClick={() => onExport(snapshot.runId!, snapshot.queryId!, exportFormat)} type="button" variant="secondary">Export full view</Button>
            </>
          )}
          {active && snapshot?.queryId && (
            <Button disabled={!snapshot.canCancel} onClick={() => onCancel(snapshot.queryId!)} type="button" variant="secondary">
              {snapshot.canCancel ? 'Cancel view' : 'Cancelling…'}
            </Button>
          )}
        </div>
      </div>

      {exportRunning && exportSnapshot?.exportId && (
        <Alert actions={<Button disabled={!exportSnapshot.canCancel} onClick={() => onCancelExport(exportSnapshot.exportId!)} type="button" variant="secondary">Cancel export</Button>} title="Exporting the complete active view" tone="info">
          {formatCount(exportSnapshot.writtenRows)} of {formatCount(exportSnapshot.rowCount)} rows written. Changing this view cancels safely without publishing a partial file.
        </Alert>
      )}
      {exportMatchesView && exportSnapshot?.state === 'completed' && (
        <Alert title="Export complete" tone="success">{formatCount(exportSnapshot.rowCount)} rows saved ({formatCount(exportSnapshot.fileBytes ?? '0')} bytes). The destination stays private to the desktop save dialog.</Alert>
      )}
      {exportMatchesView && exportSnapshot?.state === 'cancelled' && <Alert title="Export cancelled" tone="warning">No partial result file was published.</Alert>}
      {exportMatchesView && exportSnapshot?.state === 'failed' && <Alert title="Export failed" tone="danger">{exportSnapshot.failure?.message ?? 'The active result view could not be exported safely.'}</Alert>}

      <Dialog
        description="Set optional minimum and maximum values. Blank endpoints do not restrict a metric."
        footer={(
          <>
            <Button onClick={clearFilterDraft} type="button" variant="ghost">Clear all</Button>
            <Button onClick={closeSecondaryFilters} type="button" variant="secondary">Cancel</Button>
            <Button disabled={filterIssues.length > 0} onClick={applyFilterDraft} type="button">Apply filters</Button>
          </>
        )}
        onClose={closeSecondaryFilters}
        open={filterOpen}
        title="Secondary stat filters"
      >
        {filterDraft && (
          <div className="optimizer-result-filter-dialog">
            {filterIssues.length > 0 && (
              <Alert title="Correct these ranges" tone="warning">
                <ul className="optimizer-validation-summary">{filterIssues.map((issue) => <li key={issue}>{issue}</li>)}</ul>
              </Alert>
            )}
            <div className="optimizer-result-filter-grid">
              {OPTIMIZER_PRIMARY_STATS.map((field) => (
                <DecimalRangeInputs
                  key={field.key}
                  label={field.label}
                  onChange={(value) => setFilterDraft((current) => current ? {
                    ...current,
                    primaryRanges: { ...current.primaryRanges, [field.key]: value },
                  } : current)}
                  value={filterDraft.primaryRanges[field.key]}
                />
              ))}
              {OPTIMIZER_DERIVED_METRICS.map((field) => (
                <DecimalRangeInputs
                  key={field.key}
                  label={field.label}
                  onChange={(value) => setFilterDraft((current) => current ? {
                    ...current,
                    derivedRanges: { ...current.derivedRanges, [field.key]: value },
                  } : current)}
                  value={filterDraft.derivedRanges[field.key]}
                />
              ))}
              <FloatRangeInputs
                label="Priority score"
                onChange={(priorityScore) => setFilterDraft((current) => current ? { ...current, priorityScore } : current)}
                value={filterDraft.priorityScore}
              />
              <DecimalRangeInputs
                label="Equipped count"
                onChange={(equippedCount) => setFilterDraft((current) => current ? { ...current, equippedCount } : current)}
                value={filterDraft.equippedCount}
              />
            </div>
          </div>
        )}
      </Dialog>

      {queryIssues.length > 0 && (
        <Alert title="Correct the result filters" tone="warning">
          <ul className="optimizer-validation-summary">{queryIssues.map((issue) => <li key={issue}>{issue}</li>)}</ul>
          <p>The previous page is hidden until every minimum is less than or equal to its maximum.</p>
        </Alert>
      )}
      {queryIssues.length === 0 && !queryIsSubmitted && (
        <div aria-live="polite" className="optimizer-result-loading" role="status">
          <span className="spinner" /><span>Result filters changed. Updating the bounded page…</span>
        </div>
      )}
      {error && <Alert title="Result explorer unavailable" tone="danger">{error}</Alert>}
      {snapshot?.state === 'rerun-required' && (
        <Alert title="Run a new search" tone="warning">
          These filters ask for builds outside the completed search: {snapshot.rerunReasons.join('; ')}. Adjust the requested target ranges, then use Start new search above.
        </Alert>
      )}
      {snapshot?.state === 'cancelled' && (
        <Alert title="Result view cancelled">No partial page was kept. Change a filter to request a fresh bounded page.</Alert>
      )}
      {snapshot?.state === 'failed' && snapshot.failure && (
        <Alert title="Result query stopped safely" tone="danger">{snapshot.failure.message}</Alert>
      )}
      {active && (
        <div aria-live="polite" className="optimizer-result-loading" role="status">
          <span className="spinner" />
          <span>{snapshot.stage === 'filtering' ? `Filtering ${formatCount(snapshot.scannedRows)} rows…` : snapshot.stage === 'sorting' ? 'Ranking matching builds…' : snapshot.stage === 'resolving' ? 'Resolving visible rows…' : 'Waiting for the previous view…'}</span>
        </div>
      )}

      {queryIssues.length === 0 && queryIsSubmitted && snapshot?.state === 'completed' && rows.length === 0 && (
        <Alert
          actions={snapshot.hasPrevious ? <Button onClick={() => updateQuery({ pageIndex: Math.max(0, query.pageIndex - 1) })} type="button" variant="secondary">Previous page</Button> : <Button onClick={clearSecondaryFilters} type="button" variant="secondary">Clear secondary filters</Button>}
          title="No builds on this page"
        >
          {snapshot.hasPrevious ? 'Return to the previous page, or clear secondary filters.' : 'Clear secondary filters to restore matching builds.'}
        </Alert>
      )}

      {queryIssues.length === 0 && queryIsSubmitted && snapshot?.state === 'completed' && rows.length > 0 && (
        <Card className="optimizer-result-table-card">
          <div aria-label="Compact optimizer results" className="optimizer-result-table-scroll" role="region">
            <div
              aria-colcount={RESULT_COLUMN_COUNT}
              aria-labelledby="optimizer-results-title"
              aria-rowcount={rows.length + 1}
              className="optimizer-result-table"
              role="table"
            >
              <div
                className="optimizer-result-viewport"
                onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
                role="presentation"
                style={{ height: VIEWPORT_HEIGHT + TABLE_HEADER_HEIGHT }}
              >
                <div className="optimizer-result-table-head" role="rowgroup">
                  <div aria-rowindex={1} className="optimizer-result-table-header" role="row">
                    <span role="columnheader" title="Selected build">Build</span>
                    <span role="columnheader">Sets</span>
                    {RESULT_METRIC_COLUMNS.map((column) => (
                      <span aria-label={column.label} key={`${column.source}-${column.key}`} role="columnheader" title={column.label}>
                        {column.abbreviation}
                      </span>
                    ))}
                    <span aria-label="Priority score" role="columnheader" title="Priority score">Prio</span>
                    <span aria-label="Equipped count" role="columnheader" title="Equipped count">Eq</span>
                  </div>
                </div>
              <div className="optimizer-result-row-space" role="rowgroup" style={{ height: rows.length * ROW_HEIGHT }}>
                {visible.rows.map((row, localIndex) => {
                  const index = visible.first + localIndex;
                  const setIcons = completedSetIcons(row.sets);
                  const completedSetLabel = row.sets
                    .filter((set) => set.activations > 0)
                    .map((set) => `${set.label} ×${set.activations}`)
                    .join(', ');
                  return (
                    <div
                      aria-rowindex={index + 2}
                      aria-selected={selectedRowKey === row.rowKey}
                      className={`optimizer-result-row${selectedRowKey === row.rowKey ? ' is-selected' : ''}`}
                      key={row.rowKey}
                      onClick={(event) => {
                        if (!snapshot.queryId) return;
                        detailOrigin.current = event.currentTarget;
                        setSelectedRowKey(row.rowKey);
                        onInspect({ runId, queryId: snapshot.queryId, rowKey: row.rowKey });
                      }}
                      onKeyDown={(event) => {
                        if (event.key !== 'Enter' && event.key !== ' ') return;
                        event.preventDefault();
                        event.currentTarget.click();
                      }}
                      role="row"
                      style={{ height: ROW_HEIGHT, transform: `translateY(${index * ROW_HEIGHT}px)` }}
                      tabIndex={0}
                      title={`Open build ${index + 1} gear cards`}
                    >
                      <span className="optimizer-result-build-cell" role="cell" title={`Build ${index + 1}`}>
                        #{index + 1}
                      </span>
                      <span
                        className="optimizer-result-set-cell"
                        role="cell"
                        aria-label={completedSetLabel || 'No completed sets'}
                        title={completedSetLabel || 'No completed sets'}
                      >
                        {setIcons.length === 0 && <span className="optimizer-result-set-empty">—</span>}
                        {setIcons.map((setIcon) => setIcon.source
                          ? <img alt="" aria-hidden="true" key={setIcon.key} src={setIcon.source} />
                          : <span className="optimizer-result-set-fallback" key={setIcon.key}>{setIcon.label.slice(0, 2)}</span>)}
                      </span>
                      {RESULT_METRIC_COLUMNS.map((column) => (
                        <span
                          aria-label={`${column.label}: ${formatCount(metricValue(row, column))}`}
                          key={`${column.source}-${column.key}`}
                          role="cell"
                          title={`${column.label}: ${formatCount(metricValue(row, column))}`}
                        >
                          {formatCount(metricValue(row, column))}
                        </span>
                      ))}
                      <span role="cell" title={`Priority score: ${row.priorityScore}`}>{row.priorityScore.toLocaleString()}</span>
                      <span role="cell" title={`Equipped pieces: ${row.equippedCount}`}>{row.equippedCount}</span>
                    </div>
                  );
                })}
              </div>
              </div>
            </div>
          </div>
          <footer className="optimizer-result-table-footer">
            <div className="optimizer-result-sortbar">
              <label>Rank by
                <select onChange={(event) => updateQuery({ sortKey: event.currentTarget.value })} value={query.sortKey}>
                  {options.sortOptions.map((item) => <option key={item.sortKey} value={item.sortKey}>{item.label}</option>)}
                </select>
              </label>
              <label>Direction
                <select onChange={(event) => updateQuery({ direction: event.currentTarget.value as OptimizerResultQuery['direction'] })} value={query.direction}>
                  <option value="descending">Highest first</option>
                  <option value="ascending">Lowest first</option>
                </select>
              </label>
              <label>Rows per page
                <select onChange={(event) => updateQuery({ pageSize: Number(event.currentTarget.value) })} value={query.pageSize}>
                  {[50, 100, 250, 500, 1000].map((size) => <option key={size} value={size}>{size}</option>)}
                </select>
              </label>
            </div>
            <nav aria-label="Result pages" className="optimizer-result-pagination">
              <Badge tone="accent">{formatCount(filteredRows)} builds</Badge>
              <span>Rows {formatCount((BigInt(snapshot.startOffset) + 1n).toString())}–{formatCount(snapshot.endOffset)}</span>
              <span>Page {snapshot.pageCount === 0 ? 0 : snapshot.pageIndex + 1} of {snapshot.pageCount.toLocaleString()}</span>
              <Button aria-label="Previous result page" disabled={!snapshot.hasPrevious || pending} onClick={() => updateQuery({ pageIndex: Math.max(0, query.pageIndex - 1) })} type="button" variant="secondary">Previous</Button>
              <Button aria-label="Next result page" disabled={!snapshot.hasNext || pending} onClick={() => updateQuery({ pageIndex: query.pageIndex + 1 })} type="button" variant="secondary">Next</Button>
            </nav>
          </footer>
        </Card>
      )}

      <OptimizerResultDetail
        equipping={equipping}
        heroName={heroName}
        onClose={closeDetail}
        onEquip={onEquip}
        workspace={detail}
      />
    </section>
  );
}
