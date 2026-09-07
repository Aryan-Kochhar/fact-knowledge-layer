import { useCallback, useRef, useState } from 'react'
import { api, type DocumentProfile, type DocumentRow, type Job } from '../lib/api'
import { formatBytes } from '../lib/format'
import { useAsync, useInterval } from '../lib/hooks'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Progress,
  SectionHeading,
  Skeleton,
  Spinner,
  cx,
} from '../components/ui'

const STAGES = ['parsing', 'profiling', 'extracting', 'embedding', 'linking', 'done']

function StageTrack({ job }: { job: Job }) {
  const current = STAGES.indexOf(job.stage)
  return (
    <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1">
      {STAGES.slice(0, 5).map((stage, i) => {
        const done = current > i || job.status === 'done'
        const active = current === i && job.status === 'running'
        return (
          <span
            key={stage}
            className={cx(
              'rounded-full border px-2 py-0.5 text-[10px] tracking-wide transition-colors duration-500',
              done
                ? 'border-[color-mix(in_oklab,var(--color-agree)_35%,transparent)] text-[var(--color-agree)]'
                : active
                  ? 'border-[var(--color-accent-dim)] text-[var(--color-accent)]'
                  : 'border-[var(--color-line)] text-[var(--color-faint)]',
            )}
          >
            {stage}
          </span>
        )
      })}
    </div>
  )
}

const PROFILE_FIELDS: [keyof DocumentProfile, string][] = [
  ['publisher', 'Publisher'],
  ['doc_type', 'Type'],
  ['primary_entity', 'Reports on'],
  ['reporting_period', 'Reporting period'],
  ['as_of_date', 'As of'],
  ['currency', 'Currency'],
  ['scale_convention', 'Scale'],
  ['accounting_scope', 'Scope'],
]

/** The context inferred once from the opening pages and applied to every fact.
 *  Worth showing: it is why a figure on page 60 carries a period that page 60
 *  never states. */
function ProfilePanel({ docId }: { docId: string }) {
  const { data, loading } = useAsync(() => api.document(docId), [docId])
  if (loading && !data) return <Skeleton className="mt-4 h-24 w-full" />

  const profile = data?.profile
  if (!profile || typeof profile === 'string') {
    return (
      <p className="mt-4 text-xs text-[var(--color-muted)]">
        No document context was inferred for this file.
      </p>
    )
  }

  const rows = PROFILE_FIELDS.filter(([key]) => profile[key])
  return (
    <div className="animate-fade mt-4 rounded-lg border border-[var(--color-line)] bg-[var(--color-ink-950)] p-4">
      <div className="text-[10px] font-medium tracking-[0.16em] text-[var(--color-faint)] uppercase">
        Inferred document context
      </div>
      <dl className="mt-3 grid gap-x-6 gap-y-2 sm:grid-cols-2">
        {rows.map(([key, label]) => (
          <div key={key} className="flex gap-2 text-xs">
            <dt className="w-28 shrink-0 text-[var(--color-faint)]">{label}</dt>
            <dd className="min-w-0 font-mono text-[var(--color-fg)]/85">{profile[key]}</dd>
          </div>
        ))}
      </dl>
      {profile.notes && (
        <p className="mt-3 border-t border-[var(--color-line-soft)] pt-3 text-xs leading-relaxed text-[var(--color-muted)]">
          {profile.notes}
        </p>
      )}
      {data && (
        <div className="mt-3 font-mono text-[10px] text-[var(--color-faint)]">
          {data.pages_with_labels}/{data.page_count} pages carry a recoverable printed page number
        </div>
      )}
    </div>
  )
}

function DocumentRowItem({
  doc,
  job,
  onDelete,
  index,
}: {
  doc: DocumentRow
  job?: Job
  onDelete: (id: string) => void
  index: number
}) {
  const running = job && (job.status === 'running' || job.status === 'queued')
  const [expanded, setExpanded] = useState(false)

  return (
    <Card className="animate-rise stagger p-5" style={{ ['--i' as string]: index }}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-sm font-medium text-[var(--color-fg)]">
              {doc.title || doc.filename}
            </h3>
            {doc.status === 'ready' && <Badge tone="agree">ready</Badge>}
            {doc.status === 'failed' && <Badge tone="conflict">failed</Badge>}
            {running && (
              <Badge tone="accent">
                <Spinner className="h-3 w-3" /> {job?.stage}
              </Badge>
            )}
          </div>
          <div className="mt-1 truncate font-mono text-[11px] text-[var(--color-faint)]">
            {doc.filename}
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-1 font-mono text-[11px] text-[var(--color-muted)]">
            <span>{doc.page_count} pages</span>
            <span>{doc.fact_count} facts</span>
            <span>{formatBytes(doc.byte_size)}</span>
            {doc.issue_count > 0 && (
              <span className="text-[var(--color-context)]">{doc.issue_count} issues logged</span>
            )}
          </div>

          {doc.error && <div className="mt-3"><ErrorNote message={doc.error} /></div>}

          {running && job && (
            <div className="mt-4 space-y-2">
              <StageTrack job={job} />
              <Progress value={job.done} total={job.total} />
              <div className="font-mono text-[11px] text-[var(--color-muted)]">{job.message}</div>
            </div>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {doc.status === 'ready' && (
            <Button size="sm" onClick={() => setExpanded((v) => !v)}>
              {expanded ? 'Hide context' : 'Context'}
            </Button>
          )}
          <Button variant="danger" size="sm" onClick={() => onDelete(doc.id)} disabled={!!running}>
            Delete
          </Button>
        </div>
      </div>

      {expanded && <ProfilePanel docId={doc.id} />}
    </Card>
  )
}

function KeyPoolPanel() {
  const { data } = useAsync(() => api.keys(), [])
  const [tick, setTick] = useState(0)
  const { data: live } = useAsync(() => api.keys(), [tick])
  useInterval(() => setTick((t) => t + 1), 4000, true)
  const pool = live ?? data

  if (!pool) return null
  if (pool.keys.length === 0) {
    return (
      <Card className="border-[color-mix(in_oklab,var(--color-conflict)_30%,transparent)] p-5">
        <div className="text-sm font-medium text-[var(--color-conflict)]">No API keys configured</div>
        <p className="mt-2 text-xs leading-relaxed text-[var(--color-muted)]">
          Add a comma-separated pool to <span className="font-mono">GEMINI_API_KEYS</span> in{' '}
          <span className="font-mono">backend/.env</span> and restart the server. Uploads are
          rejected until at least one key is present.
        </p>
      </Card>
    )
  }

  return (
    <Card className="p-5">
      <div className="flex items-center justify-between">
        <div className="text-[11px] font-medium tracking-[0.16em] text-[var(--color-faint)] uppercase">
          Key pool
        </div>
        <span className="font-mono text-[11px] text-[var(--color-muted)]">
          {pool.keys.filter((k) => !k.disabled).length}/{pool.keys.length} live ·{' '}
          {pool.per_key_rpm} rpm each
        </span>
      </div>

      <div className="mt-4 space-y-2">
        {pool.keys.map((key) => {
          const load = Math.min(1, key.in_flight_window / Math.max(key.rpm_limit, 1))
          const cooling = key.cooldown_seconds > 0
          return (
            <div key={key.index} className="flex items-center gap-3">
              <span className="w-24 shrink-0 font-mono text-[11px] text-[var(--color-muted)]">
                {key.key}
              </span>
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-[var(--color-ink-800)]">
                <div
                  className="h-full rounded-full transition-all duration-500"
                  style={{
                    width: `${load * 100}%`,
                    background: key.disabled
                      ? 'var(--color-conflict)'
                      : cooling
                        ? 'var(--color-context)'
                        : 'var(--color-accent)',
                  }}
                />
              </div>
              <span className="w-28 shrink-0 text-right font-mono text-[10px] text-[var(--color-faint)]">
                {key.disabled
                  ? 'disabled'
                  : cooling
                    ? `cooldown ${key.cooldown_seconds.toFixed(0)}s`
                    : `${key.total_calls} calls`}
              </span>
            </div>
          )
        })}
      </div>

      <p className="mt-4 text-[11px] leading-relaxed text-[var(--color-faint)]">
        Requests are spread across keys by earliest availability. A key that returns 429 is parked
        for its retry window rather than retried into the same limit.
      </p>
    </Card>
  )
}

export function IngestView() {
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  const { data: docs } = useAsync(() => api.documents(), [refreshKey])
  const { data: jobs } = useAsync(() => api.jobs(), [refreshKey])

  const anyRunning = (jobs ?? []).some((j) => j.status === 'running' || j.status === 'queued')
  useInterval(() => setRefreshKey((k) => k + 1), 1500, anyRunning)
  useInterval(() => setRefreshKey((k) => k + 1), 10000, !anyRunning)

  const jobByDoc = new Map<string, Job>()
  for (const job of jobs ?? []) {
    if (job.doc_id && !jobByDoc.has(job.doc_id)) jobByDoc.set(job.doc_id, job)
  }

  const submit = useCallback(async (files: File[]) => {
    const pdfs = files.filter((f) => f.name.toLowerCase().endsWith('.pdf'))
    if (pdfs.length === 0) {
      setError('Only PDF files can be ingested.')
      return
    }
    setUploading(true)
    setError(null)
    setNotice(null)
    try {
      const result = await api.upload(pdfs)
      const queued = result.results.filter((r) => r.status === 'queued').length
      const dupes = result.results.filter((r) => r.status === 'duplicate')
      const rejected = result.results.filter((r) => r.status === 'rejected')
      const parts: string[] = []
      if (queued) parts.push(`${queued} queued for ingestion`)
      if (dupes.length) parts.push(`${dupes.length} already ingested (identical content)`)
      if (rejected.length)
        parts.push(`${rejected.length} rejected: ${rejected.map((r) => r.reason).join(', ')}`)
      setNotice(parts.join(' · '))
      setRefreshKey((k) => k + 1)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setUploading(false)
    }
  }, [])

  const remove = useCallback(async (id: string) => {
    try {
      const result = await api.deleteDocument(id)
      setNotice(
        `Removed document · ${result.facts_removed} facts and ${result.relations_removed} relationships deleted`,
      )
      setRefreshKey((k) => k + 1)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [])

  return (
    <div className="space-y-10">
      <SectionHeading
        eyebrow="Ingest"
        title="Add documents"
        description="Each PDF is parsed page by page, profiled once for its reporting context, chunked, and extracted into evidence-linked facts. Adding a document never reprocesses the ones already here — only the new cross-document comparisons it makes possible."
      />

      <div
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragging(false)
          void submit(Array.from(e.dataTransfer.files))
        }}
        className={cx(
          'animate-rise relative overflow-hidden rounded-xl border border-dashed transition-all duration-300',
          dragging
            ? 'border-[var(--color-accent)] bg-[color-mix(in_oklab,var(--color-accent)_6%,transparent)]'
            : 'border-[var(--color-line)] hover:border-[var(--color-ink-700)]',
        )}
      >
        <div className="flex flex-col items-center px-8 py-16 text-center">
          <div
            className={cx(
              'grid h-12 w-12 place-items-center rounded-full border border-[var(--color-line)] text-[var(--color-muted)]',
              dragging && 'animate-pulse-ring border-[var(--color-accent)] text-[var(--color-accent)]',
            )}
          >
            {uploading ? <Spinner /> : <UploadGlyph />}
          </div>
          <div className="mt-5 text-sm text-[var(--color-fg)]">
            Drop PDFs here, or{' '}
            <button
              onClick={() => inputRef.current?.click()}
              className="text-[var(--color-accent)] underline decoration-dotted underline-offset-4 transition-opacity hover:opacity-80"
            >
              choose files
            </button>
          </div>
          <div className="mt-2 text-xs text-[var(--color-muted)]">
            Any text-based PDF. Nothing about the schema, layout or subject matter is assumed.
          </div>
          <input
            ref={inputRef}
            type="file"
            accept="application/pdf"
            multiple
            hidden
            onChange={(e) => {
              void submit(Array.from(e.target.files ?? []))
              e.target.value = ''
            }}
          />
        </div>
      </div>

      {notice && (
        <div className="animate-fade rounded-lg border border-[var(--color-line)] bg-[var(--color-ink-900)] px-4 py-3 text-sm text-[var(--color-muted)]">
          {notice}
        </div>
      )}
      {error && <ErrorNote message={error} />}

      <div className="grid gap-8 lg:grid-cols-[1fr_340px]">
        <div className="space-y-3">
          <div className="text-[11px] font-medium tracking-[0.16em] text-[var(--color-faint)] uppercase">
            Corpus
          </div>
          {docs && docs.length === 0 ? (
            <EmptyState
              title="No documents yet"
              body="Upload two or more related PDFs to see corroborations, contradictions and context-explained differences between them."
            />
          ) : (
            (docs ?? []).map((doc, i) => (
              <DocumentRowItem
                key={doc.id}
                doc={doc}
                job={jobByDoc.get(doc.id)}
                onDelete={remove}
                index={i}
              />
            ))
          )}
        </div>

        <div className="space-y-3">
          <div className="text-[11px] font-medium tracking-[0.16em] text-[var(--color-faint)] uppercase">
            Throughput
          </div>
          <KeyPoolPanel />
        </div>
      </div>
    </div>
  )
}

function UploadGlyph() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5M4 16v2.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V16"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}
