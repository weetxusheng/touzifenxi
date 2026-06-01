# FeedCore Remote Fetcher

这个目录是外网抓取机的完整自包含部署包。远端服务只负责 RSS 拉取、文章网页抓取、正文抽取和 artifact 打包；模型分析仍在本地分析机执行。

## 目录内容

- `app.py`: 自包含远端抓取服务实现。
- `run_fetcher.py`: Python 启动入口。
- `run_fetcher.bat`: Windows 启动脚本。
- `run_fetcher.sh`: Linux/macOS 启动脚本。
- `requirements.txt`: 远端服务额外依赖。
- `.env.example`: 环境变量示例。

## 部署步骤

在服务器上只需要上传整个 `feedcore_remote_fetcher` 文件夹，不需要上传 `src/`、`pyproject.toml` 或本地分析代码。部署时可以保持原名，或在服务器上重命名为 `remote_fetcher` 以简化路径。

推荐服务器目录结构：

```text
/root/remote_fetcher/
  app.py
  run_fetcher.py
  requirements.txt
  README.md
```

然后进入 `remote_fetcher` 目录：

```bash
cd /root/remote_fetcher
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Windows PowerShell：

```powershell
cd C:\remote_fetcher
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -r requirements.txt
python -m playwright install chromium
```

## systemd（Linux 服务器）

仓库内提供示例单元文件：`deploy/feedcore-remote-fetcher.service` 与 `deploy/README.md`。

若 `systemctl status` 显示 **Deactivated successfully** 且服务立刻退出，多半是 `ExecStart` 写错（例如只执行了 `venv/bin/activate`、用了 `Type=oneshot`、或 `run_fetcher.sh` 未常驻）。请用 venv 内 python 绝对路径启动 `run_fetcher.py`，并执行：

```bash
sudo journalctl -u feedcore-remote-fetcher -n 80 --no-pager
```

## 启动

先设置访问令牌：

```bash
export FEEDCORE_FETCHER_TOKEN="your-token"
```

Windows：

```bat
set FEEDCORE_FETCHER_TOKEN=your-token
```

启动服务：

```bash
python run_fetcher.py --host 0.0.0.0 --port 3000 --output-dir remote_output
```

### 通过 Cloudflare Worker 抓取 RSS 和文章

推荐把 RSS XML 和文章 HTML 抓取转发到 Cloudflare Worker，腾讯云服务器只负责 API、任务状态、正文抽取和 artifact 打包。

先部署仓库根目录下的 `worker/`，然后在腾讯云服务器上设置：

```bash
export FEEDCORE_WORKER_FETCH_URL="https://feedcore-fetch-proxy.<your-subdomain>.workers.dev/fetch"
export FEEDCORE_WORKER_TOKEN="your-worker-token"
```

也可以通过启动参数传入：

```bash
python run_fetcher.py \
  --host 0.0.0.0 \
  --port 3000 \
  --output-dir remote_output \
  --worker-fetch-url "https://feedcore-fetch-proxy.<your-subdomain>.workers.dev/fetch" \
  --worker-token "your-worker-token"
```

配置 `FEEDCORE_WORKER_FETCH_URL` 后，RSS 和普通文章 HTML 抓取都会走 Worker；未配置时会保持原来的直连/代理模式。

### 可选：传统出站代理

如果仍需要用代理，抓取服务会读取：

1. `FEEDCORE_FETCHER_PROXY`（例如 `http://127.0.0.1:7890`）
2. 否则回退到 shell 里的 `http_proxy` / `HTTPS_PROXY` 等环境变量

`browser.enabled=true` 时的 Playwright 仍使用这个代理配置。Worker 只能代理 HTTP 抓取，不能替代浏览器渲染。

代理可用性测试：

```bash
curl --proxy http://127.0.0.1:7890 -I "https://news.google.com"
```

## API

所有任务接口都建议带上：

```http
Authorization: Bearer your-token
```

创建抓取任务：

```http
POST /api/fetch-runs
```

请求体示例：

```json
{
  "client_run_id": "local_20260518",
  "config": {
    "rss_sources": [
      {
        "url": "https://news.google.com/rss/search?q=OpenAI&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "default_category": "人工智能与科技"
      }
    ],
    "quick_sample_size": 10,
    "max_articles": 0,
    "workflow": {
      "rss_concurrency": 5,
      "article_fetch_concurrency": 8
    },
    "browser": {
      "enabled": true,
      "timeout": 60
    }
  }
}
```

并发建议：

- `rss_concurrency` 控制 RSS 源抓取并发；不传时默认 `10`。
- `article_fetch_concurrency` 控制文章正文抓取的“源级并发”；不传时默认 `10`。即多个 RSS 源同时抓，单个源内部仍顺序抓文章，避免对同一站点瞬时请求过多。
- 如果 `browser.enabled=true`，建议先把 `article_fetch_concurrency` 控制在 `5-10`，因为 Playwright 并发会明显消耗 CPU 和内存。

查询状态：

```http
GET /api/fetch-runs/{run_id}
```

下载 artifact：

```http
GET /api/fetch-runs/{run_id}/artifact
```

本地确认 artifact 下载并解压成功后，删除远端临时目录：

```http
DELETE /api/fetch-runs/{run_id}
```

返回完整 JSON 结果：

```http
GET /api/fetch-runs/{run_id}/result
```

任务完成后，每篇文章还会写入 artifact zip，客户端下载 artifact 后可在本地直接打开：

```text
output/<run_id>/articles/yyyyMMdd/rss001_a001.html
```

`step3_article_contents.json` 中会记录相对路径：

```json
{
  "stored_html_path": "articles/yyyyMMdd/rss001_a001.html",
  "stored_html_url": null
}
```

artifact zip 内包含：

- `manifest.json`
- `step1_rss_sources.json`
- `step2_feed_items.json`
- `step3_article_contents.json`
- `articles/`
- `rss_tasks/`
- `logs/`

本地分析机后续只需要消费 `step3_article_contents.json`。
