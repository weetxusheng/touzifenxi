# 开发流程

本文档定义本项目新增 Python 功能、修复缺陷、编写 skill、补提示词和打包前的标准开发顺序。

## 1. 先判断归属

开始写代码前，先判断这次改动属于哪一类：

- 项目通用能力
  - 放 `src/touzifenxi/`
- 单个 skill 私有能力
  - 放 `skills/<skill-name>/`
- 开发规范与流程
  - 放 `AGENTS.md` 或 `docs/`

如果一段逻辑未来可能被多个 skill 复用，默认上收 `src/touzifenxi/`。

## 2. 测试先行

默认顺序：

1. 先列主流程、边界、失败和 fallback 用例
2. 先补失败测试或准备对应验证方式
3. 再写最小实现
4. 再做整理和文档补充

推荐检查命令：

```bash
python -m pytest
ruff check .
ruff format --check .
pre-commit run --all-files
```

## 3. Python 开发顺序

推荐步骤：

1. 确认模块职责和输入输出
2. 列出主流程、边界、失败和 fallback 用例
3. 新增或补充测试
4. 写最小实现
5. 补类型标注、docstring、边界注释
6. 对照用例逐条验证输出路径、返回结构和兼容性

不要把网络抓取、解析、导出、CLI 混在一个新增函数里。

## 4. Skill 开发顺序

新增 skill 时按下面顺序进行：

1. 从 `docs/templates/skill-template/` 复制目录骨架
2. 先列清 skill 的主流程、边界、失败和 fallback 用例
3. 用中文填写 `SKILL.md`
4. 将提示词写入 `prompts/`
5. 将配置写入 `config/`
6. 将运行入口放入 `scripts/`
7. 补充最小测试
8. 运行 `validate-skill`
9. 对照用例执行一遍完整验证
10. 运行 `package-skill`

## 5. 文档与产物

- 原始数据：`data/raw/`
- 中间结构化结果：`data/processed/`
- 简报和搜索清单：`reports/`
- 打包产物：`build/`

任何新能力都要明确自己落在哪一层，不允许临时写到仓库根目录。
