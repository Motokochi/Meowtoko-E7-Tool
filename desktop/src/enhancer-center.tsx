import { useEffect, useMemo, useRef, useState } from 'react';

import {
  enhancementReadiness,
  maxPiecesError,
  parseMaxPieces,
  requiresDestroyConfirmation,
  type EnhancementDebug,
  type EnhancementModeId,
  type EnhancementOptions,
  type EnhancementSnapshot,
  type EnhancementStartOptions,
} from './shared/enhancement';
import type { HealthSnapshot } from './shared/health';
import { Alert, Badge, Button, Card, Dialog, TextInput } from './ui';

interface EnhancerCenterProps {
  health: HealthSnapshot | null;
  options: EnhancementOptions;
  snapshot: EnhancementSnapshot;
  onStart(options: EnhancementStartOptions): Promise<EnhancementSnapshot>;
  onCancel(jobId: string): Promise<EnhancementSnapshot>;
  onGetDebug(): Promise<EnhancementDebug>;
}

export const ALLOW_DESTROY_STORAGE_KEY = 'e7-hub.enhancer-allow-destroy';

export interface EnhancerPreferenceStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export function readAllowDestroy(storage: EnhancerPreferenceStorage | null): boolean {
  if (!storage) return false;
  try {
    return storage.getItem(ALLOW_DESTROY_STORAGE_KEY) === 'true';
  } catch {
    return false;
  }
}

export function writeAllowDestroy(
  storage: EnhancerPreferenceStorage | null,
  value: boolean,
): void {
  if (!storage) return;
  try {
    storage.setItem(ALLOW_DESTROY_STORAGE_KEY, String(value));
  } catch {
    // A blocked storage provider must not prevent the Enhancer from rendering.
  }
}

function browserStorage(): EnhancerPreferenceStorage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage;
  } catch {
    return null;
  }
}

function stateTone(state: EnhancementSnapshot['state']): 'accent' | 'success' | 'warning' | 'danger' | 'neutral' {
  if (state === 'running') return 'accent';
  if (state === 'succeeded') return 'success';
  if (state === 'failed') return 'danger';
  if (state === 'cancelling' || state === 'cancelled') return 'warning';
  return 'neutral';
}

export function EnhancerCenter({
  health,
  options,
  snapshot,
  onStart,
  onCancel,
  onGetDebug,
}: EnhancerCenterProps): React.JSX.Element {
  const mode: EnhancementModeId = 'adb';
  const [maxPieces, setMaxPieces] = useState('0');
  const [allowDestroy, setAllowDestroy] = useState(() => readAllowDestroy(browserStorage()));
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [debugOpen, setDebugOpen] = useState(false);
  const [debug, setDebug] = useState<EnhancementDebug>({ available: false, artifacts: [] });
  const [error, setError] = useState<string | null>(null);
  const logPanel = useRef<HTMLDivElement | null>(null);
  const active = snapshot.state === 'running' || snapshot.state === 'cancelling';
  const readiness = useMemo(() => enhancementReadiness(health, options, mode), [health, mode, options]);
  const limitError = maxPiecesError(maxPieces);
  const decision = snapshot.lastDecision ?? snapshot.result?.lastDecision;
  const runLimit = snapshot.options?.maxPieces ?? parseMaxPieces(maxPieces);
  const pieceStatus = snapshot.pieceNumber > 0
    ? `Piece ${snapshot.pieceNumber}${runLimit ? ` of ${runLimit}` : ''}`
    : 'No active piece';

  useEffect(() => {
    if (logPanel.current) {
      logPanel.current.scrollTop = logPanel.current.scrollHeight;
    }
  }, [snapshot.logs]);

  const requestStart = (): void => {
    setError(null);
    if (limitError) {
      setError(limitError);
      document.getElementById('enhancer-max-pieces')?.focus();
      return;
    }
    if (!readiness.available) {
      setError(readiness.reason ?? 'This automation mode is not ready.');
      return;
    }
    if (requiresDestroyConfirmation(allowDestroy)) {
      setConfirmOpen(true);
      return;
    }
    void start(false);
  };

  const start = async (destroyPermission: boolean): Promise<void> => {
    setConfirmOpen(false);
    setError(null);
    try {
      await onStart({ mode, allowDestroy: destroyPermission, maxPieces: parseMaxPieces(maxPieces) });
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : 'Enhancement automation could not start.');
    }
  };

  const cancel = async (): Promise<void> => {
    if (!snapshot.jobId) return;
    try {
      await onCancel(snapshot.jobId);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : 'Enhancement automation could not stop.');
    }
  };

  const showDebug = async (): Promise<void> => {
    try {
      setDebug(await onGetDebug());
      setDebugOpen(true);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : 'Debug details could not be loaded.');
    }
  };

  return (
    <div className="enhancer-page page-stack">
      <section aria-labelledby="enhancer-workspace-title" className="enhancer-commandbar">
        <div className="enhancer-commandbar-copy">
          <div className="enhancer-commandbar-meta">
            <Badge tone="accent">SAFE AUTOMATION</Badge>
            <span className={`enhancer-readiness ${readiness.available ? 'enhancer-readiness-ready' : 'enhancer-readiness-setup'}`}>
              <span aria-hidden="true" className="enhancer-readiness-dot" />
              {readiness.available ? 'Packets + ADB ready' : 'Setup needed'}
            </span>
          </div>
          <h2 id="enhancer-workspace-title">Enhance gear through ADB</h2>
          <p>Exact enhancement stats come from game packets; every tap uses ADB with a stop check first.</p>
        </div>
        <div className="enhancer-commandbar-actions">
          {active ? (
            <Button
              busy={snapshot.state === 'cancelling'}
              onClick={() => void cancel()}
              size="small"
              variant="danger"
            >Stop safely</Button>
          ) : (
            <Button
              disabled={!readiness.available}
              icon="enhancer"
              onClick={requestStart}
              size="small"
            >Start automation</Button>
          )}
          <Button
            disabled={!snapshot.result?.debugAvailable}
            onClick={() => void showDebug()}
            size="small"
            variant="secondary"
          >Debug</Button>
        </div>
      </section>

      {(!readiness.available || error) && (
        <div className="enhancer-notices">
          {!readiness.available && (
            <Alert className="enhancer-compact-alert" title="ADB mode is unavailable" tone="warning">
              {readiness.reason}
            </Alert>
          )}
          {error && (
            <Alert className="enhancer-compact-alert" title="Enhancer needs attention" tone="danger">
              {error}
            </Alert>
          )}
        </div>
      )}

      <section aria-label="Enhancement workspace" className="enhancer-dashboard">
        <Card className="enhancer-setup-card">
          <div className="enhancer-panel-heading">
            <div>
              <span className="card-kicker">RUN CONFIGURATION</span>
              <h3>Run setup</h3>
            </div>
            <span>ADB only</span>
          </div>

          <div aria-label="Required automation backend" className="enhancer-backend-list">
            {options.modes.map((item) => {
              const itemReadiness = enhancementReadiness(health, options, item.id);
              return (
                <div className="enhancer-backend-row" key={item.id}>
                  <span aria-hidden="true" className="enhancer-backend-mark">ADB</span>
                  <span className="enhancer-backend-copy">
                    <strong>ADB automation</strong>
                    <small>{item.label} · {item.description}</small>
                  </span>
                  <Badge tone={itemReadiness.available ? 'success' : 'warning'}>
                    {itemReadiness.available ? 'READY' : 'NEEDS SETUP'}
                  </Badge>
                </div>
              );
            })}
          </div>

          <div className="enhancer-safety-note">
            <span aria-hidden="true">i</span>
            <p>
              <strong>Fresh gear.txt required</strong>
              Import your latest gear.txt in Importer before every run. If the selected
              item ID or enhancement history does not match, automation stops. Each newly
              opened piece consumes one basic powder to identify it from the exact game packet.
            </p>
          </div>

          <div className="enhancer-setup-section">
            <div className="enhancer-section-label">
              <strong>Run limits</strong>
              <span>0 means run until stopped</span>
            </div>
            <TextInput
              description="Use a positive piece count, or 0 for unlimited."
              disabled={active}
              error={limitError}
              id="enhancer-max-pieces"
              inputMode="numeric"
              label="Maximum pieces"
              min={0}
              onChange={(event) => setMaxPieces(event.target.value)}
              type="number"
              value={maxPieces}
            />
          </div>

          <div className="enhancer-setup-section enhancer-destroy-section">
            <label className="enhancer-destroy-permission">
              <input
                checked={allowDestroy}
                disabled={active}
                onChange={(event) => {
                  setAllowDestroy(event.target.checked);
                  writeAllowDestroy(browserStorage(), event.target.checked);
                }}
                type="checkbox"
              />
              <span className="enhancer-destroy-copy">
                <strong>Allow destroy clicks</strong>
                <small>Requires confirmation for every run.</small>
              </span>
              <Badge tone={allowDestroy ? 'danger' : 'neutral'}>{allowDestroy ? 'ON' : 'OFF'}</Badge>
            </label>
          </div>

          <div className="enhancer-safety-note">
            <span aria-hidden="true">!</span>
            <p><strong>Safe stop boundary</strong>Stop is checked before every action.</p>
          </div>
        </Card>

        <div className="enhancer-live-column">
          <Card className="enhancer-run-card" aria-live="polite">
            <div className="enhancer-run-heading">
              <div>
                <span className="card-kicker">LIVE RUN</span>
                <h3>Enhancement run</h3>
              </div>
              <Badge tone={stateTone(snapshot.state)}>{snapshot.state.toUpperCase()}</Badge>
            </div>
            <div className="enhancer-run-progress-meta">
              <strong>{pieceStatus}</strong>
              <span>{Math.round(snapshot.progress * 100)}%</span>
            </div>
            <progress aria-label="Enhancement run progress" max={1} value={snapshot.progress} />
            <p className="enhancer-run-stage">{snapshot.message}</p>
            {snapshot.error && <p className="enhancer-run-error" role="alert">{snapshot.error}</p>}
            {decision ? (
              <div className="enhancer-decision">
                <div><span>LAST DECISION</span><strong>{decision.action.toUpperCase()}</strong></div>
                <div><span>CURRENT GS</span><strong>{decision.currentGs.toFixed(1)}</strong></div>
                <div><span>POTENTIAL GS</span><strong>{decision.potentialGs.toFixed(1)}</strong></div>
                <p>{decision.reason}</p>
              </div>
            ) : (
              <div className="enhancer-decision-empty">
                <strong>No gear decision yet</strong>
                <span>The latest score and action will appear here during a run.</span>
              </div>
            )}
          </Card>

          <Card className="enhancer-log-card">
            <div className="enhancer-panel-heading">
              <div>
                <span className="card-kicker">BOUNDED ACTIVITY LOG</span>
                <h3>Run evidence</h3>
              </div>
              <span>{snapshot.logs.length} retained</span>
            </div>
            {snapshot.logs.length > 0 ? (
              <div aria-label="Enhancement activity log" className="enhancer-log" ref={logPanel} role="log">
                {snapshot.logs.map((line, index) => (
                  <p key={`${index}-${line}`}><span>{String(index + 1).padStart(2, '0')}</span>{line}</p>
                ))}
              </div>
            ) : (
              <div className="enhancer-log-empty">
                <span aria-hidden="true">ADB</span>
                <p>
                  <strong>No automation activity</strong>
                  Connect a ready ADB device and start a bounded run. Nothing taps until you do.
                </p>
              </div>
            )}
          </Card>
        </div>
      </section>

      <Dialog
        description="This permission applies only to the run you are starting. Rules may click Destroy and its confirmation button."
        footer={(
          <>
            <Button onClick={() => setConfirmOpen(false)} variant="secondary">Keep destroy off</Button>
            <Button onClick={() => void start(true)} variant="danger">Confirm and start</Button>
          </>
        )}
        onClose={() => setConfirmOpen(false)}
        open={confirmOpen}
        title="Allow destructive clicks for this run?"
      >
        <Alert title="Destructive action enabled" tone="danger">
          Rejected gear can be permanently destroyed. Verify the selected backend and game screen first.
        </Alert>
      </Dialog>

      <Dialog
        description="Decoded enhancement evidence and relative artifact names. Files stay inside private user data."
        onClose={() => setDebugOpen(false)}
        open={debugOpen}
        title="Enhancement debug details"
      >
        {debug.available ? (
          <div className="analyzer-debug">
            {debug.text && <pre>{debug.text}</pre>}
            <div>
              <span className="card-kicker">LOCAL ARTIFACTS</span>
              <ul>{debug.artifacts.map((item) => <li key={item}>{item}</li>)}</ul>
            </div>
          </div>
        ) : (
          <p>No completed-run debug data is available.</p>
        )}
      </Dialog>
    </div>
  );
}
