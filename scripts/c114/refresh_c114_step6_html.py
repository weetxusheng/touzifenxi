"""从同目录 step5 YAML 重跑 sanitize，再据现有 step6 正文重渲染 MD/HTML。"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _to_draft(top: object) -> object:
    from utils.tools.analysis.models import BriefSectionDraft

    s = top.sections
    def b(name: str) -> str:
        return (s.get(name) or "").strip()

    follow_raw = s.get("需要继续跟踪的点", "")
    followups: list[str] = []
    for line in (follow_raw or "").splitlines():
        line = line.strip()
        if line.startswith("- "):
            followups.append(line[2:].strip())
        elif line:
            followups.append(line)
    return BriefSectionDraft(
        topic=top.topic,
        core_judgment=b("核心判断"),
        incremental_info=b("增量信息"),
        industry_impact=b("产业/公司影响"),
        followups=followups,
    )


def main() -> None:
    project_root = _project_root()
    src = project_root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    from utils.tools.analysis.models import BriefSectionDraft
    from utils.tools.analysis.yaml_io import load_content_analysis_inputs, save_content_analysis_yaml
    from utils.tools.brief.markdown import render_generated_brief_markdown
    from utils.tools.facades.intelligence import c114_reports_root, find_latest_search_run_directory, step_6_brief_name
    from utils.tools.orchestration.c114.brief_review import parse_brief_markdown
    from utils.tools.orchestration.c114.step6_outputs import write_step6_outputs
    from utils.tools.steps.step5_content_analysis import sanitize_step5_link_candidates

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--brief", type=Path, default=None, help="c114_step_6_brief_YYYYMMDD.md 路径")
    ap.add_argument("--date", type=str, default=None, help="简报日 YYYY-MM-DD（无 --brief 时用最新 run+该日）")
    args = ap.parse_args()

    reports_dir = project_root / "output" / "reports"

    if args.brief:
        brief_path = args.brief.resolve()
    else:
        d = date.fromisoformat(args.date) if args.date else date.today()
        run_dir = find_latest_search_run_directory(reports_dir, d) or c114_reports_root(reports_dir)
        brief_path = (run_dir / step_6_brief_name(d)).resolve()
        if not brief_path.exists():
            c114_root = reports_dir / "c114_report"
            if c114_root.is_dir():
                cands = sorted(
                    c114_root.glob("**/c114_step_6_brief_*.md"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if cands:
                    brief_path = cands[0].resolve()
        if not brief_path.exists():
            print("未找到 c114 step6 简报 .md", file=sys.stderr)
            sys.exit(1)

    m = re.search(r"(\d{4})(\d{2})(\d{2})", brief_path.stem)
    if not m:
        print("无法从文件名解析日期:", brief_path, file=sys.stderr)
        sys.exit(1)
    target_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    step5_guess = brief_path.parent / f"c114_step_5_content_analysis_{target_date.strftime('%Y%m%d')}.yaml"
    step5_path = step5_guess
    if not step5_path.exists():
        found = list(brief_path.parent.glob("c114_step_5_content_analysis_*.yaml"))
        if len(found) == 1:
            step5_path = found[0]
    if not step5_path.exists():
        print("缺少 step5 YAML:", step5_guess, file=sys.stderr)
        sys.exit(1)

    md = brief_path.read_text(encoding="utf-8")
    payload = sanitize_step5_link_candidates(load_content_analysis_inputs(step5_path))
    save_content_analysis_yaml(step5_path, payload)

    topics = parse_brief_markdown(md)
    by_topic = {t.topic: t for t in topics}
    drafts: list[BriefSectionDraft] = []
    for cat in payload.categories:
        if cat.topic not in by_topic:
            print("step6 中缺主题块:", cat.topic, file=sys.stderr)
            sys.exit(1)
        drafts.append(_to_draft(by_topic[cat.topic]))

    out_md = render_generated_brief_markdown(payload, drafts)
    html_path, _ = write_step6_outputs(
        brief_output=brief_path,
        markdown_text=out_md,
        run_dir=brief_path.parent,
        target_date=target_date,
        context="scripts/c114/refresh_c114_step6_html.py",
    )
    print(step5_path)
    print(brief_path)
    print(html_path)


if __name__ == "__main__":
    main()
