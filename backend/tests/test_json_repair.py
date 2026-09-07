import pytest

from app.llm.gemini import GeminiResponseError, parse_json_response


class TestParseJsonResponse:
    def test_clean_json(self):
        assert parse_json_response('[{"a": 1}]') == [{"a": 1}]

    def test_code_fenced(self):
        assert parse_json_response('```json\n[{"a": 1}]\n```') == [{"a": 1}]

    def test_leading_prose(self):
        assert parse_json_response('Here are the facts:\n[{"a": 1}]') == [{"a": 1}]

    def test_truncated_array_salvages_complete_elements(self):
        # The failure mode that matters: maxOutputTokens cuts the response mid
        # object. We should keep the objects that did finish.
        truncated = '[{"subject": "A", "value": "1"}, {"subject": "B", "value": "2"}, {"subject": "C", "val'
        out = parse_json_response(truncated)
        assert isinstance(out, list)
        assert len(out) == 2
        assert out[1]["subject"] == "B"

    def test_truncated_nested_object(self):
        truncated = '[{"a": {"b": 1}}, {"a": {"b": 2}}, {"a": {"b"'
        out = parse_json_response(truncated)
        assert len(out) == 2

    def test_unparseable_raises(self):
        with pytest.raises(GeminiResponseError):
            parse_json_response("I could not complete that request.")

    def test_empty_raises(self):
        with pytest.raises(GeminiResponseError):
            parse_json_response("   ")
