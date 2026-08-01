import assert from 'node:assert/strict';
import { renderToStaticMarkup } from 'react-dom/server';
import { test } from 'node:test';

import { AppShell } from './app-shell';

test('renders semantic landmarks, skip navigation, and the active page contract', () => {
  const markup = renderToStaticMarkup(
    <AppShell
      activePage="health"
      healthState="degraded"
      onNavigate={() => undefined}
      onThemeChange={() => undefined}
      themePreference="dark"
    >
      <p>Health content</p>
    </AppShell>,
  );

  assert.match(markup, /href="#main-content"[^>]*>Skip to main content/);
  assert.match(markup, /<nav[^>]*aria-label="Primary navigation"/);
  assert.match(markup, /aria-current="page"[^>]*>[\s\S]*Health Center/);
  assert.match(markup, /<main[^>]*id="main-content"[^>]*tabindex="-1"/);
  assert.match(markup, /Limited features/);
  assert.match(markup, /<option value="dark" selected=""/);
});

test('keeps gear, analyzer, enhancer, importer, optimizer, and settings enabled', () => {
  const markup = renderToStaticMarkup(
    <AppShell
      activePage="overview"
      healthState={null}
      onNavigate={() => undefined}
      onThemeChange={() => undefined}
      themePreference="system"
    >
      <p>Overview</p>
    </AppShell>,
  );

  assert.match(markup, /<button[^>]*>[\s\S]*Gear/);
  assert.match(markup, /<button[^>]*>[\s\S]*Analyzer/);
  assert.match(markup, /<button[^>]*>[\s\S]*Enhancer/);
  assert.match(markup, /<button[^>]*>[\s\S]*Importer/);
  assert.match(markup, /<button[^>]*>[\s\S]*Optimizer/);
  assert.match(markup, /<button[^>]*>[\s\S]*Settings/);
  assert.doesNotMatch(markup, /<button[^>]*disabled=""/);
  assert.doesNotMatch(markup, />Later</);
  assert.match(markup, /Checking system/);
  assert.match(markup, /aria-label="Color theme"/);
});

test('renders optimizer as the active page repeatedly without changing shell semantics', () => {
  const render = (): string => renderToStaticMarkup(
    <AppShell
      activePage="optimizer"
      healthState="ready"
      onNavigate={() => undefined}
      onThemeChange={() => undefined}
      themePreference="light"
    >
      <p>Optimizer content</p>
    </AppShell>,
  );

  const first = render();
  const second = render();
  assert.equal(first, second);
  assert.match(first, /aria-current="page"[^>]*>[\s\S]*Optimizer/);
  assert.match(first, /Owned gear build search[\s\S]*<h1>Optimizer<\/h1>/);
  assert.match(first, /<main[^>]*id="main-content"[^>]*tabindex="-1"/);
});
