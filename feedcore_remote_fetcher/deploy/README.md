# systemd 部署

## 安装

```bash
sudo mkdir -p /etc/feedcore
sudo cp remote-fetcher.env.example /etc/feedcore/remote-fetcher.env
# 编辑 token、public URL、Worker 等
sudo nano /etc/feedcore/remote-fetcher.env

# 修改 service 里 WorkingDirectory / ExecStart 路径后：
sudo cp feedcore-remote-fetcher.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now feedcore-remote-fetcher
```

## 验证

```bash
sudo systemctl status feedcore-remote-fetcher
curl -s http://127.0.0.1:3000/health
```

## `Deactivated successfully`（启动后立刻退出）

表示 **主进程已正常结束（退出码 0）**，systemd 认为任务完成，不是常驻服务。常见原因：

| 原因 | 典型错误写法 |
|------|----------------|
| `Type=oneshot` 或未设 `Type` 且脚本很快结束 | `Type=oneshot` + 仅执行安装脚本 |
| `ExecStart` 只执行了 `activate` | `ExecStart=/path/.venv/bin/activate` |
| 用了 `run_fetcher.sh` 且 shell 未真正拉起 uvicorn | 工作目录错误、`python` 不在 PATH |
| `ExecStart` 里后台运行 `&` | `python run_fetcher.py &` → 父 shell 立刻退出 |
| `Type=forking` 但 uvicorn 不 fork | 应使用 `Type=simple` |

**正确做法**：`Type=simple`，`ExecStart` 指向 **venv 内 python 的绝对路径** + `run_fetcher.py` 绝对路径，并设置 `WorkingDirectory`。

## 排查命令

```bash
# 最近日志（ImportError、端口占用等都在这里）
sudo journalctl -u feedcore-remote-fetcher -n 80 --no-pager

# 前台手动跑（与 systemd 相同环境）
cd /root/remote_fetcher
source .venv/bin/activate
set -a && source /etc/feedcore/remote-fetcher.env && set +a
python run_fetcher.py --host 0.0.0.0 --port 3000 --output-dir remote_output

# 端口是否已被占用
ss -tlnp | grep 3000
```

若 `status` 为 **failed** 而非 deactivated，看 journal 里的 traceback（多为缺依赖：`pip install -r requirements.txt`）。
