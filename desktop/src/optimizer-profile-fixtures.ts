import {
  OPTIMIZER_CUSTOM_BONUS_KEYS,
  OPTIMIZER_PRIMARY_STATS,
  type OptimizerHeroDetails,
  type OptimizerHeroDraftEnvelope,
  type OptimizerHeroSearchResult,
  type OptimizerArtifactSearchResult,
  type OptimizerSetOption,
} from './shared/optimizer-profile';

export const HERO_SEARCH: OptimizerHeroSearchResult = {
  query: 'Achates',
  results: [{
    heroId: 'hero.fribbels.achates',
    name: 'Achates',
    element: 'fire',
    role: 'soul-weaver',
    rarity: 4,
    portraitUrl: 'https://raw.githubusercontent.com/fribbels/Fribbels-Epic-7-Optimizer/main/data/cachedimages/c1017_l.png',
  }],
};

export const ARTIFACT_SEARCH: OptimizerArtifactSearchResult = {
  query: 'Rod',
  results: [{
    artifactId: 'artifact.fribbels.rod-of-amaryllis',
    name: 'Rod of Amaryllis',
    role: 'soul-weaver',
    rarity: 5,
    maxLevel: 30,
  }],
};

const finalStats = {
  'final_stat.attack': 1176,
  'final_stat.health': 6034,
  'final_stat.defense': 743,
  'final_stat.speed': 101,
  'final_stat.critical_hit_chance': 0.15,
  'final_stat.critical_hit_damage': 1.5,
  'final_stat.effectiveness': 0,
  'final_stat.effect_resistance': 0,
};

const customBonusFields = OPTIMIZER_CUSTOM_BONUS_KEYS.map((key) => ({
  key,
  label: key,
  percentage: key.toLocaleLowerCase().includes('percent'),
}));

const sets: OptimizerSetOption[] = [
  ['set.attack', 'Attack Set', 4, false],
  ['set.counter', 'Counter Set', 4, false],
  ['set.critical', 'Critical Set', 2, true],
  ['set.defense', 'Defense Set', 2, true],
  ['set.destruction', 'Destruction Set', 4, false],
  ['set.fervor', 'Fervor Set', 2, false],
  ['set.health', 'Health Set', 2, true],
  ['set.hit', 'Hit Set', 2, true],
  ['set.immunity', 'Immunity Set', 2, false],
  ['set.injury', 'Injury Set', 4, false],
  ['set.lifesteal', 'Lifesteal Set', 4, false],
  ['set.penetration', 'Penetration Set', 2, false],
  ['set.protection', 'Protection Set', 4, false],
  ['set.pursuit', 'Pursuit Set', 2, false],
  ['set.rage', 'Rage Set', 4, false],
  ['set.resist', 'Resist Set', 2, true],
  ['set.revenge', 'Revenge Set', 4, false],
  ['set.reversal', 'Reversal Set', 4, false],
  ['set.riposte', 'Riposte Set', 4, false],
  ['set.speed', 'Speed Set', 4, false],
  ['set.torrent', 'Torrent Set', 2, true],
  ['set.unity', 'Unity Set', 2, true],
  ['set.warfare', 'Warfare Set', 4, false],
  ['set.weakening', 'Weakening Set', 4, false],
].map(([setId, label, piecesRequired, stackable]) => ({
  setId: String(setId),
  label: String(label),
  piecesRequired: piecesRequired as 2 | 4,
  stackable: Boolean(stackable),
}));

const baseMainStats = [
  { statId: 'item_stat.flat_attack', label: 'Attack' },
  { statId: 'item_stat.attack_percent', label: 'Attack %' },
  { statId: 'item_stat.flat_health', label: 'Health' },
  { statId: 'item_stat.health_percent', label: 'Health %' },
  { statId: 'item_stat.flat_defense', label: 'Defense' },
  { statId: 'item_stat.defense_percent', label: 'Defense %' },
];

export const HERO_DETAILS: OptimizerHeroDetails = {
  hero: { ...HERO_SEARCH.results[0], zodiac: 'cancer' },
  defaultProfileId: 'profile.achates.60',
  profiles: [
    { profileId: 'profile.achates.50', label: 'Level 50 / 5 star', level: 50, stars: 5, finalStats },
    { profileId: 'profile.achates.60', label: 'Level 60 / 6 star / fully awakened', level: 60, stars: 6, finalStats },
  ],
  imprints: [
    { grade: 'D', statType: 'hero_modifier.health_percent', displayValue: 3.6 },
    { grade: 'SSS', statType: 'hero_modifier.health_percent', displayValue: 12.9 },
  ],
  exclusiveEquipment: {
    equipmentId: 'exclusive-equipment.fribbels.achates.0.proof',
    statType: 'hero_modifier.health_percent',
    rolls: [7, 8, 9, 10, 11, 12, 13, 14],
    skillOptions: [1, 2, 3].map((ordinal) => ({
      optionId: `exclusive-equipment.fribbels.achates.0.proof.skill-option.${ordinal}`,
      label: `Skill slot ${ordinal}`,
      effectDataState: 'unavailable-in-snapshot' as const,
    })),
  },
  customBonusFields,
  sets,
  rightSideMainStats: [
    {
      slotId: 'slot.necklace', label: 'Necklace',
      options: [...baseMainStats,
        { statId: 'item_stat.critical_hit_chance_percent', label: 'Critical Hit Chance %' },
        { statId: 'item_stat.critical_hit_damage_percent', label: 'Critical Hit Damage %' }],
    },
    {
      slotId: 'slot.ring', label: 'Ring',
      options: [...baseMainStats,
        { statId: 'item_stat.effectiveness_percent', label: 'Effectiveness %' },
        { statId: 'item_stat.effect_resistance_percent', label: 'Effect Resistance %' }],
    },
    {
      slotId: 'slot.boots', label: 'Boots',
      options: [...baseMainStats, { statId: 'item_stat.speed', label: 'Speed' }],
    },
  ],
  skills: [
    {
      skill: 'skill.s1', label: 'S1', isDamaging: true, hitTypes: ['hit.critical', 'hit.normal'],
      sourceOptions: [], sourceTargetCount: 1, sourcePenetrationPercent: 0, note: null,
    },
    {
      skill: 'skill.s2', label: 'S2', isDamaging: false, hitTypes: [],
      sourceOptions: [{ optionId: 'skill-option.fribbels.achates.s2.0.proof', label: 'S2 heal', isDamaging: false }],
      sourceTargetCount: null, sourcePenetrationPercent: null, note: 'Heal context',
    },
    {
      skill: 'skill.s3', label: 'S3', isDamaging: false, hitTypes: [],
      sourceOptions: [], sourceTargetCount: null, sourcePenetrationPercent: null, note: null,
    },
  ],
};

const customBonuses = Object.fromEntries(OPTIMIZER_CUSTOM_BONUS_KEYS.map((key) => [key, null])) as OptimizerHeroDraftEnvelope['draft']['customBonuses'];
const primaryStats = Object.fromEntries(OPTIMIZER_PRIMARY_STATS.map(({ key }) => [
  key,
  { minimum: null, maximum: null, priority: 0 },
])) as OptimizerHeroDraftEnvelope['draft']['primaryStats'];
export const HERO_DRAFT: OptimizerHeroDraftEnvelope = {
  state: 'saved',
  savedAt: '2026-07-22T18:30:00.000Z',
  schemaVersion: 7,
  selectedArtifact: ARTIFACT_SEARCH.results[0],
  draft: {
    heroId: HERO_DETAILS.hero.heroId,
    baseProfileId: HERO_DETAILS.defaultProfileId,
    artifact: {
      artifactId: ARTIFACT_SEARCH.results[0].artifactId,
      level: 30,
      attackOverride: null,
      healthOverride: null,
      defenseOverride: null,
    },
    imprintGrade: 'SSS',
    exclusiveEquipment: {
      equipmentId: HERO_DETAILS.exclusiveEquipment?.equipmentId ?? null,
      statValue: 14,
      skillOptionId: HERO_DETAILS.exclusiveEquipment?.skillOptions[0].optionId ?? null,
    },
    customBonuses,
    primaryStats,
    setPattern: { kind: '4+2', sets: ['set.speed', 'set.health'] },
    includeEquipped: false,
    maximumReplacementDistance: 0,
    nearSetTolerancePercent: 0,
    itemProjectionMode: 'projection.current',
    gearFilters: {
      minimumEnhance: 15,
      rightSideMainStats: {
        'slot.necklace': [],
        'slot.ring': [],
        'slot.boots': [],
      },
    },
    skills: [
      { skill: 'skill.s1', sourceOptionId: null, hitType: 'hit.critical', targetCountOverride: 1, penetrationPercent: 20, targetDefense: 1000 },
      { skill: 'skill.s2', sourceOptionId: 'skill-option.fribbels.achates.s2.0.proof', hitType: null, targetCountOverride: null, penetrationPercent: null, targetDefense: 1200 },
      { skill: 'skill.s3', sourceOptionId: null, hitType: null, targetCountOverride: null, penetrationPercent: null, targetDefense: 1400 },
    ],
  },
};
