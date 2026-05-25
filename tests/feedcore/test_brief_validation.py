from feedcore.brief_validation import brief_has_reasonable_length, compact_four_dimension_brief


def test_brief_has_reasonable_length_rejects_overlong_summary_for_short_source():
    source = "OpenAI announced a product update with limited concrete details. " * 4
    brief = "\n".join(
        [
            "#### 事实",
            "- OpenAI announced a product update.",
            "- The article discusses launch timing, partnerships, pricing, adoption, technical details, and market reaction.",
            "",
            "#### 背景",
            "- The report connects the launch to broader AI competition and enterprise demand.",
            "- It also revisits prior product milestones and infrastructure context.",
            "",
            "#### 产生的影响",
            "- The launch may influence customer adoption, partner expectations, and market narratives.",
            "- It may also affect procurement decisions, roadmap planning, and competitive positioning.",
            "",
            "#### 反面观点 / 数据矛盾点",
            "- The article does not provide enough primary detail to support this much expansion.",
            "- Several conclusions are broader than the sparse source text can justify.",
        ]
    )

    assert brief_has_reasonable_length(brief, source) is False


def test_brief_has_reasonable_length_accepts_concise_summary_for_longer_source():
    source = (
        "OpenAI announced a product update, described the release timing, listed partner integrations, "
        "explained enterprise adoption plans, outlined infrastructure costs, and noted unresolved risks. "
    ) * 12
    brief = "\n".join(
        [
            "#### 事实",
            "- OpenAI announced a product update and outlined release timing.",
            "",
            "#### 背景",
            "- The article places the launch in ongoing AI competition and enterprise adoption.",
            "",
            "#### 产生的影响",
            "- The update may shape customer demand and partner expectations.",
            "",
            "#### 反面观点 / 数据矛盾点",
            "- Cost pressure and unresolved risks remain.",
        ]
    )

    assert brief_has_reasonable_length(brief, source) is True


def test_compact_four_dimension_brief_compresses_short_source_into_sparse_points():
    source = "OpenAI announced a product update. Cost details were not disclosed. Release timing remains unclear."
    verbose = "\n".join(
        [
            "#### 事实",
            "- OpenAI announced a product update and described it at length with extra commentary that goes beyond the source.",
            "- The report also speculates on adoption, partner interest, and roadmap implications in a way that feels too long.",
            "",
            "#### 背景",
            "- The article is framed within broader AI competition, enterprise demand, past launches, and infrastructure pressure.",
            "",
            "#### 产生的影响",
            "- The update may affect product planning, enterprise adoption, procurement expectations, and competitive reactions.",
            "",
            "#### 反面观点 / 数据矛盾点",
            "- Cost details were not disclosed and release timing remains unclear, but the original summary expands this uncertainty too much.",
        ]
    )

    compact = compact_four_dimension_brief(verbose, source)

    assert len(compact) < len(verbose)
    assert compact.count("\n- ") <= 4
    assert "#### 事实" in compact
    assert "#### 背景" in compact
    assert "#### 产生的影响" in compact
    assert "#### 反面观点 / 数据矛盾点" in compact


def test_compact_four_dimension_brief_avoids_dangling_half_clauses():
    source = "x" * 214
    verbose = "\n".join(
        [
            "#### 事实",
            "- 政府正制定指令以应对美国国内市场牛肉短暂供应不足的情况。",
            "",
            "#### 背景",
            "- 相关措施原本旨在让更多进口牛肉流入美国，以降低终端价格并缓解供应压力。",
            "",
            "#### 产生的影响",
            "- 美国牧场主及牛肉生产商反对增加进口的立场可能影响政策落地。",
            "",
            "#### 反面观点 / 数据矛盾点",
            "- 牧场主和牛肉生产商担忧增加进口会削弱其业务。",
        ]
    )

    compact = compact_four_dimension_brief(verbose, source)

    assert "相关措施原本旨在让更多进口牛肉流入美国" in compact
    assert "立场可能" not in compact
    assert "反对增加进口的立场" in compact
