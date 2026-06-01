from feedcore.config import parse_config


def test_parse_config_reads_browser_settings():
    config = parse_config(
        {
            "rss_urls": ["https://news.google.com/rss/search?q=Donald+Trump"],
            "browser": {
                "enabled": True,
                "save_html": True,
                "save_pdf": True,
                "timeout": 45,
            },
        }
    )

    assert config.browser.enabled is True
    assert config.browser.save_html is True
    assert config.browser.save_pdf is True
    assert config.browser.timeout == 45


def test_parse_config_similar_article_threshold_null_disables_filter():
    config = parse_config(
        {
            "rss_urls": ["https://news.google.com/rss/search?q=Donald+Trump"],
            "similar_article_threshold": None,
        }
    )

    assert config.similar_article_threshold is None


def test_parse_config_max_articles_zero_means_unlimited():
    config = parse_config(
        {
            "rss_urls": ["https://news.google.com/rss/search?q=Donald+Trump"],
            "max_articles": 0,
        }
    )
    assert config.max_articles is None


def test_parse_config_can_disable_model():
    config = parse_config(
        {
            "rss_urls": ["https://news.google.com/rss/search?q=Donald+Trump"],
            "model": {"enabled": False},
        }
    )

    assert config.model.enabled is False


def test_parse_config_model_defaults_to_unified_env_names():
    config = parse_config(
        {
            "rss_urls": ["https://news.google.com/rss/search?q=Donald+Trump"],
        }
    )
    assert config.model.enabled is True
    assert config.model.model_env == "MODEL"
    assert config.model.base_url_env == "BASE_URL"
    assert config.model.api_key_env == "API_KEY"


def test_parse_config_reads_model_timeout():
    config = parse_config(
        {
            "rss_urls": ["https://news.google.com/rss/search?q=Donald+Trump"],
            "model": {"timeout": 90},
        }
    )
    assert config.model.timeout == 90


def test_parse_config_require_substantive_four_dimensions_defaults_true():
    config = parse_config(
        {
            "rss_urls": ["https://news.google.com/rss/search?q=Donald+Trump"],
        }
    )
    assert config.require_substantive_four_dimensions is True


def test_parse_config_can_disable_four_dimension_requirement():
    config = parse_config(
        {
            "rss_urls": ["https://news.google.com/rss/search?q=Donald+Trump"],
            "require_substantive_four_dimensions": False,
        }
    )
    assert config.require_substantive_four_dimensions is False


def test_parse_config_reads_structured_rss_sources_with_default_categories():
    config = parse_config(
        {
            "rss_sources": [
                {
                    "url": "https://news.google.com/rss/search?q=OpenAI",
                    "default_category": "人工智能与科技",
                },
                {
                    "url": "https://news.google.com/rss/search?q=Bitcoin",
                    "category": "金融市场与宏观",
                },
            ]
        }
    )

    assert config.rss_urls == [
        "https://news.google.com/rss/search?q=OpenAI",
        "https://news.google.com/rss/search?q=Bitcoin",
    ]
    assert config.rss_default_categories == {
        "https://news.google.com/rss/search?q=OpenAI": "人工智能与科技",
        "https://news.google.com/rss/search?q=Bitcoin": "金融市场与宏观",
    }


def test_parse_config_reads_quick_sample_size():
    config = parse_config(
        {
            "rss_urls": ["https://news.google.com/rss/search?q=OpenAI"],
            "quick_sample_size": 10,
        }
    )

    assert config.quick_sample_size == 10


def test_parse_config_reads_workflow_concurrency_defaults():
    config = parse_config({"rss_urls": ["https://feed.example/rss"]})

    assert config.workflow.rss_concurrency == 10
    assert config.workflow.article_fetch_concurrency == 5
    assert config.workflow.model_concurrency == 6
    assert config.workflow.type_classification_concurrency == 2
    assert config.workflow.type_synthesis_concurrency == 2


def test_parse_config_reads_workflow_concurrency_overrides():
    config = parse_config(
        {
            "rss_urls": ["https://feed.example/rss"],
            "workflow": {
                "rss_concurrency": 3,
                "article_fetch_concurrency": 4,
                "model_concurrency": 7,
                "type_classification_concurrency": 5,
                "type_synthesis_concurrency": 6,
            },
        }
    )

    assert config.workflow.rss_concurrency == 3
    assert config.workflow.article_fetch_concurrency == 4
    assert config.workflow.model_concurrency == 7
    assert config.workflow.type_classification_concurrency == 5
    assert config.workflow.type_synthesis_concurrency == 6
