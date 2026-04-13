# Skill 打包与校验规范

本项目的 skill 默认以 `skills/<skill-name>/` 为最小分发单元。

## 1. 打包前要求

一个可打包 skill 至少包含：

- `SKILL.md`
- `agents/agent.yaml`
- `config/`
- `prompts/`
- `scripts/`
- `src/`
- `output/`
- `docs/`（可选；只放该 skill 专属说明、运行机制、排障手册）

其中：

- `SKILL.md` 正文必须使用中文
- 提示词、配置、模板不得依赖 skill 目录外的绝对路径
- `scripts/` 应只承担 skill 的编排入口
- `src/` 应只放该 skill 的私有运行代码
- `docs/` 应只放该 skill 专属文档；跨 skill 的项目规范仍放项目级 `docs/`
- `src/` 下的长期维护 Python 文件必须具备模块级 docstring；公开数据类、入口方法、关键业务方法应有 docstring
- `config/runtime.local.json` 属于本地配置，不进入分发包
- 一个 skill 默认只保留一个总览文档：`SKILL.md`；若需要更细的运行机制、排障、配置说明，必须放在 skill 内的 `docs/` 或 `config/README.md`
- skill 内不放正式测试代码，测试统一收口到项目级 `tests/skills/`
- `config/` 若包含 JSON 配置模板，则必须配套 `config/README.md` 或在 `SKILL.md` 中逐项解释 key 含义
- 若 skill 依赖配置或外部 API，`SKILL.md` 必须明确写出：
  - 哪些 key / 配置是硬门槛
  - 它们分别卡在哪些步骤
  - 哪些步骤可以在未配置完整时先跑
  - 是否存在代理 / SSL / 公司证书链等额外环境前提

## 2. 不允许进入分发包的内容

以下内容不应被打进 zip：

- `__pycache__/`
- `.pyc`
- 临时输出
- 本地调试缓存
- 非 skill 必需的构建产物
- skill `output/` 下的运行结果、历史报告、缓存和状态文件
- 本地配置，例如 `config/runtime.local.json`

## 3. 标准命令

校验：

```bash
PYTHONPATH=src python3 -m touzifenxi.cli validate-skill --skill websearch
```

打包：

```bash
PYTHONPATH=src python3 -m touzifenxi.cli package-skill --skill websearch
```

默认输出到：

```text
build/<skill-name>-skill.zip
```

## 4. 推荐流程

1. 完成目录骨架
2. 填写中文 `SKILL.md`
3. 确认提示词、配置、脚本和 skill 专属文档都在 skill 目录中
4. 运行校验
5. 再执行打包
