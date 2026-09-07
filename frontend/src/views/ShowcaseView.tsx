import type { ReactNode } from 'react'
import { api, type Issue } from '../lib/api'
import { percent } from '../lib/format'
import { useAsync } from '../lib/hooks'
import { RelationCard } from '../components/RelationCard'
import { Badge, Card, EmptyState, ErrorNote, SectionHeading, Skeleton, Stat } from '../components/ui'

function CaseHeader({
  n,
  title,
  claim,
  accent,
}: {
  n: number
  title: string
  claim: ReactNode
  accent: string
}) {
  return (
    <div className="animate-rise flex gap-5">
      <div
        className="mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-full border font-mono text-sm"
        style={{ borderColor: `color-mix(in oklab, ${accent} 40%, transparent)`, color: accent }}
      >
        {n}
      </div>
      <div className="min-w-0">
        <h3 className="text-lg font-medium tracking-tight text-[var(--color-fg)]">{title}</h3>
        <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-[var(--color-muted)]">{claim}</p>
      </div>
    </div>
  )
}

const ISSUE_EXPLANATION: Record<string, { title: string; handling: string }> = {
  quote_not_found: {
    title: 'Unverifiable evidence — fact rejected',
    handling:
      'The model returned a quote that does not occur in the page text it was given, usually because it stitched together two fragments of a table or smoothed the wording. Since the citation could not be located, the fact was discarded rather than stored with a citation that would not survive a click-through.',
  },
  quote_relocated: {
    title: 'Wrong page cited — citation repaired',
    handling:
      'The quote was real but attributed to the wrong page, typically because the page footer prints a different number from the PDF position. The quote was found on a neighbouring page in the same chunk and the citation was rewritten to point there.',
  },
  incomplete_fact: {
    title: 'Malformed fact — dropped',
    handling:
      'The model omitted a required field (subject, predicate, value or quote). Without all four the record cannot be compared or cited, so it is dropped and logged rather than stored half-formed.',
  },
  chunk_failed: {
    title: 'Extraction call failed',
    handling:
      'Every retry across the key pool failed for this chunk — exhausted quota, or a response that could not be parsed even after repair. Failures are isolated per chunk, so the rest of the document still ingests; re-uploading after quota resets picks up only what is missing.',
  },
  judge_failed: {
    title: 'Relationship judgment failed',
    handling:
      'A batch of candidate pairs could not be judged. The facts and their evidence are unaffected; only the relationships for that batch are missing.',
  },
  unexpected_shape: {
    title: 'Unexpected response shape',
    handling:
      'The model returned valid JSON in a shape we did not ask for. The parser accepts several common shapes; anything else is logged with the raw payload so the prompt can be tightened.',
  },
  no_text_layer: {
    title: 'No extractable text',
    handling:
      'The PDF yielded almost no characters, which means it is a scan with no text layer. The pipeline cannot extract from images; an OCR pass would be required first.',
  },
  judgment_inconsistent: {
    title: 'Reasoning failure — verdict contradicted the arithmetic',
    handling:
      'The model returned a label that its own inputs rule out: a corroboration between two different periods, or a contradiction between an estimate and an actual. This happens because the judge sees each fact’s evidence quote, and quotes routinely mention other figures and years — in one observed case a 6.6%/2023 fact and a 5.7%/2024 fact were called identical because both quotes contained "5.7 per cent in 2024". The deterministic layer only ever sees normalised values and periods, so it cannot be fooled the same way. Every verdict is cross-checked against it; a verdict that disagrees is kept but has its confidence halved, is marked llm-flagged, and is listed here rather than being quietly trusted.',
  },
  pairs_over_budget: {
    title: 'Relationship graph is a ranked subset',
    handling:
      'Candidate pairs grow roughly quadratically with corpus size, and judging all of them would exceed a free-tier daily quota many times over. Pairs are ranked by how likely they are to yield an interesting verdict — same-period value gaps first, quiet agreement last — and the budget is spent from the top. This is recorded rather than hidden: a pair that was never judged is not a pair that was judged unrelated.',
  },
  profile_failed: {
    title: 'Document context could not be inferred',
    handling:
      'The opening-pages pass failed, so facts from this document only carry a period where their own text states one. Cross-document matching still works, but figures that rely on the document’s implicit reporting period may be missing a period key.',
  },
}

function FailureCase({ data }: { data: NonNullable<Awaited<ReturnType<typeof api.showcase>>>['failures'] }) {
  const grouped = new Map<string, Issue[]>()
  for (const issue of data.examples) {
    const list = grouped.get(issue.kind) ?? []
    list.push(issue)
    grouped.set(issue.kind, list)
  }

  return (
    <div className="space-y-6">
      <Card className="animate-rise p-6">
        <div className="grid grid-cols-2 gap-6 sm:grid-cols-4">
          <Stat label="Facts stored" value={data.summary.facts_stored.toLocaleString()} />
          <Stat
            label="Rejected"
            value={data.summary.facts_rejected_unverifiable_quote.toLocaleString()}
            hint="quote not found in source"
            accent="var(--color-conflict)"
          />
          <Stat
            label="Citations repaired"
            value={data.summary.citations_auto_corrected.toLocaleString()}
            hint="wrong page, quote relocated"
            accent="var(--color-context)"
          />
          <Stat
            label="Verdicts flagged"
            value={(data.summary.flagged_judgments ?? 0).toLocaleString()}
            hint={`contradicted the arithmetic · ${percent(data.summary.rejection_rate)} fact rejection rate`}
            accent="var(--color-accent)"
          />
        </div>
      </Card>

      {data.counts.length === 0 && (
        <p className="text-sm text-[var(--color-muted)]">
          No failures have been logged yet. Ingest a document to populate this section.
        </p>
      )}

      {[...grouped.entries()].map(([kind, issues]) => {
        const meta = ISSUE_EXPLANATION[kind] ?? {
          title: kind,
          handling: 'Logged for inspection.',
        }
        const count = data.counts.find((c) => c.kind === kind)?.n ?? issues.length
        return (
          <Card key={kind} className="animate-rise overflow-hidden">
            <div className="flex flex-wrap items-center gap-3 border-b border-[var(--color-line-soft)] px-5 py-3">
              <span className="text-sm font-medium text-[var(--color-fg)]">{meta.title}</span>
              <Badge tone={kind === 'quote_not_found' ? 'conflict' : 'warn'}>
                {count} logged
              </Badge>
              <code className="ml-auto font-mono text-[10px] text-[var(--color-faint)]">{kind}</code>
            </div>

            <div className="space-y-4 px-5 py-4">
              <p className="text-sm leading-relaxed text-[var(--color-muted)]">{meta.handling}</p>

              <div className="space-y-2">
                {issues.slice(0, 2).map((issue) => (
                  <div
                    key={issue.id}
                    className="rounded-lg border border-[var(--color-line)] bg-[var(--color-ink-900)] p-3"
                  >
                    <div className="font-mono text-[11px] leading-relaxed text-[var(--color-fg)]/80">
                      {issue.detail}
                    </div>
                    {issue.filename && (
                      <div className="mt-1.5 font-mono text-[10px] text-[var(--color-faint)]">
                        {issue.filename}
                      </div>
                    )}
                    {kind === 'quote_not_found' &&
                      typeof issue.payload === 'object' &&
                      issue.payload !== null &&
                      'fact' in issue.payload && (
                        <div className="mt-2 border-l-2 border-[color-mix(in_oklab,var(--color-conflict)_40%,transparent)] pl-3">
                          <div className="text-[10px] tracking-wide text-[var(--color-faint)] uppercase">
                            The quote the model claimed
                          </div>
                          <div className="mt-1 font-mono text-[11px] text-[var(--color-conflict)]/85">
                            {String(
                              (issue.payload as { fact?: { quote?: string } }).fact?.quote ?? '',
                            )}
                          </div>
                        </div>
                      )}
                  </div>
                ))}
              </div>
            </div>
          </Card>
        )
      })}

      <Card className="animate-rise border-[var(--color-line)] p-6">
        <div className="text-[10px] font-medium tracking-[0.16em] text-[var(--color-accent)] uppercase">
          What I would fix next
        </div>
        <ul className="mt-3 space-y-2.5 text-sm leading-relaxed text-[var(--color-muted)]">
          <li>
            <span className="text-[var(--color-fg)]">Re-ask instead of discarding.</span> A rejected
            fact is currently dropped. A cheap second pass could hand the model back its own
            proposal plus the page text and ask it to point at the exact span, recovering facts that
            are real but poorly quoted.
          </li>
          <li>
            <span className="text-[var(--color-fg)]">Table-aware extraction.</span> Most rejections
            trace to wide tables whose rows get re-flowed during text extraction. Feeding the model
            a structured table representation, and matching quotes against cells rather than lines,
            would remove the largest single failure mode.
          </li>
          <li>
            <span className="text-[var(--color-fg)]">Adjudicate low-confidence judgments.</span>{' '}
            Relationships below ~0.5 confidence are stored but never re-examined. A second judgment
            from a stronger model, or a self-consistency vote, would catch the borderline calls.
          </li>
          <li>
            <span className="text-[var(--color-fg)]">OCR fallback.</span> A scanned PDF currently
            yields nothing but a logged warning; routing zero-text documents through OCR would make
            the ingest path complete.
          </li>
        </ul>
      </Card>
    </div>
  )
}

export function ShowcaseView({ onOpenFact }: { onOpenFact: (id: string) => void }) {
  const { data, loading, error } = useAsync(() => api.showcase(), [])

  if (error) return <ErrorNote message={error} />

  if (loading && !data) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-72 w-full" />
        <Skeleton className="h-72 w-full" />
      </div>
    )
  }
  if (!data) return null

  const nothingYet =
    data.corroboration.length === 0 &&
    data.contradiction.length === 0 &&
    data.reconcilable.length === 0

  return (
    <div className="space-y-16">
      <SectionHeading
        eyebrow="Walkthrough"
        title="Four cases, from live data"
        description="Each case below is a query over whatever is currently ingested, ranked by how well an example demonstrates that case — not a fixed list. Ingest a different corpus and these sections fill with that corpus's examples."
      />

      {nothingYet && (
        <EmptyState
          title="Nothing to show yet"
          body="Ingest at least two documents that cover overlapping subject matter, then come back here."
        />
      )}

      <section className="space-y-6">
        <CaseHeader
          n={1}
          accent="var(--color-agree)"
          title="Corroborated across documents, stated differently"
          claim="The same underlying quantity asserted by two independent documents in different words, units or scale. Ranked to prefer pairs whose printed values differ — matching strings are a weak demonstration; matching magnitudes across different notations are the real thing."
        />
        {data.corroboration.length === 0 ? (
          <p className="pl-14 text-sm text-[var(--color-muted)]">No cross-document corroborations found yet.</p>
        ) : (
          <div className="space-y-5 lg:pl-14">
            {data.corroboration.slice(0, 2).map((relation, i) => (
              <RelationCard key={relation.id} relation={relation} index={i} onOpenFact={onOpenFact} />
            ))}
          </div>
        )}
      </section>

      <section className="space-y-6">
        <CaseHeader
          n={2}
          accent="var(--color-conflict)"
          title="A genuine or likely contradiction"
          claim="Same subject, same period, same scope, same unit basis — and values that cannot both be true. The judge is instructed to reach this label only after actively rejecting every reconciling explanation, which is why it is the rarest of the three."
        />
        {data.contradiction.length === 0 ? (
          <p className="pl-14 text-sm text-[var(--color-muted)]">
            No contradictions found in the current corpus. That is a legitimate outcome, not a
            failure — documents from the same publisher usually agree with themselves.
          </p>
        ) : (
          <div className="space-y-5 lg:pl-14">
            {data.contradiction.slice(0, 2).map((relation, i) => (
              <RelationCard key={relation.id} relation={relation} index={i} onOpenFact={onOpenFact} />
            ))}
          </div>
        )}
      </section>

      <section className="space-y-6">
        <CaseHeader
          n={3}
          accent="var(--color-context)"
          title="An apparent contradiction that context explains"
          claim="Two figures that look inconsistent until you account for a specific difference — a different period, a different unit or scale, a different scope, or an estimate compared against an actual. The judge must name which dimension resolves it and say what to hold constant."
        />
        {data.reconcilable.length === 0 ? (
          <p className="pl-14 text-sm text-[var(--color-muted)]">No context-reconcilable pairs found yet.</p>
        ) : (
          <div className="space-y-5 lg:pl-14">
            {data.reconcilable.slice(0, 3).map((relation, i) => (
              <RelationCard key={relation.id} relation={relation} index={i} onOpenFact={onOpenFact} />
            ))}
          </div>
        )}
      </section>

      <section className="space-y-6">
        <CaseHeader
          n={4}
          accent="var(--color-accent)"
          title="Where it fails, and what happens then"
          claim="Every extraction the pipeline rejected or repaired is kept and shown rather than silently swallowed. The headline number is the rejection rate: facts the model proposed whose quotes could not be found in the source, and which therefore never entered the store."
        />
        <div className="lg:pl-14">
          <FailureCase data={data.failures} />
        </div>
      </section>
    </div>
  )
}
