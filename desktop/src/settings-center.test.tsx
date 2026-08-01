import assert from 'node:assert/strict';
import { renderToStaticMarkup } from 'react-dom/server';
import { test } from 'node:test';

import { SettingsCenter } from './settings-center';
import { DEFAULT_DESKTOP_SETTINGS, type SettingsSnapshot } from './shared/settings';
import type { UpdateSnapshot } from './shared/update';

const SNAPSHOT: SettingsSnapshot = {
  schemaVersion: 1,
  revision: 'revision-1',
  source: 'file',
  readOnly: false,
  migratedFrom: 0,
  warning: 'Settings will be upgraded safely when they are next saved.',
  settings: DEFAULT_DESKTOP_SETTINGS,
};

const UPDATE_SNAPSHOT: UpdateSnapshot = {
  state: 'current',
  currentVersion: '0.1.18',
  checkedAt: '2026-07-25T00:00:00.000Z',
  release: null,
  progress: null,
  installOnQuit: false,
  error: null,
};

test('renders every legacy settings group and migration feedback', () => {
  const markup = renderToStaticMarkup(
    <SettingsCenter
      onPreview={async (_settings, request) => ({
        source: request.source,
        kind: request.target.kind,
        itemId: request.target.id,
        label: 'Preview',
        width: 10,
        height: 10,
        dataUrl: 'data:image/png;base64,AAAA',
      })}
      onPreviewTheme={() => undefined}
      onSelectAdbExecutable={async () => 'C:\\Android\\adb.exe'}
      onReload={async () => SNAPSHOT}
      onSave={async () => SNAPSHOT}
      onCheckUpdate={async () => undefined}
      onDownloadUpdate={async () => undefined}
      onOpenUpdateRelease={async () => undefined}
      saving={false}
      snapshot={SNAPSHOT}
      update={UPDATE_SNAPSHOT}
    />,
  );

  assert.match(markup, /Application preferences/);
  assert.match(markup, /Settings will be upgraded safely/);
  assert.match(markup, /Screenshot regions/);
  assert.doesNotMatch(markup, /Target game window/);
  assert.match(markup, /ADB preview for Main stat/);
  assert.match(markup, /ADB preview for Destroy/);
  assert.match(markup, /Enhancement click points/);
  assert.match(markup, /Automation delays and retries/);
  assert.match(markup, /Android connection/);
  assert.match(markup, /Browse for adb\.exe/);
  assert.match(markup, /Settings color theme/);
  assert.match(markup, /<button[^>]*disabled=""[^>]*>[^<]*<span>Save settings/);
});

test('protects newer settings and presents save progress accessibly', () => {
  const readOnly: SettingsSnapshot = {
    ...SNAPSHOT,
    schemaVersion: 9,
    readOnly: true,
    migratedFrom: undefined,
    warning: 'Created by a newer version.',
  };
  const markup = renderToStaticMarkup(
    <SettingsCenter
      onPreview={async (_settings, request) => ({
        source: request.source,
        kind: request.target.kind,
        itemId: request.target.id,
        label: 'Preview',
        width: 10,
        height: 10,
        dataUrl: 'data:image/png;base64,AAAA',
      })}
      onPreviewTheme={() => undefined}
      onSelectAdbExecutable={async () => null}
      onReload={async () => readOnly}
      onSave={async () => readOnly}
      onCheckUpdate={async () => undefined}
      onDownloadUpdate={async () => undefined}
      onOpenUpdateRelease={async () => undefined}
      saving
      snapshot={readOnly}
      update={UPDATE_SNAPSHOT}
    />,
  );

  assert.match(markup, /<fieldset[^>]*disabled=""/);
  assert.match(markup, /Newer settings are protected from overwrite/);
  assert.match(markup, /aria-busy="true"/);
});
