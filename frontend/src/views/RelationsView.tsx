import { useEffect, useState } from 'react'
import { api, type RelationType } from '../lib/api'
import { RELATION_META } from '../lib/format'
import { useAsync } from '../lib/hooks'
import { RelationCard } from '../components/RelationCard'
import { Button, EmptyState, ErrorNote, SectionHeading, Skeleton, cx } from '../components/ui'

const FILTERS: { value: RelationType | ''; label: string }[] = [
  { value: '', label: 'All' },
  { value: 'contradicts', label: 'Contradictions' },
  { value: 'reconcilable_context', label: 'Explained by context' },
  { value: 'corroborates', label: 'Corroborations' },
]

const PAGE_SIZE = 20

export function RelationsView({ onOpenFact }: { onOpenFact: (id: string) => void }) {
  const [filter, setFilter] = useState<RelationType | ''>('')
  const [crossOnly, setCrossOnly] = useState(false)
  const [page, setPage] = useState(0)

  useEffect(() => setPage(0), [filter, crossOnly])

  const { data: stats } = useAsync(() => api.stats(), [])
  const { data, loading, error } = useAsync(
    () =>
      api.relations({
        relation: filter || undefined,
        cross_doc_only: crossOnly,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
    [filter, crossOnly, page],
  )

  const total = data?.total ?? 0
  const pages = Math.ceil(total / PAGE_SIZE)

  return (
    <div className="space-y-8">
      <SectionHeading
        eyebrow="Reconciliation"
        title="How the documents relate"
        description="Candidate pairs come from vector similarity over metric identity — deliberately excluding values and periods, so facts that measure the same thing land together even when their numbers disagree. Unit conversion and period comparison are computed in Python; the label and the written reasoning come from the model."
      />

      <div className="animate-rise flex flex-wrap items-center gap-2">
        {FILTERS.map((f) => {
          const count =
            f.value === ''
              ? Object.entries(stats?.relation_counts ?? {})
                  .filter(([k]) => k !== 'unrelated')
                  .reduce((sum, [, n]) => sum + (n ?? 0), 0)
              : (stats?.relation_counts?.[f.value] ?? 0)
          const meta = f.value ? RELATION_META[f.value] : null
          return (
            <button
              key={f.value || 'all'}
              onClick={() => setFilter(f.value)}
              className={cx(
                'rounded-lg border px-3.5 py-2 text-xs transition-all duration-200',
                filter === f.value
                  ? 'border-[var(--color-ink-700)] bg-[var(--color-ink-850)] text-[var(--color-fg)]'
                  : 'border-[var(--color-line)] text-[var(--color-muted)] hover:border-[var(--color-ink-700)]',
              )}
              style={filter === f.value && meta ? { color: meta.color } : undefined}
            >
              {f.label}
              <span className="ml-2 font-mono text-[10px] text-[var(--color-faint)]">{count}</span>
            </button>
          )
        })}

        <button
          onClick={() => setCrossOnly((v) => !v)}
          className={cx(
            'ml-auto rounded-lg border px-3.5 py-2 text-xs transition-all duration-200',
            crossOnly
              ? 'border-[var(--color-accent-dim)] bg-[color-mix(in_oklab,var(--color-accent)_10%,transparent)] text-[var(--color-accent)]'
              : 'border-[var(--color-line)] text-[var(--color-muted)] hover:border-[var(--color-ink-700)]',
          )}
        >
          Cross-document only
        </button>
      </div>

      {error && <ErrorNote message={error} />}

      {loading && !data && (
        <div className="space-y-4">
          <Skeleton className="h-64 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      )}

      {data && data.relations.length === 0 ? (
        <EmptyState
          title="No relationships yet"
          body="Relationships appear once two or more documents share comparable facts. Ingest a second document covering overlapping subject matter."
        />
      ) : (
        <div className="space-y-5">
          {(data?.relations ?? []).map((relation, i) => (
            <RelationCard key={relation.id} relation={relation} index={i} onOpenFact={onOpenFact} />
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
    </div>
  )
}
