from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from c114.c114_brief_review import (
    build_brief_review_report,
    build_brief_review_report_with_llm,
    normalize_brief_review_report,
    parse_markdown_link_line,
    render_brief_review_yaml,
    resolve_brief_review_output_paths,
)
from touzifenxi.settings import AppPaths


class BriefReviewPathTests(unittest.TestCase):
    def test_resolve_brief_review_output_paths_uses_same_run_directory(self) -> None:
        app_paths = AppPaths(
            project_root=Path("/repo"),
            data_dir=Path("/repo/data"),
            raw_dir=Path("/repo/data/raw"),
            processed_dir=Path("/repo/data/processed"),
            reports_dir=Path("/repo/reports"),
            state_dir=Path("/repo/state"),
            db_path=Path("/repo/state/touzifenxi.db"),
            database_url=None,
            sample_universe_path=Path("/repo/data/universe_sample.json"),
            watchlist_path=Path("/repo/data/watchlist_v2.json"),
            theme_config_path=Path("/repo/data/themes_v1.json"),
        )

        resolved = resolve_brief_review_output_paths(
            app_paths,
            date(2026, 4, 2),
            brief_input_override="reports/c114_report/c114_search_202604030927/c114_step_6_brief_20260402.md",
        )

        self.assertEqual(
            resolved.brief_input_path,
            Path("/repo/reports/c114_report/c114_search_202604030927/c114_step_6_brief_20260402.md"),
        )
        self.assertEqual(
            resolved.analysis_input_path,
            Path("/repo/reports/c114_report/c114_search_202604030927/c114_step_5_content_analysis_20260402.yaml"),
        )
        self.assertEqual(
            resolved.content_input_path,
            Path("/repo/reports/c114_report/c114_search_202604030927/c114_step_4_content_20260402.yaml"),
        )
        self.assertEqual(
            resolved.review_output_path,
            Path("/repo/reports/c114_report/c114_search_202604030927/c114_step_7_brief_review_20260402.yaml"),
        )


class BriefReviewReportTests(unittest.TestCase):
    def test_parse_markdown_link_line_handles_titles_with_pipes(self) -> None:
        name, url = parse_markdown_link_line(
            "《对话》中国联通曹畅 | 智能体互联网补齐AI时代互联网的“最后一块拼图” | "
            "[https://www.c114.com.cn/video/5918/a1303172.html]"
            "(https://www.c114.com.cn/video/5918/a1303172.html)"
        )

        self.assertEqual(name, "《对话》中国联通曹畅 | 智能体互联网补齐AI时代互联网的“最后一块拼图”")
        self.assertEqual(url, "https://www.c114.com.cn/video/5918/a1303172.html")

    def test_build_brief_review_report_flags_template_leftover_and_link_misgrouped(self) -> None:
        analysis_yaml = """report_date: '2026-04-02'
input_path: '/tmp/step4.yaml'
generated_at: '2026-04-03T09:00:00'
categories:
  - topic: 'AI与算力'
    items:
      - original_title: '未来移动通信论坛吴建军：6G已转入产业实战阶段'
        topic: 'AI与算力'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/news/41/a1307790.html'
        original_content:
          title: '未来移动通信论坛吴建军：6G已转入产业实战阶段'
          summary: '摘要'
          text: '正文内容'
          source: 'aliyun'
          status: 'success'
        selected_contents:
          - query: '吴建军 6G转入产业实战阶段'
            query_type: 'keyword'
            url: 'https://example.com/6g'
            title: '论坛观点：6G进入实战'
            summary: '补充摘要'
            text: '补充正文'
            source: 'aliyun'
            status: 'success'
        analysis:
          summary: ''
          core_points:
          new_facts:
          entities:
          signals:
          risk_or_uncertainty:
          why_it_matters: ''
          layer_notes:
"""
        brief_md = """# C114 主题简报（2026-04-02）

> 角色：资深研究员 agent

## AI与算力

### 核心判断

_待资深研究员 agent 补全：给出该主题最重要的判断。_

### 增量信息

_待资深研究员 agent 补全：仅写相对源稿新增的事实、数据或观点。_

### 产业/公司影响

_待资深研究员 agent 补全：说明对产业链、公司或竞争格局的潜在影响。_

### 需要继续跟踪的点

_待资深研究员 agent 补全：列出后续值得持续追踪的线索。_

### 源地址

- 论坛观点：6G进入实战 | https://example.com/6g

### 补充地址

- 未来移动通信论坛吴建军：6G已转入产业实战阶段 | https://www.c114.com.cn/news/41/a1307790.html
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            analysis_path = tmp / "step5.yaml"
            content_path = tmp / "step4.yaml"
            brief_path = tmp / "step6.md"
            analysis_path.write_text(analysis_yaml, encoding="utf-8")
            content_path.write_text("report_date: '2026-04-02'\n", encoding="utf-8")
            brief_path.write_text(brief_md, encoding="utf-8")

            report = build_brief_review_report(
                report_date="2026-04-02",
                brief_path=brief_path,
                analysis_path=analysis_path,
                content_path=content_path,
            )
            rendered = render_brief_review_yaml(report)

        self.assertEqual(report.overall_decision, "revise")
        issue_types = {finding.issue_type for finding in report.findings}
        self.assertIn("template_leftover", issue_types)
        self.assertIn("link_misgrouped", issue_types)
        self.assertIn("overall_decision: 'revise'", rendered)
        self.assertIn("issue_type: 'template_leftover'", rendered)
        self.assertIn("issue_type: 'link_misgrouped'", rendered)

    def test_build_brief_review_report_passes_clean_brief(self) -> None:
        analysis_yaml = """report_date: '2026-04-02'
input_path: '/tmp/step4.yaml'
generated_at: '2026-04-03T09:00:00'
categories:
  - topic: '量子技术'
    items:
      - original_title: 'Google计划2029年过渡到PQC，并呼吁加密货币社区迁移'
        topic: '量子技术'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/quantum/5285/a1307942.html'
        original_content:
          title: 'Google计划2029年过渡到PQC'
          summary: '摘要'
          text: '正文内容'
          source: 'html_fallback'
          status: 'success'
        selected_contents:
          - query: 'Google 2029 PQC 加密货币'
            query_type: 'keyword'
            url: 'https://finance.sina.com.cn/tech/roll/2026-04-02/doc-inhtaktw2051950.shtml'
            title: 'Google计划2029年过渡到PQC，并呼吁加密货币社区迁移_新浪科技_新浪网'
            summary: '补充摘要'
            text: '补充正文'
            source: 'html_fallback'
            status: 'success'
        analysis:
          summary: 'Google 给出后量子密码迁移时间表。'
          core_points:
            - '量子安全议题开始具备明确时间表。'
          new_facts:
            - 'Google 将自身过渡到 PQC 的时间点明确指向 2029 年。'
          entities:
            - 'Google'
          signals:
            - 'PQC 迁移时间表'
          risk_or_uncertainty:
            - '行业迁移节奏仍取决于云厂商和交易平台推进。'
          why_it_matters: '量子安全议题正在前移。'
          layer_notes:
            - '可继续观察加密社区迁移进度。'
"""
        brief_md = """# C114 主题简报（2026-04-02）

> 角色：资深研究员 agent

## 量子技术

### 核心判断

量子安全议题正在从长期概念转向具有明确时间表的迁移任务，Google 给出的 2029 年窗口意味着加密体系升级开始具备现实紧迫性。

### 增量信息

- Google 将自身过渡到 PQC 的时间点明确指向 2029 年。

### 产业/公司影响

金融安全、加密资产和关键信息基础设施需要更早布局抗量子迁移。

### 需要继续跟踪的点

- 主要云厂商和加密社区的 PQC 迁移节奏。

### 源地址

- Google计划2029年过渡到PQC，并呼吁加密货币社区迁移 | https://www.c114.com.cn/quantum/5285/a1307942.html

### 补充地址

- Google计划2029年过渡到PQC，并呼吁加密货币社区迁移_新浪科技_新浪网 | https://finance.sina.com.cn/tech/roll/2026-04-02/doc-inhtaktw2051950.shtml
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            analysis_path = tmp / "step5.yaml"
            content_path = tmp / "step4.yaml"
            brief_path = tmp / "step6.md"
            analysis_path.write_text(analysis_yaml, encoding="utf-8")
            content_path.write_text("report_date: '2026-04-02'\n", encoding="utf-8")
            brief_path.write_text(brief_md, encoding="utf-8")

            report = build_brief_review_report(
                report_date="2026-04-02",
                brief_path=brief_path,
                analysis_path=analysis_path,
                content_path=content_path,
            )

        self.assertEqual(report.overall_decision, "pass")
        self.assertEqual(report.findings, [])
        self.assertTrue(report.strengths)

    def test_build_brief_review_report_accepts_markdown_source_link_with_pipe_title(self) -> None:
        analysis_yaml = """report_date: '2026-04-02'
input_path: '/tmp/step4.yaml'
generated_at: '2026-04-03T09:00:00'
categories:
  - topic: 'AI与算力'
    items:
      - original_title: '《对话》中国联通曹畅 | 智能体互联网补齐AI时代互联网的“最后一块拼图”'
        topic: 'AI与算力'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/video/5918/a1303172.html'
        original_content:
          title: '《对话》中国联通曹畅 | 智能体互联网补齐AI时代互联网的“最后一块拼图”'
          summary: '摘要'
          text: '正文内容'
          source: 'aliyun'
          status: 'success'
        selected_contents: []
        analysis:
          summary: '中国联通围绕智能体互联网给出网络演进判断。'
          core_points:
          new_facts:
          entities:
          signals:
          risk_or_uncertainty:
          why_it_matters: '智能体互联网被视为 AI 时代网络底座补齐方向。'
          layer_notes:
"""
        brief_md = """# C114 主题简报（2026-04-02）

> 角色：资深研究员 agent

## AI与算力

### 核心判断

中国联通提出智能体互联网概念，意在补齐 AI 时代网络连接与协同机制。

### 增量信息

- 曹畅明确提出智能体互联网是 AI 时代互联网的“最后一块拼图”。

### 产业/公司影响

运营商在 AI 时代的网络定位，正在从连接能力向智能协同底座延伸。

### 需要继续跟踪的点

- 中国联通后续是否发布相关白皮书或落地方案。

### 源地址

- 《对话》中国联通曹畅 | 智能体互联网补齐AI时代互联网的“最后一块拼图” | [https://www.c114.com.cn/video/5918/a1303172.html](https://www.c114.com.cn/video/5918/a1303172.html)

### 补充地址
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            analysis_path = tmp / "step5.yaml"
            content_path = tmp / "step4.yaml"
            brief_path = tmp / "step6.md"
            analysis_path.write_text(analysis_yaml, encoding="utf-8")
            content_path.write_text("report_date: '2026-04-02'\n", encoding="utf-8")
            brief_path.write_text(brief_md, encoding="utf-8")

            report = build_brief_review_report(
                report_date="2026-04-02",
                brief_path=brief_path,
                analysis_path=analysis_path,
                content_path=content_path,
            )

        issue_types = {finding.issue_type for finding in report.findings}
        self.assertNotIn("link_misgrouped", issue_types)

    def test_build_brief_review_report_flags_content_quality_when_analysis_support_is_missing(self) -> None:
        analysis_yaml = """report_date: '2026-04-02'
input_path: '/tmp/step4.yaml'
generated_at: '2026-04-03T09:00:00'
categories:
  - topic: 'AI与算力'
    items:
      - original_title: '《对话》中国联通曹畅 | 智能体互联网补齐AI时代互联网的“最后一块拼图”'
        topic: 'AI与算力'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/video/5918/a1303172.html'
        original_content:
          title: '《对话》中国联通曹畅 | 智能体互联网补齐AI时代互联网的“最后一块拼图”'
          summary: '摘要'
          text: '正文内容'
          source: 'aliyun'
          status: 'success'
        selected_contents:
          - query: '中国联通曹畅 智能体互联网'
            query_type: 'keyword'
            url: 'https://finance.sina.com.cn/tech/roll/2025-12-29/doc-inhemysr0209956.shtml'
            title: '中国联通曹畅：智能体互联网补齐AI时代互联网的“最后一块拼图”'
            summary: '补充摘要'
            text: '补充正文'
            source: 'aliyun'
            status: 'success'
        analysis:
          summary: ''
          core_points:
          new_facts:
          entities:
          signals:
          risk_or_uncertainty:
          why_it_matters: ''
          layer_notes:
"""
        brief_md = """# C114 主题简报（2026-04-02）

> 角色：资深研究员 agent

## AI与算力

### 核心判断

AI 与通信网络融合已经进入资本市场主线，相关设备商将因此获得持续性估值支撑。

### 增量信息

- 中国联通提出智能体互联网将补齐 AI 时代互联网的最后一块拼图。

### 产业/公司影响

设备商和运营商将因此显著改善盈利能力，并重塑竞争格局。

### 需要继续跟踪的点

- 持续跟踪相关进展。

### 源地址

- 《对话》中国联通曹畅 | 智能体互联网补齐AI时代互联网的“最后一块拼图” | [https://www.c114.com.cn/video/5918/a1303172.html](https://www.c114.com.cn/video/5918/a1303172.html)

### 补充地址

- 中国联通曹畅：智能体互联网补齐AI时代互联网的“最后一块拼图” | [https://finance.sina.com.cn/tech/roll/2025-12-29/doc-inhemysr0209956.shtml](https://finance.sina.com.cn/tech/roll/2025-12-29/doc-inhemysr0209956.shtml)
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            analysis_path = tmp / "step5.yaml"
            content_path = tmp / "step4.yaml"
            brief_path = tmp / "step6.md"
            analysis_path.write_text(analysis_yaml, encoding="utf-8")
            content_path.write_text("report_date: '2026-04-02'\n", encoding="utf-8")
            brief_path.write_text(brief_md, encoding="utf-8")

            report = build_brief_review_report(
                report_date="2026-04-02",
                brief_path=brief_path,
                analysis_path=analysis_path,
                content_path=content_path,
            )

        issue_types = {finding.issue_type for finding in report.findings}
        self.assertEqual(report.overall_decision, "revise")
        self.assertIn("unsupported_claim", issue_types)
        self.assertIn("increment_not_new", issue_types)
        self.assertIn("impact_overreach", issue_types)
        self.assertIn("followup_too_generic", issue_types)

    def test_normalize_brief_review_report_accepts_valid_payload(self) -> None:
        report = normalize_brief_review_report(
            {
                "overall_decision": "pass",
                "summary": "当前简报可直接使用。",
                "findings": [
                    {
                        "topic": "AI与算力",
                        "severity": "low",
                        "issue_type": "other",
                        "problem": "无明显问题。",
                        "evidence": "章节结构完整。",
                        "suggestion": "保持当前写法。",
                    }
                ],
                "strengths": ["结构完整。"],
            },
            report_date="2026-04-07",
            brief_path=Path("/tmp/step6.md"),
            analysis_path=Path("/tmp/step5.yaml"),
        )

        self.assertEqual(report.overall_decision, "pass")
        self.assertEqual(len(report.findings), 1)

    def test_build_brief_review_report_with_llm_reviews_each_topic_separately(self) -> None:
        analysis_yaml = """report_date: '2026-04-07'
input_path: '/tmp/step4.yaml'
generated_at: '2026-04-07T20:00:00'
categories:
  - topic: 'AI与算力'
    items:
      - original_title: '标题A'
        topic: 'AI与算力'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/news/a.html'
        original_content:
          title: '标题A'
          summary: '摘要A'
          text: '正文A'
          source: 'aliyun'
          status: 'success'
        selected_contents:
        analysis:
          summary: '摘要A'
          core_points:
            - '观点A'
          new_facts:
            - '事实A'
          entities:
            - '公司A'
          signals:
            - '信号A'
          risk_or_uncertainty:
            - '风险A'
          why_it_matters: '重要性A'
          layer_notes:
            - '备注A'
  - topic: '量子技术'
    items:
      - original_title: '标题B'
        topic: '量子技术'
        channel: '首页'
        original_url: 'https://www.c114.com.cn/news/b.html'
        original_content:
          title: '标题B'
          summary: '摘要B'
          text: '正文B'
          source: 'aliyun'
          status: 'success'
        selected_contents:
        analysis:
          summary: '摘要B'
          core_points:
            - '观点B'
          new_facts:
            - '事实B'
          entities:
            - '公司B'
          signals:
            - '信号B'
          risk_or_uncertainty:
            - '风险B'
          why_it_matters: '重要性B'
          layer_notes:
            - '备注B'
"""
        brief_md = """# C114 主题简报（2026-04-07）

## AI与算力

### 核心判断

判断A

### 增量信息

增量A

### 产业/公司影响

影响A

### 需要继续跟踪的点

跟踪A

### 源地址

- 标题A | https://www.c114.com.cn/news/a.html

### 补充地址

- 无

## 量子技术

### 核心判断

判断B

### 增量信息

增量B

### 产业/公司影响

影响B

### 需要继续跟踪的点

跟踪B

### 源地址

- 标题B | https://www.c114.com.cn/news/b.html

### 补充地址

- 无
"""

        class FakeLLMClient:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def complete_json(self, *, system_prompt: str, user_prompt: str) -> object:
                del system_prompt
                self.calls.append(user_prompt)
                if "AI与算力" in user_prompt:
                    return {
                        "summary": "AI 与算力主题存在 1 个中等问题。",
                        "findings": [
                            {
                                "topic": "AI与算力",
                                "severity": "medium",
                                "issue_type": "weak_judgment",
                                "problem": "判断偏满。",
                                "evidence": "证据不足。",
                                "suggestion": "收缩判断。",
                            }
                        ],
                        "strengths": ["AI 与算力结构完整。"],
                    }
                return {
                    "summary": "量子技术主题可直接通过。",
                    "findings": [],
                    "strengths": ["量子技术增量信息明确。"],
                }

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            analysis_path = tmp / "step5.yaml"
            brief_path = tmp / "step6.md"
            content_path = tmp / "step4.yaml"
            analysis_path.write_text(analysis_yaml, encoding="utf-8")
            brief_path.write_text(brief_md, encoding="utf-8")
            content_path.write_text("report_date: '2026-04-07'\n", encoding="utf-8")

            llm_client = FakeLLMClient()
            report = build_brief_review_report_with_llm(
                report_date="2026-04-07",
                brief_path=brief_path,
                analysis_path=analysis_path,
                content_path=content_path,
                llm_client=llm_client,
            )

        self.assertEqual(len(llm_client.calls), 2)
        self.assertEqual(report.overall_decision, "revise")
        self.assertTrue(any(finding.topic == "AI与算力" for finding in report.findings))
        self.assertIn("AI 与算力结构完整。", report.strengths)
        self.assertIn("量子技术增量信息明确。", report.strengths)
