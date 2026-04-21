# ChinaWealth Scraper

面向 AI agent 的通用说明文档。

这个目录当前采用了一种 skill 风格的组织方式，但其中的核心能力并不依赖某个特定 agent 平台。只要某个 AI agent 具备以下能力，就可以复用这套抓取逻辑：

- 读取本地文件
- 执行 Python 脚本
- 访问互联网
- 在需要时安装或加载 OCR 依赖

因此，这份说明文档按“通用 agent 工具包”来写，而不是绑定到某一个 agent 产品。

## 1. 能力概述

`chinawealth-scraper` 用于抓取中国理财网产品列表数据，并导出为 CSV。

它不是解析网页 HTML，而是直接调用中国理财网后端接口，并处理以下协议细节：

- RSA 公钥交换
- AES-ECB 请求体加密
- `X-Nonce`、`X-Timestamp`、`X-Sign` 签名
- 二次校验触发后的点选汉字验证码处理
- 分页抓取与 CSV 导出

适用场景：

- 批量导出中国理财网理财产品列表
- 按不同筛选条件重复抓取
- 让 agent 自动执行导出任务
- 分析接口加密、签名或验证码逻辑
- 在不同 agent 系统中复用同一套抓取脚本

## 2. 目录结构

当前安装目录示例：

`C:\Users\zhanglimin202307\.codex\skills\chinawealth-scraper`

在不同 agent 环境中，你也可以把这套目录放到任意其他位置。下面文档中的 `<skill-root>` 都表示这套工具所在的根目录。

主要文件：

- `scripts/chinawealth_scraper.py`
  实际执行抓取的主脚本。任何 agent 都可以直接调用它。
- `references/protocol.md`
  协议参考，适合在接口失效、签名变化或验证码样式变化时加载。
- `SKILL.md`
  一份供 skill 型 agent 系统读取的工作流说明。对不支持 skill 的 agent 不是必需的。
- `agents/openai.yaml`
  一份供特定 UI 或 agent 平台使用的元数据。对大多数通用 agent 不是必需的。

如果你的 agent 平台不支持 “skill” 概念，也没有关系。最重要的是：

- 读取 `scripts/chinawealth_scraper.py`
- 在需要时参考 `references/protocol.md`
- 通过命令行调用脚本

## 3. 对 AI Agent 的通用接入要求

一个通用 AI agent 要稳定使用这套工具，至少应满足这些条件：

### 3.1 基础执行能力

- 能执行 `python <script>` 命令
- 能读取本地路径和输出文件
- 能访问 `https://www.chinawealth.com.cn`

### 3.2 Python 依赖

基础依赖：

- `requests`
- `cryptography`

验证码依赖，仅在命中二次校验时需要：

- `Pillow`
- `opencv-python`
- `numpy`
- `cnocr`

### 3.3 可选依赖目录

脚本会按以下顺序寻找额外依赖目录：

1. 环境变量 `CHINAWEALTH_VENDOR_DIR`
2. `scripts/_vendor`
3. 技能根目录 `_vendor`
4. 当前工作目录 `_vendor`

因此，不同 agent 系统可以有两种接入方式：

- 方式 A：把依赖直接安装到 Python 环境
- 方式 B：把依赖打包进 `_vendor`，由 agent 在运行时自动发现

## 4. 典型使用方式

### 4.1 作为命令行工具被 agent 调用

先做小规模探测：

```powershell
python "<skill-root>\scripts\chinawealth_scraper.py" `
  --max-pages 1 `
  --page-size 20 `
  --output ".\chinawealth_check.csv"
```

再做完整导出：

```powershell
python "<skill-root>\scripts\chinawealth_scraper.py" `
  --output ".\chinawealth_full.csv"
```

### 4.2 作为 agent 工作流中的可调用工具

推荐让 agent 按以下顺序执行：

1. 先跑 `--max-pages 1` 验证接口当前可用
2. 检查终端输出里的 `total rows`
3. 检查 CSV 表头和首批数据
4. 再执行全量抓取
5. 抓取完成后校验导出行数是否合理

### 4.3 作为协议分析参考

如果 agent 的任务不是“导出数据”，而是“修复/分析抓取逻辑”，优先读取：

- `scripts/chinawealth_scraper.py`
- `references/protocol.md`

这种模式适合：

- 逆向接口变化
- 修复签名失败
- 调整验证码识别逻辑
- 增加新的筛选参数

## 5. 核心实现逻辑

抓取流程如下：

1. 本地生成一对临时 RSA 密钥。
2. 把公钥发送到 `/m/n`。
3. 服务端返回加密结果，客户端用私钥解密，得到当前请求使用的 AES key。
4. 将查询参数压缩为紧凑 JSON。
5. 使用 AES-ECB 加密查询参数。
6. 生成 `nonce` 和毫秒级时间戳。
7. 用固定签名串 `hold?fish:palm` 参与 SHA-256 运算，生成 `X-Sign`。
8. 将加密数据提交到 `/prod/search`。
9. 若返回“二次校验”，则调用验证码接口并自动尝试 OCR 求解。
10. 首次成功后得到总条数与分页信息，再逐页抓取并导出 CSV。

二次校验逻辑不是简单字符验证码，而是点选汉字验证码。当前脚本采用两层策略：

- OCR 识别目标字位置
- 用颜色分割与连通域分析补充候选点

再对候选点做有限组合尝试，直到服务端接受。

## 6. 脚本参数

主脚本当前支持：

- `--output`
  输出 CSV 文件路径。
- `--page-size`
  每页抓取条数，默认 `100`。
- `--prod-status`
  产品状态筛选，默认 `02`。
- `--sleep-seconds`
  分页请求间隔秒数，默认 `4.0`。
- `--retries`
  单页失败后的最大重试次数，默认 `3`。
- `--captcha-rounds`
  验证码刷新轮数上限，默认 `8`。
- `--captcha-combinations`
  每轮验证码最多尝试的候选组合数，默认 `80`。
- `--max-pages`
  仅抓前几页，适合测试、探测和调试。

## 7. 输出内容

脚本输出两类结果：

- 终端日志
  包括总条数、总页数、页抓取进度、验证码求解进度、输出路径等。
- CSV 文件
  使用 UTF-8 BOM 编码，方便 Excel 直接打开。

常见字段包括：

- `prodId`
- `prodName`
- `prodStatus`
- `prodStatusName`
- `prodRegCode`
- `orgName`
- `prodCollectMethName`
- `prodOperateModeName`
- `prodTermName`
- `collCCY`
- `prodInvestNatureName`
- `prodRiskLevelName`
- `collSDate`
- `collEDate`
- `prodSDate`
- `prodEDate`

## 8. 给不同 AI Agent 的使用建议

### 8.1 对话型 agent

如果 agent 主要通过自然语言接收任务，建议把这个工具描述成：

“一个可以抓取中国理财网列表接口、自动处理加密和验证码、并导出 CSV 的 Python 工具。”

这样 agent 更容易决定：

- 什么时候直接调用脚本
- 什么时候转入协议分析模式

### 8.2 工作流型 agent

如果 agent 运行在工作流、调度器或自动化平台里，建议拆成两个阶段：

1. 探测阶段：只抓 1 页
2. 正式阶段：全量抓取

这样可以减少接口变化或验证码异常时的失败成本。

### 8.3 代码代理型 agent

如果 agent 具备改代码能力，建议遵循这条原则：

- 先改参数，不先改协议代码
- 先对照浏览器请求，不凭猜测改签名逻辑
- 先保存验证码样本，再改 OCR 阈值

## 9. 常见问题

### 9.1 为什么不能直接解析网页？

因为列表数据不是静态 HTML 内容，而是通过后端接口动态加载，并且请求体与签名都做了加密处理。

### 9.2 为什么会触发二次校验？

通常和访问频率、请求模式或站点风控有关。当前脚本内置了自动求解，但不能保证每次都一次成功。

### 9.3 如果导出行数和接口总数不一致怎么办？

先确认是不是用了 `--max-pages`。如果是完整抓取但数量不一致，优先重试缺失页，不要先怀疑 CSV 写出逻辑。

### 9.4 如果接口突然全部失败怎么办？

优先检查：

1. `/m/n` 是否还存在
2. `/prod/search` 的签名规则是否改变
3. 浏览器提交的 payload 是否变化
4. 验证码样式是否变化

这时应优先阅读 `references/protocol.md`。

## 10. 维护建议

- 先做 1 页验证，再跑全量
- 不要轻易降低 `--sleep-seconds`
- 修改筛选条件前先核对浏览器真实请求
- 如果只是新增导出任务，优先参数化，不要先重写协议逻辑
- 如果验证码成功率明显下降，先保存样本图，再调整识别逻辑

## 11. 可扩展方向

未来可以继续扩展：

- 增加更多筛选参数
- 支持按机构、期限、风险等级筛选
- 支持增量抓取
- 支持导出到数据库或 Excel
- 支持保存验证码样本用于回归测试

## 12. 结论

这套目录即使当前被放在某个特定 agent 的 skill 路径下，本质上仍然是一个“可被任意 AI agent 调用的抓取工具包”：

- `scripts/chinawealth_scraper.py` 负责执行
- `references/protocol.md` 负责解释协议
- 其他 agent 只要具备文件读取、命令执行和网络访问能力，就可以直接复用
