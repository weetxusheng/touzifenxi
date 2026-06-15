/**
 * 上传期 .doc -> .docx 异步转换的前端 UI 与决策。
 *
 * 暴露 (window.fileComparisonConversion):
 *   - Card(props)                组件：进度卡片，自带轮询，转换完毕自动收起轮询。
 *                                props: uploadId, initialStatus, onChange?
 *   - confirmCreateIfNeeded(scanPayload, antdModal): Promise<boolean>
 *                                "发起生成" 前调用：转换未完成时弹确认框；用户选"等待"返回 false。
 *   - isTerminal(status: string): boolean
 *
 * 设计要点：
 *   - 部署前缀从 window.API_PREFIX 读取（index.html 顶部 inline script 注入），不再逐层透传。
 *   - 卡片轮询周期 1.5s，进入终止状态(completed/partial/failed) 自动停轮询。
 *   - 后端兜底允许"边转边发起生成"（pair_compare 会回退现场转换），所以确认框默认 allow-continue。
 *   - 不依赖 React.useState 之外的外部状态：组件可以独立挂在 app.js 任何位置。
 */
(function () {
  const React = window.React;
  const antd = window.antd;
  const html = window.htm.bind(React.createElement);

  const POLL_INTERVAL_MS = 1500;
  const TERMINAL_STATUSES = ["completed", "partial", "failed"];

  function isTerminal(status) {
    return TERMINAL_STATUSES.indexOf(status) !== -1;
  }

  function buildStatusUrl(uploadId) {
    // 部署前缀统一从 window.API_PREFIX 读，由 index.html 顶部 inline script 注入。
    const prefix = window.API_PREFIX || "";
    return prefix + "/api/file-comparison/upload/" + encodeURIComponent(uploadId) + "/conversion-status";
  }

  async function fetchStatusOnce(uploadId) {
    const resp = await fetch(buildStatusUrl(uploadId));
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    return await resp.json();
  }

  function ConversionStatusCard(props) {
    const uploadId = props.uploadId;
    const onChange = props.onChange;
    const initialStatus = props.initialStatus;

    const [status, setStatus] = React.useState(initialStatus || null);
    const [pollError, setPollError] = React.useState("");

    React.useEffect(function () {
      if (!uploadId) return undefined;
      let cancelled = false;
      let timer = null;

      // 如果初始状态就是终止态，不发轮询。
      if (initialStatus && isTerminal(initialStatus.status)) {
        if (onChange) onChange(initialStatus);
        return undefined;
      }

      async function tick() {
        if (cancelled) return;
        try {
          const payload = await fetchStatusOnce(uploadId);
          if (cancelled) return;
          setStatus(payload);
          setPollError("");
          if (onChange) onChange(payload);
          if (isTerminal(payload.status)) return;
        } catch (err) {
          if (cancelled) return;
          setPollError(err.message || "状态查询失败");
        }
        timer = setTimeout(tick, POLL_INTERVAL_MS);
      }
      tick();
      return function () {
        cancelled = true;
        if (timer) clearTimeout(timer);
      };
    }, [uploadId]);

    if (!uploadId) return null;
    if (!status) return null;
    if (!status.total_count) return null; // 没有需要转换的文件就不渲染

    const Card = antd.Card;
    const Progress = antd.Progress;
    const Tag = antd.Tag;
    const Typography = antd.Typography;
    const List = antd.List;
    const Text = Typography.Text;

    const total = status.total_count || 0;
    const done = status.completed_count || 0;
    const failed = status.failed_count || 0;
    const finishedOrFailed = done + failed;
    const percent = total ? Math.floor((finishedOrFailed / total) * 100) : 0;
    const isDone = isTerminal(status.status);

    const headline = isDone
      ? failed > 0
        ? ".doc → .docx 转换完成（含 " + failed + " 个失败）"
        : ".doc → .docx 转换完成"
      : "正在把 .doc 转成 .docx：" + done + "/" + total;

    const progressStatus = failed > 0 ? "exception" : isDone ? "success" : "active";

    const failedItems = (status.items || []).filter(function (it) {
      return it.status === "failed";
    });

    return html`
      <div className="conversion-status-panel" style=${{ marginTop: 8 }}>
        <${Card} size="small" bordered=${false} className="dense-card">
          <div style=${{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <${Text} strong=${true}>${headline}<//>
            ${failed > 0 ? html`<${Tag} color="error">失败 ${failed}<//>` : null}
            ${isDone && failed === 0 ? html`<${Tag} color="success">就绪<//>` : null}
            ${!isDone ? html`<${Tag} color="processing">进行中<//>` : null}
          </div>
          <${Progress}
            percent=${percent}
            size="small"
            status=${progressStatus}
            style=${{ marginTop: 6, marginBottom: failedItems.length ? 4 : 0 }}
          />
          ${failedItems.length
            ? html`
              <${List}
                size="small"
                bordered=${false}
                dataSource=${failedItems}
                renderItem=${function (it) {
                  return html`
                    <${List.Item} style=${{ padding: "4px 0" }}>
                      <${Text} type="danger" style=${{ marginRight: 8 }}>${it.original_name}<//>
                      <${Text} type="secondary">${it.error}<//>
                    <//>
                  `;
                }}
              />
              <${Text} type="secondary" style=${{ display: "block", marginTop: 4, fontSize: 12 }}>
                建议重新上传上述失败文件；仍可点「发起生成」，系统会在生成阶段再次尝试转换。
              <//>
            `
            : null}
          ${pollError
            ? html`<${Text} type="warning" style=${{ display: "block", marginTop: 4, fontSize: 12 }}>轮询出错：${pollError}<//>`
            : null}
        <//>
      </div>
    `;
  }

  async function confirmCreateIfNeeded(scanPayload, antdModal) {
    if (!scanPayload) return true;
    let current = scanPayload.conversion_status || null;
    // 用 conversion_status.upload_id 拉一次最新——避免基于上传当时的快照决策。
    const uploadId = current && current.upload_id;
    if (uploadId) {
      try {
        current = await fetchStatusOnce(uploadId);
      } catch (_) {
        /* 网络失败就按 scanPayload 缓存决策 */
      }
    }
    if (!current) return true;
    if (!current.total_count) return true; // 没有 .doc 需要转换
    if (isTerminal(current.status)) return true;

    return await new Promise(function (resolve) {
      antdModal.confirm({
        title: ".doc 文件还在转换",
        content:
          ".doc → .docx 转换尚未完成（" +
          (current.completed_count || 0) +
          "/" +
          (current.total_count || 0) +
          "）。继续会在生成阶段现场转换剩余文件，速度较慢；建议等待转换完成。",
        okText: "继续发起",
        cancelText: "等待转换",
        onOk: function () {
          resolve(true);
        },
        onCancel: function () {
          resolve(false);
        },
      });
    });
  }

  window.fileComparisonConversion = {
    Card: ConversionStatusCard,
    confirmCreateIfNeeded: confirmCreateIfNeeded,
    isTerminal: isTerminal,
  };
})();
