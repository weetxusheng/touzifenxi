---
name: infoq-daily-hot-topics
description: 当用户需要抓取、搜索、分析并汇总 InfoQ 中文站当天热点文章时使用。
---

# InfoQ 当日简报 Skill

## 适用场景

- 用户希望抓取 InfoQ 中文站当天首页新文章
- 用户希望复用现有日报分析链路，生成 InfoQ 独立简报
- 用户希望验证多站点日报扩展能力，而不影响现有 C114 skill

## 当前范围

- 仅覆盖：
  - `InfoQ 中文站首页新文章`
  - `InfoQ 中文站热点详情接口`
- 产出形态：
  - 独立 InfoQ 日报
- 不做：
  - 全站覆盖
  - 多语言版本
  - 与 C114 合并成总日报

## 运行方式

推荐直接运行完整链路：

- `python scripts/infoq.py run --date YYYY-MM-DD`

若只想先抓当天文章物料：

- `python scripts/infoq.py infoq-hot-topics --date YYYY-MM-DD`

## 输出目录

默认写到 skill 自带输出目录：

- `output/reports/infoq_report/infoq_search_<timestamp>/`

每轮目录下包含：

- `logs/`
- `checkpoints/`
- `infoq_step_1_analysis_YYYYMMDD.csv`
- `infoq_step_2_search_checklist_YYYYMMDD.yaml`
- `infoq_step_3_search_results_YYYYMMDD.yaml`
- `infoq_step_4_content_YYYYMMDD.yaml`
- `infoq_step_5_content_analysis_YYYYMMDD.yaml`
- `infoq_step_6_brief_YYYYMMDD.md`

## 说明

- 当前 InfoQ skill 复用了已验证过的 C114 后半段分析链路
- 站点差异主要在采集与标准化层
- 共享标准文章模型位于项目级：
  - `src/touzifenxi/content_sources/`
- 列表抓取当前默认使用 InfoQ 首页新文章接口，不再依赖旧的热榜列表
