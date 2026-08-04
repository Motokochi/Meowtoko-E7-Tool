import type {
  OptimizerDataResetResult,
  OptimizerInventoryImportResult,
  OptimizerInventorySnapshot,
} from './shared/optimizer-inventory';

export const EMPTY_INVENTORY: OptimizerInventorySnapshot = {
  state: 'empty', totalItems: 0, equippedItems: 0, lockedItems: 0, lastImport: null,
  gear: [],
  itemsBySlot: [
    { slot: 'slot.weapon', label: 'Weapon', count: 0 },
    { slot: 'slot.helmet', label: 'Helmet', count: 0 },
    { slot: 'slot.armor', label: 'Armor', count: 0 },
    { slot: 'slot.necklace', label: 'Necklace', count: 0 },
    { slot: 'slot.ring', label: 'Ring', count: 0 },
    { slot: 'slot.boots', label: 'Boots', count: 0 },
  ],
};

export const IMPORT_RESULT: OptimizerInventoryImportResult = {
  inventory: {
    state: 'ready', totalItems: 2, equippedItems: 1, lockedItems: 1,
    gear: [{
      gearKey: 'fixture-armor',
      slotId: 'slot.armor',
      slotLabel: 'Armor',
      setId: 'set.defense',
      setLabel: 'Defense',
      rankId: 'rank.epic',
      rankLabel: 'Epic',
      itemLevel: 85,
      enhance: 15,
      gearScore: 37,
      reforgedGearScore: 37,
      combatGearScore: 22,
      supportGearScore: 37,
      archetypeAnalysis: {
        verdict: 'destroy',
        reason: 'No archetype matches this set, main stat, and at least three substats.',
        rollHistoryAvailable: true,
        matches: [],
      },
      locked: true,
      equippedStatus: 'other-hero',
      equippedHeroName: 'Alencia',
      mainStat: { statId: 'item_stat.flat_defense', label: 'Flat Defense', value: 310 },
      substats: [
        { statId: 'item_stat.health_percent', label: 'Health', value: 18, reforgedValue: 20 },
        { statId: 'item_stat.effect_resistance_percent', label: 'Effect Resistance', value: 12, reforgedValue: 15 },
      ],
    }],
    lastImport: {
      importedAt: '2026-07-22T12:34:56.000Z', sourceEncoding: 'utf-8', sourceVariant: 'enriched',
      sourceItemCount: 2, acceptedCount: 2, rejectedCount: 0, warningCount: 0,
      insertedCount: 2, updatedCount: 0, unchangedCount: 0, conflictCount: 0,
      unseenExistingCount: 0,
    },
    itemsBySlot: [
      { slot: 'slot.weapon', label: 'Weapon', count: 0 },
      { slot: 'slot.helmet', label: 'Helmet', count: 0 },
      { slot: 'slot.armor', label: 'Armor', count: 1 },
      { slot: 'slot.necklace', label: 'Necklace', count: 0 },
      { slot: 'slot.ring', label: 'Ring', count: 0 },
      { slot: 'slot.boots', label: 'Boots', count: 1 },
    ],
  },
  report: {
    importedAt: '2026-07-22T12:34:56.000Z', sourceEncoding: 'utf-8', sourceVariant: 'enriched',
    sourceItemCount: 2, acceptedCount: 2, rejectedCount: 0, warningCount: 0,
    warningItemCount: 0, insertedCount: 2, updatedCount: 0, unchangedCount: 0,
    conflictCount: 0, unseenExistingCount: 0, equippedItemCount: 1,
    importedHeroCount: 1, resultingInventoryCount: 2, repositoryCreated: true,
    repositoryMigrated: false, issues: [], additionalIssueCount: 0,
  },
};

export const RESET_RESULT: OptimizerDataResetResult = {
  state: 'cleared',
  inventory: EMPTY_INVENTORY,
  removed: {
    databaseFiles: 1,
    profileFiles: 2,
    resultArtifacts: 8,
  },
};
