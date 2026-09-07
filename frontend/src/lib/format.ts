import type { RelationType } from './api'

export const RELATION_META: Record<
  RelationType,
  { label: string; short: string; color: string; ring: string; text: string; glyph: string }
> = {
  corroborates: {
    label: 'Corroborates',
    short: 'Agree',
    color: 'var(--color-agree)',
    ring: 'ring-[color-mix(in_oklab,var(--color-agree)_40%,transparent)]',
    text: 'text-[var(--color-agree)]',
    glyph: '=',
  },
  contradicts: {
    label: 'Contradicts',
    short: 'Conflict',
    color: 'var(--color-conflict)',
    ring: 'ring-[color-mix(in_oklab,var(--color-conflict)_40%,transparent)]',
    text: 'text-[var(--color-conflict)]',
    glyph: '≠',
  },
  reconcilable_context: {
    label: 'Reconcilable in context',
    short: 'Explained',
    color: 'var(--color-context)',
    ring: 'ring-[color-mix(in_oklab,var(--color-context)_40%,transparent)]',
    text: 'text-[var(--color-context)]',
    glyph: '≈',
  },
  unrelated: {
    label: 'Unrelated',
    short: 'Unrelated',
    color: 'var(--color-neutral)',
    ring: 'ring-white/10',
    text: 'text-[var(--color-neutral)]',
    glyph: '·',
  },
}

export const DIMENSION_LABEL: Record<string, string> = {
  time: 'Time period',
  unit: 'Units / scale',
  scope: 'Scope',
  basis: 'Measurement basis',
  none: '—',
}

/** Compact human rendering of a normalised magnitude. */
export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  const abs = Math.abs(value)
  if (abs >= 1e12) return `${(value / 1e12).toFixed(2)}T`
  if (abs >= 1e9) return `${(value / 1e9).toFixed(2)}B`
  if (abs >= 1e6) return `${(value / 1e6).toFixed(2)}M`
  if (abs >= 1e4) return value.toLocaleString(undefined, { maximumFractionDigits: 0 })
  if (abs >= 1) return value.toLocaleString(undefined, { maximumFractionDigits: 2 })
  return String(Number(value.toFixed(4)))
}

export function formatBytes(bytes: number): string {
  if (!bytes) return '—'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`
}

export function percent(value: number): string {
  return `${Math.round(value * 100)}%`
}

/** "page 46 (pdf 51)" — printed labels are what a reader can actually find. */
export function citation(printed: string | null, pageIndex: number | null): string {
  if (printed && String(printed) !== String(pageIndex)) return `p. ${printed} · pdf ${pageIndex}`
  if (printed) return `p. ${printed}`
  if (pageIndex) return `pdf p. ${pageIndex}`
  return 'no page'
}

export function shortDoc(name: string | null | undefined, fallback: string): string {
  const value = (name || fallback || '').replace(/\.pdf$/i, '').replace(/[-_]/g, ' ')
  return value.length > 64 ? `${value.slice(0, 62)}…` : value
}

export function titleCase(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1)
}
