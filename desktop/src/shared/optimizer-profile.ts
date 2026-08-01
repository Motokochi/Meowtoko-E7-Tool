export const OPTIMIZER_SKILL_SLOTS = ['skill.s1', 'skill.s2', 'skill.s3'] as const;
export type OptimizerSkillSlot = typeof OPTIMIZER_SKILL_SLOTS[number];
export type OptimizerHitType = 'hit.critical' | 'hit.crushing' | 'hit.normal' | 'hit.miss';

export const OPTIMIZER_CUSTOM_BONUS_KEYS = [
  'flatAttack',
  'attackPercent',
  'flatHealth',
  'healthPercent',
  'flatDefense',
  'defensePercent',
  'speed',
  'criticalHitChancePercent',
  'effectivenessPercent',
  'effectResistancePercent',
  'finalAttackPercent',
  'finalHealthPercent',
  'finalDefensePercent',
] as const;
export type OptimizerCustomBonusKey = typeof OPTIMIZER_CUSTOM_BONUS_KEYS[number];
export type OptimizerCustomBonuses = Record<OptimizerCustomBonusKey, number | null>;

export const OPTIMIZER_PRIMARY_STATS = [
  { key: 'attack', label: 'Attack', percentage: false },
  { key: 'health', label: 'Health', percentage: false },
  { key: 'defense', label: 'Defense', percentage: false },
  { key: 'speed', label: 'Speed', percentage: false },
  { key: 'criticalHitChancePercent', label: 'Critical Hit Chance', percentage: true },
  { key: 'criticalHitDamagePercent', label: 'Critical Hit Damage', percentage: true },
  { key: 'effectivenessPercent', label: 'Effectiveness', percentage: true },
  { key: 'effectResistancePercent', label: 'Effect Resistance', percentage: true },
] as const;
export type OptimizerPrimaryStatKey = typeof OPTIMIZER_PRIMARY_STATS[number]['key'];
export interface OptimizerPrimaryStatDraft {
  minimum: number | null;
  maximum: number | null;
  priority: -1 | 0 | 1 | 2 | 3;
}
export type OptimizerPrimaryStatsDraft = Record<OptimizerPrimaryStatKey, OptimizerPrimaryStatDraft>;

export const OPTIMIZER_DERIVED_METRICS = [
  { key: 'metric.build_score', label: 'Build Score', group: 'scores' },
  { key: 'metric.cp', label: 'Combat Power', group: 'scores' },
  { key: 'metric.damage', label: 'Average Damage', group: 'damage' },
  { key: 'metric.damage_defense', label: 'Damage × Defense', group: 'damage' },
  { key: 'metric.damage_health', label: 'Damage × Health', group: 'damage' },
  { key: 'metric.damage_speed', label: 'Damage × Speed', group: 'damage' },
  { key: 'metric.ehp', label: 'Effective Health', group: 'durability' },
  { key: 'metric.ehp_speed', label: 'EHP × Speed', group: 'durability' },
  { key: 'metric.gear_score', label: 'Gear Score', group: 'scores' },
  { key: 'metric.hp_speed', label: 'Health × Speed', group: 'durability' },
  { key: 'metric.mcd', label: 'Max Critical Damage', group: 'damage' },
  { key: 'metric.mcd_speed', label: 'MCD × Speed', group: 'damage' },
  { key: 'metric.s1', label: 'S1', group: 'skills' },
  { key: 'metric.s2', label: 'S2', group: 'skills' },
  { key: 'metric.s3', label: 'S3', group: 'skills' },
] as const;
export type OptimizerDerivedMetricId = typeof OPTIMIZER_DERIVED_METRICS[number]['key'];

export const OPTIMIZER_RIGHT_SIDE_SLOTS = ['slot.necklace', 'slot.ring', 'slot.boots'] as const;
export type OptimizerRightSideSlotId = typeof OPTIMIZER_RIGHT_SIDE_SLOTS[number];
export type OptimizerSetLayout = '4+2' | '2+2+2' | 'flexible';
export type OptimizerItemProjectionMode = 'projection.current' | 'projection.reforged';

export interface OptimizerSetOption {
  setId: string;
  label: string;
  piecesRequired: 2 | 4;
  stackable: boolean;
}

export interface OptimizerMainStatOption {
  statId: string;
  label: string;
}

export interface OptimizerRightSideMainStatGroup {
  slotId: OptimizerRightSideSlotId;
  label: string;
  options: OptimizerMainStatOption[];
}

export interface OptimizerSetPatternDraft {
  kind: OptimizerSetLayout;
  sets: Array<string | null>;
}

export interface OptimizerGearFiltersDraft {
  minimumEnhance: number;
  rightSideMainStats: Record<OptimizerRightSideSlotId, string[]>;
}

export interface OptimizerHeroSummary {
  heroId: string;
  name: string;
  element: string;
  role: string;
  rarity: number;
  portraitUrl: string;
}

export interface OptimizerHeroSearchResult {
  query: string;
  results: OptimizerHeroSummary[];
}

export interface OptimizerArtifactSummary {
  artifactId: string;
  name: string;
  role: string;
  rarity: number;
  maxLevel: number;
}

export interface OptimizerArtifactSearchResult {
  query: string;
  results: OptimizerArtifactSummary[];
}

export interface OptimizerBaseProfileOption {
  profileId: string;
  label: string;
  level: number;
  stars: number;
  finalStats: Record<string, number>;
}

export interface OptimizerImprintOption {
  grade: string;
  statType: string;
  displayValue: number;
}

export interface OptimizerExclusiveEquipmentOption {
  equipmentId: string;
  statType: string;
  rolls: number[];
  skillOptions: Array<{
    optionId: string;
    label: string;
    effectDataState: 'unavailable-in-snapshot';
  }>;
}

export interface OptimizerCustomBonusField {
  key: OptimizerCustomBonusKey;
  label: string;
  percentage: boolean;
}

export interface OptimizerSkillOption {
  optionId: string;
  label: string;
  isDamaging: boolean;
}

export interface OptimizerSkillDetails {
  skill: OptimizerSkillSlot;
  label: string;
  isDamaging: boolean;
  hitTypes: OptimizerHitType[];
  sourceOptions: OptimizerSkillOption[];
  sourceTargetCount: number | null;
  sourcePenetrationPercent: number | null;
  note: string | null;
}

export interface OptimizerHeroDetails {
  hero: OptimizerHeroSummary & { zodiac: string };
  defaultProfileId: string;
  profiles: OptimizerBaseProfileOption[];
  imprints: OptimizerImprintOption[];
  exclusiveEquipment: OptimizerExclusiveEquipmentOption | null;
  customBonusFields: OptimizerCustomBonusField[];
  sets: OptimizerSetOption[];
  rightSideMainStats: OptimizerRightSideMainStatGroup[];
  skills: OptimizerSkillDetails[];
}

export interface OptimizerArtifactDraft {
  artifactId: string | null;
  level: number | null;
  attackOverride: number | null;
  healthOverride: number | null;
  defenseOverride: number | null;
}

export interface OptimizerExclusiveEquipmentDraft {
  equipmentId: string | null;
  statValue: number | null;
  skillOptionId: string | null;
}

export interface OptimizerSkillDraft {
  skill: OptimizerSkillSlot;
  sourceOptionId: string | null;
  hitType: OptimizerHitType | null;
  targetCountOverride: number | null;
  penetrationPercent: number | null;
  targetDefense: number;
}

export interface OptimizerHeroDraft {
  heroId: string;
  baseProfileId: string;
  artifact: OptimizerArtifactDraft;
  imprintGrade: string | null;
  exclusiveEquipment: OptimizerExclusiveEquipmentDraft;
  customBonuses: OptimizerCustomBonuses;
  primaryStats: OptimizerPrimaryStatsDraft;
  setPattern: OptimizerSetPatternDraft;
  includeEquipped: boolean;
  /** Retained only to read schema-v7 profiles; exact-only is always zero. */
  maximumReplacementDistance: 0;
  /** Retained only to read schema-v7 profiles; exact-only is always zero. */
  nearSetTolerancePercent: 0;
  itemProjectionMode: OptimizerItemProjectionMode;
  gearFilters: OptimizerGearFiltersDraft;
  skills: OptimizerSkillDraft[];
}

export interface OptimizerHeroDraftEnvelope {
  state: 'default' | 'saved';
  savedAt: string | null;
  schemaVersion: 7;
  draft: OptimizerHeroDraft;
  selectedArtifact: OptimizerArtifactSummary | null;
}

export interface OptimizerDraftValidationIssue {
  path: string;
  message: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const expected = [...keys].sort();
  const actual = Object.keys(value).sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function isText(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0;
}

function isNullableText(value: unknown): value is string | null {
  return value === null || isText(value);
}

function isFiniteNumber(value: unknown, minimum = 0): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= minimum;
}

function isNullableNumber(value: unknown, minimum = 0): value is number | null {
  return value === null || isFiniteNumber(value, minimum);
}

function isTimestamp(value: unknown): value is string {
  return typeof value === 'string' && value.endsWith('Z') && Number.isFinite(Date.parse(value));
}

function isHeroSummary(value: unknown): value is OptimizerHeroSummary {
  return isRecord(value)
    && hasExactKeys(value, ['heroId', 'name', 'element', 'role', 'rarity', 'portraitUrl'])
    && isText(value.heroId)
    && isText(value.name)
    && isText(value.element)
    && isText(value.role)
    && isText(value.portraitUrl)
    && Number.isInteger(value.rarity)
    && Number(value.rarity) >= 1
    && Number(value.rarity) <= 6;
}

export function isOptimizerHeroSearchResult(value: unknown): value is OptimizerHeroSearchResult {
  return isRecord(value)
    && hasExactKeys(value, ['query', 'results'])
    && typeof value.query === 'string'
    && Array.isArray(value.results)
    && value.results.length <= 50
    && value.results.every(isHeroSummary);
}

function isArtifactSummary(value: unknown): value is OptimizerArtifactSummary {
  return isRecord(value)
    && hasExactKeys(value, ['artifactId', 'name', 'role', 'rarity', 'maxLevel'])
    && isText(value.artifactId)
    && isText(value.name)
    && typeof value.role === 'string'
    && Number.isInteger(value.rarity)
    && Number(value.rarity) >= 1
    && Number(value.rarity) <= 5
    && value.maxLevel === 30;
}

export function isOptimizerArtifactSearchResult(value: unknown): value is OptimizerArtifactSearchResult {
  return isRecord(value)
    && hasExactKeys(value, ['query', 'results'])
    && typeof value.query === 'string'
    && Array.isArray(value.results)
    && value.results.length <= 50
    && value.results.every(isArtifactSummary);
}

function isStats(value: unknown): value is Record<string, number> {
  if (!isRecord(value)) return false;
  const expected = [
    'final_stat.attack', 'final_stat.health', 'final_stat.defense', 'final_stat.speed',
    'final_stat.critical_hit_chance', 'final_stat.critical_hit_damage',
    'final_stat.effectiveness', 'final_stat.effect_resistance',
  ];
  return hasExactKeys(value, expected) && Object.values(value).every((item) => isFiniteNumber(item));
}

function isProfile(value: unknown): value is OptimizerBaseProfileOption {
  return isRecord(value)
    && hasExactKeys(value, ['profileId', 'label', 'level', 'stars', 'finalStats'])
    && isText(value.profileId)
    && isText(value.label)
    && Number.isInteger(value.level)
    && isFiniteNumber(value.level, 1)
    && Number.isInteger(value.stars)
    && Number(value.stars) >= 1
    && Number(value.stars) <= 6
    && isStats(value.finalStats);
}

function isImprint(value: unknown): value is OptimizerImprintOption {
  return isRecord(value)
    && hasExactKeys(value, ['grade', 'statType', 'displayValue'])
    && isText(value.grade)
    && isText(value.statType)
    && isFiniteNumber(value.displayValue);
}

function isExclusiveEquipment(value: unknown): value is OptimizerExclusiveEquipmentOption {
  if (!isRecord(value)
    || !hasExactKeys(value, ['equipmentId', 'statType', 'rolls', 'skillOptions'])
    || !isText(value.equipmentId)
    || !isText(value.statType)
    || !Array.isArray(value.rolls)
    || value.rolls.length === 0
    || !value.rolls.every((item) => Number.isInteger(item) && isFiniteNumber(item))
    || !Array.isArray(value.skillOptions)
    || value.skillOptions.length !== 3) return false;
  return value.skillOptions.every((item) => isRecord(item)
    && hasExactKeys(item, ['optionId', 'label', 'effectDataState'])
    && isText(item.optionId)
    && isText(item.label)
    && item.effectDataState === 'unavailable-in-snapshot');
}

function isCustomField(value: unknown): value is OptimizerCustomBonusField {
  return isRecord(value)
    && hasExactKeys(value, ['key', 'label', 'percentage'])
    && OPTIMIZER_CUSTOM_BONUS_KEYS.includes(value.key as OptimizerCustomBonusKey)
    && isText(value.label)
    && typeof value.percentage === 'boolean';
}

function isSetOption(value: unknown): value is OptimizerSetOption {
  return isRecord(value)
    && hasExactKeys(value, ['setId', 'label', 'piecesRequired', 'stackable'])
    && isText(value.setId)
    && value.setId.startsWith('set.')
    && isText(value.label)
    && (value.piecesRequired === 2 || value.piecesRequired === 4)
    && typeof value.stackable === 'boolean'
    && (value.piecesRequired === 2 || value.stackable === false);
}

function isMainStatOption(value: unknown): value is OptimizerMainStatOption {
  return isRecord(value)
    && hasExactKeys(value, ['statId', 'label'])
    && isText(value.statId)
    && value.statId.startsWith('item_stat.')
    && isText(value.label);
}

function isRightSideMainStatGroup(value: unknown, index: number): value is OptimizerRightSideMainStatGroup {
  return isRecord(value)
    && hasExactKeys(value, ['slotId', 'label', 'options'])
    && value.slotId === OPTIMIZER_RIGHT_SIDE_SLOTS[index]
    && isText(value.label)
    && Array.isArray(value.options)
    && value.options.length > 0
    && value.options.every(isMainStatOption)
    && new Set(value.options.map((option) => option.statId)).size === value.options.length;
}

function isHitType(value: unknown): value is OptimizerHitType {
  return value === 'hit.critical' || value === 'hit.crushing' || value === 'hit.normal' || value === 'hit.miss';
}

function isSkillDetails(value: unknown, index: number): value is OptimizerSkillDetails {
  if (!isRecord(value)
    || !hasExactKeys(value, ['skill', 'label', 'isDamaging', 'hitTypes', 'sourceOptions', 'sourceTargetCount', 'sourcePenetrationPercent', 'note'])
    || value.skill !== OPTIMIZER_SKILL_SLOTS[index]
    || !isText(value.label)
    || typeof value.isDamaging !== 'boolean'
    || !Array.isArray(value.hitTypes)
    || !value.hitTypes.every(isHitType)
    || !Array.isArray(value.sourceOptions)
    || !isNullableNumber(value.sourceTargetCount, 1)
    || !isNullableNumber(value.sourcePenetrationPercent)
    || (value.note !== null && typeof value.note !== 'string')) return false;
  return value.sourceOptions.every((item) => isRecord(item)
    && hasExactKeys(item, ['optionId', 'label', 'isDamaging'])
    && isText(item.optionId)
    && isText(item.label)
    && typeof item.isDamaging === 'boolean');
}

export function isOptimizerHeroDetails(value: unknown): value is OptimizerHeroDetails {
  if (!isRecord(value)
    || !hasExactKeys(value, ['hero', 'defaultProfileId', 'profiles', 'imprints', 'exclusiveEquipment', 'customBonusFields', 'sets', 'rightSideMainStats', 'skills'])
    || !isRecord(value.hero)
    || !hasExactKeys(value.hero, ['heroId', 'name', 'element', 'role', 'rarity', 'portraitUrl', 'zodiac'])) return false;
  const { zodiac, ...summary } = value.hero;
  return isHeroSummary(summary)
    && isText(zodiac)
    && isText(value.defaultProfileId)
    && Array.isArray(value.profiles)
    && value.profiles.length > 0
    && value.profiles.every(isProfile)
    && value.profiles.some((profile) => profile.profileId === value.defaultProfileId)
    && Array.isArray(value.imprints)
    && value.imprints.length > 0
    && value.imprints.every(isImprint)
    && (value.exclusiveEquipment === null || isExclusiveEquipment(value.exclusiveEquipment))
    && Array.isArray(value.customBonusFields)
    && value.customBonusFields.length === OPTIMIZER_CUSTOM_BONUS_KEYS.length
    && value.customBonusFields.every(isCustomField)
    && new Set(value.customBonusFields.map((field) => field.key)).size === OPTIMIZER_CUSTOM_BONUS_KEYS.length
    && Array.isArray(value.sets)
    && value.sets.length === 24
    && value.sets.every(isSetOption)
    && new Set(value.sets.map((set) => set.setId)).size === value.sets.length
    && value.sets.some((set) => set.piecesRequired === 4)
    && value.sets.some((set) => set.piecesRequired === 2)
    && Array.isArray(value.rightSideMainStats)
    && value.rightSideMainStats.length === OPTIMIZER_RIGHT_SIDE_SLOTS.length
    && value.rightSideMainStats.every(isRightSideMainStatGroup)
    && Array.isArray(value.skills)
    && value.skills.length === 3
    && value.skills.every(isSkillDetails);
}

function isArtifactDraft(value: unknown): value is OptimizerArtifactDraft {
  if (!isRecord(value) || !hasExactKeys(value, ['artifactId', 'level', 'attackOverride', 'healthOverride', 'defenseOverride'])) return false;
  if (!isNullableText(value.artifactId)
    || !isNullableNumber(value.level)
    || !isNullableNumber(value.attackOverride)
    || !isNullableNumber(value.healthOverride)
    || !isNullableNumber(value.defenseOverride)) return false;
  return value.artifactId === null
    ? value.level === null && value.attackOverride === null && value.healthOverride === null && value.defenseOverride === null
    : Number.isInteger(value.level) && Number(value.level) <= 30;
}

function isEeDraft(value: unknown): value is OptimizerExclusiveEquipmentDraft {
  if (!isRecord(value) || !hasExactKeys(value, ['equipmentId', 'statValue', 'skillOptionId'])) return false;
  if (!isNullableText(value.equipmentId) || !isNullableNumber(value.statValue) || !isNullableText(value.skillOptionId)) return false;
  return value.equipmentId === null
    ? value.statValue === null && value.skillOptionId === null
    : Number.isInteger(value.statValue);
}

function isCustomBonuses(value: unknown): value is OptimizerCustomBonuses {
  return isRecord(value)
    && hasExactKeys(value, OPTIMIZER_CUSTOM_BONUS_KEYS)
    && OPTIMIZER_CUSTOM_BONUS_KEYS.every((key) => isNullableNumber(value[key]));
}

function isPrimaryStats(value: unknown): value is OptimizerPrimaryStatsDraft {
  const keys = OPTIMIZER_PRIMARY_STATS.map((stat) => stat.key);
  if (!isRecord(value) || !hasExactKeys(value, keys)) return false;
  return keys.every((key) => {
    const stat = value[key];
    if (!isRecord(stat) || !hasExactKeys(stat, ['minimum', 'maximum', 'priority'])) return false;
    if (!isNullableNumber(stat.minimum)
      || !isNullableNumber(stat.maximum)
      || !Number.isInteger(stat.priority)
      || Number(stat.priority) < -1
      || Number(stat.priority) > 3) return false;
    return stat.minimum === null || stat.maximum === null || stat.minimum <= stat.maximum;
  });
}

function isSetPatternDraft(value: unknown): value is OptimizerSetPatternDraft {
  if (!isRecord(value)
    || !hasExactKeys(value, ['kind', 'sets'])
    || (value.kind !== '4+2' && value.kind !== '2+2+2' && value.kind !== 'flexible')
    || !Array.isArray(value.sets)
    || !value.sets.every(isNullableText)) return false;
  return value.sets.length === (value.kind === '4+2' ? 2 : 3);
}

function isGearFiltersDraft(value: unknown): value is OptimizerGearFiltersDraft {
  if (!isRecord(value)
    || !hasExactKeys(value, ['minimumEnhance', 'rightSideMainStats'])
    || value.minimumEnhance !== 15
    || !isRecord(value.rightSideMainStats)
    || !hasExactKeys(value.rightSideMainStats, OPTIMIZER_RIGHT_SIDE_SLOTS)) return false;
  const rightSideMainStats = value.rightSideMainStats;
  return OPTIMIZER_RIGHT_SIDE_SLOTS.every((slotId) => {
    const selected = rightSideMainStats[slotId];
    return Array.isArray(selected)
      && selected.every(isText)
      && new Set(selected).size === selected.length;
  });
}

function isSkillDraft(value: unknown, index: number): value is OptimizerSkillDraft {
  return isRecord(value)
    && hasExactKeys(value, ['skill', 'sourceOptionId', 'hitType', 'targetCountOverride', 'penetrationPercent', 'targetDefense'])
    && value.skill === OPTIMIZER_SKILL_SLOTS[index]
    && isNullableText(value.sourceOptionId)
    && (value.hitType === null || isHitType(value.hitType))
    && isNullableNumber(value.targetCountOverride, 1)
    && (value.targetCountOverride === null || Number.isInteger(value.targetCountOverride))
    && isNullableNumber(value.penetrationPercent)
    && (value.penetrationPercent === null || value.penetrationPercent <= 100)
    && isFiniteNumber(value.targetDefense);
}

export function isOptimizerHeroDraft(value: unknown): value is OptimizerHeroDraft {
  return isRecord(value)
    && hasExactKeys(value, [
      'heroId', 'baseProfileId', 'artifact', 'imprintGrade',
      'exclusiveEquipment', 'customBonuses', 'primaryStats',
      'setPattern', 'includeEquipped', 'maximumReplacementDistance',
      'nearSetTolerancePercent', 'itemProjectionMode', 'gearFilters', 'skills',
    ])
    && isText(value.heroId)
    && isText(value.baseProfileId)
    && isArtifactDraft(value.artifact)
    && isNullableText(value.imprintGrade)
    && isEeDraft(value.exclusiveEquipment)
    && isCustomBonuses(value.customBonuses)
    && isPrimaryStats(value.primaryStats)
    && isSetPatternDraft(value.setPattern)
    && typeof value.includeEquipped === 'boolean'
    && value.maximumReplacementDistance === 0
    && value.nearSetTolerancePercent === 0
    && (value.itemProjectionMode === 'projection.current' || value.itemProjectionMode === 'projection.reforged')
    && isGearFiltersDraft(value.gearFilters)
    && Array.isArray(value.skills)
    && value.skills.length === 3
    && value.skills.every(isSkillDraft);
}

export function isOptimizerHeroDraftEnvelope(value: unknown): value is OptimizerHeroDraftEnvelope {
  return isRecord(value)
    && hasExactKeys(value, ['state', 'savedAt', 'schemaVersion', 'draft', 'selectedArtifact'])
    && (value.state === 'default' || value.state === 'saved')
    && (value.savedAt === null || isTimestamp(value.savedAt))
    && (value.state === 'default' ? value.savedAt === null : value.savedAt !== null)
    && value.schemaVersion === 7
    && isOptimizerHeroDraft(value.draft)
    && (value.selectedArtifact === null || isArtifactSummary(value.selectedArtifact))
    && (value.draft.artifact.artifactId === null
      ? value.selectedArtifact === null
      : value.selectedArtifact?.artifactId === value.draft.artifact.artifactId);
}

export function validateOptimizerHeroDraft(
  draft: OptimizerHeroDraft,
  details?: OptimizerHeroDetails | null,
): OptimizerDraftValidationIssue[] {
  const issues: OptimizerDraftValidationIssue[] = [];
  if (!draft.heroId) issues.push({ path: 'draft.heroId', message: 'Choose a hero.' });
  if (!draft.baseProfileId) issues.push({ path: 'draft.baseProfileId', message: 'Choose a base profile.' });
  if (draft.artifact.artifactId !== null && (!Number.isInteger(draft.artifact.level) || Number(draft.artifact.level) < 0 || Number(draft.artifact.level) > 30)) {
    issues.push({ path: 'draft.artifact.level', message: 'Artifact level must be an integer from 0 through 30.' });
  }
  OPTIMIZER_PRIMARY_STATS.forEach((definition) => {
    const stat = draft.primaryStats[definition.key];
    const label = definition.label;
    if (stat.minimum !== null && (!Number.isFinite(stat.minimum) || stat.minimum < 0)) {
      issues.push({ path: `draft.primaryStats.${definition.key}.minimum`, message: `${label} minimum must be zero or greater.` });
    }
    if (stat.maximum !== null && (!Number.isFinite(stat.maximum) || stat.maximum < 0)) {
      issues.push({ path: `draft.primaryStats.${definition.key}.maximum`, message: `${label} maximum must be zero or greater.` });
    }
    if (!Number.isInteger(stat.priority) || stat.priority < -1 || stat.priority > 3) {
      issues.push({ path: `draft.primaryStats.${definition.key}.priority`, message: `${label} priority must be an integer from -1 through 3.` });
    }
    if (stat.minimum !== null && stat.maximum !== null
      && Number.isFinite(stat.minimum) && Number.isFinite(stat.maximum)
      && stat.minimum > stat.maximum) {
      issues.push({ path: `draft.primaryStats.${definition.key}.maximum`, message: `${label} maximum must be greater than or equal to its minimum.` });
    }
  });
  const expectedSetPieces = draft.setPattern.kind === '4+2'
    ? [4, 2]
    : draft.setPattern.kind === '2+2+2'
      ? [2, 2, 2]
      : [null, null, null];
  if (draft.setPattern.sets.length !== expectedSetPieces.length) {
    issues.push({
      path: 'draft.setPattern.sets',
      message: `The ${draft.setPattern.kind} layout requires exactly ${expectedSetPieces.length} set selections.`,
    });
  }
  draft.setPattern.sets.forEach((setId, index) => {
    const path = `draft.setPattern.sets[${index}]`;
    if (setId === null && draft.setPattern.kind !== 'flexible') {
      issues.push({ path, message: 'Choose a gear set.' });
      return;
    }
    if (setId === null) return;
    const option = details?.sets.find((candidate) => candidate.setId === setId);
    if (details && !option) {
      issues.push({ path, message: 'Choose a known gear set.' });
      return;
    }
    const expectedPieces = expectedSetPieces[index];
    if (option && expectedPieces !== null && expectedPieces !== undefined && option.piecesRequired !== expectedPieces) {
      issues.push({ path, message: `This selector requires a ${expectedPieces}-piece set.` });
    }
    if (draft.setPattern.sets.indexOf(setId) !== index && option && !option.stackable) {
      issues.push({ path, message: `${option.label} cannot be selected more than once.` });
    }
  });
  if (details && draft.setPattern.kind === 'flexible') {
    const requiredPieces = draft.setPattern.sets.reduce((total, setId) => {
      const option = setId === null ? undefined : details.sets.find((candidate) => candidate.setId === setId);
      return total + (option?.piecesRequired ?? 0);
    }, 0);
    if (requiredPieces > 6) {
      issues.push({
        path: 'draft.setPattern.sets',
        message: 'Selected sets require more than the six gear pieces a hero can equip.',
      });
    }
  }
  if (typeof draft.includeEquipped !== 'boolean') {
    issues.push({ path: 'draft.includeEquipped', message: 'Include equipped must be on or off.' });
  }
  if (draft.maximumReplacementDistance !== 0) {
    issues.push({
      path: 'draft.maximumReplacementDistance',
      message: 'The optimizer currently supports exact completed sets only.',
    });
  }
  if (draft.nearSetTolerancePercent !== 0) {
    issues.push({
      path: 'draft.nearSetTolerancePercent',
      message: 'Near-build tolerance is retired and must remain zero.',
    });
  }
  if (draft.itemProjectionMode !== 'projection.current'
    && draft.itemProjectionMode !== 'projection.reforged') {
    issues.push({ path: 'draft.itemProjectionMode', message: 'Choose current or reforged item stats.' });
  }
  if (draft.gearFilters.minimumEnhance !== 15) {
    issues.push({
      path: 'draft.gearFilters.minimumEnhance',
      message: 'The optimizer only accepts +15 equipment.',
    });
  }
  OPTIMIZER_RIGHT_SIDE_SLOTS.forEach((slotId) => {
    const selected = draft.gearFilters.rightSideMainStats[slotId];
    const group = details?.rightSideMainStats.find((candidate) => candidate.slotId === slotId);
    selected.forEach((statId, index) => {
      const path = `draft.gearFilters.rightSideMainStats.${slotId}[${index}]`;
      if (selected.indexOf(statId) !== index) {
        issues.push({ path, message: 'Choose each main stat only once.' });
      } else if (details && !group?.options.some((option) => option.statId === statId)) {
        issues.push({ path, message: `This main stat is not legal for ${group?.label ?? slotId}.` });
      }
    });
  });
  draft.skills.forEach((skill, index) => {
    if (!Number.isFinite(skill.targetDefense) || skill.targetDefense < 0) {
      issues.push({ path: `draft.skills[${index}].targetDefense`, message: `${skill.skill.toUpperCase()} target Defense must be zero or greater.` });
    }
    if (skill.targetCountOverride !== null && (!Number.isInteger(skill.targetCountOverride) || skill.targetCountOverride < 1)) {
      issues.push({ path: `draft.skills[${index}].targetCountOverride`, message: 'Target count must be a positive integer.' });
    }
    if (skill.penetrationPercent !== null && (skill.penetrationPercent < 0 || skill.penetrationPercent > 100)) {
      issues.push({ path: `draft.skills[${index}].penetrationPercent`, message: 'Penetration must be from 0% through 100%.' });
    }
  });
  return issues;
}
