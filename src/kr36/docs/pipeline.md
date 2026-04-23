# 36Kr 日更流程（四步，与 C114 外搜/搜索清单解耦）

## 总览

| 步骤 | 含义 | 主要产物 | 说明 |
|------|------|----------|------|
| 1 | 文章理解与分组 | `kr36_step_1_analysis_YYYYMMDD.csv` | 沿用既有分析链（`analyze_daily_articles`、主题聚合 LLM 等）；**数据条数以本步 `len(analyses)` / CSV 数据行为准。** |
| 2 | 拉取原文 | `kr36_step2_content_YYYYMMDD.yaml` | **仅**根据步骤 1 中的**文章链接**拉取页面正文，不经过 C114 的搜索清单、不外搜补链。实现见 `synthetic_workflow_from_step1.py` + 正文抓取。**正文「文章行」条数与步骤 1 一致**：`build_synthetic_workflow_from_analyses` 内校验 `sum(每主题 items) == len(analyses)`；`run_content_fetch_workflow(..., expected_article_count=len(analyses))` 在抓取前后再校验，避免静默丢行。 |
| 3 | 整合与结构化分析 | `kr36_step3_analysis_YYYYMMDD.yaml` + `kr36_layer_issues_*.yaml` | 在步骤 2 正文体量上走内容分析（原「step5」类逻辑） |
| 4 | 导出 | `kr36_step4_brief_YYYYMMDD.md` 及同 stem 的 html/email/txt | 主题简报，实现见 `generate_brief_markdown` 与 `brief_assets` |

`websearch` / `c114` 的 `--source 36kr` 会调用 `kr36.cli:run_with_args`：完成步骤 1 的 CSV 落盘后，在 **不写入** `kr36_step_2_search_checklist_*.yaml` 的前提下，直接调用 `kr36.pipeline:run_stages_2_3_4`。

## 与历史六步文件名的关系

历史产物曾使用 `kr36_step_2_search_checklist`、`kr36_step_3_search_results`、`kr36_step_4_content`、`kr36_step_5_content_analysis`、`kr36_step_6_brief`。**新**流程不再生成前三类 C114/外搜相关文件，正文与分角色对应关系为：旧 step4 正文体 ≈ 新 `kr36_step2_content_*`；旧 step5 分析 ≈ 新 `kr36_step3_analysis_*`；旧 step6 简报 ≈ 新 `kr36_step4_brief_*`。

## 步骤 2 抓取失败与 36kr 风控

正文拉取走 `utils.tools.content.fetch.fetch_url_content`；对 `36kr.com` 链接，在配置
`content.kr36_step4_risk_playwright` 为 **true**（见 `config/runtime`）时：

- 直抓 **失败**（`failed`，如网络/HTTP 错误），或
- 直抓 **无正文**（`empty`）且判定为风控/验证（`kr36_risk` 类错误，或页面**标题**含「验证」「人机」「滑块」等），

会打印日志并调用 `kr36.step4_risk.try_36kr_step4_html_after_risk` → `Kr36SourceAdapter.step4_fetch_html_via_playwright_slider`（Chromium + 自动滑块）。

## 相关源码

- `src/kr36/pipeline.py`：步骤 2–4 编排
- `src/kr36/synthetic_workflow_from_step1.py`：用 `ArticleAnalysis` 构造与「无外搜」等价的拉取计划
- `src/kr36/names.py`：文件名约定
- `src/kr36/brief_assets.py`：HTML/邮件侧导出

## 专题聚焦 Step 1.5（与上表「四步」并行的一条专题链）

| 与四步主流程的关系 | 说明 |
|-------------------|------|
| 独立语义 | 从 `/topics/` 列表 → 专题详情子项（`metadata.topic_item_kind`）→ 视频子页拉 CDN/文章子页存 HTML，以及 **视频：ffmpeg 抽音 → 火山转写 `*.transcript.txt`**。实现分段见 `topic_focus_step15.py`。 |
| **专题视频不重复拉「文章全文」** | 带 `topic_item_kind` 的 **视频** 条目，**步骤 2 不应**再对同一 `/video/{id}` 当普通文章去拉 HTML 当正文；**全文以专题目录下** `与 mp4 同名的 .transcript.txt` **（ASR）为准**（见 `docs/topic-focus-step15.md` 表格第 6 步）。 |
| 专题文章 | 仍按 `/p/…` 子链保存或走 Step2 正文拉取。 |

详表、落盘文件命名与 `volc_speech` 配置见 **`src/kr36/docs/topic-focus-step15.md`**。

**专题全文索引**：同一次运行目录下另有 **`kr36_topic_fulltext_YYYYMMDD.json`**（与 `kr36_hot_topics_*.json` 同日期后缀），内为 `items[].article_id` / `content_text` / `fulltext_source`；整理或合并进分析流时**直接读该文件**，勿对专题视频 URL 重复取「文章 HTML 全文」。
