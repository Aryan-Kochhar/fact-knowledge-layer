# Sample output

Committed so the project can be evaluated **without a Gemini API key** and
without waiting for an ingestion run.

## You do not need to do anything with this

`python run.py` copies `facts.db` into `data/` automatically on first run, so a
fresh clone opens with every view already populated. Facts, evidence,
relationships and the four-case walkthrough all come from this database, and no
API calls happen unless you upload a new PDF.

To start from an empty store instead: `python run.py --fresh`. To re-seed after
that, delete `data/facts.db` and run again.

The JSON files are here for reading directly, without running anything at all.

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
