"""从已有第5步（内容分析）YAML 重跑第6步：主题简报 MD + HTML/TXT，不重复抓取/分析。

优先读取 ``kr36_step_5_content_analysis_YYYYMMDD.yaml``；若不存在则回退
``kr36_step3_analysis_YYYYMMDD.yaml``（与旧脚本习惯一致）。"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

BRIEF_PROMPT_PATH = SRC / "kr36" / "prompts" / "brief-agent.md"
STEP5_CANON = "kr36_step_5_content_analysis"
LEGACY_STEP3 = "kr36_step3_analysis"
STEP6_PREFIX = "kr36_step_6_brief"

from kr36.emailing.preview_assets import save_brief_preview_assets  # noqa: E402
from utils.tools.facades.content_analysis import (  # noqa: E402
    generate_brief_markdown,
    load_content_analysis_inputs,
)
from utils.tools.facades.intelligence import LLM_TRACE_LOG_DIR_NAME  # noqa: E402
from utils.tools.llm import StructuredChatClient  # noqa: E402
from utils.tools.runtime.checkpoint import StepCheckpointStore, checkpoint_path_for_step  # noqa: E402


def _date_token(d: date) -> str:
    return d.strftime("%Y%m%d")


def resolve_step5_yaml(run_dir: Path, d: date) -> Path:
    token = _date_token(d)
    for name in (f"{STEP5_CANON}_{token}.yaml", f"{LEGACY_STEP3}_{token}.yaml"):
        p = run_dir / name
        if p.is_file():
            return p
    return run_dir / f"{STEP5_CANON}_{token}.yaml"


def step6_brief_path(run_dir: Path, d: date) -> Path:
    return run_dir / f"{STEP6_PREFIX}_{_date_token(d)}.md"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="36Kr: 从第5步内容分析 YAML 重跑第6步简报与 HTML（不重复前几步）。"
    )
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "--run-dir",
        type=Path,
        required=True,
        help="含 kr36_step_5_content_analysis_*.yaml 的报告目录",
    )
    args = parser.parse_args()
    target_date = date.fromisoformat(args.date)
    report_date_text = target_date.isoformat()
    run_dir = args.run_dir.resolve()
    step5 = resolve_step5_yaml(run_dir, target_date)
    step6 = step6_brief_path(run_dir, target_date)
    if not step5.is_file():
        raise SystemExit(
            f"找不到第5步分析文件（已试 {STEP5_CANON} / {LEGACY_STEP3}）: {step5.parent}"
        )

    payload = load_content_analysis_inputs(step5)
    llm = StructuredChatClient.from_runtime_config()
    llm.set_trace_log_directory(
        (run_dir / LLM_TRACE_LOG_DIR_NAME).resolve(),
        report_date=report_date_text,
        source_prefix="kr36",
        reset_files=False,
    )
    step6_checkpoint = StepCheckpointStore.load_or_create(
        checkpoint_path=checkpoint_path_for_step(
            output_path=step6,
            step_name="step_6",
            report_date=report_date_text,
            prefix="kr36",
        ),
        step_name="step_6",
        report_date=report_date_text,
        input_path=step5,
        output_path=step6,
    )
    brief_markdown = generate_brief_markdown(
        payload,
        llm,
        prompt_path=BRIEF_PROMPT_PATH,
        checkpoint_store=step6_checkpoint,
    )
    step6.write_text(brief_markdown, encoding="utf-8")
    html_out, txt_out, doc_out = save_brief_preview_assets(step6, brief_markdown)
    print(f"第5步分析 YAML: {step5}")
    print(f"第6步 简报 MD: {step6}")
    print(f"第6步 HTML: {html_out}")
    print(f"第6步 doc HTML: {doc_out}")
    print(f"第6步 TXT: {txt_out}")


if __name__ == "__main__":
    main()
