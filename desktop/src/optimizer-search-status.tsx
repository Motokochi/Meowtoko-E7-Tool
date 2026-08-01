import {
  formatOptimizerSearchCount,
  optimizerSearchProgress,
  type OptimizerSearchSnapshot,
} from './shared/optimizer-search';
import { Alert, Badge, Button, Card } from './ui';

interface OptimizerSearchStatusProps {
  snapshot: OptimizerSearchSnapshot | null;
  pending: boolean;
  disabled: boolean;
  disabledReason: string;
  error: string | null;
  onStart(): void;
  onCancel(jobId: string): void;
  onRetryCpu(jobId: string): void;
}

const EMPTY_COUNTS = { exact: '0', oneAway: '0', twoAway: '0' } as const;

function stateTitle(snapshot: OptimizerSearchSnapshot | null): string {
  if (!snapshot || snapshot.state === 'idle') return 'Ready to search';
  if (snapshot.state === 'preparing') return 'Preparing private inventory';
  if (snapshot.state === 'running') return 'Search running';
  if (snapshot.state === 'completed') return 'Search complete';
  if (snapshot.state === 'overflowed') return 'Too many matching builds';
  if (snapshot.state === 'cancelled') return 'Search cancelled';
  return 'Search stopped safely';
}

function elapsedLabel(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${(seconds - minutes * 60).toFixed(0)}s`;
}

export function OptimizerSearchStatus({
  snapshot,
  pending,
  disabled,
  disabledReason,
  error,
  onStart,
  onCancel,
  onRetryCpu,
}: OptimizerSearchStatusProps): React.JSX.Element {
  const state = snapshot?.state ?? 'idle';
  const active = state === 'preparing' || state === 'running';
  const recovery = state === 'failed' && snapshot?.failure?.cpuRecoveryAvailable === true;
  const counts = snapshot?.categoryCounts ?? EMPTY_COUNTS;
  const progress = snapshot ? optimizerSearchProgress(snapshot) : 0;
  const totalKnown = snapshot !== null && snapshot.totalPermutations !== '0';
  const title = stateTitle(snapshot);
  const primaryDisabled = active ? pending || !snapshot?.canCancel : recovery ? pending : disabled || pending;
  const primaryLabel = active
    ? (snapshot?.canCancel ? 'Cancel search' : 'Cancelling safely')
    : recovery
      ? 'Retry with CPU'
      : state === 'completed' || state === 'overflowed' || state === 'cancelled' || state === 'failed'
        ? 'Start new search'
        : 'Start search';

  const runPrimary = (): void => {
    if (active && snapshot?.jobId) onCancel(snapshot.jobId);
    else if (recovery && snapshot?.jobId) onRetryCpu(snapshot.jobId);
    else onStart();
  };

  return (
    <section aria-labelledby="optimizer-search-title" className="optimizer-search-panel">
      <div className="section-heading optimizer-search-heading">
        <div>
          <span className="card-kicker">STEP 03 · LOCAL SEARCH</span>
          <h2 id="optimizer-search-title">Calculate matching builds</h2>
          <p>Exact completed-set builds share one five-million-result safety limit.</p>
        </div>
        {snapshot?.backend && (
          <Badge tone={snapshot.backend === 'cuda' ? 'accent' : 'neutral'}>
            {snapshot.backend === 'cuda' ? 'CUDA GPU' : 'CPU'}
          </Badge>
        )}
      </div>

      <Card className={`optimizer-search-card optimizer-search-${state}`} elevated>
        <div className="optimizer-search-summary">
          <div aria-atomic="true" aria-live="polite" role="status">
            <span className="card-kicker">SEARCH STATUS</span>
            <strong>{title}</strong>
          </div>
          <Button
            aria-describedby={!active && disabled ? 'optimizer-search-disabled-reason' : undefined}
            busy={pending && !active}
            disabled={primaryDisabled}
            onClick={runPrimary}
            type="button"
            variant={active ? 'danger' : 'primary'}
          >
            {primaryLabel}
          </Button>
        </div>

        {active && (
          <div className="optimizer-search-progress">
            <progress
              aria-label="Optimizer search progress"
              max={100}
              value={totalKnown ? progress : undefined}
            />
            <span>{totalKnown ? `${progress.toFixed(1)}%` : 'Preparing…'}</span>
          </div>
        )}

        <dl className="optimizer-search-metrics">
          <div>
            <dt>Searched</dt>
            <dd>{formatOptimizerSearchCount(snapshot?.searchedPermutations ?? '0')}</dd>
          </div>
          <div>
            <dt>Total permutations</dt>
            <dd>{formatOptimizerSearchCount(snapshot?.totalPermutations ?? '0')}</dd>
          </div>
          <div>
            <dt>Matching builds</dt>
            <dd>{formatOptimizerSearchCount(counts.exact)}</dd>
          </div>
          <div>
            <dt>Elapsed</dt>
            <dd>{elapsedLabel(snapshot?.elapsedSeconds ?? 0)}</dd>
          </div>
        </dl>

        {!active && disabled && state !== 'completed' && state !== 'overflowed' && state !== 'cancelled' && state !== 'failed' && (
          <p className="optimizer-search-disabled-reason" id="optimizer-search-disabled-reason">{disabledReason}</p>
        )}
        {state === 'completed' && (
          <Alert title="Results are ready" tone="success">
            The completed local result run is ready for the bounded explorer.
          </Alert>
        )}
        {state === 'overflowed' && (
          <Alert title="Result limit exceeded" tone="warning">
            More than 5,000,000 exact completed-set builds matched. No partial result set was kept.
            Narrow requested primary ranges, sets or main stats, Include equipped, or enhancement, then search again.
          </Alert>
        )}
        {state === 'cancelled' && (
          <Alert title="No partial results kept">Change the build target or start the search again when ready.</Alert>
        )}
        {state === 'failed' && snapshot?.failure && (
          <Alert title="Search failed" tone="danger">
            {snapshot.failure.message}
            <small className="optimizer-search-failure-code">
              {snapshot.failure.stage} · {snapshot.failure.code}
            </small>
          </Alert>
        )}
        {error && <Alert title="Search action failed" tone="danger">{error}</Alert>}
      </Card>
    </section>
  );
}
