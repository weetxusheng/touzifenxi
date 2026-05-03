import importlib.util
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = PROJECT_ROOT / "skills" / "file-comparison" / "scripts" / "rerender_from_run.py"


def load_rerender_module():
    spec = importlib.util.spec_from_file_location("rerender_from_run", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_rerender_output_paths_use_two_source_file_names(tmp_path):
    module = load_rerender_module()
    pair = module.PairMatch(
        pair_id="pair-001",
        key="基金合同",
        old_path=tmp_path / "3月.docx",
        new_path=tmp_path / "6月.docx",
        old_label="3月.docx",
        new_label="6月.docx",
    )
    pair_dir = tmp_path / "pairs" / "pair-001"

    docx_path, doc_path = module.output_paths(pair_dir, pair=pair, overwrite=False, timestamp="20260501_180101")

    assert docx_path.name == "3月 与 6月 对照表 20260501_180101.docx"
    assert doc_path.name == "3月 与 6月 对照表 20260501_180101.doc"
    assert "comparison" not in docx_path.name.lower()
