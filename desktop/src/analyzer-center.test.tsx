import assert from 'node:assert/strict';
import { renderToStaticMarkup } from 'react-dom/server';
import { test } from 'node:test';

import { AnalyzerCenter } from './analyzer-center';
import type { AnalyzerOptions, AnalyzerPiece, AnalyzerScanSnapshot } from './shared/analyzer';

const OPTIONS: AnalyzerOptions = {
  enhancements: Array.from({ length: 16 }, (_item, index) => `+${index}`),
  slots: ['Weapon'],
  sets: ['Speed Set'],
  stats: ['Flat Attack', 'Attack', 'Health', 'Speed', 'Critical Hit Chance'],
  slotMainStats: { Weapon: ['Flat Attack'] },
  restrictedSubstats: { Weapon: [] },
  autoDetectCapabilities: ['tesseract', 'ollama', 'adb'],
};
const PIECE: AnalyzerPiece = {
  enhancement: '+0', slot: 'Weapon', set: 'Speed Set', mainStat: 'Flat Attack',
  substats: [
    { stat: 'Attack', value: '0' }, { stat: 'Health', value: '0' },
    { stat: 'Speed', value: '0' }, { stat: 'Critical Hit Chance', value: '0' },
  ],
};
const EVALUATION = { piece: PIECE, archetypeText: 'NO MATCH', gearScoreText: 'Error', gearScore: null };
const SCORED_EVALUATION = {
  piece: PIECE,
  archetypeText: 'Speed DPS: strong match',
  gearScoreText: 'Current GS: 82\nPotential GS: 92',
  gearScore: { current: 82, potential: 92, rolls: 4, enhancement: 12, recommendation: 'keep' as const },
};

const callbacks = {
  onEvaluate: async () => EVALUATION,
  onStartScan: async () => ({ jobId: 'job-1', state: 'running', stage: 'capture', message: 'Capture', progress: 0.1 } as AnalyzerScanSnapshot),
  onCancelScan: async () => ({ jobId: 'job-1', state: 'cancelling', stage: 'capture', message: 'Cancelling', progress: 0.1 } as AnalyzerScanSnapshot),
  onGetDebug: async () => ({ available: true, jobId: 'job-1', text: 'debug', artifacts: ['crop.png'] }),
};

test('renders the complete manual analyzer while optional auto-detect is unavailable', () => {
  const markup = renderToStaticMarkup(
    <AnalyzerCenter
      {...callbacks}
      autoDetectAvailable={false}
      autoDetectReason="Ollama is missing. Manual evaluation remains available."
      evaluating={false}
      options={OPTIONS}
      snapshot={{ state: 'idle', stage: 'idle', message: 'Ready', progress: 0 }}
    />,
  );

  assert.match(markup, /Analyze one gear piece/);
  assert.match(markup, /analyzer-commandbar/);
  assert.match(markup, /analyzer-dashboard/);
  assert.match(markup, /Gear input/);
  assert.match(markup, /Four rolled stats/);
  assert.match(markup, /Substat 4/);
  assert.match(markup, /Run evaluation/);
  assert.match(markup, /Ollama is missing/);
  assert.match(markup, /<button[^>]*disabled=""[^>]*>[^<]*<svg[^>]*>.*Auto-detect gear/s);
  assert.match(markup, /Ready to score/);
  assert.match(markup, /No matches yet/);
  assert.doesNotMatch(markup, /Capture, verify, and rate one gear piece/);
});

test('renders determinate scan progress and safe cancellation state', () => {
  const markup = renderToStaticMarkup(
    <AnalyzerCenter
      {...callbacks}
      autoDetectAvailable
      evaluating={false}
      options={OPTIONS}
      snapshot={{ jobId: 'job-1', state: 'running', stage: 'ocr', message: 'Reading gear text', progress: 0.55 }}
    />,
  );

  assert.match(markup, /Reading gear text/);
  assert.match(markup, /<progress[^>]*value="0.55"/);
  assert.match(markup, /Cancel scan/);
  assert.match(markup, /<fieldset[^>]*disabled=""/);
});

test('renders auto-detected evaluation results and enables debug access', () => {
  const snapshot: AnalyzerScanSnapshot = {
    jobId: 'job-1', state: 'succeeded', stage: 'complete', message: 'Gear scan complete.', progress: 1,
    result: { piece: PIECE, evaluation: SCORED_EVALUATION, debugAvailable: true },
  };
  const markup = renderToStaticMarkup(
    <AnalyzerCenter
      {...callbacks}
      autoDetectAvailable
      evaluating={false}
      options={OPTIONS}
      snapshot={snapshot}
    />,
  );

  assert.match(markup, /Gear scan complete/);
  assert.match(markup, /Archetype matches/);
  assert.match(markup, /Speed DPS: strong match/);
  assert.match(markup, /Current Gear Score 82/);
  assert.match(markup, /Potential GS/);
  assert.match(markup, /Rolls tested/);
  assert.match(markup, />KEEP</);
  assert.match(markup, /Calculation details/);
  assert.match(markup, />Debug</);
  assert.doesNotMatch(markup, /<button[^>]*disabled=""[^>]*><span>Debug<\/span>/);
});

test('surfaces scan failure without disabling manual evaluation', () => {
  const markup = renderToStaticMarkup(
    <AnalyzerCenter
      {...callbacks}
      autoDetectAvailable
      evaluating={false}
      options={OPTIONS}
      snapshot={{
        jobId: 'job-failed', state: 'failed', stage: 'failed', message: 'Gear scan failed.',
        progress: 0.2, error: 'Could not capture the slot region.',
      }}
    />,
  );

  assert.match(markup, /Gear scan failed/);
  assert.match(markup, /role="alert">Could not capture the slot region/);
  assert.doesNotMatch(markup, /<fieldset[^>]*disabled=""/);
  assert.match(markup, /Run evaluation/);
});
