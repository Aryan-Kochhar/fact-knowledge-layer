# Fact Knowledge Layer

Extracts checkable facts from PDFs, anchors every one to a verbatim quote in its
source page, and works out how facts from different documents relate: whether
they corroborate each other, genuinely conflict, or only appear to conflict
because they were measured over different periods, in different units, or at
different scopes.

Backend is FastAPI + SQLite, frontend is React + Tailwind, extraction and
reconciliation run on Google Gemini's free tier, and semantic similarity runs
locally on `sentence-transformers` so no tokens are spent finding candidates.

**On the six starter documents: 3,460 facts, every one with a located quote,
1,377 relationships, 279 API calls.**

---

## Setup and Run Instructions

### Option A: explore the results with no API key (fastest)

The full ingested corpus is committed at `samples/facts.db`, so the entire UI
works without a Gemini key and without spending any quota.

```bash
mkdir -p data && cp samples/facts.db data/facts.db
```

Then run steps 1, 4 and 5 below, skipping the key setup. Facts, evidence,
relationships and the four-case walkthrough all load from that database; no API
calls happen unless you upload a new PDF.

Raw JSON is in `samples/` as well. `samples/showcase.json` holds the four
required cases with their evidence and reasoning.

### Option B: run the full pipeline on your own PDFs

Requirements: Python 3.11+, Node 18+, and at least one Gemini API key from
[aistudio.google.com](https://aistudio.google.com/apikey). The free tier is
enough; the entire starter corpus cost 279 calls.

```bash
# 1. Backend dependencies
cd backend
python -m venv .venv
.venv/Scripts/activate            # Windows;  source .venv/bin/activate elsewhere
pip install --index-url https://download.pytorch.org/whl/cpu torch
pip install -r requirements.txt
```

```bash
# 2. Keys: copy the template, then fill in GEMINI_API_KEYS
cp .env.example .env
```

`backend/.env` needs one line to work. More keys means more throughput; they are
rotated automatically and rate-limited independently.

```
GEMINI_API_KEYS=key_one,key_two,key_three
```

```bash
# 3. Confirm the keys work before spending an ingest on them
python scripts/check_keys.py
```

```bash
# 4. Run the API
python -m uvicorn app.main:app --reload --port 8000
```

```bash
# 5. Run the UI, in a second terminal
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173> and drop PDFs onto the Ingest tab. The UI proxies
`/api` to the backend, so there is no base URL to configure.

To load a folder of PDFs from the command line instead:

```bash
python scripts/ingest.py "../Problem Statement/starter-datasets/india-macroeconomy"
```

### Useful scripts

| Command | What it does | API calls |
|---|---|---|
| `python scripts/check_env.py` | Show loaded config and key count (never prints key values) | 0 |
| `python scripts/check_keys.py` | Probe every key against the live API | 1 per key |
| `python scripts/plan_ingest.py <dir>` | Parse and chunk a corpus, report the call budget it needs | 0 |
| `python scripts/ingest.py <dir>` | Upload and follow ingestion to completion | many |
| `python scripts/diagnose.py` | Corpus-wide quality report | 0 |
| `python scripts/peek.py facts\|relations\|issues` | Readable sample of what was extracted | 0 |
| `python scripts/debug_reject.py [n]` | Show rejected extractions beside the source text | 0 |
| `python scripts/reverify.py` | Re-check every stored quote against a fresh parse | 0 |
| `python scripts/renormalize.py [--apply]` | Re-derive comparison keys after a parser fix | 0 |
| `python scripts/revalidate.py [--apply]` | Replay the judgment consistency check | 0 |
| `python scripts/export_samples.py` | Regenerate `samples/` | 0 |
| `python -m pytest` | 140 unit tests | 0 |

---

## Video Demo

**[Demo video (3 minutes or less)](ADD_YOUR_LINK_HERE)**

Shows a PDF being ingested end to end, then each of the four required cases with
its source evidence and the system's reasoning.

---

## Results on the starter corpus

Both starter datasets were ingested into one shared knowledge layer. They cover
unrelated subject areas on purpose: the system had to keep them apart on its own
rather than being told they were separate corpora.

| | |
|---|---|
| Documents / pages | 6 / 511 |
| Facts stored | **3,460** |
| Facts with a located quote | **3,460 (100%)**: 3,413 on the cited page, 47 relocated |
| Facts rejected for unverifiable evidence | 76 (**2.15%** of everything proposed) |
| Relationships | **1,377**: 80 corroborate, 6 contradict, 834 reconcilable-by-context, 457 unrelated |
| Verdicts flagged as inconsistent | 18 (initially 50, see below) |
| LLM calls | **279** (155 extraction, 118 judgment, 6 profile) |
| Wall clock | 37 minutes on 5 free-tier keys |

Normalisation coverage: 98.6% of facts got a parsed numeric value, 92.4% a
canonical period, 100% a measurement basis.

The relation mix is worth reading carefully. Contradictions are rare (6) because
the bar is deliberately high: a difference in numbers is not a contradiction
until period, unit, scale, scope and basis have all been ruled out. Most
apparent conflicts turn out to be reconcilable, and 698 of the 834 reconcilable
pairs differ on time alone.

### The four required cases

All four are computed live from whatever is currently ingested, not hardcoded.
They are the Walkthrough tab in the UI, and `samples/showcase.json` on disk.

**1. Corroborated across documents, stated differently.** Delhivery's FY24
EBITDA appears as `₹127 Cr` in the Q4 earnings deck and `₹1,266 million` in the
annual report. Different documents, different scales, same quantity. The
normaliser converts both to 1.27e9 INR; the judge explains the conversion.

**2. A genuine or likely contradiction.** Growth in emerging markets for CY2025
is stated as `4.3%` in Delhivery's annual report and `3.7%` in the RBI annual
report. Both are projections, for the same period, about the same subject. They
cannot both be right, and they trace to different IMF forecast vintages.

**3. An apparent contradiction explained by context.** The Economic Survey
reports Kharif food-grain production growth as both `8.2%` and `5.7%` for
2024-25. Not a conflict: one is measured against the five-year average, the
other against the previous year. Labelled `reconcilable_context`, dimension
`scope`. A second example differs on `basis`: an RBI inflation projection for
FY25 appears as both `4.5%` and `4.8%` because the December 2024 MPC revised it.

**4. An extraction or reasoning failure.** Three distinct kinds, all surfaced in
the UI rather than swallowed: 76 facts rejected because their quote could not be
found in the source, 47 citations automatically repaired, and 18 verdicts
flagged where the model's label contradicted the deterministic analysis. The
"Things that went wrong" section below describes each and what fixed it.

---

## Approach

### Pipeline

```
PDF
 |
 |--> parse        per-page text in true reading order, tables rendered inline,
 |                 printed page labels recovered
 |--> profile      ONE call per document: infer the entity, reporting period,
 |                 currency and scale that the rest of the document assumes
 |--> chunk        page-aware, ~15k chars, every page stamped [[page N]]
 |--> extract      one call per chunk -> atomic facts with verbatim quotes
 |--> VERIFY       locate each quote in the real page text; reject what is absent
 |--> normalise    deterministic: value+unit -> number, period -> canonical key
 |--> embed        local MiniLM over metric identity (not values, not periods)
 |--> candidates   vector search + exact metric blocking, ranked by expected value
 |--> analyse      deterministic: unit conversion, numeric delta, period/scope diff
 `--> judge        batched LLM call -> corroborates / contradicts /
                   reconcilable_context / unrelated, with written reasoning,
                   then cross-checked against the deterministic analysis
```

### Module map

| Path | Responsibility |
|---|---|
| `app/config.py` | Every tunable in one place, env-overridable |
| `app/db.py` | SQLite access, migrations, issue and call logging |
| `app/schema.sql` | Storage model, with the reasoning for each table |
| `app/llm/keypool.py` | Key rotation, per-key rate limiting, 429 cooldowns |
| `app/llm/gemini.py` | REST client, retries, JSON repair, model circuit breaker |
| `app/llm/prompts.py` | The three prompts: profile, extract, judge |
| `app/pipeline/pdf_parse.py` | PDF to page text, column handling, page labels |
| `app/pipeline/chunking.py` | Page-aware chunking with page markers |
| `app/pipeline/extract.py` | Chunk to verified, normalised facts |
| `app/pipeline/verify.py` | Quote matching: the evidence guarantee |
| `app/pipeline/normalize.py` | Units, scales, periods, metric identity |
| `app/pipeline/embeddings.py` | Local encoder and in-memory vector index |
| `app/pipeline/linking.py` | Candidates, analysis, judgment, validation |
| `app/pipeline/ingest.py` | Orchestration, concurrency, progress, failure isolation |
| `app/pipeline/showcase.py` | The four demonstration cases, as live queries |
| `app/api/routes.py` | HTTP API |

The frontend mirrors this: `views/ShowcaseView.tsx` is the four-case
walkthrough, `views/RelationsView.tsx` the relationship browser,
`views/FactsView.tsx` the searchable fact list with an evidence drawer that
highlights the quote inside its source page, `views/IngestView.tsx` upload plus
live job progress and key-pool telemetry.

### The ideas that matter

**1. A citation is only a citation if it survives a click.** Every fact carries a
quote and a page. Before storage, the quote is searched for in the actual text
of the page it claims. Matching tolerates the whitespace and punctuation damage
that PDF extraction always produces, and nothing else. Three outcomes:
`verified` (found on the claimed page), `relocated` (found on a different page
in the same chunk, citation corrected and logged), `unverified` (**the fact is
rejected** and written to the issues table). A model that paraphrases, merges
two sentences, or invents a number does not clear this bar. There are tests
asserting that a hallucinated quote and a paraphrase both fail.

**2. Deterministic normalisation, not prompt arithmetic.** The model reads the
document; Python does the maths. `8,142` + `INR crore` becomes `81.42e9 INR`.
`FY24`, `FY 2023-24` and `2023-24` all become `FY2024`. `25 bps` becomes
`0.25 pp`. `(1,008)` becomes `-1008`. This is auditable, free, and
deterministic, and it hands the judge a finished comparison rather than asking a
language model to do arithmetic.

**3. Embed the metric, not the measurement.** The embedded text is subject,
predicate and scope qualifiers, with the value and period deliberately excluded.
This is counter-intuitive and load-bearing: if the value were embedded, two
documents that disagree would be pushed *apart* in vector space and the
disagreement would never be found. Excluding the period has a bonus: `revenue
FY2024` and `revenue FY2023` embed identically, become a candidate pair, and get
labelled reconcilable-by-time. Period markers are also stripped from metric
names, because models write `FY24 EBITDA` in one document and `EBITDA` in
another.

**4. Judgment is the scarce resource, so it is rationed by expected value.**
Candidate pairs grow roughly quadratically. On the largest document, 1,072
candidates were generated, 311 rejected by rule, the top 420 by priority judged,
and 341 skipped. `linking.priority` ranks same-period value gaps (the
contradiction signature) highest, near-misses on the same period almost as high,
then scale differences, then agreement stated in different words or units;
identically-worded agreement ranks lowest, and unverified evidence is penalised.
The skipped count is recorded as an issue: a pair that was never judged is not a
pair that was judged unrelated.

**5. The model's verdict is checked against the arithmetic.** The judge sees each
fact's evidence quote, and quotes routinely mention other years and figures. It
once called a 6.6%/2023 fact and a 5.7%/2024 fact identical because both quotes
contained "5.7 per cent in 2024". The deterministic layer only ever sees
normalised values and periods, so it cannot be misled that way. Every verdict is
cross-checked (`linking.validate_judgment`); a corroboration across different
periods, or a contradiction between an estimate and an actual, is inconsistent
by construction. Such verdicts are kept but have their confidence halved, are
marked `llm-flagged`, and are logged.

**6. Incremental by construction.** Ingesting document N never re-parses,
re-extracts or re-embeds documents 1..N-1. The only new work is this document's
extraction plus the cross-document comparisons it newly makes possible. Deleting
a document cascades to its facts, its embeddings and exactly the relations that
referenced them.

### Why the similarity threshold is 0.58

From the judged pairs, grouped by the cosine score that made them candidates:

| Similarity band | Pairs | Judged `unrelated` |
|---|---|---|
| 0.95 - 1.00 | 825 | 18.3% |
| 0.85 - 0.95 | 197 | 24.9% |
| 0.75 - 0.85 | 163 | 56.4% |
| 0.65 - 0.75 | 147 | 83.7% |
| 0.58 - 0.65 | 45 | 93.3% |

A clean monotonic curve, so the threshold is a data-backed dial rather than a
guess: raising it saves budget and costs recall. Note that even at 0.95+, 18% are
unrelated. That is the direct consequence of embedding metric identity while
excluding values and periods, and it means the judge is doing real work.

### Generalising to unseen PDFs

The following appear nowhere in the codebase: document filenames, titles or
publishers; metric names, expected facts, or per-document schemas; layout rules
keyed to a particular report.

What is imposed instead is the universal shape of a measurement (who, what, how
much, when, under what scope) plus a free-form `qualifiers` list carrying
whatever document-specific nuance exists, in the document's own words.
`fact_type` and `qualifiers` are model-authored and open-ended, which is how the
schema evolves as new kinds of facts appear.

Everything else is driven by structure, not content: character counts drive
chunking, block geometry drives column detection, regular expressions over units
and periods drive normalisation, and the four demonstration cases are queries
over live data. Ingest a corpus about shipping or pharmaceuticals and the same
queries surface that corpus's examples.

### Design decisions and trade-offs

**SQLite over Postgres.** Single-node system, working set of thousands of rows.
SQLite gives transactions, joins, foreign-key cascades and a single-file artifact
that can be committed and handed to a reviewer, at zero operational cost. The
whole corpus is 12.3 MB including 511 pages of source text and 5.3 MB of
embeddings. It would need replacing for concurrent writers or a corpus large
enough that a brute-force vector scan stops being instant.

**Brute-force vectors over a vector database.** Vectors are L2-normalised, so
cosine similarity is one NumPy dot product over 3,460 x 384. That is
sub-millisecond, faster than the network round trip to any vector service, with
none of the operational surface. The index is a class with `load`/`add`/`search`,
so swapping it is contained.

**A small embedding model.** Facts are one-line claims, not paragraphs, and
similarity is used only for *recall*: deciding which pairs are worth looking at.
The decision is made downstream by rules plus the judge. A larger encoder would
be optimising the wrong stage.

**Raw REST over the `google-genai` SDK.** The SDK binds an API key at client
construction, so a rotating pool means rebuilding clients per call and losing
connection pooling. Talking to the endpoint directly gives exact control over
which key is attached to which request, and access to the `RetryInfo` payload in
429 responses that drives key cooldowns.

**Three model tiers, matched to three difficulties.** Extraction is high-volume
and mechanical, so it runs on a fast non-thinking model at roughly 1.5s a call,
and it is about 90% of all calls. Routine judgment runs on a reliable lite model.
A thinking model is reserved for pairs where the verdict is hardest and most
consequential: same period, values that disagree, nothing obvious to explain it.

The tiering comes from measurement, not preference. On a five-way burst test the
newest flash model returned 429/503 on 5 of 5 calls; the lite model answered 5
of 5 but got a basis-difference case wrong once in five; `gemini-3.5-flash`
answered 5 of 5 with the same correct verdict every time. Its free-tier *daily*
allowance, however, is small enough that it cannot judge a whole corpus, so it is
spent only where it changes the answer.

**A circuit breaker at the model level, not just the key.** Two different failure
modes share HTTP 429. A per-minute rate limit is the key's problem and clears in
seconds. An exhausted daily allowance is the *model's* problem, and parking a key
for it would wrongly shrink the pool for every other model. This was measured,
not hypothesised: during the final run the thinking model's daily quota ran out,
and the telemetry shows judgment calls averaging 4.01 attempts and 64.5 seconds
while extraction averaged 1.01 attempts. `gemini._ModelHealth` now tracks quota
exhaustion per model; once two distinct keys have been refused, the model is
skipped until its cooldown expires.

**~15k character chunks.** The context window is far larger than anything sent,
so the real trade-off is recall, not capacity: a bigger chunk costs fewer calls
but asks the model to notice every fact across more text, and one failure loses
more work. Citation precision is unaffected either way, because page markers
travel inside the chunk and every quote is verified afterwards. This is the main
quota dial (`CHUNK_TARGET_CHARS`).

**PyMuPDF over pdfplumber for the main text pass.** Roughly an order of magnitude
faster on 100-page filings. Its reading-order default had to be replaced, though;
see below.

**Batched judgment.** Twelve pairs per call, so a thousand candidate pairs cost
84 calls instead of a thousand.

### Things that went wrong, and what fixed them

Each was found by measurement during the build, and each is locked down by a
regression test.

**Two-column layouts were shredding sentences.** PyMuPDF's reading-order sort
interleaves the columns of a two-column page line by line, so a sentence in the
left column is stored with fragments of the right column wedged into it. The
model read those pages correctly and quoted them correctly, and the verifier then
rejected **394 correct facts** because the quote was not contiguous in the stored
text. Institutional reports are overwhelmingly two-column, so this decided
whether those documents were usable at all. Replaced with block-geometry column
detection. Validated against ground truth by re-checking every already-stored
quote: **350 of 394 rejected facts recovered, 18 of 1,352 verified facts
regressed.** Across the full corpus, rejections fell from 394 to 76.

**Wide landscape pages were almost entirely whitespace.** One financial-statement
page extracted to 145,352 characters, of which about 6,600 were content; the rest
was padding positioning columns. That page alone would have cost sixteen
extraction calls. Capping space runs cut the corpus from 751 to 456 chunks, a 39%
reduction, with no digits lost.

**`March 31, 2024` was parsed as the year 2031.** The month-year rule matched
before the full-date rule and read the day number as a two-digit year. This
mis-dated 447 facts, 14% of the corpus, because "as at March 31, 2024" is how
every Indian balance-sheet line states its date.

**Table columns extracted without their discriminator.** Two cells from the same
table row arrived with identical subject, predicate and period but different
values, and nothing to tell them apart. The prompt now requires the column header
to go into `qualifiers` whenever a row carries multiple value columns.

**The judge was distracted by other numbers in the evidence quote.** Described in
idea 5 above; fixed by prompt hardening plus the deterministic cross-check.

**Then the consistency check caught a bug in the checker, not the model.** Of the
50 verdicts it flagged, 46 were the model being right and the normaliser being
wrong, in two ways. First, `"for the year ended March 31, 2024"` denotes a whole
fiscal year, but was being collapsed to an instant, which made a year's EBITDA
incomparable with the same figure written `FY24`; 158 facts were affected.
Second, financial-statement tables are *headed* "for the year ended..." while the
model timestamps individual rows with the bare date, so an instant legitimately
sits on a fiscal-year boundary. A third comparison state, `aligned`, now covers
that, kept distinct from `same` because a stock measured *at* a date and a flow
measured *over* the year ending on it genuinely are different things.

**Flagged verdicts fell from 50 to 18 with zero API calls**, via
`scripts/renormalize.py --apply` (re-derive 3,460 facts' comparison keys) then
`scripts/revalidate.py --apply` (replay the consistency check over 1,377 stored
relations). That is the separation of extraction from normalisation paying for
itself: the model's raw output never had to be re-requested. Most of the 18 that
remain are correct flags. One example: the judge called RBI's policy rate of 5.5%
(June 2025) and 6.0% (April 2025) a contradiction, when it is a rate cut between
two months.

### Brownie points

All four suggested extensions are implemented.

- **Large PDFs without performance issues.** Page-aware chunking, bounded
  concurrency, per-chunk failure isolation, and a whitespace fix that cut the
  corpus by 39%. The largest document is 100 pages and 1,233 facts.
- **Many PDFs in the same knowledge layer.** Six documents from two unrelated
  domains share one store; 1,159 of the 1,377 relationships are cross-document.
- **A schema that evolves dynamically.** Facts carry model-authored `fact_type`
  and an open-ended `qualifiers` list rather than a fixed column set, so a new
  kind of fact needs no migration.
- **Incremental ingestion.** Guaranteed by construction and by foreign-key
  cascades, not by convention.

### AI tools used

- **Claude Code (Opus 5)** for building the system: architecture, implementation,
  the test suite, and the debugging sessions that produced the fixes above.
- **Google Gemini** at runtime: `gemini-3.5-flash-lite` for extraction and
  document profiling, `gemini-3.1-flash-lite` for routine judgment,
  `gemini-3.5-flash` for escalated contradiction candidates.
- **sentence-transformers (`all-MiniLM-L6-v2`)** locally for candidate retrieval,
  chosen so that finding candidate pairs costs no tokens.

---

## Limitations and Next Steps

**Scanned PDFs are not handled.** A document with no text layer yields nothing
but a logged `no_text_layer` warning. An OCR pass would complete the ingest path.

**Fiscal-year convention is assumed.** April to March, correct for these
documents and wrong elsewhere. It is a single constant
(`normalize.FY_START_MONTH`) rather than something spread through the code, but
it should be inferred per document. The profile pass already reads the front
matter and could determine it.

**Some period phrasings still fall through.** Abbreviated forms with an
apostrophe (`Mar '23`) do not parse, leaving those facts without a period key. An
instant falling inside a stated month (`@2024-09-13` against `2024-09`) is
treated as a different period rather than an aligned one, which flags a small
number of correct corroborations.

**Rejected facts are discarded, not repaired.** A fact whose quote cannot be
located is dropped. A cheap second pass could hand the model back its own
proposal plus the page text and ask it to point at the exact span, recovering
facts that are real but poorly quoted.

**Table extraction is line-based, not cell-based.** Most remaining rejections
trace to wide tables whose rows are re-flowed during extraction. Feeding the
model a structured table representation, and matching quotes against cells rather
than lines, would remove the largest surviving failure mode.

**The relationship graph is a ranked subset, not exhaustive.** Bounded by
`MAX_PAIRS_PER_INGEST`. The count of skipped pairs is recorded, but a pair that
was never judged is not a pair that was judged unrelated.

**Flagged verdicts are not re-adjudicated.** Inconsistent judgments have their
confidence halved and are logged, but nothing re-examines them. A second opinion
from a stronger model, or a self-consistency vote, would close the loop.

**Confidence scores are model self-reports.** Useful for ranking; not calibrated
probabilities.

**No authentication.** It binds to localhost and is a local tool. Anything
multi-user needs auth, per-user quotas and upload limits.

**What I would build next, in order:** cell-level table extraction (biggest
quality win), the re-ask pass for rejected quotes (cheap recall win), per-document
fiscal-year inference, then OCR.

---

## Additional Notes

### Testing

```bash
cd backend && python -m pytest
```

140 tests, covering the logic where mistakes are silent and expensive: unit and
scale conversion, fiscal-versus-calendar period parsing, quote matching against
deliberately hallucinated and paraphrased quotes, truncated-JSON recovery, metric
identity across period labels, the candidate priority ranking, escalation
routing, the model circuit breaker, and the judgment consistency guard. Cases
taken from real failures on the starter corpus are marked as such in the test
docstrings.

`scripts/reverify.py` is the integration-level check: it re-parses every ingested
PDF and re-tests every stored quote, so a change to PDF handling can be measured
against ground truth rather than guessed at.

### Handling of failure, by design

Everything rejected, repaired, flagged or skipped is a row in the `issues` table
and visible in the UI:

| Kind | Meaning |
|---|---|
| `quote_not_found` | Evidence could not be located; fact rejected |
| `quote_relocated` | Quote found on a different page; citation corrected |
| `judgment_inconsistent` | Model verdict contradicted the deterministic analysis |
| `pairs_over_budget` | Candidate pairs skipped to stay within quota |
| `incomplete_fact` | Required field missing; fact dropped |
| `chunk_failed` / `judge_failed` | All retries exhausted for one unit of work |
| `no_text_layer` | PDF has no extractable text |
| `profile_failed` | Document context could not be inferred |

### API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Key pool status, models, daily budget remaining |
| `GET` | `/api/keys` | Per-key load, cooldowns, models in cooldown (masked) |
| `GET` | `/api/stats` | Corpus counts, relation mix, call telemetry |
| `POST` | `/api/documents` | Upload PDFs; returns job ids |
| `GET` | `/api/documents` | List documents with fact and issue counts |
| `GET` | `/api/documents/{id}` | Detail, including the inferred document profile |
| `DELETE` | `/api/documents/{id}` | Remove a document and everything derived from it |
| `GET` | `/api/documents/{id}/pages/{n}` | Raw page text, for the evidence viewer |
| `GET` | `/api/jobs/{id}` | Ingestion progress |
| `GET` | `/api/facts` | Search and filter facts |
| `GET` | `/api/facts/{id}` | Fact with evidence, page context and relationships |
| `GET` | `/api/relations` | Filter relationships by type and confidence |
| `GET` | `/api/showcase` | The four demonstration cases |
| `GET` | `/api/issues` | Everything rejected, repaired or flagged |

Interactive docs at <http://localhost:8000/docs>.

### Credentials

`backend/.env` is gitignored and no key is committed. `scripts/check_env.py` and
`scripts/check_keys.py` mask key values in all output, and `scripts/set_env.py`
refuses to write any variable whose name looks like a secret.

### A note on the corpus

The assignment supplied two starter datasets of three PDFs each. Both were
ingested into one knowledge layer rather than kept separate, because a system
that only works when you tell it which documents belong together is not really
doing the job. 1,159 of 1,377 relationships are cross-document, and the system
correctly labels the Delhivery/macroeconomy cross-pairs as unrelated except where
they genuinely overlap, such as both citing IMF world growth projections.
