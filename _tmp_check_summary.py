import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
os.environ["PYTHONIOENCODING"] = "utf-8"

from pathlib import Path
from utils.tools.facades.content_analysis import load_content_analysis_inputs

p = Path(r"output\reports\kr36_report\kr36_search_202604232049\kr36_step_5_content_analysis_20260423.yaml")
payload = load_content_analysis_inputs(p)
for category in payload.categories:
    for item in category.items:
        title = item.original_title or ""
        summary = (item.analysis.summary or "").strip()
        print(f"[{title[:50]}]")
        print(f"  summary={summary[:120]}")
        print()
