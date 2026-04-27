# Prompt 目录说明

当前 `file-comparison` skill 的模型提示词仍以内嵌字符串形式保存在运行代码中，原因是：

- 当前只有一条结构化比较提示词
- 需要和 JSON Schema、provider 请求体一起紧密维护
- 还没有拆分出多步骤、多角色 prompt 的需求

保留 `prompts/` 目录是为了满足 skill 统一目录规范，并为后续演进预留位置。

若后续需要引入：

- 分章节分析 prompt
- 汇总说明 prompt
- controller-agent 模式 manifest prompt

应优先新增到本目录，而不是散落到仓库其它位置。
