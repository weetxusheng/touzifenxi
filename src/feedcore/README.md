# FeedCore — 国际新闻简报模块

与 `src/c114/`、`src/kr36/` 并列的业务模块，负责拉取多个 RSS 源 → 跨语种正文抓取 → 多 Agent 四维分析 → 动态子类规划 → 阅读主题聚合 → 输出中文简报。

**默认执行模式**：本地不抓 RSS / 不下正文，全部委托给远端 fetcher（FastAPI 服务，部署目录 `feedcore_remote_fetcher/`），本地只负责 step 4 以后的 LLM 分析与简报渲染。

---

## 1. 快速上手

```bash
# 准备 .env（项目根目录），追加以下 5 个键
MODEL=deepseek-v4-flash
BASE_URL=https://api.deepseek.com
API_KEY=your-llm-api-key
FEEDCORE_REMOTE_FETCHER_URL=http://<remote-host>:3000
FEEDCORE_FETCHER_TOKEN=<server-bearer-token>

# 运行 profile（当前主 profile）
python scripts/feedcore_us_news.py

# Windows 定时任务封装
scripts\feedcore\run_feedcore_us_news_windows.bat
scripts\feedcore\register_feedcore_schedule_windows.bat
scripts\feedcore\send_feedcore_latest_brief_email_windows.bat
```

跑完产物落 `output/reports/feedcore_report/<run_id>/`，最终简报是 `brief.md` 与 `brief.html`。

---

## 2. Profile 配置（替代旧 yaml）

每个 profile = `scripts/feedcore_<name>.py` 一个文件，里面用 Python 常量声明：

```python
RSS_URLS = ["https://news.google.com/rss/search?q=OpenAI&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", ...]
OUTPUT_DIR = ROOT / "output" / "reports" / "feedcore_report"
CONFIG = AppConfig(
    rss_urls=RSS_URLS,
    output_dir=OUTPUT_DIR,
    max_articles=20,                       # 总条数上限；None=不限
    quick_sample_size=3,                   # 跨类目抽样 N 个 RSS 源（None=用全部）
    article_fetch_timeout_seconds=180,     # 单篇正文抓取超时
    similar_article_threshold=0.7,         # 标题相似度去重阈值
    require_substantive_four_dimensions=True,  # 四维门禁
    model=ModelConfig(enabled=True, timeout=120),
    workflow=WorkflowConfig(
        model_concurrency=8,                    # LLM 并发
        type_classification_concurrency=3,
        type_synthesis_concurrency=3,
    ),
    browser=BrowserConfig(enabled=True, timeout=60),
)
```

新增 profile：复制现有脚本改 `RSS_URLS` 和 `CONFIG` 即可。

---

## 3. 流水线步骤详解

总共 10 步。**Step 1-3 默认在远端 fetcher 执行**，本地拉取 artifact 后从 step 4 开始；这里按逻辑顺序说明每一步的输入 / 输出 / 落盘文件 / 失败点。

### Step 1 · 选源 `select_rss_sources`
- **作用**：从 profile 的 `RSS_URLS` 中选出本次要抓的 RSS 源；如果设了 `quick_sample_size=N`，会跨类目均匀抽取 N 个源。
- **输入**：`RSS_URLS: list[str]`、可选 `default_categories: dict[url→str]`。
- **输出文件**：`step1_rss_sources.json` —— `list[RssSourceRecord]`（url、label、default_category、selected_reason）。
- **可能失败**：URL 列表为空 → `ValueError`。

### Step 2 · 拉 RSS 列表 `fetch_feed_items`
- **作用**：对每个 RSS 源发请求，解析 feedparser 拿到 entries，去重（按 link）。
- **输入**：step 1 输出 + `RequestsRssClient`（HTTP 端） + `parse_feed`（feedparser 端）。
- **输出文件**：`step2_feed_items.json` 总表 + `rss_tasks/rss_<NNN>_feed_items.json` 分源明细。
- **schema**：`list[FeedItemRecord]`，含 `article (title/link/pub_date/description)`、`default_category`、`source_label`。
- **可能失败**：单个 RSS 拉不通会跳过该源继续，不抛错。

### Step 3 · 抓正文 + 翻译 `fetch_article_contents`
- **作用**：对每条 feed_item 的 link，HTTP 抓 HTML → trafilatura 抽正文；HTTP 失败且 `browser_enabled=True` 时降级用 Playwright；正文为英文且 `translator!=None` 时调用 deep-translator 翻成中文。
- **输入**：step 2 输出 + `ArticleClient` (+ 可选 `Translator`)。
- **输出文件**：`step3_article_contents.json` 总表 + `rss_tasks/rss_<NNN>_articles_text.json` 分源。
- **schema**：`list[ArticleContentRecord]`，含 `text`、`detected_language`、`translated`、`fetch_error`、`extract_error`、`translation_error`。
- **耗时主因**：Google News 链接需要 Playwright 渲染绕反爬，单篇 30–180s；`article_fetch_concurrency` 控制并发。
- **失败处理**：抓不到的 fallback 用 `article.description or article.title`，并把错误写进 `fetch_error` 字段。

### Step 4a · 研究价值打分（买方门禁）`build_research_score_prompt`
- **作用**：每篇调用 LLM 用买方研究员视角打分（0–100），决定是否进入下游昂贵的四维分析。
- **输入**：step 3 输出（仅 `text` 非空的）。
- **输出文件**：`step4_research_scores.json`。
- **schema**：`score`（整数）、`decision`（`keep` / `pending` / `drop`）、`reason`、`investment_relevance`、`information_increment`、`decision_value`、`verifiability`、`noise_penalty`、`evidence`、`tags`。
- **过滤逻辑**：`decision="drop"` 的文章被剔除，不进入 step 4b。`pending` 视为暂留。
- **示例（真实数据）**：`马斯克起诉 OpenAI 败诉`（score=13, drop, 理由：无实质投资含义）；`OpenAI 周五递交 IPO`（score=64, keep）。

### Step 4b · 单文四维分析 `generate_article_four_dims`
- **作用**：对过门禁的文章，调用 LLM 提取「事实 / 背景 / 影响 / 反面观点」四个维度，并用 `brief_taxonomy` 做最终类目判定。
- **输入**：step 4a 通过门禁的文章 + step 3 的 `text`。
- **输出文件**：`step4_article_four_dims.json` + `rss_tasks/rss_<NNN>_article_four_dims.json`。
- **schema**：`facts: list[str]`、`background: list[str]`、`impact: list[str]`、`contradictions: list[str]`、`brief`（合并文本）、`final_category`（最终一级类目）。
- **四维校验**：`require_substantive_four_dimensions=True` 时，任意维度为空的文章会被丢弃。

### Step 5 · 动态子类规划 `plan_dynamic_subcategories` + `build_type_plan_prompt`
- **作用**：按 `final_category` 分组，每个类目下让 LLM 提议一组短 type（"OpenAI IPO" / "AI 人才争夺" / "AI 芯片" 等），并把 article_id 分配到 type。
- **输入**：step 4b 输出，按 `final_category` 分桶。
- **输出文件**：`step5_type_plan.json`。
- **schema**：`{types: list[{type_id, name, rationale, article_ids: list[str]}], ungrouped: list, validation_errors: list}`。
- **特点**：type 是 LLM **当场提议**的，不依赖预设白名单（区别于 c114 用 `data/themes_v1.json`）。

### Step 6 · 子类校验 + 分组 `group_validated_subcategories`
- **作用**：把 step 5 提议的 type 跟实际 article 对齐做校验（拒绝未知 id / 重复分配 / vague 名 / 缺 rationale），再按 type 重组成集合。
- **输入**：step 4b 输出 + step 5 plan。
- **输出文件**：
  - `step6_grouped_types.json` —— 全部 type 集合（`list[{type_id, name, rationale, articles: list[FourDimRecord]}]`）。
  - `type_collections/type_<NNN>_<name>.json` —— 每个 type 单独一份。
- **后续过滤**：`step6_filter_type_collections` 把低价值边缘集合滤掉（如只有 1 篇低关联文章），结果写 `filtered_type_collections.json`。

### Step 7 · 子类聚合四维 `generate_subcategory_four_dims` + `_synthesize_types`
- **作用**：每个 type 集合里把多篇文章的四维合并去重，让 LLM 合成 type 级"事件级四维 brief"。
- **输入**：step 6 过滤后的 type 集合。
- **输出文件**：`step7_type_four_dims.json` + `step8_collapsed_type_four_dims.json`（按 type_id 折叠的版本）。
- **schema**：`name`、`parent_category`、`facts/background/impact/contradictions` 各为合并去重后的 list。

### Step 8 · 渲染追溯简报 `render_type_brief_markdown` + `render_brief_html`
- **作用**：把 step 7 的 type 级四维渲染成"按 type 分节"的可追溯简报（每条事实附原文链接）。
- **输入**：step 7 输出。
- **输出文件**：`step8_brief.md` + `step8_brief.html`。
- **特点**：这是中间产物，给人工审稿用；最终发邮件 / 给用户的是 step 10。

### Step 9 · 阅读主题聚合 `_synthesize_reading_events`
- **作用**：把 step 7 的细粒度 type（可能 6-10 个）二次聚合成 2-3 个"阅读主题"（reading topics），加 LLM 写一段一级类目导语（intro），并对每个事件做去重整合。
- **输入**：step 7 输出。
- **输出文件**：`step9_reading_topic_groups.json`。
- **schema**：`{category_group, intro, topics: list[{reading_topic, summary, key_points, watchout, source_type_names, events: list[{event, summary, synthesized_facts, synthesized_background, ...}]}]}`。
- **耗时主因**：每个 reading topic 都要跨 type 综合一次 LLM，是流水线里 LLM 调用最重的一步。

### Step 10 · 渲染最终简报 `render_reading_topic_brief_*`
- **作用**：渲染最终用户看到的简报（标题：`国际新闻简报（YYYY-MM-DD）`），按"一级类目 → 阅读主题 → 事件 → 四维"四层结构展开，并保留全部原文链接。
- **输入**：step 9 输出。
- **输出文件**：`step10_brief.md` + `step10_brief.html`，同时复制为顶层 `brief.md` / `brief.html`。

---

## 4. 输出目录结构

```
output/reports/feedcore_report/fetch_YYYYMMDDHHMMSS_full_<id>/
├── artifact.zip                       # 远端 step 1-3 打包
├── manifest.json                      # 远端 fetcher 元数据
├── step1_rss_sources.json             # 选源结果
├── step2_feed_items.json              # RSS 文章元数据
├── step3_article_contents.json        # 正文 + 翻译状态
├── step4_research_scores.json         # 研究价值门禁
├── step4_article_four_dims.json       # 单文四维
├── step5_type_plan.json               # LLM 提议的 type
├── step6_grouped_types.json           # type 集合
├── step7_type_four_dims.json          # type 级四维
├── step8_brief.md / step8_brief.html  # 追溯版简报
├── step8_collapsed_type_four_dims.json
├── step9_reading_topic_groups.json    # 阅读主题聚合
├── step10_brief.md / step10_brief.html # 最终简报
├── brief.md / brief.html              # 复制自 step10
├── filtered_by_research_score.json    # 被门禁滤掉的
├── filtered_type_collections.json     # 被 step6 滤掉的
├── low_quality_articles.json          # 正文质量不够的
├── rss_tasks/                         # 按 RSS 源拆分的中间产物
│   ├── rss_001_feed_items.json
│   ├── rss_001_articles_text.json
│   └── rss_001_article_four_dims.json
├── type_collections/                  # 按 type 拆分
│   └── type_<NNN>_<name>.json
├── checkpoints/                       # 断点续跑用
└── logs/
    ├── workflow.log                   # 步骤事件流（含 input/output/skipped 计数）
    ├── model_calls.log                # LLM trace
    ├── rss_<NNN>.log                  # 每源处理日志
    └── type_<NNN>_<name>.log          # 每个 type 合成日志
```

---

## 5. 与 c114 / kr36 的差异

| 维度 | c114 / kr36 | FeedCore |
|---|---|---|
| 站点形态 | 单站点（c114.com.cn / 36kr.com） | 多 RSS 源跨站 |
| 语种 | 纯中文 | 中文 + 英文（自动翻译） |
| 类目策略 | 主题白名单 `data/themes_v1.json` | LLM 当场动态提议 type |
| 抓取层 | 本地走 `utils/tools/content/fetch.py` | 远端 fetcher（FastAPI 服务） |
| 配置入口 | `config/runtime.local.json` + `c114.runtime.config` | Python profile 脚本（`scripts/feedcore_*.py`） |
| 入口 | `touzifenxi run-c114-daily-brief` 等子命令 | `python scripts/feedcore_<profile>.py` |
| 简报形态 | 单层（subcategory → 四维） | 双层（reading topic → events → 四维） |

---

## 6. 常见问题

**Q：远端 fetcher 一直卡在 `articles=X/N`，怎么办？**  
Google News 链接经常被 Cloudflare / 反爬卡住，远端用 Playwright 抓需要较长时间（单篇 30–180s）。可以直接 `curl` 远端 `/api/fetch-runs/<run_id>/progress` 看实际进度。极端情况下可让远端管理员重启 service。

**Q：本地为什么不再用 Playwright？**  
默认走远端模式，本地不抓正文，所以不需要 Playwright。如果想完全本地化，可以调用 `feedcore.cli` 不带 `--remote-fetch`，但需要本地装 `playwright install chromium`。

**Q：怎么加新 RSS 源？**  
编辑对应的 `scripts/feedcore_<profile>.py`，在 `RSS_URLS` 列表追加 URL。不需要改配置文件。

**Q：怎么调 LLM 参数？**  
`.env` 里改 `MODEL` / `BASE_URL` / `API_KEY` 即可全局生效；timeout / concurrency 在 profile 的 `CONFIG.model` / `CONFIG.workflow` 调。

**Q：如何只跑 step 4 以后（不联远端）？**  
拿到 artifact.zip 解压到 `output_dir/<run_id>/`，然后调 `run_workflow_from_prefetched()` 并跳过 `fetch_remote_artifact`。常用于本地反复调试 LLM 步骤。

---

## 7. 相关代码地图

```
src/feedcore/
├── cli.py                    # 兼容入口（推荐用 scripts/feedcore_*.py）
├── pipeline.py               # legacy 单线程入口（保留作内部 contract）
├── config.py                 # AppConfig / ModelConfig / WorkflowConfig / BrowserConfig + parse_config
├── env.py                    # .env 加载
├── models.py                 # 全部 dataclass 定义
├── rss.py                    # feedparser 封装（远端模式不调）
├── article_fetcher.py        # HTTP + Playwright 抓正文（远端模式不调）
├── content_clean.py / content_scrubber.py  # 正文清洗
├── brief_taxonomy.py         # 一级类目分类
├── brief_validation.py       # 四维门禁
├── brief_editorial_policy.py # 编辑学规则常量
├── openai_compatible_client.py  # LLM 调用层（OpenAI 协议）
├── remote_client.py          # 拉远端 artifact（核心入口）
├── remote_fetcher.py         # legacy 自托管远端逻辑
├── remote_server.py          # legacy 自托管 server
├── local_brief.py            # 无 LLM 启发式 brief（fallback）
├── existing_brief.py         # 从 articles.json 反推 brief
└── workflow/
    ├── orchestrator.py       # WorkflowClients + run_workflow* 入口
    ├── concurrent.py         # 并发执行 + step 4–10 主流程
    ├── steps.py              # 各步骤函数定义
    ├── run_context.py        # 运行目录 / 日志上下文
    ├── subcategories.py      # type 校验逻辑
    ├── types.py              # type plan 协议
    ├── reading_topics.py     # step 9-10 渲染层
    └── research_gate.py      # step 4a 打分 prompt
```

外部相关：
- `feedcore_remote_fetcher/`（仓库根）—— FastAPI 远端服务部署单元
- `scripts/feedcore_us_news.py` —— 当前 us_news profile 入口
- `scripts/feedcore/` —— Windows 跑批 / 发信 / 注册定时任务
- `tests/feedcore/` —— 全部 17 个 pytest 用例
