import importlib.util
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = PROJECT_ROOT / "skills" / "file-comparison" / "scripts" / "file_comparison.py"


def load_module():
    spec = importlib.util.spec_from_file_location("file_comparison", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_split_sections_handles_page_breaks_and_toc_numbers():
    module = load_module()
    text = """
封面
第一部分  前言\t1
第二部分  释义\t4

第一部分  前言
一、总则
旧内容
\f第二部分  释义
一、定义
新内容
""".strip()

    sections = module.split_sections(text)

    assert [section.number for section in sections] == ["第一部分", "第二部分"]
    assert sections[0].title == "第一部分  前言"
    assert sections[1].title == "第二部分  释义"
    assert "旧内容" in sections[0].body
    assert "新内容" in sections[1].body


def test_build_rows_splits_multiple_second_level_changes():
    module = load_module()
    old = module.Section(
        number="第一部分",
        title="第一部分  前言",
        body="\n".join(
            [
                "第一部分  前言",
                "三、设立方式",
                "旧段落A",
                "四、风险提示",
                "旧段落B",
            ]
        ),
    )
    new = module.Section(
        number="第一部分",
        title="第一部分  前言",
        body="\n".join(
            [
                "第一部分  前言",
                "三、设立方式",
                "新段落A",
                "四、风险提示",
                "新段落B",
            ]
        ),
    )

    rows = module.build_section_rows(old, new)

    assert len(rows) == 2
    assert [row.chapter for row in rows] == ["第一部分  前言", "第一部分  前言"]
    assert rows[0].subchapter == "三、设立方式"
    assert rows[0].old_text == "旧段落A"
    assert rows[0].new_text == "新段落A"
    assert rows[1].subchapter == "四、风险提示"
    assert rows[1].old_text == "旧段落B"
    assert rows[1].new_text == "新段落B"


def test_format_number_label_handles_word_decimal_list():
    module = load_module()

    assert module.format_number_label("decimal", "%1、", [3]) == "3、"


def test_numbered_definition_uses_short_subchapter_label():
    module = load_module()

    row = module.build_comparison_row(
        "第二部分  释义",
        "1、基金或本基金：指旧基金",
        "1、基金或本基金：指新基金",
    )

    assert row.subchapter == "1、基金或本基金："
    assert row.old_text == "1、基金或本基金：指旧基金"
    assert row.new_text == "1、基金或本基金：指新基金"


def test_build_rows_keeps_numbered_definitions_in_separate_rows():
    module = load_module()
    old = module.Section(
        number="第二部分",
        title="第二部分  释义",
        body="\n".join(
            [
                "第二部分  释义",
                "4、基金合同：指旧合同",
                "5、托管协议：指旧协议",
            ]
        ),
    )
    new = module.Section(
        number="第二部分",
        title="第二部分  释义",
        body="\n".join(
            [
                "第二部分  释义",
                "4、基金合同：指新合同",
                "5、托管协议：指新协议",
            ]
        ),
    )

    rows = module.build_section_rows(old, new)

    assert [row.subchapter for row in rows] == ["4、基金合同：", "5、托管协议："]
    assert all("……" not in row.old_text for row in rows)
    assert all("……" not in row.new_text for row in rows)


def test_build_rows_aligns_numbered_definitions_when_one_is_deleted():
    module = load_module()
    old = module.Section(
        number="第二部分",
        title="第二部分  释义",
        body="\n".join(
            [
                "第二部分  释义",
                "4、基金合同：指旧合同",
                "5、托管协议：指旧协议",
                "6、基金募集期：指旧募集期",
            ]
        ),
    )
    new = module.Section(
        number="第二部分",
        title="第二部分  释义",
        body="\n".join(
            [
                "第二部分  释义",
                "4、基金合同：指新合同",
                "5、托管协议：指新协议",
            ]
        ),
    )

    rows = module.build_section_rows(old, new)

    assert [row.subchapter for row in rows] == ["4、基金合同：", "5、托管协议：", "6、基金募集期："]
    assert rows[-1].new_text == "删除"


def test_build_rows_aligns_numbered_definitions_after_number_shift():
    module = load_module()
    old = module.Section(
        number="第二部分",
        title="第二部分  释义",
        body="\n".join(
            [
                "第二部分  释义",
                "7、基金份额发售公告：指旧公告",
                "8、基金产品资料概要：指旧概要",
                "9、法律法规：指旧法规",
            ]
        ),
    )
    new = module.Section(
        number="第二部分",
        title="第二部分  释义",
        body="\n".join(
            [
                "第二部分  释义",
                "7、基金产品资料概要：指新概要",
                "8、法律法规：指新法规",
            ]
        ),
    )

    rows = module.build_section_rows(old, new)

    assert [row.subchapter for row in rows] == [
        "7、基金份额发售公告：",
        "7、基金产品资料概要：",
        "8、法律法规：",
    ]
    assert rows[0].new_text == "删除"


def test_build_rows_skips_when_only_list_number_changes():
    module = load_module()
    old = module.Section(
        number="第二部分",
        title="第二部分  释义",
        body="\n".join(
            [
                "第二部分  释义",
                "8、法律法规：指中国现行有效并公布实施的法律法规",
            ]
        ),
    )
    new = module.Section(
        number="第二部分",
        title="第二部分  释义",
        body="\n".join(
            [
                "第二部分  释义",
                "7、法律法规：指中国现行有效并公布实施的法律法规",
            ]
        ),
    )

    assert module.build_section_rows(old, new) == []


def test_build_rows_omits_equal_lines_within_same_subchapter_block():
    module = load_module()
    old = module.Section(
        number="第三部分",
        title="第三部分  基金管理人",
        body="\n".join(
            [
                "第三部分  基金管理人",
                "一、基本情况",
                "设立日期：2014年7月9日",
                "批准设立机关及批准设立文号：中国证监会证监许可[2014]651号",
                "组织形式：有限责任公司",
                "注册资本：2.33亿元人民币",
                "存续期限：持续经营",
                "联系电话：0755-23838000",
            ]
        ),
    )
    new = module.Section(
        number="第三部分",
        title="第三部分  基金管理人",
        body="\n".join(
            [
                "第三部分  基金管理人",
                "一、基本情况",
                "设立日期：2014年7月9日",
                "批准设立机关及批准设立文号：中国证监会证监许可[2014]651号",
                "组织形式：股份有限公司",
                "注册资本：2.33亿元人民币",
                "存续期限：持续经营",
                "联系电话：0755-23839000",
            ]
        ),
    )

    rows = module.build_section_rows(old, new)

    assert len(rows) == 1
    assert rows[0].subchapter == "一、基本情况"
    assert rows[0].old_text == "组织形式：有限责任公司\n联系电话：0755-23838000"
    assert rows[0].new_text == "组织形式：股份有限公司\n联系电话：0755-23839000"


def test_preprocess_sections_for_llm_skips_unchanged_sections_and_keeps_only_changed_lines():
    module = load_module()
    old_sections = [
        module.Section(
            number="第一部分",
            title="第一部分  前言",
            body="\n".join(["第一部分  前言", "一、总则", "完全一致"]),
        ),
        module.Section(
            number="第三部分",
            title="第三部分  基金管理人",
            body="\n".join(
                [
                    "第三部分  基金管理人",
                    "一、基本情况",
                    "设立日期：2014年7月9日",
                    "组织形式：有限责任公司",
                    "联系电话：0755-23838000",
                ]
            ),
        ),
    ]
    new_sections = [
        module.Section(
            number="第一部分",
            title="第一部分  前言",
            body="\n".join(["第一部分  前言", "一、总则", "完全一致"]),
        ),
        module.Section(
            number="第三部分",
            title="第三部分  基金管理人",
            body="\n".join(
                [
                    "第三部分  基金管理人",
                    "一、基本情况",
                    "设立日期：2014年7月9日",
                    "组织形式：股份有限公司",
                    "联系电话：0755-23839000",
                ]
            ),
        ),
    ]

    reduced_old, reduced_new = module.preprocess_sections_for_llm(old_sections, new_sections)

    assert [section.number for section in reduced_old] == ["第三部分"]
    assert [section.number for section in reduced_new] == ["第三部分"]
    assert "设立日期：2014年7月9日" not in reduced_old[0].body
    assert "设立日期：2014年7月9日" not in reduced_new[0].body
    assert "组织形式：有限责任公司" in reduced_old[0].body
    assert "组织形式：股份有限公司" in reduced_new[0].body


def test_preprocess_sections_for_llm_marks_added_section_for_model():
    module = load_module()
    old_sections = []
    new_sections = [
        module.Section(
            number="第四部分",
            title="第四部分  基金的历史沿革",
            body="\n".join(["第四部分  基金的历史沿革", "新增段落"]),
        )
    ]

    reduced_old, reduced_new = module.preprocess_sections_for_llm(old_sections, new_sections)

    assert reduced_old[0].body == "第四部分  基金的历史沿革\n新增"
    assert reduced_new[0].body.endswith("新增段落")


def test_old_revision_runs_strike_only_replaced_characters():
    module = load_module()

    paragraphs = module.build_old_revision_paragraphs(
        "创金合信宜久来福3个月持有期混合型发起式基金中基金（FOF）",
        "创金合信宜久来福6个月持有期混合型发起式基金中基金（FOF）",
    )

    struck = [
        text
        for paragraph in paragraphs
        for text, style in paragraph
        if style.get("strike")
    ]
    unstruck = "".join(
        text
        for paragraph in paragraphs
        for text, style in paragraph
        if not style.get("strike")
    )
    assert struck == ["3"]
    assert "创金合信宜久来福" in unstruck
    assert "个月持有期" in unstruck


def test_old_revision_runs_strike_entire_text_for_deleted_rows():
    module = load_module()

    paragraphs = module.build_old_revision_paragraphs("基金份额发售公告：旧定义", "删除")

    assert paragraphs == [[("基金份额发售公告：旧定义", {"strike": True, "color": "C00000", "size": 9.5})]]


def test_new_revision_runs_underline_only_replaced_characters():
    module = load_module()

    paragraphs = module.build_new_revision_paragraphs(
        "创金合信宜久来福3个月持有期混合型发起式基金中基金（FOF）",
        "创金合信宜久来福6个月持有期混合型发起式基金中基金（FOF）",
    )

    emphasized = [
        text
        for paragraph in paragraphs
        for text, style in paragraph
        if style.get("bold") and style.get("underline")
    ]
    normal = "".join(
        text
        for paragraph in paragraphs
        for text, style in paragraph
        if not style.get("bold") and not style.get("underline")
    )
    assert emphasized == ["6"]
    assert [
        style.get("color")
        for paragraph in paragraphs
        for text, style in paragraph
        if text == "6"
    ] == ["0070C0"]
    assert "创金合信宜久来福" in normal
    assert "个月持有期" in normal


def test_new_revision_runs_underline_entire_text_for_added_rows():
    module = load_module()

    paragraphs = module.build_new_revision_paragraphs("新增", "新增章节内容\n第二行")

    assert paragraphs == [
        [("新增章节内容", {"bold": True, "underline": True, "color": "0070C0", "size": 9.5})],
        [("第二行", {"bold": True, "underline": True, "color": "0070C0", "size": 9.5})],
    ]


def test_new_revision_runs_do_not_underline_long_added_paragraphs():
    module = load_module()
    long_text = "这是一段较长的新增说明文字，用于避免整段文字都出现下划线导致阅读困难。"

    paragraphs = module.build_new_revision_paragraphs("新增", long_text)

    assert paragraphs == [[(long_text, {"bold": True, "underline": False, "color": "0070C0", "size": 9.5})]]


def test_new_revision_runs_do_not_underline_when_most_of_line_changed():
    module = load_module()

    paragraphs = module.build_new_revision_paragraphs(
        "（26）基金合同约定的其他义务。",
        "（26）法律法规及中国证监会规定的和《基金合同》约定的其他义务。",
    )

    changed_styles = [
        style
        for paragraph in paragraphs
        for text, style in paragraph
        if style.get("color") == "0070C0"
    ]
    assert changed_styles
    assert all(not style.get("underline") for style in changed_styles)


def test_display_text_includes_subchapter_when_body_does_not():
    module = load_module()
    row = module.ComparisonRow("第一部分  前言", "旧段落A", "新段落A", "三、设立方式")

    assert module.display_text_with_subchapter(row.old_text, row.subchapter) == "三、设立方式\n旧段落A"
    assert module.display_text_with_subchapter("三、设立方式\n旧段落A", row.subchapter) == "三、设立方式\n旧段落A"
    assert module.display_text_with_subchapter(
        "8、基金产品资料概要：指旧概要",
        "7、基金产品资料概要：",
    ) == "8、基金产品资料概要：指旧概要"


def test_write_docx_hides_subchapter_column_and_merges_same_chapter(tmp_path):
    pytest.importorskip("docx")
    module = load_module()
    output = tmp_path / "comparison.docx"
    rows = [
        module.ComparisonRow("第一部分  前言", "旧A", "新A", "三、设立方式"),
        module.ComparisonRow("第一部分  前言", "旧B", "新B", "四、风险提示"),
        module.ComparisonRow("第二部分  释义", "1、基金或本基金：指旧", "1、基金或本基金：指新", "1、基金或本基金："),
    ]

    module.write_docx(rows, output, "旧基金", "新基金")

    from docx import Document
    from docx.oxml.ns import qn

    document = Document(output)
    table = document.tables[0]
    assert len(table.columns) == 3
    assert table.rows[0].cells[1].text == "原《旧基金》版本"
    assert table.rows[0].cells[2].text == "修订后《新基金》版本"
    assert "二级内容" not in "\n".join(cell.text for row in table.rows for cell in row.cells)
    assert table.rows[2].cells[1].text.startswith("三、设立方式")

    first_data_cell_pr = table.rows[2].cells[0]._tc.get_or_add_tcPr()
    second_data_cell_pr = table.rows[3].cells[0]._tc.get_or_add_tcPr()
    assert first_data_cell_pr.find(qn("w:vMerge")).get(qn("w:val")) == "restart"
    assert second_data_cell_pr.find(qn("w:vMerge")).get(qn("w:val")) is None
