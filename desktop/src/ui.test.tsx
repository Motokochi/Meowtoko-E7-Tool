import assert from 'node:assert/strict';
import { renderToStaticMarkup } from 'react-dom/server';
import { test } from 'node:test';

import {
  Alert,
  Button,
  Dialog,
  EmptyState,
  Skeleton,
  TextInput,
  ToastRegion,
  Tooltip,
} from './ui';

test('form and feedback primitives expose accessible state', () => {
  const markup = renderToStaticMarkup(
    <div>
      <TextInput
        description="Used for result names"
        error="A name is required"
        id="build-name"
        label="Build name"
      />
      <Alert title="Could not save" tone="danger">Try again.</Alert>
      <Button busy>Working</Button>
      <Skeleton label="Loading results" />
      <ToastRegion
        notices={[{ id: 'notice-1', message: 'Saved', tone: 'success' }]}
        onDismiss={() => undefined}
      />
    </div>,
  );

  assert.match(markup, /<label for="build-name">Build name/);
  assert.match(markup, /aria-describedby="build-name-description build-name-error"/);
  assert.match(markup, /aria-invalid="true"/);
  assert.match(markup, /role="alert"/);
  assert.match(markup, /aria-busy="true"[^>]*disabled=""/);
  assert.match(markup, /aria-label="Loading results"[^>]*role="status"/);
  assert.match(markup, /aria-live="polite"/);
});

test('dialog, tooltip, and empty states include keyboard-reader semantics', () => {
  const markup = renderToStaticMarkup(
    <div>
      <Tooltip label="More information"><button type="button">Help</button></Tooltip>
      <Dialog
        description="Confirm this local change."
        onClose={() => undefined}
        open
        title="Confirm action"
      >
        Dialog content
      </Dialog>
      <EmptyState description="Nothing matches the current filters." title="No results" />
    </div>,
  );

  assert.match(markup, /role="tooltip"/);
  assert.match(markup, /role="dialog"/);
  assert.match(markup, /aria-modal="true"/);
  assert.match(markup, /tabindex="-1"/);
  assert.match(markup, /aria-label="Close dialog"/);
  assert.match(markup, /No results/);
});
