from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .brief_editorial_policy import AGGREGATE_BRIEF_EXTRA, FOUR_DIMENSION_QUALITY
from .env import load_env_file
from .openai_compatible_client import OpenAICompatibleClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an AI brief from an existing articles.json file.")
    parser.add_argument("--input", default="output/articles.json", help="Path to existing articles.json.")
    parser.add_argument("--output", default="output/ark_brief.md", help="Path to write the generated brief.")
    parser.add_argument("--max-chars-per-article", type=int, default=5000)
    parser.add_argument("--timeout", type=int, default=300, help="Request timeout in seconds.")
    parser.add_argument("--max-tokens", type=int, default=3000, help="Maximum output tokens.")
    args = parser.parse_args()

    load_env_file(Path(".env"))
    input_path = Path(args.input)
    output_path = Path(args.output)
    items = json.loads(input_path.read_text(encoding="utf-8"))
    filtered_items = filter_items_with_extracted_content(items)
    print(f"Using {len(filtered_items)} articles with extracted content; skipped {len(items) - len(filtered_items)}.")
    prompt = build_existing_articles_prompt(items, max_chars_per_article=args.max_chars_per_article)
    system = (
        "你是专业中文新闻编辑，负责基于多篇已抓取正文撰写综合简报；须严格依据所给材料，不得编造未出现的实体、数字与结论；"
        "每一条归纳须能在下文某篇「正文」中找到出处，禁止空穴来风。\n"
        + FOUR_DIMENSION_QUALITY
        + "\n"
        + AGGREGATE_BRIEF_EXTRA
    )
    brief = OpenAICompatibleClient(timeout=args.timeout, max_tokens=args.max_tokens).chat(
        prompt, system_prompt=system
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(brief.strip() + "\n", encoding="utf-8")
    print(f"Wrote AI brief to {output_path}")


def build_existing_articles_prompt(items: list[dict[str, Any]], max_chars_per_article: int = 5000) -> str:
    items = filter_items_with_extracted_content(items)
    if not items:
        raise ValueError("No articles with extracted content were found.")

    article_blocks = []
    for index, item in enumerate(items, start=1):
        article = item.get("article", {})
        content = str(item.get("content") or article.get("description") or "")[:max_chars_per_article]
        article_blocks.append(
            "\n".join(
                [
                    f"## Article {index}",
                    f"标题：{article.get('title', '')}",
                    f"来源：{article.get('source', '')}",
                    f"发布时间：{article.get('pub_date', '')}",
                    f"链接：{article.get('link', '')}",
                    f"抓取错误：{item.get('fetch_error') or '无'}",
                    "正文：",
                    content,
                ]
            )
        )

    return "\n\n".join(
        [
            "请基于以下已抓取新闻生成一份中文综合简报。",
            "要求：",
            "1. 一级分类（按主体/主题）只能使用下列之一，并用二级标题 ## 分类名 分段："
            "国际形势与地缘政治、美国政治与政策、人工智能与科技、金融市场与宏观、产业与公司、社会与其它。",
            "2. 在同一分类下，按事件或议题分子章节；每个事件用 #### 事实、#### 背景、#### 产生的影响、#### 反面观点 / 数据矛盾点 四维撰写；**四要素缺一不可**，不得以单句「未涉及/需核实/未见矛盾」敷衍任一节；若某条材料无法同时支撑完整四维，**不要写该条对应的小节，宁可省略该条新闻**",
            "3. 四维列表须先过滤无用内容：不写导航/分享/视频控件/订阅与 APP 推广/扫码登录/版权声明/责任编辑/荐读标题堆砌等与议题无关的碎片；不写同义反复或弱相关 filler；合并重复报道，宁少勿滥、以信息密度为先。",
            "4. 开篇可给 3-6 条跨主题的要点摘要（同样排除上述噪声，只保留高价值归纳；每条须能在下方正文印证）。",
            "5. 每条四维要点须可追溯至具体 Article 的正文表述；标注来源媒体；禁止常识脑补、禁止空穴来风。",
            "6. 输出 Markdown。",
            "",
            "\n\n---\n\n".join(article_blocks),
        ]
    )


def filter_items_with_extracted_content(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in items if _has_extracted_content(item)]


def _has_extracted_content(item: dict[str, Any]) -> bool:
    if item.get("fetch_error"):
        return False

    content = str(item.get("content") or "").strip()
    if not content:
        return False

    lowered = content.lower()
    if lowered.startswith("<a ") or lowered.startswith("<ol") or "news.google.com/rss/articles" in lowered:
        return False

    return True


if __name__ == "__main__":
    main()
