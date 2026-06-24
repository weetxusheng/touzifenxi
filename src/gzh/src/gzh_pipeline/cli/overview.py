"""CLI：生成每日文章总览页面。"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gzh_pipeline.util.dotenv_tools import load_dotenv_near_cli
from gzh_pipeline.util.text import biz_date_for_path, biz_date_iso_for_display


# 文章分类中英文映射表
CATEGORY_ZH_MAP = {
    # 价值预筛相关
    "gate_disabled": "预筛已关闭",
    "gate_error": "预筛异常",
    "gate_parse_error": "解析错误",
    
    # 内容类型
    "ad": "广告",
    "spam": "垃圾信息",
    "news": "新闻",
    "company": "公司动态",
    "macro_geopolitics": "宏观/地缘政治",
    "industry": "行业动态",
    "policy": "政策法规",
    "market_data": "市场数据",
    "analysis": "分析观点",
    "event": "事件通知",
    "other": "其他",
}


def get_category_zh(category: str) -> str:
    """将英文分类标签转换为中文显示。"""
    if not category or category == "unknown":
        return "未分类"
    return CATEGORY_ZH_MAP.get(category.lower(), category)


def load_trace_files(audit_root: Path, biz_date: str) -> list[dict[str, Any]]:
    """
    加载指定日期的所有 .trace.json 文件，提取每篇文章的元数据。
    
    路径结构：{audit_root}/{biz_date}/parse_{run_id}/*.trace.json
    
    Returns:
        文章列表，每项包含：stem, title, star_rating, summary, account, output_path
    """
    articles = []
    biz_date_path = biz_date_for_path(biz_date)
    
    # 遍历该日期下的所有 parse_{run_id} 目录
    date_dir = audit_root / biz_date_path
    if not date_dir.exists():
        return articles
    
    for run_dir in date_dir.iterdir():
        if not run_dir.is_dir():
            continue
        
        # 只处理 parse_ 开头的目录（解析阶段）
        if not run_dir.name.startswith("parse_"):
            continue
        
        for trace_file in run_dir.glob("*.trace.json"):
            try:
                with open(trace_file, 'r', encoding='utf-8') as f:
                    trace_data = json.load(f)
                
                # 从 summary 中提取数据（新结构）
                summary = trace_data.get("summary", {})
                
                # 提取公众号名称
                account = summary.get("account", "unknown")
                
                # 提取每篇文章的信息（从 summary.per_source_outputs）
                per_source_outputs = summary.get("per_source_outputs", [])
                for item in per_source_outputs:
                    stem = item.get("stem", "")
                    if not stem:
                        continue
                    
                    # 只保留有 output_path 的文章（即实际生成 HTML 的文章）
                    output_path = item.get("path", "")
                    if not output_path:
                        continue
                    
                    # 验证文件是否实际存在
                    from pathlib import Path
                    if not Path(output_path).exists():
                        continue
                    
                    article = {
                        "stem": stem,
                        "title": item.get("title", stem),
                        "star_rating": int(item.get("star_rating", 3)),
                        "summary": item.get("summary", ""),
                        "valuable": item.get("valuable", True),
                        "category": item.get("value_gate_category", "unknown"),
                        "reason_zh": item.get("value_gate_reason_zh", ""),
                        "account": account,
                        "output_path": output_path,
                    }
                    
                    # 限制摘要长度
                    if len(article["summary"]) > 40:
                        article["summary"] = article["summary"][:40] + "..."
                    
                    articles.append(article)
                    
            except Exception as e:
                print(f"[WARNING] Failed to load {trace_file}: {e}", file=__import__('sys').stderr)
                continue
    
    return articles


def generate_overview_html(articles: list[dict[str, Any]], biz_date: str) -> str:
    """
    生成总览页面的 HTML 内容。
    
    Args:
        articles: 文章列表
        biz_date: 业务日期 (yyyyMMdd 或 YYYY-MM-DD)
    
    Returns:
        HTML 字符串
    """
    # 按公众号分组
    by_account: dict[str, list[dict[str, Any]]] = {}
    for article in articles:
        account = article["account"]
        if account not in by_account:
            by_account[account] = []
        by_account[account].append(article)
    
    # 每个公众号内按星级降序排序
    for account in by_account:
        by_account[account].sort(key=lambda x: (-x["star_rating"], x["title"]))
    
    # 计算统计信息
    total_articles = len(articles)
    avg_rating = sum(a["star_rating"] for a in articles) / total_articles if total_articles > 0 else 0
    high_value_count = sum(1 for a in articles if a["star_rating"] >= 4)
    
    # 星级分布
    rating_distribution = {i: 0 for i in range(1, 6)}
    for a in articles:
        rating_distribution[a["star_rating"]] += 1
    
    # 生成 HTML
    biz_label = biz_date_iso_for_display(biz_date)
    html_parts = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN">',
        "<head>",
        '  <meta charset="UTF-8">',
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0">',
        f'  <title>公众号文章总览 - {biz_label}</title>',
        '  <style>',
        get_css_styles(),
        '  </style>',
        "</head>",
        "<body>",
        '  <div class="container">',
        '    <header>',
        f'      <h1>📊 今日总览 - {biz_label}</h1>',
        '      <div class="stats">',
        f'        <span class="stat-item">总文章数: <strong>{total_articles}</strong></span>',
        f'        <span class="stat-item">平均星级: <strong>{avg_rating:.1f}⭐</strong></span>',
        f'        <span class="stat-item">高价值文章: <strong>{high_value_count}</strong> (4-5星)</span>',
        '      </div>',
        '      <div class="rating-dist">',
        f'        <span>5星: {rating_distribution[5]}篇</span>',
        f'        <span>4星: {rating_distribution[4]}篇</span>',
        f'        <span>3星: {rating_distribution[3]}篇</span>',
        f'        <span>2星: {rating_distribution[2]}篇</span>',
        f'        <span>1星: {rating_distribution[1]}篇</span>',
        '      </div>',
        '    </header>',
        '',
    ]
    
    # 生成每个公众号的文章卡片
    for account in sorted(by_account.keys()):
        account_articles = by_account[account]
        html_parts.extend([
            f'    <section class="account-section">',
            f'      <h2>【{account}】 (共{len(account_articles)}篇)</h2>',
            '      <div class="article-grid">',
        ])
        
        for article in account_articles:
            html_parts.append(generate_article_card(article))
        
        html_parts.extend([
            '      </div>',
            '    </section>',
            '',
        ])
    
    # 如果没有文章
    if not articles:
        html_parts.extend([
            '    <div class="empty-state">',
            '      <p>今日暂无文章</p>',
            '    </div>',
            '',
        ])
    
    html_parts.extend([
        '  </div>',
        '</body>',
        '</html>',
    ])
    
    return '\n'.join(html_parts)


def generate_article_card(article: dict[str, Any]) -> str:
    """生成单篇文章的卡片 HTML。"""
    stem = article["stem"]
    title = article["title"]
    star_rating = article["star_rating"]
    summary = article["summary"] or "（无摘要）"
    output_path = article["output_path"]
    category = get_category_zh(article["category"])  # 转换为中文
    reason_zh = article["reason_zh"]
    account = article.get("account", "")
    
    # 生成星星
    stars = '⭐' * star_rating + '☆' * (5 - star_rating)
    
    # 确定颜色类
    color_class = f"rating-{star_rating}"
    
    # 构建相对路径：{account}/{stem}.html
    if output_path:
        from pathlib import Path
        out_path = Path(output_path)
        if stem in str(out_path):
            relative_path = f"{out_path.parent.name}/{out_path.name}"
        else:
            relative_path = f"{account}/{stem}.html" if account else f"{stem}.html"
    else:
        relative_path = f"{account}/{stem}.html" if account else f"{stem}.html"
    
    # 构建卡片
    card_parts = [
        f'      <a href="{relative_path}" target="_blank" class="article-card {color_class}" title="{reason_zh}">',
        f'        <div class="card-header">',
        f'          <h3 class="article-title">{title}</h3>',
        f'          <span class="stars">{stars}</span>',
        f'        </div>',
        f'        <p class="article-summary">{summary}</p>',
        f'        <div class="card-footer">',
        f'          <span class="category-tag">{category}</span>',
        f'        </div>',
        f'      </a>',
    ]
    
    return '\n'.join(card_parts)


def get_css_styles() -> str:
    """返回 CSS 样式。"""
    return '''
    :root {
      --color-5: #FFD700;
      --color-4: #4CAF50;
      --color-3: #2196F3;
      --color-2: #FF9800;
      --color-1: #F44336;
      --bg-color: #f5f7fa;
      --card-bg: #ffffff;
      --text-primary: #2c3e50;
      --text-secondary: #7f8c8d;
      --border-color: #e1e8ed;
    }
    
    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }
    
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Microsoft YaHei', sans-serif;
      background-color: var(--bg-color);
      color: var(--text-primary);
      line-height: 1.6;
      padding: 20px;
    }
    
    .container {
      max-width: 1400px;
      margin: 0 auto;
    }
    
    header {
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      color: white;
      padding: 30px;
      border-radius: 12px;
      margin-bottom: 30px;
      box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
    }
    
    h1 {
      font-size: 28px;
      margin-bottom: 20px;
    }
    
    .stats {
      display: flex;
      gap: 30px;
      flex-wrap: wrap;
      margin-bottom: 15px;
    }
    
    .stat-item {
      font-size: 16px;
    }
    
    .stat-item strong {
      font-size: 20px;
    }
    
    .rating-dist {
      display: flex;
      gap: 15px;
      flex-wrap: wrap;
      font-size: 14px;
      opacity: 0.9;
    }
    
    .account-section {
      margin-bottom: 40px;
    }
    
    .account-section h2 {
      font-size: 22px;
      margin-bottom: 20px;
      color: var(--text-primary);
      border-left: 4px solid #667eea;
      padding-left: 15px;
    }
    
    .article-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(350px, 1fr));
      gap: 20px;
    }
    
    .article-card {
      background: var(--card-bg);
      border: 2px solid var(--border-color);
      border-radius: 10px;
      padding: 20px;
      text-decoration: none;
      color: inherit;
      transition: all 0.3s ease;
      display: block;
      position: relative;
      overflow: hidden;
    }
    
    .article-card:hover {
      transform: translateY(-4px);
      box-shadow: 0 8px 16px rgba(0, 0, 0, 0.15);
    }
    
    .article-card::before {
      content: '';
      position: absolute;
      top: 0;
      left: 0;
      width: 100%;
      height: 4px;
    }
    
    .rating-5::before { background: var(--color-5); }
    .rating-4::before { background: var(--color-4); }
    .rating-3::before { background: var(--color-3); }
    .rating-2::before { background: var(--color-2); }
    .rating-1::before { background: var(--color-1); }
    
    .card-header {
      margin-bottom: 12px;
    }
    
    .article-title {
      font-size: 18px;
      font-weight: 600;
      margin-bottom: 8px;
      line-height: 1.4;
      color: var(--text-primary);
    }
    
    .stars {
      font-size: 16px;
      letter-spacing: 2px;
    }
    
    .article-summary {
      font-size: 14px;
      color: var(--text-secondary);
      margin-bottom: 12px;
      line-height: 1.6;
      display: -webkit-box;
      -webkit-line-clamp: 3;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }
    
    .card-footer {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding-top: 12px;
      border-top: 1px solid var(--border-color);
    }
    
    .category-tag {
      font-size: 12px;
      padding: 4px 10px;
      background: #f0f2f5;
      border-radius: 12px;
      color: var(--text-secondary);
    }
    
    .empty-state {
      text-align: center;
      padding: 60px 20px;
      background: var(--card-bg);
      border-radius: 12px;
      color: var(--text-secondary);
      font-size: 18px;
    }
    
    @media (max-width: 768px) {
      body {
        padding: 10px;
      }
      
      header {
        padding: 20px;
      }
      
      h1 {
        font-size: 22px;
      }
      
      .stats {
        flex-direction: column;
        gap: 10px;
      }
      
      .article-grid {
        grid-template-columns: 1fr;
      }
    }
    '''


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。"""
    # 加载 .env 文件
    load_dotenv_near_cli(__file__)
    
    parser = argparse.ArgumentParser(
        description="生成每日公众号文章总览页面"
    )
    parser.add_argument(
        "--biz-date",
        required=True,
        help="业务日期 (yyyyMMdd 或 YYYY-MM-DD)",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(os.environ.get("PARSE_OUTPUT_ROOT", "reports/gzh")),
        help="解析产出根目录",
    )
    parser.add_argument(
        "--audit-json-root",
        type=Path,
        default=Path(os.environ.get("PARSE_AUDIT_JSON_ROOT", "audit_traces")),
        help="审计日志根目录",
    )
    
    args = parser.parse_args(argv)
    
    # Determine output directory
    output_dir = args.output_root / biz_date_for_path(args.biz_date)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    
    # 加载文章数据
    print(f"[overview] Loading articles for {args.biz_date}...", file=__import__('sys').stderr)
    articles = load_trace_files(args.audit_json_root, args.biz_date)
    print(f"[overview] Found {len(articles)} articles", file=__import__('sys').stderr)
    
    # 生成 HTML
    html_content = generate_overview_html(articles, args.biz_date)
    
    # 保存文件
    output_file = output_dir / "index.html"
    output_file.write_text(html_content, encoding='utf-8')
    
    print(f"[overview] Generated: {output_file}", file=__import__('sys').stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
