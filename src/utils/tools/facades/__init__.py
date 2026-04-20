"""集中存放历史兼容门面。

这里的模块只负责承接旧导入路径与历史公开 API；真正的实现已经拆到
`analysis`、`content`、`brief`、`steps` 等职责子包中。

本包不做预加载导入，避免兼容门面之间在初始化阶段产生循环依赖。
"""

from __future__ import annotations

__all__ = ["content", "content_analysis", "intelligence"]
