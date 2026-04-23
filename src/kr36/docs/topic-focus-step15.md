# 专题聚焦 Step 1.5（`topic_focus_step15`）

与主流程四步（`pipeline.md`：分析 → 拉正文 → 整合 → 简报）中的 **Step 1**（文章理解/分组）**不是同一步**：本页描述的是 **36kr 专题链路**「从 `/topics/` 到本地视频/文章落盘 + 视频转写」的可复用分段，实现见 `src/kr36/topic_focus_step15.py`，编排见 `Kr36SourceAdapter` 中专题下载与 `_download_topic_item_asset`。

## 步骤总览（1.5 专题内有序步骤）

| 段 | 含义 | 产物/说明 |
|----|------|-----------|
| 1 | `/topics/` 列表解析 + 按关键词选聚焦专题 | 专题 `RawArticleRef` 列表 |
| 2 | 专题详情页 HTML 解析 | 时间窗内子项（`metadata.topic_item_kind` 等） |
| 3 | 子页目标：视频规范到 `https://36kr.com/video/{id}`，文章保留 `/p/` | `SubpageForDownload` |
| 4 | 再抓子页 HTML（`Adapter._fetch_text`；壳页/风控可走 Playwright+滑块） | 含 `initialState` / `<video>` 的 HTML |
| 5 | 从 HTML 抽 CDN → Referer+Cookie 下载 `*.mp4`（或失败则 `*.html`） | 同目录旁路文件 |
| **6** | **ffmpeg 抽 `*.asr.mp3` → 火山豆包「大模型录音文件极速版」→ `*.transcript.txt` + `*.asr.json`** | **专题视频的「全文」以转写为准** |

## 专题与主流程「全文」的关系（重要）

- **专题来源**的条目在 `metadata` 中带 `topic_item_kind`（`video` / `article`）。
- **视频**：播放页正文对业务价值有限；**步骤 2 不应再对同一视频链重复拉「文章 HTML 当全文」**。已跑通 ASR 时，**全文 = 同目录 `basename.transcript.txt`**（与 `basename.mp4`、`basename.asr.mp3` 同名 stem）。
- **文章**：仍以专题子链抓下的 `*.html` 或后续主流程 Step2 对 `/p/` 的正文为准（与历史 `fetch_article` 语义一致）。

配置项在 `config/runtime.local.json` → `sources.kr36`：`topic_extract_audio_enabled`、`topic_asr_enabled`、`volc_speech_*`、限长等，见 `runtime.example.json` 中 `_topic_asr_hint`。

## 专题全文索引 JSON（与 `kr36_hot_topics_*.json` 同目录）

抓取（`kr36-hot-topics` / `kr36-topics-only`）或 `kr36-videos-from-json` 完成后，会在 **同一 `run_dir`** 下生成：

| 文件 | 说明 |
|------|------|
| `kr36_hot_topics_YYYYMMDD.json` | 文章列表（`content_text` 多为空） |
| **`kr36_topic_fulltext_YYYYMMDD.json`** | 仅专题子项：`items[].content_text` 为 **ASR 或子页 HTML**，`fulltext_source` 标明来源；**整理/后续步骤请用** `kr36.topic_fulltext_index.load_kr36_topic_fulltext_json` 或 `load_content_by_article_id_from_topic_fulltext(run_dir, report_date)` 按 `article_id` 取全文，**不必再对视频 URL 拉网页当正文**。 |

实现：`src/kr36/topic_fulltext_index.py`，在 `fetch_and_materialize_kr36_articles` 与 `run_kr36_videos_from_hot_topics_json` 末尾写入。

## 相关文件

| 模块 | 作用 |
|------|------|
| `topic_focus_step15.py` | Step 1–5 纯函数 + **Step 6** 转写封装（与线上一致） |
| `topic_fulltext_index.py` | 汇总专题全文落盘为 `kr36_topic_fulltext_*.json` |
| `volc_speech.py` | 火山 HTTP `bigmodel/recognize/flash` |
| `topic_media.py` | CDN 下载、ffmpeg 抽 MP3 |
| `source_adapter.py` | `_download_topic_item_asset` 串联 4→5→6 |

CLI：`python -m kr36.cli kr36-transcribe-audio --audio <path/to/basename.asr.mp3>` 仅重跑 Step 6（不下载视频）。
