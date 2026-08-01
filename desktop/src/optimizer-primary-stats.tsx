import type { OptimizerDraftValidationIssue, OptimizerPrimaryStatsDraft } from './shared/optimizer-profile';
import { OPTIMIZER_PRIMARY_STATS } from './shared/optimizer-profile';
import { Badge, Card } from './ui';

interface OptimizerPrimaryStatsProps {
  issues: OptimizerDraftValidationIssue[];
  onChange(value: OptimizerPrimaryStatsDraft): void;
  value: OptimizerPrimaryStatsDraft;
}

const PRIORITY_LABELS = {
  [-1]: 'Penalize more',
  0: 'Neutral',
  1: 'Favor',
  2: 'Strongly favor',
  3: 'Highest priority',
} as const;

function displayNumber(value: number | null): number | '' {
  return value !== null && Number.isFinite(value) ? value : '';
}

function inputNumber(value: string): number | null {
  return value.trim() === '' ? null : Number(value);
}

export function OptimizerPrimaryStats({
  issues,
  onChange,
  value,
}: OptimizerPrimaryStatsProps): React.JSX.Element {
  const issueFor = (path: string): string | undefined => issues.find((issue) => issue.path === path)?.message;

  return (
    <Card className="optimizer-config-card optimizer-primary-stat-card">
      <div className="optimizer-config-heading">
        <div>
          <span className="card-kicker">FINAL BUILD TARGETS</span>
          <h3>Primary stats and priorities</h3>
        </div>
        <Badge>Blank = any</Badge>
      </div>
      <p className="sr-only" id="optimizer-primary-stat-help">
        Leave either bound blank when it does not matter. Zero is a real bound.
        Percentage stats use percentage points.
      </p>
      <div aria-describedby="optimizer-primary-stat-help" className="optimizer-primary-stat-grid">
        <div aria-hidden="true" className="optimizer-primary-stat-columns">
          <span>Stat</span><span>Minimum</span><span>Maximum</span><span>Priority</span>
        </div>
        {OPTIMIZER_PRIMARY_STATS.map((definition) => {
          const stat = value[definition.key];
          const basePath = `draft.primaryStats.${definition.key}`;
          const minimumIssue = issueFor(`${basePath}.minimum`);
          const maximumIssue = issueFor(`${basePath}.maximum`);
          const priorityIssue = issueFor(`${basePath}.priority`);
          const slug = definition.key.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`);
          const unit = definition.percentage ? ' (%)' : '';
          const minimumId = `optimizer-primary-${slug}-minimum`;
          const maximumId = `optimizer-primary-${slug}-maximum`;
          const priorityId = `optimizer-primary-${slug}-priority`;
          return (
            <section aria-labelledby={`optimizer-primary-${slug}-title`} className="optimizer-primary-stat-row" key={definition.key}>
              <div className="optimizer-primary-stat-name">
                <h4 id={`optimizer-primary-${slug}-title`}>{definition.label}</h4>
                {definition.percentage && <span>%</span>}
              </div>
              <div className="field optimizer-primary-bound">
                <label className="sr-only" htmlFor={minimumId}>Minimum {definition.label}{unit}</label>
                <input
                  aria-describedby={minimumIssue ? `${minimumId}-error` : undefined}
                  aria-invalid={Boolean(minimumIssue)}
                  id={minimumId}
                  inputMode="decimal"
                  min={0}
                  onChange={(event) => onChange({
                    ...value,
                    [definition.key]: { ...stat, minimum: inputNumber(event.currentTarget.value) },
                  })}
                  placeholder="Any"
                  step={definition.percentage ? 0.1 : 1}
                  type="number"
                  value={displayNumber(stat.minimum)}
                />
                {minimumIssue && <span className="field-error-message" id={`${minimumId}-error`}>{minimumIssue}</span>}
              </div>
              <div className="field optimizer-primary-bound">
                <label className="sr-only" htmlFor={maximumId}>Maximum {definition.label}{unit}</label>
                <input
                  aria-describedby={maximumIssue ? `${maximumId}-error` : undefined}
                  aria-invalid={Boolean(maximumIssue)}
                  id={maximumId}
                  inputMode="decimal"
                  min={0}
                  onChange={(event) => onChange({
                    ...value,
                    [definition.key]: { ...stat, maximum: inputNumber(event.currentTarget.value) },
                  })}
                  placeholder="Any"
                  step={definition.percentage ? 0.1 : 1}
                  type="number"
                  value={displayNumber(stat.maximum)}
                />
                {maximumIssue && <span className="field-error-message" id={`${maximumId}-error`}>{maximumIssue}</span>}
              </div>
              <div className="field optimizer-priority-control">
                <div className="optimizer-priority-label">
                  <label className="sr-only" htmlFor={priorityId}>Priority</label>
                  <output htmlFor={priorityId}>{stat.priority} · {PRIORITY_LABELS[stat.priority]}</output>
                </div>
                <input
                  aria-describedby={priorityIssue ? `${priorityId}-error` : undefined}
                  aria-invalid={Boolean(priorityIssue)}
                  aria-valuetext={`${stat.priority}: ${PRIORITY_LABELS[stat.priority]}`}
                  id={priorityId}
                  max={3}
                  min={-1}
                  onChange={(event) => onChange({
                    ...value,
                    [definition.key]: {
                      ...stat,
                      priority: Number(event.currentTarget.value) as -1 | 0 | 1 | 2 | 3,
                    },
                  })}
                  step={1}
                  type="range"
                  value={stat.priority}
                />
                <div aria-hidden="true" className="optimizer-priority-ticks"><span>-1</span><span>0</span><span>1</span><span>2</span><span>3</span></div>
                {priorityIssue && <span className="field-error-message" id={`${priorityId}-error`}>{priorityIssue}</span>}
              </div>
            </section>
          );
        })}
      </div>
    </Card>
  );
}
