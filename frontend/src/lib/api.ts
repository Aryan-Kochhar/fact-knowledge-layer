/** Typed client for the Fact Knowledge Layer API. */

export type Verification = 'verified' | 'relocated' | 'unverified'
export type RelationType =
  | 'corroborates'
  | 'contradicts'
  | 'reconcilable_context'
  | 'unrelated'

export interface Fact {
  id: string
  doc_id: string
  chunk_id: string | null
  subject: string
  predicate: string
  value_raw: string
  unit_raw: string | null
  time_scope_raw: string | null
  qualifiers: string[]
  fact_type: string | null
  confidence: number
  value_num: number | null
  value_unit: string | null
  value_dim: string | null
  period_key: string | null
  period_basis: string | null
  metric_key: string | null
  claim_text: string
  quote: string
  page_index: number | null
  printed_page: string | null
  verification: Verification
  verify_score: number
  quote_start: number | null
  quote_end: number | null
  filename: string
  doc_title: string | null
  relation_counts?: Partial<Record<RelationType, number>>
}

export interface Evidence {
  page_index: number | null
  printed_page: string | null
  quote: string
  quote_start: number | null
  quote_end: number | null
  verification: Verification
  verify_score: number
  page_text: string | null
}

export interface Relation {
  id: string
  relation: RelationType
  dimension: 'time' | 'unit' | 'scope' | 'basis' | 'none'
  reasoning: string
  reconciliation: string | null
  confidence: number
  similarity: number
  cross_doc: boolean
  decided_by: string
  created_at?: string
  a: Fact
  b: Fact
}

export interface FactDetail extends Fact {
  evidence: Evidence
  relations: Relation[]
}

export interface DocumentRow {
  id: string
  filename: string
  title: string | null
  page_count: number
  byte_size: number
  status: 'pending' | 'ready' | 'failed'
  error: string | null
  created_at: string
  completed_at: string | null
  fact_count: number
  issue_count: number
  latest_job: string | null
}

/** Context inferred once from a document's opening pages, then applied to every
 *  fact extracted from it. Free-form: the model fills in what it can find. */
export interface DocumentProfile {
  title?: string
  publisher?: string
  doc_type?: string
  primary_entity?: string
  reporting_period?: string
  as_of_date?: string
  currency?: string
  scale_convention?: string
  accounting_scope?: string
  notes?: string
}

export interface DocumentDetail extends DocumentRow {
  profile: DocumentProfile | string | null
  chunks: { status: string; n: number }[]
  issues: { kind: string; n: number }[]
  pages_with_labels: number
}

export interface Job {
  id: string
  doc_id: string | null
  status: 'queued' | 'running' | 'done' | 'failed'
  stage: string
  done: number
  total: number
  message: string | null
  error: string | null
  filename?: string
}

export interface Issue {
  id: string
  doc_id: string | null
  chunk_id: string | null
  kind: string
  severity: string
  detail: string
  payload: unknown
  created_at: string
  filename?: string
}

export interface Stats {
  documents: number
  pages: number
  facts: number
  facts_with_numbers: number
  relations: number
  relation_counts: Partial<Record<RelationType, number>>
  verification: Partial<Record<Verification, number>>
  issues: number
  llm_calls: { purpose: string; status: string; n: number; avg_ms: number }[]
  top_periods: { period_key: string; n: number }[]
}

export interface KeyStatus {
  index: number
  key: string
  disabled: boolean
  disabled_reason: string | null
  in_flight_window: number
  rpm_limit: number
  cooldown_seconds: number
  total_calls: number
  total_errors: number
  rate_limit_hits: number
}

export interface Health {
  status: string
  keys_configured: number
  keys_live: number
  model: string
  fallback_model: string
  embedding_model: string
  documents: number
  facts: number
}

export interface Showcase {
  corroboration: Relation[]
  contradiction: Relation[]
  reconcilable: Relation[]
  failures: {
    counts: { kind: string; severity: string; n: number }[]
    examples: Issue[]
    summary: {
      facts_stored: number
      facts_rejected_unverifiable_quote: number
      citations_auto_corrected: number
      flagged_judgments: number
      rejection_rate: number
    }
  }
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, init)
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      detail = body.detail ?? detail
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(detail, response.status)
  }
  return (await response.json()) as T
}

function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '' && value !== false) {
      search.set(key, String(value))
    }
  }
  const out = search.toString()
  return out ? `?${out}` : ''
}

export const api = {
  health: () => request<Health>('/health'),
  stats: () => request<Stats>('/stats'),
  keys: () => request<{ per_key_rpm: number; keys: KeyStatus[] }>('/keys'),

  documents: () => request<DocumentRow[]>('/documents'),
  document: (id: string) => request<DocumentDetail>(`/documents/${id}`),
  deleteDocument: (id: string) =>
    request<{ deleted: string; facts_removed: number; relations_removed: number }>(
      `/documents/${id}`,
      { method: 'DELETE' },
    ),

  upload: (files: File[]) => {
    const form = new FormData()
    files.forEach((file) => form.append('files', file))
    return request<{
      results: { filename: string; doc_id?: string; job_id?: string; status: string; reason?: string }[]
    }>('/documents', { method: 'POST', body: form })
  },

  job: (id: string) => request<Job>(`/jobs/${id}`),
  jobs: () => request<Job[]>('/jobs'),

  facts: (params: {
    doc_id?: string
    q?: string
    period?: string
    verification?: string
    numeric_only?: boolean
    linked_only?: boolean
    limit?: number
    offset?: number
  }) =>
    request<{ total: number; limit: number; offset: number; facts: Fact[] }>(
      `/facts${qs(params)}`,
    ),
  fact: (id: string) => request<FactDetail>(`/facts/${id}`),

  relations: (params: {
    relation?: string
    doc_id?: string
    cross_doc_only?: boolean
    min_confidence?: number
    limit?: number
    offset?: number
  }) =>
    request<{ total: number; limit: number; offset: number; relations: Relation[] }>(
      `/relations${qs(params)}`,
    ),

  showcase: () => request<Showcase>('/showcase'),
  issues: (params: { doc_id?: string; kind?: string; limit?: number } = {}) =>
    request<Issue[]>(`/issues${qs(params)}`),
}
