import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { renderToStaticMarkup } from 'react-dom/server';

import { App } from './app';
import { ThemeProvider } from './theme';

function renderRoute(hash: string): string {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'window');
  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: { location: { hash } } as Window,
  });
  try {
    return renderToStaticMarkup(
      <ThemeProvider storage={null}>
        <App />
      </ThemeProvider>,
    );
  } finally {
    if (previous) {
      Object.defineProperty(globalThis, 'window', previous);
    } else {
      Reflect.deleteProperty(globalThis, 'window');
    }
  }
}

test('opens every enabled route repeatedly and gates optimizer data behind its typed load', () => {
  const routes: readonly [string, RegExp][] = [
    ['#/overview', /Build better heroes\./],
    ['#/health', /Preparing Health Center/],
    ['#/analyzer', /aria-label="Loading gear analyzer"/],
    ['#/enhancer', /aria-label="Loading enhancement automation"/],
    ['#/importer', /aria-label="Loading importer backend"/],
    ['#/optimizer', /aria-label="Loading optimizer backend"/],
    ['#/settings', /aria-label="Loading application settings"/],
  ];

  for (const [hash, expected] of routes) {
    const first = renderRoute(hash);
    const second = renderRoute(hash);
    assert.equal(first, second, `${hash} changed across repeated openings`);
    assert.match(first, expected);
  }

  const unknown = renderRoute('#/not-a-page');
  assert.match(unknown, /Build better heroes\./);
  assert.doesNotMatch(unknown, /Loading optimizer backend/);
});

test('optimizer reconnect and route leave invalidate async detail while retaining result state ownership', () => {
  const source = readFileSync(path.resolve('src', 'app.tsx'), 'utf8');
  assert.match(source, /backendConnectionGeneration/);
  assert.match(source, /shouldAcceptHealthSnapshot/);
  assert.match(source, /setHealth\(\(current\) =>/);
  assert.match(source, /optimizerResultQueryGeneration/);
  assert.match(source, /optimizerActiveSearchJobId/);
  assert.match(source, /optimizerActiveResultQueryId/);
  assert.match(source, /optimizerActiveDetailRequest/);
  assert.match(source, /previousActivePage\.current === 'optimizer'/);
  assert.match(source, /dispatchOptimizerResultDetail\(\{ type: 'closed' \}\)/);
  assert.match(source, /Optimizer backend is unavailable/);
  assert.match(source, /results=\{optimizerResults\}/);
  assert.doesNotMatch(source, /activePage !== 'optimizer'[\s\S]{0,240}dispatchOptimizerResults\(\{ type: 'session-reset' \}\)/);
});

test('local Equip retains its result checklist while hero changes clear it', () => {
  const source = readFileSync(path.resolve('src', 'app.tsx'), 'utf8');
  const heroSelection = source.slice(
    source.indexOf('const selectOptimizerHero'),
    source.indexOf('const updateOptimizerDraft'),
  );
  const localEquip = source.slice(
    source.indexOf('const equipOptimizerResultBuild'),
    source.indexOf('const exportOptimizerResults'),
  );

  assert.match(heroSelection, /dispatchOptimizerSearch\(\{ type: 'session-reset' \}\)/);
  assert.match(heroSelection, /dispatchOptimizerResults\(\{ type: 'session-reset' \}\)/);
  assert.match(heroSelection, /dispatchOptimizerResultDetail\(\{ type: 'session-reset' \}\)/);
  assert.match(localEquip, /type: 'local-equip-completed'/);
  assert.match(localEquip, /results and gear cards remain open for reference/);
  assert.doesNotMatch(localEquip, /type: 'session-reset'/);
});
