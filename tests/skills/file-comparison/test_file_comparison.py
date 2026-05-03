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


def test_output_document_paths_use_two_source_file_names(tmp_path):
    module = load_module()
    pair = module.PairMatch(
        pair_id="pair-001",
        key="基金合同",
        old_path=tmp_path / "创金合信基金合同_3月.docx",
        new_path=tmp_path / "创金合信基金合同_6月.docx",
        old_label="创金合信基金合同_3月.docx",
        new_label="创金合信基金合同_6月.docx",
    )

    docx_path, doc_path = module.build_output_document_paths(pair, tmp_path, timestamp="20260501_101530")

    assert docx_path.name == "创金合信基金合同_3月 与 创金合信基金合同_6月 对照表 20260501_101530.docx"
    assert doc_path.name == "创金合信基金合同_3月 与 创金合信基金合同_6月 对照表 20260501_101530.doc"
    assert "comparison" not in docx_path.name.lower()


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
    assert rows[0].old_text == "组织形式：有限责任公司\n……\n联系电话：0755-23838000"
    assert rows[0].new_text == "组织形式：股份有限公司\n……\n联系电话：0755-23839000"


def test_remove_fully_equal_lines_marks_omitted_middle_content():
    module = load_module()
    old_text = "\n".join(["旧变化一", "中间未变化段落", "旧变化二"])
    new_text = "\n".join(["新变化一", "中间未变化段落", "新变化二"])

    old_changed, new_changed = module.remove_fully_equal_lines(old_text, new_text)

    assert old_changed == "旧变化一\n……\n旧变化二"
    assert new_changed == "新变化一\n……\n新变化二"


def test_remove_fully_equal_lines_ignores_shifted_parenthesized_numbering():
    module = load_module()
    old_text = "\n".join(
        [
            "（24）基金在募集期间未能达到基金的备案条件，《基金合同》不能生效；",
            "（25）执行生效的基金份额持有人大会的决议；",
            "（26）建立并保存基金份额持有人名册；",
            "（27）法律法规及中国证监会规定的和《基金合同》约定的其他义务。",
        ]
    )
    new_text = "\n".join(
        [
            "（24）执行生效的基金份额持有人大会的决议；",
            "（25）建立并保存基金份额持有人名册；",
            "（26）法律法规及中国证监会规定的和《基金合同》约定的其他义务。",
        ]
    )

    old_changed, new_changed = module.remove_fully_equal_lines(old_text, new_text)

    assert old_changed == "（24）基金在募集期间未能达到基金的备案条件，《基金合同》不能生效；"
    assert new_changed == "删除"


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


def test_build_compare_units_for_llm_keeps_items_as_separate_units():
    module = load_module()
    old_sections = [
        module.Section(
            number="第三部分",
            title="第三部分  基金的基本情况",
            body="\n".join(
                [
                    "第三部分  基金的基本情况",
                    "一、基金名称",
                    "创金合信宜久来福3个月持有期混合型发起式基金中基金（FOF）",
                    "三、基金的运作方式",
                    "本基金对每份基金份额设置3个月的最短持有期。",
                ]
            ),
        )
    ]
    new_sections = [
        module.Section(
            number="第三部分",
            title="第三部分  基金的基本情况",
            body="\n".join(
                [
                    "第三部分  基金的基本情况",
                    "一、基金名称",
                    "创金合信宜久来福6个月持有期混合型发起式基金中基金（FOF）",
                    "三、基金的运作方式",
                    "本基金对每份基金份额设置6个月的最短持有期。",
                ]
            ),
        )
    ]

    units, summaries = module.build_compare_units_for_llm(old_sections, new_sections)

    assert [unit.subchapter for unit in units] == ["一、基金名称", "三、基金的运作方式"]
    assert units[0].old_text == "一、基金名称\n创金合信宜久来福3个月持有期混合型发起式基金中基金（FOF）"
    assert units[0].new_text == "一、基金名称\n创金合信宜久来福6个月持有期混合型发起式基金中基金（FOF）"
    assert summaries[0]["unit_count"] == 2


def test_build_compare_units_keeps_parenthesized_items_under_second_level_heading():
    module = load_module()
    old_sections = [
        module.Section(
            number="第七部分",
            title="第七部分 基金合同当事人及权利义务",
            body="\n".join(
                [
                    "第七部分 基金合同当事人及权利义务",
                    "一、基金管理人",
                    "（15）完全一致内容；",
                    "（16）办理基金认购、申购业务；",
                    "（17）继续一致内容；",
                    "（24）基金募集失败时退还认购人；",
                    "（25）执行生效的基金份额持有人大会的决议；",
                ]
            ),
        )
    ]
    new_sections = [
        module.Section(
            number="第七部分",
            title="第七部分 基金合同当事人及权利义务",
            body="\n".join(
                [
                    "第七部分 基金合同当事人及权利义务",
                    "一、基金管理人",
                    "（15）完全一致内容；",
                    "（16）办理基金申购业务；",
                    "（17）继续一致内容；",
                    "（24）执行生效的基金份额持有人大会的决议；",
                ]
            ),
        )
    ]

    units, summaries = module.build_compare_units_for_llm(old_sections, new_sections)

    assert len(units) == 1
    assert units[0].subchapter == "一、基金管理人"
    assert units[0].old_text.startswith("一、基金管理人\n")
    assert "（16）办理基金认购、申购业务；" in units[0].old_text
    assert "（24）基金募集失败时退还认购人；" in units[0].old_text
    assert all(not unit.subchapter.startswith("（") for unit in units)
    assert summaries[0]["unit_count"] == 1


def test_build_compare_units_groups_numbered_items_when_chinese_second_level_exists():
    module = load_module()
    old_sections = [
        module.Section(
            number="第七部分",
            title="第七部分 基金合同当事人及权利义务",
            body="\n".join(
                [
                    "第七部分 基金合同当事人及权利义务",
                    "一、基金管理人",
                    "1、基金管理人的权利包括但不限于：",
                    "（16）办理基金认购、申购业务；",
                    "2、基金管理人的义务包括但不限于：",
                    "（8）计算基金份额认购、申购价格；",
                    "（24）募集失败时退还基金认购人；",
                    "二、基金托管人",
                    "完全一致内容",
                ]
            ),
        )
    ]
    new_sections = [
        module.Section(
            number="第七部分",
            title="第七部分 基金合同当事人及权利义务",
            body="\n".join(
                [
                    "第七部分 基金合同当事人及权利义务",
                    "一、基金管理人",
                    "1、基金管理人的权利包括但不限于：",
                    "（16）办理基金申购业务；",
                    "2、基金管理人的义务包括但不限于：",
                    "（8）计算基金份额申购价格；",
                    "二、基金托管人",
                    "完全一致内容",
                ]
            ),
        )
    ]

    units, _summaries = module.build_compare_units_for_llm(old_sections, new_sections)

    assert len(units) == 1
    assert units[0].subchapter == "一、基金管理人"
    assert units[0].old_text.startswith("一、基金管理人\n")
    assert "1、基金管理人的权利包括但不限于：" in units[0].old_text
    assert "2、基金管理人的义务包括但不限于：" in units[0].old_text
    assert "（24）募集失败时退还基金认购人；" in units[0].old_text


def test_group_compare_units_into_batches_uses_chapter_count_limit():
    module = load_module()
    units = [
        module.CompareUnit("第一部分-unit-001", "第一部分", "第一部分  前言", "一、总则", "旧1", "新1"),
        module.CompareUnit("第一部分-unit-002", "第一部分", "第一部分  前言", "二、范围", "旧2", "新2"),
        module.CompareUnit("第二部分-unit-001", "第二部分", "第二部分  释义", "一、定义", "旧3", "新3"),
        module.CompareUnit("第三部分-unit-001", "第三部分", "第三部分  基本情况", "一、基金名称", "旧4", "新4"),
    ]

    batches = module.group_compare_units_into_batches(units, batch_size=2)

    assert [batch.batch_id for batch in batches] == ["batch-001", "batch-002"]
    assert batches[0].chapter_numbers == ("第一部分", "第二部分")
    assert [unit.unit_id for unit in batches[0].compare_units] == [
        "第一部分-unit-001",
        "第一部分-unit-002",
        "第二部分-unit-001",
    ]
    assert batches[1].chapter_numbers == ("第三部分",)


def test_group_compare_units_splits_oversized_four_chapter_batch_into_two_chapters():
    module = load_module()
    units = [
        module.CompareUnit("第一部分-unit-001", "第一部分", "第一部分  前言", "一、总则", "旧1" * 1200, "新1" * 1200),
        module.CompareUnit("第二部分-unit-001", "第二部分", "第二部分  释义", "一、定义", "旧2" * 1200, "新2" * 1200),
        module.CompareUnit("第三部分-unit-001", "第三部分", "第三部分  基本情况", "一、名称", "旧3" * 1200, "新3" * 1200),
        module.CompareUnit("第四部分-unit-001", "第四部分", "第四部分  发售", "一、发售", "旧4" * 1200, "新4" * 1200),
    ]

    batches = module.group_compare_units_into_batches(
        units,
        batch_size=4,
        max_batch_chars=10000,
        oversized_batch_size=2,
    )

    assert [batch.batch_id for batch in batches] == ["batch-001", "batch-002"]
    assert batches[0].chapter_numbers == ("第一部分", "第二部分")
    assert batches[1].chapter_numbers == ("第三部分", "第四部分")


def test_group_compare_units_splits_oversized_two_chapter_batch_to_single_chapters():
    module = load_module()
    units = [
        module.CompareUnit("第一部分-unit-001", "第一部分", "第一部分  前言", "一、总则", "旧1" * 2000, "新1" * 2000),
        module.CompareUnit("第二部分-unit-001", "第二部分", "第二部分  释义", "一、定义", "旧2" * 2000, "新2" * 2000),
    ]

    batches = module.group_compare_units_into_batches(
        units,
        batch_size=4,
        max_batch_chars=10000,
        oversized_batch_size=2,
    )

    assert [batch.batch_id for batch in batches] == ["batch-001", "batch-002"]
    assert batches[0].chapter_numbers == ("第一部分",)
    assert batches[1].chapter_numbers == ("第二部分",)


def test_group_compare_units_splits_many_compare_units_inside_same_chapter():
    module = load_module()
    units = [
        module.CompareUnit(
            f"第二十四部分-unit-{index:03d}",
            "第二十四部分",
            "第二十四部分  基金合同内容摘要",
            f"{index}、摘要",
            f"旧{index}",
            f"新{index}",
        )
        for index in range(1, 10)
    ]

    batches = module.group_compare_units_into_batches(
        units,
        batch_size=4,
        max_batch_chars=10000,
        oversized_batch_size=2,
        max_compare_units_per_batch=4,
    )

    assert [batch.batch_id for batch in batches] == ["batch-001", "batch-002", "batch-003"]
    assert [len(batch.compare_units) for batch in batches] == [4, 4, 1]
    assert all(batch.chapter_numbers == ("第二十四部分",) for batch in batches)


def test_group_compare_units_splits_oversized_single_unit_by_line_parts():
    module = load_module()
    old_lines = [f"旧第{index}行" + "甲" * 2000 for index in range(1, 5)]
    new_lines = [f"新第{index}行" + "乙" * 2000 for index in range(1, 5)]
    units = [
        module.CompareUnit(
            "第二十四部分-unit-001",
            "第二十四部分",
            "第二十四部分  基金合同内容摘要",
            "二、基金份额持有人大会召集、议事及表决的程序和规则",
            "\n".join(old_lines),
            "\n".join(new_lines),
        )
    ]

    batches = module.group_compare_units_into_batches(
        units,
        batch_size=4,
        max_batch_chars=10000,
        oversized_batch_size=2,
        max_compare_units_per_batch=4,
        max_compare_unit_chars=10000,
    )

    split_units = [unit for batch in batches for unit in batch.compare_units]
    assert [unit.unit_id for unit in split_units] == [
        "第二十四部分-unit-001-part-001",
        "第二十四部分-unit-001-part-002",
    ]
    assert all(len(unit.old_text) + len(unit.new_text) <= 10000 for unit in split_units)
    assert "\n".join(unit.old_text for unit in split_units) == "\n".join(old_lines)
    assert "\n".join(unit.new_text for unit in split_units) == "\n".join(new_lines)


def test_normalize_product_name_rows_moves_name_before_preface():
    module = load_module()
    rows = [
        module.ComparisonRow(chapter="第一部分  前言", old_text="旧前言", new_text="新前言"),
        module.ComparisonRow(
            chapter="第三部分  基金的基本情况",
            subchapter="一、基金名称",
            old_text="一、基金名称\n旧产品",
            new_text="一、基金名称\n新产品",
        ),
    ]

    normalized = module.normalize_product_name_rows(rows, "旧产品", "新产品")

    assert normalized[0].chapter == "基金名称"
    assert normalized[0].old_text == "旧产品"
    assert normalized[0].new_text == "新产品"
    assert [row.chapter for row in normalized[1:]] == ["第一部分  前言"]


def test_fund_name_detection_is_optional():
    module = load_module()
    rows = [module.ComparisonRow(chapter="第一部分  前言", old_text="旧前言", new_text="新前言")]

    assert module.extract_fund_name_or_empty("第一部分  前言\n没有基金名称字段") == ""
    assert module.normalize_product_name_rows(rows, "", "") == rows


def test_apply_section_skip_rules_trims_signature_tail():
    module = load_module()
    sections = [
        module.Section(
            number="第二十四部分",
            title="第二十四部分  基金合同内容摘要",
            body="\n".join(
                [
                    "第二十四部分  基金合同内容摘要",
                    "正文条款",
                    "本页无正文，为《基金合同》的签署页。",
                    "基金管理人：某某公司（盖章）",
                ]
            ),
        )
    ]

    filtered, records = module.apply_section_skip_rules(sections, ("签署页", "签字页", "盖章页", "签章页"))

    assert filtered == [
        module.Section(
            number="第二十四部分",
            title="第二十四部分  基金合同内容摘要",
            body="第二十四部分  基金合同内容摘要\n正文条款",
        )
    ]
    assert records == [
        {
            "section_number": "第二十四部分",
            "section_title": "第二十四部分  基金合同内容摘要",
            "action": "trim_tail",
            "reason": "signature_page",
            "matched_pattern": "签署页",
            "removed_line_count": 2,
        }
    ]


def test_apply_section_skip_rules_skips_signature_only_section():
    module = load_module()
    sections = [
        module.Section(
            number="第二十五部分",
            title="第二十五部分  签署页",
            body="第二十五部分  签署页\n基金管理人：某某公司（盖章）",
        )
    ]

    filtered, records = module.apply_section_skip_rules(sections, ("签署页", "签字页", "盖章页", "签章页"))

    assert filtered == []
    assert records[0]["action"] == "skip_section"
    assert records[0]["matched_pattern"] == "签署页"


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


def test_revision_runs_treat_embedded_short_equal_as_replacement():
    module = load_module()
    old_text = "依照《基金法》、基金合同及其他有关规定募集"
    new_text = "依照《基金法》、《创金合信宜久来福3个月持有期混合型发起式基金中基金（FOF）基金合同》及其他有关规定募集"

    old_paragraphs = module.build_old_revision_paragraphs(old_text, new_text)
    new_paragraphs = module.build_new_revision_paragraphs(old_text, new_text)
    struck = "".join(
        text
        for paragraph in old_paragraphs
        for text, style in paragraph
        if style.get("strike")
    )
    emphasized = "".join(
        text
        for paragraph in new_paragraphs
        for text, style in paragraph
        if style.get("bold") and style.get("color") == "0070C0"
    )

    assert "基金合同" in struck
    assert "《创金合信宜久来福3个月持有期混合型发起式基金中基金（FOF）基金合同》" in emphasized


def test_revision_runs_mark_highly_changed_short_title_as_full_replace():
    module = load_module()

    old_paragraphs = module.build_old_revision_paragraphs("第四部分  基金份额的发售", "第四部分  基金的历史沿革")
    new_paragraphs = module.build_new_revision_paragraphs("第四部分  基金份额的发售", "第四部分  基金的历史沿革")

    assert old_paragraphs == [
        [
            ("第四部分  ", {"size": 9.5}),
            ("基金份额的发售", {"strike": True, "color": "C00000", "size": 9.5}),
        ]
    ]
    assert new_paragraphs == [
        [
            ("第四部分  ", {"size": 9.5}),
            ("基金的历史沿革", {"bold": True, "underline": False, "color": "0070C0", "size": 9.5}),
        ]
    ]


def test_revision_runs_count_scattered_short_equals_as_full_replace():
    module = load_module()
    old_text = "自基金份额发售之日起最长不得超过3个月，具体发售时间见基金份额发售公告。"
    new_text = "创金合信宜久来福6个月持有期混合型发起式基金中基金（FOF）由创金合信宜久来福3个月持有期混合型发起式基金中基金（FOF）转型而来。"

    old_paragraphs = module.build_old_revision_paragraphs(old_text, new_text)
    new_paragraphs = module.build_new_revision_paragraphs(old_text, new_text)

    assert old_paragraphs == [[(old_text, {"strike": True, "color": "C00000", "size": 9.5})]]
    assert new_paragraphs == [[(new_text, {"bold": True, "underline": False, "color": "0070C0", "size": 9.5})]]


def test_revision_runs_smooth_changed_suffix_with_short_equals():
    module = load_module()
    old_text = "1、《基金合同》经基金管理人、基金托管人双方盖章以及双方法定代表人或授权代表签字或盖章并在募集结束后经基金管理人向中国证监会办理基金备案手续，并经中国证监会书面确认后生效。"
    new_text = "1、本《基金合同》由《创金合信宜久来福3个月持有期混合型发起式基金中基金（FOF）基金合同》修订而来。《基金合同》经基金管理人、基金托管人双方盖章以及双方法定代表人或授权代表签字或盖章，自202X年XX月XX日起，《创金合信宜久来福6个月持有期混合型发起式基金中基金（FOF）基金合同》正式生效，原《创金合信宜久来福3个月持有期混合型发起式基金中基金（FOF）基金合同》自同一日起失效。"

    old_paragraphs = module.build_old_revision_paragraphs(old_text, new_text)

    normal_text = "".join(
        text
        for paragraph in old_paragraphs
        for text, style in paragraph
        if not style.get("strike")
    )
    struck_text = "".join(
        text
        for paragraph in old_paragraphs
        for text, style in paragraph
        if style.get("strike")
    )

    assert "1、《基金合同》经基金管理人、基金托管人双方盖章以及双方法定代表人或授权代表签字或盖章" in normal_text
    assert struck_text == "并在募集结束后经基金管理人向中国证监会办理基金备案手续，并经中国证监会书面确认后生效。"


def test_revision_runs_mark_mostly_changed_body_as_full_replace():
    module = load_module()
    old_text = "甲" * 120 + "共同"
    new_text = "乙" * 120 + "共同"

    old_paragraphs = module.build_old_revision_paragraphs(old_text, new_text)
    new_paragraphs = module.build_new_revision_paragraphs(old_text, new_text)

    assert old_paragraphs == [[(old_text, {"strike": True, "color": "C00000", "size": 9.5})]]
    assert new_paragraphs == [[(new_text, {"bold": True, "underline": False, "color": "0070C0", "size": 9.5})]]


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
    assert module.display_text_with_subchapter("（16）办理基金申购业务；", "一、基金管理人") == "一、基金管理人\n（16）办理基金申购业务；"
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

    first_data_cell_pr = table._tbl.tr_lst[2].tc_lst[0].tcPr
    second_data_cell_pr = table._tbl.tr_lst[3].tc_lst[0].tcPr
    assert first_data_cell_pr.find(qn("w:vMerge")).get(qn("w:val")) == "restart"
    assert second_data_cell_pr.find(qn("w:vMerge")).get(qn("w:val")) is None


def test_write_docx_uses_compact_margins_and_chapter_column(tmp_path):
    pytest.importorskip("docx")
    module = load_module()
    output = tmp_path / "comparison.docx"

    module.write_docx(
        [module.ComparisonRow("第一部分  前言", "旧内容", "新内容")],
        output,
        "旧基金",
        "新基金",
    )

    from docx import Document

    document = Document(output)
    table = document.tables[0]
    assert round(document.sections[0].left_margin.cm, 1) == 1.0
    assert round(document.sections[0].right_margin.cm, 1) == 1.0
    assert round(table.columns[0].width.cm, 1) == 1.4
