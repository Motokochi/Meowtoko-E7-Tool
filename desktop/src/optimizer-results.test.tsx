import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { renderToStaticMarkup } from 'react-dom/server';

import {
  OptimizerResultExplorer,
  optimizerResultRangeIssues,
} from './optimizer-result-explorer';
import { OptimizerResultDetail } from './optimizer-result-detail';
import { completedSetIcons } from './fribbels-set-icons';
import { createDesktopApi } from './desktop-api';
import {
  initialOptimizerResultWorkspaceState,
  optimizerResultWorkspaceReducer,
} from './optimizer-result-workspace';
import {
  initialOptimizerResultDetailWorkspaceState,
  optimizerResultDetailWorkspaceReducer,
} from './optimizer-result-detail-workspace';
import { OPTIMIZER_DERIVED_METRICS, OPTIMIZER_PRIMARY_STATS } from './shared/optimizer-profile';
import {
  defaultOptimizerResultQuery,
  isOptimizerResultOptions,
  isOptimizerResultQuery,
  isOptimizerResultSnapshot,
  type OptimizerResultOptions,
  type OptimizerResultRow,
  type OptimizerResultSnapshot,
} from './shared/optimizer-results';
import {
  isOptimizerResultBuildDetail,
  isOptimizerResultDetailRequest,
  isOptimizerResultDetailSnapshot,
  isOptimizerResultEquipResult,
  type OptimizerOwnedGearDetail,
  type OptimizerResultBuildDetail,
  type OptimizerResultDetailSnapshot,
} from './shared/optimizer-result-detail';
import {
  isOptimizerResultExportRequest,
  isOptimizerResultExportSnapshot,
  type OptimizerResultExportSnapshot,
} from './shared/optimizer-result-export';

const OPTIONS: OptimizerResultOptions = {
  maxPageSize: 1000,
  primaryFields: OPTIMIZER_PRIMARY_STATS.map(({ key, label }) => ({ fieldId: key, label, sortKey: `primary:${key}` })),
  derivedFields: OPTIMIZER_DERIVED_METRICS.map(({ key, label }) => ({ fieldId: key, label, sortKey: `derived:${key}` })),
  sortOptions: [
    ...OPTIMIZER_PRIMARY_STATS.map(({ key, label }) => ({ sortKey: `primary:${key}`, label })),
    ...OPTIMIZER_DERIVED_METRICS.map(({ key, label }) => ({ sortKey: `derived:${key}`, label })),
    { sortKey: 'priority-score', label: 'Priority score' },
    { sortKey: 'equipped-count', label: 'Equipped count' },
  ],
};

const ROW: OptimizerResultRow = {
  rowKey: 'query.0',
  category: 'exact',
  replacementCount: 0,
  equippedCount: 2,
  priorityScore: 197,
  constraintDistance: 0,
  primaryStats: Object.fromEntries(OPTIMIZER_PRIMARY_STATS.map(({ key }, index) => [key, String(1000 + index)])) as OptimizerResultRow['primaryStats'],
  derivedMetrics: Object.fromEntries(OPTIMIZER_DERIVED_METRICS.map(({ key }, index) => [key, String(2000 + index)])) as OptimizerResultRow['derivedMetrics'],
  sets: [{ setId: 'set.speed', label: 'Speed', pieces: 4, activations: 1 }],
};

const SNAPSHOT: OptimizerResultSnapshot = {
  sequence: 8,
  state: 'completed',
  queryId: 'query-8',
  runId: 'run-8',
  stage: null,
  scannedRows: '5000000',
  totalRows: '5000000',
  canCancel: false,
  categoryCounts: { exact: '3000000', oneAway: '1500000', twoAway: '500000' },
  filteredRows: '1000',
  pageIndex: 0,
  pageSize: 1000,
  pageCount: 1,
  startOffset: '0',
  endOffset: '1000',
  hasPrevious: false,
  hasNext: false,
  outOfRange: false,
  rows: Array.from({ length: 1000 }, (_, index) => ({ ...ROW, rowKey: `query.${index}` })),
  rerunReasons: [],
  failure: null,
};

const EXPORT_SNAPSHOT: OptimizerResultExportSnapshot = {
  sequence: 2,
  state: 'completed',
  exportId: 'export-2',
  runId: 'run-8',
  queryId: 'query-8',
  format: 'csv',
  rowCount: '1000',
  writtenRows: '1000',
  fileBytes: '4096',
  sha256: 'a'.repeat(64),
  canCancel: false,
  failure: null,
};

const GEAR_SLOTS = [
  ['slot.weapon', 'Weapon'], ['slot.helmet', 'Helmet'], ['slot.armor', 'Armor'],
  ['slot.necklace', 'Necklace'], ['slot.ring', 'Ring'], ['slot.boots', 'Boots'],
] as const;

const GEAR: OptimizerOwnedGearDetail[] = GEAR_SLOTS.map(([slotId, slotLabel], index) => ({
  gearKey: `gear-${index + 1}`,
  slotId,
  slotLabel,
  setId: index < 4 ? 'set.speed' : 'set.health',
  setLabel: index < 4 ? 'Speed' : 'Health',
  rankId: 'rank.epic',
  rankLabel: 'Epic',
  itemLevel: 85,
  enhance: 15,
  gearScore: 70 + index,
  locked: index === 0,
  equippedStatus: index === 1 ? 'other-hero' : index === 2 ? 'selected-hero' : 'unequipped',
  equippedHeroName: index === 1 ? 'Alencia' : index === 2 ? 'Setsuka' : null,
  mainStat: { statId: 'item_stat.flat_attack', label: 'Flat Attack', value: 500 },
  substats: [{ statId: 'item_stat.speed', label: 'Speed', value: 12, reforgedValue: 14 }],
}));

const PRIMARY_CONSTRAINTS = OPTIMIZER_PRIMARY_STATS.map(({ key, label }, index) => ({
  fieldId: key, label, actual: String(1000 + index), minimum: null, maximum: null, status: 'unrestricted' as const,
}));
const DERIVED_CONSTRAINTS = OPTIMIZER_DERIVED_METRICS.map(({ key, label }, index) => ({
  fieldId: key, label, actual: String(2000 + index), minimum: null, maximum: null, status: 'unrestricted' as const,
}));

const EXACT_DETAIL: OptimizerResultBuildDetail = {
  category: 'exact',
  replacementCount: 0,
  equippedCount: 2,
  priorityScore: 197,
  constraintDistance: 0,
  primaryStats: ROW.primaryStats,
  derivedMetrics: ROW.derivedMetrics,
  constraints: { status: 'satisfied', normalizedDistance: 0, primary: PRIMARY_CONSTRAINTS, derived: DERIVED_CONSTRAINTS },
  sets: [
    { setId: 'set.speed', label: 'Speed', pieces: 4, activations: 1, requiredPieces: 4, status: 'target-complete' },
    { setId: 'set.health', label: 'Health', pieces: 2, activations: 1, requiredPieces: 2, status: 'target-complete' },
  ],
  gear: GEAR,
  guidance: { kind: 'set-complete', message: 'The selected set pattern is complete. No future replacement is needed.' },
};

const DETAIL_SNAPSHOT: OptimizerResultDetailSnapshot = {
  sequence: 4,
  state: 'completed',
  selectionId: 'detail-4',
  runId: 'run-8',
  queryId: 'query-8',
  rowKey: 'query.0',
  detail: EXACT_DETAIL,
  failure: null,
};

const EQUIP_RESULT = {
  state: 'equipped' as const,
  heroName: 'Setsuka',
  equippedCount: 6,
  alreadyEquipped: 1,
  movedFromOtherHeroes: 1,
  newlyEquipped: 4,
  unequippedFromHero: 2,
  inventoryEquippedItems: 230,
};

test('strict result contracts keep int64 values as decimals and reject private or oversized payloads', () => {
  const query = defaultOptimizerResultQuery('run-8');
  assert.equal(isOptimizerResultOptions(OPTIONS), true);
  assert.equal(isOptimizerResultQuery(query), true);
  assert.equal(isOptimizerResultSnapshot(SNAPSHOT), true);
  assert.equal(isOptimizerResultQuery({ ...query, pageSize: 1001 }), false);
  assert.equal(isOptimizerResultQuery({ ...query, primaryRanges: { attack: { minimum: 1, maximum: null } } }), false);
  assert.equal(isOptimizerResultSnapshot({ ...SNAPSHOT, rows: [...SNAPSHOT.rows, ROW] }), false);
  assert.equal(isOptimizerResultSnapshot({ ...SNAPSHOT, resultPath: 'C:\\private' }), false);
  assert.equal(isOptimizerResultSnapshot({ ...SNAPSHOT, rows: [{ ...ROW, rowOrdinal: 7 }] }), false);
  assert.equal(isOptimizerResultSnapshot({ ...SNAPSHOT, totalRows: '-1' }), false);
  assert.equal(isOptimizerResultSnapshot({
    ...SNAPSHOT, state: 'failed', filteredRows: null, rows: [], failure: { code: 'failed', message: 'C:\\private\\result.bin' },
  }), false);
});

test('workspace reducer ignores stale responses and preserves options across run invalidation', () => {
  const withOptions = optimizerResultWorkspaceReducer(initialOptimizerResultWorkspaceState, { type: 'options-received', options: OPTIONS });
  const query = defaultOptimizerResultQuery('run-8');
  const querying = optimizerResultWorkspaceReducer(withOptions, { type: 'query-started', query });
  const current = optimizerResultWorkspaceReducer(querying, { type: 'snapshot-received', snapshot: SNAPSHOT });
  assert.equal(current.activeQuery, query);
  const stale = optimizerResultWorkspaceReducer(current, { type: 'snapshot-received', snapshot: { ...SNAPSHOT, sequence: 7, rows: [] } });
  assert.equal(stale, current);
  const reset = optimizerResultWorkspaceReducer(current, { type: 'session-reset' });
  assert.equal(reset.snapshot, null);
  assert.equal(reset.options, OPTIONS);
});

test('strict selected-build contracts reject forged identities, private fields, and unbounded gear', () => {
  assert.equal(isOptimizerResultDetailRequest({ runId: 'run-8', queryId: 'query-8', rowKey: 'query.0' }), true);
  assert.equal(isOptimizerResultBuildDetail(EXACT_DETAIL), true);
  assert.equal(isOptimizerResultBuildDetail({ ...EXACT_DETAIL, category: 'one-away', replacementCount: 1 }), false);
  assert.equal(isOptimizerResultDetailSnapshot(DETAIL_SNAPSHOT), true);
  assert.equal(isOptimizerResultEquipResult(EQUIP_RESULT), true);
  assert.equal(isOptimizerResultDetailRequest({ runId: 'run-8', queryId: 'query-8', rowOrdinal: 0 }), false);
  assert.equal(isOptimizerResultBuildDetail({ ...EXACT_DETAIL, rowOrdinal: 0 }), false);
  assert.equal(isOptimizerResultBuildDetail({ ...EXACT_DETAIL, gear: [...GEAR, GEAR[0]] }), false);
  assert.equal(isOptimizerResultBuildDetail({ ...EXACT_DETAIL, gear: GEAR.map((item, index) => index === 0 ? { ...item, stableItemId: 'private' } : item) }), false);
  assert.equal(isOptimizerResultBuildDetail({
    ...EXACT_DETAIL,
    gear: GEAR.map((item, index) => index === 0 ? { ...item, equippedHeroName: 'Alencia' } : item),
  }), false);
  assert.equal(isOptimizerResultDetailSnapshot({ ...DETAIL_SNAPSHOT, resultPath: 'C:\\private' }), false);
  assert.equal(isOptimizerResultEquipResult({ ...EQUIP_RESULT, stableItemIds: ['private'] }), false);
  assert.equal(isOptimizerResultEquipResult({ ...EQUIP_RESULT, newlyEquipped: 5 }), false);
});

test('detail reducer rejects stale completion and closing does not let a late event reopen the panel', () => {
  const selecting = optimizerResultDetailWorkspaceReducer(initialOptimizerResultDetailWorkspaceState, { type: 'selection-started', rowKey: 'query.0' });
  const loading = optimizerResultDetailWorkspaceReducer(selecting, { type: 'snapshot-received', snapshot: { ...DETAIL_SNAPSHOT, sequence: 3, state: 'loading', detail: null } });
  const closed = optimizerResultDetailWorkspaceReducer(loading, { type: 'closed' });
  const completed = optimizerResultDetailWorkspaceReducer(closed, { type: 'snapshot-received', snapshot: DETAIL_SNAPSHOT });
  assert.equal(completed.open, false);
  const stale = optimizerResultDetailWorkspaceReducer(completed, { type: 'snapshot-received', snapshot: { ...DETAIL_SNAPSHOT, sequence: 2 } });
  assert.equal(stale, completed);
  const equipped = optimizerResultDetailWorkspaceReducer(completed, {
    type: 'local-equip-completed',
    heroName: 'Setsuka',
  });
  assert.equal(equipped.open, false);
  assert.equal(equipped.snapshot?.detail?.equippedCount, 6);
  assert.equal(equipped.snapshot?.detail?.gear.every(
    (item) => item.equippedStatus === 'selected-hero' && item.equippedHeroName === 'Setsuka',
  ), true);
});

test('result explorer renders a compact abbreviated table and only a virtual window of a 1000-row page', () => {
  const query = defaultOptimizerResultQuery('run-8');
  const markup = renderToStaticMarkup(
    <OptimizerResultExplorer
      error={null}
      initialQuery={query}
      onCancel={() => undefined}
      onQuery={() => undefined}
      options={OPTIONS}
      pending={false}
      runId="run-8"
      snapshot={SNAPSHOT}
      exportSnapshot={EXPORT_SNAPSHOT}
    />,
  );
  assert.match(markup, /Compare matching builds/);
  assert.match(markup, /Secondary filters/);
  assert.doesNotMatch(markup, /role="tab"|role="tabpanel"|>All<|>Exact</);
  assert.match(markup, /role="table"/);
  assert.match(markup, /aria-colcount="27"/);
  assert.match(markup, /aria-rowcount="1001"/);
  assert.equal((markup.match(/role="columnheader"/g) ?? []).length, 27);
  assert.equal((markup.match(/role="rowgroup"/g) ?? []).length, 2);
  assert.equal((markup.match(/role="cell"/g) ?? []).length > 28, true);
  assert.equal((markup.match(/class="optimizer-result-row"/g) ?? []).length < 40, true);
  assert.equal((markup.match(/class="optimizer-result-row"/g) ?? []).length > 10, true);
  assert.match(markup, /aria-label="Attack"[^>]*title="Attack">ATK/);
  assert.match(markup, /aria-label="Effective Health"[^>]*title="Effective Health">EHP/);
  assert.match(markup, /aria-label="Priority score"[^>]*>Prio/);
  assert.match(markup, /tabindex="0"[^>]*title="Open build 1 gear cards"/);
  assert.match(markup, /class="optimizer-result-build-cell"[^>]*>#1/);
  assert.doesNotMatch(markup, />View<\/button>/);
  assert.match(markup, /test-asset:\/\/setspeed\.png/);
  assert.match(markup, /Page 1 of 1/);
  assert.match(markup, /Export full view/);
  assert.match(markup, /Export complete/);
  assert.match(markup, /Priority score: 197[^>]*>197</);
  assert.doesNotMatch(markup, /197\.000/);
  assert.ok(markup.indexOf('Rank by') > markup.indexOf('role="table"'));
  assert.doesNotMatch(markup, /(?:denseItem|rowOrdinal|cacheKey|sourcePath|private-result)/);
});

test('secondary filters use an explicit apply dialog without adding renderer sorting', () => {
  const source = readFileSync(path.resolve('src', 'optimizer-result-explorer.tsx'), 'utf8');
  assert.match(source, /title="Secondary stat filters"/);
  assert.match(source, />Apply filters<\/Button>/);
  assert.match(source, /setFilterDraft\(cloneFilterQuery\(query\)\)/);
  assert.doesNotMatch(source, /role="tab"|optimizer-result-tabs/);
  assert.doesNotMatch(source, /\.sort\(|localeCompare\(/);
});

test('invalid secondary ranges are associated, summarized once, and hide stale rows', () => {
  const query = defaultOptimizerResultQuery('run-8');
  query.primaryRanges.attack = { minimum: '4000', maximum: '3999' };
  assert.deepEqual(optimizerResultRangeIssues(query), ['Attack maximum must be greater than or equal to its minimum.']);
  const markup = renderToStaticMarkup(
    <OptimizerResultExplorer
      error={null}
      initialQuery={query}
      onCancel={() => undefined}
      onQuery={() => undefined}
      options={OPTIONS}
      pending={false}
      runId="run-8"
      snapshot={SNAPSHOT}
    />,
  );
  assert.match(markup, /Correct the result filters/);
  assert.match(markup, /previous page is hidden/i);
  assert.equal((markup.match(/Attack maximum must be greater/g) ?? []).length, 1);
  assert.doesNotMatch(markup, /role="table"/);
});

test('cancelled, rerun-required, empty-page, and detail failure states retain a safe action', () => {
  const query = defaultOptimizerResultQuery('run-8');
  const renderExplorer = (snapshot: OptimizerResultSnapshot): string => renderToStaticMarkup(
    <OptimizerResultExplorer error={null} initialQuery={query} onCancel={() => undefined} onQuery={() => undefined} options={OPTIONS} pending={false} runId="run-8" snapshot={snapshot} />,
  );
  assert.match(renderExplorer({ ...SNAPSHOT, state: 'cancelled', filteredRows: null, rows: [] }), /Result view cancelled[\s\S]*fresh bounded page/);
  assert.match(renderExplorer({ ...SNAPSHOT, state: 'rerun-required', filteredRows: null, rows: [], rerunReasons: ['Attack exceeds searched range.'] }), /Start new search above/);
  assert.match(renderExplorer({ ...SNAPSHOT, rows: [], filteredRows: '0', endOffset: '0' }), /Clear secondary filters/);
  const failedDetail = renderToStaticMarkup(<OptimizerResultDetail
    onClose={() => undefined}
    workspace={{ snapshot: { ...DETAIL_SNAPSHOT, state: 'failed', detail: null, failure: { code: 'invalidated', message: 'The selected page changed.' } }, open: true, pending: false, error: null }}
  />);
  assert.match(failedDetail, /Close the cards, then select another visible build/);
});

test('completed set icons repeat activations and omit incomplete sets', () => {
  const icons = completedSetIcons([
    { setId: 'set.speed', label: 'Speed', pieces: 2, activations: 0 },
    { setId: 'set.critical', label: 'Critical', pieces: 4, activations: 2 },
  ]);
  assert.equal(icons.length, 2);
  assert.deepEqual(icons.map(({ label }) => label), ['Critical', 'Critical']);
  assert.deepEqual(
    icons.map(({ source }) => source),
    ['test-asset://setcritical.png', 'test-asset://setcritical.png'],
  );
});

test('detail selection focuses its inline card region once and returns focus to the mounted row origin', () => {
  const explorer = readFileSync(path.resolve('src', 'optimizer-result-explorer.tsx'), 'utf8');
  const detail = readFileSync(path.resolve('src', 'optimizer-result-detail.tsx'), 'utf8');
  assert.match(explorer, /detailOrigin\.current\?\.isConnected/);
  assert.match(explorer, /detailOrigin\.current\.focus\(\)/);
  assert.match(detail, /panelRef\.current\?\.focus\(\)/);
  assert.match(detail, /tabIndex=\{-1\}/);
  assert.match(detail, /role="region"/);
  assert.match(detail, /aria-describedby="optimizer-result-detail-description"/);
});

test('responsive optimizer markers keep the compact table free of horizontal scrolling', () => {
  const css = readFileSync(path.resolve('src', 'styles.css'), 'utf8');
  assert.match(css, /@media \(max-width: 62rem\)[\s\S]*optimizer-result-filter-grid/);
  assert.match(css, /@media \(max-width: 48rem\)[\s\S]*optimizer-result-table-footer/);
  assert.match(css, /@media \(max-width: 35rem\)[\s\S]*optimizer-result-range/);
  assert.match(css, /optimizer-result-table-scroll[\s\S]*overflow-x: hidden/);
  assert.match(css, /optimizer-result-table-head[\s\S]*position: sticky/);
  assert.match(css, /optimizer-result-viewport[\s\S]*overflow-y: scroll/);
  assert.match(css, /optimizer-result-table-header[\s\S]*repeat\(23,/);
  assert.match(css, /@media \(forced-colors: active\)[\s\S]*optimizer-result-row/);
});

test('selected exact build panel shows six compact owned-gear cards, equipped faces, and completed sets', () => {
  const exactMarkup = renderToStaticMarkup(<OptimizerResultDetail
    heroName="Setsuka"
    onClose={() => undefined}
    onEquip={() => undefined}
    workspace={{ snapshot: DETAIL_SNAPSHOT, open: true, pending: false, error: null }}
  />);
  assert.match(exactMarkup, /Equip these six pieces/);
  assert.equal((exactMarkup.match(/class="optimizer-gear-card"/g) ?? []).length, 6);
  assert.match(exactMarkup, /setspeed\.png/);
  assert.match(exactMarkup, /12 → 14/);
  assert.equal((exactMarkup.match(/Lv 85/g) ?? []).length, 6);
  assert.match(exactMarkup, /Sets complete/);
  assert.match(exactMarkup, /GS 70/);
  for (const icon of ['gearweapon', 'gearhelmet', 'geararmor', 'gearnecklace', 'gearring', 'gearboots']) {
    assert.match(exactMarkup, new RegExp(`test-asset://${icon}\\.png`));
  }
  assert.equal((exactMarkup.match(/alt="(?:Weapon|Helmet|Armor|Necklace|Ring|Boots) slot"/g) ?? []).length, 6);
  assert.doesNotMatch(exactMarkup, /optimizer-gear-slot-icon"><svg/);
  assert.match(exactMarkup, /src="e7-character:\/\/artwork\/image\?name=Alencia&amp;variant=face_s"/);
  assert.match(exactMarkup, /alt="Alencia equipped character"/);
  assert.match(exactMarkup, /src="e7-character:\/\/artwork\/image\?name=Setsuka&amp;variant=face_s"/);
  assert.equal((exactMarkup.match(/<img[^>]*variant=face_s/g) ?? []).length, 2);
  assert.match(exactMarkup, />Equip<\/span><\/button>/);
  assert.ok(exactMarkup.indexOf('>Equip</span>') < exactMarkup.indexOf('>Close cards</span>'));
  assert.match(exactMarkup, /<strong>197<\/strong> priority/);
  assert.doesNotMatch(exactMarkup, /197\.000/);
  assert.doesNotMatch(exactMarkup, /seek a Speed piece/);
  const source = readFileSync(path.resolve('src', 'optimizer-result-detail.tsx'), 'utf8');
  assert.match(source, /Equip this build locally\?/);
  assert.match(source, /This does not tap or change[\s\S]*Epic Seven/);
});

test('Electron wiring exposes only bounded result methods and update events', () => {
  const main = readFileSync(path.resolve('src', 'main.ts'), 'utf8');
  const preload = readFileSync(path.resolve('src', 'desktop-api.ts'), 'utf8');
  const backendClient = readFileSync(path.resolve('src', 'backend-client.ts'), 'utf8');
  for (const channel of [
    'optimizer:results:options', 'optimizer:results:get', 'optimizer:results:query',
    'optimizer:results:cancel', 'optimizer:results:updated', 'optimizer:results:detail',
    'optimizer:results:detail-updated', 'optimizer:results:equip',
    'optimizer:results:export:get', 'optimizer:results:export:select',
    'optimizer:results:export:cancel', 'optimizer:results:export:updated',
  ]) assert.match(`${main}\n${preload}\n${backendClient}`, new RegExp(channel.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  assert.doesNotMatch(`${main}\n${preload}\n${backendClient}`, /optimizer:results:(?:raw|all|memmap|path|ordinals)/);
});

test('result export contracts expose no destination and reject private failure text', () => {
  assert.equal(isOptimizerResultExportRequest({ runId: 'run-8', queryId: 'query-8', format: 'csv' }), true);
  assert.equal(isOptimizerResultExportSnapshot(EXPORT_SNAPSHOT), true);
  assert.equal(isOptimizerResultExportRequest({ runId: 'run-8', queryId: 'query-8', format: 'xlsx' }), false);
  assert.equal(isOptimizerResultExportSnapshot({ ...EXPORT_SNAPSHOT, destination: 'C:\\private.csv' }), false);
  assert.equal(isOptimizerResultExportSnapshot({
    ...EXPORT_SNAPSHOT,
    state: 'failed',
    fileBytes: null,
    sha256: null,
    failure: { code: 'failed', message: 'Could not write C:\\private.csv' },
  }), false);
});

test('preload bridge validates result requests, responses, and the exact update subscription', async () => {
  const calls: Array<{ channel: string; args: unknown[] }> = [];
  let eventListener: ((payload: unknown) => void) | null = null;
  let detailEventListener: ((payload: unknown) => void) | null = null;
  let exportEventListener: ((payload: unknown) => void) | null = null;
  const api = createDesktopApi(
    async (channel, ...args) => {
      calls.push({ channel, args });
      if (channel === 'optimizer:results:options') return OPTIONS;
      if (channel === 'optimizer:results:detail') return DETAIL_SNAPSHOT;
      if (channel === 'optimizer:results:equip') return EQUIP_RESULT;
      if (channel === 'optimizer:results:export:get' || channel === 'optimizer:results:export:cancel') return EXPORT_SNAPSHOT;
      if (channel === 'optimizer:results:export:select') return { status: 'started', snapshot: EXPORT_SNAPSHOT };
      return SNAPSHOT;
    },
    (channel, listener) => {
      if (channel === 'optimizer:results:detail-updated') {
        detailEventListener = listener;
        return () => { detailEventListener = null; };
      }
      if (channel === 'optimizer:results:export:updated') {
        exportEventListener = listener;
        return () => { exportEventListener = null; };
      }
      assert.equal(channel, 'optimizer:results:updated');
      eventListener = listener;
      return () => { eventListener = null; };
    },
  );
  const updates: OptimizerResultSnapshot[] = [];
  const detailUpdates: OptimizerResultDetailSnapshot[] = [];
  const exportUpdates: OptimizerResultExportSnapshot[] = [];
  const unsubscribe = api.onOptimizerResultsUpdated((snapshot) => updates.push(snapshot));
  const unsubscribeDetail = api.onOptimizerResultDetailUpdated((snapshot) => detailUpdates.push(snapshot));
  const unsubscribeExport = api.onOptimizerResultExportUpdated((snapshot) => exportUpdates.push(snapshot));
  assert.deepEqual(await api.getOptimizerResultOptions(), OPTIONS);
  assert.deepEqual(await api.getOptimizerResults(), SNAPSHOT);
  const query = defaultOptimizerResultQuery('run-8');
  assert.deepEqual(await api.queryOptimizerResults(query), SNAPSHOT);
  assert.deepEqual(await api.cancelOptimizerResults('query-8'), SNAPSHOT);
  const detailRequest = { runId: 'run-8', queryId: 'query-8', rowKey: 'query.0' };
  assert.deepEqual(await api.selectOptimizerResultDetail(detailRequest), DETAIL_SNAPSHOT);
  assert.deepEqual(await api.equipOptimizerResultBuild(detailRequest), EQUIP_RESULT);
  assert.deepEqual(await api.getOptimizerResultExport(), EXPORT_SNAPSHOT);
  const exportRequest = { runId: 'run-8', queryId: 'query-8', format: 'csv' as const };
  assert.deepEqual(await api.selectOptimizerResultExport(exportRequest), { status: 'started', snapshot: EXPORT_SNAPSHOT });
  assert.deepEqual(await api.cancelOptimizerResultExport('export-2'), EXPORT_SNAPSHOT);
  assert.ok(eventListener);
  (eventListener as (payload: unknown) => void)(SNAPSHOT);
  assert.equal(updates.length, 1);
  assert.ok(detailEventListener);
  (detailEventListener as (payload: unknown) => void)(DETAIL_SNAPSHOT);
  assert.equal(detailUpdates.length, 1);
  assert.ok(exportEventListener);
  (exportEventListener as (payload: unknown) => void)(EXPORT_SNAPSHOT);
  assert.equal(exportUpdates.length, 1);
  unsubscribe();
  unsubscribeDetail();
  unsubscribeExport();
  assert.deepEqual(calls, [
    { channel: 'optimizer:results:options', args: [] },
    { channel: 'optimizer:results:get', args: [] },
    { channel: 'optimizer:results:query', args: [query] },
    { channel: 'optimizer:results:cancel', args: ['query-8'] },
    { channel: 'optimizer:results:detail', args: [detailRequest] },
    { channel: 'optimizer:results:equip', args: [detailRequest] },
    { channel: 'optimizer:results:export:get', args: [] },
    { channel: 'optimizer:results:export:select', args: [exportRequest] },
    { channel: 'optimizer:results:export:cancel', args: ['export-2'] },
  ]);
  await assert.rejects(api.queryOptimizerResults({ ...query, pageSize: 1001 }), /Unsupported/);
  await assert.rejects(api.selectOptimizerResultDetail({ ...detailRequest, rowKey: '' }), /Unsupported/);
  await assert.rejects(api.equipOptimizerResultBuild({ ...detailRequest, rowKey: '' }), /Unsupported/);
  await assert.rejects(api.selectOptimizerResultExport({ ...exportRequest, format: 'xlsx' as 'csv' }), /Unsupported/);
});
