"""FeedCore 美国国际新闻 profile（英文源、T-1 数据窗口，v17: 4 桶均衡 10 源 / 共 40 源，全 GN 宽频高更新）。

定位（4 大类各 10 源 = 40 源，全中高价值）[v16 精简]:
  1. 国际形势      10 源
  2. 人工智能与科技 10 源
  3. 金融市场与宏观 10 源
  4. 财经信息      10 源

[v15] 全部 60 源统一为 Google News 关键字搜索（news.google.com/rss/search?q=...），
  不再混用外部 publisher 直链（原 15 个官方直链如 NPR/BBC/Ars/CNBC/Fed/Yahoo/Fortune 等
  已逐一替换为对应主题的 GN 关键字源，逐条见下方 [v15 全GN] 注释）。

v3 改动（vs v2，每桶补 2-3 个新 GN 主题，T-1 实测）:
  国际 +3:  NATO/G7 (56) / Iran 朝核 (28) / Pentagon DoD (31)
  AI   +2:  机器人 (43) / 数据中心 GPU (4)
  金融 +2:  海外央行 ECB/BOE/BOJ (45) / 关税 tariffs (9)
  财经 +3:  财报超预期 beat/miss (40) / 回购 buyback (14) / 信用评级 Moody's (33)

v2 vs v1 历史改动:
  剔除 15 个低价值源（CNN/Al Jazeera/Vox/TechCrunch/The Verge/IEEE/MarketWatch/
                    Business Insider/Quartz/SEC官方/GN美债收益率/GN经济衰退/
                    GN 选股/GN M&A(旧)/GN IPO(旧)）。
  补充 15 个 T-1≥10 新源（Putin/EU/印太/OpenAI/终端/AImodel/华尔街/加密/石油/黄金/
                       投行/大公司财报/SEC执法/并购/IPO定价）。

执行链路与 ``feedcore_general_news.py`` 同款：
  1) 远端 fetcher 完成 step 1-3（选源 / 抓 RSS / 抓正文）
  2) 本地从 step 4 起按 FeedCore 既有 workflow 跑完四要素分析 / 动态子类 / 简报渲染

T-1 数据窗口由 ``src/feedcore/rss.py::_filter_t_minus_one_window`` 自动应用，
profile 层无需额外配置。

产物落点: ``output/reports/feedcore_report/<run_id>/``，最终简报 brief.md / brief.html。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import concurrent.futures as _cf
import dataclasses as _dataclasses
import json as _json
import re as _re
import time as _time

from feedcore.article_fetcher import ArticleClient  # noqa: E402
from feedcore.config import AppConfig, BrowserConfig, ModelConfig, WorkflowConfig  # noqa: E402
from feedcore.env import load_env_file  # noqa: E402
from feedcore.openai_compatible_client import OpenAICompatibleClient  # noqa: E402
from feedcore.pipeline import RequestsRssClient  # noqa: E402
from feedcore.remote_client import fetch_remote_artifact  # noqa: E402
from feedcore.workflow.orchestrator import WorkflowClients, run_workflow_from_prefetched  # noqa: E402


# --- Step 2.5: RSS 层粗筛 ---------------------------------------------------
# v6 阈值调整：基于 v5 实测召回率分析，阈值 25 漏掉了 14% 的 v4-keep 文章
# (主要是 score 10-24 区间的地缘政治深度评论)。调到 15 可把召回率从 64.1% 提到 74.4%，
# drop 数量从 280 降到 ~140，下游负载略增但仍能砍掉明显垃圾。
PREFILTER_SCORE_THRESHOLD = 15
PREFILTER_MAX_CONCURRENCY = 12


def _build_prefilter_prompt(item: dict) -> str:
    a = item.get("article", {}) or {}
    desc = (a.get("description") or "")[:300]
    return f"""You are a buy-side researcher screening RSS feed items.
Based on title + short description only (no full article yet), decide whether this is worth deep analysis.

Title: {a.get("title", "")}
Description: {desc}
Default category: {item.get("default_category", "")}

Score 0-100 reflecting investment-research value:
- 60+: concrete business/finance/policy/tech news with named entity, hard numbers, or material event
- 25-59: borderline / partial signal (vague headline but plausible relevance)
- 0-24: soft news / opinion / lifestyle / listicle / pure local color / commemoration

Return strict JSON only: {{"score": <int>, "reason": "<one short sentence>"}}"""


def _parse_score_from_response(raw: str) -> int:
    txt = (raw or "").strip()
    # strip markdown code fence if present
    m = _re.search(r"```(?:json)?\s*(\{.*?\})\s*```", txt, _re.S)
    if m:
        txt = m.group(1)
    # 找到第一个 JSON 对象
    m = _re.search(r"\{[^{}]*\"score\"[^{}]*\}", txt, _re.S)
    if m:
        try:
            return int(_json.loads(m.group(0)).get("score", 50))
        except Exception:
            pass
    # 兜底：解析失败给保守分（保留）
    return 50


def _cap_per_source(artifact, max_per_source: int = 15):
    """每个 RSS source 最多保留 max_per_source 条 feed_items (按 pub_date 取最新)。

    防止 GN 关键词搜索 (100+ 条/源) 信号密度过高,垄断 4 桶平衡。
    NPR/BBC 等官方 RSS 本身就 ~10-30 条不受影响。
    """
    by_feed: dict[str, list[dict]] = {}
    order: list[str] = []
    for item in artifact.feed_items:
        article = item.get("article", {}) or {}
        feed_url = article.get("feed_url", "") or article.get("source", "") or "_unknown_"
        if feed_url not in by_feed:
            by_feed[feed_url] = []
            order.append(feed_url)
        by_feed[feed_url].append(item)

    kept_items: list[dict] = []
    kept_urls: set[str] = set()
    dropped = 0
    for feed_url in order:
        # 按 pub_date 倒序排,取最新 max_per_source 条
        group = by_feed[feed_url]

        def _pub_key(it):
            return (it.get("article", {}) or {}).get("pub_date", "") or ""
        group.sort(key=_pub_key, reverse=True)
        kept = group[:max_per_source]
        dropped += max(0, len(group) - len(kept))
        for it in kept:
            kept_items.append(it)
            link = (it.get("article", {}) or {}).get("link", "")
            if link:
                kept_urls.add(link)

    # 同步过滤 article_contents
    if kept_urls:
        kept_contents = [
            c for c in artifact.article_contents
            if (c.get("article", {}) or {}).get("link", "") in kept_urls
        ]
    else:
        kept_contents = list(artifact.article_contents)

    print(
        f"[cap-per-source] 每源上限 {max_per_source}: "
        f"feed_items {len(artifact.feed_items)} → {len(kept_items)} (drop {dropped}) | "
        f"article_contents {len(artifact.article_contents)} → {len(kept_contents)}"
    )
    return _dataclasses.replace(artifact, feed_items=kept_items, article_contents=kept_contents)


def step2_5_rss_prefilter(
    feed_items: list[dict],
    article_contents: list[dict],
    client: OpenAICompatibleClient,
    *,
    score_threshold: int = PREFILTER_SCORE_THRESHOLD,
    max_concurrency: int = PREFILTER_MAX_CONCURRENCY,
    output_dir: Path | None = None,
    run_id: str = "",
) -> tuple[list[dict], list[dict], dict]:
    """
    Step 2.5: 用 flash 模型基于 (title + description) 打粗筛分；低于阈值的 feed_item
    直接丢弃，对应的 article_contents 也同步剔除。

    返回 (kept_feed_items, kept_article_contents, stats)
    其中 stats 含 keep/drop 计数 + 耗时 + 单价估算，便于跑后数据验证。

    打分明细落盘到 output_dir/<run_id>/step2_5_prefilter_scores.json，便于人审。
    """
    t0 = _time.monotonic()
    n = len(feed_items)
    scores: list[int] = [50] * n
    errors = 0

    def _score_one(idx_item):
        idx, item = idx_item
        try:
            raw = client.chat(
                _build_prefilter_prompt(item),
                system_prompt="Strict JSON scorer. Use the provided text only.",
                use_flash=True,
            )
            return idx, _parse_score_from_response(raw), None
        except Exception as exc:
            return idx, 50, str(exc)[:80]

    print(f"[step2.5] 粗筛 {n} feed_items via flash (并发 {max_concurrency}, 阈值 {score_threshold}) ...")
    with _cf.ThreadPoolExecutor(max_workers=max_concurrency) as ex:
        for idx, score, err in ex.map(_score_one, enumerate(feed_items)):
            scores[idx] = score
            if err:
                errors += 1

    keep_links: set[str] = set()
    kept_items: list[dict] = []
    score_records: list[dict] = []
    for idx, item in enumerate(feed_items):
        s = scores[idx]
        article = item.get("article", {}) or {}
        link = article.get("link", "") or ""
        decision = "keep" if s >= score_threshold else "drop"
        if decision == "keep":
            kept_items.append(item)
            if link:
                keep_links.add(link)
        score_records.append({
            "link": link,
            "title": (article.get("title") or "")[:120],
            "default_category": item.get("default_category", ""),
            "score": s,
            "decision": decision,
        })

    # 同步过滤 article_contents（按 link 匹配；keep_links 为空时保守全留以防关联失败）
    if keep_links:
        kept_contents = [
            c for c in article_contents
            if (c.get("article", {}) or {}).get("link", "") in keep_links
        ]
    else:
        kept_contents = list(article_contents)

    elapsed = _time.monotonic() - t0
    stats = {
        "input_feed_items": n,
        "kept_feed_items": len(kept_items),
        "dropped_feed_items": n - len(kept_items),
        "input_article_contents": len(article_contents),
        "kept_article_contents": len(kept_contents),
        "errors": errors,
        "elapsed_seconds": round(elapsed, 1),
        "score_threshold": score_threshold,
    }

    if output_dir and run_id:
        out_dir = Path(output_dir) / run_id
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "step2_5_prefilter_scores.json").write_text(
            _json.dumps(score_records, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out_dir / "step2_5_prefilter_stats.json").write_text(
            _json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print(
        f"[step2.5] 完成 {elapsed:.1f}s | feed_items {n}→{len(kept_items)} "
        f"(drop {n - len(kept_items)}, errors {errors}) | "
        f"article_contents {len(article_contents)}→{len(kept_contents)}"
    )
    return kept_items, kept_contents, stats

OUTPUT_DIR = ROOT / "output" / "reports" / "feedcore_report"

_DEFAULT_REMOTE_FETCHER_URL = "http://81.69.47.226:3000"

# ---------------------------------------------------------------------------
# 50 个全免费英文 RSS 源（已 curl 核实可达；2026-05-20 抓取窗口验证 200 + 数据）
# ---------------------------------------------------------------------------

RSS_URLS: list[str] = [
    # ─────────────────────── 1. 国际形势 (10) ─────────────────────────
    "https://news.google.com/rss/search?q=%22tariffs%22+OR+%22trade+war%22&hl=en-US&gl=US&ceid=US:en",  # 关税/贸易战（宽频）[v17]
    "https://news.google.com/rss/search?q=%22sanctions%22+OR+%22export+controls%22&hl=en-US&gl=US&ceid=US:en",             # 制裁/出口管制 [v15 全GN 替 ABC]
    "https://news.google.com/rss/search?q=%22China%22+OR+%22Beijing%22&hl=en-US&gl=US&ceid=US:en",                                  # 中国/北京 宏观（宽频）[v17]
    "https://news.google.com/rss/search?q=Russia+Ukraine+War&hl=en-US&gl=US&ceid=US:en",                                  # 俄乌战争
    "https://news.google.com/rss/search?q=Middle+East+Conflict&hl=en-US&gl=US&ceid=US:en",                                # 中东冲突
    "https://news.google.com/rss/search?q=%22State+Department%22+OR+%22White+House%22&hl=en-US&gl=US&ceid=US:en",         # 国务院+白宫
    "https://news.google.com/rss/search?q=%22European+Union%22+OR+%22Brussels%22&hl=en-US&gl=US&ceid=US:en",        # 欧盟/布鲁塞尔（宽频）[v17]
    "https://news.google.com/rss/search?q=%22Indo-Pacific%22+OR+%22Taiwan%22&hl=en-US&gl=US&ceid=US:en",                  # 印太/台湾
    "https://news.google.com/rss/search?q=%22Iran+nuclear%22+OR+%22North+Korea%22&hl=en-US&gl=US&ceid=US:en",             # 伊朗/朝核扩散 [v3]
    "https://news.google.com/rss/search?q=%22Trump%22&hl=en-US&gl=US&ceid=US:en",  # Trump 综合（宽频）[v17]

    # ─────────────────────── 2. 人工智能与科技 (10) ───────────────────
    "https://news.google.com/rss/search?q=%22generative+AI%22+OR+%22AI+agents%22&hl=en-US&gl=US&ceid=US:en",               # 生成式 AI/智能体 [v15 全GN 替 OpenAI Blog]
    "https://news.google.com/rss/search?q=Anthropic+OR+%22Claude+AI%22&hl=en-US&gl=US&ceid=US:en",                        # Anthropic / Claude
    "https://news.google.com/rss/search?q=%22Artificial+Intelligence%22&hl=en-US&gl=US&ceid=US:en",                       # AI 综合
    "https://news.google.com/rss/search?q=%22AI+Chips%22+OR+Nvidia&hl=en-US&gl=US&ceid=US:en",                            # AI 芯片
    "https://news.google.com/rss/search?q=Microsoft+OR+Apple+OR+Google+OR+Meta&hl=en-US&gl=US&ceid=US:en",                # 大科技公司
    "https://news.google.com/rss/search?q=AI+Regulation+OR+AI+Safety&hl=en-US&gl=US&ceid=US:en",                          # AI 监管
    "https://news.google.com/rss/search?q=%22OpenAI%22+OR+%22ChatGPT%22+OR+%22GPT-5%22&hl=en-US&gl=US&ceid=US:en",        # OpenAI / ChatGPT 动态
    "https://news.google.com/rss/search?q=%22AI+model%22+OR+%22foundation+model%22+OR+%22LLM%22&hl=en-US&gl=US&ceid=US:en",            # 模型发布
    "https://news.google.com/rss/search?q=%22robotics%22+OR+%22humanoid+robot%22&hl=en-US&gl=US&ceid=US:en",              # 机器人/具身智能 [v3]
    "https://news.google.com/rss/search?q=%22data+center%22+OR+%22GPU+shortage%22&hl=en-US&gl=US&ceid=US:en",             # 数据中心/算力供需 [v3]

    # ─────────────────────── 3. 金融市场与宏观 (10, v12 重排) ───────────────────
    # v12 改动: 替换 5 个低信号源 (investing strategy / nonfarm / trade tariffs / Bitcoin / ECB海外央行)
    # 为 5 个金融细分关键词,覆盖宏观/利率/消费/地产/波动率
    "https://news.google.com/rss/search?q=%22GDP%22+OR+%22economic+growth%22+OR+%22recession%22&hl=en-US&gl=US&ceid=US:en",  # GDP/增长/衰退 [v15 全GN 替 CNBC]
    "https://news.google.com/rss/search?q=%22interest+rate%22+OR+%22rate+cut%22+OR+%22monetary+policy%22&hl=en-US&gl=US&ceid=US:en",  # 利率/货币政策 [v15 全GN 替 Federal Reserve]
    "https://news.google.com/rss/search?q=%22Bitcoin%22+OR+%22cryptocurrency%22&hl=en-US&gl=US&ceid=US:en",            # 加密货币（宽频）[v17]
    "https://news.google.com/rss/search?q=%22Federal+Reserve%22+OR+Powell&hl=en-US&gl=US&ceid=US:en",                     # 美联储/鲍威尔
    "https://news.google.com/rss/search?q=CPI+OR+%22inflation+data%22&hl=en-US&gl=US&ceid=US:en",                         # CPI/通胀
    "https://news.google.com/rss/search?q=%22S%26P+500%22+OR+Nasdaq&hl=en-US&gl=US&ceid=US:en",                           # 标普/纳斯达克
    "https://news.google.com/rss/search?q=%22crude+oil%22+OR+%22Brent%22+OR+%22WTI%22&hl=en-US&gl=US&ceid=US:en",         # 石油/能源
    "https://news.google.com/rss/search?q=%22Wall+Street%22+OR+%22Dow+Jones%22&hl=en-US&gl=US&ceid=US:en",       # 华尔街/道指（宽频）[v17]
    "https://news.google.com/rss/search?q=%22housing+starts%22+OR+%22home+sales%22&hl=en-US&gl=US&ceid=US:en",            # 房地产 [v12 NEW]
    "https://news.google.com/rss/search?q=%22stock+market%22+OR+%22stocks%22&hl=en-US&gl=US&ceid=US:en",                # 股市大盘（宽频）[v17]

    # ─────────────────────── 4. 财经信息 (10) ─────────────────────────
    "https://news.google.com/rss/search?q=%22Tesla%22+OR+%22Nvidia%22+OR+%22Amazon%22&hl=en-US&gl=US&ceid=US:en",  # 大盘公司 Tesla/Nvidia/Amazon（宽频）[v17]
    "https://news.google.com/rss/search?q=%22bank%22+OR+%22banking%22&hl=en-US&gl=US&ceid=US:en",  # 银行业（宽频）[v17]
    "https://news.google.com/rss/search?q=%22earnings+report%22+OR+%22Q1+earnings%22&hl=en-US&gl=US&ceid=US:en",          # 财报季
    "https://news.google.com/rss/search?q=%22CEO%22+OR+%22executive%22&hl=en-US&gl=US&ceid=US:en",  # CEO/高管（宽频）[v17]
    "https://news.google.com/rss/search?q=%22dividend%22+OR+%22share+buyback%22&hl=en-US&gl=US&ceid=US:en",              # 股息/回购（宽频）[v17]
    "https://news.google.com/rss/search?q=%22layoffs%22+OR+%22job+cuts%22&hl=en-US&gl=US&ceid=US:en",        # 裁员/职位削减（宽频）[v17]
    "https://news.google.com/rss/search?q=%22Goldman+Sachs%22+OR+%22Morgan+Stanley%22+OR+%22JPMorgan%22&hl=en-US&gl=US&ceid=US:en",  # 投行动态
    "https://news.google.com/rss/search?q=%22merger%22+OR+%22acquisition%22&hl=en-US&gl=US&ceid=US:en", # 并购（宽频）[v17]
    "https://news.google.com/rss/search?q=%22IPO%22&hl=en-US&gl=US&ceid=US:en",  # IPO（宽频）[v17]
    "https://news.google.com/rss/search?q=%22credit+rating%22+OR+%22Moody%27s%22+OR+%22downgrade%22&hl=en-US&gl=US&ceid=US:en",  # 信用评级 [v3]
]

assert len(RSS_URLS) == 40, f"expected 40 sources, got {len(RSS_URLS)}"


def _interleave_buckets(urls: list[str]) -> list[str]:
    """把 RSS_URLS 按 4 桶交错重排，让前 N 个 quick_sample_size 涵盖 4 类。

    Why: v7 实测发现 quick_sample_size=10 只取前 10 个 RSS_URLS，结果全是
    国际形势桶（0-14），AI/金融/财经 完全没被采样 — 导致最终 brief 里
    "AI 资本与前沿研究" 只有 1 篇。交错后前 10 = 国际×3 + AI×3 + 金融×2 + 财经×2。
    """
    bucket_size = len(urls) // 4
    buckets = [urls[i * bucket_size : (i + 1) * bucket_size] for i in range(4)]
    out: list[str] = []
    for i in range(bucket_size):
        for b in buckets:
            if i < len(b):
                out.append(b[i])
    return out


RSS_URLS = _interleave_buckets(RSS_URLS)
assert len(RSS_URLS) == 40, "interleave should preserve count"


CONFIG = AppConfig(
    rss_urls=RSS_URLS,
    output_dir=OUTPUT_DIR,
    max_articles=None,
    article_fetch_timeout_seconds=180,
    similar_article_threshold=0.7,
    require_substantive_four_dimensions=True,
    # 实测(fetch_20260525050643)证明: 取样=10 时 step5 有 90% 小类是孤儿(单篇),
    # 渲染层 step6/step9 的去碎片闸门会把每个大类压成 1 个小类; 模拟显示即便把
    # min_sources_per_topic 降到 1 也只从 3→4 个小类 — 病根是数据太薄而非阈值。
    # 故取全部 60 源喂量, 让同主题被多源覆盖、孤儿率下降, 小类才能自然成形。
    quick_sample_size=None,
    # 但全开 60 源、每个 GN 关键词源 ~100 条 → 5694 篇,远程 fetcher 逐篇抓正文会被拖死
    # (fetch_20260525062948 实测卡死 504)。在远程抓正文之前按源砍到 20 条:
    # 60 源 × ≤20 = ≤1200 篇,既限总量又保每类均衡(15 源 × ≤20 ≈ ≤300/类)。
    max_articles_per_source=10,
    model=ModelConfig(enabled=True, timeout=120),
    workflow=WorkflowConfig(
        # v7 优化: 之前 type_classification/synthesis 并发=3 是新瓶颈
        # (article_four_dims/research_score 已 11.6x 并发，但 type_* 仅 3x → 各占 14-15 min wall)
        rss_concurrency=10,
        article_fetch_concurrency=12,
        model_concurrency=12,
        type_classification_concurrency=12,   # 3 → 12: 解锁 type 分类并发
        type_synthesis_concurrency=12,        # 3 → 12: 解锁 type 合成并发
    ),
    browser=BrowserConfig(enabled=True, save_html=False, save_pdf=False, timeout=60),
)


def main() -> None:
    load_env_file(ROOT / ".env")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    base_url = (os.environ.get("FEEDCORE_REMOTE_FETCHER_URL") or _DEFAULT_REMOTE_FETCHER_URL).rstrip("/")
    token = os.environ.get("FEEDCORE_FETCHER_TOKEN", "").strip()

    print(f"[us_news] {len(CONFIG.rss_urls)} RSS sources, fetching via {base_url}")

    artifact = fetch_remote_artifact(
        config=CONFIG,
        base_url=base_url,
        token=token,
        output_root=OUTPUT_DIR,
    )

    # 远程已在抓正文前按源砍到 max_articles_per_source(20),这里是本地安全网,
    # 与远程上限对齐(20),正常情况下是 no-op;若直连未限流的 fetcher 仍能兜底。
    artifact = _cap_per_source(artifact, max_per_source=20)

    # Step 2.5: RSS 层粗筛 — 用 flash 看 title+description 打分，丢掉低价值文章，
    # 大幅减少下游 Step 4a research_score + Step 4b article_four_dims 的工作量。
    summary_client = OpenAICompatibleClient(timeout=CONFIG.model.timeout)
    kept_items, kept_contents, prefilter_stats = step2_5_rss_prefilter(
        feed_items=artifact.feed_items,
        article_contents=artifact.article_contents,
        client=summary_client,
        output_dir=OUTPUT_DIR,
        run_id=artifact.run_id,
    )
    artifact = _dataclasses.replace(artifact, feed_items=kept_items, article_contents=kept_contents)

    clients = WorkflowClients(
        rss=RequestsRssClient(),
        article=ArticleClient(
            browser_enabled=CONFIG.browser.enabled,
            document_dir=None,
            save_html=False,
            save_pdf=False,
            browser_timeout=CONFIG.browser.timeout,
        ),
        summary=summary_client,
        translator=None,
    )
    result = run_workflow_from_prefetched(
        output_dir=OUTPUT_DIR,
        clients=clients,
        rss_sources=artifact.rss_sources,
        feed_items=artifact.feed_items,
        article_contents=artifact.article_contents,
        run_id=artifact.run_id,
        model_concurrency=CONFIG.workflow.model_concurrency,
        type_classification_concurrency=CONFIG.workflow.type_classification_concurrency,
        type_synthesis_concurrency=CONFIG.workflow.type_synthesis_concurrency,
    )
    print(f"[us_news] Wrote workflow outputs to {result.run_dir}")


if __name__ == "__main__":
    main()
