# 📋 公众号文章总览页面功能需求

## 1. 功能概述

在现有解析流程基础上，增加**每日文章总览页面**功能，以卡片形式展示当天所有公众号文章的摘要和评分，支持快速浏览和跳转。

---

## 2. 详细需求

### 2.1 价值预筛阶段增强

**修改位置**：`src/gzh/src/gzh_pipeline/parse/value_gate.py`

#### 2.1.1 输出字段扩展

在现有的 `GatedSource` 数据结构中增加两个字段：

```python
@dataclass(frozen=True)
class GatedSource:
    article: SourceArticle
    valuable: bool
    category: str
    reason_zh: str
    # 新增字段 ✨
    star_rating: int          # 1-5星评分
    summary: str              # 40字以内简短摘要
```

#### 2.1.2 评分标准（1-5星）

| 星级 | 含义 | 判定标准 |
|------|------|----------|
| ⭐⭐⭐⭐⭐ (5星) | 极高价值 | 重大宏观政策、地缘政治事件、影响市场的核心数据、重磅行业变革 |
| ⭐⭐⭐⭐ (4星) | 高价值 | 重要公司动态、行业发展趋势、监管政策、市场数据分析 |
| ⭐⭐⭐ (3星) | 中等价值 | 一般性新闻报道、常规公告、信息量有限但有参考价值 |
| ⭐ (2星) | 低价值 | 活动通知、纯导流内容、重复转载无新增信息 |
| ⭐ (1星) | 无价值 | 硬广促销、抽奖招聘、情绪口号、标题党诱导点击 |

#### 2.1.3 提示词修改

修改 `value_gate_system_prompt()` 函数，要求大模型输出：
- `star_rating`：1-5的整数
- `summary`：40字以内的中文摘要（突出核心价值点）

**示例输出**：
```json
{
  "valuable": true,
  "category": "macro_geopolitics",
  "reason_zh": "中美高层会晤达成多项贸易协议，可能影响汇率和板块情绪",
  "star_rating": 5,
  "summary": "中美高层会晤达成贸易协议，涉及关税减免和技术合作，利好出口和科技板块"
}
```

---

### 2.2 总览页面生成

**新增文件**：`src/gzh/src/gzh_pipeline/cli/overview.py`

#### 2.2.1 数据来源

从以下位置读取数据：
- **审计日志**：`{PARSE_AUDIT_JSON_ROOT}/{date}/parse/{run_id}/{account}.trace.json`
- **解析产出**：`{PARSE_OUTPUT_ROOT}/{date}/{account}/{stem}.html`

从 `.trace.json` 的 `per_source_outputs` 数组中提取：
```json
{
  "stem": "article_001",
  "title": "文章标题",
  "valuable": true,
  "value_gate_category": "macro_geopolitics",
  "value_gate_reason_zh": "...",
  "star_rating": 5,        // 新增
  "summary": "..."         // 新增
}
```

#### 2.2.2 页面结构

**文件命名**：`index.html`  
**保存位置**：`{PARSE_OUTPUT_ROOT}/{date}/index.html`

**页面布局**：
```
┌─────────────────────────────────────────────┐
│  📊 今日总览 - 2026年05月22日                │
│  总文章数: 25  |  平均星级: 3.8  |  高价值: 12 │
├─────────────────────────────────────────────┤
│                                             │
│  【公众号A】 (共8篇)                         │
│  ┌──────────────┐  ┌──────────────         │
│  │ 文章标题1     │  │ 文章标题2     │         │
│  │ (摘要...)     │  │ (摘要...)     │         │
│  │ ⭐⭐⭐⭐⭐   │  │ ⭐⭐⭐⭐     │         │
│  └──────────────┘  └──────────────┘         │
│                                             │
│  【公众号B】 (共10篇)                        │
│  ┌──────────────┐  ┌──────────────┐         │
│  │ 文章标题1     │  │ 文章标题2     │         │
│  │ (摘要...)     │  │ (摘要...)     │         │
│  │ ⭐⭐⭐⭐⭐   │  │ ⭐⭐⭐       │         │
│  └──────────────┘  └──────────────┘         │
│                                             │
└─────────────────────────────────────────────┘
```

#### 2.2.3 排序规则

1. **按公众号分组**：每个公众号一个独立区域
2. **组内排序**：同一公众号内的文章按 `star_rating` 降序排列（5星在前）
3. **同星排序**：相同星级的文章按原标题字母顺序排列（保证稳定性）

#### 2.2.4 交互功能

- **点击文章卡片** → 在新标签页打开 `{stem}.html` 详细页面
- **悬停效果** → 显示完整的原因说明（`reason_zh`）
- **颜色标识**：
  - 5星：金色 (#FFD700)
  - 4星：绿色 (#4CAF50)
  - 3星：蓝色 (#2196F3)
  - 2星：橙色 (#FF9800)
  - 1星：红色 (#F44336)

#### 2.2.5 统计信息

页面顶部显示：
- 📊 **总文章数**：当天所有公众号的文章总数
-  **平均星级**：所有文章的平均评分（保留1位小数）
-  **高价值文章数**：4-5星文章数量
- 📈 **星级分布**：简单的柱状图或数字展示（如：5星: 8篇, 4星: 10篇...）

---

### 2.3 集成到现有流程

**修改文件**：`scripts/gzh/run_aggregate_parse.bat`

在脚本末尾（第 60 行之前）增加总览生成调用：

```batch
:: 生成当日总览页面
echo Generating overview page for %BIZ%...
%PYEXE% %PYVER% -u -m gzh_pipeline.cli.overview ^
  --biz-date "!BIZ!" ^
  --output-root "%GZH_PARSE_OUTPUT_DIR%" ^
  --audit-json-root "%GZH_PARSE_AUDIT_DIR%"

set "EXITCODE=!ERRORLEVEL!"
if not "!EXITCODE!"=="0" (
  echo WARNING: Overview generation failed with exit code !EXITCODE!
)
```

**新增批处理脚本**（可选）：`scripts/gzh/run_overview.bat`
- 用于单独重新生成某一天的总览页面
- 支持手动指定日期参数

---

### 2.4 技术实现要点

#### 2.4.1 HTML 模板设计

使用纯 HTML + CSS + JavaScript（单文件，无外部依赖）：
- **CSS**：使用 Flexbox/Grid 布局，响应式设计
- **JavaScript**：仅用于基本的交互（点击跳转），无需复杂逻辑
- **字体**：使用系统默认字体（Arial, Microsoft YaHei）

#### 2.4.2 性能考虑

- 如果某天文章数量超过 100 篇，考虑分页或懒加载
- 图片资源内联到 HTML（base64 编码星星图标）
- 避免使用大型 JS 库（如 jQuery、React）

#### 2.4.3 容错处理

- 如果某篇文章缺少 `star_rating` 或 `summary`，使用默认值（3星，空摘要）
- 如果 `.trace.json` 文件损坏或缺失，跳过该公众号并记录警告
- 如果当天无任何文章，生成空白页面并提示"今日无文章"

---

## 3. 实施步骤

### Phase 1: 修改价值预筛（核心）
1. 修改 `value_gate.py` 中的 `GatedSource` 数据结构
2. 修改 `value_gate_system_prompt()` 提示词
3. 修改 `assess_article_value()` 函数解析新字段
4. 测试验证评分和摘要生成的准确性

### Phase 2: 创建总览生成器
1. 新建 `overview.py` CLI 模块
2. 实现数据读取和聚合逻辑
3. 实现 HTML 模板渲染
4. 单元测试

### Phase 3: 集成与测试
1. 修改 `run_aggregate_parse.bat` 集成总览生成
2. 端到端测试（从抓取到总览生成）
3. UI/UX 优化
4. 边界情况测试（空数据、异常数据等）

### Phase 4: 文档与维护
1. 更新 README 或使用说明
2. 添加配置项说明（如是否启用总览生成）
3. 监控和日志完善

---

## 4. 验收标准

✅ 每篇文章都有 1-5 星评分和 40 字以内摘要  
✅ 总览页面按公众号分组，组内按星级降序排列  
✅ 点击文章卡片能正确跳转到详细页面  
✅ 页面顶部显示正确的统计信息  
✅ 每天自动生成独立的总览页面（`{date}/index.html`）  
✅ 无外部依赖，单 HTML 文件可独立打开查看  

---

## 5. 后续可扩展功能（非本次需求）

- 🔍 搜索功能（按标题/摘要关键词）
-  筛选功能（只看 4-5 星文章）
- 📥 导出 Excel/CSV
- 📅 多日汇总视图
- 📊 星级趋势图表

---

## 6. 相关文件清单

### 需要修改的文件
- `src/gzh/src/gzh_pipeline/parse/value_gate.py` - 增加评分和摘要字段
- `scripts/gzh/run_aggregate_parse.bat` - 集成总览生成调用

### 需要新增的文件
- `src/gzh/src/gzh_pipeline/cli/overview.py` - 总览页面生成器
- `scripts/gzh/run_overview.bat` - 独立总览生成脚本（可选）
- `docs/overview-feature-requirements.md` - 本文档

### 依赖的现有文件
- `src/gzh/src/gzh_pipeline/audit/trace.py` - 审计日志读写
- `src/gzh/src/gzh_pipeline/util/text.py` - 文本工具函数
- `src/gzh/src/gzh_pipeline/constants.py` - 常量定义

---

## 7. 环境变量配置

| 环境变量 | 作用 | 默认值 |
|---------|------|--------|
| `GZH_OVERVIEW_ENABLED` | 是否启用总览生成 | 1 (启用) |
| `GZH_OVERVIEW_MAX_ARTICLES` | 单天最大展示文章数 | 200 |
| `GZH_OVERVIEW_TEMPLATE` | 自定义HTML模板路径 | 内置默认模板 |

---

**文档版本**: v1.0  
**最后更新**: 2026-05-22  
**负责人**: TBD
