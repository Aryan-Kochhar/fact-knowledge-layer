import type { ReactNode } from 'react'

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(' ')
}

export function Card({
  children,
  className,
  interactive,
  onClick,
  style,
}: {
  children: ReactNode
  className?: string
  interactive?: boolean
  onClick?: () => void
  style?: React.CSSProperties
}) {
  const Tag = onClick ? 'button' : 'div'
  return (
    <Tag
      onClick={onClick}
      style={style}
      className={cx(
        'rounded-xl border border-[var(--color-line)] bg-[var(--color-ink-900)]',
        interactive &&
          'text-left transition-all duration-300 hover:border-[var(--color-ink-700)] hover:bg-[var(--color-ink-850)] focus-visible:border-[var(--color-accent-dim)]',
        className,
      )}
    >
      {children}
    </Tag>
  )
}

export function Badge({
  children,
  tone = 'neutral',
  className,
}: {
  children: ReactNode
  tone?: 'neutral' | 'agree' | 'conflict' | 'context' | 'accent' | 'warn'
  className?: string
}) {
  const tones: Record<string, string> = {
    neutral: 'border-[var(--color-line)] text-[var(--color-muted)]',
    agree:
      'border-[color-mix(in_oklab,var(--color-agree)_35%,transparent)] text-[var(--color-agree)] bg-[color-mix(in_oklab,var(--color-agree)_9%,transparent)]',
    conflict:
      'border-[color-mix(in_oklab,var(--color-conflict)_35%,transparent)] text-[var(--color-conflict)] bg-[color-mix(in_oklab,var(--color-conflict)_9%,transparent)]',
    context:
      'border-[color-mix(in_oklab,var(--color-context)_35%,transparent)] text-[var(--color-context)] bg-[color-mix(in_oklab,var(--color-context)_9%,transparent)]',
    accent:
      'border-[color-mix(in_oklab,var(--color-accent)_35%,transparent)] text-[var(--color-accent)] bg-[color-mix(in_oklab,var(--color-accent)_9%,transparent)]',
    warn: 'border-amber-500/35 text-amber-300 bg-amber-500/8',
  }
  return (
    <span
      className={cx(
        'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium tracking-wide whitespace-nowrap',
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  )
}

export function Stat({
  label,
  value,
  hint,
  accent,
}: {
  label: string
  value: ReactNode
  hint?: string
  accent?: string
}) {
  return (
    <div className="min-w-0">
      <div className="text-[11px] font-medium tracking-[0.14em] text-[var(--color-faint)] uppercase">
        {label}
      </div>
      <div
        className="mt-1.5 font-mono text-2xl font-medium tabular-nums"
        style={accent ? { color: accent } : undefined}
      >
        {value}
      </div>
      {hint && <div className="mt-0.5 truncate text-xs text-[var(--color-muted)]">{hint}</div>}
    </div>
  )
}

export function Spinner({ className }: { className?: string }) {
  return (
    <svg
      className={cx('animate-spin', className)}
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden
    >
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.2" strokeWidth="2.5" />
      <path
        d="M21 12a9 9 0 0 0-9-9"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
      />
    </svg>
  )
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx('shimmer rounded-lg', className)} />
}

export function EmptyState({
  title,
  body,
  action,
}: {
  title: string
  body: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="animate-fade flex flex-col items-center justify-center rounded-xl border border-dashed border-[var(--color-line)] px-8 py-20 text-center">
      <div className="text-base font-medium text-[var(--color-fg)]">{title}</div>
      <div className="mt-2 max-w-md text-sm leading-relaxed text-[var(--color-muted)]">{body}</div>
      {action && <div className="mt-6">{action}</div>}
    </div>
  )
}

export function Progress({ value, total }: { value: number; total: number }) {
  const pct = total > 0 ? Math.min(100, (value / total) * 100) : 0
  return (
    <div className="h-1 w-full overflow-hidden rounded-full bg-[var(--color-ink-800)]">
      <div
        className="h-full rounded-full bg-[var(--color-accent)] transition-[width] duration-700 ease-out"
        style={{ width: `${total > 0 ? pct : 0}%` }}
      />
    </div>
  )
}

export function Button({
  children,
  onClick,
  variant = 'ghost',
  size = 'md',
  disabled,
  className,
  type = 'button',
}: {
  children: ReactNode
  onClick?: () => void
  variant?: 'primary' | 'ghost' | 'danger'
  size?: 'sm' | 'md'
  disabled?: boolean
  className?: string
  type?: 'button' | 'submit'
}) {
  const variants: Record<string, string> = {
    primary:
      'bg-[var(--color-fg)] text-[var(--color-ink-950)] hover:bg-white disabled:bg-[var(--color-ink-700)] disabled:text-[var(--color-faint)]',
    ghost:
      'border border-[var(--color-line)] text-[var(--color-fg)] hover:border-[var(--color-ink-700)] hover:bg-[var(--color-ink-850)]',
    danger:
      'border border-[color-mix(in_oklab,var(--color-conflict)_30%,transparent)] text-[var(--color-conflict)] hover:bg-[color-mix(in_oklab,var(--color-conflict)_10%,transparent)]',
  }
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={cx(
        'inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-all duration-200 disabled:cursor-not-allowed disabled:opacity-60',
        size === 'sm' ? 'px-3 py-1.5 text-xs' : 'px-4 py-2 text-sm',
        variants[variant],
        className,
      )}
    >
      {children}
    </button>
  )
}

export function SectionHeading({
  eyebrow,
  title,
  description,
}: {
  eyebrow?: string
  title: string
  description?: ReactNode
}) {
  return (
    <div className="animate-rise max-w-2xl">
      {eyebrow && (
        <div className="text-[11px] font-medium tracking-[0.18em] text-[var(--color-accent)] uppercase">
          {eyebrow}
        </div>
      )}
      <h2 className="mt-2 text-2xl font-light tracking-tight text-[var(--color-fg)]">{title}</h2>
      {description && (
        <p className="mt-3 text-sm leading-relaxed text-[var(--color-muted)]">{description}</p>
      )}
    </div>
  )
}

export function ErrorNote({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-[color-mix(in_oklab,var(--color-conflict)_30%,transparent)] bg-[color-mix(in_oklab,var(--color-conflict)_8%,transparent)] px-4 py-3 text-sm text-[var(--color-conflict)]">
      {message}
    </div>
  )
}
