from datetime import date

from kr36.source_adapter import (
    _bool_config,
    _kr36_search_html_has_risk_interstitial,
    _kr36_search_html_likely_zero_hits_message,
    _kr36_search_playwright_body_acceptable,
    _looks_like_captcha_or_block,
    extract_kr36_cookie_values,
    infer_kr36_bucket,
    parse_search_listing_html,
)


def test_extract_kr36_cookie_values_filters_other_domains() -> None:
    cookies = [
        {"name": "sid", "value": "abc", "domain": ".36kr.com"},
        {"name": "trace", "value": "def", "domain": "gateway.36kr.com"},
        {"name": "ignored", "value": "zzz", "domain": ".example.com"},
        {"name": "", "value": "blank", "domain": ".36kr.com"},
    ]

    assert extract_kr36_cookie_values(cookies) == {"sid": "abc", "trace": "def"}


def test_bool_config_parses_common_string_values() -> None:
    assert _bool_config("true", default=False) is True
    assert _bool_config("OFF", default=True) is False
    assert _bool_config("unexpected", default=True) is True


def test_risk_detection_matches_slider_copy() -> None:
    html = "<html><body>完成验证后继续，按住左边按钮拖动完成上方拼图</body></html>"

    assert _looks_like_captcha_or_block(html) is True


def test_search_listing_keeps_first_items_per_fixed_category_without_time_filter() -> None:
    html = """
    <a href="/p/1">当天文章</a><span>刚刚</span>
    <a href="/p/2">旧文章</a><span>30天前</span>
    <a href="/p/4">无时间文章</a>
    """

    refs = parse_search_listing_html(
        html,
        base_url="https://36kr.com",
        listing_url="https://36kr.com/search/articles/深氪",
        report_date=date(2026, 4, 21),
        fixed_category="深氪",
    )

    assert [ref.title for ref in refs] == ["当天文章", "旧文章", "无时间文章"]
    assert {ref.channel for ref in refs} == {"深氪"}


def test_search_listing_parses_title_wrapper_csr_dom() -> None:
    html = """
    <li class="search-result-list-item-article">
      <p class="title-wrapper ellipsis-2"><a href="/p/2733882499096088">36氪独家专访 | 标题</a></p>
    </li>
    """

    refs = parse_search_listing_html(
        html,
        base_url="https://36kr.com",
        listing_url="https://36kr.com/search/articles/36%E6%B0%AA%E7%8B%AC%E5%AE%B6",
        report_date=date(2026, 4, 21),
        fixed_category="36氪独家",
    )

    assert len(refs) == 1
    assert refs[0].url == "https://36kr.com/p/2733882499096088"
    assert "36氪独家专访" in refs[0].title


def test_search_playwright_body_classifies_risk_vs_real_page() -> None:
    risk_small = "<html><script src='https://lf-cdn-tos.bytescm.com/obj/static/sec_sdk_build/3.3.4/captcha/index.js'></script></html>"
    assert _kr36_search_html_has_risk_interstitial(risk_small) is True
    assert _kr36_search_playwright_body_acceptable(risk_small) is False

    big_shell = "x" * 41000
    assert _kr36_search_playwright_body_acceptable(big_shell) is True

    empty_msg = (
        "<ul class='kr-search-result-list-main'></ul><div>暂无相关结果</div>" + "z" * 20000
    )
    assert _kr36_search_html_likely_zero_hits_message(empty_msg) is True


def test_bucket_keeps_topics_separate() -> None:
    assert infer_kr36_bucket("https://36kr.com/topics/123", "36氪编辑精选") == "专题"
    assert infer_kr36_bucket("https://36kr.com/activity/foo", "活动报名") == "活动"
