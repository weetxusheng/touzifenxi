# 架构说明

## 目标

建立一套面向 A 股 3-6 个月中线风格轮动的多 Agent 投研系统，要求：

- 全市场或可配置大样本股票池
- 真实行情与财务数据
- 每日盘后运行
- 推荐结果持久化
- 可跟踪、可复盘、可回测

## 系统分层

### 1. Data Layer

- `universe`
  - 股票主表
  - 行业映射
  - 风格标签
  - 流动性过滤
- `market`
  - 日线行情
  - 成交额和换手
  - 波动率
  - 均线和动量衍生字段
- `fundamental`
  - ROE
  - 营收/利润增速
  - 估值因子
  - 现金流因子
- `event`
  - 业绩预告
  - 公告
  - 政策
  - 景气跟踪

### 2. Research Layer

所有 Agent 必须统一输入和输出。

输入：

- 交易日
- 股票池
- 当日市场快照
- 历史衍生指标

输出：

- `score`
- `reason`
- `risk_flag`
- `evidence`

第一批 Agent：

- `style_agent`
- `fundamental_agent`
- `technical_agent`
- `risk_agent`
- `event_agent`

后续新增：

- `macro_agent`
- `industry_rotation_agent`
- `capital_flow_agent`

### 3. Portfolio Layer

- 行业集中度上限
- 风格暴露上限
- 单票风险预算
- 流动性底线
- 组合换手控制

### 4. Validation Layer

- 每日推荐表
- 推荐后 1/5/20/60 日表现
- 胜率、盈亏比、最大回撤
- Agent 归因

## 存储

使用 SQLite 作为第一阶段持久化方案：

- `research_runs`
- `recommendations`
- `agent_scores`

后续如果数据量扩大，再切换 PostgreSQL/Parquet。

## 当前重构范围

本次重构先落：

- 系统目录结构
- SQLite 持久化
- 研究运行流水线
- 结果归档
- CLI 入口

暂不在本次内解决：

- 全市场增量行情同步
- 回测引擎
- 事件抓取集群
