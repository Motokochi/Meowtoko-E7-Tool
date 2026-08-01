import { useEffect, useState, type FormEvent } from 'react';

import {
  AUTOMATION_IDS,
  CLICK_POINT_IDS,
  LEVEL_IDS,
  REGION_IDS,
  cloneDesktopSettings,
  validateDesktopSettings,
  type AutomationId,
  type ClickPointId,
  type DesktopSettings,
  type LevelId,
  type RegionId,
  type SettingsSnapshot,
  type SettingsPreview,
  type SettingsPreviewRequest,
  type SettingsThemePreference,
  type SettingsValidationIssues,
} from './shared/settings';
import { Alert, Badge, Button, Card, Dialog, TextInput } from './ui';
import type { UpdateSnapshot } from './shared/update';
import { UpdateSettingsCard } from './update-center';

interface SettingsCenterProps {
  snapshot: SettingsSnapshot;
  saving: boolean;
  onPreviewTheme(theme: SettingsThemePreference): void;
  onSelectAdbExecutable(): Promise<string | null>;
  onPreview(settings: DesktopSettings, request: SettingsPreviewRequest): Promise<SettingsPreview>;
  onReload(): Promise<SettingsSnapshot>;
  onSave(settings: DesktopSettings): Promise<SettingsSnapshot>;
  update: UpdateSnapshot;
  onCheckUpdate(): Promise<void>;
  onDownloadUpdate(): Promise<void>;
  onOpenUpdateRelease(): Promise<void>;
  onDirtyChange?(dirty: boolean): void;
}

const REGION_LABELS: Record<RegionId, string> = {
  enhance: 'Enhancement badge',
  slot: 'Gear slot',
  mainStat: 'Main stat',
  set: 'Gear set',
  subs: 'Substats',
};

const POINT_LABELS: Record<ClickPointId, string> = {
  lock: 'Lock gear',
  back: 'Back',
  nextPiece: 'Next piece',
  openEnhance: 'Open enhancement',
  destroy: 'Destroy',
  destroyConfirm: 'Confirm destroy',
  enhance: 'Enhance',
  autoSelect: 'Auto select',
  probeIngredient: 'Basic powder probe',
  probeSelect: 'Confirm powder selection',
};

const AUTOMATION_LABELS: Record<AutomationId, string> = {
  afterAutoSelectSeconds: 'After auto select',
  afterLevelSelectSeconds: 'After level selection',
  afterEnhanceSeconds: 'Enhancement animation wait',
  afterDestroySeconds: 'After destroy',
  afterDestroyConfirmSeconds: 'After destroy confirmation',
  afterLockSeconds: 'After lock',
  afterBackSeconds: 'After back',
  afterNextPieceSeconds: 'After next piece',
  afterOpenEnhanceSeconds: 'After opening enhancement',
  afterRewardPopupSeconds: 'After reward popup',
  enhancementPacketTimeoutSeconds: 'Packet wait timeout',
  afterEnhancementRetrySeconds: 'After enhancement retry',
  enhancementReadRetries: 'Enhancement read retries',
};

function numericValue(value: string): number {
  return value.trim() === '' ? Number.NaN : Number(value);
}

interface CompactNumberProps {
  label: string;
  value: number;
  error?: string;
  min?: number;
  step?: number;
  disabled?: boolean;
  onChange(value: number): void;
}

function CompactNumber({
  label,
  value,
  error,
  min = 0,
  step = 1,
  disabled,
  onChange,
}: CompactNumberProps): React.JSX.Element {
  return (
    <label className={`compact-number ${error ? 'compact-number-error' : ''}`}>
      <span className="sr-only">{label}</span>
      <input
        aria-invalid={Boolean(error)}
        disabled={disabled}
        min={min}
        onChange={(event) => onChange(numericValue(event.target.value))}
        step={step}
        title={error ?? label}
        type="number"
        value={Number.isFinite(value) ? value : ''}
      />
    </label>
  );
}

export function SettingsCenter({
  snapshot,
  saving,
  onPreviewTheme,
  onSelectAdbExecutable,
  onPreview,
  onReload,
  onSave,
  update,
  onCheckUpdate,
  onDownloadUpdate,
  onOpenUpdateRelease,
  onDirtyChange,
}: SettingsCenterProps): React.JSX.Element {
  const [draft, setDraft] = useState(() => cloneDesktopSettings(snapshot.settings));
  const [dirty, setDirty] = useState(false);
  const [issues, setIssues] = useState<SettingsValidationIssues>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [preview, setPreview] = useState<SettingsPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [selectingAdb, setSelectingAdb] = useState(false);

  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);

  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

  useEffect(() => {
    setDraft((current) => dirty
      ? { ...current, appearance: { ...snapshot.settings.appearance } }
      : cloneDesktopSettings(snapshot.settings));
    if (!dirty) setIssues({});
  }, [snapshot.revision, snapshot.settings]);

  const change = (updater: (current: DesktopSettings) => DesktopSettings): void => {
    setDraft(updater);
    setDirty(true);
    setFormError(null);
  };

  const updateRegion = (id: RegionId, axis: 'x' | 'y' | 'width' | 'height', value: number): void => {
    change((current) => ({
      ...current,
      regions: { ...current.regions, [id]: { ...current.regions[id], [axis]: value } },
    }));
  };

  const updatePoint = (id: ClickPointId, axis: 'x' | 'y', value: number): void => {
    change((current) => ({
      ...current,
      clickPoints: { ...current.clickPoints, [id]: { ...current.clickPoints[id], [axis]: value } },
    }));
  };

  const updateLevel = (id: LevelId, axis: 'x' | 'y', value: number): void => {
    change((current) => ({
      ...current,
      clickPoints: {
        ...current.clickPoints,
        levels: { ...current.clickPoints.levels, [id]: { ...current.clickPoints.levels[id], [axis]: value } },
      },
    }));
  };

  const submit = async (event: FormEvent): Promise<void> => {
    event.preventDefault();
    const validation = validateDesktopSettings(draft);
    setIssues(validation);
    if (Object.keys(validation).length > 0) {
      setFormError('Correct the highlighted settings before saving.');
      window.requestAnimationFrame(() => {
        document.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
      });
      return;
    }
    try {
      const saved = await onSave(draft);
      setDraft(cloneDesktopSettings(saved.settings));
      setDirty(false);
      setIssues({});
      setFormError(null);
    } catch (error: unknown) {
      setFormError(error instanceof Error ? error.message : 'Settings could not be saved.');
    }
  };

  const reload = async (): Promise<void> => {
    try {
      const loaded = await onReload();
      setDraft(cloneDesktopSettings(loaded.settings));
      setDirty(false);
      setIssues({});
      setFormError(null);
      onPreviewTheme(loaded.settings.appearance.theme);
    } catch (error: unknown) {
      setFormError(error instanceof Error ? error.message : 'Settings could not be reloaded.');
    }
  };

  const showPreview = async (request: SettingsPreviewRequest): Promise<void> => {
    const validation = validateDesktopSettings(draft);
    setIssues(validation);
    if (Object.keys(validation).length > 0) {
      setFormError('Correct the highlighted settings before previewing.');
      window.requestAnimationFrame(() => document.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus());
      return;
    }
    setPreview(null);
    setPreviewError(null);
    setPreviewLoading(true);
    setPreviewOpen(true);
    try {
      setPreview(await onPreview(draft, request));
    } catch (error: unknown) {
      setPreviewError(error instanceof Error ? error.message : 'The preview could not be captured.');
    } finally {
      setPreviewLoading(false);
    }
  };

  const selectAdbExecutable = async (): Promise<void> => {
    setSelectingAdb(true);
    try {
      const selected = await onSelectAdbExecutable();
      if (selected) {
        change((current) => ({ ...current, adb: { ...current.adb, adbPath: selected } }));
      }
    } catch (error: unknown) {
      setFormError(error instanceof Error ? error.message : 'The ADB executable could not be selected.');
    } finally {
      setSelectingAdb(false);
    }
  };

  const previewActions = (target: SettingsPreviewRequest['target'], label: string): React.JSX.Element => (
    <span className="settings-preview-actions">
      <Button
        aria-label={`ADB preview for ${label}`}
        disabled={disabled}
        onClick={() => void showPreview({ source: 'adb', target })}
        size="small"
        type="button"
        variant="ghost"
      >ADB</Button>
    </span>
  );

  const disabled = snapshot.readOnly || saving;

  return (
    <form className="settings-page page-stack" onSubmit={(event) => void submit(event)}>
      <section className="settings-summary">
        <div>
          <Badge tone={snapshot.readOnly ? 'danger' : snapshot.migratedFrom !== undefined ? 'warning' : 'success'}>
            SCHEMA {snapshot.schemaVersion} · {snapshot.source}
          </Badge>
          <h2>Application preferences</h2>
          <p>
            Configure ADB capture geometry and automation timing without giving the
            renderer direct access to your files.
          </p>
        </div>
        <Button onClick={() => void reload()} type="button" variant="secondary">Reload from disk</Button>
      </section>

      {snapshot.warning && (
        <Alert title="Settings recovery notice" tone={snapshot.readOnly ? 'danger' : 'warning'}>
          {snapshot.warning}
        </Alert>
      )}
      {formError && <Alert title="Settings were not saved" tone="danger">{formError}</Alert>}

      <UpdateSettingsCard
        onCheck={onCheckUpdate}
        onDownload={onDownloadUpdate}
        onOpenRelease={onOpenUpdateRelease}
        snapshot={update}
      />

      <fieldset className="settings-fieldset" disabled={disabled}>
        <Card className="settings-card">
          <div className="settings-card-heading">
            <div><span className="card-kicker">GENERAL</span><h3>Appearance</h3></div>
            <span>Used across every workflow</span>
          </div>
          <div className="settings-form-grid">
            <label className="field">
              <span>Color theme</span>
              <span className="field-description">System follows your Windows appearance.</span>
              <select
                aria-label="Settings color theme"
                onChange={(event) => {
                  const theme = event.target.value as SettingsThemePreference;
                  change((current) => ({ ...current, appearance: { theme } }));
                  onPreviewTheme(theme);
                }}
                value={draft.appearance.theme}
              >
                <option value="system">System</option>
                <option value="light">Light</option>
                <option value="dark">Dark</option>
              </select>
            </label>
          </div>
        </Card>

        <details className="card settings-card settings-details" open>
          <summary>
            <span><span className="card-kicker">CAPTURE</span><strong>Screenshot regions</strong></span>
            <span>5 regions</span>
          </summary>
          <p className="settings-section-description">
            Pixel coordinates are measured in the configured 1280×720 reference space.
            Every preview is captured from the configured ADB device.
          </p>
          <div className="coordinate-grid coordinate-grid-region">
            <span className="coordinate-header">Region</span>
            <span className="coordinate-header">X</span><span className="coordinate-header">Y</span>
            <span className="coordinate-header">Width</span><span className="coordinate-header">Height</span>
            <span className="coordinate-header">Preview</span>
            {REGION_IDS.map((id) => (
              <div className="coordinate-row" key={id}>
                <strong>{REGION_LABELS[id]}</strong>
                {(['x', 'y', 'width', 'height'] as const).map((axis) => (
                  <CompactNumber
                    disabled={disabled}
                    error={issues[`regions.${id}.${axis}`]}
                    key={axis}
                    label={`${REGION_LABELS[id]} ${axis}`}
                    min={axis === 'width' || axis === 'height' ? 1 : 0}
                    onChange={(value) => updateRegion(id, axis, value)}
                    value={draft.regions[id][axis]}
                  />
                ))}
                {previewActions({ kind: 'region', id }, REGION_LABELS[id])}
              </div>
            ))}
          </div>
        </details>

        <details className="card settings-card settings-details">
          <summary>
            <span><span className="card-kicker">AUTOMATION</span><strong>Enhancement click points</strong></span>
            <span>13 points</span>
          </summary>
          <p className="settings-section-description">Click positions use the same reference coordinate space.</p>
          <div className="coordinate-grid coordinate-grid-point">
            <span className="coordinate-header">Action</span>
            <span className="coordinate-header">X</span><span className="coordinate-header">Y</span>
            <span className="coordinate-header">Preview</span>
            {CLICK_POINT_IDS.map((id) => (
              <div className="coordinate-row" key={id}>
                <strong>{POINT_LABELS[id]}</strong>
                {(['x', 'y'] as const).map((axis) => (
                  <CompactNumber
                    disabled={disabled}
                    error={issues[`clickPoints.${id}.${axis}`]}
                    key={axis}
                    label={`${POINT_LABELS[id]} ${axis}`}
                    onChange={(value) => updatePoint(id, axis, value)}
                    value={draft.clickPoints[id][axis]}
                  />
                ))}
                {previewActions({ kind: 'point', id }, POINT_LABELS[id])}
              </div>
            ))}
            {LEVEL_IDS.map((id) => (
              <div className="coordinate-row" key={id}>
                <strong>Level {id}</strong>
                {(['x', 'y'] as const).map((axis) => (
                  <CompactNumber
                    disabled={disabled}
                    error={issues[`clickPoints.levels.${id}.${axis}`]}
                    key={axis}
                    label={`Level ${id} ${axis}`}
                    onChange={(value) => updateLevel(id, axis, value)}
                    value={draft.clickPoints.levels[id][axis]}
                  />
                ))}
                {previewActions({ kind: 'level', id }, `Level ${id}`)}
              </div>
            ))}
          </div>
        </details>

        <details className="card settings-card settings-details">
          <summary>
            <span><span className="card-kicker">TIMING</span><strong>Automation delays and retries</strong></span>
            <span>12 values</span>
          </summary>
          <div className="timing-grid">
            {AUTOMATION_IDS.map((id) => (
              <TextInput
                error={issues[`automation.${id}`]}
                key={id}
                label={AUTOMATION_LABELS[id]}
                min={0}
                onChange={(event) => change((current) => ({
                  ...current,
                  automation: { ...current.automation, [id]: numericValue(event.target.value) },
                }))}
                step={id === 'enhancementReadRetries' ? 1 : 0.1}
                type="number"
                value={Number.isFinite(draft.automation[id]) ? draft.automation[id] : ''}
              />
            ))}
          </div>
        </details>

        <Card className="settings-card">
          <div className="settings-card-heading">
            <div><span className="card-kicker">ADB / EMULATOR</span><h3>Android connection</h3></div>
            <span>Required for every capture</span>
          </div>
          <div className="settings-form-grid adb-settings-grid">
            <div className="adb-path-picker">
              <TextInput
                error={issues['adb.adbPath']}
                label="ADB executable"
                onChange={(event) => change((current) => ({ ...current, adb: { ...current.adb, adbPath: event.target.value } }))}
                value={draft.adb.adbPath}
              />
              <Button
                busy={selectingAdb}
                onClick={() => void selectAdbExecutable()}
                type="button"
                variant="secondary"
              >Browse for adb.exe</Button>
            </div>
            <TextInput
              error={issues['adb.deviceSerial']}
              label="Device serial"
              onChange={(event) => change((current) => ({ ...current, adb: { ...current.adb, deviceSerial: event.target.value } }))}
              placeholder="emulator-5554"
              value={draft.adb.deviceSerial}
            />
            {(['coordinateWidth', 'coordinateHeight', 'commandTimeoutSeconds'] as const).map((id) => (
              <TextInput
                error={issues[`adb.${id}`]}
                key={id}
                label={{
                  coordinateWidth: 'Reference width',
                  coordinateHeight: 'Reference height',
                  commandTimeoutSeconds: 'Command timeout (seconds)',
                }[id]}
                min={id === 'commandTimeoutSeconds' ? 0.1 : 1}
                onChange={(event) => change((current) => ({
                  ...current,
                  adb: { ...current.adb, [id]: numericValue(event.target.value) },
                }))}
                step={id === 'commandTimeoutSeconds' ? 0.1 : 1}
                type="number"
                value={Number.isFinite(draft.adb[id]) ? draft.adb[id] : ''}
              />
            ))}
          </div>
        </Card>
      </fieldset>

      <Dialog
        description={preview ? `${preview.source.toUpperCase()} · ${preview.width}×${preview.height} pixels` : 'Capturing the unsaved coordinates over ADB without clicking.'}
        onClose={() => setPreviewOpen(false)}
        open={previewOpen}
        title={preview?.label ?? 'Settings preview'}
      >
        {previewLoading && <p aria-live="polite">Capturing preview…</p>}
        {previewError && <Alert title="Preview unavailable" tone="danger">{previewError}</Alert>}
        {preview && (
          <figure className="settings-preview-figure">
            <img alt={`${preview.source} preview for ${preview.label}`} src={preview.dataUrl} />
            <figcaption>No click was sent. ADB captured this preview using the current unsaved form values.</figcaption>
          </figure>
        )}
      </Dialog>

      <div className="settings-savebar">
        <div>
          <Badge tone={dirty ? 'warning' : 'success'}>{dirty ? 'Unsaved changes' : 'Saved'}</Badge>
          <span>{snapshot.readOnly ? 'Newer settings are protected from overwrite.' : 'Writes are validated and backed up atomically.'}</span>
        </div>
        <Button busy={saving} disabled={!dirty || snapshot.readOnly} type="submit">Save settings</Button>
      </div>
    </form>
  );
}
