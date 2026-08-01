import { useEffect, useState } from 'react';

import { OptimizerPrimaryStats } from './optimizer-primary-stats';
import { OptimizerSetInventoryControls } from './optimizer-set-inventory-controls';
import type { OptimizerProfileWorkspaceState } from './optimizer-profile-workspace';
import { characterArtworkUrl } from './shared/character-artwork';
import type {
  OptimizerArtifactSummary,
  OptimizerHeroDetails,
  OptimizerHeroDraft,
  OptimizerHeroSummary,
  OptimizerSkillDraft,
} from './shared/optimizer-profile';
import { Alert, Badge, Button, Card, Dialog } from './ui';

interface OptimizerProfileEditorProps {
  enabled: boolean;
  profile: OptimizerProfileWorkspaceState;
  onArtifactSearch(query: string): void;
  onChooseArtifact(artifact: OptimizerArtifactSummary | null): void;
  onDraftChange(draft: OptimizerHeroDraft): void;
  onHeroSearch(query: string): void;
  onSaveDraft(): void;
  onSelectHero(heroId: string): void;
}

function issueFor(profile: OptimizerProfileWorkspaceState, path: string): string | undefined {
  return profile.issues.find((issue) => issue.path === path)?.message;
}

function displayNumber(value: number | null): number | '' {
  return value !== null && Number.isFinite(value) ? value : '';
}

function inputNumber(value: string): number | null {
  return value.trim() === '' ? null : Number(value);
}

function statLabel(value: string): string {
  return value
    .replace('hero_modifier.', '')
    .replace('final_stat.', '')
    .replaceAll('_', ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function hitLabel(value: string): string {
  return value.replace('hit.', '').replace(/^./, (letter) => letter.toUpperCase());
}

interface SearchBoxProps<T> {
  activeId: string | null;
  disabled: boolean;
  getId(item: T): string;
  getLabel(item: T): string;
  inputId: string;
  label: string;
  loading: boolean;
  onChoose(item: T): void;
  onSearch(query: string): void;
  query: string;
  results: T[];
}

function SearchBox<T>({
  activeId,
  disabled,
  getId,
  getLabel,
  inputId,
  label,
  loading,
  onChoose,
  onSearch,
  query,
  results,
}: SearchBoxProps<T>): React.JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const listId = `${inputId}-results`;
  const statusId = `${inputId}-status`;

  useEffect(() => {
    setActiveIndex((current) => Math.min(current, Math.max(0, results.length - 1)));
  }, [results.length]);

  const choose = (item: T): void => {
    onChoose(item);
    setExpanded(false);
  };

  return (
    <div className="optimizer-combobox">
      <label htmlFor={inputId}>{label}</label>
      <input
        aria-activedescendant={expanded && results[activeIndex] ? `${inputId}-option-${activeIndex}` : undefined}
        aria-autocomplete="list"
        aria-busy={loading || undefined}
        aria-controls={listId}
        aria-describedby={statusId}
        aria-expanded={expanded && !disabled}
        autoComplete="off"
        disabled={disabled}
        id={inputId}
        onBlur={() => setExpanded(false)}
        onChange={(event) => {
          setExpanded(true);
          setActiveIndex(0);
          onSearch(event.currentTarget.value);
        }}
        onFocus={() => setExpanded(true)}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown') {
            event.preventDefault();
            setExpanded(true);
            setActiveIndex((current) => expanded
              ? Math.min(current + 1, Math.max(0, results.length - 1))
              : 0);
          } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            setExpanded(true);
            setActiveIndex((current) => expanded
              ? Math.max(0, current - 1)
              : Math.max(0, results.length - 1));
          } else if (event.key === 'Home' && expanded) {
            event.preventDefault();
            setActiveIndex(0);
          } else if (event.key === 'End' && expanded) {
            event.preventDefault();
            setActiveIndex(Math.max(0, results.length - 1));
          } else if (event.key === 'Enter' && expanded && results[activeIndex]) {
            event.preventDefault();
            choose(results[activeIndex]);
          } else if (event.key === 'Escape') {
            setExpanded(false);
          }
        }}
        placeholder={`Search ${label.toLocaleLowerCase()}`}
        role="combobox"
        type="search"
        value={query}
      />
      <span aria-live="polite" className="optimizer-search-state" id={statusId}>
        {loading ? 'Searching…' : `${results.length} bounded results`}
      </span>
      {expanded && !disabled && (
        <ul aria-label={`${label} results`} className="optimizer-combobox-results" id={listId} role="listbox">
          {results.map((item, index) => {
            const id = getId(item);
            return (
              <li
                aria-selected={id === activeId}
                className={index === activeIndex ? 'optimizer-combobox-option-active' : ''}
                id={`${inputId}-option-${index}`}
                key={id}
                onClick={() => choose(item)}
                onMouseDown={(event) => event.preventDefault()}
                onMouseEnter={() => setActiveIndex(index)}
                role="option"
              >
                <strong>{getLabel(item)}</strong>
              </li>
            );
          })}
          {results.length === 0 && (
            <li className="optimizer-combobox-empty" role="presentation">
              No matching source-backed choices
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

interface BonusConfigurationProps {
  busy: boolean;
  details: OptimizerHeroDetails;
  draft: OptimizerHeroDraft;
  profile: OptimizerProfileWorkspaceState;
  onArtifactSearch(query: string): void;
  onChooseArtifact(artifact: OptimizerArtifactSummary | null): void;
  onUpdate(draft: OptimizerHeroDraft): void;
}

export function OptimizerBonusConfiguration({
  busy,
  details,
  draft,
  profile,
  onArtifactSearch,
  onChooseArtifact,
  onUpdate,
}: BonusConfigurationProps): React.JSX.Element {
  const updateSkill = (index: number, next: OptimizerSkillDraft): void => {
    const skills = draft.skills.map((skill, skillIndex) => skillIndex === index ? next : skill);
    onUpdate({ ...draft, skills });
  };

  return (
    <fieldset className="optimizer-bonus-form" disabled={busy}>
      <legend className="sr-only">Bonus stats and damage configuration</legend>

      <section className="optimizer-dialog-section">
        <div className="optimizer-dialog-section-heading">
          <div><span className="card-kicker">ARTIFACT</span><h3>Artifact contribution</h3></div>
          <Button onClick={() => onChooseArtifact(null)} size="small" type="button" variant="ghost">
            No artifact
          </Button>
        </div>
        <SearchBox<OptimizerArtifactSummary>
          activeId={draft.artifact.artifactId}
          disabled={busy}
          getId={(artifact) => artifact.artifactId}
          getLabel={(artifact) => `${artifact.name} · ${artifact.rarity}★ ${artifact.role || 'Any role'}`}
          inputId="optimizer-artifact-search"
          label="Artifact"
          loading={profile.artifactSearching}
          onChoose={onChooseArtifact}
          onSearch={onArtifactSearch}
          query={profile.artifactQuery}
          results={profile.artifactResults}
        />
        {draft.artifact.artifactId === null ? (
          <p className="optimizer-unavailable">No artifact contribution is applied.</p>
        ) : (
          <div className="optimizer-control-grid optimizer-artifact-controls">
            <div className="field">
              <label htmlFor="optimizer-artifact-level">Level</label>
              <input
                aria-describedby={issueFor(profile, 'draft.artifact.level') ? 'optimizer-artifact-level-error' : undefined}
                aria-invalid={Boolean(issueFor(profile, 'draft.artifact.level'))}
                id="optimizer-artifact-level"
                max={30}
                min={0}
                onChange={(event) => onUpdate({
                  ...draft,
                  artifact: { ...draft.artifact, level: inputNumber(event.currentTarget.value) },
                })}
                step={1}
                type="number"
                value={displayNumber(draft.artifact.level)}
              />
              {issueFor(profile, 'draft.artifact.level') && (
                <span className="field-error-message" id="optimizer-artifact-level-error">
                  {issueFor(profile, 'draft.artifact.level')}
                </span>
              )}
            </div>
            {(['attackOverride', 'healthOverride', 'defenseOverride'] as const).map((key) => (
              <div className="field" key={key}>
                <label htmlFor={`optimizer-artifact-${key}`}>{key.replace('Override', '')} override</label>
                <input
                  id={`optimizer-artifact-${key}`}
                  min={0}
                  onChange={(event) => onUpdate({
                    ...draft,
                    artifact: { ...draft.artifact, [key]: inputNumber(event.currentTarget.value) },
                  })}
                  placeholder="Calculated"
                  type="number"
                  value={displayNumber(draft.artifact[key])}
                />
              </div>
            ))}
          </div>
        )}
        <p className="field-description">Limit-break effects are unavailable in the pinned snapshot and are never invented.</p>
      </section>

      <div className="optimizer-modifier-grid">
        <section className="optimizer-dialog-section">
          <div className="optimizer-dialog-section-heading">
            <div><span className="card-kicker">SELF IMPRINT</span><h3>Concentration</h3></div>
          </div>
          <div className="field">
            <label htmlFor="optimizer-imprint">Imprint grade</label>
            <select
              id="optimizer-imprint"
              onChange={(event) => onUpdate({ ...draft, imprintGrade: event.currentTarget.value || null })}
              value={draft.imprintGrade ?? ''}
            >
              <option value="">No self imprint</option>
              {details.imprints.map((option) => (
                <option key={option.grade} value={option.grade}>
                  {option.grade} · {statLabel(option.statType)} +{option.displayValue}
                </option>
              ))}
            </select>
            <span className="field-description">Only self concentration applies; team imprint is not applied.</span>
          </div>
        </section>

        <section className="optimizer-dialog-section">
          <div className="optimizer-dialog-section-heading">
            <div><span className="card-kicker">EXCLUSIVE EQUIPMENT</span><h3>Stat and skill slot</h3></div>
          </div>
          {details.exclusiveEquipment === null ? (
            <p className="optimizer-unavailable">No exclusive equipment exists for this hero.</p>
          ) : (
            <div className="optimizer-control-grid optimizer-ee-grid">
              <div className="field">
                <label htmlFor="optimizer-ee">Exclusive equipment</label>
                <select
                  id="optimizer-ee"
                  onChange={(event) => onUpdate({
                    ...draft,
                    exclusiveEquipment: event.currentTarget.value
                      ? {
                        equipmentId: event.currentTarget.value,
                        statValue: details.exclusiveEquipment?.rolls[0] ?? null,
                        skillOptionId: null,
                      }
                      : { equipmentId: null, statValue: null, skillOptionId: null },
                  })}
                  value={draft.exclusiveEquipment.equipmentId ?? ''}
                >
                  <option value="">No exclusive equipment</option>
                  <option value={details.exclusiveEquipment.equipmentId}>
                    {statLabel(details.exclusiveEquipment.statType)}
                  </option>
                </select>
              </div>
              {draft.exclusiveEquipment.equipmentId && (
                <>
                  <div className="field">
                    <label htmlFor="optimizer-ee-roll">Stat roll</label>
                    <select
                      id="optimizer-ee-roll"
                      onChange={(event) => onUpdate({
                        ...draft,
                        exclusiveEquipment: {
                          ...draft.exclusiveEquipment,
                          statValue: Number(event.currentTarget.value),
                        },
                      })}
                      value={draft.exclusiveEquipment.statValue ?? ''}
                    >
                      {details.exclusiveEquipment.rolls.map((roll) => (
                        <option key={roll} value={roll}>{roll}</option>
                      ))}
                    </select>
                  </div>
                  <div className="field">
                    <label htmlFor="optimizer-ee-skill">Independent EE skill slot</label>
                    <select
                      id="optimizer-ee-skill"
                      onChange={(event) => onUpdate({
                        ...draft,
                        exclusiveEquipment: {
                          ...draft.exclusiveEquipment,
                          skillOptionId: event.currentTarget.value || null,
                        },
                      })}
                      value={draft.exclusiveEquipment.skillOptionId ?? ''}
                    >
                      <option value="">No EE skill slot</option>
                      {details.exclusiveEquipment.skillOptions.map((option) => (
                        <option key={option.optionId} value={option.optionId}>
                          {option.label} · effect unavailable
                        </option>
                      ))}
                    </select>
                  </div>
                </>
              )}
            </div>
          )}
        </section>
      </div>

      <section className="optimizer-dialog-section">
        <div className="optimizer-dialog-section-heading">
          <div><span className="card-kicker">CUSTOM BONUSES</span><h3>Flat and percentage contributions</h3></div>
          <Badge>{details.customBonusFields.length} supported</Badge>
        </div>
        <div className="optimizer-custom-grid">
          {details.customBonusFields.map((field) => (
            <div className="field" key={field.key}>
              <label htmlFor={`optimizer-custom-${field.key}`}>{field.label}</label>
              <input
                id={`optimizer-custom-${field.key}`}
                min={0}
                onChange={(event) => onUpdate({
                  ...draft,
                  customBonuses: {
                    ...draft.customBonuses,
                    [field.key]: inputNumber(event.currentTarget.value),
                  },
                })}
                placeholder="Not applied"
                step={field.percentage ? 0.1 : 1}
                type="number"
                value={displayNumber(draft.customBonuses[field.key])}
              />
            </div>
          ))}
        </div>
      </section>

      <section className="optimizer-dialog-section">
        <div className="optimizer-dialog-section-heading">
          <div><span className="card-kicker">DAMAGE CONTEXT</span><h3>Independent S1 / S2 / S3 settings</h3></div>
        </div>
        <div className="optimizer-skill-list">
          {details.skills.map((skill, index) => {
            const context = draft.skills[index];
            const selectedOption = skill.sourceOptions.find(
              (option) => option.optionId === context.sourceOptionId,
            );
            const damaging = skill.isDamaging && (selectedOption?.isDamaging ?? true);
            const defenseIssue = issueFor(profile, `draft.skills[${index}].targetDefense`);
            const targetCountIssue = issueFor(profile, `draft.skills[${index}].targetCountOverride`);
            const penetrationIssue = issueFor(profile, `draft.skills[${index}].penetrationPercent`);
            const skillSlug = skill.label.toLocaleLowerCase().replace(/[^a-z0-9]+/g, '-');
            return (
              <details key={skill.skill} open={index === 0}>
                <summary>
                  <span>{skill.label}</span>
                  <Badge tone={damaging ? 'accent' : 'neutral'}>
                    {damaging ? 'Damage context' : 'Non-damaging'}
                  </Badge>
                </summary>
                <div className="optimizer-skill-controls">
                  <div className="field">
                    <label htmlFor={`optimizer-${skill.label}-option`}>Source option</label>
                    <select
                      id={`optimizer-${skill.label}-option`}
                      onChange={(event) => {
                        const sourceOptionId = event.currentTarget.value || null;
                        const option = skill.sourceOptions.find((item) => item.optionId === sourceOptionId);
                        const applies = skill.isDamaging && (option?.isDamaging ?? true);
                        updateSkill(index, {
                          ...context,
                          sourceOptionId,
                          hitType: applies ? context.hitType : null,
                          targetCountOverride: applies ? context.targetCountOverride : null,
                          penetrationPercent: applies ? context.penetrationPercent : null,
                        });
                      }}
                      value={context.sourceOptionId ?? ''}
                    >
                      <option value="">Base source skill</option>
                      {skill.sourceOptions.map((option) => (
                        <option key={option.optionId} value={option.optionId}>
                          {option.label}{option.isDamaging ? '' : ' · non-damaging'}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="field">
                    <label htmlFor={`optimizer-${skill.label}-hit`}>Hit type</label>
                    <select
                      disabled={!damaging || skill.hitTypes.length === 0}
                      id={`optimizer-${skill.label}-hit`}
                      onChange={(event) => updateSkill(index, {
                        ...context,
                        hitType: event.currentTarget.value as OptimizerSkillDraft['hitType'] || null,
                      })}
                      value={context.hitType ?? ''}
                    >
                      <option value="">No hit override</option>
                      {skill.hitTypes.map((hit) => <option key={hit} value={hit}>{hitLabel(hit)}</option>)}
                    </select>
                  </div>
                  <div className="field">
                    <label htmlFor={`optimizer-${skill.label}-targets`}>Target count</label>
                    <input
                      aria-describedby={targetCountIssue ? `optimizer-${skillSlug}-targets-error` : undefined}
                      aria-invalid={Boolean(targetCountIssue)}
                      disabled={!damaging}
                      id={`optimizer-${skill.label}-targets`}
                      min={1}
                      onChange={(event) => updateSkill(index, {
                        ...context,
                        targetCountOverride: inputNumber(event.currentTarget.value),
                      })}
                      placeholder={skill.sourceTargetCount?.toString() ?? 'Source'}
                      step={1}
                      type="number"
                      value={displayNumber(context.targetCountOverride)}
                    />
                    {targetCountIssue && (
                      <span className="field-error-message" id={`optimizer-${skillSlug}-targets-error`}>
                        {targetCountIssue}
                      </span>
                    )}
                  </div>
                  <div className="field">
                    <label htmlFor={`optimizer-${skill.label}-penetration`}>Penetration %</label>
                    <input
                      aria-describedby={penetrationIssue ? `optimizer-${skillSlug}-penetration-error` : undefined}
                      aria-invalid={Boolean(penetrationIssue)}
                      disabled={!damaging}
                      id={`optimizer-${skill.label}-penetration`}
                      max={100}
                      min={0}
                      onChange={(event) => updateSkill(index, {
                        ...context,
                        penetrationPercent: inputNumber(event.currentTarget.value),
                      })}
                      placeholder={skill.sourcePenetrationPercent?.toString() ?? 'Source'}
                      step={0.1}
                      type="number"
                      value={displayNumber(context.penetrationPercent)}
                    />
                    {penetrationIssue && (
                      <span className="field-error-message" id={`optimizer-${skillSlug}-penetration-error`}>
                        {penetrationIssue}
                      </span>
                    )}
                  </div>
                  <div className="field">
                    <label htmlFor={`optimizer-${skill.label}-defense`}>Target Defense</label>
                    <input
                      aria-describedby={defenseIssue ? `optimizer-${skillSlug}-defense-error` : undefined}
                      aria-invalid={Boolean(defenseIssue)}
                      id={`optimizer-${skill.label}-defense`}
                      min={0}
                      onChange={(event) => updateSkill(index, {
                        ...context,
                        targetDefense: Number(event.currentTarget.value),
                      })}
                      type="number"
                      value={Number.isFinite(context.targetDefense) ? context.targetDefense : ''}
                    />
                    {defenseIssue && (
                      <span className="field-error-message" id={`optimizer-${skillSlug}-defense-error`}>
                        {defenseIssue}
                      </span>
                    )}
                  </div>
                </div>
                {!damaging && (
                  <p className="optimizer-unavailable">
                    Hit type, target count and penetration do not apply to this source choice.
                  </p>
                )}
              </details>
            );
          })}
        </div>
      </section>
    </fieldset>
  );
}

export function OptimizerProfileEditor({
  enabled,
  profile,
  onArtifactSearch,
  onChooseArtifact,
  onDraftChange,
  onHeroSearch,
  onSaveDraft,
  onSelectHero,
}: OptimizerProfileEditorProps): React.JSX.Element {
  const [bonusOpen, setBonusOpen] = useState(false);
  const [artworkFailed, setArtworkFailed] = useState(false);
  const draft = profile.envelope?.draft ?? null;
  const details = profile.details;
  const busy = profile.loading || profile.saving;
  const update = (next: OptimizerHeroDraft): void => onDraftChange({
    ...next,
    gearFilters: { ...next.gearFilters, minimumEnhance: 15 },
  });

  useEffect(() => {
    setArtworkFailed(false);
  }, [details?.hero.heroId]);

  return (
    <section aria-labelledby="optimizer-profile-title" className="optimizer-profile-panel optimizer-profile-compact">
      {!enabled && (
        <Alert title="Import owned gear first" tone="info">
          Choose a Fribbels gear.txt file before configuring a build.
        </Alert>
      )}
      {profile.notice && (
        <Alert title={profile.notice.title} tone={profile.notice.tone}>
          {profile.notice.message}
        </Alert>
      )}
      {profile.issues.length > 0 && (
        <Alert title="Check the highlighted fields" tone="danger">
          <ul className="optimizer-validation-summary">
            {profile.issues.map((issue) => <li key={issue.path}>{issue.message}</li>)}
          </ul>
        </Alert>
      )}

      <div className="optimizer-compact-workspace">
        <Card className="optimizer-character-pane" elevated>
          <div className="optimizer-character-search">
            <SearchBox<OptimizerHeroSummary>
              activeId={draft?.heroId ?? null}
              disabled={!enabled || busy}
              getId={(hero) => hero.heroId}
              getLabel={(hero) => `${hero.name} · ${hero.element} ${hero.role}`}
              inputId="optimizer-hero-search"
              label="Character"
              loading={profile.heroSearching}
              onChoose={(hero) => onSelectHero(hero.heroId)}
              onSearch={onHeroSearch}
              query={profile.heroQuery}
              results={profile.heroResults}
            />
          </div>

          {profile.loading && (
            <p aria-atomic="true" aria-live="polite" className="optimizer-profile-loading" role="status">
              Loading character profile…
            </p>
          )}

          {!draft || !details ? (
            <div className="optimizer-character-empty">
              <span className="optimizer-character-empty-mark">E7</span>
              <strong>Select a character</strong>
              <p>The character artwork and build controls will appear here.</p>
            </div>
          ) : (
            <>
              <div className="optimizer-character-art">
                {!artworkFailed ? (
                  <img
                    alt={`${details.hero.name} character artwork`}
                    onError={() => setArtworkFailed(true)}
                    src={characterArtworkUrl(details.hero.name)}
                  />
                ) : (
                  <div className="optimizer-character-art-unavailable" role="status">
                    <strong>Artwork unavailable</strong>
                    <span>This character is not published in the bundled E7 Codex library.</span>
                  </div>
                )}
                <div className="optimizer-character-identity">
                  <div>
                    <span>{details.hero.rarity}★ · {details.hero.element}</span>
                    <h2>{details.hero.name}</h2>
                    <p>{details.hero.role} · {details.hero.zodiac}</p>
                  </div>
                  <Badge tone={profile.envelope?.state === 'saved' ? 'success' : 'accent'}>
                    {profile.envelope?.state === 'saved' ? 'Saved' : 'New'}
                  </Badge>
                </div>
              </div>
              <div className="optimizer-base-profile-compact field">
                <label htmlFor="optimizer-base-profile">Base profile</label>
                <select
                  id="optimizer-base-profile"
                  onChange={(event) => update({ ...draft, baseProfileId: event.currentTarget.value })}
                  value={draft.baseProfileId}
                >
                  {details.profiles.map((option) => (
                    <option key={option.profileId} value={option.profileId}>{option.label}</option>
                  ))}
                </select>
              </div>
            </>
          )}
        </Card>

        {draft && details ? (
          <fieldset className="optimizer-requirements-pane" disabled={!enabled || busy}>
            <legend className="sr-only">{details.hero.name} optimizer configuration</legend>
            <div className="optimizer-requirements-heading">
              <div>
                <span className="card-kicker">BUILD CONFIGURATION</span>
                <h2 id="optimizer-profile-title">Target stats</h2>
              </div>
              <div className="optimizer-requirements-actions">
                <Button
                  aria-expanded={bonusOpen}
                  aria-haspopup="dialog"
                  onClick={() => setBonusOpen(true)}
                  size="small"
                  type="button"
                  variant="secondary"
                >
                  Add bonus stats
                </Button>
                <Button
                  busy={profile.saving}
                  disabled={!profile.dirty || profile.issues.length > 0}
                  onClick={onSaveDraft}
                  size="small"
                  type="button"
                >
                  Save
                </Button>
              </div>
            </div>

            <OptimizerPrimaryStats
              issues={profile.issues}
              onChange={(primaryStats) => update({ ...draft, primaryStats })}
              value={draft.primaryStats}
            />

            <OptimizerSetInventoryControls
              details={details}
              draft={draft}
              issues={profile.issues}
              onChange={update}
            />

            <div className="optimizer-compact-save-state" aria-live="polite">
              {profile.saving
                ? 'Saving…'
                : profile.dirty
                  ? 'Unsaved changes'
                  : 'Profile saved'}
            </div>
          </fieldset>
        ) : (
          <Card className="optimizer-requirements-empty">
            <div>
              <span className="card-kicker">BUILD CONFIGURATION</span>
              <h2 id="optimizer-profile-title">Target stats</h2>
              <p>Select a character to configure minimums, maximums, priorities and sets.</p>
            </div>
          </Card>
        )}
      </div>

      {draft && details && (
        <Dialog
          description="Artifact, imprint, exclusive equipment, custom contributions and skill damage context."
          footer={(
            <>
              <span className="optimizer-dialog-save-state">
                {profile.dirty ? 'Changes are not saved yet.' : 'Profile saved.'}
              </span>
              <Button onClick={() => setBonusOpen(false)} type="button" variant="secondary">Done</Button>
              <Button
                busy={profile.saving}
                disabled={!profile.dirty || profile.issues.length > 0}
                onClick={onSaveDraft}
                type="button"
              >
                Save profile
              </Button>
            </>
          )}
          onClose={() => setBonusOpen(false)}
          open={bonusOpen}
          title={`Bonus stats · ${details.hero.name}`}
        >
          <OptimizerBonusConfiguration
            busy={busy}
            details={details}
            draft={draft}
            onArtifactSearch={onArtifactSearch}
            onChooseArtifact={onChooseArtifact}
            onUpdate={update}
            profile={profile}
          />
        </Dialog>
      )}
    </section>
  );
}
