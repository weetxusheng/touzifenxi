# Prompt 目录说明

## 结构化比较（主流程）

- 默认提示词文件：[compare-structured-instructions.md](compare-structured-instructions.md)
- 运行时代码在 `OpenAIResponsesClient` 构造时读取该文件（UTF-8），并作为请求体里的 `instructions` 字段传给模型。
- 可覆盖方式：
  - `OpenAIResponsesClient(..., compare_instructions="...")` 直接传入完整字符串；
  - `OpenAIResponsesClient(..., compare_instructions_path=Path("..."))` 从任意路径读取 Markdown。

JSON Schema、provider 请求体仍在 `src/file_comparison/llm/` 中与客户端一并维护；提示词正文以本目录 Markdown 为单一事实来源，避免冗长字符串挤在代码里。

## 后续可扩展

若需要引入分章节分析、汇总说明、controller-agent manifest 等 prompt，应优先新增到本目录，而不是散落到仓库其它位置。
