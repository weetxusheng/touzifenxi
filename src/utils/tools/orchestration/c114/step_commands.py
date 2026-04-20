"""单步命令门面。

真实实现按职责拆到更细的命令模块中，这里只保留兼容导出。
"""

from __future__ import annotations

from .analysis import handle_analyze_command
from .content import (
    handle_analyze_content_command,
    handle_fetch_content_command,
    handle_review_brief_command,
)
from .hot_topics import handle_hot_topics_command
from .search import handle_search_command

__all__ = [
    "handle_analyze_command",
    "handle_analyze_content_command",
    "handle_fetch_content_command",
    "handle_hot_topics_command",
    "handle_review_brief_command",
    "handle_search_command",
]
