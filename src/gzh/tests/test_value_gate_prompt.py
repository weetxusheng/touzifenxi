"""价值预筛提示词：语义判断、非硬编码规则。"""

from gzh_pipeline.parse.value_gate import value_gate_system_prompt


def test_value_gate_system_prompt_covers_geopolitics_not_hardcoded_keywords(monkeypatch):
    monkeypatch.delenv("GZH_VALUE_GATE_SYSTEM", raising=False)
    p = value_gate_system_prompt()
    assert "禁止用固定关键词" in p or "硬规则" in p
    assert "地缘政治" in p or "国际关系" in p
    assert "外交" in p and "不得直接判为无价值" in p
    assert "习近平" not in p and "普京" not in p


def test_value_gate_system_custom_override(monkeypatch):
    from gzh_pipeline.parse.vision_llm import gate_vision_system_line

    monkeypatch.setenv("GZH_VALUE_GATE_SYSTEM", "自定义{vision_line}")
    assert value_gate_system_prompt() == "自定义" + gate_vision_system_line()
