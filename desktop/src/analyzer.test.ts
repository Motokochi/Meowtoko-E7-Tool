import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  availableSubstats,
  createDefaultAnalyzerPiece,
  isAnalyzerDebug,
  isAnalyzerEvaluation,
  isAnalyzerOptions,
  isAnalyzerPiece,
  isAnalyzerScanSnapshot,
  reconcileAnalyzerPiece,
  validateAnalyzerPiece,
  type AnalyzerOptions,
  type AnalyzerPiece,
} from './shared/analyzer';

export const ANALYZER_OPTIONS: AnalyzerOptions = {
  enhancements: Array.from({ length: 16 }, (_item, index) => `+${index}`),
  slots: ['Weapon', 'Helmet', 'Armor', 'Necklace', 'Ring', 'Boots'],
  sets: ['Speed Set', 'Attack Set'],
  stats: [
    'Flat Attack', 'Attack', 'Defense', 'Flat Defense', 'Flat Health', 'Health',
    'Speed', 'Critical Hit Chance', 'Critical Hit Damage', 'Effectiveness', 'Effect Resistance',
  ],
  slotMainStats: {
    Weapon: ['Flat Attack'], Helmet: ['Flat Health'], Armor: ['Flat Defense'],
    Necklace: ['Critical Hit Chance', 'Health'], Ring: ['Effectiveness', 'Attack'], Boots: ['Speed', 'Attack'],
  },
  restrictedSubstats: {
    Weapon: ['Flat Defense', 'Defense'], Helmet: [], Armor: ['Flat Attack', 'Attack'],
    Necklace: [], Ring: [], Boots: [],
  },
  autoDetectCapabilities: ['tesseract', 'ollama', 'adb'],
};

export const ANALYZER_PIECE: AnalyzerPiece = {
  enhancement: '+9',
  slot: 'Weapon',
  set: 'Speed Set',
  mainStat: 'Flat Attack',
  substats: [
    { stat: 'Attack', value: '12' },
    { stat: 'Flat Health', value: '120' },
    { stat: 'Speed', value: '4' },
    { stat: 'Critical Hit Chance', value: '5' },
  ],
};

export const ANALYZER_EVALUATION = {
  piece: ANALYZER_PIECE,
  archetypeText: 'MATCHES:\n- Fast bruiser (4/4)',
  gearScoreText: 'Current GS: 40 | Potential GS: 56',
  gearScore: {
    current: 40,
    potential: 56,
    rolls: 3,
    enhancement: 9,
    recommendation: 'stop' as const,
  },
};

test('validates analyzer options, pieces, evaluations, scans, and safe debug metadata', () => {
  assert.equal(isAnalyzerOptions(ANALYZER_OPTIONS), true);
  assert.equal(isAnalyzerPiece(ANALYZER_PIECE), true);
  assert.equal(isAnalyzerEvaluation(ANALYZER_EVALUATION), true);
  assert.equal(isAnalyzerScanSnapshot({
    jobId: 'job-1',
    state: 'succeeded',
    stage: 'complete',
    message: 'Complete',
    progress: 1,
    result: { piece: ANALYZER_PIECE, evaluation: ANALYZER_EVALUATION, debugAvailable: true },
  }), true);
  assert.equal(isAnalyzerDebug({ available: true, jobId: 'job-1', text: 'debug', artifacts: ['crop.png'] }), true);
  assert.equal(isAnalyzerDebug({ available: true, text: 'debug', artifacts: ['C:/private/crop.png'] }), false);
  assert.equal(isAnalyzerScanSnapshot({ state: 'running', stage: 'ocr', message: 'OCR', progress: 2 }), false);
});

test('enforces slot main-stat, restricted-stat, uniqueness, and numerical form constraints', () => {
  const invalid: AnalyzerPiece = {
    ...ANALYZER_PIECE,
    mainStat: 'Health',
    substats: [
      { stat: 'Defense', value: '4' },
      { stat: 'Speed', value: 'fast' },
      { stat: 'Speed', value: '4' },
      { stat: 'Critical Hit Chance', value: '5' },
    ],
  };
  const issues = validateAnalyzerPiece(invalid, ANALYZER_OPTIONS);

  assert.ok(issues.mainStat);
  assert.ok(issues['substats.0.stat']);
  assert.ok(issues['substats.1.value']);
  assert.ok(issues['substats.2.stat']);
});

test('creates and reconciles a valid four-substat piece when the slot changes', () => {
  const initial = createDefaultAnalyzerPiece(ANALYZER_OPTIONS);
  assert.equal(initial.slot, 'Weapon');
  assert.equal(initial.mainStat, 'Flat Attack');
  assert.equal(initial.substats.length, 4);
  assert.deepEqual(validateAnalyzerPiece(initial, ANALYZER_OPTIONS), {});

  const changed = reconcileAnalyzerPiece({ ...ANALYZER_PIECE, slot: 'Armor' }, ANALYZER_OPTIONS);
  assert.equal(changed.mainStat, 'Flat Defense');
  assert.equal(new Set(changed.substats.map((item) => item.stat)).size, 4);
  assert.ok(changed.substats.every((item) => item.stat !== 'Attack' && item.stat !== 'Flat Attack'));
  assert.deepEqual(validateAnalyzerPiece(changed, ANALYZER_OPTIONS), {});
  assert.ok(!availableSubstats(changed, ANALYZER_OPTIONS, 0).includes(changed.substats[1].stat));
});
