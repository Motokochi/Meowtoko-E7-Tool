import type { HealthSnapshot } from './shared/health';
import type { BackendConnectionState } from './shared/protocol';
import { Icon, type IconName } from './icons';
import { Alert, Badge, Button, Card, Skeleton } from './ui';

type BackendViewState = BackendConnectionState | { state: 'connecting' };

interface OverviewProps {
  backend: BackendViewState;
  health: HealthSnapshot | null;
  onReconnect(): void;
  onOpenHealth(): void;
}

interface WorkflowCard {
  id: string;
  title: string;
  description: string;
  dependency: string;
  icon: IconName;
}

const WORKFLOWS: readonly WorkflowCard[] = [
  {
    id: 'analyzer',
    title: 'Gear Analyzer',
    description: 'Capture gear, extract stats, and review rating evidence.',
    dependency: 'OCR + Ollama',
    icon: 'analyzer',
  },
  {
    id: 'enhancer',
    title: 'Enhancement Assistant',
    description: 'Apply your enhancement rules through focus-free ADB automation.',
    dependency: 'ADB device',
    icon: 'enhancer',
  },
  {
    id: 'optimizer',
    title: 'Build Optimizer',
    description: 'Search owned gear with accelerated CUDA and a reliable CPU fallback.',
    dependency: 'GPU optional',
    icon: 'optimizer',
  },
];

export function Overview({
  backend,
  health,
  onReconnect,
  onOpenHealth,
}: OverviewProps): React.JSX.Element {
  return (
    <div className="page-stack">
      <section className="overview-hero" aria-labelledby="overview-title">
        <div className="overview-hero-copy">
          <Badge tone="accent">LOCAL DESKTOP WORKSPACE</Badge>
          <h2 id="overview-title">Build better heroes.<br />Keep every decision yours.</h2>
          <p>
            One focused home for gear analysis, enhancement automation, and high-speed
            build search—designed to stay useful even when an optional tool is offline.
          </p>
          <div className="overview-actions">
            <Button icon="health" onClick={onOpenHealth}>Review system health</Button>
            <span>No console windows. No cloud account required.</span>
          </div>
        </div>

        <Card className="backend-card" elevated>
          <div className="card-heading">
            <span className={`connection-indicator connection-${backend.state}`} aria-hidden="true" />
            <div>
              <span className="card-kicker">APPLICATION CORE</span>
              <h3>
                {backend.state === 'connecting' && 'Connecting…'}
                {backend.state === 'ready' && 'Backend ready'}
                {backend.state === 'error' && 'Connection needs attention'}
              </h3>
            </div>
          </div>

          {backend.state === 'connecting' && <Skeleton label="Connecting to application backend" lines={2} />}

          {backend.state === 'ready' && (
            <dl className="definition-list">
              <div><dt>Bridge protocol</dt><dd>{backend.details.protocolVersion}</dd></div>
              <div><dt>Backend</dt><dd>v{backend.details.backendVersion}</dd></div>
              <div><dt>Python runtime</dt><dd>{backend.details.pythonVersion}</dd></div>
            </dl>
          )}

          {backend.state === 'error' && (
            <Alert
              actions={<Button onClick={onReconnect} size="small" variant="danger">Try again</Button>}
              title="The local backend did not answer"
              tone="danger"
            >
              {backend.message}
            </Alert>
          )}
        </Card>
      </section>

      <section aria-labelledby="workflow-title">
        <div className="section-heading">
          <div>
            <span className="card-kicker">YOUR TOOLKIT</span>
            <h2 id="workflow-title">Everything has a clear place</h2>
          </div>
          {health && (
            <Badge tone={health.overall === 'ready' ? 'success' : health.overall === 'error' ? 'danger' : 'warning'}>
              {health.capabilities.filter((item) => item.state === 'ready').length}/{health.capabilities.length} capabilities ready
            </Badge>
          )}
        </div>

        <div className="workflow-grid">
          {WORKFLOWS.map((workflow, index) => (
            <Card className="workflow-card" key={workflow.id}>
              <div className="workflow-card-topline">
                <span className="workflow-icon"><Icon name={workflow.icon} size={21} /></span>
                <span className="workflow-index">0{index + 1}</span>
              </div>
              <h3>{workflow.title}</h3>
              <p>{workflow.description}</p>
              <div className="workflow-footer">
                <Badge>{workflow.dependency}</Badge>
                <span>Available now</span>
              </div>
            </Card>
          ))}
        </div>
      </section>
    </div>
  );
}
