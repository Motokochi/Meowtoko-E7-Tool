import type { OverallHealthState } from './shared/health';
import { Icon } from './icons';
import {
  NAVIGATION_ITEMS,
  navigationItem,
  type PageId,
} from './navigation';
import type { ThemePreference } from './theme';
import { Badge } from './ui';

interface AppShellProps {
  activePage: PageId;
  healthState: OverallHealthState | null;
  themePreference: ThemePreference;
  onNavigate(pageId: PageId): void;
  onThemeChange(preference: ThemePreference): void;
  banner?: React.ReactNode;
  children: React.ReactNode;
}

const HEALTH_LABELS: Record<OverallHealthState, string> = {
  checking: 'Checking system',
  ready: 'All systems ready',
  degraded: 'Limited features',
  error: 'Attention needed',
};

const HEALTH_TONES: Record<OverallHealthState, 'neutral' | 'success' | 'warning' | 'danger'> = {
  checking: 'neutral',
  ready: 'success',
  degraded: 'warning',
  error: 'danger',
};

export function AppShell({
  activePage,
  healthState,
  themePreference,
  onNavigate,
  onThemeChange,
  banner,
  children,
}: AppShellProps): React.JSX.Element {
  const current = navigationItem(activePage);

  return (
    <>
      <a className="skip-link" href="#main-content">Skip to main content</a>
      <div className="app-frame">
        <aside className="sidebar" aria-label="Application sidebar">
          <div className="brand-lockup">
            <span className="brand-mark" aria-hidden="true">E7</span>
            <span>
              <strong>Meowtoko E7 Tool</strong>
              <small>Desktop toolkit</small>
            </span>
          </div>

          <nav aria-label="Primary navigation" className="primary-navigation">
            <p className="navigation-label">Workspace</p>
            <ul>
              {NAVIGATION_ITEMS.map((item) => (
                <li key={item.id}>
                  <button
                    aria-current={activePage === item.id ? 'page' : undefined}
                    className="navigation-link"
                    disabled={!item.enabled}
                    onClick={() => onNavigate(item.id)}
                    type="button"
                  >
                    <Icon name={item.icon} />
                    <span className="navigation-copy">
                      <strong>{item.label}</strong>
                      <small>{item.description}</small>
                    </span>
                    {!item.enabled && <Badge>Later</Badge>}
                  </button>
                </li>
              ))}
            </ul>
          </nav>

          <div className="sidebar-footer">
            <span className="sidebar-footer-dot" aria-hidden="true" />
            <span><strong>Local first</strong><small>Your data stays on this PC</small></span>
          </div>
        </aside>

        <div className="workspace">
          <header className="app-header">
            <div className="page-title">
              <p>{current.description}</p>
              <h1>{current.label}</h1>
            </div>
            <div className="header-controls">
              <button
                className="readiness-control"
                onClick={() => onNavigate('health')}
                type="button"
              >
                <span className={`readiness-dot readiness-${healthState ?? 'checking'}`} aria-hidden="true" />
                <span>{healthState ? HEALTH_LABELS[healthState] : 'Checking system'}</span>
                <Badge tone={healthState ? HEALTH_TONES[healthState] : 'neutral'}>
                  Health
                </Badge>
              </button>

              <label className="theme-picker">
                <Icon name="palette" size={16} />
                <span className="sr-only">Color theme</span>
                <select
                  aria-label="Color theme"
                  onChange={(event) => onThemeChange(event.target.value as ThemePreference)}
                  value={themePreference}
                >
                  <option value="system">System</option>
                  <option value="light">Light</option>
                  <option value="dark">Dark</option>
                </select>
              </label>
            </div>
          </header>

          {banner}
          <main className="page-content" id="main-content" tabIndex={-1}>
            {children}
          </main>
        </div>
      </div>
    </>
  );
}
