from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from job_os.services.llm_json import extract_json_object, parse_model_json


class Sample(BaseModel):
    name: str
    count: int = 0


def test_plain_object_passes_through() -> None:
    assert extract_json_object('{"name": "a"}') == '{"name": "a"}'


def test_fenced_object_is_unwrapped() -> None:
    text = 'Sure, here you go:\n```json\n{"name": "a"}\n```\nHope that helps.'
    assert extract_json_object(text) == '{"name": "a"}'


def test_object_is_recovered_from_surrounding_prose() -> None:
    # The shape that 400'd the revise path in production: a conversational
    # preamble in front of the object.
    text = '**Assistant message:**\nI will do that.\n{"name": "a", "count": 2}\nDone.'
    assert extract_json_object(text) == '{"name": "a", "count": 2}'


def test_braces_inside_strings_do_not_end_the_object() -> None:
    text = '{"name": "a } b { c", "count": 1} trailing'
    assert extract_json_object(text) == '{"name": "a } b { c", "count": 1}'


def test_escaped_quote_inside_string_is_handled() -> None:
    text = 'note: {"name": "say \\"hi\\" }", "count": 3}'
    assert extract_json_object(text) == '{"name": "say \\"hi\\" }", "count": 3}'


def test_nested_objects_keep_the_outer_span() -> None:
    text = 'prose {"name": "a", "inner": {"deep": {"x": 1}}} more prose'
    assert extract_json_object(text) == '{"name": "a", "inner": {"deep": {"x": 1}}}'


def test_prose_with_no_object_is_returned_for_a_useful_error() -> None:
    # Keeping the original text means the validation error names what the model
    # actually said instead of an empty string.
    assert extract_json_object("  I cannot do that.  ") == "I cannot do that."


def test_parse_model_json_validates_through_prose() -> None:
    parsed = parse_model_json(Sample, 'Here: {"name": "ok", "count": 4}')
    assert (parsed.name, parsed.count) == ("ok", 4)


def test_parse_model_json_still_raises_on_real_prose() -> None:
    with pytest.raises(ValidationError):
        parse_model_json(Sample, "I will run the Review action myself")
