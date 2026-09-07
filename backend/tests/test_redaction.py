"""API keys must never reach a log line or a stored error string.

The key travels as a query parameter, so an httpx transport error can carry the
whole request URL in its string form. That string is written to
`llm_calls.error`, in a database this repository commits - so a leak there would
be a leak into git history.
"""

import httpx

from app.llm import gemini
from app.llm.gemini import redact

FAKE = "AQ.Ab8RN6JzFAKEKEYFAKEKEYFAKEKEYFAKEKEY123456"


class TestRedactUrls:
    def test_strips_key_query_parameter(self):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/x:generateContent?key={FAKE}"
        out = redact(url)
        assert FAKE not in out
        assert "key=<redacted>" in out

    def test_strips_key_when_not_the_first_parameter(self):
        out = redact(f"https://example.com/v1?alt=json&key={FAKE}&pretty=1")
        assert FAKE not in out
        assert "alt=json" in out
        assert "pretty=1" in out

    def test_survives_an_exception_carrying_the_url(self):
        request = httpx.Request(
            "POST", f"https://generativelanguage.googleapis.com/v1beta/x?key={FAKE}"
        )
        exc = httpx.ConnectTimeout("timed out", request=request)
        assert FAKE not in redact(f"{exc!r} {exc.request.url}")

    def test_leaves_ordinary_text_alone(self):
        text = "all 6 attempts failed: 503 UNAVAILABLE"
        assert redact(text) == text

    def test_handles_non_strings(self):
        assert redact(ValueError("boom")) == "boom"
        assert redact(None) == "None"


class TestRedactConfiguredKeys:
    def test_strips_a_configured_key_appearing_bare(self, monkeypatch):
        # Not every leak arrives as a URL; a key echoed in a body must go too.
        monkeypatch.setattr(gemini.settings, "gemini_keys", [FAKE])
        out = redact(f"rejected credential {FAKE} for project")
        assert FAKE not in out
        assert "<redacted>" in out

    def test_short_values_are_not_used_as_patterns(self, monkeypatch):
        # A stray short string must not blank out unrelated text.
        monkeypatch.setattr(gemini.settings, "gemini_keys", ["abc"])
        assert redact("abc appears in this sentence") == "abc appears in this sentence"
