import { useEffect, useMemo, useRef, useState } from 'react';

import {
  availableSubstats,
  createDefaultAnalyzerPiece,
  reconcileAnalyzerPiece,
  validateAnalyzerPiece,
  type AnalyzerDebug,
  type AnalyzerEvaluation,
  type AnalyzerOptions,
  type AnalyzerPiece,
  type AnalyzerScanSnapshot,
  type AnalyzerValidationIssues,
} from './shared/analyzer';
import { Alert, Badge, Button, Card, Dialog, TextInput } from './ui';

interface AnalyzerCenterProps {
  options: AnalyzerOptions;
  snapshot: AnalyzerScanSnapshot;
  autoDetectAvailable: boolean;
  autoDetectReason?: string;
  evaluating: boolean;
  onEvaluate(piece: AnalyzerPiece): Promise<AnalyzerEvaluation>;
  onStartScan(): Promise<AnalyzerScanSnapshot>;
  onCancelScan(jobId: string): Promise<AnalyzerScanSnapshot>;
  onGetDebug(): Promise<AnalyzerDebug>;
}

function fieldId(path: string): string {
  return `analyzer-${path.replace(/[^a-z0-9]+/gi, '-')}`;
}

function SelectField({
  label,
  description,
  error,
  id,
  value,
  values,
  onChange,
}: {
  label: string;
  description?: string;
  error?: string;
  id: string;
  value: string;
  values: readonly string[];
  onChange(value: string): void;
}): React.JSX.Element {
  const descriptionId = description ? `${id}-description` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  return (
    <div className={`field ${error ? 'field-error' : ''}`}>
      <label htmlFor={id}>{label}</label>
      {description && <span className="field-description" id={descriptionId}>{description}</span>}
      <select
        aria-describedby={[descriptionId, errorId].filter(Boolean).join(' ') || undefined}
        aria-invalid={Boolean(error)}
        id={id}
        onChange={(event) => onChange(event.target.value)}
        value={value}
      >
        {values.map((option) => <option key={option} value={option}>{option}</option>)}
      </select>
      {error && <span className="field-error-message" id={errorId}>{error}</span>}
    </div>
  );
}

function scanTone(state: AnalyzerScanSnapshot['state']): 'neutral' | 'accent' | 'success' | 'warning' | 'danger' {
  if (state === 'succeeded') return 'success';
  if (state === 'failed') return 'danger';
  if (state === 'cancelled' || state === 'cancelling') return 'warning';
  if (state === 'running') return 'accent';
  return 'neutral';
}

export function AnalyzerCenter({
  options,
  snapshot,
  autoDetectAvailable,
  autoDetectReason,
  evaluating,
  onEvaluate,
  onStartScan,
  onCancelScan,
  onGetDebug,
}: AnalyzerCenterProps): React.JSX.Element {
  const [piece, setPiece] = useState<AnalyzerPiece>(() => snapshot.state === 'succeeded' && snapshot.result
    ? reconcileAnalyzerPiece(snapshot.result.piece, options)
    : createDefaultAnalyzerPiece(options));
  const [issues, setIssues] = useState<AnalyzerValidationIssues>({});
  const [evaluation, setEvaluation] = useState<AnalyzerEvaluation | null>(
    () => snapshot.state === 'succeeded' ? snapshot.result?.evaluation ?? null : null,
  );
  const [formError, setFormError] = useState<string | null>(null);
  const [debug, setDebug] = useState<AnalyzerDebug>({ available: false, artifacts: [] });
  const [debugOpen, setDebugOpen] = useState(false);
  const appliedJob = useRef<string | undefined>(snapshot.state === 'succeeded' ? snapshot.jobId : undefined);
  const scanActive = snapshot.state === 'running' || snapshot.state === 'cancelling';

  useEffect(() => {
    if (snapshot.state !== 'succeeded' || !snapshot.result || appliedJob.current === snapshot.jobId) return;
    appliedJob.current = snapshot.jobId;
    setPiece(reconcileAnalyzerPiece(snapshot.result.piece, options));
    setEvaluation(snapshot.result.evaluation);
    setIssues({});
    setFormError(null);
  }, [options, snapshot]);

  const mainStats = options.slotMainStats[piece.slot] ?? [];
  const recommendation = evaluation?.gearScore?.recommendation;
  const scoreSummary = useMemo(() => {
    const score = evaluation?.gearScore;
    if (!score) return null;
    return [
      { label: 'Current GS', value: Math.round(score.current) },
      { label: 'Potential GS', value: Math.round(score.potential) },
      { label: 'Rolls tested', value: score.rolls },
    ];
  }, [evaluation]);

  const updatePiece = (next: AnalyzerPiece): void => {
    setPiece(next);
    setIssues({});
    setFormError(null);
  };

  const submit = async (event: React.FormEvent): Promise<void> => {
    event.preventDefault();
    const nextIssues = validateAnalyzerPiece(piece, options);
    setIssues(nextIssues);
    if (Object.keys(nextIssues).length > 0) {
      setFormError('Correct the highlighted gear values before evaluating.');
      const first = document.getElementById(fieldId(Object.keys(nextIssues)[0]));
      first?.focus();
      return;
    }
    setFormError(null);
    try {
      setEvaluation(await onEvaluate(piece));
    } catch (error: unknown) {
      setFormError(error instanceof Error ? error.message : 'Gear evaluation failed.');
    }
  };

  const showDebug = async (): Promise<void> => {
    try {
      const loaded = await onGetDebug();
      setDebug(loaded);
      setDebugOpen(true);
    } catch (error: unknown) {
      setFormError(error instanceof Error ? error.message : 'Debug details could not be loaded.');
    }
  };

  const startScan = async (): Promise<void> => {
    setFormError(null);
    try {
      await onStartScan();
    } catch (error: unknown) {
      setFormError(error instanceof Error ? error.message : 'Gear scan could not start.');
    }
  };

  const cancelScan = async (jobId: string): Promise<void> => {
    try {
      await onCancelScan(jobId);
    } catch (error: unknown) {
      setFormError(error instanceof Error ? error.message : 'Gear scan could not be cancelled.');
    }
  };

  return (
    <div className="analyzer-page page-stack">
      <section aria-labelledby="analyzer-workspace-title" className="analyzer-commandbar">
        <div className="analyzer-commandbar-copy">
          <div className="analyzer-commandbar-meta">
            <Badge tone="accent">UNIFIED ANALYZER</Badge>
            <span className={`analyzer-readiness ${autoDetectAvailable ? 'analyzer-readiness-ready' : 'analyzer-readiness-manual'}`}>
              <span aria-hidden="true" className="analyzer-readiness-dot" />
              {autoDetectAvailable ? 'ADB auto-detect ready' : 'Manual evaluation ready'}
            </span>
          </div>
          <h2 id="analyzer-workspace-title">Analyze one gear piece</h2>
          <p>Capture through ADB or enter the piece manually, then verify its score without leaving this workspace.</p>
        </div>
        <div className="analyzer-commandbar-actions">
          {scanActive && snapshot.jobId ? (
            <Button
              busy={snapshot.state === 'cancelling'}
              onClick={() => void cancelScan(snapshot.jobId as string)}
              size="small"
              variant="danger"
            >Cancel scan</Button>
          ) : (
            <Button
              disabled={!autoDetectAvailable}
              icon="analyzer"
              onClick={() => void startScan()}
              size="small"
            >Auto-detect gear</Button>
          )}
          <Button
            disabled={!snapshot.result?.debugAvailable}
            onClick={() => void showDebug()}
            size="small"
            variant="secondary"
          >Debug</Button>
        </div>
      </section>

      {(!autoDetectAvailable || formError || snapshot.state !== 'idle') && (
        <div className="analyzer-notices">
          {!autoDetectAvailable && (
            <Alert className="analyzer-compact-alert" title="Manual evaluation is ready" tone="warning">
              {autoDetectReason ?? 'Auto-detect needs Tesseract and Ollama. Use Health Center to prepare them.'}
            </Alert>
          )}
          {formError && (
            <Alert className="analyzer-compact-alert" title="Analyzer needs attention" tone="danger">
              {formError}
            </Alert>
          )}
          {snapshot.state !== 'idle' && (
            <Card className="analyzer-progress-card" aria-live="polite">
              <div className="analyzer-progress-copy">
                <div>
                  <Badge tone={scanTone(snapshot.state)}>{snapshot.state.toUpperCase()}</Badge>
                  <strong>{snapshot.message}</strong>
                </div>
                <span>{Math.round(snapshot.progress * 100)}%</span>
              </div>
              <progress aria-label="Gear scan progress" max={1} value={snapshot.progress} />
              {snapshot.error && <p className="analyzer-scan-error" role="alert">{snapshot.error}</p>}
            </Card>
          )}
        </div>
      )}

      <form className="analyzer-workspace" onSubmit={(event) => void submit(event)}>
        <fieldset className="analyzer-fieldset" disabled={scanActive || evaluating}>
          <div className="analyzer-dashboard">
            <Card className="analyzer-input-card">
              <div className="analyzer-panel-heading">
                <div>
                  <span className="card-kicker">GEAR INPUT</span>
                  <h3>Gear input</h3>
                </div>
                <span>Slot rules update automatically</span>
              </div>

              <div className="analyzer-identity-grid">
                <SelectField
                  id={fieldId('enhancement')}
                  label="Enhancement"
                  onChange={(enhancement) => updatePiece({ ...piece, enhancement })}
                  value={piece.enhancement}
                  values={options.enhancements}
                />
                <SelectField
                  error={issues.slot}
                  id={fieldId('slot')}
                  label="Equipment slot"
                  onChange={(slot) => updatePiece(reconcileAnalyzerPiece({ ...piece, slot }, options))}
                  value={piece.slot}
                  values={options.slots}
                />
                <SelectField
                  error={issues.set}
                  id={fieldId('set')}
                  label="Equipment set"
                  onChange={(set) => updatePiece({ ...piece, set })}
                  value={piece.set}
                  values={options.sets}
                />
                <SelectField
                  error={issues.mainStat}
                  id={fieldId('mainStat')}
                  label="Main stat"
                  onChange={(mainStat) => updatePiece(reconcileAnalyzerPiece({ ...piece, mainStat }, options))}
                  value={piece.mainStat}
                  values={mainStats}
                />
              </div>

              <div className="analyzer-substats-heading">
                <div>
                  <span className="card-kicker">SUBSTATS</span>
                  <h3>Four rolled stats</h3>
                </div>
                <span>Duplicates and impossible slot stats are excluded</span>
              </div>
              <div className="analyzer-substat-columns" aria-hidden="true">
                <span>#</span>
                <span>Stat</span>
                <span>Value</span>
              </div>
              <div className="analyzer-substats">
                {piece.substats.map((substat, index) => (
                  <div className="analyzer-substat-row" key={index}>
                    <span className="analyzer-substat-index">{index + 1}</span>
                    <SelectField
                      error={issues[`substats.${index}.stat`]}
                      id={fieldId(`substats.${index}.stat`)}
                      label={`Substat ${index + 1}`}
                      onChange={(stat) => updatePiece({
                        ...piece,
                        substats: piece.substats.map((item, itemIndex) => itemIndex === index ? { ...item, stat } : item),
                      })}
                      value={substat.stat}
                      values={availableSubstats(piece, options, index)}
                    />
                    <TextInput
                      error={issues[`substats.${index}.value`]}
                      id={fieldId(`substats.${index}.value`)}
                      inputMode="numeric"
                      label={`Substat ${index + 1} value`}
                      min={0}
                      onChange={(event) => updatePiece({
                        ...piece,
                        substats: piece.substats.map((item, itemIndex) => itemIndex === index
                          ? { ...item, value: event.target.value }
                          : item),
                      })}
                      type="number"
                      value={substat.value}
                    />
                  </div>
                ))}
              </div>
              <div className="analyzer-form-actions">
                <span>
                  <span aria-hidden="true" className="analyzer-valid-dot" />
                  Evaluation uses the same archetype and Gear Score rules as the legacy analyzer.
                </span>
                <Button busy={evaluating} type="submit">Run evaluation</Button>
              </div>
            </Card>

            <section aria-label="Gear evaluation results" className="analyzer-results">
              <Card className="analyzer-result-card analyzer-score-card" elevated>
                <div className="analyzer-result-heading">
                  <div>
                    <span className="card-kicker">GEAR SCORE</span>
                    <h3>Gear score</h3>
                  </div>
                  {recommendation && (
                    <Badge tone={recommendation === 'keep' || recommendation === 'final' ? 'success' : 'warning'}>
                      {recommendation === 'final' ? 'FINAL' : recommendation === 'keep' ? 'KEEP' : 'STOP'}
                    </Badge>
                  )}
                </div>

                {evaluation?.gearScore ? (
                  <div className="analyzer-score-overview">
                    <div
                      aria-label={`Current Gear Score ${Math.round(evaluation.gearScore.current)}`}
                      className="analyzer-score-ring"
                      role="img"
                    >
                      <strong>{Math.round(evaluation.gearScore.current)}</strong>
                      <span>GS</span>
                    </div>
                    {scoreSummary && (
                      <div className="analyzer-score-metrics">
                        {scoreSummary.map((metric) => (
                          <div key={metric.label}>
                            <span>{metric.label}</span>
                            <strong>{metric.value}</strong>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="analyzer-result-placeholder">
                    <div aria-hidden="true" className="analyzer-score-ring analyzer-score-ring-empty">
                      <strong>-</strong>
                      <span>GS</span>
                    </div>
                    <div>
                      <strong>{evaluation ? 'Score unavailable' : 'Ready to score'}</strong>
                      <p>{evaluation
                        ? 'Review the calculation details for the invalid input.'
                        : 'Enter a piece or use ADB auto-detect, then run the evaluation.'}</p>
                    </div>
                  </div>
                )}

                {evaluation && (
                  <details className="analyzer-result-breakdown">
                    <summary>Calculation details</summary>
                    <pre>{evaluation.gearScoreText}</pre>
                  </details>
                )}
              </Card>

              <Card className="analyzer-result-card analyzer-archetype-card" elevated>
                <div className="analyzer-result-heading">
                  <div>
                    <span className="card-kicker">BUILD FIT</span>
                    <h3>Archetype matches</h3>
                  </div>
                </div>
                {evaluation ? (
                  <pre>{evaluation.archetypeText}</pre>
                ) : (
                  <div className="analyzer-archetype-placeholder">
                    <strong>No matches yet</strong>
                    <p>Matching archetypes will appear here as soon as the piece is evaluated.</p>
                  </div>
                )}
              </Card>

              <p className="analyzer-session-note">
                Results stay local to this application session.
              </p>
            </section>
          </div>
        </fieldset>
      </form>

      <Dialog
        description="OCR evidence and artifact names from the latest successful scan. Files remain inside private user data."
        onClose={() => setDebugOpen(false)}
        open={debugOpen}
        title="Analyzer debug details"
      >
        {debug.available ? (
          <div className="analyzer-debug">
            <pre>{debug.text}</pre>
            <div>
              <span className="card-kicker">LOCAL ARTIFACTS</span>
              {debug.artifacts.length > 0
                ? <ul>{debug.artifacts.map((artifact) => <li key={artifact}>{artifact}</li>)}</ul>
                : <p>No image artifacts were retained.</p>}
            </div>
          </div>
        ) : <p>No successful scan debug data is available yet.</p>}
      </Dialog>
    </div>
  );
}
