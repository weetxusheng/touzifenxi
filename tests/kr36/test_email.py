import csv
import json

from kr36.email import _render_activity_source_item, render_kr36_brief_email


def test_kr36_email_renders_fixed_three_bucket_template(tmp_path) -> None:
    step6_path = tmp_path / "kr36_step4_brief_20260421.md"
    markdown_text = """# 36Kr 主题简报（2026-04-21）

## 运行摘要

- 专题：专题栏目的人工总述示例。
- 活动：活动栏目的人工总述示例。
- 资讯：资讯栏目的人工总述示例。

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
- 子类页 | https://36kr.com/search/articles/123
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
                            "activity_theme": "AI Agent",
                            "start_label": "今天开始",
                        },
                    },
                    {
                        "source_bucket": "资讯",
                        "channel": "资讯",
                        "title": "品牌文",
                        "url": "https://36kr.com/search/articles/123",
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
        writer.writerow({"栏目": "后浪白皮书", "文章链接": "https://36kr.com/search/articles/123"})

    rendered = render_kr36_brief_email(markdown_text, step6_path)

    assert "<h2 class=\"section-title\">专题</h2>" in rendered.html
    assert "<h2 class=\"section-title\">活动</h2>" in rendered.html
    assert "<h2 class=\"section-title\">资讯</h2>" in rendered.html
    assert "专题栏目的人工总述示例。" in rendered.html
    assert "活动栏目的人工总述示例。" in rendered.html
    assert "资讯栏目的人工总述示例。" in rendered.html

    assert "源地址" in rendered.text
    assert "补充地址" not in rendered.text
    assert "编辑精选 的观点：专题判断。" not in rendered.text
    assert "活动A 的观点：活动判断。" not in rendered.text
    assert "1) 品牌文 的观点：资讯判断。" in rendered.text
    assert "2) 子类页 的观点：资讯判断。" in rendered.text
    assert "编辑精选 | https://36kr.com/topics/1" in rendered.text
    assert "子类页 | https://36kr.com/search/articles/123" in rendered.text
    assert "名称：活动A；时间：04月21日-04月22日；地点：上海；主题：AI Agent；倒计时：今天开始；链接：https://36kr.com/sign-up-activity/1" in rendered.text

    assert "activity-card-title" in rendered.html
    assert "活动A" in rendered.html
    assert "时间：" in rendered.html
    assert "地点：" in rendered.html
    assert "主题：" in rendered.html
    assert "倒计时：" in rendered.html
    assert "activity-card" in rendered.html
    assert "活动A 的观点：" not in rendered.html
    assert "查看链接" not in rendered.html

    topic_pos = rendered.text.index("\n专题\n")
    activity_pos = rendered.text.index("\n活动\n")
    info_pos = rendered.text.index("\n资讯\n")
    assert topic_pos < activity_pos < info_pos


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
