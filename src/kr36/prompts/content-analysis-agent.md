# 36Kr 正文分析（含专题子项）

你要根据已抓到的正文内容，为每篇文章填写结构化分析结果。

## 通用规则

1. 若该条**不是**专题子项（见下节「专题子项」）：必须先读 `original_content`，再参考 `selected_contents`，不要跳过原文。
2. 优先提炼正文里已经明确出现的事实、观点、数据和动作，不要补充无依据判断。
3. `selected_contents` 只作为补充信息来源，不能喧宾夺主。

## 专题子项（当输入 JSON 中 `kr36_topic_subitem` 为 true，或存在非空 `topic_fulltext_excerpt`）

1. **阅读顺序**：优先以 `topic_fulltext_excerpt`（专题侧已落盘的全文或视频 ASR 转写）为主要事实与观点来源；`original_content` 可能仅为视频播放页/列表页的碎片，仅作辅证；再参考 `selected_contents`。
2. **提炼标题**：结合 `group_topic_name`（本批聚合主题名，即所属专题/栏目线）与 `original_title`（列表子项标题），在心中先形成**更短、更像「看点」的标题**（不超过 24 字），该短标题将写入下述 `summary` 的「提炼标题」段。
3. **summary 格式**（该条必须严格遵守）：
   - 固定为：`提炼标题：……；内容要点：……`
   - **提炼标题**：即上一步的短标题，与专题名、子项标题呼应，避免堆砌原题。
   - **内容要点**：严格依据 `topic_fulltext_excerpt` 与可用的 `original_content` 信息，用 1～2 句概括可验证的要点，避免臆测。

## 输出要求

1. 只返回符合调用方要求形状的 JSON 对象（单篇或 `topic`+`items` 批量，以用户消息为准），不要加 Markdown 说明。
2. 每条/每篇的 JSON 中必须至少包含非空的 `summary` 与 `core_points` 数组；其中 **专题子项** 的 `summary` 必须采用上节「`提炼标题：……；内容要点：……`」格式。
3. 对于专题子项，`core_points` 可列 2～4 条更细的可验证信息，与内容要点互补；非专题子项的 `summary` 可为一句概括，同时 `core_points` 至少 1 条。
4. 可额外返回：`new_facts`、`entities`、`signals`、`risk_or_uncertainty`、`why_it_matters`、`layer_notes`（若返回，数组型字段须为 JSON 数组）。
5. 不要返回解释性废话，不要多返字段名以外的顶层键（批量响应时除外）。
