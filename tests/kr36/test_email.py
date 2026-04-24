import csv
import json

from kr36.email import (
    BUCKET_INFO,
    _compose_item_viewpoint,
    _filter_kr36_visible_activities,
    _normalize_bucket,
    _render_activity_source_item,
    _render_viewpoint_as_html,
    _render_viewpoint_row_body_html,
    _split_viewpoint_for_point_topic_row,
    _step5_item_has_original_text,
    render_kr36_brief_email,
)


def test_dedupe_and_title_patch_moves_exclusive_article_to_info_scope() -> None:
    """hot 中同 URL 先专题后 36氪独家时保留独家；标题含硬氪专访时栏目视为创投。"""
    from kr36.email import _dedupe_hot_articles_for_brief, _kr36_title_implied_brief_channel

    dup = [
        {
            "source_bucket": "专题",
            "channel": "专题",
            "title": "小米汽车重磅任命｜36氪独家",
            "url": "https://36kr.com/p/1",
        },
        {
            "source_bucket": "36氪独家",
            "channel": "36氪独家",
            "title": "小米汽车重磅任命｜36氪独家",
            "url": "https://36kr.com/p/1",
        },
    ]
    one = _dedupe_hot_articles_for_brief(dup)
    assert len(one) == 1
    assert one[0]["channel"] == "36氪独家"
    assert _kr36_title_implied_brief_channel("半年融资｜硬氪专访") == "创投"
    assert _kr36_title_implied_brief_channel("xx｜36氪首发") == "创投"


def test_normalize_bucket_prefers_info_channel_over_topic_source_bucket() -> None:
    """专题页同步下来的条目常带 source_bucket=专题，仍以栏目 AI/创投 归入资讯。"""
    art = {
        "source_bucket": "专题",
        "channel": "AI",
        "title": "某文",
        "url": "https://36kr.com/p/999",
    }
    assert _normalize_bucket(art) == BUCKET_INFO
    art2 = {**art, "channel": "创投"}
    assert _normalize_bucket(art2) == BUCKET_INFO


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
    assert "名称：活动A；时间：04月21日-04月22日；地点：上海；状态：报名中；倒计时：今天开始；链接：https://36kr.com/sign-up-activity/1" in rendered.text

    assert "activity-card-title" in rendered.html
    assert "活动A" in rendered.html
    assert "activity-card" in rendered.html
    assert "活动A 的观点：" not in rendered.html
    assert "查看链接" not in rendered.html

    topic_pos = rendered.text.index("\n专题\n")
    activity_pos = rendered.text.index("\n活动\n")
    info_pos = rendered.text.index("\n资讯\n")
    assert topic_pos < activity_pos < info_pos


def test_kr36_email_omit_source_links_drops_url_blocks_keeps_info_viewpoints(tmp_path) -> None:
    step6_path = tmp_path / "kr36_step6_brief_20260421.md"
    hot_topics = tmp_path / "kr36_hot_topics_20260421.json"
    hot_topics.write_text(
        json.dumps(
            {
                "articles": [
                    {
                        "source_bucket": "资讯",
                        "channel": "AI",
                        "title": "品牌文",
                        "url": "https://36kr.com/p/100",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    md = """# 测试

## 运行摘要

- 专题：a。
- 活动：b。
- 资讯：c。

## 某资讯主题

### 核心判断

1）提炼观点一：看点一
2）提炼观点二：看点二
"""
    step6_path.write_text(md, encoding="utf-8")
    out = render_kr36_brief_email(md, step6_path, omit_source_links=True)
    assert " 源地址" not in out.text
    assert "https://36kr.com" not in out.text
    assert " 源地址" not in out.html
    assert "某资讯主题" in out.html
    assert "提炼观点一" in out.html

    with_links = render_kr36_brief_email(md, step6_path, omit_source_links=False)
    assert " 源地址" in with_links.text
    assert "https://36kr.com/p/100" in with_links.text


def test_omit_mode_lists_step5_per_item_topic_and_summary(tmp_path) -> None:
    """不展示链接时：若有 step5，专题/活动/资讯按条列观点，栏头为「总述 + 条数」。"""
    step6 = tmp_path / "kr36_step6_brief_20260421.md"
    step6.write_text(
        "# 简报\n\n## 运行摘要\n\n- 专题：导言A。\n\n## 占位\n\n### 核心判断\n\nx\n",
        encoding="utf-8",
    )
    step5 = tmp_path / "kr36_step_5_content_analysis_20260421.yaml"
    step5.write_text(
        """
report_date: '2026-04-21'
input_path: 'x'
generated_at: 'x'
categories:
  - topic: '专题线一'
    items:
      - original_title: '子项甲'
        topic: '专题线一'
        channel: '专题'
        original_url: 'https://36kr.com/video/1'
        original_published_at: ''
        original_content:
          text: ''
          title: ''
          summary: ''
          source: ''
          status: success
        selected_contents: []
        analysis:
          summary: '提炼标题：甲短；内容要点：甲长文观点。'
          core_points: ['要点1']
          new_facts: []
          entities: []
          signals: []
          risk_or_uncertainty: []
          why_it_matters: ''
          layer_notes: []
      - original_title: '子项乙'
        topic: '专题线一'
        channel: '专题'
        original_url: 'https://36kr.com/video/2'
        original_published_at: ''
        original_content:
          text: ''
          title: ''
          summary: ''
          source: ''
          status: success
        selected_contents: []
        analysis:
          summary: '乙的观点一句。'
          core_points: []
          new_facts: []
          entities: []
          signals: []
          risk_or_uncertainty: []
          why_it_matters: ''
          layer_notes: []
""",
        encoding="utf-8",
    )
    out = render_kr36_brief_email(step6.read_text(encoding="utf-8"), step6, omit_source_links=True)
    assert "条专题子项" not in out.html
    assert "下为逐条观点" not in out.html
    # 首条 summary 经 compose 后为「甲短：…」形态，链上标题用结论文「甲短」而非 original_title。
    assert "甲短" in out.html
    assert "子项乙" in out.html
    assert "甲长文观点" in out.html or "提炼标题：甲短" in out.html
    assert "subclass-heading" in out.html
    assert "专题线一" in out.html


def test_omit_mode_topic_two_subclasses_in_template(tmp_path) -> None:
    """step5 中两个「聚合主题」的专题子项在邮件里按子类分块（外层 ol + 子类标题 + 内层观点 ol）。"""
    step6 = tmp_path / "kr36_step6_brief_20260421.md"
    step6.write_text(
        "# 简报\n\n## 运行摘要\n\n- 专题：x。\n\n## 占位\n\n### 核心判断\n\nx\n",
        encoding="utf-8",
    )
    step5 = tmp_path / "kr36_step_5_content_analysis_20260421.yaml"
    step5.write_text(
        """
report_date: '2026-04-21'
input_path: 'x'
generated_at: 'x'
categories:
  - topic: '人工智能与前沿技术'
    items:
      - original_title: 'A文'
        channel: '专题'
        original_url: 'https://36kr.com/video/10'
        original_content: { text: 'x', title: '', summary: '', source: '', status: success }
        selected_contents: []
        analysis:
          summary: '观点A。'
          core_points: []
          new_facts: []
          entities: []
          signals: []
          risk_or_uncertainty: []
          why_it_matters: ''
          layer_notes: []
  - topic: '公司动态与商业策略'
    items:
      - original_title: 'B文'
        channel: '专题'
        original_url: 'https://36kr.com/video/20'
        original_content: { text: 'x', title: '', summary: '', source: '', status: success }
        selected_contents: []
        analysis:
          summary: '观点B。'
          core_points: []
          new_facts: []
          entities: []
          signals: []
          risk_or_uncertainty: []
          why_it_matters: ''
          layer_notes: []
""",
        encoding="utf-8",
    )
    out = render_kr36_brief_email(step6.read_text(encoding="utf-8"), step6, omit_source_links=True)
    assert "section-groups" in out.html
    assert "subclass-block" in out.html
    assert "人工智能与前沿技术" in out.html
    assert "公司动态与商业策略" in out.html
    assert "A文" in out.html and "B文" in out.html
    assert "人工智能与前沿技术：" in out.text
    assert "公司动态与商业策略：" in out.text


def test_omit_mode_info_step5_keeps_rows_without_body_title_link_and_view_explain(tmp_path) -> None:
    """资讯：链文优先用 summary 冒号前观点句（非文章标题），与专题同款。"""
    step6 = tmp_path / "kr36_step6_brief_20260421.md"
    step6.write_text(
        "# 简报\n\n## 运行摘要\n\n- 资讯：导言。\n\n## 占位\n\n### 核心判断\n\nx\n",
        encoding="utf-8",
    )
    hot = tmp_path / "kr36_hot_topics_20260421.json"
    hot.write_text(
        json.dumps(
            {
                "articles": [
                    {
                        "source_bucket": "AI",
                        "channel": "AI",
                        "title": "AI 文",
                        "url": "https://36kr.com/p/9001",
                    },
                    {
                        "source_bucket": "创投",
                        "channel": "创投",
                        "title": "创投文",
                        "url": "https://36kr.com/p/9002",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    step5 = tmp_path / "kr36_step_5_content_analysis_20260421.yaml"
    step5.write_text(
        """
report_date: '2026-04-21'
input_path: 'x'
generated_at: 'x'
categories:
  - topic: '产业'
    items:
      - original_title: 'AI 文'
        channel: 'AI'
        original_url: 'https://36kr.com/p/9001'
        original_content:
          text: ''
          status: empty
        selected_contents: []
        analysis:
          summary: '结论句要短：后面是展开说明用于解释区。'
          core_points: []
          new_facts: []
          entities: []
          signals: []
          risk_or_uncertainty: []
          why_it_matters: ''
          layer_notes: []
      - original_title: '创投文'
        channel: '创投'
        original_url: 'https://36kr.com/p/9002'
        original_content:
          text: ''
          status: empty
        selected_contents: []
        analysis:
          summary: '仅一段无冒号拆分。'
          core_points: []
          new_facts: []
          entities: []
          signals: []
          risk_or_uncertainty: []
          why_it_matters: ''
          layer_notes: []
""",
        encoding="utf-8",
    )
    out = render_kr36_brief_email(step6.read_text(encoding="utf-8"), step6, omit_source_links=True)
    assert "point-topic-link" in out.html
    assert "viewpoints-list" in out.html
    assert 'href="https://36kr.com/p/9001"' in out.html
    assert 'href="https://36kr.com/p/9002"' in out.html
    # 链文为观点句「结论句要短」，href 仍为原文；正文为冒号后展开
    assert '">结论句要短</a>：</span><span class="vp-inline">后面是展开说明用于解释区' in out.html
    # 勿因 step5 资讯为空列表而回退成 Markdown 节标题当条目
    assert "一、AI监督" not in out.html


def test_omit_mode_info_step5_without_hot_json_uses_step5_channel(tmp_path) -> None:
    """报告目录无 kr36_hot_topics.json 时，仍以 step5 的 AI/创投 栏目生成资讯块。"""
    step6 = tmp_path / "kr36_step6_brief_20260421.md"
    step6.write_text(
        "# 简报\n\n## 运行摘要\n\n- 资讯：x。\n\n## 占位\n\n### 核心判断\n\nx\n",
        encoding="utf-8",
    )
    step5 = tmp_path / "kr36_step_5_content_analysis_20260421.yaml"
    step5.write_text(
        """
report_date: '2026-04-21'
input_path: 'x'
categories:
  - topic: '动态'
    items:
      - original_title: '某 AI 报道'
        channel: 'AI'
        original_url: 'https://36kr.com/p/777'
        original_content: { text: '', status: empty }
        selected_contents: []
        analysis:
          summary: '短结：长解释写在这里。'
          core_points: []
          new_facts: []
          entities: []
          signals: []
          risk_or_uncertainty: []
          why_it_matters: ''
          layer_notes: []
""",
        encoding="utf-8",
    )
    out = render_kr36_brief_email(step6.read_text(encoding="utf-8"), step6, omit_source_links=True)
    assert "point-topic-link" in out.html
    assert "viewpoints-list" in out.html
    assert 'href="https://36kr.com/p/777"' in out.html
    assert '">短结</a>' in out.html
    assert "subclass-heading" in out.html


def test_omit_mode_activity_drops_ended_per_step5_and_hot_topics(tmp_path) -> None:
    """不展示链接 + step5：活动子项按 hot_topics 去掉已结束。"""
    step6 = tmp_path / "kr36_step6_brief_20260421.md"
    step6.write_text(
        "# 简报\n\n## 运行摘要\n\n- 活动：导言B。\n\n## 占位\n\n### 核心判断\n\nx\n",
        encoding="utf-8",
    )
    hot = tmp_path / "kr36_hot_topics_20260421.json"
    hot.write_text(
        json.dumps(
            {
                "articles": [
                    {
                        "source_bucket": "活动",
                        "title": "已收场",
                        "url": "https://36kr.com/sign-up-activity/old",
                        "metadata": {"activity_status": "已结束", "start_label": "已结束"},
                    },
                    {
                        "source_bucket": "活动",
                        "title": "进行中活动",
                        "url": "https://36kr.com/sign-up-activity/new",
                        "metadata": {"activity_status": "报名中", "start_label": "今天开始"},
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    step5 = tmp_path / "kr36_step_5_content_analysis_20260421.yaml"
    step5.write_text(
        """
report_date: '2026-04-21'
input_path: 'x'
generated_at: 'x'
categories:
  - topic: '活动线'
    items:
      - original_title: '已收场'
        topic: '活动线'
        channel: '活动'
        original_url: 'https://36kr.com/sign-up-activity/old'
        original_published_at: ''
        original_content:
          text: ''
          title: ''
          summary: ''
          source: ''
          status: success
        selected_contents: []
        analysis:
          summary: '旧活动观点。'
          core_points: []
          new_facts: []
          entities: []
          signals: []
          risk_or_uncertainty: []
          why_it_matters: ''
          layer_notes: []
      - original_title: '进行中活动'
        topic: '活动线'
        channel: '活动'
        original_url: 'https://36kr.com/sign-up-activity/new'
        original_published_at: ''
        original_content:
          text: ''
          title: ''
          summary: ''
          source: ''
          status: success
        selected_contents: []
        analysis:
          summary: '新活动观点：提炼标题：新；内容要点：可参加。'
          core_points: []
          new_facts: []
          entities: []
          signals: []
          risk_or_uncertainty: []
          why_it_matters: ''
          layer_notes: []
""",
        encoding="utf-8",
    )
    out = render_kr36_brief_email(step6.read_text(encoding="utf-8"), step6, omit_source_links=True)
    assert "条活动子项" not in out.html
    assert "下为逐条观点" not in out.html
    assert "今日活动共覆盖" not in out.html
    assert "活动信息" not in out.html
    assert "已收场" not in out.html
    assert "activity-card" in out.html
    assert "activity-viewpoint" not in out.html


def test_filter_kr36_visible_activities_caps_eight_and_drops_ended() -> None:
    rows: list[dict] = []
    for i in range(9):
        rows.append(
            {
                "title": f"A{i}",
                "url": f"https://36kr.com/activity/{i}",
                "metadata": {"activity_status": "报名中"},
            }
        )
    rows.append(
        {
            "title": "End",
            "url": "https://36kr.com/activity/z",
            "metadata": {"activity_status": "已结束"},
        }
    )
    vis = _filter_kr36_visible_activities(rows, max_items=8)
    assert len(vis) == 8
    assert all("End" not in str(x.get("title")) for x in vis)


def test_step5_info_item_without_body_is_not_shown() -> None:
    """无原文（text 空）的资讯在邮件逐条中不出现。"""
    from utils.tools.analysis.models import ContentAnalysisDraft, ContentAnalysisItem, ContentDocument

    with_body = ContentAnalysisItem(
        original_title="A",
        topic="t",
        channel="资讯",
        original_url="https://36kr.com/p/1",
        original_published_at="",
        original_content=ContentDocument(
            "u", "d", "t", "s", "正文有字够长" * 5, "src", "success", ""
        ),
        selected_contents=[],
        analysis=ContentAnalysisDraft("x", [], [], [], [], [], "", []),
    )
    no_body = ContentAnalysisItem(
        original_title="B",
        topic="t",
        channel="资讯",
        original_url="https://36kr.com/p/2",
        original_published_at="",
        original_content=ContentDocument("u2", "d2", "t2", "s2", "", "src", "empty", ""),
        selected_contents=[],
        analysis=ContentAnalysisDraft("y", [], [], [], [], [], "", []),
    )
    assert _step5_item_has_original_text(with_body) is True
    assert _step5_item_has_original_text(no_body) is False


def test_compose_item_viewpoint_leads_with_title_not_labels() -> None:
    """提炼标题+内容要点 合并为「标题：正文」，不再保留前缀标签。"""
    raw = (
        "提炼标题：AI“同伴保全”行为挑战人类控制权；"
        "内容要点：加州大学伯克利分校研究显示，多个模型会帮助同伴作弊。"
    )
    out = _compose_item_viewpoint(raw, [])
    assert "提炼标题" not in out
    assert "内容要点" not in out
    assert "AI“同伴保全”行为挑战人类控制权：加州大学伯克利分校研究显示" in out.replace(" ", "")


def test_colon_lead_viewpoint_renders_head_and_body() -> None:
    """冒号总起式：结论加粗，展开段保留 **粗体**。"""
    s = "顶尖模型存在同伴风险：**7 个**模型会作弊：加州大学研究显示。"
    html = _render_viewpoint_as_html(s)
    assert "vp-main" in html and "vp-body" in html
    assert "同伴风险" in html
    assert "<strong>7 个</strong>" in html


def test_multiline_cn_chapter_line_splits_colon_on_second_line() -> None:
    """首行「一、…」单独成行时：链文用第二行结论，首行不塞进冒号拆分。"""
    v = (
        "一、AI技术应用与伦理：\n"
        "AI技术滥用冲击伦理边界，虚假信息与身份盗用风险激增：展开段落。"
    )
    h, rest = _split_viewpoint_for_point_topic_row(v)
    assert h == "AI技术滥用冲击伦理边界，虚假信息与身份盗用风险激增"
    assert rest == "展开段落。"


def test_multiline_cn_chapter_row_body_html_has_flush_chapter() -> None:
    """大标题内联：vp-chapter-inline 在前，vp-inline 正文在后，均无块级缩进。"""
    v = (
        "一、AI技术应用与伦理\n"
        "AI技术滥用冲击伦理边界，虚假信息与身份盗用风险激增：展开段落。"
    )
    html = _render_viewpoint_row_body_html(v)
    assert 'class="vp-chapter-inline"' in html
    assert "一、AI技术应用与伦理" in html
    assert "展开段落" in html
    assert html.index("vp-chapter-inline") < html.index("vp-inline")


def test_colon_viewpoint_body_html_wraps_description_in_vp_inline() -> None:
    """冒号总起式：结论在链上，正文只保留展开段并包在 vp-inline 内联 span 里（不缩进）。"""
    html = _render_viewpoint_row_body_html("短结论：描述层要内联展示。")
    assert "vp-inline" in html
    assert "描述层要内联展示" in html
    assert "短结论" not in html


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
