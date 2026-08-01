export interface AnalyzerSubstat {
  stat: string;
  value: string;
}

export interface AnalyzerPiece {
  enhancement: string;
  slot: string;
  set: string;
  mainStat: string;
  substats: AnalyzerSubstat[];
}

export interface AnalyzerOptions {
  enhancements: string[];
  slots: string[];
  sets: string[];
  stats: string[];
  slotMainStats: Record<string, string[]>;
  restrictedSubstats: Record<string, string[]>;
  autoDetectCapabilities: string[];
}

export interface AnalyzerGearScore {
  current: number;
  potential: number;
  rolls: number;
  enhancement: number;
  recommendation: 'final' | 'keep' | 'stop';
}

export interface AnalyzerEvaluation {
  piece: AnalyzerPiece;
  archetypeText: string;
  gearScoreText: string;
  gearScore: AnalyzerGearScore | null;
}

export interface AnalyzerScanResult {
  piece: AnalyzerPiece;
  evaluation: AnalyzerEvaluation;
  debugAvailable: boolean;
}

export type AnalyzerScanState = 'idle' | 'running' | 'cancelling' | 'succeeded' | 'failed' | 'cancelled';

export interface AnalyzerScanSnapshot {
  jobId?: string;
  state: AnalyzerScanState;
  stage: string;
  message: string;
  progress: number;
  result?: AnalyzerScanResult;
  error?: string;
}

export interface AnalyzerDebug {
  available: boolean;
  jobId?: string;
  text?: string;
  artifacts: string[];
}

export type AnalyzerValidationIssues = Record<string, string>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string');
}

function isStringArrayRecord(value: unknown): value is Record<string, string[]> {
  return isRecord(value) && Object.values(value).every(isStringArray);
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  return actual.length === keys.length && actual.every((key, index) => key === [...keys].sort()[index]);
}

export function isAnalyzerOptions(value: unknown): value is AnalyzerOptions {
  if (!isRecord(value) || !hasExactKeys(value, [
    'enhancements', 'slots', 'sets', 'stats', 'slotMainStats',
    'restrictedSubstats', 'autoDetectCapabilities',
  ])) return false;
  return isStringArray(value.enhancements)
    && value.enhancements.length === 16
    && value.enhancements.every((item, index) => item === `+${index}`)
    && isStringArray(value.slots) && value.slots.length > 0
    && isStringArray(value.sets) && value.sets.length > 0
    && isStringArray(value.stats) && value.stats.length >= 4
    && isStringArrayRecord(value.slotMainStats)
    && value.slots.every((slot) => (value.slotMainStats as Record<string, string[]>)[slot]?.length > 0)
    && isStringArrayRecord(value.restrictedSubstats)
    && isStringArray(value.autoDetectCapabilities);
}

export function isAnalyzerPiece(value: unknown): value is AnalyzerPiece {
  if (!isRecord(value) || !hasExactKeys(value, ['enhancement', 'slot', 'set', 'mainStat', 'substats'])) {
    return false;
  }
  return typeof value.enhancement === 'string'
    && typeof value.slot === 'string'
    && typeof value.set === 'string'
    && typeof value.mainStat === 'string'
    && Array.isArray(value.substats)
    && value.substats.length === 4
    && value.substats.every((substat) => isRecord(substat)
      && hasExactKeys(substat, ['stat', 'value'])
      && typeof substat.stat === 'string'
      && typeof substat.value === 'string'
      && /^\d+$/.test(substat.value));
}

function isGearScore(value: unknown): value is AnalyzerGearScore {
  return isRecord(value)
    && hasExactKeys(value, ['current', 'potential', 'rolls', 'enhancement', 'recommendation'])
    && typeof value.current === 'number' && Number.isFinite(value.current)
    && typeof value.potential === 'number' && Number.isFinite(value.potential)
    && typeof value.rolls === 'number' && Number.isInteger(value.rolls)
    && typeof value.enhancement === 'number' && Number.isInteger(value.enhancement)
    && (value.recommendation === 'final' || value.recommendation === 'keep' || value.recommendation === 'stop');
}

export function isAnalyzerEvaluation(value: unknown): value is AnalyzerEvaluation {
  return isRecord(value)
    && hasExactKeys(value, ['piece', 'archetypeText', 'gearScoreText', 'gearScore'])
    && isAnalyzerPiece(value.piece)
    && typeof value.archetypeText === 'string'
    && typeof value.gearScoreText === 'string'
    && (value.gearScore === null || isGearScore(value.gearScore));
}

export function isAnalyzerScanResult(value: unknown): value is AnalyzerScanResult {
  return isRecord(value)
    && hasExactKeys(value, ['piece', 'evaluation', 'debugAvailable'])
    && isAnalyzerPiece(value.piece)
    && isAnalyzerEvaluation(value.evaluation)
    && typeof value.debugAvailable === 'boolean';
}

export function isAnalyzerScanSnapshot(value: unknown): value is AnalyzerScanSnapshot {
  if (!isRecord(value)) return false;
  const allowedKeys = new Set(['jobId', 'state', 'stage', 'message', 'progress', 'result', 'error']);
  if (Object.keys(value).some((key) => !allowedKeys.has(key))) return false;
  const states: readonly AnalyzerScanState[] = ['idle', 'running', 'cancelling', 'succeeded', 'failed', 'cancelled'];
  if (!states.includes(value.state as AnalyzerScanState)
    || typeof value.stage !== 'string'
    || typeof value.message !== 'string'
    || typeof value.progress !== 'number'
    || !Number.isFinite(value.progress)
    || value.progress < 0 || value.progress > 1) return false;
  if (value.state !== 'idle' && (typeof value.jobId !== 'string' || value.jobId.length === 0)) return false;
  if (value.state === 'idle' && value.jobId !== undefined) return false;
  if (value.state === 'succeeded' && !isAnalyzerScanResult(value.result)) return false;
  if (value.result !== undefined && !isAnalyzerScanResult(value.result)) return false;
  if (value.error !== undefined && typeof value.error !== 'string') return false;
  return value.state !== 'failed' || typeof value.error === 'string';
}

export function isAnalyzerDebug(value: unknown): value is AnalyzerDebug {
  if (!isRecord(value)) return false;
  const allowedKeys = new Set(['available', 'jobId', 'text', 'artifacts']);
  if (Object.keys(value).some((key) => !allowedKeys.has(key))
    || typeof value.available !== 'boolean'
    || !isStringArray(value.artifacts)
    || value.artifacts.some((artifact) => /^(?:[a-z]:[\\/]|[\\/])/i.test(artifact)
      || artifact.split(/[\\/]/).includes('..'))) return false;
  if (!value.available) {
    return value.jobId === undefined && value.text === undefined;
  }
  return typeof value.jobId === 'string' && value.jobId.length > 0 && typeof value.text === 'string';
}

export function validateAnalyzerPiece(piece: AnalyzerPiece, options: AnalyzerOptions): AnalyzerValidationIssues {
  const issues: AnalyzerValidationIssues = {};
  if (!options.enhancements.includes(piece.enhancement)) issues.enhancement = 'Choose +0 through +15.';
  if (!options.slots.includes(piece.slot)) issues.slot = 'Choose a supported equipment slot.';
  if (!options.sets.includes(piece.set)) issues.set = 'Choose a supported equipment set.';
  if (!(options.slotMainStats[piece.slot] ?? []).includes(piece.mainStat)) {
    issues.mainStat = 'Choose a main stat available for this slot.';
  }
  if (piece.substats.length !== 4) issues.substats = 'Exactly four substats are required.';
  const seen = new Set<string>();
  const restricted = new Set(options.restrictedSubstats[piece.slot] ?? []);
  piece.substats.forEach((substat, index) => {
    if (!options.stats.includes(substat.stat)) {
      issues[`substats.${index}.stat`] = 'Choose a supported substat.';
    } else if (substat.stat === piece.mainStat) {
      issues[`substats.${index}.stat`] = 'A substat cannot match the main stat.';
    } else if (restricted.has(substat.stat)) {
      issues[`substats.${index}.stat`] = 'This substat cannot appear on the selected slot.';
    } else if (seen.has(substat.stat)) {
      issues[`substats.${index}.stat`] = 'Each substat must be unique.';
    } else {
      seen.add(substat.stat);
    }
    if (!/^\d+$/.test(substat.value)) {
      issues[`substats.${index}.value`] = 'Enter a non-negative whole number.';
    }
  });
  return issues;
}

export function availableSubstats(
  piece: AnalyzerPiece,
  options: AnalyzerOptions,
  index: number,
): string[] {
  const selectedElsewhere = new Set(piece.substats
    .filter((_item, itemIndex) => itemIndex !== index)
    .map((item) => item.stat));
  const restricted = new Set(options.restrictedSubstats[piece.slot] ?? []);
  return options.stats.filter((stat) => stat !== piece.mainStat
    && !restricted.has(stat)
    && !selectedElsewhere.has(stat));
}

export function createDefaultAnalyzerPiece(options: AnalyzerOptions): AnalyzerPiece {
  const slot = options.slots[0];
  const mainStat = options.slotMainStats[slot][0];
  const restricted = new Set(options.restrictedSubstats[slot] ?? []);
  const stats = options.stats.filter((stat) => stat !== mainStat && !restricted.has(stat)).slice(0, 4);
  return {
    enhancement: options.enhancements[0],
    slot,
    set: options.sets.includes('Speed Set') ? 'Speed Set' : options.sets[0],
    mainStat,
    substats: stats.map((stat) => ({ stat, value: '0' })),
  };
}

export function reconcileAnalyzerPiece(piece: AnalyzerPiece, options: AnalyzerOptions): AnalyzerPiece {
  const slot = options.slots.includes(piece.slot) ? piece.slot : options.slots[0];
  const allowedMains = options.slotMainStats[slot];
  const mainStat = allowedMains.includes(piece.mainStat) ? piece.mainStat : allowedMains[0];
  const restricted = new Set(options.restrictedSubstats[slot] ?? []);
  const available = options.stats.filter((stat) => stat !== mainStat && !restricted.has(stat));
  const seen = new Set<string>();
  const substats = piece.substats.slice(0, 4).map((item) => {
    const selected = available.includes(item.stat) && !seen.has(item.stat)
      ? item.stat
      : available.find((stat) => !seen.has(stat)) ?? '';
    seen.add(selected);
    return { stat: selected, value: /^\d+$/.test(item.value) ? item.value : '0' };
  });
  while (substats.length < 4) {
    const selected = available.find((stat) => !seen.has(stat)) ?? '';
    seen.add(selected);
    substats.push({ stat: selected, value: '0' });
  }
  return {
    enhancement: options.enhancements.includes(piece.enhancement) ? piece.enhancement : options.enhancements[0],
    slot,
    set: options.sets.includes(piece.set) ? piece.set : options.sets[0],
    mainStat,
    substats,
  };
}
