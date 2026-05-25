"""芯片产业主题白名单 + 关键词命中分类。

设计要点：
- 5 个核心桶 + "其他动态" + "非半导体内容"，避免桶过多稀释核心信号
- 关键词只在 title + summary 命中（不看 content_text）
- 命中时做"否定前缀扫描"，避免"放弃建厂"误归"产能/扩产"
- 字典顺序固定（决定简报渲染主次顺序）

非半导体过滤（硬/软两级白名单）：
- HARD 白名单（产业概念词：芯片/半导体/晶圆/HBM/NAND/...）命中 → 必是半导体
- SOFT 白名单（公司名：英伟达/三星电子/...）命中：
  - 若没有非半导体黑名单命中 → 视为半导体
  - 若同时命中非半导体黑名单 → 视为非半导体
- 只命中非半导体黑名单 → 非半导体
- 都没命中 → 默认视为相关
"""

from __future__ import annotations

from collections.abc import Mapping

from utils.tools.content_models import StandardArticle

# 5 桶：用户最关心 "新品/价格" 两个核心；其他半导体周边动态合并到 "产业动态" 一桶
CHIP_THEMES: dict[str, tuple[str, ...]] = {
    # —— 核心主桶 ——
    "新品发布": ("流片", "量产", "首发", "发布会", "新一代", "推出"),
    "价格变动": ("涨价", "调价", "提价", "降价", "合同价", "合约价", "长协价", "报价", "上涨"),
    # —— 资本运作 ——
    "投融资/并购": (
        "IPO", "上市辅导", "辅导备案", "募资", "申报", "科创板IPO",
        "收购", "并购", "控股权", "股权", "领投", "战略投资",
        "战略合作", "增资", "出资", "重整",
    ),
    # —— 产业动态：原产能/供需/节点/业绩/活动 合并为一桶 ——
    "产业动态": (
        # 产能/扩产
        "扩产", "新厂", "投产", "产能", "建厂", "封顶", "智能工厂",
        # 供需/缺货
        "缺货", "短缺", "供应紧张", "去库存", "下单", "订单",
        # 技术节点
        "nm", "EUV", "GAA", "良率", "工艺", "制程", "光刻",
        "HBM", "DDR", "3D NAND", "3D堆叠",
        "先进封装", "Chiplet", "RISC-V架构",
        # 业绩
        "Q1", "Q2", "Q3", "Q4", "财报", "营收", "盈利", "净利润", "同比", "环比", "业绩",
        # 行业活动
        "峰会", "论坛", "大会", "启幕", "校友", "嘉宾", "研讨会", "启动会",
    ),
}

# 硬半导体白名单
SEMICONDUCTOR_HARD_KEYWORDS: tuple[str, ...] = (
    "芯片", "半导体", "晶圆", "封测", "封装", "光刻", "制程", "EDA",
    "HBM", "DRAM", "NAND", "NOR", "SoC", "MCU", "MEMS",
    "IC载板", "IC设计", "集成电路", "代工", "Fab", "存储芯片",
    "射频", "光通信", "硅光", "CPO", "RISC-V", "GPU", "FPGA",
    "IP核", "光掩模", "CIS",
)

# 软半导体白名单（公司名）
SEMICONDUCTOR_SOFT_KEYWORDS: tuple[str, ...] = (
    "TSMC", "中芯国际", "长江存储", "新芯", "格罗方德",
    "英特尔", "三星电子", "美光", "海力士", "SK海力士", "镁光", "SMIC",
    "联电", "联发科", "ASML", "应用材料", "南亚科", "华虹", "华大九天",
    "纳芯微", "圣邦股份", "思瑞浦", "艾为电子", "上海贝岭", "希荻微",
    "德州仪器", "ADI", "英伟达",
)

SEMICONDUCTOR_KEYWORDS: tuple[str, ...] = SEMICONDUCTOR_HARD_KEYWORDS + SEMICONDUCTOR_SOFT_KEYWORDS

# 非半导体黑名单
NON_SEMICONDUCTOR_KEYWORDS: tuple[str, ...] = (
    # 新能源 / 电力
    "风电", "风光", "光伏", "电网", "发电", "新能源",
    # 汽车（含纯车企品牌名 — "汽车"虽含但车企标题里常不带"汽车"二字）
    "电动车", "整车", "新能源汽车", "汽车",
    "特斯拉", "比亚迪", "蔚来", "小鹏", "理想汽车", "Stellantis",
    "广汽", "东风", "上汽", "长安汽车", "吉利", "奇瑞",
    "Ola Electric",
    # 物流
    "无人物流", "物流车",
    # 文化娱乐
    "影视", "动画", "传媒", "影业",
    # 房地产
    "物业",
    # AI 软件 / 应用（非半导体硬件）—— "AI 芯片" 由 HARD 白名单"芯片"先救回
    "AI初创", "AI 初创", "AI大模型", "AI 大模型",
    "AI影视", "AI 影视", "生成式AI", "生成式 AI",
    "AI业务", "AI 业务",
)

# 否定前缀：关键词前 ≤NEGATION_WINDOW 字符内命中其一 → 视为否定语境，不算命中
NEGATION_PREFIXES: tuple[str, ...] = (
    "放弃", "取消", "终止", "暂停", "撤销", "撤回", "搁浅", "搁置",
    "未能", "无法", "未推进", "不再推进",
)
NEGATION_WINDOW: int = 12  # 字符数（中文按字符计；haystack 已 lower）

# 后向兼容（旧代码可能 import）
STRONG_INVESTMENT_KEYWORDS: tuple[str, ...] = (
    "IPO", "上市辅导", "辅导备案", "募资", "科创板IPO",
    "收购", "并购", "控股权", "领投", "战略投资", "重整",
)


def _haystack(article: StandardArticle) -> str:
    return " ".join((article.title, article.summary)).lower()


def _hit_any(haystack: str, keywords: tuple[str, ...]) -> bool:
    for kw in keywords:
        if kw.lower() in haystack:
            return True
    return False


def _is_keyword_negated_everywhere(haystack: str, keyword: str) -> bool:
    """关键词在 haystack 中所有命中位置是否都被否定前缀否定。

    返回 True：所有命中都在 NEGATION_PREFIXES 影响范围内（视为不命中）
    返回 False：至少一次命中是正面的（视为命中）
    """
    kw = keyword.lower()
    idx = 0
    found = False
    while True:
        pos = haystack.find(kw, idx)
        if pos < 0:
            break
        found = True
        before = haystack[max(0, pos - NEGATION_WINDOW):pos]
        if not any(neg.lower() in before for neg in NEGATION_PREFIXES):
            return False  # 至少一次正面命中
        idx = pos + len(kw)
    return found


def _hit_any_positive(haystack: str, keywords: tuple[str, ...]) -> bool:
    """与 _hit_any 类似，但跳过被否定前缀（放弃/取消/终止/...）覆盖的命中。"""

    for kw in keywords:
        if kw.lower() not in haystack:
            continue
        if not _is_keyword_negated_everywhere(haystack, kw):
            return True
    return False


def is_semiconductor_article(article: StandardArticle) -> bool:
    """判定文章是否半导体相关。

    决策流：
      1. 命中硬白名单 → True
      2. 命中非半导体黑名单 → False
      3. 命中软白名单 + 没命中黑名单 → True
      4. 都没命中 → True（默认）
    """

    haystack = _haystack(article)
    if _hit_any(haystack, SEMICONDUCTOR_HARD_KEYWORDS):
        return True
    if _hit_any(haystack, NON_SEMICONDUCTOR_KEYWORDS):
        return False
    if _hit_any(haystack, SEMICONDUCTOR_SOFT_KEYWORDS):
        return True
    return True


def classify_chip_themes(
    article: StandardArticle,
    *,
    themes: Mapping[str, tuple[str, ...]] | None = None,
) -> list[str]:
    """Return all themes whose keyword set hits the article text.

    使用否定感知命中（_hit_any_positive）：避免"放弃建厂"被归"产业动态"等反向陷阱。
    """

    catalog = themes if themes is not None else CHIP_THEMES
    haystack = _haystack(article)
    hits: list[str] = []
    for theme, keywords in catalog.items():
        if _hit_any_positive(haystack, keywords):
            hits.append(theme)
    return hits


def classify_chip_themes_with_filter(
    article: StandardArticle,
    *,
    themes: Mapping[str, tuple[str, ...]] | None = None,
) -> tuple[list[str], bool]:
    """返回 (themes, is_semiconductor)。

    决策顺序：
    1. 半导体公司软白命中 + 强投融资词命中 → 视为"半导体公司资本动作"，直接归
       "投融资/并购"，即便正文也提到 AI 软件 / 新能源 等非半导体业务。
       覆盖 case：英伟达领投印度 AI 初创 / 三星收购 X 公司 / 中芯入股 X / 等
    2. 否则按 is_semiconductor_article 判定半导体相关性
       - 非半导体 → ([], False)
       - 半导体 → 按 CHIP_THEMES 字典顺序返回命中主题
    """

    catalog = themes if themes is not None else CHIP_THEMES
    haystack = _haystack(article)

    # Rule 1: 半导体公司主动资本动作（即便涉及非半导体业务也视为半导体动态）
    soft_hit = _hit_any(haystack, SEMICONDUCTOR_SOFT_KEYWORDS)
    strong_invest_hit = _hit_any(haystack, STRONG_INVESTMENT_KEYWORDS)
    if soft_hit and strong_invest_hit:
        return ["投融资/并购"], True

    # Rule 2: 原逻辑
    if not is_semiconductor_article(article):
        return [], False
    return classify_chip_themes(article, themes=catalog), True
