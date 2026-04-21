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
STEP_5_PREFIX = "kr36_step_5_content_analysis"
STEP_6_PREFIX = "kr36_step_6_brief"

from kr36.email import render_kr36_brief_email  # noqa: E402
from utils.tools.facades.content_analysis import generate_brief_markdown, load_content_analysis_inputs  # noqa: E402
from utils.tools.facades.intelligence import LLM_TRACE_LOG_DIR_NAME  # noqa: E402
from utils.tools.llm import StructuredChatClient  # noqa: E402
from utils.tools.runtime.checkpoint import StepCheckpointStore, checkpoint_path_for_step  # noqa: E402


def step_5_name(d: date) -> str:
    return f"{STEP_5_PREFIX}_{d.strftime('%Y%m%d')}.yaml"


def step_6_name(d: date) -> str:
    return f"{STEP_6_PREFIX}_{d.strftime('%Y%m%d')}.md"


def save_brief_preview_assets(step6_markdown_path: Path, markdown_text: str) -> tuple[Path, Path]:
    rendered = render_kr36_brief_email(markdown_text, step6_markdown_path)
    html_output = step6_markdown_path.with_suffix(".html")
    legacy_email_output = step6_markdown_path.with_name(f"{step6_markdown_path.stem}_email.html")
    text_output = step6_markdown_path.with_name(f"{step6_markdown_path.stem}_email.txt")
    html_output.write_text(rendered.html, encoding="utf-8")
    legacy_email_output.write_text(rendered.html, encoding="utf-8")
    text_output.write_text(rendered.text, encoding="utf-8")
    return html_output, text_output


def main() -> None:
    parser = argparse.ArgumentParser(description="36Kr: regenerate step 6 from step 5 YAML.")
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
    step5 = run_dir / step_5_name(target_date)
    step6 = run_dir / step_6_name(target_date)
    if not step5.is_file():
        raise SystemExit(f"找不到 step5 文件: {step5}")

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
    html_out, txt_out = save_brief_preview_assets(step6, brief_markdown)
    print(f"Step 6 MD: {step6}")
    print(f"Step 6 HTML: {html_out}")
    print(f"Step 6 email HTML: {step6.with_name(step6.stem + '_email.html')}")
    print(f"Step 6 TXT: {txt_out}")


if __name__ == "__main__":
    main()
