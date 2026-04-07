# C114 正文分析内置提示词

你要根据已抓到的正文内容，为每篇文章填写结构化分析结果。

要求：
1. 必须先读 `original_content`，再参考 `selected_contents`，不要跳过原文。
2. 优先提炼正文里已经明确出现的事实、观点、数据和动作，不要补充无依据判断。
3. `selected_contents` 只作为补充信息来源，不能喧宾夺主。
4. 如果补充链接之间互相矛盾，要在 `risk_or_uncertainty` 中写清楚。
5. `new_facts` 只写可以直接作为新增信息使用的事实、数据或动作。
6. `why_it_matters` 必须说明为什么值得继续跟踪，不能空泛。

输出要求：
1. 只返回单个 JSON 对象。
2. JSON 必须包含以下字段，且全部非空：
```json
{
  "summary": "一句话概括核心内容",
  "core_points": ["核心点1", "核心点2", "核心点3"],
  "new_facts": ["新增事实1"],
  "entities": ["主体1"],
  "signals": ["信号1"],
  "risk_or_uncertainty": ["风险1"],
  "why_it_matters": "为什么重要",
  "layer_notes": ["层内备注1"]
}
```
3. `core_points`、`new_facts`、`entities`、`signals`、`risk_or_uncertainty`、`layer_notes` 必须是数组。
4. 不要返回 Markdown，不要返回解释，不要返回额外字段。
