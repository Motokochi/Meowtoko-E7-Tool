import {
  isOptimizerOwnedGearDetail,
  type OptimizerOwnedGearDetail,
} from './optimizer-result-detail';

export const OPTIMIZER_GEAR_SLOTS = [
  { slot: 'slot.weapon', label: 'Weapon' },
  { slot: 'slot.helmet', label: 'Helmet' },
  { slot: 'slot.armor', label: 'Armor' },
  { slot: 'slot.necklace', label: 'Necklace' },
  { slot: 'slot.ring', label: 'Ring' },
  { slot: 'slot.boots', label: 'Boots' },
] as const;

export type OptimizerGearSlotId = typeof OPTIMIZER_GEAR_SLOTS[number]['slot'];
export type OptimizerSourceEncoding = 'utf-8' | 'utf-8-sig';
export type OptimizerSourceVariant = 'scanner' | 'items-only' | 'enriched';
export type OptimizerInventoryIssueKind = 'warning' | 'rejection' | 'conflict';
const MAX_VISIBLE_GEAR = 5_000;

export interface OptimizerInventorySlotCount {
  slot: OptimizerGearSlotId;
  label: string;
  count: number;
}

export interface OptimizerInventoryLastImport {
  importedAt: string;
  sourceEncoding: OptimizerSourceEncoding;
  sourceVariant: OptimizerSourceVariant;
  sourceItemCount: number;
  acceptedCount: number;
  rejectedCount: number;
  warningCount: number;
  insertedCount: number;
  updatedCount: number;
  unchangedCount: number;
  conflictCount: number;
  unseenExistingCount: number;
}

export interface OptimizerInventorySnapshot {
  state: 'empty' | 'ready';
  totalItems: number;
  equippedItems: number;
  lockedItems: number;
  gear: OptimizerInventoryGearItem[];
  lastImport: OptimizerInventoryLastImport | null;
  itemsBySlot: OptimizerInventorySlotCount[];
}

export interface OptimizerInventoryGearItem extends OptimizerOwnedGearDetail {
  reforgedGearScore: number;
  combatGearScore: number;
  supportGearScore: number;
}

export interface OptimizerInventoryIssue {
  kind: OptimizerInventoryIssueKind;
  code: string;
  documentPath: string;
  message: string;
  itemIndex: number | null;
  heroIndex: number | null;
}

export interface OptimizerInventoryImportReport {
  importedAt: string;
  sourceEncoding: OptimizerSourceEncoding;
  sourceVariant: OptimizerSourceVariant;
  sourceItemCount: number;
  acceptedCount: number;
  rejectedCount: number;
  warningCount: number;
  warningItemCount: number;
  insertedCount: number;
  updatedCount: number;
  unchangedCount: number;
  conflictCount: number;
  unseenExistingCount: number;
  equippedItemCount: number;
  importedHeroCount: number;
  resultingInventoryCount: number;
  repositoryCreated: boolean;
  repositoryMigrated: boolean;
  issues: OptimizerInventoryIssue[];
  additionalIssueCount: number;
}

export interface OptimizerInventoryImportResult {
  inventory: OptimizerInventorySnapshot;
  report: OptimizerInventoryImportReport;
}

export interface OptimizerInventoryCaptureState {
  state: 'capturing';
}

export interface OptimizerDataResetResult {
  state: 'cleared';
  inventory: OptimizerInventorySnapshot;
  removed: {
    databaseFiles: number;
    profileFiles: number;
    resultArtifacts: number;
  };
}

export type OptimizerInventorySelectionResult =
  | { outcome: 'cancelled' }
  | { outcome: 'imported'; import: OptimizerInventoryImportResult };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const expected = [...keys].sort();
  const actual = Object.keys(value).sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function isCount(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0;
}

function isTimestamp(value: unknown): value is string {
  return typeof value === 'string'
    && value.endsWith('Z')
    && Number.isFinite(Date.parse(value));
}

function isEncoding(value: unknown): value is OptimizerSourceEncoding {
  return value === 'utf-8' || value === 'utf-8-sig';
}

function isVariant(value: unknown): value is OptimizerSourceVariant {
  return value === 'scanner' || value === 'items-only' || value === 'enriched';
}

function isSlotCount(value: unknown, index: number): value is OptimizerInventorySlotCount {
  if (!isRecord(value) || !hasExactKeys(value, ['slot', 'label', 'count'])) return false;
  const expected = OPTIMIZER_GEAR_SLOTS[index];
  return value.slot === expected?.slot
    && value.label === expected.label
    && isCount(value.count);
}

function isLastImport(value: unknown): value is OptimizerInventoryLastImport {
  if (!isRecord(value) || !hasExactKeys(value, [
    'importedAt', 'sourceEncoding', 'sourceVariant', 'sourceItemCount', 'acceptedCount',
    'rejectedCount', 'warningCount', 'insertedCount', 'updatedCount', 'unchangedCount',
    'conflictCount', 'unseenExistingCount',
  ])) return false;
  const counts = [
    value.sourceItemCount, value.acceptedCount, value.rejectedCount, value.warningCount,
    value.insertedCount, value.updatedCount, value.unchangedCount, value.conflictCount,
    value.unseenExistingCount,
  ];
  return isTimestamp(value.importedAt)
    && isEncoding(value.sourceEncoding)
    && isVariant(value.sourceVariant)
    && counts.every(isCount)
    && value.sourceItemCount === (value.acceptedCount as number) + (value.rejectedCount as number)
    && value.acceptedCount === (value.insertedCount as number) + (value.updatedCount as number)
      + (value.unchangedCount as number) + (value.conflictCount as number);
}

export function isOptimizerInventorySnapshot(value: unknown): value is OptimizerInventorySnapshot {
  if (!isRecord(value) || !hasExactKeys(value, [
    'state', 'totalItems', 'equippedItems', 'lockedItems', 'gear', 'lastImport', 'itemsBySlot',
  ])) return false;
  if (value.state !== 'empty' && value.state !== 'ready') return false;
  if (!isCount(value.totalItems) || !isCount(value.equippedItems) || !isCount(value.lockedItems)) return false;
  if (value.equippedItems > value.totalItems || value.lockedItems > value.totalItems) return false;
  if (value.lastImport !== null && !isLastImport(value.lastImport)) return false;
  if (!Array.isArray(value.gear)
    || value.gear.length > MAX_VISIBLE_GEAR
    || !value.gear.every((gear) => (
      isRecord(gear)
      && hasExactKeys(gear, [
        'gearKey', 'slotId', 'slotLabel', 'setId', 'setLabel', 'rankId', 'rankLabel',
        'itemLevel', 'enhance', 'gearScore', 'reforgedGearScore', 'combatGearScore',
        'supportGearScore', 'locked', 'equippedStatus', 'equippedHeroName', 'mainStat',
        'substats',
      ])
      && isOptimizerOwnedGearDetail({
        gearKey: gear.gearKey,
        slotId: gear.slotId,
        slotLabel: gear.slotLabel,
        setId: gear.setId,
        setLabel: gear.setLabel,
        rankId: gear.rankId,
        rankLabel: gear.rankLabel,
        itemLevel: gear.itemLevel,
        enhance: gear.enhance,
        gearScore: gear.gearScore,
        locked: gear.locked,
        equippedStatus: gear.equippedStatus,
        equippedHeroName: gear.equippedHeroName,
        mainStat: gear.mainStat,
        substats: gear.substats,
      })
      && isCount(gear.reforgedGearScore)
      && isCount(gear.combatGearScore)
      && isCount(gear.supportGearScore)
      && gear.gearScore === gear.reforgedGearScore
      && gear.enhance === 15
    ))) return false;
  const gear = value.gear as OptimizerInventoryGearItem[];
  if (gear.length > value.totalItems
    || new Set(gear.map((item) => item.gearKey)).size !== gear.length
    || gear.filter((item) => item.equippedStatus !== 'unequipped').length > value.equippedItems
    || gear.filter((item) => item.locked).length > value.lockedItems) return false;
  if (!Array.isArray(value.itemsBySlot)
    || value.itemsBySlot.length !== OPTIMIZER_GEAR_SLOTS.length
    || !value.itemsBySlot.every(isSlotCount)) return false;
  if (value.itemsBySlot.reduce((total, item) => total + item.count, 0) !== value.totalItems) return false;
  return value.state !== 'empty'
    || (
      value.totalItems === 0
      && value.equippedItems === 0
      && value.lockedItems === 0
      && value.gear.length === 0
      && value.lastImport === null
    );
}

function isIssue(value: unknown): value is OptimizerInventoryIssue {
  if (!isRecord(value) || !hasExactKeys(value, [
    'kind', 'code', 'documentPath', 'message', 'itemIndex', 'heroIndex',
  ])) return false;
  if (value.kind !== 'warning' && value.kind !== 'rejection' && value.kind !== 'conflict') return false;
  if (typeof value.code !== 'string' || !value.code
    || typeof value.documentPath !== 'string' || !value.documentPath
    || typeof value.message !== 'string' || !value.message) return false;
  const validIndex = (index: unknown): boolean => index === null || isCount(index);
  if (!validIndex(value.itemIndex) || !validIndex(value.heroIndex)) return false;
  if (value.itemIndex !== null && value.heroIndex !== null) return false;
  return value.kind === 'warning' || value.itemIndex !== null;
}

export function isOptimizerInventoryImportReport(value: unknown): value is OptimizerInventoryImportReport {
  if (!isRecord(value) || !hasExactKeys(value, [
    'importedAt', 'sourceEncoding', 'sourceVariant', 'sourceItemCount', 'acceptedCount',
    'rejectedCount', 'warningCount', 'warningItemCount', 'insertedCount', 'updatedCount',
    'unchangedCount', 'conflictCount', 'unseenExistingCount', 'equippedItemCount',
    'importedHeroCount', 'resultingInventoryCount', 'repositoryCreated',
    'repositoryMigrated', 'issues', 'additionalIssueCount',
  ])) return false;
  const counts = [
    value.sourceItemCount, value.acceptedCount, value.rejectedCount, value.warningCount,
    value.warningItemCount, value.insertedCount, value.updatedCount, value.unchangedCount,
    value.conflictCount, value.unseenExistingCount, value.equippedItemCount,
    value.importedHeroCount, value.resultingInventoryCount, value.additionalIssueCount,
  ];
  if (!isTimestamp(value.importedAt) || !isEncoding(value.sourceEncoding) || !isVariant(value.sourceVariant)
    || !counts.every(isCount)
    || typeof value.repositoryCreated !== 'boolean'
    || typeof value.repositoryMigrated !== 'boolean'
    || !Array.isArray(value.issues)
    || value.issues.length > 20
    || !value.issues.every(isIssue)) return false;
  if (value.repositoryCreated && value.repositoryMigrated) return false;
  if (value.sourceItemCount !== (value.acceptedCount as number) + (value.rejectedCount as number)) return false;
  if (value.acceptedCount !== (value.insertedCount as number) + (value.updatedCount as number)
    + (value.unchangedCount as number) + (value.conflictCount as number)) return false;
  if ((value.warningItemCount as number) > (value.acceptedCount as number)
    || (value.equippedItemCount as number) > (value.acceptedCount as number)) return false;
  const issueCount = (value.warningCount as number) + (value.rejectedCount as number) + (value.conflictCount as number);
  return value.issues.length + (value.additionalIssueCount as number) === issueCount;
}

export function isOptimizerInventoryImportResult(value: unknown): value is OptimizerInventoryImportResult {
  return isRecord(value)
    && hasExactKeys(value, ['inventory', 'report'])
    && isOptimizerInventorySnapshot(value.inventory)
    && isOptimizerInventoryImportReport(value.report)
    && value.inventory.state === 'ready'
    && value.inventory.totalItems === value.report.resultingInventoryCount
    && value.inventory.lastImport?.importedAt === value.report.importedAt;
}

export function isOptimizerInventoryCaptureState(value: unknown): value is OptimizerInventoryCaptureState {
  return isRecord(value)
    && hasExactKeys(value, ['state'])
    && value.state === 'capturing';
}

export function isOptimizerDataResetResult(value: unknown): value is OptimizerDataResetResult {
  return isRecord(value)
    && hasExactKeys(value, ['state', 'inventory', 'removed'])
    && value.state === 'cleared'
    && isOptimizerInventorySnapshot(value.inventory)
    && value.inventory.state === 'empty'
    && isRecord(value.removed)
    && hasExactKeys(value.removed, ['databaseFiles', 'profileFiles', 'resultArtifacts'])
    && isCount(value.removed.databaseFiles)
    && isCount(value.removed.profileFiles)
    && isCount(value.removed.resultArtifacts);
}

export function isOptimizerInventorySelectionResult(value: unknown): value is OptimizerInventorySelectionResult {
  if (!isRecord(value)) return false;
  if (value.outcome === 'cancelled') return hasExactKeys(value, ['outcome']);
  return value.outcome === 'imported'
    && hasExactKeys(value, ['outcome', 'import'])
    && isOptimizerInventoryImportResult(value.import);
}
