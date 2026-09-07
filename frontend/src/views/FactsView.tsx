import { useEffect, useState } from 'react'
import { api, type Fact } from '../lib/api'
import { RELATION_META, citation, formatNumber, shortDoc } from '../lib/format'
import { useAsync, useDebounced, useEscape } from '../lib/hooks'
import { NormalisedChips, PageContext, Quote, VerificationMark } from '../components/Evidence'
import { RelationCard } from '../components/RelationCard'
import { Badge, Button, Card, EmptyState, ErrorNote, SectionHeading, Skeleton, cx } from '../components/ui'

function FactItem({
  fact,
  onOpen,
  active,
  index,
}: {
  fact: Fact
  onOpen: (id: string) => void
  active: boolean
  index: number
}) {
  const links = Object.entries(fact.relation_counts ?? {}).filter(
    ([type, n]) => type !== 'unrelated' && (n ?? 0) > 0,
  )

  return (
    <Card
      interactive
      onClick={() => onOpen(fact.id)}
      className={cx(
        'animate-rise stagger w-full p-4',
        active && 'border-[var(--color-accent-dim)] bg-[var(--color-ink-850)]',
      )}
      style={{ ['--i' as string]: Math.min(index, 12) }}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium text-[var(--color-fg)]">{fact.subject}</div>
          <div className="truncate text-sm text-[var(--color-muted)]">{fact.predicate}</div>
        </div>
        <div className="shrink-0 text-right">
          <div className="font-mono text-sm tabular-nums text-[var(--color-fg)]">
            {fact.value_raw}
          </div>
          {fact.unit_raw && (
            <div className="font-mono text-[10px] text-[var(--color-faint)]">{fact.unit_raw}</div>
          )}
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[11px] text-[var(--color-faint)]">
        <span className="truncate">{shortDoc(fact.doc_title, fact.filename)}</span>
        <span className="font-mono">{citation(fact.printed_page, fact.page_index)}</span>
        {fact.period_key && <Badge>{fact.period_key}</Badge>}
        <VerificationMark verification={fact.verification} score={fact.verify_score} />
        {links.map(([type, n]) => {
          const meta = RELATION_META[type as keyof typeof RELATION_META]
          return (
            <span key={type} style={{ color: meta.color }} className="font-mono">
              {meta.glyph} {n}
            </span>
          )
        })}
      </div>
    </Card>
  )
}

function FactDrawer({ factId, onClose }: { factId: string; onClose: () => void }) {
  const { data: fact, loading, error } = useAsync(() => api.fact(factId), [factId])
  useEscape(onClose, true)

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <button
        aria-label="Close"
        onClick={onClose}
        className="animate-fade absolute inset-0 bg-black/60 backdrop-blur-[2px]"
      />
      <aside className="animate-slide-in relative flex h-full w-full max-w-2xl flex-col border-l border-[var(--color-line)] bg-[var(--color-ink-950)]">
        <header className="flex items-start justify-between gap-4 border-b border-[var(--color-line)] px-6 py-5">
          <div className="min-w-0">
            <div className="text-[10px] font-medium tracking-[0.18em] text-[var(--color-faint)] uppercase">
              Fact
            </div>
            <div className="mt-1 truncate font-mono text-[11px] text-[var(--color-muted)]">
              {factId}
            </div>
          </div>
          <Button size="sm" onClick={onClose}>
            Close
          </Button>
        </header>

        <div className="flex-1 overflow-y-auto px-6 py-6">
          {loading && !fact && (
            <div className="space-y-3">
              <Skeleton className="h-6 w-2/3" />
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-40 w-full" />
            </div>
          )}
          {error && <ErrorNote message={error} />}

          {fact && (
            <div className="space-y-8">
              <div>
                <h3 className="text-lg leading-snug font-medium text-[var(--color-fg)]">
                  {fact.subject}
                </h3>
                <p className="text-sm text-[var(--color-muted)]">{fact.predicate}</p>
                <div className="mt-3 flex flex-wrap items-baseline gap-2">
                  <span className="font-mono text-2xl tabular-nums text-[var(--color-fg)]">
                    {fact.value_raw}
                  </span>
                  {fact.unit_raw && (
                    <span className="font-mono text-sm text-[var(--color-muted)]">
                      {fact.unit_raw}
                    </span>
                  )}
                  {fact.time_scope_raw && (
                    <span className="text-sm text-[var(--color-faint)]">· {fact.time_scope_raw}</span>
                  )}
                </div>
                <div className="mt-3">
                  <NormalisedChips fact={fact} />
                </div>
              </div>

              <section>
                <div className="text-[10px] font-medium tracking-[0.16em] text-[var(--color-faint)] uppercase">
                  Normalised for comparison
                </div>
                <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-3 font-mono text-xs">
                  <Field label="value" value={formatNumber(fact.value_num)} />
                  <Field label="unit" value={fact.value_unit ?? '—'} />
                  <Field label="dimension" value={fact.value_dim ?? '—'} />
                  <Field label="period" value={fact.period_key ?? '—'} />
                  <Field label="basis" value={fact.period_basis ?? '—'} />
                  <Field label="confidence" value={fact.confidence.toFixed(2)} />
                </dl>
              </section>

              <section>
                <div className="flex items-center justify-between">
                  <div className="text-[10px] font-medium tracking-[0.16em] text-[var(--color-faint)] uppercase">
                    Evidence
                  </div>
                  <span className="font-mono text-[11px] text-[var(--color-faint)]">
                    {shortDoc(fact.doc_title, fact.filename)} ·{' '}
                    {citation(fact.printed_page, fact.page_index)}
                  </span>
                </div>
                <div className="mt-3">
                  <Quote fact={fact} />
                </div>
                <div className="mt-4 rounded-lg border border-[var(--color-line)] bg-[var(--color-ink-900)] p-4">
                  <div className="mb-2 text-[10px] tracking-[0.16em] text-[var(--color-faint)] uppercase">
                    In the source page
                  </div>
                  <PageContext
                    text={fact.evidence.page_text}
                    start={fact.evidence.quote_start}
                    end={fact.evidence.quote_end}
                  />
                </div>
              </section>

              <section>
                <div className="text-[10px] font-medium tracking-[0.16em] text-[var(--color-faint)] uppercase">
                  Relationships ({fact.relations.filter((r) => r.relation !== 'unrelated').length})
                </div>
                <div className="mt-3 space-y-4">
                  {fact.relations.filter((r) => r.relation !== 'unrelated').length === 0 && (
                    <p className="text-xs text-[var(--color-muted)]">
                      No corroborating or conflicting facts were found for this claim in the other
                      documents currently ingested.
                    </p>
                  )}
                  {fact.relations
                    .filter((r) => r.relation !== 'unrelated')
                    .map((relation, i) => (
                      <RelationCard key={relation.id} relation={relation} index={i} />
                    ))}
                </div>
              </section>
            </div>
          )}
        </div>
      </aside>
    </div>
  )
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[10px] tracking-wide text-[var(--color-faint)] uppercase">{label}</dt>
      <dd className="mt-0.5 text-[var(--color-fg)]">{value}</dd>
    </div>
  )
}

const PAGE_SIZE = 40

export function FactsView({
  selected,
  onSelect,
}: {
  selected?: string
  onSelect: (id?: string) => void
}) {
  const [search, setSearch] = useState('')
  const [docId, setDocId] = useState('')
  const [linkedOnly, setLinkedOnly] = useState(false)
  const [numericOnly, setNumericOnly] = useState(false)
  const [page, setPage] = useState(0)
  const query = useDebounced(search, 300)

  useEffect(() => setPage(0), [query, docId, linkedOnly, numericOnly])

  const { data: docs } = useAsync(() => api.documents(), [])
  const { data, loading, error } = useAsync(
    () =>
      api.facts({
        q: query || undefined,
        doc_id: docId || undefined,
        linked_only: linkedOnly,
        numeric_only: numericOnly,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
    [query, docId, linkedOnly, numericOnly, page],
  )

  const total = data?.total ?? 0
  const pages = Math.ceil(total / PAGE_SIZE)

  return (
    <div className="space-y-8">
      <SectionHeading
        eyebrow="Facts"
        title="Every extracted claim, with its receipt"
        description="Each row is an atomic claim whose quote was located character-for-character in the source page before it was stored. Open one to see the surrounding page text and anything it agrees or conflicts with."
      />

      <div className="animate-rise flex flex-wrap items-center gap-3">
        <div className="relative min-w-64 flex-1">
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search subjects, values, quotes…"
            className="w-full rounded-lg border border-[var(--color-line)] bg-[var(--color-ink-900)] px-4 py-2.5 text-sm text-[var(--color-fg)] placeholder:text-[var(--color-faint)] transition-colors focus:border-[var(--color-accent-dim)] focus:outline-none"
          />
        </div>

        <select
          value={docId}
          onChange={(e) => setDocId(e.target.value)}
          className="rounded-lg border border-[var(--color-line)] bg-[var(--color-ink-900)] px-3 py-2.5 text-sm text-[var(--color-fg)] focus:border-[var(--color-accent-dim)] focus:outline-none"
        >
          <option value="">All documents</option>
          {(docs ?? []).map((doc) => (
            <option key={doc.id} value={doc.id}>
              {shortDoc(doc.title, doc.filename)}
            </option>
          ))}
        </select>

        <Toggle active={linkedOnly} onToggle={() => setLinkedOnly((v) => !v)}>
          Linked only
        </Toggle>
        <Toggle active={numericOnly} onToggle={() => setNumericOnly((v) => !v)}>
          Numeric only
        </Toggle>
      </div>

      <div className="font-mono text-[11px] text-[var(--color-faint)]">
        {loading && !data ? 'loading…' : `${total.toLocaleString()} facts`}
      </div>

      {error && <ErrorNote message={error} />}

      {data && data.facts.length === 0 ? (
        <EmptyState
          title="Nothing matches"
          body="Try clearing the filters, or ingest a document from the Ingest tab."
        />
      ) : (
        <div className="grid gap-2">
          {(data?.facts ?? []).map((fact, i) => (
            <FactItem
              key={fact.id}
              fact={fact}
              index={i}
              active={selected === fact.id}
              onOpen={(id) => onSelect(id)}
            />
          ))}
        </div>
      )}

      {pages > 1 && (
        <div className="flex items-center justify-center gap-3">
          <Button size="sm" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
            Previous
          </Button>
          <span className="font-mono text-xs text-[var(--color-muted)]">
            {page + 1} / {pages}
          </span>
          <Button size="sm" disabled={page + 1 >= pages} onClick={() => setPage((p) => p + 1)}>
            Next
          </Button>
        </div>
      )}

      {selected && <FactDrawer factId={selected} onClose={() => onSelect(undefined)} />}
    </div>
  )
}

function Toggle({
  active,
  onToggle,
  children,
}: {
  active: boolean
  onToggle: () => void
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onToggle}
      className={cx(
        'rounded-lg border px-3 py-2.5 text-xs transition-all duration-200',
        active
          ? 'border-[var(--color-accent-dim)] bg-[color-mix(in_oklab,var(--color-accent)_10%,transparent)] text-[var(--color-accent)]'
          : 'border-[var(--color-line)] text-[var(--color-muted)] hover:border-[var(--color-ink-700)]',
      )}
    >
      {children}
    </button>
  )
}
