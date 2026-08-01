import {
  cloneElement,
  useEffect,
  useId,
  useRef,
  type ButtonHTMLAttributes,
  type HTMLAttributes,
  type InputHTMLAttributes,
  type ReactElement,
  type ReactNode,
} from 'react';

import { Icon, type IconName } from './icons';

type Tone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger' | 'info';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  size?: 'small' | 'medium';
  icon?: IconName;
  busy?: boolean;
}

export function Button({
  variant = 'primary',
  size = 'medium',
  icon,
  busy = false,
  className = '',
  children,
  disabled,
  ...props
}: ButtonProps): React.JSX.Element {
  return (
    <button
      {...props}
      aria-busy={busy || undefined}
      className={`button button-${variant} button-${size} ${className}`.trim()}
      disabled={disabled || busy}
    >
      {icon && <Icon className={busy ? 'button-icon spinning' : 'button-icon'} name={icon} size={16} />}
      <span>{children}</span>
    </button>
  );
}

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  elevated?: boolean;
}

export function Card({ elevated = false, className = '', ...props }: CardProps): React.JSX.Element {
  return <div {...props} className={`card ${elevated ? 'card-elevated' : ''} ${className}`.trim()} />;
}

interface BadgeProps {
  tone?: Tone;
  children: ReactNode;
  className?: string;
}

export function Badge({ tone = 'neutral', children, className = '' }: BadgeProps): React.JSX.Element {
  return <span className={`badge badge-${tone} ${className}`.trim()}>{children}</span>;
}

interface AlertProps {
  tone?: Extract<Tone, 'info' | 'success' | 'warning' | 'danger'>;
  title: string;
  children?: ReactNode;
  actions?: ReactNode;
  className?: string;
}

export function Alert({ tone = 'info', title, children, actions, className = '' }: AlertProps): React.JSX.Element {
  const icon = tone === 'danger' || tone === 'warning' ? 'alert' : tone === 'success' ? 'check' : 'info';
  return (
    <div className={`alert alert-${tone} ${className}`.trim()} role={tone === 'danger' ? 'alert' : 'status'}>
      <Icon className="alert-icon" name={icon} />
      <div className="alert-content">
        <strong>{title}</strong>
        {children && <div className="alert-body">{children}</div>}
      </div>
      {actions && <div className="alert-actions">{actions}</div>}
    </div>
  );
}

interface TextInputProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  description?: string;
  error?: string;
}

export function TextInput({ label, description, error, id, className = '', ...props }: TextInputProps): React.JSX.Element {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  const descriptionId = description ? `${inputId}-description` : undefined;
  const errorId = error ? `${inputId}-error` : undefined;
  const describedBy = [descriptionId, errorId].filter(Boolean).join(' ') || undefined;
  return (
    <div className={`field ${error ? 'field-error' : ''} ${className}`.trim()}>
      <label htmlFor={inputId}>{label}</label>
      {description && <span className="field-description" id={descriptionId}>{description}</span>}
      <input {...props} aria-describedby={describedBy} aria-invalid={Boolean(error)} id={inputId} />
      {error && <span className="field-error-message" id={errorId}>{error}</span>}
    </div>
  );
}

interface TooltipProps {
  label: string;
  children: ReactElement<{ 'aria-describedby'?: string }>;
}

export function Tooltip({ label, children }: TooltipProps): React.JSX.Element {
  const tooltipId = useId();
  const describedBy = [children.props['aria-describedby'], tooltipId].filter(Boolean).join(' ');
  return (
    <span className="tooltip-anchor">
      {cloneElement(children, { 'aria-describedby': describedBy })}
      <span className="tooltip" id={tooltipId} role="tooltip">{label}</span>
    </span>
  );
}

interface DialogProps {
  open: boolean;
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
  onClose(): void;
}

export function Dialog({ open, title, description, children, footer, onClose }: DialogProps): React.JSX.Element | null {
  const titleId = useId();
  const descriptionId = useId();
  const dialogRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!open) return undefined;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialogRef.current?.focus();
    const closeOnEscape = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('keydown', closeOnEscape);
      if (previous?.isConnected) previous.focus();
    };
  }, [open]);
  if (!open) {
    return null;
  }
  return (
    <div className="dialog-backdrop">
      <section
        aria-describedby={description ? descriptionId : undefined}
        aria-labelledby={titleId}
        aria-modal="true"
        className="dialog"
        ref={dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <div className="dialog-header">
          <div>
            <h2 id={titleId}>{title}</h2>
            {description && <p id={descriptionId}>{description}</p>}
          </div>
          <Button aria-label="Close dialog" onClick={onClose} size="small" type="button" variant="ghost">Close</Button>
        </div>
        <div className="dialog-body">{children}</div>
        {footer && <div className="dialog-footer">{footer}</div>}
      </section>
    </div>
  );
}

export interface ToastNotice {
  id: string;
  message: string;
  tone?: Extract<Tone, 'info' | 'success' | 'warning' | 'danger'>;
}

interface ToastRegionProps {
  notices: readonly ToastNotice[];
  onDismiss(id: string): void;
}

export function ToastRegion({ notices, onDismiss }: ToastRegionProps): React.JSX.Element {
  return (
    <div aria-atomic="false" aria-live="polite" className="toast-region">
      {notices.map((notice) => (
        <div className={`toast toast-${notice.tone ?? 'info'}`} key={notice.id} role="status">
          <span>{notice.message}</span>
          <button aria-label="Dismiss notification" onClick={() => onDismiss(notice.id)} type="button">×</button>
        </div>
      ))}
    </div>
  );
}

interface SkeletonProps {
  label: string;
  lines?: number;
}

export function Skeleton({ label, lines = 3 }: SkeletonProps): React.JSX.Element {
  return (
    <div aria-label={label} className="skeleton-card" role="status">
      <span className="sr-only">{label}</span>
      <span className="skeleton skeleton-heading" />
      {Array.from({ length: lines }, (_, index) => (
        <span className="skeleton skeleton-line" key={index} />
      ))}
    </div>
  );
}

interface EmptyStateProps {
  icon?: IconName;
  title: string;
  description: string;
  action?: ReactNode;
}

export function EmptyState({ icon = 'info', title, description, action }: EmptyStateProps): React.JSX.Element {
  return (
    <div className="empty-state">
      <span className="empty-state-icon"><Icon name={icon} size={24} /></span>
      <h2>{title}</h2>
      <p>{description}</p>
      {action && <div className="empty-state-action">{action}</div>}
    </div>
  );
}
