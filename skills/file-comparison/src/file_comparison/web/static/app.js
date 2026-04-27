(function () {
  const React = window.React;
  const ReactDOM = window.ReactDOM;
  const antd = window.antd;
  const html = window.htm.bind(React.createElement);

  const {
    Alert,
    App,
    Button,
    Card,
    Col,
    ConfigProvider,
    Divider,
    Input,
    Layout,
    Row,
    Space,
    Statistic,
    Table,
    Tag,
    Typography,
    Upload,
  } = antd;
  const Header = Layout.Header;
  const Content = Layout.Content;
  const Title = Typography.Title;
  const Paragraph = Typography.Paragraph;
  const Text = Typography.Text;
  const Dragger = Upload.Dragger;

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
    if (status === "running") return html`<${Tag} color="processing">运行中<//>`;
    return html`<${Tag}>${status || "待处理"}<//>`;
  }

  function FileComparisonPage() {
    const app = App.useApp();
    const message = app.message;
    const [folderPath, setFolderPath] = React.useState("");
    const [uploadedFiles, setUploadedFiles] = React.useState([]);
    const [scanPayload, setScanPayload] = React.useState(null);
    const [taskPayload, setTaskPayload] = React.useState(null);
    const [currentTaskId, setCurrentTaskId] = React.useState("");
    const [loadingScan, setLoadingScan] = React.useState(false);
    const [loadingTask, setLoadingTask] = React.useState(false);
    const [uploading, setUploading] = React.useState(false);

    React.useEffect(function () {
      if (!currentTaskId) return undefined;
      const timer = window.setInterval(async function () {
        try {
          const payload = await fetchJson("/api/file-comparison/task/" + currentTaskId + "/status");
          setTaskPayload(payload);
          if (["completed", "failed", "partial_failed", "aborted"].indexOf(payload.status) >= 0) {
            window.clearInterval(timer);
          }
        } catch (error) {
          window.clearInterval(timer);
          message.error(error.message);
        }
      }, 2000);
      return function () {
        window.clearInterval(timer);
      };
    }, [currentTaskId, message]);

    const pairColumns = React.useMemo(function () {
      return [
        {
          title: "主键",
          dataIndex: "key",
          key: "key",
          render: function (value) {
            return value || "未命名分组";
          },
        },
        { title: "前文件", dataIndex: "old_label", key: "old_label" },
        { title: "后文件", dataIndex: "new_label", key: "new_label" },
      ];
    }, []);

    const taskColumns = React.useMemo(
      function () {
        return [
          {
            title: "文件对",
            dataIndex: "key",
            key: "key",
            render: function (value) {
              return value || "未命名分组";
            },
          },
          {
            title: "状态",
            dataIndex: "status",
            key: "status",
            render: function (value) {
              return statusTag(value);
            },
          },
          {
            title: "结果",
            key: "result",
            render: function (_, record) {
              if (!record.docx_path || !taskPayload) {
                return html`<${Text} type="secondary">等待生成<//>`;
              }
              return html`<a href=${"/api/file-comparison/task/" + taskPayload.task_id + "/artifact/" + record.pair_id + "/docx"}>docx</a>`;
            },
          },
          {
            title: "错误",
            dataIndex: "error",
            key: "error",
            render: function (value) {
              return value ? html`<${Text} type="danger">${value}<//>` : html`<${Text} type="secondary">-<//>`;
            },
          },
        ];
      },
      [taskPayload]
    );

    async function scanFolder(path) {
      const targetPath = (path || folderPath).trim();
      if (!targetPath) {
        message.warning("请先输入或上传文件。");
        return;
      }
      setLoadingScan(true);
      try {
        const payload = await fetchJson("/api/file-comparison/scan-folder", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ folder_path: targetPath }),
        });
        setFolderPath(payload.folder_path || targetPath);
        setScanPayload(payload);
        message.success("扫描完成，共识别 " + payload.pairs.length + " 组候选文件对。");
      } catch (error) {
        message.error(error.message);
      } finally {
        setLoadingScan(false);
      }
    }

    async function uploadAndScan() {
      if (!uploadedFiles.length) {
        message.warning("先拖入文档再上传。");
        return;
      }
      const formData = new FormData();
      uploadedFiles.forEach(function (fileWrapper) {
        const fileObject = fileWrapper.originFileObj || fileWrapper;
        if (fileObject) {
          formData.append("files", fileObject, fileObject.name || fileWrapper.name || "upload.docx");
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
        setFolderPath(payload.folder_path);
        setScanPayload(payload);
        message.success("上传完成，已保存 " + payload.uploaded_count + " 个文件。");
      } catch (error) {
        message.error(error.message);
      } finally {
        setUploading(false);
      }
    }

    async function createTask() {
      if (!folderPath.trim()) {
        message.warning("请先扫描或上传一批文件。");
        return;
      }
      setLoadingTask(true);
      try {
        const payload = await fetchJson("/api/file-comparison/task", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ folder_path: folderPath.trim() }),
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
      <${Layout} style=${{ minHeight: "100vh", background: "transparent" }}>
        <${Header} style=${{ background: "transparent", padding: "20px 28px 0", height: "auto" }}>
          <${Space} direction="vertical" size=${4}>
            <${Title} level=${2} style=${{ margin: 0 }}>文件对照批处理<//>
            <${Paragraph} type="secondary" style=${{ margin: 0 }}>
              React + Ant Design 版批处理页面，支持拖拽上传、自动配对、发起生成与结果轮询。
            <//>
          <//>
        <//>
        <${Content} className="page-shell">
          <${Row} gutter=${[18, 18]}>
            <${Col} xs=${24} xl=${16}>
              <${Card} className="hero-card" bordered=${false}>
                <${Space} direction="vertical" size=${20} style=${{ width: "100%" }}>
                  <div>
                    <${Text} strong>上传文件或直接填写本地目录<//>
                    <${Paragraph} type="secondary" style=${{ marginBottom: 0, marginTop: 6 }}>
                      推荐把前后版本的 .doc / .docx 直接拖进来，系统会在服务端创建临时目录并自动扫描配对。
                    <//>
                  </div>
                  <${Dragger}
                    className="drop-zone"
                    multiple=${true}
                    accept=".doc,.docx"
                    beforeUpload=${function (file) {
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
                      <span style=${{ fontSize: "36px", color: "#1677ff" }}>+</span>
                    </p>
                    <p className="ant-upload-text">拖拽 Word 文档到这里，或点击选择文件</p>
                    <p className="ant-upload-hint">支持一次拖入多份文档，上传后会自动按去掉月份后的主键配对。</p>
                  <//>
                  <${Space} wrap=${true}>
                    <${Button} type="primary" loading=${uploading} onClick=${uploadAndScan}>上传并扫描<//>
                    <${Button} loading=${loadingScan} onClick=${function () { scanFolder(); }}>扫描当前目录<//>
                    <${Button} type="primary" ghost=${true} loading=${loadingTask} onClick=${createTask}>一键发起生成<//>
                  <//>
                  <${Input}
                    value=${folderPath}
                    onChange=${function (event) { setFolderPath(event.target.value); }}
                    placeholder="输入服务端可访问的绝对路径，或先用拖拽上传"
                    size="large"
                  />
                  ${scanPayload && scanPayload.folder_path
                    ? html`<${Alert} type="info" showIcon=${true} message=${"当前扫描目录：" + scanPayload.folder_path} />`
                    : null}
                <//>
              <//>
            <//>
            <${Col} xs=${24} xl=${8}>
              <${Row} gutter=${[16, 16]}>
                <${Col} span=${12}>
                  <${Card}><${Statistic} title="候选文件对" value=${(scanPayload && scanPayload.pairs && scanPayload.pairs.length) || 0} /><//>
                <//>
                <${Col} span=${12}>
                  <${Card}><${Statistic} title="上传文件数" value=${uploadedFiles.length} /><//>
                <//>
                <${Col} span=${24}>
                  <${Card}>
                    <${Statistic} title="任务状态" value=${(taskPayload && taskPayload.status) || "未开始"} />
                    <${Divider} style=${{ margin: "16px 0" }} />
                    <${Space} size=${24}>
                      <${Statistic} title="成功" value=${(taskPayload && taskPayload.success_count) || 0} />
                      <${Statistic} title="失败" value=${(taskPayload && taskPayload.failed_count) || 0} />
                    <//>
                  <//>
                <//>
              <//>
            <//>
          <//>
          <${Row} gutter=${[18, 18]} style=${{ marginTop: 2 }}>
            <${Col} span=${24}>
              <${Card} title="配对预览" bordered=${false}>
                <${Table}
                  rowKey=${function (record) { return record.pair_id; }}
                  columns=${pairColumns}
                  dataSource=${(scanPayload && scanPayload.pairs) || []}
                  pagination=${false}
                  locale=${{ emptyText: "尚未扫描或未找到可配对文件" }}
                />
              <//>
            <//>
            <${Col} span=${24}>
              <${Card} title="任务状态" bordered=${false}>
                <${Table}
                  rowKey=${function (record) { return record.pair_id; }}
                  columns=${taskColumns}
                  dataSource=${(taskPayload && taskPayload.pairs) || []}
                  pagination=${false}
                  locale=${{ emptyText: "尚未发起任务" }}
                />
              <//>
            <//>
          <//>
        <//>
      <//>
    `;
  }

  function RootApp() {
    return html`
      <${ConfigProvider}
        theme=${{
          token: {
            colorPrimary: "#1677ff",
            borderRadius: 16,
            fontFamily: '"PingFang SC", "Hiragino Sans GB", sans-serif',
          },
        }}
      >
        <${App}><${FileComparisonPage} /><//>
      <//>
    `;
  }

  ReactDOM.createRoot(document.getElementById("root")).render(html`<${RootApp} />`);
})();
