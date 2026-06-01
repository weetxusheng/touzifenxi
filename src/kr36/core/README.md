# kr36.core

`kr36` 的核心编排层，目标是把「入口编排」与「抓取实现细节」分开，便于维护和逐步拆分。

## 当前结构

- `names.py`：流程产物命名约定。
- `settings.py`：路径与运行时目录初始化。
- `pipeline.py`：step4/5/6 的主编排。
- `source_adapter.py`：`source_adapter` 兼容入口（对外稳定）。
- `source/common.py`：已抽取的底层共用能力（安全文件名、日志写入、cookies/log 路径常量）。

## 拆分策略

当前采用「兼容优先」迁移：

1. 先抽公共基础能力到 `core/source/common.py`；
2. 旧入口 `kr36/source_adapter.py` 继续可用；
3. 后续再按职责继续拆 `source_adapter`（risk/topic/fetch/media），每一步都保持兼容壳不破坏现有调用。
