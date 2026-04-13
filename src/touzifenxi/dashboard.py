from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote_plus, urlparse

from .models import UniverseFilter
from .settings import AppPaths, resolve_paths
from .storage import ResearchStore


def _format_pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value * 100:.2f}%"


def _esc(value: object) -> str:
    return html.escape(str(value))


def _ticker_market_code(ticker: str) -> str:
    value = str(ticker or "").strip().upper()
    if not value:
        return ""
    code = value.split(".")[0]
    suffix = value.split(".")[-1] if "." in value else ""
    market = "1" if suffix == "SH" else "0"
    return f"{market}.{code}"


def _eastmoney_stock_url(ticker: str) -> str:
    market_code = _ticker_market_code(ticker)
    return f"https://quote.eastmoney.com/unify/r/{market_code}" if market_code else "#"


def _eastmoney_notice_url(ticker: str) -> str:
    code = str(ticker or "").split(".")[0]
    return f"https://data.eastmoney.com/notices/stock/{code}.html" if code else "#"


def _policy_search_url(title: str) -> str:
    query = f"{str(title or '').strip()} site:cctv.com"
    return f"https://www.baidu.com/s?wd={quote_plus(query)}"


def _event_link(item: dict[str, object]) -> str:
    source_url = str(item.get("source_url", "") or "").strip()
    if source_url:
        return source_url
    source_type = str(item.get("source_type", ""))
    ticker = str(item.get("ticker", ""))
    title = str(item.get("title", ""))
    if source_type == "notice" and ticker:
        return _eastmoney_notice_url(ticker)
    if source_type == "policy" and title:
        return _policy_search_url(title)
    if ticker:
        return _eastmoney_stock_url(ticker)
    return "#"


def _load_dashboard_snapshot(paths: AppPaths, store: ResearchStore) -> dict[str, object]:
    store.init_db()
    latest_snapshot = store.get_latest_factor_snapshot_date() or "N/A"
    master_count = store.get_universe_master_count()
    master_rows = store.get_universe_master_rows(limit=80)
    coverage = store.get_coverage_summary(
        snapshot_date=latest_snapshot if latest_snapshot != "N/A" else "1970-01-01",
        universe_filter=UniverseFilter(limit=1),
    )
    candidate_rows = [dict(row) for row in store.list_universe_candidates(UniverseFilter(limit=80))]
    ready_rows = [row for row in candidate_rows if bool(row.get("ready_pool", False))]
    performance = store.get_performance_summary()
    weekly_summary = store.get_latest_weekly_pool_summary()
    weekly_pool_rows = [dict(row) for row in store.get_latest_weekly_pool_rows(limit=50)]
    latest_recommendations = store.get_latest_recommendations(limit=5)
    recent_events = store.get_recent_theme_events(limit=18)
    recent_pool_changes = store.get_recent_weekly_pool_changes(limit=20)
    recent_candidate_decisions = store.get_recent_candidate_decisions(limit=30)
    active_rule_versions = store.get_active_rule_versions()
    stock_lifecycle = store.get_stock_pool_lifecycle_summary(limit=20)
    theme_lifecycle = store.get_theme_lifecycle_summary(limit=20)
    db_status = store.get_database_status()
    report_path = None
    with store.connect() as conn:
        row = conn.execute(
            """
            SELECT report_path
            FROM research_runs
            WHERE report_path IS NOT NULL AND report_path != ''
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if row and row[0]:
            report_path = str(row[0])
    return {
        "paths": {
            "db_path": str(paths.db_path),
            "report_path": report_path,
        },
        "latest_snapshot": latest_snapshot,
        "master_count": master_count,
        "master_rows": master_rows,
        "coverage": coverage,
        "candidate_rows": candidate_rows,
        "ready_rows": ready_rows,
        "performance": performance,
        "weekly_summary": weekly_summary,
        "weekly_pool_rows": weekly_pool_rows,
        "latest_recommendations": latest_recommendations,
        "recent_events": recent_events,
        "recent_pool_changes": recent_pool_changes,
        "recent_candidate_decisions": recent_candidate_decisions,
        "active_rule_versions": active_rule_versions,
        "stock_lifecycle": stock_lifecycle,
        "theme_lifecycle": theme_lifecycle,
        "db_status": db_status,
    }


def _render_dashboard(snapshot: dict[str, object], active_tab: str, selected_theme: str = "", foundation_view: str = "candidate") -> str:
    master_count = snapshot["master_count"]
    master_rows = snapshot["master_rows"]
    coverage = snapshot["coverage"]
    candidate_rows = snapshot["candidate_rows"]
    ready_rows = snapshot["ready_rows"]
    performance = snapshot["performance"]
    weekly_summary = snapshot["weekly_summary"]
    weekly_pool_rows = snapshot["weekly_pool_rows"]
    latest_recommendations = snapshot["latest_recommendations"]
    recent_events = snapshot["recent_events"]
    recent_pool_changes = snapshot["recent_pool_changes"]
    recent_candidate_decisions = snapshot["recent_candidate_decisions"]
    active_rule_versions = snapshot["active_rule_versions"]
    stock_lifecycle = snapshot["stock_lifecycle"]
    theme_lifecycle = snapshot["theme_lifecycle"]
    db_status = snapshot["db_status"]
    latest_run = performance["latest_run"]
    latest_weekly_pool = performance["latest_weekly_pool"]
    ready_ratio = (
        coverage["ready_candidates"] / coverage["total_candidates"] if coverage["total_candidates"] else 0.0
    )

    def card(title: str, value: str, meta: str = "") -> str:
        return f"""
        <div class="card metric">
          <div class="metric-title">{_esc(title)}</div>
          <div class="metric-value">{_esc(value)}</div>
          <div class="metric-meta">{_esc(meta)}</div>
        </div>
        """

    theme_detail = None
    if weekly_summary and selected_theme:
        for item in weekly_summary["themes"]:
            if str(item["theme_name"]) == selected_theme:
                try:
                    detail = json.loads(str(item.get("detail_json", "{}")))
                except json.JSONDecodeError:
                    detail = {}
                theme_detail = {"summary": item, "detail": detail}
                break

    tabs = [
        ("overview", "总览"),
        ("foundation", "底座池"),
        ("pool", "周度池"),
        ("recommendations", "日推结果"),
        ("events", "主题事件"),
        ("runs", "运行历史"),
        ("tracking", "追踪中心"),
    ]

    def tab_link(tab_id: str, label: str) -> str:
        active_class = "tab-active" if tab_id == active_tab else ""
        return f'<a class="tab-link {active_class}" href="/?tab={tab_id}">{_esc(label)}</a>'

    def metric_link(label: str, value: str, meta: str, tab_id: str, view: str) -> str:
        return f"""
        <a class="metric-link" href="/?tab={tab_id}&view={quote_plus(view)}">
          <div class="card metric metric-clickable">
            <div class="metric-title">{_esc(label)}</div>
            <div class="metric-value">{_esc(value)}</div>
            <div class="metric-meta">{_esc(meta)}</div>
          </div>
        </a>
        """

    theme_score_rows = ""
    if weekly_summary:
        theme_score_rows = "".join(
            f"""
            <tr>
              <td><a class="event-link" href="/?tab=overview&theme={quote_plus(str(item['theme_name']))}">{_esc(item['theme_name'])}</a></td>
              <td>{item['total_score']:.4f}</td>
              <td>{item['policy_score']:.4f}</td>
              <td>{item['valuation_score']:.4f}</td>
              <td>{item['performance_score']:.4f}</td>
              <td>{_esc(item['performance_source'])}</td>
              <td>{'selected' if item['selected'] else 'reserve'}</td>
            </tr>
            """
            for item in weekly_summary["themes"]
        )

    theme_detail_html = ""
    if theme_detail is not None:
        detail = theme_detail["detail"]
        summary_item = theme_detail["summary"]
        theme_detail_html = f"""
        <div class="card" style="margin-top:18px;">
          <h2>主题分详情 · {_esc(summary_item['theme_name'])}</h2>
          <div class="subtitle">这里展示分数是怎么估算出来的。当前表现分来源：{_esc(summary_item['performance_source'])}。</div>
          <table>
            <tbody>
              <tr><th>总分</th><td>{float(summary_item['total_score']):.4f}</td></tr>
              <tr><th>政策分</th><td>{float(summary_item['policy_score']):.4f} ｜ recent_theme_strength={_esc(detail.get('policy_recent_strength', 'N/A'))} ｜ {_esc(detail.get('policy_formula', ''))}</td></tr>
              <tr><th>估值分</th><td>{float(summary_item['valuation_score']):.4f} ｜ valuation_median={_esc(detail.get('valuation_median', 'N/A'))} ｜ ready_ratio={_esc(detail.get('ready_ratio', 'N/A'))} ｜ {_esc(detail.get('valuation_formula', ''))}</td></tr>
              <tr><th>表现分</th><td>{float(summary_item['performance_score']):.4f} ｜ history_count={_esc(detail.get('history_count', 'N/A'))} ｜ avg_return={_esc(detail.get('history_avg_return', 'N/A'))} ｜ win_rate={_esc(detail.get('history_win_rate', 'N/A'))} ｜ stage_ratio={_esc(detail.get('stage_ratio', 'N/A'))} ｜ {_esc(detail.get('performance_formula', ''))}</td></tr>
              <tr><th>主题股票数</th><td>{_esc(detail.get('member_count', 'N/A'))}</td></tr>
              <tr><th>总分公式</th><td>{_esc(detail.get('total_formula', ''))}</td></tr>
            </tbody>
          </table>
        </div>
        """

    pool_rows_html = "".join(
        f"""
        <tr>
          <td><a class="mono-link" href="{_esc(_eastmoney_stock_url(str(row.get('ticker', ''))))}" target="_blank" rel="noreferrer">{_esc(row.get('ticker', ''))}</a></td>
          <td><a class="name-link" href="{_esc(_eastmoney_stock_url(str(row.get('ticker', ''))))}" target="_blank" rel="noreferrer">{_esc(row.get('name', ''))}</a></td>
          <td>{_esc(row.get('prefilter_theme', 'wildcard') or 'wildcard')}</td>
          <td>{_esc(row.get('prefilter_bucket', ''))}</td>
          <td>{_esc(row.get('prefilter_source', ''))}</td>
          <td>{'1' if row.get('ready_pool') else '0'}</td>
          <td>{_esc(row.get('fundamental_source', ''))}</td>
          <td>{float(row.get('prefilter_score', 0.0)):.2f}</td>
        </tr>
        """
        for row in weekly_pool_rows
    )

    recommendation_rows = "".join(
        f"""
        <div class="rec-item">
          <div class="rec-head">
            <span class="rank">#{int(rec['rank_no'])}</span>
            <a class="ticker mono-link" href="{_esc(_eastmoney_stock_url(str(rec['ticker'])))}" target="_blank" rel="noreferrer">{_esc(rec['ticker'])}</a>
            <a class="name name-link" href="{_esc(_eastmoney_stock_url(str(rec['ticker'])))}" target="_blank" rel="noreferrer">{_esc(rec['name'])}</a>
            <span class="score">{float(rec['total_score']):.4f}</span>
          </div>
          <div class="rec-meta">
            <span>阶段: {_esc(rec['stage'])}</span>
            <span>主题: {_esc(rec['prefilter_theme'] or rec['theme_name'] or 'N/A')}</span>
            <span>分层: {_esc(rec['prefilter_bucket'] or rec['theme_bucket'])}</span>
            <span>Ready: {'1' if rec['ready_pool'] else '0'}</span>
            <span>基本面: {_esc(rec['fundamental_source'])}</span>
          </div>
          <div class="rec-reasons">{_esc('；'.join(rec['reasons']) if rec['reasons'] else 'N/A')}</div>
        </div>
        """
        for rec in latest_recommendations
    )

    run_rows = "".join(
        f"""
        <tr>
          <td>{_esc(run['id'])}</td>
          <td>{_esc(run['run_at'])}</td>
          <td>{_esc(run['data_source'])}</td>
          <td>{_esc(run['dominant_style'])}</td>
          <td>{_esc(run['router_mode'])}</td>
          <td>{int(run['ready_pool_size'])}</td>
          <td>{int(run['fallback_pool_size'])}</td>
          <td>{_format_pct(float(run['coverage_ratio']))}</td>
        </tr>
        """
        for run in performance["recent_runs"]
    )

    event_rows = "".join(
        f"""
        <tr>
          <td>{_esc(item['event_date'])}</td>
          <td>{_esc(item['theme_name'])}</td>
          <td>{_esc(item['source_type'])}</td>
          <td>{f'<a class="mono-link" href="{_esc(_eastmoney_stock_url(str(item["ticker"])))}" target="_blank" rel="noreferrer">{_esc(item["ticker"])}</a>' if item['ticker'] else '-'}</td>
          <td>{float(item['strength']):.2f}</td>
          <td><a class="event-link" href="{_esc(_event_link(item))}" target="_blank" rel="noreferrer">{_esc(item['title'])}</a></td>
        </tr>
        """
        for item in recent_events
    )

    flow_json = json.dumps(
        {
            "weekly_mode": latest_weekly_pool["build_mode"] if latest_weekly_pool else "N/A",
            "weekly_pool_size": latest_weekly_pool["pool_size"] if latest_weekly_pool else 0,
            "refresh_added": latest_weekly_pool["refresh_added"] if latest_weekly_pool else 0,
            "refresh_removed": latest_weekly_pool["refresh_removed"] if latest_weekly_pool else 0,
            "run_router_mode": latest_run["router_mode"] if latest_run else "N/A",
            "final_picks": len(latest_recommendations),
        },
        ensure_ascii=False,
    )

    overview_section = f"""
    <section class="hero hero-refined">
      <div class="card masthead">
        <div class="eyebrow">Research Operating Console</div>
        <h1>多 Agent 投研系统</h1>
        <div class="subtitle">
          现在的主链不是从全市场直接日扫，而是先用更大的研究底座做周度预筛，再压到周度池，最后日选 5 只以内。
        </div>
        <div class="flow flow-refined">
          <div class="flow-step"><strong>研究底座</strong><span>主表 {master_count} 只<br>预筛底座 {coverage['total_candidates']} 只</span></div>
          <div class="flow-step"><strong>周度预筛</strong><span>本周池 {latest_weekly_pool['pool_size'] if latest_weekly_pool else 0} 只<br>主题 {latest_weekly_pool['theme_count'] if latest_weekly_pool else 0} 个</span></div>
          <div class="flow-step"><strong>日度打分</strong><span>ready {latest_run['ready_pool_size'] if latest_run else 0}<br>fallback {latest_run['fallback_pool_size'] if latest_run else 0}</span></div>
          <div class="flow-step"><strong>最终输出</strong><span>推荐 {len(latest_recommendations)} 只<br>router {latest_run['router_mode'] if latest_run else 'N/A'}</span></div>
        </div>
      </div>
      <div class="metrics">
        {metric_link("全市场主表", str(master_count), "原始股票主表，可点进去看具体名单", "foundation", "master")}
        {metric_link("周度预筛底座", str(coverage["total_candidates"]), "用于每周选主题和压缩到 50 只，不是日度直接全扫", "foundation", "candidate")}
        {metric_link("Ready Pool", str(coverage["ready_candidates"]), f"三层真实覆盖完整，当前占比 {_format_pct(ready_ratio)}", "foundation", "ready")}
        {metric_link("最新周度池", str(latest_weekly_pool["pool_size"]) if latest_weekly_pool else "0", f"本周最终可研究池，主题 {latest_weekly_pool['theme_count'] if latest_weekly_pool else 0} 个", "pool", "weekly")}
      </div>
    </section>

    <section class="grid">
      <div class="card">
        <h2>主题打分</h2>
        <div class="subtitle">主题分 = 政策支持 40% + 历史表现 35% + 低估值 25%。点击主题名可以看明细。</div>
        <table>
          <thead>
            <tr>
              <th>主题</th>
              <th>总分</th>
              <th>政策</th>
              <th>估值</th>
              <th>表现</th>
              <th>来源</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>{theme_score_rows}</tbody>
        </table>
      </div>
      <div class="card">
        <h2>最新前 5</h2>
        <div class="subtitle">这里是最新一轮落库后的结果，点击代码或名称可以直接跳到东方财富个股页。</div>
        {recommendation_rows}
      </div>
    </section>
    {theme_detail_html}
    """

    foundation_rows_source = candidate_rows
    foundation_title = "周度预筛底座"
    foundation_subtitle = "这是每周预筛时使用的可研究底座，不是最终周度池。它保留较大范围，是为了不遗漏潜在线索。"
    if foundation_view == "master":
        foundation_rows_source = master_rows
        foundation_title = "全市场主表"
        foundation_subtitle = "这是全市场原始股票主表，主要用于给周度预筛提供上游素材。"
    elif foundation_view == "ready":
        foundation_rows_source = ready_rows
        foundation_title = "Ready Pool"
        foundation_subtitle = "这是底座里已经具备正式行业、真实财务和最新因子覆盖的高质量候选。"

    foundation_rows_html = "".join(
        f"""
        <tr>
          <td><a class="mono-link" href="{_esc(_eastmoney_stock_url(str(row.get('ticker', ''))))}" target="_blank" rel="noreferrer">{_esc(row.get('ticker', ''))}</a></td>
          <td><a class="name-link" href="{_esc(_eastmoney_stock_url(str(row.get('ticker', ''))))}" target="_blank" rel="noreferrer">{_esc(row.get('name', ''))}</a></td>
          <td>{_esc(row.get('sector', row.get('board', '-')))}</td>
          <td>{_esc(row.get('board', '-'))}</td>
          <td>{'1' if row.get('ready_pool') else '0'}</td>
          <td>{'1' if row.get('industry_ready') else '0'}</td>
          <td>{'1' if row.get('financial_ready') else '0'}</td>
          <td>{'1' if row.get('factor_ready') else '0'}</td>
          <td>{_esc(row.get('fundamental_source', '-'))}</td>
          <td>{float(row.get('amount', 0.0)) / 100000000:.2f}亿</td>
        </tr>
        """
        for row in foundation_rows_source
    )

    foundation_section = f"""
    <section class="card">
      <h2>{_esc(foundation_title)}</h2>
      <div class="subtitle">{_esc(foundation_subtitle)}</div>
      <div class="subnav">
        <a class="subnav-link {'subnav-active' if foundation_view == 'master' else ''}" href="/?tab=foundation&view=master">全市场主表</a>
        <a class="subnav-link {'subnav-active' if foundation_view == 'candidate' else ''}" href="/?tab=foundation&view=candidate">周度预筛底座</a>
        <a class="subnav-link {'subnav-active' if foundation_view == 'ready' else ''}" href="/?tab=foundation&view=ready">Ready Pool</a>
      </div>
      <table>
        <thead>
          <tr>
            <th>代码</th>
            <th>名称</th>
            <th>行业/板块</th>
            <th>所属板块</th>
            <th>Ready</th>
            <th>行业</th>
            <th>财务</th>
            <th>因子</th>
            <th>基本面来源</th>
            <th>成交额</th>
          </tr>
        </thead>
        <tbody>{foundation_rows_html}</tbody>
      </table>
    </section>
    """

    pool_section = f"""
    <section class="card">
      <h2>最新周度 50 池</h2>
      <div class="subtitle">
        周次 {_esc(latest_weekly_pool['prefilter_week'] if latest_weekly_pool else 'N/A')}，
        mode={_esc(latest_weekly_pool['build_mode'] if latest_weekly_pool else 'N/A')}，
        theme/wildcard 分层来自最新周度池。
      </div>
      <div class="footer-note">数据库: {_esc(snapshot['paths']['db_path'])}</div>
      <table>
        <thead>
          <tr>
            <th>代码</th>
            <th>名称</th>
            <th>主题</th>
            <th>分层</th>
            <th>来源</th>
            <th>Ready</th>
            <th>基本面</th>
            <th>预筛分</th>
          </tr>
        </thead>
        <tbody>{pool_rows_html}</tbody>
      </table>
    </section>
    """

    recommendations_section = f"""
    <section class="card">
      <h2>最新前 5 推荐</h2>
      <div class="subtitle">这是最终输出页，保留阶段、主题、ready 状态和理由。</div>
      {recommendation_rows}
    </section>
    """

    events_section = f"""
    <section class="card">
      <h2>最近主题事件</h2>
      <div class="subtitle">点击事件标题可以跳到对应内容页。公告走东方财富公告列表，政策走央视搜索结果。</div>
      <table>
        <thead>
          <tr>
            <th>日期</th>
            <th>主题</th>
            <th>来源</th>
            <th>代码</th>
            <th>强度</th>
            <th>内容链接</th>
          </tr>
        </thead>
        <tbody>{event_rows}</tbody>
      </table>
    </section>
    """

    runs_section = f"""
    <section class="grid">
      <div class="card">
        <h2>最近运行历史</h2>
        <div class="subtitle">让你看到最近几次是怎么跑的，路由模式和池子质量有没有变化。</div>
        <table>
          <thead>
            <tr>
              <th>Run</th>
              <th>时间</th>
              <th>数据源</th>
              <th>风格</th>
              <th>路由</th>
              <th>Ready</th>
              <th>Fallback</th>
              <th>覆盖率</th>
            </tr>
          </thead>
          <tbody>{run_rows}</tbody>
        </table>
      </div>
      <div class="card">
        <h2>系统状态</h2>
        <div class="subtitle">主要看最新周度池和最新运行的摘要。</div>
        <p><span class="pill">周度池</span> {_esc(latest_weekly_pool['prefilter_week'] if latest_weekly_pool else 'N/A')} / mode={_esc(latest_weekly_pool['build_mode'] if latest_weekly_pool else 'N/A')}</p>
        <p><span class="pill">刷新</span> added={_esc(latest_weekly_pool['refresh_added'] if latest_weekly_pool else 0)} / removed={_esc(latest_weekly_pool['refresh_removed'] if latest_weekly_pool else 0)}</p>
        <p><span class="pill">最新运行</span> run_id={_esc(latest_run['id'] if latest_run else 'N/A')} / router={_esc(latest_run['router_mode'] if latest_run else 'N/A')}</p>
        <p><span class="pill">真实财务</span> {_format_pct(latest_run['recommendation_real_financial_ratio'] if latest_run else None)}</p>
        <div class="footer-note">flow snapshot: {_esc(flow_json)}</div>
      </div>
    </section>
    """

    rule_rows = "".join(
        f"""
        <tr>
          <td>{_esc(row['scope'])}</td>
          <td>{_esc(row['version'])}</td>
          <td><code>{_esc(row['config_json'])}</code></td>
          <td>{_esc(row['created_at'])}</td>
        </tr>
        """
        for row in active_rule_versions
    )
    pool_change_rows = "".join(
        f"""
        <tr>
          <td>{_esc(row['created_at'])}</td>
          <td>{_esc(row['ticker'])}</td>
          <td>{_esc(row['name'])}</td>
          <td>{_esc(row['change_type'])}</td>
          <td>{_esc(row.get('from_theme') or '-')}</td>
          <td>{_esc(row.get('to_theme') or '-')}</td>
          <td>{_esc(row['reason_text'])}</td>
        </tr>
        """
        for row in recent_pool_changes
    )
    decision_rows = "".join(
        f"""
        <tr>
          <td>{_esc(row['created_at'])}</td>
          <td>{_esc(row['run_id'])}</td>
          <td>{_esc(row['ticker'])}</td>
          <td>{_esc(row['decision_stage'])}</td>
          <td>{_esc(row['decision'])}</td>
          <td>{_esc(row['reason_code'])}</td>
          <td>{_esc(row.get('theme_name') or '-')}</td>
          <td>{float(row['total_score']):.4f}</td>
        </tr>
        """
        for row in recent_candidate_decisions
    )
    stock_lifecycle_rows = "".join(
        f"""
        <tr>
          <td>{_esc(row['created_at'])}</td>
          <td><a class="mono-link" href="{_esc(_eastmoney_stock_url(str(row['ticker'])))}" target="_blank" rel="noreferrer">{_esc(row['ticker'])}</a></td>
          <td>{_esc(row['name'])}</td>
          <td>{_esc(row.get('theme_name') or '-')}</td>
          <td>{_esc(row['lifecycle_status'])}</td>
          <td>{_esc(row['entry_count'])}</td>
          <td>{_esc(row['consecutive_runs'])}</td>
          <td>{'1' if row['in_pool'] else '0'}</td>
        </tr>
        """
        for row in stock_lifecycle
    )
    theme_lifecycle_rows = "".join(
        f"""
        <tr>
          <td>{_esc(row['prefilter_week'])}</td>
          <td>{_esc(row['theme_name'])}</td>
          <td>{float(row['theme_score']):.4f}</td>
          <td>{_esc(row['member_count'])}</td>
          <td>{_esc(row['consecutive_active_runs'])}</td>
          <td>{_esc(row['recommendation_count'])}</td>
          <td>{_esc(row['performance_source'])}</td>
        </tr>
        """
        for row in theme_lifecycle
    )
    tracking_section = f"""
    <section class="stack">
      <div class="card">
        <h2>数据库状态</h2>
        <div class="subtitle">这里显示当前是否已经正式运行在 PostgreSQL 主库，以及 schema 与表数量是否正常。</div>
        <p><span class="pill">active</span> {_esc(db_status['active_backend'])}</p>
        <p><span class="pill">configured</span> {_esc(db_status['configured_backend'])}</p>
        <p><span class="pill">tables</span> {_esc(db_status['table_count'])}</p>
        <p><span class="pill">sqlite fallback</span> {_esc(db_status['db_path'])}</p>
        <p><span class="pill">pg schema</span> {_esc(db_status['postgres_schema_sql'])}</p>
      </div>
      <div class="card">
        <h2>规则版本</h2>
        <div class="subtitle">这里记录当前正在生效的规则版本，后面做回测和效果对比会用到。</div>
        <table>
          <thead><tr><th>范围</th><th>版本</th><th>配置</th><th>记录时间</th></tr></thead>
          <tbody>{rule_rows}</tbody>
        </table>
      </div>
      <div class="card">
        <h2>周度池变更</h2>
        <div class="subtitle">每次周度重建或日度刷新，哪些票新增、移除、变更，都会在这里留下痕迹。</div>
        <table>
          <thead><tr><th>时间</th><th>代码</th><th>名称</th><th>变更</th><th>旧主题</th><th>新主题</th><th>原因</th></tr></thead>
          <tbody>{pool_change_rows}</tbody>
        </table>
      </div>
      <div class="card">
        <h2>候选决策日志</h2>
        <div class="subtitle">投委会如何选、如何跳过，开始留下结构化决策记录了。</div>
        <table>
          <thead><tr><th>时间</th><th>Run</th><th>代码</th><th>阶段</th><th>决策</th><th>原因码</th><th>主题</th><th>分数</th></tr></thead>
          <tbody>{decision_rows}</tbody>
        </table>
      </div>
      <div class="card">
        <h2>股票生命周期</h2>
        <div class="subtitle">每只票第几次入池、是否还在池中、连续停留了多久，这里都会累计起来。</div>
        <table>
          <thead><tr><th>时间</th><th>代码</th><th>名称</th><th>主题</th><th>状态</th><th>入池次数</th><th>连续轮次</th><th>在池中</th></tr></thead>
          <tbody>{stock_lifecycle_rows}</tbody>
        </table>
      </div>
      <div class="card">
        <h2>主题生命周期</h2>
        <div class="subtitle">主题是否连续活跃、在周度池里覆盖了多少票、这轮最终命中了几只推荐，都能持续观察。</div>
        <table>
          <thead><tr><th>周次</th><th>主题</th><th>总分</th><th>池内股票</th><th>连续活跃</th><th>推荐命中</th><th>表现来源</th></tr></thead>
          <tbody>{theme_lifecycle_rows}</tbody>
        </table>
      </div>
    </section>
    """

    sections = {
        "overview": overview_section,
        "foundation": foundation_section,
        "pool": pool_section,
        "recommendations": recommendations_section,
        "events": events_section,
        "runs": runs_section,
        "tracking": tracking_section,
    }

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>投研系统 Dashboard</title>
  <style>
    :root {{
      --bg: #f4efe6;
      --panel: #fffaf1;
      --ink: #1f2a2d;
      --muted: #69777a;
      --line: #d8cbb4;
      --accent: #a9472b;
      --accent-2: #1c5660;
      --ok: #2d7a46;
      --warn: #8b5c00;
      --shadow: 0 18px 50px rgba(77, 53, 24, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Georgia, "Songti SC", "STSong", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(183,71,42,0.12), transparent 28%),
        radial-gradient(circle at bottom right, rgba(35,106,115,0.14), transparent 26%),
        var(--bg);
    }}
    .wrap {{
      max-width: 1380px;
      margin: 0 auto;
      padding: 28px 20px 48px;
    }}
    .hero {{
      display: grid;
      grid-template-columns: 1.5fr 1fr;
      gap: 18px;
      margin-bottom: 18px;
    }}
    .hero-refined {{
      align-items: stretch;
    }}
    .masthead {{
      padding: 24px 24px 18px;
      background:
        linear-gradient(145deg, rgba(255,250,241,0.96), rgba(250,240,224,0.88)),
        radial-gradient(circle at top right, rgba(169,71,43,0.08), transparent 30%);
    }}
    .eyebrow {{
      font-size: 11px;
      letter-spacing: 0.16em;
      text-transform: uppercase;
      color: var(--accent-2);
      margin-bottom: 10px;
    }}
    .topbar {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      margin-bottom: 18px;
      flex-wrap: wrap;
    }}
    .tabbar {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .tab-link {{
      text-decoration: none;
      color: var(--ink);
      padding: 10px 14px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.55);
      font-size: 14px;
    }}
    .tab-link:hover {{
      border-color: var(--accent);
      color: var(--accent);
    }}
    .tab-active {{
      background: var(--accent);
      color: white;
      border-color: var(--accent);
    }}
    .metric-link {{
      text-decoration: none;
      color: inherit;
      display: block;
    }}
    .card {{
      background: color-mix(in srgb, var(--panel) 92%, white 8%);
      border: 1px solid var(--line);
      border-radius: 22px;
      box-shadow: var(--shadow);
      padding: 20px;
    }}
    h1 {{
      margin: 0 0 10px;
      font-size: 34px;
      line-height: 1.1;
    }}
    .subtitle {{
      color: var(--muted);
      line-height: 1.6;
      font-size: 15px;
    }}
    .flow {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 12px;
      margin-top: 18px;
    }}
    .flow-refined {{
      margin-top: 20px;
      grid-template-columns: repeat(4, minmax(0, 1fr));
    }}
    .flow-step {{
      border: 1px solid rgba(216,203,180,0.9);
      border-radius: 16px;
      padding: 14px;
      background: rgba(255,255,255,0.72);
    }}
    .flow-step strong {{
      display: block;
      margin-bottom: 6px;
      color: var(--accent);
    }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 12px;
    }}
    .metric-clickable {{
      transition: transform 160ms ease, border-color 160ms ease, box-shadow 160ms ease;
    }}
    .metric-clickable:hover {{
      transform: translateY(-2px);
      border-color: rgba(169,71,43,0.55);
      box-shadow: 0 24px 50px rgba(77, 53, 24, 0.10);
    }}
    .metric-title {{
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 8px;
    }}
    .metric-value {{
      font-size: 28px;
      font-weight: 700;
    }}
    .metric-meta {{
      color: var(--muted);
      margin-top: 6px;
      font-size: 13px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: 1.1fr 0.9fr;
      gap: 18px;
      margin-bottom: 18px;
    }}
    .stack {{
      display: grid;
      gap: 18px;
    }}
    .subnav {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin: 16px 0 14px;
    }}
    .subnav-link {{
      text-decoration: none;
      color: var(--ink);
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 8px 12px;
      background: rgba(255,255,255,0.72);
      font-size: 13px;
    }}
    .subnav-active {{
      background: var(--accent-2);
      color: white;
      border-color: var(--accent-2);
    }}
    h2 {{
      margin: 0 0 14px;
      font-size: 22px;
    }}
    .pill {{
      display: inline-block;
      padding: 4px 10px;
      border-radius: 999px;
      background: rgba(183,71,42,0.12);
      color: var(--accent);
      font-size: 12px;
      margin-right: 8px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }}
    th, td {{
      text-align: left;
      padding: 10px 8px;
      border-bottom: 1px solid rgba(216,203,180,0.7);
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-weight: 600;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .rec-item {{
      border-top: 1px solid rgba(216,203,180,0.7);
      padding: 14px 0;
    }}
    .rec-item:first-child {{ border-top: none; padding-top: 0; }}
    .rec-head {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: baseline;
      margin-bottom: 8px;
    }}
    .rank {{
      color: var(--accent);
      font-weight: 700;
      font-size: 18px;
    }}
    .ticker {{
      font-family: "SFMono-Regular", Consolas, monospace;
      font-size: 13px;
      color: var(--accent-2);
    }}
    .mono-link, .name-link, .event-link {{
      color: var(--accent-2);
      text-decoration: none;
    }}
    .mono-link:hover, .name-link:hover, .event-link:hover {{
      text-decoration: underline;
    }}
    .name {{
      font-size: 20px;
      font-weight: 700;
    }}
    .score {{
      margin-left: auto;
      font-weight: 700;
    }}
    .rec-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 8px;
    }}
    .rec-reasons {{
      line-height: 1.6;
      font-size: 14px;
    }}
    .footer-note {{
      margin-top: 12px;
      color: var(--muted);
      font-size: 13px;
    }}
    @media (max-width: 1000px) {{
      .hero, .grid {{ grid-template-columns: 1fr; }}
      .flow {{ grid-template-columns: 1fr 1fr; }}
      .metrics {{ grid-template-columns: 1fr 1fr; }}
    }}
    @media (max-width: 640px) {{
      .flow, .metrics {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 28px; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="topbar">
      <div>
        <h1 style="margin-bottom:6px;">多 Agent 投研系统</h1>
        <div class="subtitle">把周度池、日度推荐、主题事件和运行轨迹拆开看，避免所有信息挤在一页里。</div>
      </div>
      <nav class="tabbar">
        {''.join(tab_link(tab_id, label) for tab_id, label in tabs)}
      </nav>
    </div>
    {sections.get(active_tab, overview_section)}
  </div>
</body>
</html>
"""


def serve_dashboard(host: str = "127.0.0.1", port: int = 8787) -> None:
    paths = resolve_paths()
    store = ResearchStore(paths.db_path, database_url=paths.database_url)

    class Handler(BaseHTTPRequestHandler):
        def _serve_dashboard(self, body_only: bool) -> None:
            parsed = urlparse(self.path)
            if parsed.path not in {"/", "/index.html"}:
                self.send_error(404, "Not Found")
                return
            query = parse_qs(parsed.query)
            try:
                snapshot = _load_dashboard_snapshot(paths, store)
                active_tab = query.get("tab", ["overview"])[0]
                selected_theme = query.get("theme", [""])[0]
                foundation_view = query.get("view", ["candidate"])[0]
                if active_tab not in {"overview", "foundation", "pool", "recommendations", "events", "runs", "tracking"}:
                    active_tab = "overview"
                if foundation_view not in {"master", "candidate", "ready", "weekly"}:
                    foundation_view = "candidate"
                body = _render_dashboard(snapshot, active_tab=active_tab, selected_theme=selected_theme, foundation_view=foundation_view).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                refresh = query.get("refresh", [""])[0]
                if refresh:
                    self.send_header("Refresh", refresh)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if not body_only:
                    self.wfile.write(body)
            except Exception as exc:
                body = f"<h1>Dashboard Error</h1><pre>{_esc(exc)}</pre>".encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if not body_only:
                    self.wfile.write(body)

        def do_GET(self) -> None:
            self._serve_dashboard(body_only=False)

        def do_HEAD(self) -> None:
            self._serve_dashboard(body_only=True)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Dashboard running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
