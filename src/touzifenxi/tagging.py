from __future__ import annotations


SECTOR_KEYWORDS = [
    ("银行", "银行"),
    ("证券", "非银金融"),
    ("保险", "非银金融"),
    ("电力", "公用事业"),
    ("能源", "能源"),
    ("煤", "煤炭"),
    ("油", "能源"),
    ("化学", "化工"),
    ("化工", "化工"),
    ("半导体", "半导体"),
    ("芯片", "半导体"),
    ("电子", "电子"),
    ("通信", "通信"),
    ("光", "通信"),
    ("医药", "医药"),
    ("药", "医药"),
    ("生物", "医药"),
    ("白酒", "消费"),
    ("食品", "消费"),
    ("乳业", "消费"),
    ("家电", "家电"),
    ("汽车", "汽车"),
    ("科技", "科技"),
    ("软件", "计算机"),
    ("计算", "计算机"),
    ("传媒", "传媒"),
    ("军工", "军工"),
    ("有色", "有色"),
    ("铜", "有色"),
    ("金", "有色"),
    ("地产", "地产"),
    ("机械", "机械"),
]


def infer_sector(name: str, board: str) -> str:
    for keyword, sector in SECTOR_KEYWORDS:
        if keyword in name:
            return sector
    if board == "STAR":
        return "科技"
    if board == "GEM":
        return "成长"
    return "综合"


def infer_style_tags(board: str, latest_price: float, turnover_ratio: float, sector: str) -> list[str]:
    tags: list[str] = []
    if board == "GEM":
        tags.append("成长")
    elif board == "STAR":
        tags.extend(["成长", "科技"])
    else:
        tags.append("价值")

    if sector in {"银行", "公用事业", "能源"}:
        tags.append("红利")
    if sector in {"半导体", "电子", "通信", "计算机", "科技"} and "科技" not in tags:
        tags.append("科技")
    if sector in {"化工", "有色", "能源", "机械"}:
        tags.append("周期")
    if turnover_ratio >= 5:
        tags.append("高换手")
    if latest_price >= 200:
        tags.append("高价")
    return tags
