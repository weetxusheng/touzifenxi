"""从已有 step5 YAML 重跑 step6（简报 + HTML），不重复抓取。"""
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
STEP_3_PREFIX = "kr36_step3_analysis"
STEP_4_PREFIX = "kr36_step4_brief"

from kr36.email import render_kr36_brief_email  # noqa: E402
from utils.tools.facades.content_analysis import generate_brief_markdown, load_content_analysis_inputs  # noqa: E402
from utils.tools.facades.intelligence import LLM_TRACE_LOG_DIR_NAME  # noqa: E402
from utils.tools.llm import StructuredChatClient  # noqa: E402
from utils.tools.runtime.checkpoint import StepCheckpointStore, checkpoint_path_for_step  # noqa: E402


def step_3_name(d: date) -> str:
    return f"{STEP_3_PREFIX}_{d.strftime('%Y%m%d')}.yaml"


def step_4_name(d: date) -> str:
    return f"{STEP_4_PREFIX}_{d.strftime('%Y%m%d')}.md"


def save_brief_preview_assets(step6_markdown_path: Path, markdown_text: str) -> tuple[Path, Path, Path]:
    rendered = render_kr36_brief_email(markdown_text, step6_markdown_path)
    html_output = step6_markdown_path.with_suffix(".html")
    legacy_email_output = step6_markdown_path.with_name(f"{step6_markdown_path.stem}_email.html")
    doc_output = step6_markdown_path.with_name(f"{step6_markdown_path.stem}_doc.html")
    text_output = step6_markdown_path.with_name(f"{step6_markdown_path.stem}_email.txt")
    html_output.write_text(rendered.html, encoding="utf-8")
    legacy_email_output.write_text(rendered.html, encoding="utf-8")
    doc_output.write_text(rendered.html, encoding="utf-8")
    text_output.write_text(rendered.text, encoding="utf-8")
    return html_output, text_output, doc_output


def main() -> None:
    parser = argparse.ArgumentParser(
        description="36Kr: 从步骤3 分析 YAML 重跑步骤4 简报与 HTML（不重复抓取）。"
    )
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "--run-dir",
        type=Path,
        required=True,
        help="含 kr36_step3_analysis_*.yaml 的报告目录",
    )
    args = parser.parse_args()
    target_date = date.fromisoformat(args.date)
    report_date_text = target_date.isoformat()
    run_dir = args.run_dir.resolve()
    step3 = run_dir / step_3_name(target_date)
    step4 = run_dir / step_4_name(target_date)
    if not step3.is_file():
        raise SystemExit(f"找不到 step3 分析文件: {step3}")

    payload = load_content_analysis_inputs(step3)
    llm = StructuredChatClient.from_runtime_config()
    llm.set_trace_log_directory(
        (run_dir / LLM_TRACE_LOG_DIR_NAME).resolve(),
        report_date=report_date_text,
        source_prefix="kr36",
        reset_files=False,
    )
    step4_checkpoint = StepCheckpointStore.load_or_create(
        checkpoint_path=checkpoint_path_for_step(
            output_path=step4,
            step_name="step_4",
            report_date=report_date_text,
            prefix="kr36",
        ),
        step_name="step_4",
        report_date=report_date_text,
        input_path=step3,
        output_path=step4,
    )
    brief_markdown = generate_brief_markdown(
        payload,
        llm,
        prompt_path=BRIEF_PROMPT_PATH,
        checkpoint_store=step4_checkpoint,
    )
    step4.write_text(brief_markdown, encoding="utf-8")
    html_out, txt_out, doc_out = save_brief_preview_assets(step4, brief_markdown)
    print(f"Step 4 简报 MD: {step4}")
    print(f"Step 4 HTML: {html_out}")
    print(f"Step 4 email HTML: {step4.with_name(step4.stem + '_email.html')}")
    print(f"Step 4 doc HTML: {doc_out}")
    print(f"Step 4 TXT: {txt_out}")


if __name__ == "__main__":
    main()
