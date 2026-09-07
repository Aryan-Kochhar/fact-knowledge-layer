import type { Fact, Relation } from '../lib/api'
import { DIMENSION_LABEL, RELATION_META, formatNumber, percent, shortDoc } from '../lib/format'
import { NormalisedChips, Quote } from './Evidence'
import { Badge, Card, cx } from './ui'

function toneFor(relation: Relation['relation']) {
  return relation === 'corroborates'
    ? 'agree'
    : relation === 'contradicts'
      ? 'conflict'
      : relation === 'reconcilable_context'
        ? 'context'
        : 'neutral'
}

function Side({
  fact,
  label,
  onOpen,
}: {
  fact: Fact
  label: string
  onOpen?: (id: string) => void
}) {
  return (
    <div className="min-w-0 flex-1 space-y-3">
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-[10px] tracking-[0.2em] text-[var(--color-faint)]">
          {label}
        </span>
        <span className="truncate text-[11px] text-[var(--color-muted)]">
          {shortDoc(fact.doc_title, fact.filename)}
        </span>
      </div>

      <button
        onClick={onOpen ? () => onOpen(fact.id) : undefined}
        className={cx(
          'block w-full text-left',
          onOpen && 'transition-opacity duration-200 hover:opacity-80',
        )}
      >
        <div className="text-sm leading-snug font-medium text-[var(--color-fg)]">
          {fact.subject}
        </div>
        <div className="text-sm text-[var(--color-muted)]">{fact.predicate}</div>
        <div className="mt-2 flex flex-wrap items-baseline gap-2">
          <span className="font-mono text-lg tabular-nums text-[var(--color-fg)]">
            {fact.value_raw}
          </span>
          {fact.unit_raw && (
            <span className="font-mono text-xs text-[var(--color-muted)]">{fact.unit_raw}</span>
          )}
          {fact.time_scope_raw && (
            <span className="text-xs text-[var(--color-faint)]">· {fact.time_scope_raw}</span>
          )}
        </div>
      </button>

      <NormalisedChips fact={fact} />
      <Quote fact={fact} compact />
    </div>
  )
}

/** Deterministic comparison, shown next to the model's verdict so a reader can
 *  check the arithmetic themselves. */
function NumericBridge({ a, b }: { a: Fact; b: Fact }) {
  if (a.value_num === null || b.value_num === null) return null
  const sameUnit = a.value_unit === b.value_unit
  const biggest = Math.max(Math.abs(a.value_num), Math.abs(b.value_num))
  const diff = biggest ? Math.abs(a.value_num - b.value_num) / biggest : 0

  return (
    <div className="flex flex-wrap items-center justify-center gap-x-3 gap-y-1 font-mono text-[11px] text-[var(--color-faint)]">
      <span>{formatNumber(a.value_num)}</span>
      <span className="text-[var(--color-line)]">|</span>
      <span>{formatNumber(b.value_num)}</span>
      {sameUnit ? (
        <span className={diff <= 0.005 ? 'text-[var(--color-agree)]' : 'text-[var(--color-muted)]'}>
          Δ {percent(diff)} {a.value_unit ? `(${a.value_unit})` : ''}
        </span>
      ) : (
        <span className="text-[var(--color-context)]">
          units differ: {a.value_unit ?? '—'} vs {b.value_unit ?? '—'}
        </span>
      )}
    </div>
  )
}

export function RelationCard({
  relation,
  onOpenFact,
  index = 0,
}: {
  relation: Relation
  onOpenFact?: (id: string) => void
  index?: number
}) {
  const meta = RELATION_META[relation.relation]
  const tone = toneFor(relation.relation)

  return (
    <Card
      className="animate-rise stagger overflow-hidden"
      style={{ ['--i' as string]: index }}
    >
      {/* Accent rail carries the verdict colour without flooding the card. */}
      <div className="h-px w-full" style={{ background: meta.color, opacity: 0.5 }} />

      <div className="flex flex-wrap items-center gap-2 border-b border-[var(--color-line-soft)] px-5 py-3">
        <Badge tone={tone}>
          <span className="font-mono">{meta.glyph}</span> {meta.label}
        </Badge>
        {relation.dimension !== 'none' && (
          <Badge>differs by {DIMENSION_LABEL[relation.dimension] ?? relation.dimension}</Badge>
        )}
        {relation.cross_doc ? (
          <Badge>cross-document</Badge>
        ) : (
          <Badge tone="warn">same document</Badge>
        )}
        <div className="ml-auto flex items-center gap-3 font-mono text-[11px] text-[var(--color-faint)]">
          <span title="Model confidence in this judgment">conf {relation.confidence.toFixed(2)}</span>
          <span title="Cosine similarity that made this pair a candidate">
            sim {relation.similarity.toFixed(2)}
          </span>
        </div>
      </div>

      <div className="px-5 py-5">
        <div className="flex flex-col gap-6 md:flex-row md:gap-8">
          <Side fact={relation.a} label="A" onOpen={onOpenFact} />
          <div className="flex shrink-0 flex-row items-center gap-3 md:flex-col md:justify-center">
            <div className="h-px flex-1 bg-[var(--color-line)] md:h-full md:w-px md:flex-none" />
            <span
              className="font-mono text-lg"
              style={{ color: meta.color }}
              aria-label={meta.label}
            >
              {meta.glyph}
            </span>
            <div className="h-px flex-1 bg-[var(--color-line)] md:h-full md:w-px md:flex-none" />
          </div>
          <Side fact={relation.b} label="B" onOpen={onOpenFact} />
        </div>

        <div className="mt-5 border-t border-[var(--color-line-soft)] pt-4">
          <NumericBridge a={relation.a} b={relation.b} />
        </div>

        <div className="mt-4 space-y-3">
          <div>
            <div className="text-[10px] font-medium tracking-[0.16em] text-[var(--color-faint)] uppercase">
              Reasoning
            </div>
            <p className="mt-1.5 text-sm leading-relaxed text-[var(--color-fg)]/85">
              {relation.reasoning}
            </p>
          </div>

          {relation.reconciliation && (
            <div
              className="rounded-lg border px-4 py-3"
              style={{
                borderColor: `color-mix(in oklab, ${meta.color} 28%, transparent)`,
                background: `color-mix(in oklab, ${meta.color} 7%, transparent)`,
              }}
            >
              <div
                className="text-[10px] font-medium tracking-[0.16em] uppercase"
                style={{ color: meta.color }}
              >
                How to reconcile
              </div>
              <p className="mt-1.5 text-sm leading-relaxed text-[var(--color-fg)]/85">
                {relation.reconciliation}
              </p>
            </div>
          )}
        </div>
      </div>
    </Card>
  )
}
