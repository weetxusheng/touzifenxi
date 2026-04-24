# utils.steps

统一的步骤模块目录，覆盖两类能力：

1. 公用工作流步骤（原 `utils.tools.steps`）
2. 36kr 专用步骤（原 `kr36` 中的 step 模块）

## 目录说明

- `step1_analysis.py`
  - 作用：Step 1 单篇文章结构化分析与 topic brief 聚合
  - 现状：兼容导出，实际实现仍在 `utils.tools.steps.step1_analysis`
- `step1_5_topic_grouping.py`
  - 作用：Step 1.5 批量主题归并（LLM + checkpoint）
  - 现状：兼容导出，实际实现仍在 `utils.tools.steps.step1_5_topic_grouping`
- `step2_keywords.py`
  - 作用：Step 2 搜索关键词生成
  - 现状：兼容导出，实际实现仍在 `utils.tools.steps.step2_keywords`
- `step4_content_fetch.py`
  - 作用：Step 4 正文抓取与去重
  - 现状：兼容导出，实际实现仍在 `utils.tools.steps.step4_content_fetch`
- `step5_content_analysis.py`
  - 作用：Step 5 正文分析（LLM 批处理）
  - 现状：兼容导出，实际实现仍在 `utils.tools.steps.step5_content_analysis`
- `step6_brief.py`
  - 作用：Step 6 简报生成
  - 现状：兼容导出，实际实现仍在 `utils.tools.steps.step6_brief`
- `kr36_synthetic_workflow_from_step1.py`
  - 作用：把 `ArticleAnalysis` 转成 36kr 的“无外搜”抓取输入（步骤 2 前置）
  - 来源：从 `kr36/synthetic_workflow_from_step1.py` 迁入
- `kr36_topic_focus_step15.py`
  - 作用：36kr 专题聚焦 Step 1.5~6（专题条目解析、视频 CDN 下载、音频抽取、ASR 转写）
  - 来源：从 `kr36/topic_focus_step15.py` 迁入

## 导入约定

新代码优先从 `utils.steps` 导入；旧路径保留兼容壳：

- `kr36/synthetic_workflow_from_step1.py` -> `utils.steps.kr36_synthetic_workflow_from_step1`
- `kr36/topic_focus_step15.py` -> `utils.steps.kr36_topic_focus_step15`

## 迁移策略

本次为“稳定优先”的收敛迁移：

- 对原公用步骤采用兼容导出，避免一次性改动过大。
- 对 36kr 步骤切换到 `utils.steps` 作为主入口。
- 旧路径保留薄封装，便于逐步替换外部调用。

