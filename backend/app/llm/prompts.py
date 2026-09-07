"""Prompt templates.

Three prompts, each doing one job:

1. PROFILE  - read the front matter of a document once and infer the context
              that the rest of the document leaves implicit (who it is about,
              what period it covers, what currency and scale its numbers use).
              That profile is injected into every extraction call, which is what
              lets a fact from page 60 carry a period even though page 60 never
              restates it.

2. EXTRACT  - turn one chunk into fact records with verbatim evidence.

3. JUDGE    - decide how two facts relate, in batches.

None of these mention the starter documents, their layout, or their metrics.
The only structure imposed is the universal shape of a measurement
(who / what / how much / when / under what scope), plus a free-form
`qualifiers` list that carries whatever the document-specific nuance happens
to be.
"""

from __future__ import annotations

import json
from typing import Any

from ..security import UNTRUSTED_PREAMBLE, fence, new_fence

# --------------------------------------------------------------------------
# 1. document profile
# --------------------------------------------------------------------------

PROFILE_SYSTEM = """You are a document analyst. You read the opening pages of a \
document and infer the reporting context that the rest of the document will \
take for granted. Reply with JSON only."""

PROFILE_PROMPT = """Below are the first pages of a document (filename: {filename}).

Infer the document's reporting context. Respond with a single JSON object:

{{
  "title": "full document title as printed, or best inference",
  "publisher": "organisation that published it",
  "doc_type": "e.g. annual report, prospectus, earnings presentation, staff report, statistical survey",
  "primary_entity": "the main entity the document reports on (company, country, institution)",
  "reporting_period": "the period the document primarily covers, as printed (e.g. 'FY2023-24', 'calendar 2024', 'Q4 FY24'); null if none",
  "as_of_date": "the data cut-off or publication date if stated, else null",
  "currency": "default currency of monetary figures (ISO code) or null",
  "scale_convention": "the default numeric scale, e.g. 'INR crore', 'USD billion', 'percent'; null if mixed",
  "accounting_scope": "e.g. 'consolidated', 'standalone', 'general government', null if not applicable",
  "notes": "one sentence on anything a reader must know to interpret figures correctly"
}}

Use null for anything you cannot determine. Do not guess an entity or period \
that is not supported by the text.

{untrusted}

## Document opening

{text}"""


def profile_prompt(filename: str, text: str) -> str:
    token = new_fence()
    return PROFILE_PROMPT.format(
        filename=filename,
        untrusted=UNTRUSTED_PREAMBLE,
        text=fence(text, token),
    )


# --------------------------------------------------------------------------
# 2. fact extraction
# --------------------------------------------------------------------------

EXTRACT_SYSTEM = """You are a fact extraction engine for a cross-document \
reconciliation system. You convert document text into atomic, checkable facts, \
each anchored to a verbatim quote from the source. You never invent numbers and \
you never paraphrase a quote. Reply with JSON only."""

EXTRACT_PROMPT = """## Document context

{profile_block}

Treat this context as the default for facts that do not restate it. If a passage \
states its own period, scope or currency, the passage always wins.

## Your task

Extract every atomic, checkable fact from the text below.

A fact is worth extracting if a reader could later ask "is this still true, and \
does another document agree?". That means:

- Quantities and measurements: revenue, volumes, headcount, rates, ratios, \
  percentages, capacity, market share, prices, index levels.
- Dated events and states: incorporation dates, listing dates, facility counts, \
  ownership stakes, credit ratings, policy rates, thresholds.
- Explicit projections, targets and estimates - but mark them as such in \
  `basis`, never as actuals.

Do NOT extract:
- Headings, page furniture, table-of-contents lines, legal boilerplate.
- Opinions, aspirations and marketing language with no measurable content.
- A number you cannot attach to a subject and a meaning.

### Rules that matter most

1. **Quote verbatim.** `quote` must be a contiguous character-for-character span \
   copied from the text below - long enough to stand alone as evidence \
   (roughly 8-40 words), short enough to be about this one fact. Never edit, \
   reflow, summarise or reconstruct it. If a fact comes from a table, quote the \
   table row exactly as it appears, including its label and figure.
2. **Cite the right page.** Text is annotated with `[[page N]]` markers. Set \
   `page` to the N of the marker the quote sits under.
3. **Resolve the subject.** Replace "the Company", "the Bank", "it" with the \
   entity's actual name using the document context. A fact must be readable on \
   its own, outside this document.
   **Never put the period inside `subject` or `predicate`.** Write \
   `predicate: "EBITDA"` with `time_scope: "FY24"`, not `predicate: "FY24 \
   EBITDA"`. The same metric must be named identically whichever year it \
   describes, otherwise it cannot be matched across documents.
4. **Separate the number from its units.** `value` holds the number or short \
   phrase as printed; `unit` holds the unit and scale exactly as the document \
   expresses it ("INR crore", "%", "per cent", "USD billion", "million tonnes", \
   "days"). If the scale is only stated in a table header or the document \
   context, still put it in `unit`.
5. **Always fill `time_scope`** when the text or context implies one. Copy the \
   period as printed ("FY2024", "Q4 FY24", "as at March 31, 2024", "2023-24", \
   "April-December 2024"). Use null only when the fact is genuinely timeless.
6. **Record scope in `qualifiers`.** These are the discriminators that stop two \
   figures from being wrongly compared later. Include any that apply, in the \
   document's own words: consolidated / standalone, the business segment, the \
   geography, gross vs net, reported vs adjusted, seasonally adjusted, \
   provisional / revised, the counting basis. Free-form list - use whatever \
   words the document uses. Empty list if none apply.
   **Tables especially.** When a row carries several value columns, each cell is \
   a separate fact and the column's own header is what tells them apart - put \
   that header in `qualifiers`. Two facts must never end up with the same \
   subject, predicate and period but different values and nothing to \
   distinguish them; if that is about to happen, you have left the \
   discriminator out. Quote enough of the table for the column headers to be \
   visible in the evidence.
7. **`basis`** is one of: "actual", "estimate", "projection", "target", \
   "unknown". A figure a body forecasts for a future period is a projection even \
   when stated confidently.
8. **`confidence`** (0-1) is your confidence that the fact is correctly read from \
   the text - not how important it is. Lower it when the text is a fragmented \
   table, when the units are ambiguous, or when you had to infer the subject.

Extract at most {max_facts} facts from this text. If there are more, keep the \
most substantive and comparable ones.

## Output

A JSON array. Each element:

{{
  "subject": "the entity or thing being measured, self-contained",
  "predicate": "what is being measured about it, in plain words",
  "value": "the figure or state as printed",
  "unit": "unit and scale as printed, or null",
  "time_scope": "period as printed, or null",
  "qualifiers": ["scope discriminators", "..."],
  "basis": "actual | estimate | projection | target | unknown",
  "fact_type": "numeric | monetary | percentage | date | count | categorical",
  "quote": "verbatim span copied from the text",
  "page": 12,
  "confidence": 0.0
}}

Return `[]` if the text contains no extractable facts. Output the array and \
nothing else.

{untrusted}

## Document content

{text}"""


def _profile_block(profile: dict[str, Any] | None) -> str:
    if not profile:
        return "(no document context available - rely only on the text below)"
    keep = [
        ("Title", profile.get("title")),
        ("Publisher", profile.get("publisher")),
        ("Document type", profile.get("doc_type")),
        ("Primary entity", profile.get("primary_entity")),
        ("Reporting period", profile.get("reporting_period")),
        ("As-of date", profile.get("as_of_date")),
        ("Default currency", profile.get("currency")),
        ("Default scale", profile.get("scale_convention")),
        ("Accounting scope", profile.get("accounting_scope")),
        ("Notes", profile.get("notes")),
    ]
    lines = [f"- {label}: {value}" for label, value in keep if value]
    return "\n".join(lines) if lines else "(no document context available)"


def extract_prompt(text: str, profile: dict[str, Any] | None, max_facts: int = 40) -> str:
    # The document is fenced with a per-request random token. A fixed delimiter
    # would let a PDF containing that delimiter end the data block early and have
    # everything after it read as instructions.
    token = new_fence()
    return EXTRACT_PROMPT.format(
        profile_block=_profile_block(profile),
        untrusted=UNTRUSTED_PREAMBLE,
        text=fence(text, token),
        max_facts=max_facts,
    )


# --------------------------------------------------------------------------
# 3. relationship judgment
# --------------------------------------------------------------------------

JUDGE_SYSTEM = """You are a reconciliation analyst. You compare pairs of facts \
drawn from different documents and decide whether they agree, genuinely \
conflict, or only appear to conflict because they are measured differently. \
You are conservative: a difference in numbers is not a contradiction until you \
have ruled out period, unit, scale, scope and basis. Reply with JSON only."""

JUDGE_PROMPT = """Below are {n} pairs of facts. For each pair, decide how fact A \
relates to fact B.

## Labels

- **`corroborates`** - both facts assert the same thing about the same subject, \
  the same period and the same scope, and their values agree (identical, or \
  equal once you convert units/scale, or one is a rounded form of the other). \
  Stating it differently, or in different units, still counts as corroboration.

- **`contradicts`** - same subject, same period, same scope, same unit basis, \
  and the values cannot both be true. Use this only when you have actively \
  considered and rejected every reconciling explanation. Restatements between \
  vintages count here when the later document reports a different figure for the \
  same period without labelling it as revised.

- **`reconcilable_context`** - the two look inconsistent at a glance but are \
  both true once you account for a specific difference. Name that difference in \
  `dimension`:
    - `time` - different periods, quarters vs years, point-in-time vs average, \
      different fiscal-year conventions, data vintages
    - `unit` - different currencies, scales (crore/million/billion), percent vs \
      absolute, per-unit vs total
    - `scope` - consolidated vs standalone, segment vs total, one geography vs \
      all, gross vs net, one entity vs a group
    - `basis` - actual vs estimate vs projection vs target, provisional vs \
      revised, different definitions of the same-named metric
  This label is for pairs where the *numbers differ* but the difference is \
  explained. If the numbers agree and the scopes match, that is `corroborates`.

- **`unrelated`** - they are not measuring the same thing, so no comparison is \
  meaningful. Superficial word overlap is not a relationship.

## How to decide

**Compare the facts, not the quotes.** Each fact is exactly its \
`value_as_printed` for its `normalised_period` - nothing else. The \
`evidence_quote` is there to show where the fact came from, and a quote very \
often mentions other figures and other years alongside the one being asserted. \
A number that appears in the quote but is not the fact's own value is context, \
never the thing under comparison. If A's value is 6.6 for 2023 and B's value is \
5.7 for 2024, those are different periods with different values, no matter what \
else either quote happens to mention.

Each pair carries a `precomputed_comparison` block. The unit conversion, \
numeric difference, period match and scope diff in it were computed \
deterministically in code, not by a model - treat them as reliable inputs and \
reason from them rather than redoing the arithmetic. `possible_scale_factor` \
being set is a strong signal that the two figures are the same quantity at \
different magnitudes. If your conclusion disagrees with \
`precomputed_comparison`, assume you have misread the facts and look again.

1. Are the subjects the same real-world entity? If not -> `unrelated`.
2. Are the predicates the same measurement? A "revenue" and a "revenue growth \
   rate" are different measurements -> `unrelated`.
3. Normalise both values to a common unit and scale. Note that 1 crore = 10 \
   million, 1 lakh = 100 thousand.
4. Compare periods precisely. Indian fiscal years run April-March: "FY24" and \
   "2023-24" are the same period; "FY24" and "CY2024" are not.
5. Compare scope qualifiers and measurement basis.
6. Only then choose the label.

## Output

A JSON array with one object per pair, in the same order, each:

{{
  "pair_id": "the id given below",
  "relation": "corroborates | contradicts | reconcilable_context | unrelated",
  "dimension": "time | unit | scope | basis | none",
  "reasoning": "2-4 sentences. Quote the specific values and periods you compared and state what you concluded. If you converted units, show the conversion.",
  "reconciliation": "for reconcilable_context: one sentence a reader can act on, saying what to hold constant for the figures to agree. Otherwise null.",
  "confidence": 0.0
}}

Be specific in `reasoning`: "1,032 crore = INR 10.32 billion, which matches B's \
10.3 billion" is useful; "the values are consistent" is not.

## Pairs

{pairs}"""


def _decode_qualifiers(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(q) for q in raw]
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return [raw]
        if isinstance(parsed, list):
            return [str(q) for q in parsed]
    return []


def _fact_block(fact: dict[str, Any]) -> dict[str, Any]:
    """Compact projection of a fact for the judge - only comparison-relevant fields."""
    return {
        "document": fact.get("doc_title") or fact.get("filename"),
        "subject": fact.get("subject"),
        "predicate": fact.get("predicate"),
        "value_as_printed": fact.get("value_raw"),
        "unit_as_printed": fact.get("unit_raw"),
        "normalised_value": fact.get("value_num"),
        "normalised_unit": fact.get("value_unit"),
        "period_as_printed": fact.get("time_scope_raw"),
        "normalised_period": fact.get("period_key"),
        "basis": fact.get("period_basis"),
        "qualifiers": _decode_qualifiers(fact.get("qualifiers")),
        "evidence_quote": fact.get("quote"),
        "page": fact.get("printed_page") or fact.get("page_index"),
    }


def judge_prompt(pairs: list[dict[str, Any]]) -> str:
    blocks = []
    for pair in pairs:
        blocks.append(
            json.dumps(
                {
                    "pair_id": pair["pair_id"],
                    "A": _fact_block(pair["a"]),
                    "B": _fact_block(pair["b"]),
                    "embedding_similarity": round(pair.get("similarity", 0.0), 3),
                    # Arithmetic already done deterministically in Python. Treat it
                    # as reliable and reason about it; do not redo it.
                    "precomputed_comparison": pair.get("computed", {}),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return JUDGE_PROMPT.format(n=len(pairs), pairs="\n\n".join(blocks))
