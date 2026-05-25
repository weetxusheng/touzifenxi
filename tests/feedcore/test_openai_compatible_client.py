import pytest

from feedcore.openai_compatible_client import OpenAICompatibleClient, parse_chat_completion_content
from feedcore.models import Article


def test_parse_chat_completion_content_reads_message_content():
    payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "AI 生成的简报",
                    "reasoning_content": "hidden reasoning",
                }
            }
        ]
    }

    assert parse_chat_completion_content(payload) == "AI 生成的简报"


def test_openai_compatible_client_uses_only_unified_env_names(monkeypatch):
    monkeypatch.setenv("API_KEY", "unified-key")
    monkeypatch.setenv("BASE_URL", "https://api.example.com")
    monkeypatch.setenv("MODEL", "configured-model")

    client = OpenAICompatibleClient()

    assert client.api_key == "unified-key"
    assert client.base_url == "https://api.example.com"
    assert client.model == "configured-model"


def test_openai_compatible_client_rejects_old_env_names(monkeypatch):
    for key in ("API_KEY", "BASE_URL", "MODEL"):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(ValueError, match="API_KEY"):
        OpenAICompatibleClient()


def test_openai_compatible_client_builds_article_summary_prompt(monkeypatch):
    monkeypatch.setenv("API_KEY", "unified-key")
    monkeypatch.setenv("BASE_URL", "https://api.example.com")
    monkeypatch.setenv("MODEL", "configured-model")
    captured = {}

    class FakeClient(OpenAICompatibleClient):
        def chat(self, prompt: str, system_prompt: str = "") -> str:
            captured["prompt"] = prompt
            captured["system_prompt"] = system_prompt
            return "brief"

    article = Article(
        title="OpenAI news",
        link="https://example.com",
        pub_date="",
        description="",
        source="Example",
        feed_url="",
    )

    assert FakeClient().summarize_article(article, "content") == "brief"
    assert "OpenAI news" in captured["prompt"]
    assert "事实" in captured["prompt"]


def test_openai_compatible_client_builds_research_score_prompt(monkeypatch):
    monkeypatch.setenv("API_KEY", "unified-key")
    monkeypatch.setenv("BASE_URL", "https://api.example.com")
    monkeypatch.setenv("MODEL", "configured-model")
    captured = {}

    class FakeClient(OpenAICompatibleClient):
        def chat(self, prompt: str, system_prompt: str = "") -> str:
            captured["prompt"] = prompt
            captured["system_prompt"] = system_prompt
            return '{"score":72,"decision":"keep","reason":"ok","investment_relevance":24,"information_increment":18,"decision_value":15,"verifiability":12,"noise_penalty":3,"evidence":["fact"],"tags":["macro"]}'

    article = Article(
        title="Macro policy update",
        link="https://example.com/macro",
        pub_date="2026-05-14",
        description="",
        source="Example",
        feed_url="",
    )

    result = FakeClient().score_research_article(article, "policy content", "macro")

    assert '"score":72' in result
    assert "Macro policy update" in captured["prompt"]
    assert "policy content" in captured["prompt"]
    assert "macro" in captured["prompt"]
    assert "portfolio manager assistant" in captured["system_prompt"]
    assert "strict JSON" in captured["system_prompt"]
    assert "keep, drop, pending" in captured["prompt"]


def test_openai_compatible_client_plans_subcategories(monkeypatch):
    monkeypatch.setenv("API_KEY", "unified-key")
    monkeypatch.setenv("BASE_URL", "https://api.example.com")
    monkeypatch.setenv("MODEL", "configured-model")
    captured = {}

    class FakeClient(OpenAICompatibleClient):
        def chat(self, prompt: str, system_prompt: str = "") -> str:
            captured["prompt"] = prompt
            captured["system_prompt"] = system_prompt
            return '{"parent_category":"人工智能与科技","subcategories":[],"ungrouped":[]}'

    result = FakeClient().plan_subcategories("人工智能与科技", "prompt body")

    assert "prompt body" in captured["prompt"]
    assert "严格 JSON" in captured["system_prompt"]
    assert "parent_category" in result


def test_openai_compatible_client_plans_short_types(monkeypatch):
    monkeypatch.setenv("API_KEY", "unified-key")
    monkeypatch.setenv("BASE_URL", "https://api.example.com")
    monkeypatch.setenv("MODEL", "configured-model")
    captured = {}

    class FakeClient(OpenAICompatibleClient):
        def chat(self, prompt: str, system_prompt: str = "") -> str:
            captured["prompt"] = prompt
            captured["system_prompt"] = system_prompt
            return '{"types":[],"ungrouped":[]}'

    result = FakeClient().plan_types("type prompt")

    assert "type prompt" in captured["prompt"]
    assert "JSON" in captured["system_prompt"]
    assert "types" in result


def test_openai_compatible_client_summarizes_type_collection(monkeypatch):
    monkeypatch.setenv("API_KEY", "unified-key")
    monkeypatch.setenv("BASE_URL", "https://api.example.com")
    monkeypatch.setenv("MODEL", "configured-model")
    captured = {}

    class FakeClient(OpenAICompatibleClient):
        def chat(self, prompt: str, system_prompt: str = "") -> str:
            captured["prompt"] = prompt
            captured["system_prompt"] = system_prompt
            return "#### 浜嬪疄\n- ok"

    article = Article(
        title="OpenAI news",
        link="https://example.com",
        pub_date="",
        description="",
        source="Example",
        feed_url="",
    )

    result = FakeClient().summarize_type_collection("妯″瀷鍙戝竷", [article])

    assert "妯″瀷鍙戝竷" in captured["prompt"]
    assert "OpenAI news" in captured["prompt"]
    assert "#### 浜嬪疄" in result



def test_openai_compatible_client_summarizes_category_intro_from_topic_facts(monkeypatch):
    monkeypatch.setenv("API_KEY", "unified-key")
    monkeypatch.setenv("BASE_URL", "https://api.example.com")
    monkeypatch.setenv("MODEL", "configured-model")
    captured = {}

    class Record:
        facts = ["Unity revenue declined."]
        impact = ["Investors reassessed growth expectations."]

    class Topic:
        name = "company earnings"
        type_records = [Record()]

    class FakeClient(OpenAICompatibleClient):
        def chat(self, prompt: str, system_prompt: str = "") -> str:
            captured["prompt"] = prompt
            captured["system_prompt"] = system_prompt
            return "Unity revenue declined, and investors reassessed growth expectations."

    result = FakeClient().summarize_category_intro("finance", [Topic()])

    assert "Unity revenue declined" in captured["prompt"]
    assert "\u672c\u7ec4\u8986\u76d6/\u91cd\u70b9\u770b" in captured["prompt"]
    assert "\u53ea\u6839\u636e\u8f93\u5165\u7684\u5b50\u7c7b\u4e8b\u5b9e" in captured["system_prompt"]
    assert "Unity revenue declined" in result


def test_openai_compatible_client_summarizes_reading_event_with_json_payload(monkeypatch):
    monkeypatch.setenv("API_KEY", "unified-key")
    monkeypatch.setenv("BASE_URL", "https://api.example.com")
    monkeypatch.setenv("MODEL", "configured-model")
    captured = {}

    class Record:
        name = "软银投资"
        facts = ["软银已向OpenAI投入超300亿美元。", "软银计划继续追加投资。"]
        background = ["OpenAI估值继续上行。"]
        impact = ["市场关注软银融资压力。"]
        contradictions = ["投资回报兑现节奏仍不确定。"]

    class FakeClient(OpenAICompatibleClient):
        def chat(self, prompt: str, system_prompt: str = "") -> str:
            captured["prompt"] = prompt
            captured["system_prompt"] = system_prompt
            return '{"facts":["软银围绕OpenAI形成既有投入并计划继续加码。"],"background":[],"impact":[],"contradictions":[]}'

    result = FakeClient().summarize_reading_event("人工智能与科技", "AI资本与前沿研究", "软银投资OpenAI", [Record()])

    assert "软银已向OpenAI投入超300亿美元" in captured["prompt"]
    assert "一个事件摘要段" in captured["prompt"]
    assert "每个维度最多输出 1 条" in captured["prompt"]
    assert "长度必须按信息量自适应" in captured["prompt"]
    assert "信息弱时可以输出空数组" in captured["prompt"]
    assert "不要为了凑齐四个维度" in captured["prompt"]
    assert "每个数组只能为空或包含 1 个字符串" in captured["prompt"]
    assert "只输出严格 JSON" in captured["system_prompt"]
    assert "软银围绕OpenAI形成既有投入" in result

def test_openai_compatible_client_reports_unauthorized_without_traceback(monkeypatch):
    monkeypatch.setenv("API_KEY", "bad-key")
    monkeypatch.setenv("BASE_URL", "https://api.example.com")
    monkeypatch.setenv("MODEL", "configured-model")

    class FakeResponse:
        status_code = 401
        text = "Unauthorized"

        def json(self):
            return {}

    def fake_post(*args, **kwargs):
        return FakeResponse()

    monkeypatch.setattr("requests.post", fake_post)

    with pytest.raises(PermissionError, match="API_KEY"):
        OpenAICompatibleClient().chat("hello")


def test_openai_compatible_client_includes_provider_error_body(monkeypatch):
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("BASE_URL", "https://api.example.com")
    monkeypatch.setenv("MODEL", "configured-model")

    class FakeResponse:
        status_code = 400
        text = '{"error":{"message":"input too long"}}'

        def json(self):
            return {}

    def fake_post(*args, **kwargs):
        return FakeResponse()

    monkeypatch.setattr("requests.post", fake_post)

    with pytest.raises(RuntimeError, match="input too long"):
        OpenAICompatibleClient().chat("hello")
