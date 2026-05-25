# 产业新闻（SEMI 中国 + 老姚吧）合并接入设计

- 状态：草稿（v3 — 实测踩点后修订）
- 日期：2026-05-19（v1）→ 2026-05-20（v3 数据实测）
- 目标读者：投研系统维护者
- 关联模块：`src/utils/tools/orchestration/`、`src/c114/`、`src/infoq/`

## 1. 背景与目标

仓库目前的 Websearch 流水线只覆盖 `c114`、`infoq`、`kr36` 三个来源。需要新增**面向芯片半导体产业新闻**的统一来源，每日聚合两个站点：

| 站点 | 入口 URL | channel key |
|------|---------|-----------|
| SEMI 中国 | https://www.semi.org.cn/site/semi/ | `semi` |
| 老姚吧（爱集微 / ijiwei） | https://www.laoyaoba.com/ | `laoyaoba` |

业务诉求聚焦两类高价值事件：

1. **半导体公司新品发布**（流片、量产、新规格、新工艺节点等）
2. **产业链价格变动**（晶圆代工/存储/封测/材料 涨跌价、长协调整、合同价变更）

输出：**一份**合并简报 Markdown + **一封**邮件。

## 2. 关键决策：合并为单源 `chip`

两站点作为同一 source 下的两个 channel：用户只读一份产业日报；同事件跨站点出现时跨 channel 去重；新增第三个产业源仅新增 channel，不再起新 source。

## 3. 站点踩点实测（2026-05-20）

下面所有结论都基于真实 HTTP 请求和 HTML 解析，**不依赖假设**。

### 3.1 SEMI 中国

```
GET https://www.semi.org.cn/site/semi/
→ 200, 92KB, server: cloudflare
→ HTML 内直接含 30+ 篇文章链接，无需 JS 渲染
```

- **文章 URL 形态**：`/site/semi/article/<32 位 hex>.html`
- **首页可直接拿到的文章字段**：URL、标题文本（部分含摘要段）。**列表上无发布日期**
- **文章详情页**：
  - 标题：`<h2 class="col-lg-8 ...">...</h2>`
  - 日期：`<span>来源：<span>来源名</span></span> ... <span>2026-05-20</span>` 紧随其后
  - 摘要：`<div class="ct-p ct-bzjy" style="font-style:italic;...">`
  - 正文：`<div class="single-post-content contentPic">...</div>`
  - meta description 也含一句话摘要
- **辅助列表入口**：`/site/semi/column/26595298402893836.html`（热点新闻栏目，约 10 条/页）
- **`/site/share/semi/newslist.html` 是 SPA 壳**（"更多"按钮跳转的页面），实测 14KB，零文章链接 → **不可用**
- **robots.txt**：返回 404（不存在）
- **速率测试**：5 次连续请求全部 200，平均 0.6s，无限流；Cloudflare bot fight 未触发
- **抓取深度估算**：首页 30 篇 × 详情请求 1 次 ≈ 30 次 HTTP，约 30 秒可完成

### 3.2 老姚吧（集微网）

```
GET https://m.laoyaoba.com/        → 4.8KB Vue SPA 壳；robots.txt disallow /api
GET https://www.laoyaoba.com/      → 93KB 服务端渲染，含 38 篇 /n/<id>
GET https://www.laoyaoba.com/xinyaowen   → 84KB 服务端渲染，含 67 篇（推荐主入口）
GET https://www.laoyaoba.com/jwfocus     → 24KB 服务端渲染，含 23 篇（集微焦点）
```

- **必须使用桌面站 `www.laoyaoba.com`**，移动站是 SPA 且 `/api` 被 robots 禁止
- **文章 URL 形态**：`/n/<numeric-id>`
- **首页 / 列表页可直接拿到的文章字段**：URL、标题、**相对或绝对时间**
  - 形如 `"3小时前 特斯拉正式放弃印度建厂计划"` 或 `"中芯国际发布26Q1财报 单季销售收入超25亿美元 05-14 18:18"`
  - 即列表上可做时间过滤，不必每篇都点开详情
- **文章详情页**：32KB，标题、正文、发布时间齐全，服务端渲染
- **官方品牌**：`<title>` 是"爱集微 - ijiwei"，"老杳吧/老姚吧"是历史品牌别名；channel key 用 `laoyaoba`（与本设计对话上下文一致）
- **速率测试**：5 次连续请求全部 200，平均 1.5s，无限流

### 3.3 实测结论对设计的影响

| 实测发现 | 设计调整 |
|---------|--------|
| 双站点都是 SSR HTML | 用 `httpx` + `BeautifulSoup`，**不引入 playwright**（c114/infoq 同栈） |
| SEMI 列表无日期 | adapter 必须"列表 + 逐篇详情"两阶段；按详情日期过滤"今日 / N 日内" |
| 老姚吧列表有时间戳 | adapter 在列表层过滤时间窗，再请求详情；省一半请求 |
| 老姚吧列表有"广告/活动/版权声明"等非新闻条目 | 列表层必须按 URL ID 范围或栏目段位置过滤；最末段 `/n/729927`(版权声明)、`/n/683318`(联系我们) 等需排除 |
| SEMI 文章标题在 `<h2>`，不在 `<title>` | 解析必须用 BS 选择器 `h2.col-lg-8`，不能用 `<title>` |
| 老姚吧覆盖范围广（含汽车、政策、商业新闻） | 主题白名单 + 关键词过滤在 adapter 后端做（即 Step 1 之前不做硬过滤，只在 Step 5/6 分桶） |
| 两站点话题重叠（如"特斯拉印度建厂"两边都出现） | Step 5 跨 channel 去重逻辑必须实装，不能省 |
| Cloudflare 在 SEMI 前 | 仍允许 curl/UA = Chrome；请求间隔保持 1s 以上避免触发 bot fight |

## 4. 非目标

- 不抓评论 / 论坛回帖 / 用户原创长贴（老姚吧也存在但本期不抓）
- 不接 m.laoyaoba.com SPA + API（被 robots 禁；信息从桌面站已经够）
- 不与 A 股投研流水线的推荐归档耦合
- 不引入新数据库表
- 不引入 playwright；如果未来 SEMI 上 bot fight 才考虑

## 5. 共享接口（已存在，本设计直接复用）

定义于 `src/utils/tools/content_models.py`：

```python
class ContentSourceAdapter(ABC):
    source_site: str
    def fetch_listing(self, report_date: date) -> list[RawArticleRef]: ...
    def fetch_article(self, ref: RawArticleRef) -> RawArticleDetail: ...
    def normalize_article(self, raw: RawArticleDetail) -> StandardArticle: ...
```

`RawArticleRef.channel` 与 `source_bucket` 字段天然支持单源多 channel。

参考多 channel 模板：`src/utils/tools/orchestration/c114/hot_topics_data.py::CHANNELS` dict + `ChannelSpec` dataclass + `collect_daily_report`。

## 6. 模块拆分

### 6.1 `src/chip/`（新增）

```
src/chip/
├── __init__.py
├── cli.py                       # 参照 src/infoq/cli.py
├── settings.py                  # 路径常量、前缀
├── source_adapter.py            # ChipSourceAdapter
└── channels/
    ├── __init__.py
    ├── spec.py                  # ChipChannelSpec + 注册表
    ├── semi.py                  # SEMI 中国 listing+article 实现
    └── laoyaoba.py              # 老姚吧 listing+article 实现
```

### 6.2 `channels/spec.py`

```python
@dataclass(frozen=True)
class ChipChannelSpec:
    key: str                # "semi" / "laoyaoba"
    name: str               # "SEMI 中国" / "老姚吧"
    listing_urls: tuple[str, ...]   # 一个或多个列表入口
    fetch_listing: Callable[[Self, date], list[RawArticleRef]]
    fetch_article: Callable[[Self, RawArticleRef], RawArticleDetail]

CHANNELS: dict[str, ChipChannelSpec] = {
    "semi":     ChipChannelSpec(...),
    "laoyaoba": ChipChannelSpec(...),
}
```

### 6.3 `channels/semi.py`

**列表抓取**：
- 主入口：`https://www.semi.org.cn/site/semi/`
- 辅助：`https://www.semi.org.cn/site/semi/column/26595298402893836.html`
- 解析：BS 选择 `a[href*="/site/semi/article/"]`，去重
- 列表层不能拿日期 → **每篇都进详情拿日期**，详情阶段按 `report_date` 过滤

**文章详情**：
- 标题：`soup.select_one('h2.col-lg-8').get_text(strip=True)`
- 日期：在 "来源：xxx" 后的最后一个独立 `<span>20\d{2}-\d{1,2}-\d{1,2}</span>`
  - fallback：HTTP `Last-Modified` 头
- 摘要：`soup.select_one('div.ct-p.ct-bzjy').get_text(strip=True)`
- 正文：`soup.select_one('div.single-post-content').get_text("\n", strip=True)`
- 来源机构：第一个 "来源：" 后跟随的 span 文本

**请求节流**：列表 + 详情合并不超过每秒 1 次（Cloudflare 友好）

### 6.4 `channels/laoyaoba.py`

**列表抓取**：
- 主入口：`https://www.laoyaoba.com/xinyaowen`（67 篇/页，最大）
- 辅助：`https://www.laoyaoba.com/jwfocus`（集微焦点）
- 解析：BS 选择 `a[href^="/n/"]`，提取数字 id
- **排除**：`/n/729927`、`/n/683318`、`/n/683317`（版权声明/联系我们/关于我们）这类静态页 → 用一个 EXCLUDED_IDS = {729927, 683317, 683318} 常量
- **过滤非新闻**：丢弃 anchor 文本 < 8 字符或纯数字开头的 `/n/<id>`
- **时间解析**：
  - "3小时前" / "30分钟前" → 用 `report_date` 当天的相对时间反推
  - "05-14 18:18" → 用 `report_date.year` 补全（跨年时再校正）
  - "X天前" → 减天数
  - 列表层就过滤掉超出 `report_date` 范围的条目

**文章详情**（已实测两篇 sample：1038379 "今日" + 1035371 "本年早些时候" + 2 篇 2018/2019 跨年）：

| 字段 | Selector | 备注 |
|------|---------|------|
| 标题 | `h1.media-title` | 与 `<title>` 一致；不要用 `<title>`（含" - 爱集微"后缀） |
| 作者 | `a.author-item`（可能缺失） | 例 "爱集微"；外稿/聚合稿可能没有 |
| 发布时间（原文） | `span.published-time` | **三种格式都存在**，见下表 |
| 来源机构 | `div.media-source > span:first-child`（文本去前缀"来源："） | 例 "来源：国家级海门经济技术开发区" |
| 标签 | `div.media-source span.media-tag-item` 全部 | 文本形如 `#海门#`、`#中芯国际#`，去 `#` 包围 |
| 正文 | `div.media-article-content` 全部子节点 | 拼接保留段落；图片 `<img src=...>` 提取 alt/url 进 metadata |
| 摘要 fallback | `meta[name="description"]` | content 字段 |
| 关键词 fallback | `meta[name="keywords"]` | 逗号分隔 |
| 文章 ID | URL `/n/(\d+)` | 数字 id 即可作 `article_id` |

**`published-time` 三态时间解析规则**：

| 形态 | 实测样本 | 解析 |
|------|---------|------|
| `N分钟前` / `N小时前` / `N天前` | `"3小时前"` | `crawl_time - timedelta(...)`，按 Asia/Shanghai |
| `MM-DD HH:MM` | `"05-14 18:18"`、`"02-27 07:15"` | 假设当年；若 `MM > current_month` 则年份 -1（跨年回溯） |
| `YYYY-MM-DD` 或 `YYYY-MM-DD HH:MM` | `"2019-09-26"` | 直接 `datetime.strptime` |
| `昨天 HH:MM`（推测有） | 未见样本 | `today - 1day` at HH:MM；写代码时按形态保留兼容分支 |
| 其它 | 罕见 | 返回 None，触发 fallback：HTTP `Last-Modified` 头 → 仍无 → 视为 crawl 当日 |

**列表与详情时间冲突处理**：列表上的"3小时前"和详情上的同字段一致时一律以详情为准；如果详情解析失败，回退到列表时间。

**请求节流**：每秒 1 次

### 6.5 `source_adapter.py::ChipSourceAdapter`

```python
class ChipSourceAdapter(ContentSourceAdapter):
    source_site = "chip"

    def __init__(self, source_config: dict[str, object] | None = None) -> None:
        self._listing_window_days = int((source_config or {}).get("listing_window_days", 1))

    def fetch_listing(self, report_date: date) -> list[RawArticleRef]:
        refs: list[RawArticleRef] = []
        for channel in CHANNELS.values():
            try:
                refs.extend(channel.fetch_listing(channel, report_date))
            except Exception as exc:
                _record_channel_failure(channel.key, exc)
        return self._dedupe(refs)

    def fetch_article(self, ref: RawArticleRef) -> RawArticleDetail:
        return CHANNELS[ref.channel].fetch_article(CHANNELS[ref.channel], ref)

    def normalize_article(self, raw: RawArticleDetail) -> StandardArticle: ...

    def _dedupe(self, refs: list[RawArticleRef]) -> list[RawArticleRef]:
        # 同 channel 内按 URL 去重；跨 channel 由 Step 5 后做语义去重
        ...
```

### 6.6 `src/utils/tools/research/chip_themes.py`（新增共享）

```python
CHIP_THEMES = {
    "新品发布": ["流片", "量产", "首发", "发布会", "新一代", "推出", "上市"],
    "价格变动": ["涨价", "调价", "提价", "降价", "合同价", "长协价", "报价"],
    "产能/扩产": ["扩产", "新厂", "投产", "产能", "建厂", "封顶", "增资"],
    "供需/缺货": ["缺货", "短缺", "供应紧张", "去库存", "下单", "订单"],
    "技术节点突破": ["nm", "EUV", "GAA", "良率", "工艺", "制程"],
}

def classify_chip_themes(article: StandardArticle) -> list[str]:
    """多标签关键词命中分类。"""
```

- Step 1 主题分析 prompt 注入此白名单作为约束
- Step 5 跨 channel 主题去重 + 同主题分桶
- Step 6 简报按主题渲染

### 6.7 跨 channel 去重（Step 5）

- 同 channel 内：URL 完全相同即去重（在 adapter 内做）
- 跨 channel：标题做规范化（去标点 + 全角转半角 + 去括号补充）后计算 Jaccard 相似度（按 2-gram），阈值 0.6
  - 阈值取 0.6 而非 0.85，因为两站点常对同事件用不同标题
  - 命中后保留 SEMI 优先（机构权威）；老姚吧 URL 仍写入 metadata.alt_urls
- 阈值与策略放进 `chip.settings`，方便调

### 6.8 `src/utils/cli.py`（扩展）

新增子命令：

- `run-chip-daily-brief --date YYYY-MM-DD`
- `send-chip-latest-brief-email [--t1-gate]`

### 6.9 `scripts/websearch.py`（小改）

当前直接转发给 `c114.cli.main`。改为按 `--source` 分发：

```python
sources = {
    "c114": "c114.cli",
    "infoq": "infoq.cli",
    "chip": "chip.cli",
}
```

向后兼容：缺省值保持 `c114`。

## 7. 数据流与产物布局

### 7.1 运行目录

- 单日：`output/reports/chip_search_YYYYMMDDHHMM/`
- 区间：`output/reports/chip_range_YYYYMMDD_YYYYMMDD_<timestamp>/`
- 原始 listing：`data/raw/chip_hot_topics_YYYYMMDD.json`，按 channel 分组

### 7.2 步骤产物

| Step | 产物 | 备注 |
|------|------|------|
| 0 | `chip_hot_topics_YYYYMMDD.json` + csv | 两 channel listing 合并，csv 增列 `channel` |
| 1 | `chip_step_1_analysis_YYYYMMDD.yaml` | 主题分析（注入 CHIP_THEMES） |
| 2 | `chip_step_2_search_checklist_YYYYMMDD.yaml` | |
| 3 | `chip_step_3_search_results_YYYYMMDD.yaml` | |
| 4 | `chip_step_4_content_YYYYMMDD.yaml` | |
| 5 | `chip_step_5_content_analysis_YYYYMMDD.yaml` | 跨 channel 主题去重 |
| 6 | `chip_step_6_brief_YYYYMMDD.md` | 按主题分组；每条标注来源 channel |
| 7 | `chip_step_7_review_YYYYMMDD.yaml` | 自动 review |

### 7.3 简报格式（Step 6 草案）

```markdown
# 产业新闻日报 2026-05-19

## 🆕 新品发布
- 【SEMI】格罗方德推出用于 CPO 的硅光子共封装先进光引擎方案 (link)
- 【老姚吧】中芯国际发布 26Q1 财报 单季销售收入超 25 亿美元 (link)

## 💰 价格变动
- 【SEMI】SEMI 报告：2025 全球半导体材料销售额创 732 亿美元新高 (link)
- 【老姚吧】三星研发移动端 HBM 芯片技术（合并自 SEMI 同源） (link)

## 🏭 产能/扩产
- 【老姚吧】南亚新材年产 360 万㎡ IC 载板智能工厂封顶 (link)
- 【老姚吧】中微四川公司增资至 10 亿元 (link)

## 📦 供需/缺货
- （今日无相关信号）

## 🔬 技术节点突破
- 【SEMI】英特尔陈立武：14A 制程 2029 年量产，18A 工艺良率回升 (link)

## 📋 其他动态（未命中主题白名单）
- 【老姚吧】壁仞科技与万达信息达成生态战略合作 (link)
- ...

---
数据来源：SEMI 中国（30 篇） + 老姚吧（67 篇），合并去重后 N 篇。
今日 X channel 数据缺失（如适用）。
```

### 7.4 邮件投递

- 复用 `src/touzifenxi/channels/email.py`
- 新增 `render_chip_brief_email`（`utils/tools/output/email.py`），结构对齐 `render_c114_brief_email`
- 收件人：暂沿用 `chengxusheng@cjhxfund.com`（与 c114 同），可在 settings 中覆写

## 8. 错误处理与降级

| 失败点 | 策略 |
|--------|------|
| 单 channel listing 抓取失败 | 记录 `ops_alerts`，**其他 channel 继续**；简报标注"今日 老姚吧 数据缺失" |
| 两 channel 同时失败 | 跳过当日并发告警 |
| 单篇正文抓取失败 | 跳过该篇；listing 元数据仍进入 Step 1 |
| SEMI 文章日期缺失 | fallback 用 HTTP `Last-Modified` 头；再 fallback 视为今日 |
| 老姚吧相对时间无法解析 | 跳过该条 |
| SEMI 触发 Cloudflare 挑战 | 重试一次延迟 5s；仍失败则当 channel 失败处理 |
| Step 3 搜索 provider 全失败 | 沿用 c114 行为：Step 3 空，下游退化 |
| 跨 channel 去重阈值误杀 | 默认 0.6 偏宽；保留所有原始 URL 在 metadata.alt_urls 中可回溯 |

## 9. 测试策略

- **fixture**：在 `tests/chip/fixtures/` 保存今日实测的 HTML 样本：
  - `semi_home.html`（92KB）
  - `semi_article_intel14a.html`（10KB）
  - `semi_column_hotnews.html`（21KB）
  - `laoyaoba_xinyaowen.html`（84KB）
  - `laoyaoba_article_icboard.html`（32KB，文章 1038379，"3小时前" 格式）
  - `laoyaoba_article_smic.html`（32KB，文章 1035371，"05-14 18:18" 格式）
  - `laoyaoba_article_legacy.html`（文章 729927，"2019-09-26" 跨年格式）
- **Channel 单测**：基于 fixture 断言 `RawArticleRef` 字段抽取、时间解析、排除项过滤
- **Adapter 聚合测试**：mock 两 channel 的 fetch，断言去重 + channel 路由
- **去重测试**：构造"标题相似但 URL 不同"两 channel 文章，断言保留 SEMI、老姚吧 URL 进 metadata.alt_urls
- **主题分类单测**：7-10 篇代表文章覆盖 5 主题桶
- **流水线 smoke**：mock LLM client，跑 Step 0 → Step 6 全链路
- **触网测试**：`@pytest.mark.chip_network`，需要 `CHIP_NETWORK_TEST=1` 才跑

## 10. 安全 / 合规 / 速率

- SEMI 无 robots.txt（404）→ 默认遵守通用爬虫礼仪
- 老姚吧 m. 站 robots 禁 `/api`，本设计**完全不用** `/api`，只用桌面站 SSR 页面
- 请求间隔：每 channel 内 ≥ 1s；详情批量抓取每秒不超过 1 请求
- UA：`touzifenxi-bot/1.0 (compatible; Mozilla/5.0; +internal-research)`
- 不抓登录后内容；不抓用户个人信息字段

## 11. 实施分期

**Phase 1（必做）**
1. fixture 收集（已部分完成于 `/tmp/`，搬到 tests/）
2. `chip_themes.py` 主题层
3. `channels/semi.py` listing + article 抓取 + 单测
4. `channels/laoyaoba.py` listing + article 抓取 + 单测
5. `ChipSourceAdapter` 聚合 + 去重 + 单测
6. CLI 接入 + `scripts/websearch.py` 分发
7. 邮件模板 + 简报渲染
8. smoke 流水线测试

**Phase 2（可选）**
1. Playwright 兜底（仅当 SEMI 触发 bot fight 才需要）
2. 多日聚合周报（参照 `c114/weekend_brief_merge.py`）
3. 接入第三个 channel（如 TrendForce 中文版）—— 仅新增 `channels/<new>.py`

## 12. 风险（更新版）

| 风险 | 概率 | 缓解 |
|-----|------|------|
| SEMI 列表无日期，详情阶段日期解析失败导致全部归到"今日" | 中 | fallback 用 `Last-Modified`；超过 N 篇异常自动告警 |
| 老姚吧列表"3小时前"在跨日抓取时归错 day | 中 | 抓取时记录当前 wall-clock 作为参照；区间抓取时用 `report_date.startofday + 24h` 卡 |
| 老姚吧列表混入活动广告 / 版权声明 | 高 | EXCLUDED_IDS + 标题长度过滤 + 详情页二次校验 |
| 跨 channel 去重阈值 0.6 误杀 | 中 | 实测调；保留 alt_urls 防丢信号 |
| SEMI Cloudflare 升级到 bot fight | 低 | Phase 2 上 playwright |
| 主题关键词召回低 | 中 | Step 1 LLM 二次分类兜底（已在共享 facade 内） |

## 13. 验收

- `touzifenxi run-chip-daily-brief --date <昨天>` 跑通，产出单份 step6 markdown
- 简报中"新品发布""价格变动"必出独立分组（即使为空也显示"今日无相关信号"）
- 简报每条标注来源 channel（【SEMI】/【老姚吧】）
- 单 channel 失败时简报继续生成，并明确标注"今日 X 数据缺失"
- 邮件能正常投递（仅一封）
- `ruff check src` 干净
- `pytest tests/chip` 全绿
- 触网测试 `CHIP_NETWORK_TEST=1 pytest -m chip_network` 通过

## 14. 组件复用盘点（实读代码后）

实测结果：**InfoQ 是真正的 lean 模板**——只有 3 个文件、不走 `run_source_daily_pipeline`，而是在自己的 CLI 里按步骤直接调 facades。`run_source_daily_pipeline` 虽名字"源无关"，**实际仍硬编码 c114** （`facade.load_c114_runtime_config`、`facade.create_c114_range_day_directories`）。本设计沿用 InfoQ 路径，**不走** `run_source_daily_pipeline`。

### 14.1 ✅ 直接复用（零改动）

| 组件 | 位置 | 复用方式 |
|------|------|---------|
| `ContentSourceAdapter` ABC | `utils/tools/content_models.py` | `class ChipSourceAdapter(ContentSourceAdapter)` 继承 |
| `RawArticleRef / RawArticleDetail / StandardArticle` | 同上 | 数据契约 |
| `load_c114_runtime_config` | `c114/runtime/config.py` | InfoQ/kr36 都直接复用；本质是全局 runtime config，名字是历史包袱 |
| `StructuredChatClient` | `utils/tools/llm` | LLM 客户端 |
| `StepCheckpointStore` / `checkpoint_path_for_step` | `utils/tools/runtime/checkpoint.py` | 步骤断点续跑 |
| `run_search_workflow` / `save_search_results` / `SearchTraceLogger` | `utils/tools/search/workflow.py` | Step 3 |
| `run_content_fetch_workflow` / `save_content_results` | `facades/content.py` | Step 4 |
| `analyze_daily_articles` / `auto_group_analysis_topics` / `write_analysis_outputs` | `facades/intelligence.py` | Step 1 |
| `auto_complete_content_analysis` / `generate_brief_markdown` / `generate_layer_issues` / `save_content_analysis_yaml` / `save_layer_issues_yaml` | `facades/content_analysis.py` | Step 5/6 |
| `build_search_checklist_items` / `build_search_checklist_sections` / `apply_step2_checkpoint_results` | `facades/intelligence.py` | Step 2 |
| `build_step_file_name` | `facades/intelligence.py` | 步骤文件名拼接（接受自定义 prefix） |
| `write_step1_csv` | `utils/tools/output/briefing.py` | Step 1 CSV 输出 |
| `src/touzifenxi/channels/email.py` | 同上 | SMTP 邮件发送 |
| `bind_llm_trace_log` / `LLM_TRACE_LOG_DIR_NAME` | `facades/intelligence.py` | 轨迹日志 |
| `httpx` + `BeautifulSoup` 抓取栈 | 第三方 | c114/infoq 都用，依赖已就绪 |

### 14.2 🔧 包薄壳后复用（小改 / 复制 InfoQ 形态）

| 组件 | 改造方式 |
|------|---------|
| **`render_c114_brief_email`** (`utils/tools/output/email.py`) | 标题已经从 Markdown H1 解析、结构按 `##`/`###` 通用；**只有 Footer 用了常量 `C114_EMAIL_FOOTER_DISCLAIMER`**。两条路：①copy 出 `render_chip_brief_email` + 自有 footer；②把现有 fn 参数化（`footer_disclaimer=` 默认 c114）。**推荐 ②**，零回归且未来 kr36/infoq 也能用 |
| **`create_search_run_directory`** (`facades/intelligence.py`) | 内部用 `c114_reports_root(reports_dir)` 拼到 `reports_dir/c114_report/...`。InfoQ 已经自实现 `create_run_directory`。chip 同样**自实现一个 `create_chip_run_directory`** 写到 `reports_dir/chip_report/`，与 c114 隔离 |
| **`step_1_analysis_name` ... `step_6_brief_name`** | 都绑死 `c114_*_PREFIX` 模块级常量。InfoQ 的方案是在自己 cli.py 里定义同名函数 + 自己的 `STEP_N_PREFIX` 常量。**chip 跟 InfoQ 同样处理**，不去改 facades |
| **`provider_stats_name`** | 已经接受 `source_prefix=` 参数；传 `"chip"` 即可 |
| **`raw_csv_path` / `RAW_JSON_PREFIX` 等** | 在 `src/chip/cli.py` 中定义自己的 `RAW_JSON_PREFIX = "chip_hot_topics"`、`RAW_CSV_NAME = "chip_hot_topics.csv"` —— 跟 InfoQ 完全对称 |

### 14.3 🚧 净新增（必须从零写）

| 组件 | 估量 | 说明 |
|------|------|------|
| `src/chip/channels/spec.py` | 小 | `ChipChannelSpec` dataclass + 注册表 |
| `src/chip/channels/semi.py` | 中 | SEMI 列表 + 详情解析。selector 已踩点：`h2.col-lg-8`、`div.single-post-content`、`div.ct-p.ct-bzjy` |
| `src/chip/channels/laoyaoba.py` | 中 | 老姚吧列表 + 详情解析。详情页 selector 仍需踩第二遍（已抓 sample html，未完整解析） |
| `src/chip/source_adapter.py` (`ChipSourceAdapter`) | 小 | channel 路由 + 同 channel URL 去重 + 异常隔离 |
| `src/chip/cli.py` | 中 | 拷贝 `src/infoq/cli.py` 改前缀；调用 facades |
| `src/chip/settings.py` | 小 | 拷贝 `src/infoq/settings.py` 改前缀 |
| `utils/tools/research/chip_themes.py` | 小 | `CHIP_THEMES` dict + `classify_chip_themes` |
| Step 5 跨 channel 标题去重 helper | 小 | 已有 `title_similarity` (`facades/content.py`)，写一个组合调用即可 |
| 简报渲染按主题分桶 | 小 | 复用 `generate_brief_markdown` 后做后处理，或在 Step 5 把主题写进 section title |
| CLI 子命令 `run-chip-daily-brief` / `send-chip-latest-brief-email` (`src/utils/cli.py`) | 小 | 参照 `run-c114-daily-brief` 同结构 |
| `scripts/websearch.py` `--source` 分发 | 小 | 当前硬转 c114，改为 dict 分发 |
| fixture HTML 样本 | 小 | 已在 `/tmp/` 抓到 SEMI 主页/文章/栏目页 + 老姚吧 xinyaowen，复制到 `tests/chip/fixtures/` |

### 14.4 ❌ 明确不复用 / 不沿用

| 组件 | 原因 |
|------|------|
| `run_source_daily_pipeline` (`orchestration/pipeline_engine.py`) | 名义源无关、实际硬编码 c114 facade 方法；InfoQ 自己也绕过；本设计不进这条岔路，避免给它捎带 chip 改动 |
| `src/utils/tools/orchestration/c114/*` 13 个子模块 | 都是 c114 私有重型实现；InfoQ-lean 模板不需要 |
| `is_c114_article_url` / `infer_home_topic_from_url` (`facades/intelligence.py`) | URL 形态 c114 专用；chip 各 channel 自己判断 |
| `extract_c114_article_text` / `strip_c114_shell_sections` (`facades/content.py`) | c114 HTML 壳剥离专用；chip 用自己的 selector |
| `m.laoyaoba.com` SPA + `/api` | robots disallow；桌面站 SSR 已经够 |
| `https://www.semi.org.cn/site/share/semi/newslist.html` | 实测为 SPA 壳，零数据 |

### 14.5 工作量摘要

按 LOC 粗估：

- **复用（零代码）**：~3500 行 facades + 邮件渠道 + 抓取工具栈，相当于"白送"
- **小改（参数化 `render_c114_brief_email`）**：< 20 行
- **net new 业务代码**：
  - `chip/cli.py`：~400 行（参照 infoq/cli.py 同量级）
  - `chip/source_adapter.py`：~100 行
  - `chip/settings.py`：~50 行
  - `chip/channels/semi.py`：~200 行
  - `chip/channels/laoyaoba.py`：~200 行
  - `chip/channels/spec.py`：~50 行
  - `chip_themes.py`：~80 行
  - CLI 接入 + websearch.py 改动：~30 行
  - 单测：~600 行
  - **合计净新增 ~1700 行**，其中 ~1100 行业务、~600 行测试

### 14.6 落地推荐顺序（在 writing-plans 阶段细化）

1. fixture 抓取 + `tests/chip/conftest.py`（**先把测试基础设施搭起来**）
2. `chip/settings.py` + `chip/__init__.py`
3. `chip/channels/semi.py` + 单测（基于 fixture）
4. `chip/channels/laoyaoba.py` + 单测（selector 与时间解析规则已写在 spec 6.4）
5. `chip/source_adapter.py` + 单测
6. `chip_themes.py` + 单测
7. `chip/cli.py`（拷 `infoq/cli.py` → 改前缀 + 接 `ChipSourceAdapter`）
8. 参数化 `render_c114_brief_email`（或新写 `render_chip_brief_email`）
9. `src/utils/cli.py` 新增 `run-chip-daily-brief` 子命令
10. `scripts/websearch.py` 改 `--source` 分发
11. smoke 流水线（touch live LLM + 实抓站点）
12. 邮件发送链路（接收人沿用 c114）
