import {
  OPTIMIZER_DERIVED_METRICS,
  OPTIMIZER_PRIMARY_STATS,
  type OptimizerDerivedMetricId,
  type OptimizerPrimaryStatKey,
} from './optimizer-profile';
import type { OptimizerResultCategory } from './optimizer-results';

export interface OptimizerResultDetailRequest {
  runId: string;
  queryId: string;
  rowKey: string;
}

export interface OptimizerResultEquipResult {
  state: 'equipped';
  heroName: string;
  equippedCount: number;
  alreadyEquipped: number;
  movedFromOtherHeroes: number;
  newlyEquipped: number;
  unequippedFromHero: number;
  inventoryEquippedItems: number;
}

export type ConstraintStatus = 'unrestricted' | 'satisfied' | 'outside-target';

export interface OptimizerConstraintAxis {
  fieldId: string;
  label: string;
  actual: string;
  minimum: number | null;
  maximum: number | null;
  status: ConstraintStatus;
}

export interface OptimizerGearStat {
  statId: string;
  label: string;
  value: number;
}

export interface OptimizerOwnedGearDetail {
  gearKey: string;
  slotId: string;
  slotLabel: string;
  setId: string;
  setLabel: string;
  rankId: string;
  rankLabel: string;
  itemLevel: number;
  enhance: number;
  gearScore: number;
  locked: boolean;
  equippedStatus: 'unequipped' | 'selected-hero' | 'other-hero';
  equippedHeroName: string | null;
  mainStat: OptimizerGearStat;
  substats: OptimizerGearStat[];
}

export interface OptimizerDetailSet {
  setId: string;
  label: string;
  pieces: number;
  activations: number;
  requiredPieces: number;
  status: 'target-complete' | 'target-incomplete' | 'off-target';
}

export type OptimizerReplacementGuidance = {
  kind: 'set-complete';
  message: string;
};

export interface OptimizerResultBuildDetail {
  category: OptimizerResultCategory;
  replacementCount: number;
  equippedCount: number;
  priorityScore: number;
  constraintDistance: number;
  primaryStats: Record<OptimizerPrimaryStatKey, string>;
  derivedMetrics: Record<OptimizerDerivedMetricId, string>;
  constraints: {
    status: 'satisfied' | 'outside-target';
    normalizedDistance: number;
    primary: OptimizerConstraintAxis[];
    derived: OptimizerConstraintAxis[];
  };
  sets: OptimizerDetailSet[];
  gear: OptimizerOwnedGearDetail[];
  guidance: OptimizerReplacementGuidance;
}

export type OptimizerResultDetailState = 'idle' | 'loading' | 'completed' | 'failed';
export interface OptimizerResultDetailSnapshot {
  sequence: number;
  state: OptimizerResultDetailState;
  selectionId: string | null;
  runId: string | null;
  queryId: string | null;
  rowKey: string | null;
  detail: OptimizerResultBuildDetail | null;
  failure: { code: string; message: string } | null;
}

const DECIMAL = /^-?(?:0|[1-9][0-9]*)$/;
const PRIVATE_PATH = /(?:[A-Za-z]:[\\/]|\\\\|file:|(?:^|\s)\/\S+)/i;
const SLOTS = ['slot.weapon', 'slot.helmet', 'slot.armor', 'slot.necklace', 'slot.ring', 'slot.boots'] as const;
const RANKS = ['rank.normal', 'rank.good', 'rank.rare', 'rank.heroic', 'rank.epic'];

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function exact(value: Record<string, unknown>, fields: readonly string[]): boolean {
  const keys = Object.keys(value);
  return keys.length === fields.length && fields.every((field) => Object.hasOwn(value, field));
}

function text(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= 512 && !PRIVATE_PATH.test(value);
}

function decimal(value: unknown): value is string {
  return typeof value === 'string' && DECIMAL.test(value);
}

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function optionalFinite(value: unknown): boolean {
  return value === null || finite(value);
}

function statMap(value: unknown, keys: readonly string[]): boolean {
  return record(value) && exact(value, keys) && keys.every((key) => decimal(value[key]));
}

function isConstraintAxis(value: unknown, expectedId: string): value is OptimizerConstraintAxis {
  return record(value) && exact(value, ['fieldId', 'label', 'actual', 'minimum', 'maximum', 'status'])
    && value.fieldId === expectedId && text(value.label) && decimal(value.actual)
    && optionalFinite(value.minimum) && optionalFinite(value.maximum)
    && ['unrestricted', 'satisfied', 'outside-target'].includes(String(value.status));
}

function isGearStat(value: unknown): value is OptimizerGearStat {
  return record(value) && exact(value, ['statId', 'label', 'value'])
    && text(value.statId) && text(value.label) && finite(value.value);
}

export function isOptimizerOwnedGearDetail(
  value: unknown,
  slotId?: string,
): value is OptimizerOwnedGearDetail {
  if (!record(value) || !exact(value, [
    'gearKey', 'slotId', 'slotLabel', 'setId', 'setLabel', 'rankId', 'rankLabel',
    'itemLevel', 'enhance', 'gearScore', 'locked', 'equippedStatus',
    'equippedHeroName', 'mainStat', 'substats',
  ])) return false;
  const equippedStatus = String(value.equippedStatus);
  return text(value.gearKey)
    && (slotId === undefined
      ? SLOTS.includes(value.slotId as typeof SLOTS[number])
      : value.slotId === slotId)
    && text(value.slotLabel)
    && text(value.setId) && text(value.setLabel) && RANKS.includes(String(value.rankId)) && text(value.rankLabel)
    && Number.isInteger(value.itemLevel) && Number(value.itemLevel) >= 1 && Number(value.itemLevel) <= 100
    && Number.isInteger(value.enhance) && Number(value.enhance) >= 0 && Number(value.enhance) <= 15
    && Number.isInteger(value.gearScore) && Number(value.gearScore) >= 0
    && typeof value.locked === 'boolean'
    && ['unequipped', 'selected-hero', 'other-hero'].includes(equippedStatus)
    && (
      equippedStatus === 'unequipped'
        ? value.equippedHeroName === null
        : value.equippedHeroName === null || text(value.equippedHeroName)
    )
    && isGearStat(value.mainStat)
    && Array.isArray(value.substats) && value.substats.length <= 4 && value.substats.every(isGearStat);
}

function isDetailSet(value: unknown): value is OptimizerDetailSet {
  return record(value) && exact(value, ['setId', 'label', 'pieces', 'activations', 'requiredPieces', 'status'])
    && text(value.setId) && text(value.label)
    && Number.isInteger(value.pieces) && Number(value.pieces) >= 0 && Number(value.pieces) <= 6
    && Number.isInteger(value.activations) && Number(value.activations) >= 0 && Number(value.activations) <= 3
    && Number.isInteger(value.requiredPieces) && Number(value.requiredPieces) >= 0 && Number(value.requiredPieces) <= 6
    && ['target-complete', 'target-incomplete', 'off-target'].includes(String(value.status));
}

function isGuidance(value: unknown): value is OptimizerReplacementGuidance {
  return record(value)
    && exact(value, ['kind', 'message'])
    && value.kind === 'set-complete'
    && text(value.message);
}

export function isOptimizerResultDetailRequest(value: unknown): value is OptimizerResultDetailRequest {
  return record(value) && exact(value, ['runId', 'queryId', 'rowKey'])
    && text(value.runId) && text(value.queryId) && text(value.rowKey);
}

export function isOptimizerResultEquipResult(value: unknown): value is OptimizerResultEquipResult {
  if (!record(value) || !exact(value, [
    'state', 'heroName', 'equippedCount', 'alreadyEquipped',
    'movedFromOtherHeroes', 'newlyEquipped', 'unequippedFromHero',
    'inventoryEquippedItems',
  ])) return false;
  const boundedCount = (candidate: unknown): boolean => (
    Number.isInteger(candidate) && Number(candidate) >= 0 && Number(candidate) <= 6
  );
  return value.state === 'equipped'
    && text(value.heroName)
    && value.equippedCount === 6
    && boundedCount(value.alreadyEquipped)
    && boundedCount(value.movedFromOtherHeroes)
    && boundedCount(value.newlyEquipped)
    && boundedCount(value.unequippedFromHero)
    && Number(value.alreadyEquipped) + Number(value.movedFromOtherHeroes)
      + Number(value.newlyEquipped) === 6
    && Number.isSafeInteger(value.inventoryEquippedItems)
    && Number(value.inventoryEquippedItems) >= 0;
}

export function isOptimizerResultBuildDetail(value: unknown): value is OptimizerResultBuildDetail {
  if (!record(value) || !exact(value, [
    'category', 'replacementCount', 'equippedCount', 'priorityScore', 'constraintDistance',
    'primaryStats', 'derivedMetrics', 'constraints', 'sets', 'gear', 'guidance',
  ])) return false;
  const category = String(value.category);
  const primaryKeys = OPTIMIZER_PRIMARY_STATS.map(({ key }) => key);
  const derivedKeys = OPTIMIZER_DERIVED_METRICS.map(({ key }) => key);
  if (!Array.isArray(value.gear) || value.gear.length !== 6
    || !value.gear.every((item, index) => isOptimizerOwnedGearDetail(item, SLOTS[index]))) return false;
  const gearKeys = new Set(value.gear.map((item) => item.gearKey));
  const constraints = value.constraints;
  return category === 'exact'
    && value.replacementCount === 0
    && Number.isInteger(value.equippedCount) && Number(value.equippedCount) >= 0 && Number(value.equippedCount) <= 6
    && Number.isInteger(value.priorityScore) && value.constraintDistance === 0
    && statMap(value.primaryStats, primaryKeys) && statMap(value.derivedMetrics, derivedKeys)
    && record(constraints) && exact(constraints, ['status', 'normalizedDistance', 'primary', 'derived'])
    && constraints.status === 'satisfied'
    && constraints.normalizedDistance === 0
    && Array.isArray(constraints.primary) && constraints.primary.length === 8
    && constraints.primary.every((item, index) => isConstraintAxis(item, primaryKeys[index]))
    && Array.isArray(constraints.derived) && constraints.derived.length === 15
    && constraints.derived.every((item, index) => isConstraintAxis(item, derivedKeys[index]))
    && Array.isArray(value.sets) && value.sets.length <= 24 && value.sets.every(isDetailSet)
    && gearKeys.size === 6 && isGuidance(value.guidance);
}

export function isOptimizerResultDetailSnapshot(value: unknown): value is OptimizerResultDetailSnapshot {
  if (!record(value) || !exact(value, [
    'sequence', 'state', 'selectionId', 'runId', 'queryId', 'rowKey', 'detail', 'failure',
  ])) return false;
  const state = String(value.state);
  const idle = state === 'idle' && value.selectionId === null && value.runId === null
    && value.queryId === null && value.rowKey === null && value.detail === null && value.failure === null;
  const identified = text(value.selectionId) && text(value.runId) && text(value.queryId) && text(value.rowKey);
  const loading = state === 'loading' && identified && value.detail === null && value.failure === null;
  const completed = state === 'completed' && identified && isOptimizerResultBuildDetail(value.detail) && value.failure === null;
  const failure = value.failure;
  const failed = state === 'failed' && identified && value.detail === null && record(failure)
    && exact(failure, ['code', 'message']) && text(failure.code) && text(failure.message);
  return Number.isInteger(value.sequence) && Number(value.sequence) >= 0
    && ['idle', 'loading', 'completed', 'failed'].includes(state)
    && (idle || loading || completed || failed);
}
