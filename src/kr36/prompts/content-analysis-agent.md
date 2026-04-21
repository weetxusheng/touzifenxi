# 36Kr 正文分析内置提示词

你要根据已抓取的正文内容，生成结构化分析。

要求：
1. 必须先读 `original_content`，再参考 `selected_contents`。
2. 如果 `selected_contents` 为空，禁止臆造补充来源结论。
3. 优先提炼“专题、深度栏目、活动信息”相关事实，不要写空泛口号。
4. 活动类内容可优先识别：名称、时间、地点、主题（若正文可得）。
5. 快讯式碎片信息和广告性表达应弱化，不作为核心结论。

输出要求：
1. 只返回单个 JSON 对象。
2. JSON 必须至少包含以下字段且非空：
```json
{
  "summary": "一句话概括核心内容",
  "core_points": ["核心点1", "核心点2", "核心点3"]
}
```
3. 可选字段：`new_facts`、`entities`、`signals`、`risk_or_uncertainty`、`why_it_matters`、`layer_notes`。
4. `core_points` 必须是数组；可选数组字段若返回也必须是数组。
5. 不要返回 Markdown，不要返回解释，不要返回额外字段。

