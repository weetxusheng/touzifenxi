# C114 搜索结果内置精筛提示词

你要判断同一 topic 下多条补充链接是否应保留进入后续正文抓取。

输入包括：
- `topic`
- 每篇原文的：
  - `original_title`
  - `original_published_at`
- 每条补充链接的：
  - `result_title`
  - `url`
  - `published_at`
  - `snippet`
  - `matched_terms`

判断要求：
1. 必须结合原标题判断是不是同一事件、是否有补充价值，不能只看域名。
2. 同一事件或明确有新增事实：`strong`
3. 同主题背景补充：`weak`
4. 主体跑偏、低质聚合、页面类型错误、重复价值低：`drop`
5. `reason` 和 `relevance_note` 要具体，不要写空话，不要模板复用。

输出要求：
1. 只返回单个 JSON 对象。
2. JSON 结构固定为：
```json
{
  "items": [
    {
      "original_title": "标题",
      "results": [
        {
          "url": "https://example.com/a",
          "keep_level": "strong",
          "reason": "一句中文原因",
          "relevance_note": "一句中文关系说明",
          "value_type": "新增事实"
        }
      ]
    }
  ]
}
```
3. 每篇原文都必须返回一项，每条补充链接也都必须返回一项，且 `original_title` / `url` 必须与输入完全一致。
4. `keep_level` 只能是：
  - `strong`
  - `weak`
  - `drop`
5. `value_type` 只能是：
  - `新增事实`
  - `同事件转载`
  - `背景补充`
  - `跑偏结果`
  - `低质聚合`
6. 不要返回 `review_status`，该字段由 Python 固定写成 `reviewed`。
7. 不要返回 Markdown，不要返回解释，不要返回额外字段。
