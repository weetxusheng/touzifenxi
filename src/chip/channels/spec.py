"""ChipChannelSpec 定义 + CHANNELS 注册表。

实际 fetch_listing / fetch_article 实现见 channels/semi.py 与 channels/laoyaoba.py。
这里只是绑定 key → spec → callable 的路由表。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

from utils.tools.content_models import RawArticleDetail, RawArticleRef


@dataclass(frozen=True)
class ChipChannelSpec:
    """单个 channel 的元数据 + 抓取回调。"""

    key: str
    name: str
    listing_urls: tuple[str, ...]
    fetch_listing: Callable[["ChipChannelSpec", date], list[RawArticleRef]]
    fetch_article: Callable[["ChipChannelSpec", RawArticleRef], RawArticleDetail]


def _build_registry() -> dict[str, ChipChannelSpec]:
    # Import locally to avoid circular imports during test collection.
    from chip.channels import laoyaoba, semi

    return {
        "semi": ChipChannelSpec(
            key="semi",
            name="SEMI 中国",
            listing_urls=(
                "https://www.semi.org.cn/site/semi/",
                "https://www.semi.org.cn/site/semi/column/26595298402893836.html",
            ),
            fetch_listing=semi.fetch_listing,
            fetch_article=semi.fetch_article,
        ),
        "laoyaoba": ChipChannelSpec(
            key="laoyaoba",
            name="爱集微",
            listing_urls=(
                "https://www.laoyaoba.com/xinyaowen",
                "https://www.laoyaoba.com/jwfocus",
            ),
            fetch_listing=laoyaoba.fetch_listing,
            fetch_article=laoyaoba.fetch_article,
        ),
    }


CHANNELS: dict[str, ChipChannelSpec] = _build_registry()
