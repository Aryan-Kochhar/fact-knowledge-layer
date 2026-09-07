import type { Fact } from '../lib/api'
import { citation, shortDoc } from '../lib/format'
import { Badge, cx } from './ui'

/** A verbatim quote with its citation — the atom of the whole system. */
export function Quote({
  fact,
  className,
  compact,
}: {
  fact: Pick<Fact, 'quote' | 'printed_page' | 'page_index' | 'verification' | 'verify_score' | 'filename' | 'doc_title'>
  className?: string
  compact?: boolean
}) {
  return (
    <figure className={cx('group', className)}>
      <blockquote
        className={cx(
          'border-l-2 border-[var(--color-ink-700)] pl-3 font-mono leading-relaxed text-[var(--color-fg)]/85 transition-colors duration-300 group-hover:border-[var(--color-accent-dim)]',
          compact ? 'line-clamp-3 text-[11.5px]' : 'text-xs',
        )}
      >
        {fact.quote}
      </blockquote>
      <figcaption className="mt-2 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[11px] text-[var(--color-faint)]">
        <span className="truncate">{shortDoc(fact.doc_title, fact.filename)}</span>
        <span aria-hidden>·</span>
        <span className="font-mono">{citation(fact.printed_page, fact.page_index)}</span>
        <VerificationMark verification={fact.verification} score={fact.verify_score} />
      </figcaption>
    </figure>
  )
}

export function VerificationMark({
  verification,
  score,
}: {
  verification: Fact['verification']
  score: number
}) {
  if (verification === 'verified') {
    return (
      <span
        className="inline-flex items-center gap-1 text-[var(--color-agree)]"
        title={`Quote located in the source page text (match ${score.toFixed(2)})`}
      >
        <Check /> verified
      </span>
    )
  }
  if (verification === 'relocated') {
    return (
      <span
        className="inline-flex items-center gap-1 text-[var(--color-context)]"
        title="The model cited a different page; the citation was corrected to the page where the quote actually appears."
      >
        <Check /> citation corrected
      </span>
    )
  }
  return <span className="text-[var(--color-conflict)]">unverified</span>
}

function Check() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M4 12.5 9.5 18 20 6.5"
        stroke="currentColor"
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

/** The full source page with the cited span highlighted in place. */
export function PageContext({
  text,
  start,
  end,
  windowChars = 900,
}: {
  text: string | null
  start: number | null
  end: number | null
  windowChars?: number
}) {
  if (!text) {
    return (
      <div className="text-xs text-[var(--color-faint)]">Source page text is not available.</div>
    )
  }

  if (start === null || end === null || start < 0 || end > text.length || start >= end) {
    return (
      <pre className="max-h-80 overflow-auto font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-[var(--color-muted)]">
        {text.slice(0, 2000)}
      </pre>
    )
  }

  const from = Math.max(0, start - windowChars / 2)
  const to = Math.min(text.length, end + windowChars / 2)

  return (
    <pre className="max-h-80 overflow-auto font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-[var(--color-muted)]">
      {from > 0 && <span className="text-[var(--color-faint)]">…</span>}
      {text.slice(from, start)}
      <mark className="evidence-mark">{text.slice(start, end)}</mark>
      {text.slice(end, to)}
      {to < text.length && <span className="text-[var(--color-faint)]">…</span>}
    </pre>
  )
}

/** Normalised comparison keys — what the reconciliation logic actually saw. */
export function NormalisedChips({ fact }: { fact: Fact }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {fact.period_key && <Badge>{fact.period_key}</Badge>}
      {fact.period_basis && fact.period_basis !== 'unknown' && (
        <Badge tone={fact.period_basis === 'actual' ? 'neutral' : 'warn'}>{fact.period_basis}</Badge>
      )}
      {fact.value_unit && <Badge>{fact.value_unit}</Badge>}
      {fact.qualifiers?.map((q) => (
        <Badge key={q} tone="accent">
          {q}
        </Badge>
      ))}
    </div>
  )
}
