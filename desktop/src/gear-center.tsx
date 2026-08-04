import { useEffect, useMemo, useState } from 'react';

import { FRIBBELS_SET_ICONS } from './fribbels-set-icons';
import {
  EquippedHeroFace,
  GEAR_SLOT_ICONS,
} from './optimizer-result-detail';
import {
  OPTIMIZER_GEAR_SLOTS,
  type OptimizerInventoryGearItem,
  type OptimizerInventorySnapshot,
} from './shared/optimizer-inventory';
import { Badge, Button, Card, Dialog, EmptyState } from './ui';

type EquippedFilter = 'all' | 'equipped' | 'unequipped';
type SortDirection = 'ascending' | 'descending';

interface GearFilters {
  setIds: string[];
  rankIds: string[];
  mainStatIds: string[];
  substatIds: string[];
  lockState: 'all' | 'locked' | 'unlocked';
  level: 'all' | '85' | '90';
  minimumRgs: string;
  minimumCgs: string;
  minimumSgs: string;
}

const EMPTY_FILTERS: GearFilters = {
  setIds: [],
  rankIds: [],
  mainStatIds: [],
  substatIds: [],
  lockState: 'all',
  level: 'all',
  minimumRgs: '',
  minimumCgs: '',
  minimumSgs: '',
};

const STAT_ABBREVIATIONS: Readonly<Record<string, string>> = {
  'item_stat.flat_attack': 'ATK',
  'item_stat.attack_percent': 'ATK',
  'item_stat.flat_health': 'HP',
  'item_stat.health_percent': 'HP',
  'item_stat.flat_defense': 'DEF',
  'item_stat.defense_percent': 'DEF',
  'item_stat.speed': 'SPD',
  'item_stat.critical_hit_chance_percent': 'CR',
  'item_stat.critical_hit_damage_percent': 'CD',
  'item_stat.effectiveness_percent': 'EFF',
  'item_stat.effect_resistance_percent': 'RES',
};

function toggle(values: string[], value: string): string[] {
  return values.includes(value)
    ? values.filter((candidate) => candidate !== value)
    : [...values, value];
}

function formatStat(stat: { statId: string; value: number }): string {
  const suffix = stat.statId.endsWith('_percent') ? '%' : '';
  return `${STAT_ABBREVIATIONS[stat.statId] ?? 'STAT'} ${stat.value.toLocaleString(undefined, {
    maximumFractionDigits: 2,
  })}${suffix}`;
}

function numericMinimum(value: string): number | null {
  const parsed = Number(value);
  return value.trim() && Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

function optionList(
  items: readonly OptimizerInventoryGearItem[],
  id: (item: OptimizerInventoryGearItem) => string,
  label: (item: OptimizerInventoryGearItem) => string,
): Array<{ id: string; label: string }> {
  return [...new Map(items.map((item) => [id(item), label(item)])).entries()]
    .map(([optionId, optionLabel]) => ({ id: optionId, label: optionLabel }))
    .sort((left, right) => left.label.localeCompare(right.label));
}

function statOptions(
  items: readonly OptimizerInventoryGearItem[],
  kind: 'main' | 'substat',
): Array<{ id: string; label: string }> {
  const values = items.flatMap((item) => (
    kind === 'main' ? [item.mainStat] : item.substats
  ));
  return [...new Map(values.map((stat) => [stat.statId, stat.label])).entries()]
    .map(([id, label]) => ({ id, label }))
    .sort((left, right) => left.label.localeCompare(right.label));
}

function GearFilterDialog({
  filters,
  gear,
  onChange,
  onClose,
  open,
}: {
  filters: GearFilters;
  gear: readonly OptimizerInventoryGearItem[];
  onChange(filters: GearFilters): void;
  onClose(): void;
  open: boolean;
}): React.JSX.Element {
  const sets = optionList(gear, (item) => item.setId, (item) => item.setLabel);
  const ranks = optionList(gear, (item) => item.rankId, (item) => item.rankLabel);
  const mainStats = statOptions(gear, 'main');
  const substats = statOptions(gear, 'substat');
  const checkboxGroup = (
    name: keyof Pick<GearFilters, 'setIds' | 'rankIds' | 'mainStatIds' | 'substatIds'>,
    options: Array<{ id: string; label: string }>,
  ): React.JSX.Element => (
    <div className="gear-filter-checks">
      {options.map((option) => (
        <label key={option.id}>
          <input
            checked={filters[name].includes(option.id)}
            onChange={() => onChange({ ...filters, [name]: toggle(filters[name], option.id) })}
            type="checkbox"
          />
          <span>{option.label}</span>
        </label>
      ))}
    </div>
  );

  return (
    <Dialog
      description="Every filter is applied to imported +15 equipment."
      footer={(
        <>
          <Button onClick={() => onChange(EMPTY_FILTERS)} type="button" variant="secondary">
            Clear filters
          </Button>
          <Button onClick={onClose} type="button">Done</Button>
        </>
      )}
      onClose={onClose}
      open={open}
      title="Filter gear"
    >
      <div className="gear-filter-dialog">
        <fieldset>
          <legend>Sets</legend>
          {checkboxGroup('setIds', sets)}
        </fieldset>
        <fieldset>
          <legend>Rarity</legend>
          {checkboxGroup('rankIds', ranks)}
        </fieldset>
        <fieldset>
          <legend>Main stat</legend>
          {checkboxGroup('mainStatIds', mainStats)}
        </fieldset>
        <fieldset>
          <legend>Required substats</legend>
          <small>Selected substats must all be present.</small>
          {checkboxGroup('substatIds', substats)}
        </fieldset>
        <div className="gear-filter-selects optimizer-result-sortbar">
          <label>
            <span>Equipment level</span>
            <select
              onChange={(event) => onChange({
                ...filters,
                level: event.currentTarget.value as GearFilters['level'],
              })}
              value={filters.level}
            >
              <option value="all">Any level</option>
              <option value="85">Level 85</option>
              <option value="90">Level 90</option>
            </select>
          </label>
          <label>
            <span>Lock state</span>
            <select
              onChange={(event) => onChange({
                ...filters,
                lockState: event.currentTarget.value as GearFilters['lockState'],
              })}
              value={filters.lockState}
            >
              <option value="all">Locked or unlocked</option>
              <option value="locked">Locked only</option>
              <option value="unlocked">Unlocked only</option>
            </select>
          </label>
        </div>
        <div className="gear-filter-scores optimizer-result-range">
          {([
            ['minimumRgs', 'Minimum RGS'],
            ['minimumCgs', 'Minimum CGS'],
            ['minimumSgs', 'Minimum SGS'],
          ] as const).map(([field, label]) => (
            <label key={field}>
              <span>{label}</span>
              <input
                min="0"
                onChange={(event) => onChange({ ...filters, [field]: event.currentTarget.value })}
                placeholder="Any"
                type="number"
                value={filters[field]}
              />
            </label>
          ))}
        </div>
      </div>
    </Dialog>
  );
}

export function GearCenter({
  inventory,
  onOpenImporter,
}: {
  inventory: OptimizerInventorySnapshot;
  onOpenImporter(): void;
}): React.JSX.Element {
  const gear = inventory.gear;
  const [search, setSearch] = useState('');
  const [equipped, setEquipped] = useState<EquippedFilter>('all');
  const [slotIds, setSlotIds] = useState<string[]>([]);
  const [filters, setFilters] = useState<GearFilters>(EMPTY_FILTERS);
  const [filterOpen, setFilterOpen] = useState(false);
  const [sortBy, setSortBy] = useState('reforgedGearScore');
  const [direction, setDirection] = useState<SortDirection>('descending');
  const [rowsPerPage, setRowsPerPage] = useState(100);
  const [page, setPage] = useState(1);
  const [selectedKey, setSelectedKey] = useState<string | null>(gear[0]?.gearKey ?? null);

  const substatSortOptions = useMemo(() => statOptions(gear, 'substat'), [gear]);
  const filtered = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    const minimumRgs = numericMinimum(filters.minimumRgs);
    const minimumCgs = numericMinimum(filters.minimumCgs);
    const minimumSgs = numericMinimum(filters.minimumSgs);
    return gear.filter((item) => {
      const isEquipped = item.equippedStatus !== 'unequipped';
      const haystack = [
        item.slotLabel,
        item.setLabel,
        item.rankLabel,
        item.mainStat.label,
        item.equippedHeroName ?? '',
        ...item.substats.map((stat) => stat.label),
      ].join(' ').toLocaleLowerCase();
      return (!query || haystack.includes(query))
        && (equipped === 'all' || (equipped === 'equipped') === isEquipped)
        && (slotIds.length === 0 || slotIds.includes(item.slotId))
        && (filters.setIds.length === 0 || filters.setIds.includes(item.setId))
        && (filters.rankIds.length === 0 || filters.rankIds.includes(item.rankId))
        && (filters.mainStatIds.length === 0 || filters.mainStatIds.includes(item.mainStat.statId))
        && filters.substatIds.every((statId) => item.substats.some((stat) => stat.statId === statId))
        && (filters.lockState === 'all' || (filters.lockState === 'locked') === item.locked)
        && (filters.level === 'all' || item.itemLevel === Number(filters.level))
        && (minimumRgs === null || item.reforgedGearScore >= minimumRgs)
        && (minimumCgs === null || item.combatGearScore >= minimumCgs)
        && (minimumSgs === null || item.supportGearScore >= minimumSgs);
    });
  }, [equipped, filters, gear, search, slotIds]);

  const sorted = useMemo(() => [...filtered].sort((left, right) => {
    let comparison: number;
    if (sortBy.startsWith('stat:')) {
      const statId = sortBy.slice(5);
      const statValue = (item: OptimizerInventoryGearItem): number => (
        item.substats.find((stat) => stat.statId === statId)?.value ?? -1
      );
      comparison = statValue(left) - statValue(right);
    } else if (sortBy === 'setLabel') {
      comparison = left.setLabel.localeCompare(right.setLabel);
    } else if (sortBy === 'equippedHeroName') {
      comparison = (left.equippedHeroName ?? '').localeCompare(right.equippedHeroName ?? '');
    } else {
      comparison = Number(left[sortBy as keyof OptimizerInventoryGearItem])
        - Number(right[sortBy as keyof OptimizerInventoryGearItem]);
    }
    if (comparison === 0) comparison = left.gearKey.localeCompare(right.gearKey);
    return direction === 'ascending' ? comparison : -comparison;
  }), [direction, filtered, sortBy]);

  const pageCount = Math.max(1, Math.ceil(sorted.length / rowsPerPage));
  const visible = sorted.slice((page - 1) * rowsPerPage, page * rowsPerPage);
  const selected = sorted.find((item) => item.gearKey === selectedKey) ?? sorted[0] ?? null;
  const equippedCount = gear.filter((item) => item.equippedStatus !== 'unequipped').length;
  const lockedCount = gear.filter((item) => item.locked).length;
  const advancedFilterCount = (
    filters.setIds.length
    + filters.rankIds.length
    + filters.mainStatIds.length
    + filters.substatIds.length
    + (filters.lockState === 'all' ? 0 : 1)
    + (filters.level === 'all' ? 0 : 1)
    + (filters.minimumRgs ? 1 : 0)
    + (filters.minimumCgs ? 1 : 0)
    + (filters.minimumSgs ? 1 : 0)
  );
  const selectedMatches = selected?.archetypeAnalysis.matches ?? [];
  const viableMatches = selectedMatches.filter((match) => match.status !== 'rejected');
  const visibleMatches = viableMatches.length ? viableMatches : selectedMatches;

  useEffect(() => {
    setPage(1);
  }, [equipped, filters, rowsPerPage, search, slotIds]);
  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);
  useEffect(() => {
    if (selected && selected.gearKey !== selectedKey) setSelectedKey(selected.gearKey);
  }, [selected, selectedKey]);

  if (inventory.state === 'empty') {
    return (
      <EmptyState
        action={<Button onClick={onOpenImporter}>Open Importer</Button>}
        description="Import gear.txt or capture your game inventory before browsing equipment."
        icon="gear"
        title="No imported gear"
      />
    );
  }
  if (gear.length === 0) {
    return (
      <EmptyState
        action={<Button onClick={onOpenImporter}>Update inventory</Button>}
        description="The current inventory does not contain any +15 equipment."
        icon="gear"
        title="No +15 gear found"
      />
    );
  }

  return (
    <div className="page-stack gear-page">
      <Card className="gear-header" elevated>
        <div>
          <span className="card-kicker">OWNED EQUIPMENT</span>
          <h2>Gear</h2>
          <p>Browse and compare imported +15 equipment using reforged score projections.</p>
        </div>
        <Button onClick={onOpenImporter} type="button" variant="secondary">Open Importer</Button>
      </Card>

      <div className="gear-summary">
        <Card><strong>{gear.length.toLocaleString()}</strong><span>+15 gear</span></Card>
        <Card><strong>{equippedCount.toLocaleString()}</strong><span>Equipped</span></Card>
        <Card><strong>{(gear.length - equippedCount).toLocaleString()}</strong><span>Unequipped</span></Card>
        <Card><strong>{lockedCount.toLocaleString()}</strong><span>Locked</span></Card>
      </div>

      <Card className="gear-browser">
        <div className="gear-toolbar">
          <label className="gear-search">
            <span className="sr-only">Search gear</span>
            <input
              onChange={(event) => setSearch(event.currentTarget.value)}
              placeholder="Search set, stat, or equipped hero…"
              type="search"
              value={search}
            />
          </label>
          <div aria-label="Equipped status" className="gear-segmented">
            {(['all', 'equipped', 'unequipped'] as const).map((value) => (
              <button
                aria-pressed={equipped === value}
                key={value}
                onClick={() => setEquipped(value)}
                type="button"
              >
                {value[0].toUpperCase()}{value.slice(1)}
              </button>
            ))}
          </div>
          <div aria-label="Gear slots" className="gear-slot-filters">
            {OPTIMIZER_GEAR_SLOTS.map((slot) => (
              <button
                aria-label={slot.label}
                aria-pressed={slotIds.includes(slot.slot)}
                key={slot.slot}
                onClick={() => setSlotIds(toggle(slotIds, slot.slot))}
                title={slot.label}
                type="button"
              >
                <img alt="" src={GEAR_SLOT_ICONS[slot.slot]} />
              </button>
            ))}
          </div>
          <Button onClick={() => setFilterOpen(true)} size="small" type="button" variant="secondary">
            Filters{advancedFilterCount ? ` (${advancedFilterCount})` : ''}
          </Button>
        </div>

        <div className="gear-workspace">
          <section aria-label="Imported gear table" className="gear-table-panel">
            <div className="gear-table-scroll">
              <table className="gear-table">
                <colgroup>
                  <col className="gear-piece-column" />
                  <col className="gear-set-column" />
                  <col className="gear-main-column" />
                  <col className="gear-substats-column" />
                  <col className="gear-score-column" />
                  <col className="gear-score-column" />
                  <col className="gear-score-column" />
                  <col className="gear-owner-column" />
                </colgroup>
                <thead>
                  <tr>
                    <th>Piece</th>
                    <th>Set</th>
                    <th>Main</th>
                    <th>Substats</th>
                    <th title="Reforged Gear Score">RGS</th>
                    <th title="Combat Gear Score">CGS</th>
                    <th title="Support Gear Score">SGS</th>
                    <th>Equipped</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((item) => (
                    <tr
                      aria-selected={selected?.gearKey === item.gearKey}
                      key={item.gearKey}
                      onClick={() => setSelectedKey(item.gearKey)}
                    >
                      <td>
                        <button
                          aria-label={`Select ${item.slotLabel} +15`}
                          className="gear-piece-button"
                          onClick={() => setSelectedKey(item.gearKey)}
                          type="button"
                        >
                          <img alt="" src={GEAR_SLOT_ICONS[item.slotId]} />
                          <span><strong>+15</strong><small>Lv {item.itemLevel} · {item.rankLabel}</small></span>
                        </button>
                      </td>
                      <td>
                        {FRIBBELS_SET_ICONS[item.setId] ? (
                          <img
                            alt={`${item.setLabel} set`}
                            className="gear-table-set-icon"
                            src={FRIBBELS_SET_ICONS[item.setId]}
                            title={`${item.setLabel} set`}
                          />
                        ) : item.setLabel}
                      </td>
                      <td><strong>{formatStat(item.mainStat)}</strong></td>
                      <td>
                        <div className="gear-substat-cell">
                          {item.substats.map((stat) => (
                            <span key={stat.statId}>{formatStat(stat)}</span>
                          ))}
                        </div>
                      </td>
                      <td><strong>{item.reforgedGearScore}</strong></td>
                      <td>{item.combatGearScore}</td>
                      <td>{item.supportGearScore}</td>
                      <td>
                        {item.equippedStatus === 'unequipped' ? (
                          <span className="gear-owner-empty">Unequipped</span>
                        ) : (
                          <span className="gear-owner">
                            <EquippedHeroFace heroName={item.equippedHeroName} />
                            <span>{item.equippedHeroName ?? 'Equipped'}</span>
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                  {visible.length === 0 && (
                    <tr><td className="gear-no-results" colSpan={8}>No +15 gear matches these filters.</td></tr>
                  )}
                </tbody>
              </table>
            </div>

            <footer className="gear-table-footer">
              <div className="optimizer-result-sortbar">
                <label>
                  <span>Sort by</span>
                  <select onChange={(event) => setSortBy(event.currentTarget.value)} value={sortBy}>
                    <option value="reforgedGearScore">Reforged GS</option>
                    <option value="combatGearScore">Combat GS</option>
                    <option value="supportGearScore">Support GS</option>
                    <option value="itemLevel">Item level</option>
                    <option value="setLabel">Set</option>
                    <option value="equippedHeroName">Equipped hero</option>
                    {substatSortOptions.map((stat) => (
                      <option key={stat.id} value={`stat:${stat.id}`}>{stat.label} substat</option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>Direction</span>
                  <select
                    onChange={(event) => setDirection(event.currentTarget.value as SortDirection)}
                    value={direction}
                  >
                    <option value="descending">Highest first</option>
                    <option value="ascending">Lowest first</option>
                  </select>
                </label>
                <label>
                  <span>Rows</span>
                  <select
                    onChange={(event) => setRowsPerPage(Number(event.currentTarget.value))}
                    value={rowsPerPage}
                  >
                    <option value="50">50</option>
                    <option value="100">100</option>
                    <option value="200">200</option>
                  </select>
                </label>
              </div>
              <div className="gear-pagination">
                <span>
                  {sorted.length === 0 ? '0' : `${(page - 1) * rowsPerPage + 1}–${Math.min(page * rowsPerPage, sorted.length)}`}
                  {' '}of {sorted.length.toLocaleString()}
                </span>
                <Button disabled={page === 1} onClick={() => setPage(page - 1)} size="small" type="button" variant="secondary">Previous</Button>
                <span>Page {page} of {pageCount}</span>
                <Button disabled={page === pageCount} onClick={() => setPage(page + 1)} size="small" type="button" variant="secondary">Next</Button>
              </div>
            </footer>
          </section>

          <aside className="gear-detail" aria-label="Selected gear">
            {selected ? (
              <>
                <div className="gear-detail-heading">
                  <div>
                    <span className="card-kicker">ARCHETYPE ANALYSIS</span>
                    <h3>{selected.slotLabel} recommendation</h3>
                  </div>
                  <Badge tone={
                    selected.archetypeAnalysis.verdict === 'keep'
                      ? 'success'
                      : selected.archetypeAnalysis.verdict === 'destroy' ? 'danger' : 'warning'
                  }>
                    {selected.archetypeAnalysis.verdict === 'keep'
                      ? 'Keep'
                      : selected.archetypeAnalysis.verdict === 'destroy' ? 'Destroy' : 'Review'}
                  </Badge>
                </div>
                <div className={`gear-archetype-verdict gear-archetype-verdict-${selected.archetypeAnalysis.verdict}`}>
                  <strong>{selected.archetypeAnalysis.reason}</strong>
                  {selected.archetypeAnalysis.verdict === 'review' && (
                    <span>Reimport or recapture inventory to restore exact roll evidence.</span>
                  )}
                </div>
                {visibleMatches.length ? (
                  <div className="gear-archetype-list">
                    {visibleMatches.map((match) => (
                      <article className="gear-archetype-match" key={match.id}>
                        <header>
                          <h4>{match.name}</h4>
                          <Badge tone={match.status === 'eligible' ? 'success' : match.status === 'rejected' ? 'danger' : 'warning'}>
                            {match.status === 'eligible' ? 'Fits' : match.status === 'rejected' ? 'Rolled out' : 'Unknown rolls'}
                          </Badge>
                        </header>
                        <p className="gear-archetype-fit">
                          {match.matchingSubstats.length}/4 desired substats
                          {match.offStats.length === 0 && ' · No off-stat'}
                        </p>
                        <div className="gear-archetype-stats" aria-label="Desired stats">
                          {match.preferredStats.map((stat) => <span key={stat}>{stat}</span>)}
                        </div>
                        {match.offStats.map((stat) => (
                          <p className="gear-archetype-offstat" key={stat.statId}>
                            Off-stat: <strong>{stat.label}</strong> · {stat.rolls === null ? 'rolls unknown' : `${stat.rolls} total roll${stat.rolls === 1 ? '' : 's'}`}
                          </p>
                        ))}
                        <p className="gear-archetype-heroes">
                          <strong>Heroes</strong>
                          <span>{match.heroes.join(', ')}</span>
                        </p>
                      </article>
                    ))}
                  </div>
                ) : (
                  <p className="gear-detail-note">This piece has no compatible archetype or heroes.</p>
                )}
              </>
            ) : (
              <p>Select a gear row to inspect it.</p>
            )}
          </aside>
        </div>
      </Card>

      <GearFilterDialog
        filters={filters}
        gear={gear}
        onChange={setFilters}
        onClose={() => setFilterOpen(false)}
        open={filterOpen}
      />
    </div>
  );
}
