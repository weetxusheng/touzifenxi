# AGENTS

本文件是 `/Users/xusheng/Documents/project/touzifenxi` 的协作与开发总入口。

后续无论是人还是 agent，在这个项目里新增 Python 模块、提示词、skill、测试、打包脚本时，都应先看这里，再看详细规范文档和模板。

## 1. 入口规则

- 项目级开发与协作规范，以本文件为入口。
- 详细规范正文见：
  - `docs/project-conventions.md`
  - `docs/development-workflow.md`
  - `docs/skill-packaging.md`
- 如果本文件与其他零散说明冲突，以本文件和 `docs/project-conventions.md` 为准。

## 2. 本项目当前关注的规范范围

当前先统一两类规范：

1. Python 开发规范
2. Skill 开发与分发规范

## 3. 快速规则

### 3.1 Python

- 通用、可复用逻辑放 `src/touzifenxi/`
- CLI 只做参数入口和编排，不堆业务细节
- 新增公共函数尽量写类型标注
- 新增或修改需求前，先把主场景、边界场景、失败场景和 fallback 用例想清楚
- 需求完成后，必须对照上述用例至少执行一遍回归验证
- 注释只解释“为什么”，不解释显然动作
- 方法过长时先拆分；暂时不能拆时，必须在方法体关键子流程前写清边界和失败语义
- 原始数据、中间数据、报告、构建产物分目录存放

### 3.2 Skill

- 每个 skill 独立放在 `skills/<skill-name>/`
- `SKILL.md` 正文统一用中文
- 提示词、配置、脚本必须尽量留在 skill 自己目录内
- 单个 skill 专属的说明文档、运行机制文档、排障文档必须放在该 skill 目录内，优先使用 `skills/<skill-name>/docs/`
- skill 目录名、提示词文件名使用短横线风格
- 可打包产物输出到 `build/`

## 4. 目录原则

- `src/` 放项目级能力
- `skills/` 放可分发 skill；skill 专属文档、提示词、配置、脚本随对应 skill 存放
- `tests/` 放项目级测试
- `data/raw/` 放原始结果
- `data/processed/` 放中间结构化结果
- `reports/` 放简报和搜索清单
- `build/` 放 zip 等构建产物

## 5. 开发前自查

开始新增功能前，先确认：

1. 这段逻辑应该放 `src/` 还是 `skills/`
2. 提示词、配置、模板、skill 专属文档是否会被放错目录
3. 主流程场景、边界场景、失败场景和 fallback 用例是否已经先列清
4. 输出应该进入 `data/raw/`、`data/processed/`、`reports/` 还是 `build/`
5. 这次改动是否需要补测试
6. 完成后要按哪些用例逐条回归验证
7. 新增 skill 是否基于 `docs/templates/skill-template/` 创建
8. 是否需要运行 `validate-skill` 和 `package-skill`

## 6. 详细规范

完整规则见：

- [docs/project-conventions.md](/Users/xusheng/Documents/project/touzifenxi/docs/project-conventions.md)
- [docs/development-workflow.md](/Users/xusheng/Documents/project/touzifenxi/docs/development-workflow.md)
- [docs/skill-packaging.md](/Users/xusheng/Documents/project/touzifenxi/docs/skill-packaging.md)

后续如果项目进入更强约束阶段，再在这里补：

- 格式化与静态检查约定
- pre-commit 约定
- skill 打包检查流程
