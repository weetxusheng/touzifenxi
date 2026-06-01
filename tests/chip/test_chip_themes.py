from __future__ import annotations

from utils.tools.content_models import StandardArticle
from utils.tools.research.chip_themes import CHIP_THEMES, classify_chip_themes


def _article(title: str, summary: str = "", content: str = "") -> StandardArticle:
    return StandardArticle(
        source_site="chip",
        source_bucket="semi",
        channel="semi",
        article_id="x",
        title=title,
        url="https://example.com/x",
        published_at="2026-05-19",
        author="",
        tags=[],
        keywords=[],
        summary=summary,
        content_text=content,
    )


def test_chip_themes_has_six_buckets():
    expected = {
        "新品发布", "价格变动", "投融资/并购",
        "产能/扩产", "供需/缺货", "技术节点突破",
    }
    assert set(CHIP_THEMES.keys()) == expected


def test_classify_product_launch():
    a = _article(title="格罗方德推出用于CPO的硅光子共封装先进光引擎方案")
    assert "新品发布" in classify_chip_themes(a)


def test_classify_price_change():
    a = _article(title="3月DRAM合约价环比上涨7%")
    assert "价格变动" in classify_chip_themes(a)


def test_classify_capacity():
    a = _article(title="年产360万平方米IC载板智能工厂封顶")
    assert "产能/扩产" in classify_chip_themes(a)


def test_classify_supply():
    a = _article(title="HBM供应紧张，下游加大下单")
    assert "供需/缺货" in classify_chip_themes(a)


def test_classify_node():
    a = _article(title="英特尔14A制程2029量产 18A工艺良率回升")
    assert "技术节点突破" in classify_chip_themes(a)


def test_classify_multilabel():
    a = _article(title="台积电推出2nm新工艺 良率达到目标")
    themes = classify_chip_themes(a)
    assert "新品发布" in themes
    assert "技术节点突破" in themes


def test_no_match_returns_empty():
    a = _article(title="美国就业数据创新高")
    assert classify_chip_themes(a) == []


from utils.tools.research.chip_themes import (
    classify_chip_themes_with_filter,
    is_semiconductor_article,
)


def test_classify_investment_ipo():
    a = _article(title="长江存储启动IPO辅导：中信证券、中信建投护航")
    assert "投融资/并购" in classify_chip_themes(a)


def test_classify_investment_acquisition():
    a = _article(title="蓝思科技拟收购巨腾国际控股权")
    themes = classify_chip_themes(a)
    assert "投融资/并购" in themes


def test_is_semiconductor_positive():
    a = _article(title="中芯国际发布26Q1财报 单季销售收入超25亿美元")
    assert is_semiconductor_article(a) is True


def test_is_semiconductor_negative_photovoltaic():
    a = _article(title="云南大理774.95MW风光项目启动竞配")
    assert is_semiconductor_article(a) is False


def test_is_semiconductor_negative_ev():
    a = _article(title="印度Ola Electric拟斥资2.085亿美元加码电动车及电芯业务")
    assert is_semiconductor_article(a) is False


def test_is_semiconductor_overlap_keeps_semi():
    """半导体公司涉足新能源车应该仍判为半导体（白名单优先黑名单）。"""
    a = _article(
        title="中芯国际增资10亿元拓展电动车相关业务",
        summary="存储芯片龙头中芯国际扩展电动车应用",
    )
    assert is_semiconductor_article(a) is True


def test_classify_with_filter_returns_tuple():
    a = _article(title="台积电2nm新工艺良率提升")
    themes, is_semi = classify_chip_themes_with_filter(a)
    assert is_semi is True
    assert "技术节点突破" in themes


def test_classify_with_filter_non_semi():
    a = _article(title="特斯拉放弃印度建厂计划：汽车供应链短板")
    themes, is_semi = classify_chip_themes_with_filter(a)
    assert is_semi is False
    assert themes == []
