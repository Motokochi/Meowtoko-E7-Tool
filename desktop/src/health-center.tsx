import { useState } from 'react';

import type { HealthActionId, HealthCapabilityState, HealthSnapshot } from './shared/health';
import { overallHealthLabel } from './shared/health';
import { Alert, Badge, Button, Card, Dialog } from './ui';

interface HealthCenterProps {
  snapshot: HealthSnapshot;
  onRefresh(): void;
  onAction(actionId: HealthActionId): void;
}

const STATE_LABELS: Record<HealthCapabilityState, string> = {
  checking: 'Checking',
  ready: 'Ready',
  degraded: 'Limited',
  unavailable: 'Unavailable',
  error: 'Error',
  in_progress: 'In progress',
};

const STATE_TONES: Record<HealthCapabilityState, 'neutral' | 'success' | 'warning' | 'danger' | 'info'> = {
  checking: 'neutral',
  ready: 'success',
  degraded: 'warning',
  unavailable: 'danger',
  error: 'danger',
  in_progress: 'info',
};

export function requiresHealthActionConfirmation(actionId: HealthActionId): boolean {
  return actionId === 'cuda.install' || actionId === 'cuda.repair';
}

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function nvidiaLabel(metadata: Record<string, unknown>): string | null {
  const nvidia = record(metadata.nvidia);
  const adapters = nvidia?.adapters;
  if (nvidia?.detected !== true || !Array.isArray(adapters) || adapters.length === 0) return null;
  const first = record(adapters[0]);
  const name = typeof first?.name === 'string' ? first.name : 'NVIDIA GPU';
  const driver = typeof first?.driverVersion === 'string' ? first.driverVersion : 'unknown driver';
  return `${name} · driver ${driver}`;
}

export function HealthCenter({ snapshot, onRefresh, onAction }: HealthCenterProps): React.JSX.Element {
  const running = snapshot.operation?.state === 'running';
  const readyCount = snapshot.capabilities.filter((capability) => capability.state === 'ready').length;
  const [pendingAction, setPendingAction] = useState<HealthActionId | null>(null);
  const requestAction = (actionId: HealthActionId): void => {
    if (requiresHealthActionConfirmation(actionId)) {
      setPendingAction(actionId);
      return;
    }
    onAction(actionId);
  };
  const gpuOperationRunning = running && (
    snapshot.operation?.actionId === 'cuda.install'
    || snapshot.operation?.actionId === 'cuda.repair'
  );

  return (
    <div className="page-stack health-center" aria-labelledby="health-title">
      <section className="health-summary">
        <div>
          <Badge tone={snapshot.overall === 'ready' ? 'success' : snapshot.overall === 'error' ? 'danger' : 'warning'}>
            {readyCount} OF {snapshot.capabilities.length} READY
          </Badge>
          <h2 id="health-title">{overallHealthLabel(snapshot.overall)}</h2>
          <p>
            Each capability is checked independently. Limited tools never block workflows
            that do not need them.
          </p>
        </div>
        <Button
          busy={running && snapshot.operation?.actionId === 'health.refresh'}
          disabled={running}
          icon="refresh"
          onClick={onRefresh}
          variant="secondary"
        >
          Check again
        </Button>
      </section>

      {snapshot.operation && (
        <Alert
          className="operation-alert"
          title={snapshot.operation.message}
          tone={snapshot.operation.state === 'failed'
            ? 'danger'
            : snapshot.operation.state === 'succeeded'
              ? 'success'
              : snapshot.operation.state === 'cancelled' ? 'warning' : 'info'}
          actions={gpuOperationRunning ? (
            <Button onClick={() => onAction('health.cancel')} size="small" type="button" variant="secondary">
              Cancel GPU setup
            </Button>
          ) : undefined}
        >
          {snapshot.operation.error && <p>{snapshot.operation.error}</p>}
          {snapshot.operation.state === 'running' && snapshot.operation.progress !== undefined && (
            <div className="progress-stack">
              <progress
                aria-label="Health operation progress"
                max={1}
                value={snapshot.operation.progress}
              />
              <span>{Math.round(snapshot.operation.progress * 100)}%</span>
            </div>
          )}
        </Alert>
      )}

      <section aria-labelledby="capabilities-title">
        <div className="section-heading capability-heading">
          <div>
            <span className="card-kicker">LOCAL CAPABILITIES</span>
            <h2 id="capabilities-title">Installed tools and runtime modes</h2>
          </div>
          <span className="last-checked">Last checked {new Date(snapshot.checkedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
        </div>

        <div className="capability-list">
          {snapshot.capabilities.map((capability) => (
            <Card className="capability" key={capability.id}>
              <div className="capability-title">
                <span className={`capability-state state-${capability.state}`} aria-hidden="true" />
                <div>
                  <h3>{capability.title}</h3>
                  <Badge tone={STATE_TONES[capability.state]}>{STATE_LABELS[capability.state]}</Badge>
                </div>
              </div>
              <p>{capability.summary}</p>
              {capability.detail && <p className="capability-detail">{capability.detail}</p>}
              {capability.id === 'cuda' && nvidiaLabel(capability.metadata) && (
                <p className="capability-component-note">
                  <strong>NVIDIA path detected:</strong> {nvidiaLabel(capability.metadata)}. CPU mode remains
                  available whether or not you install the optional component.
                </p>
              )}
              {(capability.version || capability.path) && (
                <dl className="capability-meta">
                  {capability.version && <div><dt>Version</dt><dd>{capability.version}</dd></div>}
                  {capability.path && <div><dt>Location</dt><dd title={capability.path}>{capability.path}</dd></div>}
                </dl>
              )}
              {capability.actions.length > 0 && (
                <div className="capability-actions">
                  {capability.actions.map((action) => (
                    <Button
                      disabled={running}
                      key={action.id}
                      onClick={() => requestAction(action.id)}
                      size="small"
                      variant={action.kind === 'install' || action.kind === 'repair' ? 'secondary' : 'primary'}
                    >
                      {action.label}
                    </Button>
                  ))}
                </div>
              )}
            </Card>
          ))}
        </div>
      </section>

      <Dialog
        description="GPU acceleration is optional. The optimizer remains fully usable in CPU mode."
        footer={(
          <>
            <Button onClick={() => setPendingAction(null)} type="button" variant="ghost">Not now</Button>
            <Button
              onClick={() => {
                const action = pendingAction;
                setPendingAction(null);
                if (action) onAction(action);
              }}
              type="button"
            >
              {pendingAction === 'cuda.repair' ? 'Repair GPU components' : 'Install GPU components'}
            </Button>
          </>
        )}
        onClose={() => setPendingAction(null)}
        open={pendingAction !== null}
        title={pendingAction === 'cuda.repair' ? 'Repair optional GPU components?' : 'Install optional GPU components?'}
      >
        <p>
          Meowtoko E7 Tool will use the fixed <code>cupy-cuda13x[ctk]==14.1.1</code> component from PyPI.
          The download can exceed 1 GB and the installed component can use several GB of disk space.
        </p>
        <p>
          A compatible NVIDIA driver is required. No CUDA Toolkit or <code>nvcc</code> is required.
          You can cancel while the installer is running and continue with CPU mode.
        </p>
      </Dialog>
    </div>
  );
}
