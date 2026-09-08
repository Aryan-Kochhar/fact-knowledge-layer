"""Deterministic normalisation of model-extracted facts.

The model reads the document; this module makes its output *comparable*. Doing
the arithmetic in Python rather than in the prompt matters for three reasons:

* it is auditable - a reviewer can see exactly why "8,142 crore" and
  "INR 81.42 billion" were treated as the same number;
* it is free - no tokens, no rate limit, no non-determinism;
* it gives the linker hard keys (metric, period, unit dimension) to block on,
  so we only spend LLM judgment on pairs that could plausibly relate.

Everything is pattern-driven and unit-driven. There is no table of known
metrics, companies or documents anywhere in this file.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

# --------------------------------------------------------------------------
# numeric value
# --------------------------------------------------------------------------

# Multipliers, longest-first so "billion" wins before "bn" and "crore" before "cr".
_SCALES: list[tuple[str, float]] = [
    ("trillion", 1e12),
    ("billion", 1e9),
    ("million", 1e6),
    ("thousand", 1e3),
    ("hundred", 1e2),
    ("crore", 1e7),
    ("lakh", 1e5),
    ("lac", 1e5),
    ("bn", 1e9),
    ("mn", 1e6),
    ("tn", 1e12),
    ("cr", 1e7),
    ("k", 1e3),
]

# Order matters: the first hit wins, so anything that contains a shorter token
# has to come before it. Every dollar variant precedes bare "$", and every
# "<country> dollar" precedes bare "dollar", or a Singapore figure would be
# read as US dollars.
#
# Deliberately absent: "won", "real", "rand", "peso", "krona". Each is either a
# common English word or ambiguous across countries, and a false currency hit
# is worse than no hit - it would silently make two unrelated figures look
# comparable. Their ISO codes are listed instead. "¥" stays JPY; nothing in the
# text can reliably separate it from renminbi.
_CURRENCIES: list[tuple[str, str]] = [
    ("inr", "INR"), ("rs.", "INR"), ("rs", "INR"), ("rupee", "INR"), ("₹", "INR"),

    # dollar family - symbols and qualified names before the bare forms
    ("us$", "USD"), ("a$", "AUD"), ("c$", "CAD"), ("s$", "SGD"),
    ("hk$", "HKD"), ("nz$", "NZD"),
    ("australian dollar", "AUD"), ("canadian dollar", "CAD"),
    ("singapore dollar", "SGD"), ("hong kong dollar", "HKD"),
    ("new zealand dollar", "NZD"),
    ("usd", "USD"), ("aud", "AUD"), ("cad", "CAD"), ("sgd", "SGD"),
    ("hkd", "HKD"), ("nzd", "NZD"),
    ("$", "USD"), ("dollar", "USD"),

    ("eur", "EUR"), ("€", "EUR"), ("euro", "EUR"),
    ("gbp", "GBP"), ("£", "GBP"), ("sterling", "GBP"),
    ("jpy", "JPY"), ("¥", "JPY"), ("yen", "JPY"),
    ("chf", "CHF"), ("sfr", "CHF"), ("swiss franc", "CHF"),
    ("cny", "CNY"), ("rmb", "CNY"), ("renminbi", "CNY"), ("yuan", "CNY"),
    ("krw", "KRW"), ("₩", "KRW"),
    ("aed", "AED"), ("dirham", "AED"),
    ("sar", "SAR"), ("qar", "QAR"),
    ("zar", "ZAR"), ("brl", "BRL"), ("mxn", "MXN"), ("rub", "RUB"),
    ("sek", "SEK"), ("nok", "NOK"), ("dkk", "DKK"), ("pln", "PLN"),
    ("try", "TRY"), ("ils", "ILS"), ("₪", "ILS"),
    ("idr", "IDR"), ("myr", "MYR"), ("thb", "THB"), ("php", "PHP"),
    ("vnd", "VND"), ("bdt", "BDT"), ("pkr", "PKR"), ("lkr", "LKR"),
    ("ngn", "NGN"), ("kes", "KES"), ("egp", "EGP"),
]

_PERCENT_TOKENS = ("percentage point", "per cent", "percent", "pct", "%")
# "6.3-6.8", "6.3 to 6.8", "6.3-6.8" with an en dash. Both sides must be
# unsigned digits so a plain negative like "-5.2" is never read as a range.
_RANGE_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(?:-|–|—|to)\s*(\d[\d,]*(?:\.\d+)?)")
_NUMBER_RE = re.compile(r"[-+]?\d{1,3}(?:[, ]\d{2,3})*(?:\.\d+)?|[-+]?\d*\.?\d+")


@dataclass
class ParsedValue:
    number: float | None = None
    unit: str | None = None          # INR | USD | percent | pp | bps | count | <raw token>
    dimension: str | None = None     # currency | ratio | count | rate | other
    scale_applied: float = 1.0
    is_range: bool = False
    notes: list[str] = field(default_factory=list)


def _ascii_fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def _find_scale(text: str) -> tuple[float, str | None]:
    low = text.lower()
    for token, mult in _SCALES:
        # word-boundary match so "crore" does not fire inside "corerevenue" and
        # "k" does not fire inside "risk"
        if re.search(rf"(?<![a-z]){re.escape(token)}(?![a-z])", low):
            return mult, token
    return 1.0, None


def _find_currency(text: str) -> str | None:
    low = text.lower()
    for token, iso in _CURRENCIES:
        if token.isalpha():
            if re.search(rf"(?<![a-z]){re.escape(token)}(?![a-z])", low):
                return iso
        elif token in low:
            return iso
    return None


def parse_value(value_raw: str | None, unit_raw: str | None = None) -> ParsedValue:
    """Turn a printed figure plus its unit into a comparable number."""
    out = ParsedValue()
    if not value_raw:
        return out

    value_text = _ascii_fold(str(value_raw)).strip()
    unit_text = _ascii_fold(str(unit_raw or "")).strip()
    combined = f"{value_text} {unit_text}".strip()
    low = combined.lower()

    # Accounting negatives: (1,234)
    negative = bool(re.match(r"^\(\s*[^)]*\d[^)]*\)$", value_text.strip()))

    numbers = [n for n in _NUMBER_RE.findall(value_text) if any(ch.isdigit() for ch in n)]
    if not numbers:
        # Non-numeric fact (categorical, a rating, a name). Still useful, just
        # not numerically comparable.
        out.dimension = "categorical"
        return out

    def to_float(token: str) -> float | None:
        try:
            return float(token.replace(",", "").replace(" ", ""))
        except ValueError:
            return None

    parsed = [v for v in (to_float(n) for n in numbers) if v is not None]
    if not parsed:
        out.dimension = "categorical"
        return out

    # Ranges are parsed from the raw text, not from `parsed`: the general number
    # regex reads the separating hyphen in "6.3-6.8" as the sign of the second
    # operand, which would make the midpoint nonsense.
    range_hit = _RANGE_RE.search(value_text)
    if range_hit:
        low_end = to_float(range_hit.group(1))
        high_end = to_float(range_hit.group(2))
        if low_end is not None and high_end is not None and high_end >= low_end:
            out.is_range = True
            out.notes.append(f"range {low_end}-{high_end}, midpoint used")
            number = (low_end + high_end) / 2
        else:
            number = parsed[0]
    else:
        number = parsed[0]

    if negative:
        number = -abs(number)

    # --- unit / dimension ---
    if "basis point" in low or re.search(r"(?<![a-z])bps(?![a-z])", low):
        # Normalise to percentage points so bps and % are comparable.
        out.number = number / 100.0
        out.unit = "pp"
        out.dimension = "rate_delta"
        out.notes.append("basis points converted to percentage points")
        return out

    if "percentage point" in low:
        out.number = number
        out.unit = "pp"
        out.dimension = "rate_delta"
        return out

    if any(tok in low for tok in _PERCENT_TOKENS):
        out.number = number
        out.unit = "percent"
        out.dimension = "ratio"
        return out

    scale, scale_token = _find_scale(unit_text or value_text)
    if scale == 1.0 and unit_text:
        scale, scale_token = _find_scale(value_text)
    currency = _find_currency(combined)

    out.number = number * scale
    out.scale_applied = scale
    if scale_token:
        out.notes.append(f"scale '{scale_token}' x{scale:,.0f}")

    if currency:
        out.unit = currency
        out.dimension = "currency"
        return out

    # A bare unit noun ("tonnes", "shipments", "days", "employees").
    residual = re.sub(r"[\d,.\s()%-]", "", unit_text).strip().lower()
    if scale_token:
        residual = re.sub(rf"(?<![a-z]){re.escape(scale_token)}(?![a-z])", "", residual).strip()
    if residual:
        out.unit = residual
        out.dimension = "quantity"
    else:
        out.unit = "count"
        out.dimension = "count"
    return out


# --------------------------------------------------------------------------
# time period
# --------------------------------------------------------------------------

# Default fiscal-year start month. India (and these documents) run April-March,
# so "FY24" = April 2023 - March 2024. Configurable rather than assumed
# globally; see README limitations.
FY_START_MONTH = 4

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

_BASIS_HINTS = [
    ("projection", ("projected", "projection", "forecast", "outlook for", "expected to", "is expected")),
    ("estimate", ("estimate", "estimated", "advance estimate", "provisional", "pe)", "ae)")),
    ("target", ("target", "guidance", "aims to", "aspires")),
]


@dataclass
class ParsedPeriod:
    key: str | None = None
    start: str | None = None
    end: str | None = None
    kind: str | None = None      # fiscal_year | quarter | half | calendar_year | month | instant | range
    notes: list[str] = field(default_factory=list)


def _fy_bounds(fy_end_year: int) -> tuple[str, str]:
    """FY2024 (Indian convention) spans 2023-04-01 .. 2024-03-31."""
    start = date(fy_end_year - 1, FY_START_MONTH, 1)
    end_month = FY_START_MONTH - 1 or 12
    end_year = fy_end_year if FY_START_MONTH > 1 else fy_end_year
    last_day = 31 if end_month in (1, 3, 5, 7, 8, 10, 12) else (30 if end_month != 2 else 28)
    return start.isoformat(), date(end_year, end_month, last_day).isoformat()


def fiscal_year_end(fy_end_year: int) -> str:
    """ISO date on which a given fiscal year closes. Public: the linker needs it
    to tell whether an instant sits exactly on a fiscal-year boundary."""
    return _fy_bounds(fy_end_year)[1]


def _expand_year(token: str) -> int:
    value = int(token)
    if value < 100:
        return 2000 + value if value < 70 else 1900 + value
    return value


_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}

_DATE_PATTERNS = [
    (r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3,9})\.?,?\s+(\d{4})", "dmy"),
    (r"([a-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", "mdy"),
]


def _extract_date(low: str) -> date | None:
    """Pull the first full calendar date out of a lowercased phrase."""
    for pattern, order in _DATE_PATTERNS:
        m = re.search(pattern, low)
        if not m:
            continue
        if order == "dmy":
            day_s, mon_s, year_s = m.group(1), m.group(2), m.group(3)
        else:
            mon_s, day_s, year_s = m.group(1), m.group(2), m.group(3)
        mon = _MONTHS.get(mon_s[:4].rstrip(".")) or _MONTHS.get(mon_s[:3])
        if not mon:
            continue
        try:
            return date(int(year_s), mon, int(day_s))
        except ValueError:
            continue
    return None


def _period_length_months(low: str) -> int | None:
    """"nine months ended ..." -> 9. Returns None when no length is stated."""
    m = re.search(r"\b(\d{1,2})\s*[-\s]?\s*months?\b", low)
    if m:
        return int(m.group(1))
    m = re.search(r"\b([a-z]+)[-\s]months?\b", low)
    if m and m.group(1) in _NUMBER_WORDS:
        return _NUMBER_WORDS[m.group(1)]
    return None


def _quarter_of(month: int) -> int:
    """Which fiscal quarter a month falls in, given the fiscal year start."""
    return ((month - FY_START_MONTH) % 12) // 3 + 1


def _fy_ending(year: int, month: int) -> int:
    """The fiscal year label for a period ending in (year, month)."""
    return year if month < FY_START_MONTH else year + 1


def normalize_period(raw: str | None, fallback: str | None = None) -> ParsedPeriod:
    """Map a printed period phrase onto a canonical key.

    Canonical keys:
      FY2024      fiscal year ending March 2024
      Q4FY2024    fiscal quarter
      H1FY2025    fiscal half
      CY2023      calendar year
      2024-12     calendar month
      @2024-03-31 instant ("as at")
    """
    out = ParsedPeriod()
    text = (raw or "").strip()
    if not text and fallback:
        text = fallback.strip()
        out.notes.append("period inherited from document context")
    if not text:
        return out

    low = _ascii_fold(text).lower().replace("–", "-").replace("—", "-")

    # --- a reporting period that *ends* on a date is a period, not an instant.
    #
    # "for the year ended March 31, 2024" covers all of FY2024; only "as at
    # March 31, 2024" is a point in time. Collapsing the first form to an instant
    # made a flow measure (a year's EBITDA) incomparable with the same figure
    # stated as "FY24", which is how the earnings deck writes it. On the starter
    # corpus that mislabelled 158 facts and caused 46 correct judgments to be
    # flagged as inconsistent.
    if re.search(r"end(?:ed|ing)\b", low):
        instant = _extract_date(low)
        if instant:
            year, month = instant.year, instant.month
            months = _period_length_months(low)

            if re.search(r"\bquarter\b|\bq[1-4]\b", low):
                out.key = f"Q{_quarter_of(month)}FY{_fy_ending(year, month)}"
                out.kind = "quarter"
                out.end = instant.isoformat()
                return out
            if re.search(r"\bhalf[-\s]?year\b|\bsix[-\s]months?\b", low) or months == 6:
                out.key = f"H{1 if _quarter_of(month) <= 2 else 2}FY{_fy_ending(year, month)}"
                out.kind = "half"
                out.end = instant.isoformat()
                return out
            if months and months != 12:
                # A stub period (e.g. "nine months ended December 31, 2021") is
                # neither the fiscal year nor a point in time, and must not be
                # compared against either.
                out.key = f"{months}M@{instant.isoformat()}"
                out.kind = "range"
                out.end = instant.isoformat()
                out.notes.append(f"{months}-month period ending {instant.isoformat()}")
                return out

            # A full year ending on this date.
            if month == 12 and FY_START_MONTH != 1:
                out.key = f"CY{year}"
                out.kind = "calendar_year"
                out.start, out.end = f"{year}-01-01", f"{year}-12-31"
                out.notes.append("year ending 31 December read as a calendar year")
            else:
                fy = _fy_ending(year, month)
                out.key = f"FY{fy}"
                out.kind = "fiscal_year"
                out.start, out.end = _fy_bounds(fy)
                out.notes.append(f"'year ended {instant.isoformat()}' read as FY{fy}")
            return out

    # --- a full calendar date is an instant, with or without an "as at" lead-in.
    # This must run before the month-year rule below: "March 31, 2024" would
    # otherwise match (month=March, year=31) and expand to 2031-03. That single
    # ordering mistake mis-dated 14% of facts on the starter corpus, because
    # "as at March 31, 2024" is how every Indian balance-sheet line is worded.
    for pattern, order in (
        # Ordinal suffixes ("31st March") are common in Indian filings.
        (r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3,9})\.?,?\s+(\d{4})", "dmy"),
        (r"([a-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", "mdy"),
    ):
        m = re.search(pattern, low)
        if not m:
            continue
        if order == "dmy":
            day_s, mon_s, year_s = m.group(1), m.group(2), m.group(3)
        else:
            mon_s, day_s, year_s = m.group(1), m.group(2), m.group(3)
        mon = _MONTHS.get(mon_s[:4].rstrip(".")) or _MONTHS.get(mon_s[:3])
        if not mon:
            continue
        day = int(day_s)
        year = int(year_s)
        if not (1 <= day <= 31 and 1900 <= year <= 2100):
            continue
        try:
            d = date(year, mon, day)
        except ValueError:
            continue
        out.key = f"@{d.isoformat()}"
        out.start = out.end = d.isoformat()
        out.kind = "instant"
        return out

    # "as at March 2024" - an instant stated only to the month.
    m = re.search(r"as\s+(?:at|of|on)\s+(?:the\s+)?([a-z]{3,9})\.?,?\s*(\d{4})", low)
    if m:
        mon = _MONTHS.get(m.group(1)[:4].rstrip(".")) or _MONTHS.get(m.group(1)[:3])
        if mon:
            d = date(int(m.group(2)), mon, 1)
            out.key = f"@{d.isoformat()}"
            out.start = out.end = d.isoformat()
            out.kind = "instant"
            return out

    # --- quarter: "Q4 FY24", "Q4FY2024", "fourth quarter of 2023-24" ---
    m = re.search(r"q([1-4])\s*[-/]?\s*(?:fy)?\s*'?(\d{2,4})(?:\s*[-/]\s*(\d{2,4}))?", low)
    if m:
        q = int(m.group(1))
        year = _expand_year(m.group(3)) if m.group(3) else _expand_year(m.group(2))
        if m.group(3) and int(m.group(3)) < 100:
            year = _expand_year(m.group(2)) + 1
        out.key = f"Q{q}FY{year}"
        out.kind = "quarter"
        start_month = (FY_START_MONTH + 3 * (q - 1) - 1) % 12 + 1
        start_year = year - 1 if start_month >= FY_START_MONTH else year
        out.start = date(start_year, start_month, 1).isoformat()
        end_month = (start_month + 2 - 1) % 12 + 1
        end_year = start_year + (1 if end_month < start_month else 0)
        last = 31 if end_month in (1, 3, 5, 7, 8, 10, 12) else (30 if end_month != 2 else 28)
        out.end = date(end_year, end_month, last).isoformat()
        return out

    # --- half year: "H1 FY25", "first half of 2024-25" ---
    m = re.search(r"h([12])\s*[-/]?\s*(?:fy)?\s*'?(\d{2,4})", low)
    if m:
        out.key = f"H{m.group(1)}FY{_expand_year(m.group(2))}"
        out.kind = "half"
        return out

    # --- fiscal year: "FY24", "FY 2023-24", "2023-24", "FY2024/25" ---
    m = re.search(r"(?:fy|fiscal(?:\s+year)?)\s*'?(\d{2,4})\s*[-/]\s*'?(\d{2,4})", low)
    if m:
        end_year = _expand_year(m.group(2))
        if int(m.group(2)) < 100:
            end_year = _expand_year(m.group(1)) + 1
        out.key = f"FY{end_year}"
        out.kind = "fiscal_year"
        out.start, out.end = _fy_bounds(end_year)
        return out

    m = re.search(r"(?:fy|fiscal(?:\s+year)?)\s*'?(\d{2,4})", low)
    if m:
        end_year = _expand_year(m.group(1))
        out.key = f"FY{end_year}"
        out.kind = "fiscal_year"
        out.start, out.end = _fy_bounds(end_year)
        return out

    # Bare "2023-24" / "2023/24" - in these documents this is a fiscal year.
    m = re.search(r"\b(\d{4})\s*[-/]\s*(\d{2,4})\b", low)
    if m:
        second = int(m.group(2))
        first = int(m.group(1))
        end_year = second if second > 100 else (first // 100) * 100 + second
        if end_year == first + 1:
            out.key = f"FY{end_year}"
            out.kind = "fiscal_year"
            out.start, out.end = _fy_bounds(end_year)
            out.notes.append(f"'{m.group(0)}' read as fiscal year ending {end_year}")
            return out

    # --- month: "December 2024" (4-digit year required), or the explicitly
    # hyphenated short form "Dec-24". A bare "March 31" must never reach here.
    m = re.search(r"\b([a-z]{3,9})\.?\s+(\d{4})\b", low) or re.search(
        r"\b([a-z]{3,9})\.?-(\d{2})\b", low
    )
    if m:
        mon = _MONTHS.get(m.group(1)[:4].rstrip(".")) or _MONTHS.get(m.group(1)[:3])
        if mon:
            year = _expand_year(m.group(2))
            if 1900 <= year <= 2100:
                out.key = f"{year}-{mon:02d}"
                out.kind = "month"
                out.start = date(year, mon, 1).isoformat()
                return out

    # --- calendar year: "2024", "CY2024", "calendar year 2024" ---
    m = re.search(r"\b(?:cy|calendar\s+year)?\s*(\d{4})\b", low)
    if m:
        year = int(m.group(1))
        if 1900 <= year <= 2100:
            out.key = f"CY{year}"
            out.kind = "calendar_year"
            out.start = date(year, 1, 1).isoformat()
            out.end = date(year, 12, 31).isoformat()
            return out

    return out


def detect_basis(model_basis: str | None, *hints: str | None) -> str:
    """Trust the model's `basis`, but let obvious textual cues override 'actual'."""
    basis = (model_basis or "").strip().lower()
    if basis in {"actual", "estimate", "projection", "target"}:
        if basis != "actual":
            return basis
    blob = " ".join(h.lower() for h in hints if h)
    for label, tokens in _BASIS_HINTS:
        if any(tok in blob for tok in tokens):
            return label
    return basis if basis in {"actual", "estimate", "projection", "target"} else "unknown"


# --------------------------------------------------------------------------
# metric identity
# --------------------------------------------------------------------------

_STOPWORDS = {
    "the", "a", "an", "of", "for", "in", "on", "at", "to", "and", "or", "by",
    "its", "their", "this", "that", "with", "from", "as", "is", "was", "were",
    "total", "overall",
}


# Period markers that models habitually fold into the metric name ("FY24
# EBITDA", "Q4 FY24 revenue"). Left in place they fragment the blocking key, so
# the same metric in two documents fails to block together purely because one
# document happened to repeat the year in its label. The period is already
# captured properly in period_key.
_PERIOD_TOKEN_RE = re.compile(
    r"^(?:fy\d{2,4}|q[1-4]|h[12]|cy\d{4}|\d{4}|\d{4}[-/]\d{2,4}|fy)$"
)


def strip_period_tokens(tokens: list[str]) -> list[str]:
    return [t for t in tokens if not _PERIOD_TOKEN_RE.match(t)]


def strip_periods_from_text(text: str) -> str:
    """Drop period markers from a phrase while leaving it readable prose."""
    kept = [w for w in (text or "").split() if not _PERIOD_TOKEN_RE.match(w.strip(",;:()").lower())]
    cleaned = " ".join(kept).strip(" -–—,;:")
    return cleaned or (text or "").strip()


def _slug_tokens(text: str) -> list[str]:
    text = _ascii_fold(text or "").lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    tokens = [t for t in text.split() if t and t not in _STOPWORDS]
    # crude singularisation so "shipments" and "shipment" block together
    return [t[:-1] if len(t) > 4 and t.endswith("s") and not t.endswith("ss") else t for t in tokens]


def metric_key(subject: str, predicate: str) -> str:
    """A coarse blocking key: same key => worth comparing without embeddings.

    Deliberately lossy. It is a recall aid used alongside vector search, never
    the sole basis for a decision.
    """
    subj = strip_period_tokens(_slug_tokens(subject))[:4]
    pred = strip_period_tokens(_slug_tokens(predicate))[:5]
    return "|".join(["-".join(sorted(set(subj))), "-".join(sorted(set(pred)))])


def canonical_claim(subject: str, predicate: str, qualifiers: list[str] | None) -> str:
    """The text that gets embedded.

    Note what is *absent*: the value and the period. We want vector search to
    surface facts that measure the same thing, precisely so that differing
    values and differing periods land in front of the judge. Embedding the value
    would push genuine contradictions apart - the opposite of what we need.
    """
    # Period markers are stripped for the same reason as in metric_key: two
    # documents measuring the same thing must land near each other in vector
    # space even when one of them names the year inside the metric label.
    #
    # Only the period tokens go. The text stays natural language - the sentence
    # encoder was trained on prose, and feeding it a de-stopworded slug
    # ("revenue operation") measurably weakens the embedding versus the real
    # phrase ("revenue from operations").
    parts = [strip_periods_from_text(subject), strip_periods_from_text(predicate)]
    if qualifiers:
        parts.append("; ".join(str(q).strip() for q in qualifiers if str(q).strip()))
    return " — ".join(p for p in parts if p)


def display_claim(subject: str, predicate: str, value: str, unit: str | None, period: str | None) -> str:
    """Human-readable one-line rendering used in the UI and in judge prompts."""
    bits = [subject.strip(), predicate.strip(), "=", str(value).strip()]
    if unit:
        bits.append(str(unit).strip())
    if period:
        bits.append(f"({period.strip()})")
    return " ".join(b for b in bits if b)
