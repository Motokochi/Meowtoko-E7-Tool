import type {
  OptimizerDraftValidationIssue,
  OptimizerHeroDetails,
  OptimizerHeroDraft,
  OptimizerRightSideSlotId,
} from './shared/optimizer-profile';
import { Badge, Card } from './ui';

interface OptimizerSetInventoryControlsProps {
  details: OptimizerHeroDetails;
  draft: OptimizerHeroDraft;
  issues: OptimizerDraftValidationIssue[];
  onChange(draft: OptimizerHeroDraft): void;
}

function issueFor(issues: OptimizerDraftValidationIssue[], path: string): string | undefined {
  return issues.find((issue) => issue.path === path)?.message;
}

function issueUnder(issues: OptimizerDraftValidationIssue[], path: string): string | undefined {
  return issues.find((issue) => issue.path === path || issue.path.startsWith(`${path}[`))?.message;
}

export function OptimizerSetInventoryControls({
  details,
  draft,
  issues,
  onChange,
}: OptimizerSetInventoryControlsProps): React.JSX.Element {
  const setSelections = Array.from(
    { length: 3 },
    (_, index) => draft.setPattern.sets[index] ?? null,
  );
  const selectedPieceCount = setSelections.reduce((total, setId) => {
    const option = setId === null
      ? undefined
      : details.sets.find((set) => set.setId === setId);
    return total + (option?.piecesRequired ?? 0);
  }, 0);

  const update = (next: OptimizerHeroDraft): void => {
    onChange({
      ...next,
      gearFilters: { ...next.gearFilters, minimumEnhance: 15 },
    });
  };

  const changeSet = (index: number, setId: string): void => {
    const sets = [...setSelections];
    sets[index] = setId || null;
    update({
      ...draft,
      setPattern: { kind: 'flexible', sets },
    });
  };

  const toggleMainStat = (
    slotId: OptimizerRightSideSlotId,
    statId: string,
    checked: boolean,
  ): void => {
    const group = details.rightSideMainStats.find((item) => item.slotId === slotId);
    const current = draft.gearFilters.rightSideMainStats[slotId];
    const selected = checked
      ? [...new Set([...current, statId])]
      : current.filter((candidate) => candidate !== statId);
    const canonical = group?.options
      .filter((option) => selected.includes(option.statId))
      .map((option) => option.statId) ?? selected;
    update({
      ...draft,
      gearFilters: {
        ...draft.gearFilters,
        rightSideMainStats: {
          ...draft.gearFilters.rightSideMainStats,
          [slotId]: canonical,
        },
      },
    });
  };

  const patternIssue = issueFor(issues, 'draft.setPattern.sets');

  return (
    <Card className="optimizer-compact-options">
      <section aria-labelledby="optimizer-set-pattern-heading" className="optimizer-compact-set-section">
        <div className="optimizer-compact-section-heading">
          <div>
            <span className="card-kicker">SET REQUIREMENTS</span>
            <h3 id="optimizer-set-pattern-heading">Choose up to three sets</h3>
          </div>
          <Badge tone={selectedPieceCount === 6 ? 'success' : 'neutral'}>
            {selectedPieceCount === 0 ? 'Any sets' : `${selectedPieceCount}/6 required`}
          </Badge>
        </div>

        <div className="optimizer-set-selectors optimizer-set-selectors-compact">
          {setSelections.map((setId, index) => {
            const path = `draft.setPattern.sets[${index}]`;
            const issue = issueFor(issues, path);
            const errorId = `optimizer-set-${index}-error`;
            return (
              <div className="field" key={`optional-set-${index}`}>
                <label htmlFor={`optimizer-set-${index}`}>Set {index + 1}</label>
                <select
                  aria-describedby={issue ? errorId : undefined}
                  aria-invalid={issue ? true : undefined}
                  id={`optimizer-set-${index}`}
                  onChange={(event) => changeSet(index, event.currentTarget.value)}
                  value={setId ?? ''}
                >
                  <option value="">None (any set)</option>
                  {details.sets.map((option) => {
                    const selectedElsewhere = setSelections.some(
                      (candidate, candidateIndex) => (
                        candidateIndex !== index && candidate === option.setId
                      ),
                    );
                    return (
                      <option
                        disabled={selectedElsewhere && !option.stackable}
                        key={option.setId}
                        value={option.setId}
                      >
                        {option.label} · {option.piecesRequired}pc
                        {option.stackable ? ' · stackable' : ''}
                      </option>
                    );
                  })}
                </select>
                {issue && <span className="field-error-message" id={errorId}>{issue}</span>}
              </div>
            );
          })}
        </div>
        {patternIssue && <p className="field-error-message">{patternIssue}</p>}
      </section>

      <div className="optimizer-compact-checkbox-row">
        <label className="optimizer-compact-checkbox" htmlFor="optimizer-include-equipped">
          <input
            checked={draft.includeEquipped}
            id="optimizer-include-equipped"
            onChange={(event) => update({
              ...draft,
              includeEquipped: event.currentTarget.checked,
            })}
            type="checkbox"
          />
          <span>
            <strong>Include equipped</strong>
            <small>Also consider gear worn by other heroes</small>
          </span>
        </label>

        <label className="optimizer-compact-checkbox" htmlFor="optimizer-use-reforged">
          <input
            checked={draft.itemProjectionMode === 'projection.reforged'}
            id="optimizer-use-reforged"
            onChange={(event) => update({
              ...draft,
              itemProjectionMode: event.currentTarget.checked
                ? 'projection.reforged'
                : 'projection.current',
            })}
            type="checkbox"
          />
          <span>
            <strong>Use reforged stats</strong>
            <small>Project supported items to reforged values</small>
          </span>
        </label>
      </div>

      <details className="optimizer-advanced-options">
        <summary>
          <span>
            <strong>Advanced gear filters</strong>
            <small>Optional right-side main stats</small>
          </span>
          <Badge>+15 only</Badge>
        </summary>

        <div className="optimizer-advanced-options-body">
          <section aria-labelledby="optimizer-main-stat-heading">
            <div className="optimizer-inline-heading">
              <h4 id="optimizer-main-stat-heading">Right-side main stats</h4>
              <span>Leave a slot empty to allow every legal main stat.</span>
            </div>
            <div className="optimizer-main-stat-grid optimizer-main-stat-grid-compact">
              {details.rightSideMainStats.map((group) => {
                const path = `draft.gearFilters.rightSideMainStats.${group.slotId}`;
                const issue = issueUnder(issues, path);
                const errorId = `optimizer-${group.slotId.replace('.', '-')}-main-stat-error`;
                return (
                  <fieldset
                    aria-describedby={issue ? errorId : undefined}
                    className="optimizer-main-stat-group"
                    key={group.slotId}
                  >
                    <legend>{group.label}</legend>
                    <div className="optimizer-main-stat-options">
                      {group.options.map((option) => (
                        <label key={option.statId}>
                          <input
                            checked={draft.gearFilters.rightSideMainStats[group.slotId]
                              .includes(option.statId)}
                            onChange={(event) => toggleMainStat(
                              group.slotId,
                              option.statId,
                              event.currentTarget.checked,
                            )}
                            type="checkbox"
                          />
                          <span>{option.label}</span>
                        </label>
                      ))}
                    </div>
                    {issue && <p className="field-error-message" id={errorId}>{issue}</p>}
                  </fieldset>
                );
              })}
            </div>
          </section>
        </div>
      </details>
    </Card>
  );
}
