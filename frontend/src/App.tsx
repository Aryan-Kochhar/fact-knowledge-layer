import { useCallback } from 'react'
import { api } from './lib/api'
import { useAsync, useHashRoute } from './lib/hooks'
import { FactsView } from './views/FactsView'
import { IngestView } from './views/IngestView'
import { RelationsView } from './views/RelationsView'
import { ShowcaseView } from './views/ShowcaseView'
import { cx } from './components/ui'

const TABS = [
  { id: 'showcase', label: 'Walkthrough' },
  { id: 'relations', label: 'Reconciliation' },
  { id: 'facts', label: 'Facts' },
  { id: 'ingest', label: 'Ingest' },
]

function HeaderStats() {
  const { data } = useAsync(() => api.stats(), [])
  if (!data) return null

  const items = [
    { label: 'docs', value: data.documents },
    { label: 'facts', value: data.facts },
    { label: 'links', value: data.relations - (data.relation_counts.unrelated ?? 0) },
  ]

  return (
    <div className="hidden items-center gap-5 font-mono text-[11px] text-[var(--color-faint)] sm:flex">
      {items.map((item) => (
        <span key={item.label} className="tabular-nums">
          <span className="text-[var(--color-muted)]">{item.value.toLocaleString()}</span>{' '}
          {item.label}
        </span>
      ))}
    </div>
  )
}

export default function App() {
  const [route, navigate] = useHashRoute()

  const openFact = useCallback(
    (id: string) => {
      navigate('facts', id)
    },
    [navigate],
  )

  const selectFact = useCallback(
    (id?: string) => {
      navigate('facts', id)
    },
    [navigate],
  )

  return (
    <div className="min-h-screen">
      {/* A single hairline of colour at the very top — the only chrome. */}
      <div
        className="fixed inset-x-0 top-0 z-40 h-px"
        style={{
          background:
            'linear-gradient(90deg, transparent, color-mix(in oklab, var(--color-accent) 55%, transparent), transparent)',
        }}
      />

      <header className="sticky top-0 z-30 border-b border-[var(--color-line)] bg-[var(--color-ink-950)]/85 backdrop-blur-xl">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-8 gap-y-3 px-6 py-4">
          <button
            onClick={() => navigate('showcase')}
            className="group flex items-center gap-3 text-left"
          >
            <span className="grid h-7 w-7 place-items-center rounded-md border border-[var(--color-line)] transition-colors duration-300 group-hover:border-[var(--color-accent-dim)]">
              <Mark />
            </span>
            <span>
              <span className="block text-sm font-medium tracking-tight text-[var(--color-fg)]">
                Fact Knowledge Layer
              </span>
              <span className="block text-[10px] tracking-[0.16em] text-[var(--color-faint)] uppercase">
                evidence-linked reconciliation
              </span>
            </span>
          </button>

          <nav className="flex items-center gap-1">
            {TABS.map((tab) => (
              <button
                key={tab.id}
                onClick={() => navigate(tab.id)}
                className={cx(
                  'relative rounded-lg px-3 py-1.5 text-xs transition-colors duration-200',
                  route.view === tab.id
                    ? 'text-[var(--color-fg)]'
                    : 'text-[var(--color-muted)] hover:text-[var(--color-fg)]',
                )}
              >
                {tab.label}
                {route.view === tab.id && (
                  <span className="absolute inset-x-2 -bottom-[13px] h-px bg-[var(--color-accent)]" />
                )}
              </button>
            ))}
          </nav>

          <div className="ml-auto">
            <HeaderStats />
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-6 py-14">
        {route.view === 'showcase' && <ShowcaseView onOpenFact={openFact} />}
        {route.view === 'relations' && <RelationsView onOpenFact={openFact} />}
        {route.view === 'facts' && <FactsView selected={route.param} onSelect={selectFact} />}
        {route.view === 'ingest' && <IngestView />}
      </main>

      <footer className="border-t border-[var(--color-line)]">
        <div className="mx-auto max-w-6xl px-6 py-8 text-[11px] leading-relaxed text-[var(--color-faint)]">
          Facts are stored only when their quote is located character-for-character in the source
          page. Page citations show the number printed in the document, with the PDF position beside
          it where they differ.
        </div>
      </footer>
    </div>
  )
}

function Mark() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="7" cy="7" r="2.6" stroke="var(--color-accent)" strokeWidth="1.7" />
      <circle cx="17" cy="17" r="2.6" stroke="var(--color-accent)" strokeWidth="1.7" />
      <path
        d="M9.2 8.8 14.8 15.2"
        stroke="var(--color-muted)"
        strokeWidth="1.7"
        strokeLinecap="round"
      />
    </svg>
  )
}
