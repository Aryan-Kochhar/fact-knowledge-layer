from app.pipeline.verify import locate_quote, match_quote

PAGE = """Adjusted EBITDA

 ₹ Cr        Q1 FY23  Q2 FY23  Q3 FY23  Q4 FY24     FY23     FY24
 Revenue from customers(1)   1,746   1,796   1,824   2,076    7,225    8,142

Revenue from services grew to ₹8,142 crore in FY24, an increase of
12.7% over the previous financial year, driven by express parcel volumes.
"""

THRESH = 0.82


class TestMatchQuote:
    def test_exact(self):
        m = match_quote("Revenue from services grew to ₹8,142 crore in FY24", PAGE, THRESH)
        assert m.method == "exact"
        assert m.score == 1.0
        assert PAGE[m.start:m.end].startswith("Revenue from services")

    def test_line_wrap_is_tolerated(self):
        # The quote spans a newline in the source; the model returns one line.
        quote = "an increase of 12.7% over the previous financial year"
        m = match_quote(quote, PAGE, THRESH)
        assert m.found
        assert m.score >= 0.9

    def test_punctuation_noise_tolerated(self):
        m = match_quote("Revenue from services grew to 8142 crore in FY24", PAGE, THRESH)
        assert m.found

    def test_table_row_quote(self):
        m = match_quote("Revenue from customers(1)   1,746   1,796   1,824   2,076    7,225    8,142", PAGE, THRESH)
        assert m.found

    def test_offsets_point_at_real_text(self):
        m = match_quote("driven by express parcel volumes", PAGE, THRESH)
        assert PAGE[m.start:m.end].strip().rstrip(".") == "driven by express parcel volumes"

    def test_hallucinated_quote_rejected(self):
        m = match_quote(
            "Revenue from services grew to ₹9,500 crore in FY25, driven by warehousing.",
            PAGE,
            THRESH,
        )
        assert not m.found

    def test_paraphrase_rejected(self):
        m = match_quote("The company's revenues increased substantially during the year.", PAGE, THRESH)
        assert not m.found

    def test_empty_inputs(self):
        assert not match_quote("", PAGE, THRESH).found
        assert not match_quote("anything", "", THRESH).found


class TestLocateQuote:
    def test_finds_on_claimed_page(self):
        page_idx, m = locate_quote("express parcel volumes", {14: PAGE, 15: "unrelated"}, 14, THRESH)
        assert page_idx == 14
        assert m.found

    def test_relocates_when_model_cites_wrong_page(self):
        pages = {14: "Nothing relevant on this page at all.", 15: PAGE}
        page_idx, m = locate_quote("express parcel volumes", pages, 14, THRESH)
        assert page_idx == 15
        assert m.found

    def test_returns_none_when_absent_everywhere(self):
        pages = {14: "alpha beta", 15: "gamma delta"}
        page_idx, m = locate_quote("express parcel volumes", pages, 14, THRESH)
        assert page_idx is None
        assert not m.found
