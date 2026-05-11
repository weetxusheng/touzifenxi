(function () {
  const React = window.React;
  const ReactDOM = window.ReactDOM;
  const antd = window.antd;
  const html = window.htm.bind(React.createElement);

  const {
    App,
    Button,
    Card,
    ConfigProvider,
    Input,
    Popconfirm,
    Select,
    Space,
    Table,
    Tag,
    Typography,
    Upload,
  } = antd;
  const theme = antd.theme;
  const Title = Typography.Title;
  const Paragraph = Typography.Paragraph;
  const Text = Typography.Text;
  const Dragger = Upload.Dragger;
  const DEFAULT_POLL_INTERVAL_SECONDS = 5;
  const TERMINAL_TASK_STATUSES = ["completed", "failed", "partial_failed", "aborted"];

  async function fetchJson(url, options) {
    const response = await fetch(url, options || {});
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || ("请求失败: " + response.status));
    }
    return payload;
  }

  function statusTag(status) {
    if (status === "completed") return html`<${Tag} color="success">已完成<//>`;
    if (status === "failed") return html`<${Tag} color="error">失败<//>`;
    if (status === "partial_failed") return html`<${Tag} color="warning">部分失败<//>`;
    if (status === "aborted") return html`<${Tag} color="default">已中止<//>`;
    if (status === "running") return html`<${Tag} color="processing">运行中<//>`;
    return html`<${Tag}>${status || "待处理"}<//>`;
  }

  function batchStatusTag(status) {
    if (status === "success") return html`<${Tag} color="success">已成功<//>`;
    if (status === "pending") return html`<${Tag}>等待中<//>`;
    if (status === "parse_error") return html`<${Tag} color="warning">解析失败<//>`;
    if (status === "postprocess_error") return html`<${Tag} color="warning">后处理失败<//>`;
    if (status === "error") return html`<${Tag} color="error">调用失败<//>`;
    if (status === "aborted") return html`<${Tag} color="default">已中止<//>`;
    return html`<${Tag}>${status || "未知"}<//>`;
  }

  function formatDuration(durationMs) {
    if (!durationMs) return "0s";
    if (durationMs < 1000) return durationMs + "ms";
    const seconds = Math.round(durationMs / 1000);
    if (seconds < 60) return seconds + "s";
    return Math.floor(seconds / 60) + "m " + (seconds % 60) + "s";
  }

  function fileLabelFromPath(path) {
    return String(path || "").split("/").pop() || "";
  }

  function pollIntervalMsFromPayload(payload) {
    const seconds = Number((payload && payload.poll_interval_seconds) || DEFAULT_POLL_INTERVAL_SECONDS);
    return Math.max(1000, seconds * 1000);
  }

  function providerLabelFromList(providers, fallbackProvider) {
    const values = (providers || []).filter(Boolean);
    if (!values.length && fallbackProvider) values.push(fallbackProvider);
    return values.length ? values.join(" / ") : "-";
  }

  function batchProgress(done, planned, batches) {
    const total = planned || ((batches && batches.length) || 0);
    return total ? (done || 0) + "/" + total : "-";
  }

  function readTaskIdFromLocation() {
    const params = new URLSearchParams(window.location.search);
    return params.get("task_id") || "";
  }

  function writeTaskIdToLocation(taskId) {
    const url = new URL(window.location.href);
    if (taskId) {
      url.searchParams.set("task_id", taskId);
    } else {
      url.searchParams.delete("task_id");
    }
    window.history.replaceState({}, "", url.toString());
  }

  function pairPayloadWithLabels(pair) {
    return {
      pair_id: pair.pair_id,
      key: pair.key || "",
      old_path: pair.old_path || "",
      new_path: pair.new_path || "",
      old_label: pair.old_label || fileLabelFromPath(pair.old_path),
      new_label: pair.new_label || fileLabelFromPath(pair.new_path),
    };
  }

  /** Ant Design Upload 在 beforeUpload 里拿到的项可能是 File 本身，也可能包在 originFileObj 里。 */
  function rawFileFromUploadEntry(fileWrapper) {
    if (!fileWrapper) return null;
    var candidate = fileWrapper.originFileObj != null ? fileWrapper.originFileObj : fileWrapper;
    if (typeof File !== "undefined" && candidate instanceof File) return candidate;
    if (typeof Blob !== "undefined" && candidate instanceof Blob) return candidate;
    return null;
  }

  function FileComparisonPage() {
    const app = App.useApp();
    const message = app.message;
    const [uploadedFiles, setUploadedFiles] = React.useState([]);
    const [scanPayload, setScanPayload] = React.useState(null);
    const [editablePairs, setEditablePairs] = React.useState([]);
    const [taskPayload, setTaskPayload] = React.useState(null);
    const [currentTaskId, setCurrentTaskId] = React.useState("");
    const [loadingTask, setLoadingTask] = React.useState(false);
    const [uploading, setUploading] = React.useState(false);
    const [rerunningBatchIds, setRerunningBatchIds] = React.useState({});
    const [rerenderingPairIds, setRerenderingPairIds] = React.useState({});
    const [pollVersion, setPollVersion] = React.useState(0);
    const [abortingTask, setAbortingTask] = React.useState(false);
    const initialUrlTaskIdRef = React.useRef(readTaskIdFromLocation());
    const workspaceHydratedRef = React.useRef("");

    React.useEffect(function () {
      window.sessionStorage.removeItem("fileComparison.currentTaskId");
      workspaceHydratedRef.current = "";
      const savedTaskId = readTaskIdFromLocation();
      if (savedTaskId) {
        setCurrentTaskId(savedTaskId);
      }
    }, []);

    React.useEffect(function () {
      if (!currentTaskId) return undefined;
      writeTaskIdToLocation(currentTaskId);
      const pollIntervalMs = pollIntervalMsFromPayload(taskPayload);
      const timer = window.setInterval(async function () {
        try {
          const payload = await fetchJson("/api/file-comparison/task/" + currentTaskId + "/status");
          setTaskPayload(payload);
          if (TERMINAL_TASK_STATUSES.indexOf(payload.status) >= 0) {
            window.clearInterval(timer);
          }
        } catch (error) {
          window.clearInterval(timer);
          message.error(error.message);
        }
      }, pollIntervalMs);
      fetchJson("/api/file-comparison/task/" + currentTaskId + "/status")
        .then(function (payload) {
          setTaskPayload(payload);
          tryHydrateWorkspaceFromStatus(payload, currentTaskId);
        })
        .catch(function () {
          window.sessionStorage.removeItem("fileComparison.currentTaskId");
          writeTaskIdToLocation("");
        });
      return function () {
        window.clearInterval(timer);
      };
    }, [currentTaskId, message, pollVersion, taskPayload && taskPayload.poll_interval_seconds]);

    const fileOptions = React.useMemo(function () {
      return ((scanPayload && scanPayload.files) || []).map(function (file) {
        return { label: file.label, value: file.path };
      });
    }, [scanPayload]);

    const governance = (taskPayload && taskPayload.governance_summary) || {};
    const plannedBatchCount = governance.planned_batch_count || governance.total_batch_count || 0;
    const activeProviderLabel = providerLabelFromList(governance.active_providers, governance.current_provider);
    const abortPending =
      !!(taskPayload && taskPayload.abort_requested && ["pending", "running"].indexOf(taskPayload.status) >= 0);
    const canAbortTask =
      !!currentTaskId &&
      taskPayload &&
      ["pending", "running"].indexOf(taskPayload.status) >= 0 &&
      !taskPayload.abort_requested;

    function clearCurrentTask() {
      workspaceHydratedRef.current = "";
      setCurrentTaskId("");
      setTaskPayload(null);
      window.sessionStorage.removeItem("fileComparison.currentTaskId");
      writeTaskIdToLocation("");
    }

    function updatePair(pairId, patch) {
      setEditablePairs(function (prev) {
        return prev.map(function (pair) {
          if (pair.pair_id !== pairId) return pair;
          const nextPair = Object.assign({}, pair, patch);
          if (patch.old_path) nextPair.old_label = fileLabelFromPath(patch.old_path);
          if (patch.new_path) nextPair.new_label = fileLabelFromPath(patch.new_path);
          return nextPair;
        });
      });
    }

    function swapPair(pairId) {
      setEditablePairs(function (prev) {
        return prev.map(function (pair) {
          if (pair.pair_id !== pairId) return pair;
          return Object.assign({}, pair, {
            old_path: pair.new_path,
            new_path: pair.old_path,
            old_label: pair.new_label,
            new_label: pair.old_label,
          });
        });
      });
    }

    function removePair(pairId) {
      setEditablePairs(function (prev) {
        return prev.filter(function (pair) {
          return pair.pair_id !== pairId;
        });
      });
    }

    function clearPairs() {
      setEditablePairs([]);
    }

    function tryHydrateWorkspaceFromStatus(payload, taskIdFromEffect) {
      var tid = String((payload && payload.task_id) || taskIdFromEffect || "").trim();
      if (!tid) return;
      if (workspaceHydratedRef.current === tid) return;
      var urlTask = String(initialUrlTaskIdRef.current || "").trim();
      if (!urlTask || urlTask !== tid) return;

      workspaceHydratedRef.current = tid;

      var folderPath = String((payload && payload.folder_path) || "").trim();
      var files = (payload && payload.files) && Array.isArray(payload.files) ? payload.files.slice() : [];
      var pairs = (payload && payload.pairs) || [];

      if (pairs.length) {
        setEditablePairs(pairs.map(pairPayloadWithLabels));
      }

      if (!folderPath && pairs.length) {
        var seenPaths = {};
        pairs.forEach(function (pair) {
          [pair.old_path, pair.new_path].forEach(function (pathValue) {
            var path = String(pathValue || "").trim();
            if (!path || seenPaths[path]) return;
            seenPaths[path] = true;
            files.push({ label: fileLabelFromPath(path), path: path });
          });
        });
        setScanPayload(null);
        setUploadedFiles(
          files.map(function (fileEntry, index) {
            return {
              uid: "restored-path-" + index + "-" + String(fileEntry.path),
              name: fileEntry.label,
              status: "done",
            };
          })
        );
        return;
      }

      if (folderPath && !files.length && pairs.length) {
        var seenFallback = {};
        pairs.forEach(function (pair) {
          [pair.old_path, pair.new_path].forEach(function (pathValue) {
            var path = String(pathValue || "").trim();
            if (!path || seenFallback[path]) return;
            seenFallback[path] = true;
            files.push({ label: fileLabelFromPath(path), path: path });
          });
        });
      }

      if (folderPath) {
        setScanPayload({
          folder_path: folderPath,
          files: files,
          uploaded_count: files.length,
        });
        setUploadedFiles(
          files.map(function (fileEntry, index) {
            return {
              uid: "restored-" + index + "-" + String(fileEntry.path),
              name: fileEntry.label,
              status: "done",
            };
          })
        );
      }
    }

    async function refreshTask(taskId) {
      const payload = await fetchJson("/api/file-comparison/task/" + taskId + "/status");
      setTaskPayload(payload);
      return payload;
    }

    async function requestAbortTask() {
      if (!currentTaskId) return;
      setAbortingTask(true);
      try {
        await fetchJson("/api/file-comparison/task/" + currentTaskId + "/abort", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        });
        message.success("已请求暂停，当前文件对处理结束后将停止后续对比。");
        setPollVersion(function (prev) {
          return prev + 1;
        });
        await refreshTask(currentTaskId);
      } catch (error) {
        message.error(error.message);
      } finally {
        setAbortingTask(false);
      }
    }

    async function copyText(text) {
      if (!text) {
        message.warning("没有可复制的内容。");
        return;
      }
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(text);
        } else {
          const textarea = document.createElement("textarea");
          textarea.value = text;
          textarea.setAttribute("readonly", "readonly");
          textarea.style.position = "fixed";
          textarea.style.left = "-9999px";
          document.body.appendChild(textarea);
          textarea.select();
          document.execCommand("copy");
          document.body.removeChild(textarea);
        }
        message.success("已复制。");
      } catch (error) {
        message.error("复制失败，请手动选择路径复制。");
      }
    }

    function addPair() {
      const first = fileOptions[0] && fileOptions[0].value;
      const second = fileOptions[1] && fileOptions[1].value;
      setEditablePairs(function (prev) {
        return prev.concat({
          pair_id: "manual-" + Date.now(),
          key: "手动配对",
          old_path: first || "",
          new_path: second || "",
          old_label: fileLabelFromPath(first),
          new_label: fileLabelFromPath(second),
        });
      });
    }

    async function rerunBatch(pairId, batchId) {
      if (!currentTaskId || !pairId || !batchId) {
        message.warning("缺少任务或批次信息。");
        return;
      }
      const rerunKey = pairId + ":" + batchId;
      setRerunningBatchIds(function (prev) {
        return Object.assign({}, prev, { [rerunKey]: true });
      });
      try {
        const payload = await fetchJson(
          "/api/file-comparison/task/" + currentTaskId + "/pair/" + pairId + "/batch/" + batchId + "/rerun",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: "{}",
          }
        );
        setCurrentTaskId(payload.task_id);
        setPollVersion(function (prev) { return prev + 1; });
        await refreshTask(payload.task_id);
        message.success("已重新发起 " + batchId + "。");
      } catch (error) {
        message.error(error.message);
      } finally {
        setRerunningBatchIds(function (prev) {
          const next = Object.assign({}, prev);
          delete next[rerunKey];
          return next;
        });
      }
    }

    function canRerenderDocx(record) {
      const completedBatchCount = Number((record && record.completed_batch_count) || 0);
      const plannedBatchCount = Number((record && record.planned_batch_count) || 0);
      const allPlannedBatchesSucceeded = plannedBatchCount > 0 && completedBatchCount >= plannedBatchCount;
      return !!(taskPayload && currentTaskId && record && !record.download_blocked && (record.status === "completed" || allPlannedBatchesSucceeded));
    }

    async function rerenderDocx(pairId) {
      if (!currentTaskId || !pairId) {
        message.warning("缺少任务或文件对信息。");
        return;
      }
      setRerenderingPairIds(function (prev) {
        return Object.assign({}, prev, { [pairId]: true });
      });
      try {
        await fetchJson(
          "/api/file-comparison/task/" + currentTaskId + "/pair/" + pairId + "/rerender-docx",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: "{}",
          }
        );
        await refreshTask(currentTaskId);
        message.success("已基于完成批次重新生成 DOCX。");
      } catch (error) {
        message.error(error.message);
      } finally {
        setRerenderingPairIds(function (prev) {
          const next = Object.assign({}, prev);
          delete next[pairId];
          return next;
        });
      }
    }

    const pairColumns = React.useMemo(function () {
      return [
        {
          title: "主键",
          dataIndex: "key",
          key: "key",
          width: 120,
          render: function (value, record) {
            return html`<${Input}
              size="small"
              value=${value}
              placeholder="主键"
              onChange=${function (event) { updatePair(record.pair_id, { key: event.target.value }); }}
            />`;
          },
        },
        {
          title: "修改前文件",
          dataIndex: "old_path",
          key: "old_path",
          width: 250,
          render: function (value, record) {
            return html`<${Select}
              size="small"
              value=${value || undefined}
              options=${fileOptions}
              placeholder="选择修改前文件"
              className="w-full"
              onChange=${function (nextValue) { updatePair(record.pair_id, { old_path: nextValue }); }}
            />`;
          },
        },
        {
          title: "修改后文件",
          dataIndex: "new_path",
          key: "new_path",
          width: 250,
          render: function (value, record) {
            return html`<${Select}
              size="small"
              value=${value || undefined}
              options=${fileOptions}
              placeholder="选择修改后文件"
              className="w-full"
              onChange=${function (nextValue) { updatePair(record.pair_id, { new_path: nextValue }); }}
            />`;
          },
        },
        {
          title: "操作",
          key: "actions",
          width: 150,
          render: function (_, record) {
            return html`<${Space} size=${4} wrap=${false}>
              <${Button} size="small" onClick=${function () { swapPair(record.pair_id); }}>交换<//>
              <${Button} size="small" danger=${true} onClick=${function () { removePair(record.pair_id); }}>删除匹配<//>
            <//>`;
          },
        },
      ];
    }, [fileOptions]);

    const taskColumns = React.useMemo(
      function () {
        return [
          {
            title: "文件",
            key: "file_names",
            width: 320,
            render: function (_, record) {
              return html`
                <div className="flex flex-col gap-1">
                  <div className="text-xs text-slate-500 break-all">
                    修改前文件：${record.old_label || fileLabelFromPath(record.old_path) || "-"}
                  </div>
                  <div className="text-xs text-slate-500 break-all">
                    修改后文件：${record.new_label || fileLabelFromPath(record.new_path) || "-"}
                  </div>
                </div>
              `;
            },
          },
          {
            title: "任务状态",
            dataIndex: "status",
            key: "status",
            width: 92,
            render: function (value) {
              return statusTag(value);
            },
          },
          {
            title: "批次",
            key: "batches",
            width: 78,
            render: function (_, record) {
              return batchProgress(record.completed_batch_count, record.planned_batch_count, record.batches);
            },
          },
          {
            title: "耗时",
            dataIndex: "duration_ms",
            key: "duration_ms",
            width: 86,
            render: function (value) {
              return formatDuration(value || 0);
            },
          },
          {
            title: "结果",
            key: "result",
            width: 160,
            render: function (_, record) {
              if (!taskPayload) {
                return html`<${Text} type="secondary">等待生成<//>`;
              }
              if (record.download_blocked) {
                return html`<${Text} type="danger">包含规则回退诊断，禁止下载正式文档<//>`;
              }
              const rerenderAvailable = canRerenderDocx(record);
              const docxAvailable = record.docx_available || record.docx_path;
              if (!docxAvailable && !rerenderAvailable) {
                return html`<${Text} type="secondary">等待生成<//>`;
              }
              return html`
                <${Space} size=${6} wrap=${true}>
                  ${docxAvailable
                    ? html`<${Button}
                        size="small"
                        type="primary"
                        href=${"/api/file-comparison/task/" + taskPayload.task_id + "/artifact/" + record.pair_id + "/docx"}
                      >
                        下载 DOCX
                      <//>`
                    : null}
                  <${Space} size=${6}>
                    ${rerenderAvailable
                      ? html`<${Button}
                          size="small"
                          loading=${!!rerenderingPairIds[record.pair_id]}
                          onClick=${function () { rerenderDocx(record.pair_id); }}
                        >
                          重新生成 DOCX
                        <//>`
                      : null}
                  <//>
                <//>
              `;
            },
          },
          {
            title: "错误",
            dataIndex: "error",
            key: "error",
            render: function (value) {
              return value
                ? html`<${Text} type="danger" className="break-all">${value}<//>`
                : html`<${Text} type="secondary">-<//>`;
            },
          },
        ];
      },
      [currentTaskId, rerenderingPairIds, taskPayload]
    );

    function renderBatchTable(record) {
      const batches = record.batches || [];
      if (!batches.length) {
        return html`<${Text} type="secondary">还没有批次明细，任务开始后会自动出现。<//>`;
      }
      const columns = [
        {
          title: "批次",
          dataIndex: "batch_id",
          key: "batch_id",
          width: 110,
        },
        {
          title: "章节",
          dataIndex: "chapter_range",
          key: "chapter_range",
          width: 180,
          render: function (value) {
            return (value || []).join("、") || "-";
          },
        },
        {
          title: "状态",
          dataIndex: "status",
          key: "status",
          width: 110,
          render: batchStatusTag,
        },
        {
          title: "尝试",
          dataIndex: "attempt_count",
          key: "attempt_count",
          width: 80,
          render: function (value) {
            return value || 0;
          },
        },
        {
          title: "耗时",
          dataIndex: "total_duration_ms",
          key: "total_duration_ms",
          width: 100,
          render: function (value, batch) {
            return formatDuration(value || batch.duration_ms || 0);
          },
        },
        {
          title: "错误",
          dataIndex: "error",
          key: "error",
          ellipsis: true,
          render: function (value) {
            return value ? html`<${Text} type="danger">${value}<//>` : html`<${Text} type="secondary">-<//>`;
          },
        },
        {
          title: "操作",
          key: "action",
          width: 110,
          fixed: "right",
          render: function (_, batch) {
            const rerunKey = record.pair_id + ":" + batch.batch_id;
            return html`<${Button}
              size="small"
              loading=${!!rerunningBatchIds[rerunKey]}
              onClick=${function () { rerunBatch(record.pair_id, batch.batch_id); }}
            >
              重跑批次
            <//>`;
          },
        },
      ];
      return html`
        <${Table}
          rowKey=${function (batch) { return batch.batch_id; }}
          columns=${columns}
          dataSource=${batches}
          size="small"
          pagination=${false}
          scroll=${{ x: 980 }}
        />
      `;
    }


    async function uploadAndScan() {
      if (!uploadedFiles.length) {
        message.warning("先拖入文档再上传。");
        return;
      }
      var hasLocalBlob = uploadedFiles.some(function (fileWrapper) {
        return !!rawFileFromUploadEntry(fileWrapper);
      });
      if (!hasLocalBlob) {
        if (!scanPayload || !scanPayload.folder_path) {
          message.warning("本地文件句柄已丢失，请重新选择文件后再匹配。");
          return;
        }
        setUploading(true);
        try {
          const payload = await fetchJson("/api/file-comparison/scan-folder", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ folder_path: scanPayload.folder_path }),
          });
          setScanPayload(payload);
          setEditablePairs((payload.pairs || []).map(pairPayloadWithLabels));
          message.success("已根据目录重新扫描并生成配对建议。");
        } catch (error) {
          message.error(error.message);
        } finally {
          setUploading(false);
        }
        return;
      }
      clearCurrentTask();
      const formData = new FormData();
      uploadedFiles.forEach(function (fileWrapper) {
        const fileObject = rawFileFromUploadEntry(fileWrapper);
        if (fileObject) {
          formData.append(
            "files",
            fileObject,
            (fileObject && fileObject.name) || fileWrapper.name || "upload.docx"
          );
        }
      });
      setUploading(true);
      try {
        const response = await fetch("/api/file-comparison/upload-files", {
          method: "POST",
          body: formData,
        });
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.error || ("上传失败: " + response.status));
        }
        setScanPayload(payload);
        setEditablePairs((payload.pairs || []).map(pairPayloadWithLabels));
        message.success("上传完成，已保存 " + payload.uploaded_count + " 个文件。");
      } catch (error) {
        message.error(error.message);
      } finally {
        setUploading(false);
      }
    }

    async function createTask() {
      if (!scanPayload || !scanPayload.folder_path) {
        message.warning("请先上传并匹配一批文件。");
        return;
      }
      if (!editablePairs.length) {
        message.warning("请先确认至少一组配对。");
        return;
      }
      const invalidPair = editablePairs.find(function (pair) {
        return !pair.old_path || !pair.new_path || pair.old_path === pair.new_path;
      });
      if (invalidPair) {
        message.warning("请检查配对：修改前后文件不能为空，也不能相同。");
        return;
      }
      setLoadingTask(true);
      try {
        const payload = await fetchJson("/api/file-comparison/task", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            folder_path: scanPayload.folder_path,
            pairs: editablePairs.map(function (pair) {
              return { key: pair.key, old_path: pair.old_path, new_path: pair.new_path };
            }),
          }),
        });
        setCurrentTaskId(payload.task_id);
        setTaskPayload(payload);
        message.success("任务已发起。");
      } catch (error) {
        message.error(error.message);
      } finally {
        setLoadingTask(false);
      }
    }

    return html`
      <main className="page-shell">
        <section className="mb-3 flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <${Title} level=${4} className="!mb-0">文件对照批处理<//>
            <${Text} type="secondary">上传文件、确认配对、生成结果、下载文档<//>
          </div>
          ${currentTaskId
            ? html`<${Space} size=${6} wrap=${true}>
                <${Tag} color="processing">当前任务：${currentTaskId}<//>
                ${abortPending
                  ? html`<${Tag} color="warning">暂停待生效<//>`
                  : null}
                ${canAbortTask
                  ? html`<${Popconfirm}
                      title="确定暂停当次任务？"
                      description="正在处理的文件对仍会先跑完，后续文件对将不再执行。"
                      okText="暂停"
                      cancelText="取消"
                      onConfirm=${requestAbortTask}
                    >
                      <${Button} size="small" loading=${abortingTask}>暂停当次任务<//>
                    <//>`
                  : null}
                <${Button} size="small" onClick=${clearCurrentTask}>清空当前任务<//>
              <//>`
            : null}
        </section>

        <div className="workflow-grid">
          <${Card} size="small" className="dense-card upload-card" bordered=${false}>
            <div className="upload-panel">
                  <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                    <div>
                      <${Text} strong>上传文件<//>
                      <${Paragraph} type="secondary" className="!mb-0 !text-xs">
                        支持 .doc / .docx，上传区已压缩，配对可在下方调整。
                      <//>
                    </div>
                  </div>

                    <div className="upload-body-grid">
                      <${Dragger}
                        className="compact-upload"
                        multiple=${true}
                        accept=".doc,.docx"
                        showUploadList=${false}
                        beforeUpload=${function (file) {
                          clearCurrentTask();
                          setUploadedFiles(function (prev) {
                            const exists = prev.some(function (item) {
                              return item.uid === file.uid;
                            });
                            return exists ? prev : prev.concat(file);
                          });
                          return false;
                        }}
                        onRemove=${function (file) {
                          setUploadedFiles(function (prev) {
                            return prev.filter(function (item) {
                              return item.uid !== file.uid;
                            });
                          });
                        }}
                        fileList=${uploadedFiles}
                      >
                        <p className="ant-upload-drag-icon">
                          <span className="text-xl text-blue-600">+</span>
                        </p>
                        <p className="ant-upload-text">拖拽或点击选择 Word 文档</p>
                        <p className="ant-upload-hint">右侧会同步显示已选择文件。</p>
                      <//>

                      <div className="selected-file-list">
                        <div className="selected-file-list-title">
                          <span>上传列表</span>
                          <span>已选择 ${uploadedFiles.length} 个</span>
                        </div>
                        ${uploadedFiles.length
                          ? uploadedFiles.map(function (file) {
                              return html`
                                <div className="selected-file-row" key=${file.uid || file.name}>
                                  <span className="selected-file-name" title=${file.name}>${file.name}<//>
                                  <button
                                    type="button"
                                    className="selected-file-remove"
                                    title="移除文件"
                                    onClick=${function () {
                                      setUploadedFiles(function (prev) {
                                        return prev.filter(function (item) {
                                          return item.uid !== file.uid;
                                        });
                                      });
                                    }}
                                  >
                                    ×
                                  </button>
                                </div>
                              `;
                            })
                          : html`<div className="flex h-[104px] items-center justify-center text-xs text-slate-400">已选择文件会显示在这里</div>`}
                      </div>
                    </div>

                    <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                      <${Text} type="secondary" className="!text-xs">
                        已选择 ${uploadedFiles.length} 个文件，点击匹配后生成下方配对预览。
                      <//>
                      <${Button}
                        size="small"
                        type="primary"
                        loading=${uploading}
                        disabled=${!uploadedFiles.length}
                        onClick=${uploadAndScan}
                      >
                        文件匹配
                      <//>
                    </div>

                    ${scanPayload && scanPayload.folder_path
                      ? html`
                        <div className="flex items-center gap-2 rounded-lg bg-slate-50 px-2 py-1">
                          <${Text} type="secondary" className="min-w-0 flex-1 truncate !text-xs">
                            已上传目录：${scanPayload.folder_path}
                          <//>
                          <${Button}
                            size="small"
                            onClick=${function () { copyText(scanPayload.folder_path); }}
                          >
                            复制
                          <//>
                        </div>
                      `
                      : null}
            </div>
          <//>

          <${Card}
            size="small"
            title="配对预览"
            className="dense-card pair-preview-card"
            bordered=${false}
            extra=${html`<${Space} size=${6}>
              <${Button} size="small" disabled=${!fileOptions.length} onClick=${addPair}>新增配对<//>
              <${Button} size="small" danger=${true} disabled=${!editablePairs.length} onClick=${clearPairs}>清空配对<//>
              <${Button}
                size="small"
                type="primary"
                loading=${loadingTask}
                disabled=${!scanPayload || !editablePairs.length}
                onClick=${createTask}
              >
                发起生成
              <//>
            <//>`}
          >
            <${Table}
                rowKey=${function (record) { return record.pair_id; }}
                columns=${pairColumns}
                dataSource=${editablePairs}
                size="small"
                pagination=${false}
                tableLayout="fixed"
                locale=${{ emptyText: "尚未匹配或未找到可配对文件" }}
              />
          <//>

          <${Card} size="small" title="任务状态" className="dense-card task-status-card" bordered=${false}>
                <div className="task-progress-summary">
                  <div className="task-progress-item">
                    <span className="task-progress-label">任务状态</span>
                    <span className="task-progress-value">
                      <${Space} size=${4} wrap=${true}>
                        ${statusTag((taskPayload && taskPayload.status) || "未开始")}
                        ${abortPending
                          ? html`<${Tag} color="warning">暂停待生效（当前文件对结束后停止）<//>`
                          : null}
                      <//>
                    </span>
                  </div>
                  <div className="task-progress-item">
                    <span className="task-progress-label">文件对进度</span>
                    <span className="task-progress-value">${((taskPayload && taskPayload.completed_pair_count) || 0) + "/" + ((taskPayload && taskPayload.pair_count) || 0)}</span>
                  </div>
                  <div className="task-progress-item">
                    <span className="task-progress-label">批次进度</span>
                    <span className="task-progress-value">${(governance.completed_batch_count || 0) + "/" + plannedBatchCount}</span>
                  </div>
                  <div className="task-progress-item">
                    <span className="task-progress-label">活跃模型</span>
                    <span className="task-progress-value">${activeProviderLabel}</span>
                  </div>
                  <div className="task-progress-item">
                    <span className="task-progress-label">总耗时</span>
                    <span className="task-progress-value">${formatDuration((taskPayload && taskPayload.duration_ms) || 0)}</span>
                  </div>
                </div>
                <div className="mb-2 flex flex-wrap gap-1">
                  <${Tag} color=${governance.provider_failure_count ? "error" : "default"}>模型失败 ${(governance.provider_failure_count || 0)}<//>
                  <${Tag} color=${governance.repair_count ? "warning" : "gold"}>结果修复 ${(governance.repair_count || 0)}<//>
                  <${Tag} color=${governance.fallback_count ? "purple" : "geekblue"}>规则回退 ${(governance.fallback_count || 0)}<//>
                  <${Tag} color=${taskPayload && taskPayload.failed_pair_count ? "error" : "success"}>失败文件对 ${(taskPayload && taskPayload.failed_pair_count) || 0}<//>
                </div>
                <${Table}
                    rowKey=${function (record) { return record.pair_id; }}
                    columns=${taskColumns}
                    dataSource=${(taskPayload && taskPayload.pairs) || []}
                    size="small"
                    pagination=${false}
                    tableLayout="fixed"
                    expandable=${{ expandedRowRender: renderBatchTable, rowExpandable: function (record) { return !!(record.batches && record.batches.length); } }}
                    locale=${{ emptyText: "尚未发起任务" }}
                  />
          <//>
        </div>
      </main>
    `;
  }

  function RootApp() {
    return html`
      <${ConfigProvider}
        theme=${{
          algorithm: theme && theme.compactAlgorithm ? theme.compactAlgorithm : undefined,
          token: {
            colorPrimary: "#1677ff",
            borderRadius: 10,
            fontFamily: '"PingFang SC", "Hiragino Sans GB", sans-serif',
          },
          components: {
            Card: { headerFontSize: 14 },
            Table: { cellFontSizeSM: 12 },
          },
        }}
      >
        <${App}><${FileComparisonPage} /><//>
      <//>
    `;
  }

  ReactDOM.createRoot(document.getElementById("root")).render(html`<${RootApp} />`);
})();
