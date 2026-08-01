import { useEffect, useState } from 'react';

import type {
  UpdateApplyResult,
  UpdateSnapshot,
} from './shared/update';
import { Badge, Button, Card, Dialog } from './ui';

interface UpdateActions {
  onApply(confirmActiveWork: boolean): Promise<UpdateApplyResult>;
  onCheck(): Promise<void>;
  onDownload(): Promise<void>;
  onInstallLater(): Promise<void>;
  onOpenRelease(): Promise<void>;
}

interface UpdateBannerProps extends UpdateActions {
  snapshot: UpdateSnapshot;
}

function formatBytes(value: string): string {
  const bytes = Number(value);
  if (!Number.isFinite(bytes)) return 'large download';
  const gib = bytes / (1024 ** 3);
  return gib >= 1 ? `${gib.toFixed(2)} GB` : `${Math.ceil(bytes / (1024 ** 2))} MB`;
}

function dismissedVersion(): string | null {
  try {
    return window.localStorage.getItem('e7.dismissed-update-version');
  } catch {
    return null;
  }
}

function dismiss(version: string): void {
  try {
    window.localStorage.setItem('e7.dismissed-update-version', version);
  } catch {
    // A blocked storage provider must not affect update or application state.
  }
}

function useApplyConfirmation(onApply: UpdateActions['onApply']): {
  activeWork: string[];
  applying: boolean;
  apply(): Promise<void>;
  close(): void;
  confirm(): Promise<void>;
} {
  const [activeWork, setActiveWork] = useState<string[]>([]);
  const [applying, setApplying] = useState(false);
  const apply = async (): Promise<void> => {
    setApplying(true);
    try {
      const result = await onApply(false);
      if (result.status === 'confirmation-required') setActiveWork(result.activeWork);
    } catch {
      // The application-level action reports a bounded toast.
    } finally {
      setApplying(false);
    }
  };
  const confirm = async (): Promise<void> => {
    setApplying(true);
    try {
      await onApply(true);
      setActiveWork([]);
    } catch {
      // The application-level action reports a bounded toast.
    } finally {
      setApplying(false);
    }
  };
  return {
    activeWork,
    applying,
    apply,
    close: () => setActiveWork([]),
    confirm,
  };
}

export function UpdateBanner({
  snapshot,
  onApply,
  onCheck,
  onDownload,
  onInstallLater,
  onOpenRelease,
}: UpdateBannerProps): React.JSX.Element | null {
  const [busy, setBusy] = useState(false);
  const [dismissed, setDismissed] = useState(() => dismissedVersion());
  const confirmation = useApplyConfirmation(onApply);
  const version = snapshot.release?.version ?? null;

  useEffect(() => {
    if (version && dismissed !== version) setDismissed(dismissedVersion());
  }, [version]);

  if (!version
    || !['available', 'downloading', 'downloaded'].includes(snapshot.state)
    || (snapshot.state === 'available' && dismissed === version)) {
    return null;
  }

  const run = async (action: () => Promise<void>): Promise<void> => {
    setBusy(true);
    try {
      await action();
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <section className="update-banner" aria-live="polite" aria-label="Application update">
        <div className="update-banner-copy">
          <Badge tone={snapshot.state === 'downloaded' ? 'success' : 'accent'}>
            {snapshot.state === 'downloaded' ? 'READY' : 'UPDATE'}
          </Badge>
          <span>
            <strong>
              {snapshot.state === 'downloading'
                ? `Downloading Meowtoko E7 Tool ${version}`
                : snapshot.state === 'downloaded'
                  ? `Meowtoko E7 Tool ${version} is ready`
                  : `Meowtoko E7 Tool ${version} is available`}
            </strong>
            <small>
              {snapshot.state === 'available'
                ? `${formatBytes(snapshot.release?.downloadBytes ?? '0')} · save work first; Meowtoko E7 Tool restarts automatically`
                : snapshot.state === 'downloading'
                  ? 'Downloading and validating; Meowtoko E7 Tool will restart when ready.'
                  : snapshot.installOnQuit
                    ? 'It will install after Meowtoko E7 Tool closes.'
                    : 'Choose when to install; active work will be protected.'}
            </small>
          </span>
        </div>
        <div className="update-banner-actions">
          {snapshot.state === 'available' && (
            <>
              <Button busy={busy} onClick={() => void run(onDownload)} size="small">Download and restart</Button>
              <Button onClick={() => void onOpenRelease()} size="small" variant="secondary">Release notes</Button>
              <Button
                onClick={() => {
                  dismiss(version);
                  setDismissed(version);
                }}
                size="small"
                variant="ghost"
              >Later</Button>
            </>
          )}
          {snapshot.state === 'downloading' && (
            <span className="update-indeterminate" role="progressbar" aria-label="Downloading update" />
          )}
          {snapshot.state === 'downloaded' && (
            <>
              <Button busy={confirmation.applying} onClick={() => void confirmation.apply()} size="small">
                Restart and install
              </Button>
              <Button
                busy={busy}
                disabled={snapshot.installOnQuit}
                onClick={() => void run(onInstallLater)}
                size="small"
                variant="secondary"
              >{snapshot.installOnQuit ? 'Installing on close' : 'Install on close'}</Button>
            </>
          )}
        </div>
      </section>

      <Dialog
        description="Restarting now would stop the work listed below. Nothing will happen until you confirm."
        onClose={confirmation.close}
        open={confirmation.activeWork.length > 0}
        title="Stop active work and install?"
      >
        <ul className="update-active-work">
          {confirmation.activeWork.map((item) => <li key={item}>{item}</li>)}
        </ul>
        <div className="dialog-actions">
          <Button onClick={confirmation.close} type="button" variant="secondary">Keep working</Button>
          <Button
            busy={confirmation.applying}
            onClick={() => void confirmation.confirm()}
            type="button"
            variant="danger"
          >Stop work and install</Button>
        </div>
      </Dialog>
    </>
  );
}

interface UpdateSettingsCardProps {
  snapshot: UpdateSnapshot;
  onCheck(): Promise<void>;
  onDownload(): Promise<void>;
  onOpenRelease(): Promise<void>;
}

export function UpdateSettingsCard({
  snapshot,
  onCheck,
  onDownload,
  onOpenRelease,
}: UpdateSettingsCardProps): React.JSX.Element {
  const [busy, setBusy] = useState(false);
  const run = async (action: () => Promise<void>): Promise<void> => {
    setBusy(true);
    try {
      await action();
    } finally {
      setBusy(false);
    }
  };
  const status = {
    idle: 'Automatic checks run only in an installed release.',
    checking: 'Checking the public stable release…',
    current: 'You have the latest stable version.',
    available: `Version ${snapshot.release?.version} is available. Save your work; downloading it restarts Meowtoko E7 Tool automatically.`,
    downloading: `Downloading version ${snapshot.release?.version}; Meowtoko E7 Tool will restart when ready.`,
    downloaded: snapshot.installOnQuit ? 'Downloaded; installs when Meowtoko E7 Tool closes.' : 'Downloaded and ready to install.',
    applying: 'Restarting into the update…',
    error: snapshot.error ?? 'Update checking is temporarily unavailable.',
  }[snapshot.state];

  return (
    <Card className="settings-card update-settings-card">
      <div className="settings-card-heading">
        <div><span className="card-kicker">UPDATES</span><h3>Meowtoko E7 Tool {snapshot.currentVersion}</h3></div>
        <Badge tone={snapshot.state === 'error' ? 'warning' : snapshot.state === 'current' ? 'success' : 'neutral'}>
          {snapshot.state.toUpperCase()}
        </Badge>
      </div>
      <p className="settings-section-description" aria-live="polite">{status}</p>
      <div className="update-settings-actions">
        <Button
          busy={busy || snapshot.state === 'checking'}
          disabled={['downloading', 'downloaded', 'applying'].includes(snapshot.state)}
          onClick={() => void run(onCheck)}
          size="small"
          type="button"
          variant="secondary"
        >Check for updates</Button>
        {snapshot.state === 'available' && (
          <Button busy={busy} onClick={() => void run(onDownload)} size="small" type="button">
            Download and restart {formatBytes(snapshot.release?.downloadBytes ?? '0')}
          </Button>
        )}
        {snapshot.release && (
          <Button onClick={() => void onOpenRelease()} size="small" type="button" variant="ghost">
            View release notes
          </Button>
        )}
      </div>
    </Card>
  );
}
