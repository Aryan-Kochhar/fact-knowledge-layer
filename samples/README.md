# Sample output

Committed so the project can be evaluated **without a Gemini API key** and
without waiting for an ingestion run.

## Open the real UI with this data

```bash
mkdir -p data && cp samples/facts.db data/facts.db
```

Then start the API and the frontend as described in the root README. Every view
works — facts, evidence, relationships, the four-case walkthrough — because all
of it is served from this database. No API calls are made unless you upload a
new PDF.

## Files

| File | What it holds |
|---|---|
| `facts.db` | The complete ingested corpus: 6 documents, 511 pages of source text, 3,460 facts, 1,377 relationships, and the full issue log |
| `showcase.json` | The four required cases exactly as the API serves them |
| `relations.json` | Every non-`unrelated` relationship, with both facts, both quotes, and the written reasoning |
| `facts.sample.json` | 400 numeric facts with their normalised comparison keys and evidence |
| `issues.json` | Every rejection, citation repair, flagged verdict and budget skip |
| `stats.json` | Corpus counts and LLM call telemetry |

## What was ingested

Both starter datasets, into one shared knowledge layer:

- `india-macroeconomy/` — Economic Survey 2024-25, RBI Annual Report 2024-25,
  IMF India 2025 Article IV
- `delhivery/` — 2022 prospectus, FY24 annual report, Q4 FY24 earnings deck

They are deliberately unrelated subject areas, which is part of the point: the
system had to keep them apart on its own rather than being told they were
separate corpora.

## Regenerating

```bash
cd backend && python scripts/export_samples.py
```
