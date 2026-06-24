"""LLM JSON 围栏解析."""

import json

import pytest

from gzh_pipeline.parse.dimensions import _parse_json_from_llm_message


def test_parse_fence_json():
    raw = '```json\n{"facts_html": "<p>x</p>"}\n```'
    d = _parse_json_from_llm_message(raw)
    assert d["facts_html"] == "<p>x</p>"


def test_parse_plain_json():
    d = _parse_json_from_llm_message('{"facts_html": "a"}')
    assert d["facts_html"] == "a"


def test_parse_invalid_raises():
    with pytest.raises(json.JSONDecodeError):
        _parse_json_from_llm_message("not json")
