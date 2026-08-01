import { useState } from 'react';

import type {
  OptimizerInventoryImportReport,
  OptimizerInventorySnapshot,
} from './shared/optimizer-inventory';
import { Alert, Badge, Button, Card, Dialog, TextInput } from './ui';

export interface OptimizerInventoryNotice {
  tone: 'info' | 'success' | 'warning' | 'danger';
  title: string;
  message: string;
}

interface ImporterCenterProps {
  capturing: boolean;
  importing: boolean;
  inventory: OptimizerInventorySnapshot;
  lastReport: OptimizerInventoryImportReport | null;
  notice: OptimizerInventoryNotice | null;
  onFinishCapture(): void;
  onImport(): void;
  onReset(): Promise<void>;
  onStartCapture(): void;
  packetReady: boolean;
  resetting: boolean;
}

function formatTimestamp(value: string): string {
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(parsed);
}

function CompactImportReport({ report }: { report: OptimizerInventoryImportReport }): React.JSX.Element {
  const issueCount = report.warningCount + report.rejectedCount + report.conflictCount;
  return (
    <details className="optimizer-import-report-compact importer-report">
      <summary>
        <span>Latest import</span>
        <span>{report.acceptedCount.toLocaleString()} accepted · {issueCount.toLocaleString()} issues</span>
      </summary>
      <dl>
        <div><dt>Added</dt><dd>{report.insertedCount.toLocaleString()}</dd></div>
        <div><dt>Updated</dt><dd>{report.updatedCount.toLocaleString()}</dd></div>
        <div><dt>Unchanged</dt><dd>{report.unchangedCount.toLocaleString()}</dd></div>
        <div><dt>Equipped</dt><dd>{report.equippedItemCount.toLocaleString()}</dd></div>
      </dl>
      {report.issues.length > 0 && (
        <ol>
          {report.issues.map((issue, index) => (
            <li key={`${issue.kind}-${issue.code}-${issue.documentPath}-${index}`}>
              <Badge tone={issue.kind === 'warning' ? 'warning' : 'danger'}>{issue.kind}</Badge>
              <span>{issue.message}<small>{issue.documentPath} · {issue.code}</small></span>
            </li>
          ))}
        </ol>
      )}
    </details>
  );
}

export function ImporterCenter({
  capturing,
  importing,
  inventory,
  lastReport,
  notice,
  onFinishCapture,
  onImport,
  onReset,
  onStartCapture,
  packetReady,
  resetting,
}: ImporterCenterProps): React.JSX.Element {
  const [resetOpen, setResetOpen] = useState(false);
  const [confirmation, setConfirmation] = useState('');
  const hasInventory = inventory.state === 'ready';

  const closeReset = (): void => {
    if (resetting) return;
    setResetOpen(false);
    setConfirmation('');
  };

  const confirmReset = async (): Promise<void> => {
    if (confirmation !== 'ERASE' || resetting) return;
    try {
      await onReset();
      setResetOpen(false);
      setConfirmation('');
    } catch {
      // The parent keeps the dialog open and presents the bounded backend error.
    }
  };

  return (
    <div className="page-stack importer-page">
      <Card className="importer-hero" elevated>
        <div>
          <span className="card-kicker">GAME INVENTORY</span>
          <h2>Import your owned gear</h2>
          <p>
            Capture gear and heroes directly from Epic Seven traffic, or import a
            Fribbels <strong>gear.txt</strong>. Only heroes currently at 5★ or higher
            are included.
          </p>
          {capturing && (
            <div className="importer-capture-instructions">
              <strong>Capture is running</strong>
              <p>
                Fully exit Epic Seven (do not only minimize it), open it again,
                continue to the main screen, and wait until it fully loads. Then click{' '}
                <strong>Done Capturing</strong>.
              </p>
              <small>
                The captured file will be saved as Documents\MeowtokoE7Hub\gear.txt
                and imported automatically.
              </small>
            </div>
          )}
        </div>
        <div className="importer-actions">
          {capturing ? (
            <Button busy={importing} onClick={onFinishCapture} type="button">
              Done Capturing
            </Button>
          ) : (
            <Button
              busy={importing}
              disabled={!packetReady}
              onClick={onStartCapture}
              title={packetReady ? 'Start capturing Epic Seven account traffic.' : 'Packet capture is not ready. Check Health Center.'}
              type="button"
            >
              Start capturing from game
            </Button>
          )}
          <Button disabled={importing || capturing} onClick={onImport} type="button" variant="secondary">
            {hasInventory ? 'Import another gear.txt' : 'Select gear.txt'}
          </Button>
        </div>
      </Card>

      {notice && (
        <Alert title={notice.title} tone={notice.tone}>
          {notice.message}
        </Alert>
      )}

      <Card className="importer-inventory-card">
        <div className="importer-section-heading">
          <div>
            <span className="card-kicker">CURRENT DATABASE</span>
            <h2>Owned gear inventory</h2>
          </div>
          <Badge tone={hasInventory ? 'success' : 'warning'}>{hasInventory ? 'Ready' : 'Empty'}</Badge>
        </div>
        {hasInventory ? (
          <>
            <div className="importer-inventory-totals">
              <strong>{inventory.totalItems.toLocaleString()}</strong>
              <span>pieces</span>
              <span>{inventory.equippedItems.toLocaleString()} equipped</span>
              <span>{inventory.lockedItems.toLocaleString()} locked</span>
              {inventory.lastImport && (
                <time dateTime={inventory.lastImport.importedAt}>
                  Imported {formatTimestamp(inventory.lastImport.importedAt)}
                </time>
              )}
            </div>
            <dl className="importer-slot-grid">
              {inventory.itemsBySlot.map((slot) => (
                <div key={slot.slot}><dt>{slot.label}</dt><dd>{slot.count.toLocaleString()}</dd></div>
              ))}
            </dl>
          </>
        ) : (
          <p className="importer-empty-copy">
            No gear is stored. Capture your game account or select a Fribbels gear.txt
            to prepare the Optimizer.
          </p>
        )}
      </Card>

      {lastReport && <CompactImportReport report={lastReport} />}

      <Card className="importer-danger-zone">
        <div>
          <span className="card-kicker">DESTRUCTIVE ACTION</span>
          <h2>Erase all Optimizer data</h2>
          <p>
            Permanently removes imported gear, stored result runs and caches, and every
            saved hero profile. Application settings and Analyzer data are not affected.
          </p>
        </div>
        <Button disabled={importing || capturing} onClick={() => setResetOpen(true)} type="button" variant="danger">
          Erase all Optimizer data
        </Button>
      </Card>

      <Dialog
        description="This permanently deletes gear, results, caches, and saved hero profiles."
        footer={(
          <>
            <Button disabled={resetting} onClick={closeReset} type="button" variant="secondary">Cancel</Button>
            <Button
              busy={resetting}
              disabled={confirmation !== 'ERASE'}
              onClick={() => void confirmReset()}
              type="button"
              variant="danger"
            >
              Permanently erase data
            </Button>
          </>
        )}
        onClose={closeReset}
        open={resetOpen}
        title="Erase all Optimizer data?"
      >
        <Alert title="This cannot be undone" tone="danger">
          Export any results you want to keep before continuing.
        </Alert>
        <TextInput
          autoComplete="off"
          label="Type ERASE to confirm"
          onChange={(event) => setConfirmation(event.currentTarget.value)}
          value={confirmation}
        />
      </Dialog>
    </div>
  );
}
