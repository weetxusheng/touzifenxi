from file_comparison.runtime.config import (
    default_runtime_payload,
    initialize_runtime_config,
    load_file_comparison_runtime_config,
    resolve_api_key,
    write_runtime_config,
)


def test_load_runtime_config_uses_defaults(tmp_path):
    config = load_file_comparison_runtime_config(tmp_path)

    assert config.llm_mode == "rule"
    assert config.llm.chapter_batch_size == 4
    assert config.llm.chapter_batch_char_limit == 10000
    assert config.llm.oversized_chapter_batch_size == 2
    assert config.llm.max_compare_units_per_batch == 4
    assert config.llm.max_compare_unit_chars == 10000
    assert config.llm.failure_cooldown_seconds == 40.0
    assert config.execution.per_pair_max_workers == 2
    assert config.llm.task_routing.enabled is True
    assert config.llm.task_routing.batch_compare == ("minimax", "kimi-code", "deepseek-ark", "deepseek")
    assert config.compare.skip_section_patterns == ("签署页", "签字页", "盖章页", "签章页")
    assert config.paths.output_root == "output/file-comparison/runs"
    assert config.ui.poll_interval_seconds == 2.0


def test_initialize_and_write_runtime_config(tmp_path):
    target = initialize_runtime_config(tmp_path)
    assert target == tmp_path / "config" / "runtime.local.json"
    assert target.exists()

    write_runtime_config(
        tmp_path,
        {
            "llm_mode": "responses",
            "llm": {"model": "gpt-test", "chapter_batch_size": 4},
            "ui": {"port": 9911},
        },
    )
    config = load_file_comparison_runtime_config(tmp_path)

    assert config.llm_mode == "responses"
    assert config.llm.model == "gpt-test"
    assert config.llm.chapter_batch_size == 4
    assert config.ui.port == 9911


def test_load_runtime_config_allows_jsonc_comments(tmp_path):
    config_path = tmp_path / "config" / "runtime.local.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        """
        {
          // 保留给后续切换模型用
          "llm_mode": "responses",
          "llm": {
            "chapter_batch_size": 6,
            "task_routing": {
              "batch_compare": [
                // "minimax",
                "deepseek" // 当前只跑 DeepSeek
              ]
            }
          },
          /*
           * 页面端口也允许临时注释说明。
           */
          "ui": {"port": 9988}
        }
        """,
        encoding="utf-8",
    )

    config = load_file_comparison_runtime_config(tmp_path)

    assert config.llm_mode == "responses"
    assert config.llm.chapter_batch_size == 6
    assert config.llm.task_routing.batch_compare == ("deepseek",)
    assert config.ui.port == 9988


def test_load_runtime_config_supports_multiple_providers(tmp_path):
    write_runtime_config(
        tmp_path,
        {
            "llm": {
                "providers": [
                    {
                        "provider": "minimax",
                        "model": "MiniMax-M2.7",
                        "api_key": "minimax-key",
                        "base_url": "https://api.minimaxi.com/v1",
                    },
                    {
                        "provider": "kimi-code",
                        "model": "kimi-for-coding",
                        "api_key": "kimi-code-key",
                        "base_url": "https://api.kimi.com/coding",
                    },
                ]
            }
        },
    )
    config = load_file_comparison_runtime_config(tmp_path)

    assert [provider.provider for provider in config.llm.providers] == ["minimax", "kimi-code"]
    assert config.llm.provider == "minimax"
    assert config.llm.model == "MiniMax-M2.7"
    assert config.llm.providers[1].model == "kimi-for-coding"


def test_load_runtime_config_supports_c114_style_sections(tmp_path):
    write_runtime_config(
        tmp_path,
        {
            "llm": {
                "providers": [
                    {
                        "provider": "minimax",
                        "model": "MiniMax-M2.7",
                        "api_key": "minimax-key",
                        "base_url": "https://api.minimaxi.com/v1",
                    },
                    {
                        "provider": "kimi-code",
                        "model": "kimi-for-coding",
                        "api_key": "kimi-key",
                        "base_url": "https://api.kimi.com/coding",
                    },
                    {
                        "provider": "deepseek-ark",
                        "model": "ep-20260415114137-55jp7",
                        "api_key": "ark-key",
                        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                    },
                ],
                "retry": {"honor_retry_after": False, "jitter_seconds": 1.25},
                "retry_classifier": {
                    "infra_max_attempts": 4,
                    "parse_max_attempts": 5,
                    "postprocess_max_attempts": 6,
                    "fatal_max_attempts": 1,
                },
                "concurrency": {
                    "default": 3,
                    "providers": {"minimax": 2, "kimi-code": 4, "deepseek-ark": 5},
                },
                "task_routing": {
                    "enabled": True,
                    "batch_compare": ["minimax", "kimi-code", "deepseek-ark"],
                },
            },
            "execution": {"per_pair_max_workers": 3},
        },
    )

    config = load_file_comparison_runtime_config(tmp_path)

    assert config.execution.per_pair_max_workers == 3
    assert config.llm.providers[0].honor_retry_after is False
    assert config.llm.providers[0].jitter_seconds == 1.25
    assert config.llm.providers[0].failure_cooldown_seconds == 40.0
    assert config.llm.providers[0].max_concurrency == 2
    assert config.llm.providers[1].max_concurrency == 4
    assert config.llm.providers[2].provider == "deepseek-ark"
    assert config.llm.providers[2].max_concurrency == 5
    assert config.llm.parse_max_attempts == 5
    assert config.llm.task_routing.enabled is True
    assert config.llm.task_routing.batch_compare == ("minimax", "kimi-code", "deepseek-ark")


def test_resolve_api_key_reads_environment(tmp_path, monkeypatch):
    write_runtime_config(tmp_path, {"llm": {"api_key_env": "FILE_COMPARISON_TEST_KEY"}})
    config = load_file_comparison_runtime_config(tmp_path)
    monkeypatch.setenv("FILE_COMPARISON_TEST_KEY", "secret-token")

    assert resolve_api_key(config) == "secret-token"
    assert default_runtime_payload()["llm"]["api_key_env"] == "MINIMAX_API_KEY"


def test_resolve_api_key_prefers_direct_value(tmp_path, monkeypatch):
    write_runtime_config(tmp_path, {"llm": {"api_key": "inline-secret", "api_key_env": "FILE_COMPARISON_TEST_KEY"}})
    config = load_file_comparison_runtime_config(tmp_path)
    monkeypatch.setenv("FILE_COMPARISON_TEST_KEY", "env-secret")

    assert resolve_api_key(config) == "inline-secret"
