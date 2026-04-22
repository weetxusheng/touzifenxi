import csv
import json

from kr36.email import _render_activity_source_item, render_kr36_brief_email


def test_kr36_email_renders_homepage_sections_without_tabs(tmp_path) -> None:
    step6_path = tmp_path / "kr36_step4_brief_20260421.md"
    markdown_text = """# 36Kr 主题简报（2026-04-21）

## 运行摘要

- 原始文章数：3

## 36氪编辑精选

### 核心判断

专题判断。

### 源地址

- 编辑精选 | https://36kr.com/topics/1

## 科技活动与展会

### 核心判断

活动判断。

### 源地址

- 活动A | 2026-04-21 | https://36kr.com/sign-up-activity/1

## 消费品牌观察

### 核心判断

资讯判断。

### 源地址

- 品牌文 | 2026-04-21 | https://36kr.com/p/1
- 专题链接 | https://36kr.com/topics/1
"""
    step6_path.write_text(markdown_text, encoding="utf-8")

    hot_topics_path = tmp_path / "kr36_hot_topics_20260421.json"
    hot_topics_path.write_text(
        json.dumps(
            {
                "articles": [
                    {
                        "source_bucket": "专题",
                        "channel": "专题",
                        "title": "编辑精选",
                        "url": "https://36kr.com/topics/1",
                        "metadata": {},
                    },
                    {
                        "source_bucket": "活动",
                        "channel": "活动",
                        "title": "活动A",
                        "url": "https://36kr.com/sign-up-activity/1",
                        "metadata": {
                            "activity_status": "报名中",
                            "activity_time_range": "04月21日-04月22日",
                            "activity_city": "上海",
                            "start_label": "今天开始",
                        },
                    },
                    {
                        "source_bucket": "资讯",
                        "channel": "资讯",
                        "title": "品牌文",
                        "url": "https://36kr.com/p/1",
                        "metadata": {},
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    csv_path = tmp_path / "kr36_step_1_analysis_20260421.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["栏目", "文章链接"])
        writer.writeheader()
        writer.writerow({"栏目": "专题", "文章链接": "https://36kr.com/topics/1"})
        writer.writerow({"栏目": "活动", "文章链接": "https://36kr.com/sign-up-activity/1"})
        writer.writerow({"栏目": "后浪白皮书", "文章链接": "https://36kr.com/p/1"})

    rendered = render_kr36_brief_email(markdown_text, step6_path)

    assert "tab-bar" not in rendered.html
    assert "switchTab" not in rendered.html
    assert "原始文章数" not in rendered.html
    assert 'topic-link-badge">专题' not in rendered.html
    assert "topic-link-card" not in rendered.html
    assert "topic-source-list" in rendered.html
    assert rendered.html.index("专题判断。") < rendered.html.index('<ul class="link-list topic-source-list">')
    topic_section_html = rendered.html[
        rendered.html.index('id="section-topics"') : rendered.html.index('id="section-activities"')
    ]
    assert topic_section_html.count("源地址") == 1
    assert "专题源地址" in topic_section_html
    assert "专题精选" in rendered.html
    assert "编辑精选" in rendered.html
    assert "活动速览" in rendered.html
    assert "活动A" in rendered.html
    activity_section_html = rendered.html[
        rendered.html.index('id="section-activities"') : rendered.html.index('id="section-analysis"')
    ]
    assert "活动源地址" in activity_section_html
    assert "活动信息直接展示" not in rendered.html
    assert activity_section_html.index("活动判断") < activity_section_html.index("活动源地址")
    assert "今天开始" in activity_section_html
    assert "报名中" in activity_section_html
    assert "活动A（2026-04-21）" not in activity_section_html
    assert "资讯主题分析" in rendered.html
    assert "后浪白皮书" in rendered.html
    info_section_html = rendered.html[rendered.html.index('id="section-analysis"') :]
    assert "源地址" not in info_section_html
    assert "info-channel-title\">后浪白皮书" in info_section_html
    assert "专题链接" not in info_section_html
    assert "深氪" not in rendered.html


def test_activity_source_dedupes_identical_status_and_countdown() -> None:
    html = _render_activity_source_item(
        {
            "title": "某活动",
            "url": "https://36kr.com/activity/1",
            "metadata": {"activity_status": "已结束", "start_label": "已结束"},
        }
    )
    assert html.count("已结束") == 1

    html2 = _render_activity_source_item(
        {
            "title": "另一活动",
            "url": "https://36kr.com/activity/2",
            "metadata": {"activity_status": "报名中", "start_label": "6天后开始"},
        }
    )
    assert "报名中" in html2 and "6天后开始" in html2
