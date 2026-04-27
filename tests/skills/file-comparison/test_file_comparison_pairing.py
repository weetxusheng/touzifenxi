from pathlib import Path

from file_comparison.compare.engine import build_pair_key, scan_folder_for_pairs


def test_build_pair_key_removes_month_token():
    key, month = build_pair_key(Path("/tmp/基金合同_6月.docx"), r"(?P<month>\d{1,2})月")

    assert key == "基金合同"
    assert month == 6


def test_scan_folder_for_pairs_picks_oldest_and_newest_versions(tmp_path):
    for name in ["基金合同_3月.docx", "基金合同_4月.docx", "基金合同_6月.docx", "单独文件.docx"]:
        (tmp_path / name).write_text("", encoding="utf-8")

    pairs = scan_folder_for_pairs(tmp_path, r"(?P<month>\d{1,2})月")

    assert len(pairs) == 1
    assert pairs[0].old_label == "基金合同_3月.docx"
    assert pairs[0].new_label == "基金合同_6月.docx"
