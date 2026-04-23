from datetime import date

from kr36 import slider_captcha
from kr36.source_adapter import (
    _bool_config,
    _include_kr36_activity_in_crawl_list,
    _kr36_likely_36kr_csr_risk_listing_shell,
    _kr36_step4_post_slider_page_looks_resolved,
    _normalize_kr36_playwright_chromium_channel,
    _kr36_search_html_has_risk_interstitial,
    _kr36_search_html_likely_zero_hits_message,
    _kr36_search_playwright_body_acceptable,
    _looks_like_captcha_or_block,
    deduce_topic_item_kind_from_36kr_item_url,
    effective_topic_item_kind_for_download,
    extract_kr36_cookie_values,
    extract_video_media_url,
    extract_video_media_urls,
    infer_kr36_bucket,
    parse_activity_listing_html,
    parse_search_listing_html,
    parse_topic_detail_html,
    resolve_current_week_window,
    resolve_kr36_topic_subitem_date_window,
    resolve_kr36_video_detail_page_url,
    resolve_previous_week_window,
    select_focus_topics,
    _kr36_topic_subitem_date_mode_from_config,
)
from utils.tools.content_models import RawArticleRef


def test_deduce_topic_item_kind_from_url_matches_path() -> None:
    assert deduce_topic_item_kind_from_36kr_item_url("https://36kr.com/video/1") == "video"
    assert deduce_topic_item_kind_from_36kr_item_url("https://36kr.com/p/2") == "article"
    assert deduce_topic_item_kind_from_36kr_item_url("") == ""


def test_effective_topic_item_kind_prefers_url_over_metadata() -> None:
    ref = RawArticleRef(
        source_site="36kr",
        article_id="x",
        title="t",
        url="https://36kr.com/video/3766585318896386",
        metadata={"topic_item_kind": "article"},
    )
    assert effective_topic_item_kind_for_download(ref) == "video"


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


def test_playwright_chromium_channel_auto_vs_bundled() -> None:
    assert _normalize_kr36_playwright_chromium_channel("bundled") == ""
    assert _normalize_kr36_playwright_chromium_channel("msedge") == "msedge"
    a = _normalize_kr36_playwright_chromium_channel("auto")
    assert a in ("", "msedge", "chrome")


def test_risk_detection_matches_slider_copy() -> None:
    html = "<html><body>完成验证后继续，按住左边按钮拖动完成上方拼图</body></html>"

    assert _looks_like_captcha_or_block(html) is True


def test_looks_like_captcha_does_not_flag_recaptcha_in_head() -> None:
    """首屏含 recaptcha 脚本时，子串 'captcha' 不应把整页判成滑块页（会拖死 Step4）。"""
    html = (
        "<html><head><script src='https://www.gstatic.com/recaptcha/api2/api.js'>"
        "</script></head><body>正文</body></html>"
    )
    assert _looks_like_captcha_or_block(html) is False


def test_step4_post_slider_heuristic_treats_large_36kr_page_as_ok() -> None:
    body = "x" * 20000
    html = f"<html><head></head><body>36kr.com{body}</body></html>"
    assert _kr36_step4_post_slider_page_looks_resolved(html) is True


def test_slider_pass_visible_text_36kr() -> None:
    assert slider_captcha._visible_text_says_captcha_passed("验证通过，继续访问") is True
    assert slider_captcha._visible_text_says_captcha_passed("验证成功") is True
    assert slider_captcha._visible_text_says_captcha_passed("请进行人机验证") is False


def test_search_listing_keeps_items_per_fixed_category_without_time_filter() -> None:
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


def test_kr36_csr_risk_listing_shell_not_ok_for_step4() -> None:
    """小壳在验证码进 HTML 前 _is_usable_html 为真，应用壳检测拦住「秒关窗」。"""
    from kr36.source_adapter import Kr36SourceAdapter

    shell = (
        "<html><head><title>36氪</title></head><body>"
        "https://36kr.com/foo "
        + ("x" * 2100)
        + "</body></html>"
    )
    assert 500 < len(shell) < 15000
    assert _kr36_likely_36kr_csr_risk_listing_shell(shell) is True

    big = "<html><head></head><body>36kr.com" + "z" * 20000 + "</body></html>"
    assert _kr36_likely_36kr_csr_risk_listing_shell(big) is False

    ad = Kr36SourceAdapter({})
    assert not ad._step4_playwright_allows_return_html(
        shell, age_s=10.0, after_slider_attempt=False
    )


def test_bucket_keeps_topics_separate() -> None:
    assert infer_kr36_bucket("https://36kr.com/topics/123", "36氪编辑精选") == "专题"
    assert infer_kr36_bucket("https://36kr.com/activity/foo", "活动报名") == "活动"


def test_activity_crawl_list_drops_ended_keeps_pending_and_ongoing() -> None:
    assert _include_kr36_activity_in_crawl_list("已结束") is False
    for st in ("未开始", "报名中", "活动中"):
        assert _include_kr36_activity_in_crawl_list(st) is True
    assert _include_kr36_activity_in_crawl_list("") is True


def test_parse_activity_listing_html_filters_ended_cards() -> None:
    html = """
    <a class="activity-item" href="https://x.com/a">活动A 已结束 04月01日-04月02日 北京</a>
    <a class="activity-item" href="https://x.com/b">活动B 报名中 04月21日-04月22日 上海</a>
    <a class="activity-item" href="https://x.com/c">活动C 活动中 04月20日-04月25日 深圳</a>
    """
    refs = parse_activity_listing_html(
        html,
        base_url="https://36kr.com",
        listing_url="https://36kr.com/activity",
        report_date=date(2026, 4, 22),
    )
    by_url = {r.url: (r.metadata or {}).get("activity_status") for r in refs}
    assert by_url == {
        "https://x.com/b": "报名中",
        "https://x.com/c": "活动中",
    }


def test_parse_activity_listing_html_extracts_card_description() -> None:
    html = """
    <a class="activity-item" href="https://x.com/fbif">
      <p class="item-title">FBIF2026食品饮料创新论坛及FBIF展览</p>
      <p class="item-introduce">FBIF2026是集论坛、展览与赛事于一体的综合活动。</p>
      报名中 04月27日-04月29日 杭州
    </a>
    """
    refs = parse_activity_listing_html(
        html,
        base_url="https://36kr.com",
        listing_url="https://36kr.com/activity",
        report_date=date(2026, 4, 23),
    )
    assert len(refs) == 1
    ref = refs[0]
    assert "FBIF2026是集论坛、展览与赛事于一体的综合活动。" in (ref.summary or "")
    assert (ref.metadata or {}).get("activity_description") == "FBIF2026是集论坛、展览与赛事于一体的综合活动。"


def test_select_focus_topics_picks_requested_two_topics() -> None:
    refs = [
        RawArticleRef(
            source_site="36kr",
            article_id="36kr:topic:1",
            title="其他专题",
            url="https://36kr.com/topics/1",
            channel="专题",
            source_bucket="专题",
        ),
        RawArticleRef(
            source_site="36kr",
            article_id="36kr:topic:2",
            title="本周有大事丨测试",
            url="https://36kr.com/topics/2",
            channel="专题",
            source_bucket="专题",
        ),
        RawArticleRef(
            source_site="36kr",
            article_id="36kr:topic:3",
            title="36氪编辑精选丨Vol.1",
            url="https://36kr.com/topics/3",
            channel="专题",
            source_bucket="专题",
        ),
    ]

    selected = select_focus_topics(
        refs,
        focus_keywords=("本周有大事", "36氪编辑精选"),
        limit=2,
    )

    assert [item.url for item in selected] == [
        "https://36kr.com/topics/2",
        "https://36kr.com/topics/3",
    ]


def test_resolve_previous_week_window_uses_week_monday_minus_7_days() -> None:
    window_start, window_end = resolve_previous_week_window(date(2026, 4, 22))
    assert window_start.isoformat() == "2026-04-13"
    assert window_end.isoformat() == "2026-04-19"


def test_resolve_current_week_window_is_monday_through_sunday() -> None:
    s, e = resolve_current_week_window(date(2026, 4, 24))
    assert s.isoformat() == "2026-04-20"
    assert e.isoformat() == "2026-04-26"


def test_resolve_kr36_topic_subitem_date_window_weekday_split_mon_to_thu_is_previous_week() -> None:
    s, e = resolve_kr36_topic_subitem_date_window(date(2026, 4, 22), mode="weekday_split")
    assert s and e
    assert s.isoformat() == "2026-04-13"
    assert e.isoformat() == "2026-04-19"


def test_resolve_kr36_topic_subitem_date_window_weekday_split_fri_to_sun_is_current_week() -> None:
    s, e = resolve_kr36_topic_subitem_date_window(date(2026, 4, 24), mode="weekday_split")
    assert s and e
    assert s.isoformat() == "2026-04-20"
    assert e.isoformat() == "2026-04-26"


def test_resolve_kr36_topic_subitem_date_window_none() -> None:
    s, e = resolve_kr36_topic_subitem_date_window(date(2026, 4, 22), mode="none")
    assert s is None and e is None


def test_kr36_topic_subitem_date_mode_from_config_uses_legacy_bool() -> None:
    assert _kr36_topic_subitem_date_mode_from_config({"topic_previous_week_only": True}) == "previous_week"
    assert _kr36_topic_subitem_date_mode_from_config({"topic_previous_week_only": False}) == "none"
    assert _kr36_topic_subitem_date_mode_from_config({}) == "weekday_split"
    assert _kr36_topic_subitem_date_mode_from_config({"topic_subitem_date_mode": "Previous_Week"}) == "previous_week"


def test_parse_topic_detail_html_filters_previous_week_and_keeps_video_article() -> None:
    html = """
    <ul class="kr-substance-station1video">
      <li class="list-item">
        <a class="item-title ellipsis-2" href="/video/111">视频A</a>
        <div class="item-other">2026-04-15</div>
      </li>
      <li class="list-item">
        <a class="item-title ellipsis-2" href="/video/112">视频B</a>
        <div class="item-other">2026-04-08</div>
      </li>
    </ul>
    <ul class="kr-substance-post">
      <li class="list-item">
        <a class="item-title ellipsis-2" href="/p/200">文章A</a>
        <div class="item-other">2026-04-14</div>
      </li>
      <li class="list-item">
        <a class="item-title ellipsis-2" href="/p/201">文章B</a>
        <div class="item-other">2026-04-21</div>
      </li>
    </ul>
    """

    refs = parse_topic_detail_html(
        html,
        base_url="https://36kr.com",
        listing_url="https://36kr.com/topics/3770543132934659",
        report_date=date(2026, 4, 22),
        topic_title="本周有大事",
        window_start=date(2026, 4, 13),
        window_end=date(2026, 4, 19),
    )

    assert [ref.url for ref in refs] == [
        "https://36kr.com/video/111",
        "https://36kr.com/p/200",
    ]
    assert [ref.metadata.get("topic_item_kind") for ref in refs] == ["video", "article"]


def test_extract_video_media_url_supports_video_tag_and_36krcdn_without_extension() -> None:
    html = """
    <div class="video-wrapper">
      <video class="video" src="https://videos.36krcdn.com/20260414/v2_1776165984015_video_mp4_v11"></video>
    </div>
    """
    assert (
        extract_video_media_url(html)
        == "https://videos.36krcdn.com/20260414/v2_1776165984015_video_mp4_v11"
    )


def test_extract_video_media_url_from_video_tag_protocol_relative_to_https() -> None:
    html = (
        r'<video class="video" src="//videos.36krcdn.com/20260414/v2_1776165984015_video_mp4_v11"></video>'
    )
    assert (
        extract_video_media_url(html)
        == "https://videos.36krcdn.com/20260414/v2_1776165984015_video_mp4_v11"
    )


def test_extract_video_media_url_from_video_tag_http_upgrades_to_https() -> None:
    html = r'<video src="http://videos.36krcdn.com/x/y.mp4"></video>'
    assert extract_video_media_url(html) == "https://videos.36krcdn.com/x/y.mp4"


def test_extract_video_media_url_from_unquoted_src() -> None:
    html = r'<video src=//video.36krcdn.com/20240414/v2_test_video_mp4></video>'
    assert "https://video.36krcdn.com/20240414/v2_test_video_mp4" in extract_video_media_urls(html)


def test_extract_video_media_url_accepts_singular_video_36krcdn_host() -> None:
    """PC 视频页部分版本使用 video.36krcdn.com（单数）而非 videos.36krcdn.com。"""
    html = """
    <video class="video" controls src="https://video.36krcdn.com/20240414/v2_1776185934015_video_mp4_v11">
    您的浏览器不支持视频播放
    </video>
    """
    assert (
        extract_video_media_url(html)
        == "https://video.36krcdn.com/20240414/v2_1776185934015_video_mp4_v11"
    )


def test_extract_video_media_urls_finds_singular_cdn_in_page_blob() -> None:
    """initialState 或内嵌脚本里只出现单数 host 时也应被 CDN 正则要出来。"""
    html = "ref=\"https://video.36krcdn.com/20240414/abc_video_mp4_v1\""
    out = extract_video_media_urls(html)
    assert "https://video.36krcdn.com/20240414/abc_video_mp4_v1" in out


def test_kr36_video_url_candidates_tries_bare_and_mp4_for_singular_cdn_host() -> None:
    from kr36.topic_media import kr36_video_url_candidates

    u = "https://video.36krcdn.com/20240414/v2_1776185934015_video_mp4_v11"
    c = kr36_video_url_candidates(u)
    assert c[0] == u
    assert c[1] == u + ".mp4"


def test_extract_video_media_urls_prefers_video_tag_then_initial_state() -> None:
    html = """
    <script>
      window.initialState={"videoDetail":{"data":{
        "itemId":3765091828527880,
        "url":"https://videos.36krcdn.com/20260413/v2_1776074530156_video_mp4",
        "url384":"https://videos.36krcdn.com/20260413/v2_1776074530156_video_mp4_v1",
        "url720":"https://videos.36krcdn.com/20260413/v2_1776074530156_video_mp4_v3"
      }}};
    </script>
    <video src="https://videos.36krcdn.com/other_fallback.mp4"></video>
    """

    assert extract_video_media_urls(html)[:4] == [
        "https://videos.36krcdn.com/other_fallback.mp4",
        "https://videos.36krcdn.com/20260413/v2_1776074530156_video_mp4",
        "https://videos.36krcdn.com/20260413/v2_1776074530156_video_mp4_v1",
        "https://videos.36krcdn.com/20260413/v2_1776074530156_video_mp4_v3",
    ]


def test_resolve_kr36_video_detail_page_url_normalizes_mobile() -> None:
    assert (
        resolve_kr36_video_detail_page_url("https://m.36kr.com/video/3766585318896386")
        == "https://36kr.com/video/3766585318896386"
    )


def test_extract_video_media_urls_sorts_initial_state_urls_by_resolution_rank() -> None:
    html = """
    <script>
      window.initialState={"videoDetail":{"data":{
        "url720":"https://cdn/z_v3.mp4",
        "url":"https://cdn/z.mp4",
        "url384":"https://cdn/z_v1.mp4"
      }}};
    </script>
    """

    assert extract_video_media_urls(html)[:3] == [
        "https://cdn/z.mp4",
        "https://cdn/z_v1.mp4",
        "https://cdn/z_v3.mp4",
    ]
