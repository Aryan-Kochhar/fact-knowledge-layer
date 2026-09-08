"""The key pool must not count the template's placeholders as working keys.

`.env` is created from `.env.example` on first run, and that template ships
`key_one,key_two,key_three` so the expected format is obvious. Before this was
filtered, a fresh clone reported a healthy three-key pool in the console, in
`/api/health` and in the UI, and then failed the first upload with an auth error
that looked like a bug in the app rather than a missing key.
"""

from __future__ import annotations

import pytest

from app.config import _split_keys

# Shape-accurate, entirely fake.
REAL = "AIzaSyB" + "x" * 32
REAL2 = "AQ.Ab8RN6J" + "y" * 30


def test_the_template_default_yields_no_keys():
    assert _split_keys("key_one,key_two,key_three") == []


@pytest.mark.parametrize(
    "placeholder",
    ["key_one", "KEY_TWO", "your_key_here", "your-key", "<your key>", "xxxxxxxx", "...."],
)
def test_common_placeholders_are_discarded(placeholder):
    assert _split_keys(placeholder) == []


def test_a_real_looking_key_survives():
    assert _split_keys(REAL) == [REAL]


def test_both_key_formats_survive():
    assert _split_keys(f"{REAL},{REAL2}") == [REAL, REAL2]


def test_a_real_key_is_kept_even_beside_placeholders():
    """Half-edited .env: one key filled in, the other placeholders left behind."""
    assert _split_keys(f"{REAL},key_two,key_three") == [REAL]


def test_newlines_and_padding_are_tolerated():
    assert _split_keys(f"  {REAL} \n {REAL2}  ") == [REAL, REAL2]


def test_empty_and_missing_are_empty():
    assert _split_keys(None) == []
    assert _split_keys("") == []
    assert _split_keys(" , , ") == []


def test_short_strings_never_count_as_keys():
    """Real Gemini keys are ~39 chars; nothing short is worth trying."""
    assert _split_keys("abc123") == []
    assert _split_keys("a" * 20) == []
    assert _split_keys("a" * 21) == ["a" * 21]
