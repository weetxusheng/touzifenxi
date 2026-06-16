"""Small local HTML UI for the Python SQLite scheduler."""

from __future__ import annotations

import html
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from .runner import TaskRunner
from .scheduler import PythonScheduler
from .store import ScheduleStore, StoredRun, StoredTask
from .tasks import DEFAULT_GROUPS, group_map, load_tasks


def _h(value: object) -> str:
    return html.escape(str(value), quote=True)


def _tail_text(path: Path, *, max_lines: int = 80) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"读取日志失败: {exc}"
    return "\n".join(lines[-max_lines:])


def _latest_runs_by_task(runs: list[StoredRun]) -> dict[str, StoredRun]:
    latest: dict[str, StoredRun] = {}
    for run in runs:
        if run.task_id not in latest:
            latest[run.task_id] = run
    return latest


class ScheduleAdminServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        project_root: Path,
        start_scheduler: bool = True,
        poll_seconds: int = 30,
    ):
        self.project_root = project_root.resolve()
        self.store = ScheduleStore(self.project_root / "state" / "schedule_admin.db")
        self.store.init_db()
        self.store.sync_defaults(load_tasks(self.project_root), project_root=self.project_root)
        self.runner = TaskRunner(store=self.store, project_root=self.project_root)
        self.scheduler = PythonScheduler(store=self.store, runner=self.runner, poll_seconds=poll_seconds)
        if start_scheduler:
            self.scheduler.start()
        super().__init__(server_address, ScheduleAdminHandler)

    def server_close(self) -> None:
        self.scheduler.stop()
        super().server_close()


class ScheduleAdminHandler(BaseHTTPRequestHandler):
    server: ScheduleAdminServer

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        print(f"[schedule-admin] {self.address_string()} - {format % args}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/":
            self.send_error(404)
            return
        query = parse_qs(parsed.query)
        self._send_html(self._render_page(message=query.get("message", [""])[0], error=query.get("error", [""])[0]))

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        form = {key: values[-1] if values else "" for key, values in parse_qs(raw).items()}
        action = form.get("action", "")
        try:
            if action == "save_task":
                task_id = form["task_id"]
                enabled = form.get("enabled", "") == "1"
                self.server.store.update_task(task_id, time_of_day=form["time_of_day"], enabled=enabled)
                self._redirect(message=f"已保存任务: {task_id}")
            elif action == "toggle":
                task_id = form["task_id"]
                enabled = form.get("enabled", "") == "1"
                self.server.store.set_enabled(task_id, enabled)
                self._redirect(message=f"已{'启用' if enabled else '停用'}任务: {task_id}")
            elif action == "run":
                task_id = form["task_id"]
                task = self.server.store.get_task(task_id)
                if not task:
                    raise KeyError(f"未知任务: {task_id}")
                started = self.server.runner.start_task(task)
                self._redirect(message=f"{'已触发' if started else '任务运行中，已跳过'}: {task_id}")
            elif action == "tick":
                count = self.server.scheduler.tick()
                self._redirect(message=f"调度检查完成，触发 {count} 个任务。")
            else:
                raise KeyError(f"未知操作: {action}")
        except Exception as exc:
            self._redirect(error=str(exc))

    def _send_html(self, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _redirect(self, *, message: str = "", error: str = "") -> None:
        params = {}
        if message:
            params["message"] = message
        if error:
            params["error"] = error
        target = "/" + (("?" + urlencode(params)) if params else "")
        self.send_response(303)
        self.send_header("Location", target)
        self.end_headers()

    def _render_page(self, *, message: str = "", error: str = "") -> str:
        tasks = self.server.store.list_tasks()
        runs = self.server.store.list_runs(limit=20)
        latest_runs = _latest_runs_by_task(runs)
        running_ids = self.server.runner.running_task_ids()
        message_html = f'<div class="ok">{_h(message)}</div>' if message else ""
        error_html = f'<div class="error">{_h(error)}</div>' if error else ""
        scheduler_state = "运行中" if self.server.scheduler.running else "仅页面模式"
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>touzifenxi Python 调度</title>
  <style>
    :root {{
      --bg: #f6f7fb;
      --panel: #ffffff;
      --text: #111827;
      --muted: #6b7280;
      --border: #e5e7eb;
      --soft: #f9fafb;
      --primary: #2563eb;
      --primary-dark: #1d4ed8;
      --ok-bg: #dcfce7;
      --ok-text: #166534;
      --warn-bg: #fef3c7;
      --warn-text: #92400e;
      --bad-bg: #fee2e2;
      --bad-text: #991b1b;
    }}
    * {{ box-sizing: border-box; }}
    body {{ background: var(--bg); font-family: Segoe UI, Microsoft YaHei, sans-serif; margin: 0; color: var(--text); font-size: 15px; line-height: 1.5; }}
    main {{ max-width: 1320px; margin: 0 auto; padding: 24px; }}
    h1, h2, h3 {{ margin: 0; }}
    .hero {{ display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 18px; }}
    .hero-title {{ font-size: 26px; font-weight: 750; }}
    .hero-subtitle {{ color: var(--muted); margin-top: 6px; }}
    section {{ background: var(--panel); border: 1px solid var(--border); border-radius: 16px; padding: 18px; margin: 16px 0; box-shadow: 0 10px 28px rgba(15, 23, 42, 0.05); }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid var(--border); padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: var(--soft); }}
    input {{ min-height: 36px; border: 1px solid var(--border); border-radius: 8px; padding: 6px 8px; }}
    input:focus, button:focus, summary:focus {{ outline: 3px solid rgba(37, 99, 235, 0.25); outline-offset: 2px; }}
    button {{ min-height: 36px; border: 1px solid var(--border); border-radius: 8px; padding: 6px 12px; background: #fff; cursor: pointer; }}
    button:hover {{ background: var(--soft); }}
    .primary-button {{ background: var(--primary); border-color: var(--primary); color: #fff; }}
    .primary-button:hover {{ background: var(--primary-dark); }}
    pre {{ background: #0f172a; color: #e5e7eb; border-radius: 12px; padding: 12px; overflow: auto; max-height: 320px; font-size: 12px; }}
    .ok {{ color: var(--ok-text); background: var(--ok-bg); padding: 10px 12px; border-radius: 10px; margin-bottom: 12px; }}
    .error {{ color: var(--bad-text); background: var(--bad-bg); padding: 10px 12px; border-radius: 10px; margin-bottom: 12px; }}
    .muted {{ color: var(--muted); }}
    .toolbar {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 14px; }}
    .group-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 16px; }}
    .group-card {{ border: 1px solid var(--border); border-radius: 14px; padding: 14px; background: linear-gradient(180deg, #fff, #fcfcfd); }}
    .group-title {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; font-size: 18px; font-weight: 750; margin-bottom: 10px; }}
    .task-card {{ border-top: 1px solid #eef2f7; padding: 14px 0; }}
    .task-card:first-of-type {{ border-top: 0; }}
    .task-head {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }}
    .task-name {{ font-weight: 650; }}
    .task-meta {{ color: #6b7280; font-size: 12px; }}
    .task-path {{ color: #6b7280; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-top: 3px; }}
    .task-form {{ display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin: 0; }}
    .task-actions {{ display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin-top: 10px; }}
    .inline-label {{ display: inline-flex; align-items: center; gap: 6px; min-height: 36px; }}
    .inline-label input[type="checkbox"] {{ min-height: auto; width: 16px; height: 16px; margin: 0; }}
    .time-input {{ width: 82px; }}
    .status-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 10px; }}
    .status-box {{ background: var(--soft); border: 1px solid var(--border); border-radius: 10px; padding: 8px 10px; }}
    .status-label {{ color: var(--muted); font-size: 12px; }}
    .status-value {{ font-size: 13px; margin-top: 2px; word-break: break-all; }}
    .status-line {{ margin-top: 6px; font-size: 13px; }}
    .badge {{ display: inline-block; padding: 2px 6px; border-radius: 999px; background: #e5e7eb; font-size: 12px; }}
    .badge-on {{ background: var(--ok-bg); color: var(--ok-text); }}
    .badge-off {{ background: var(--bad-bg); color: var(--bad-text); }}
    .badge-run {{ background: var(--warn-bg); color: var(--warn-text); }}
    details {{ margin-top: 10px; border-top: 1px dashed var(--border); padding-top: 8px; }}
    summary {{ cursor: pointer; color: var(--primary-dark); min-height: 36px; display: inline-flex; align-items: center; }}
    @media (max-width: 720px) {{
      main {{ padding: 16px; }}
      .hero {{ flex-direction: column; }}
      .group-grid {{ grid-template-columns: 1fr; }}
      .status-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main>
    <div class="hero">
      <div>
        <div class="hero-title">touzifenxi Python 调度</div>
        <div class="hero-subtitle">一个本机 HTML 页面管理 SQLite 中的任务，Python 进程负责按时执行。</div>
      </div>
      <div class="muted">Scheduler: {_h(scheduler_state)}<br>SQLite: {_h(self.server.store.db_path)}</div>
    </div>
    {message_html}
    {error_html}
    <section>
      <div class="toolbar">
        <h2>任务分组</h2>
        <form method="post">
          <input type="hidden" name="action" value="tick">
          <button class="primary-button" type="submit">立即调度检查</button>
        </form>
      </div>
      {self._render_tasks(tasks, running_ids, latest_runs)}
    </section>
  </main>
</body>
</html>"""

    def _render_tasks(self, tasks: list[StoredTask], running_ids: set[str], latest_runs: dict[str, StoredRun]) -> str:
        tasks_by_group: dict[str, list[StoredTask]] = {}
        for task in tasks:
            tasks_by_group.setdefault(task.group_id, []).append(task)

        group_cards: list[str] = []
        known_groups = group_map()
        ordered_group_ids = [group.id for group in DEFAULT_GROUPS]
        for group_id in ordered_group_ids:
            group_tasks = tasks_by_group.pop(group_id, [])
            if group_tasks:
                group_cards.append(self._render_group_card(known_groups[group_id].label, group_tasks, running_ids, latest_runs))
        for group_id, group_tasks in sorted(tasks_by_group.items()):
            group_cards.append(self._render_group_card(group_id, group_tasks, running_ids, latest_runs))
        return '<div class="group-grid">' + "\n".join(group_cards) + "</div>"

    def _render_group_card(
        self,
        label: str,
        tasks: list[StoredTask],
        running_ids: set[str],
        latest_runs: dict[str, StoredRun],
    ) -> str:
        cards = []
        for task in tasks:
            enabled_checked = "checked" if task.enabled else ""
            enabled_badge = (
                '<span class="badge badge-on">启用</span>' if task.enabled else '<span class="badge badge-off">停用</span>'
            )
            running_badge = ' <span class="badge badge-run">运行中</span>' if task.id in running_ids else ""
            schedule = f"{task.schedule_type} {task.days_of_week}".strip()
            last_exit = "" if task.last_exit_code is None else str(task.last_exit_code)
            latest_run = latest_runs.get(task.id)
            log_drawer = self._render_task_log_drawer(latest_run)
            cards.append(
                '<div class="task-card">'
                '<div class="task-head">'
                "<div>"
                f'<div><span class="task-name">{_h(task.label)}</span> {enabled_badge}{running_badge}</div>'
                f'<div class="task-meta">{_h(task.id)}</div>'
                "</div>"
                f'<div class="task-meta">{_h(schedule)}</div>'
                "</div>"
                f'<div class="task-path" title="{_h(task.command)}">{_h(task.command)}</div>'
                '<div class="task-actions">'
                '<form class="task-form" method="post">'
                '<input type="hidden" name="action" value="save_task">'
                f'<input type="hidden" name="task_id" value="{_h(task.id)}">'
                f'<label class="inline-label">时间 <input class="time-input" name="time_of_day" value="{_h(task.time_of_day)}" size="5"></label>'
                f'<label class="inline-label"><input type="checkbox" name="enabled" value="1" {enabled_checked}>启用</label>'
                '<button type="submit">保存</button>'
                "</form><form class=\"task-form\" method=\"post\">"
                '<input type="hidden" name="action" value="run">'
                f'<input type="hidden" name="task_id" value="{_h(task.id)}">'
                '<button type="submit">立即运行</button>'
                "</form>"
                "</div>"
                '<div class="status-grid">'
                '<div class="status-box"><div class="status-label">下次运行</div>'
                f'<div class="status-value">{_h(task.next_run_at or "-")}</div></div>'
                '<div class="status-box"><div class="status-label">上次结果</div>'
                f'<div class="status-value">{_h(task.last_run_at or "-")}<br>exit={_h(last_exit or "-")}</div></div>'
                "</div>"
                + (f'<div class="status-line error">{_h(task.last_error)}</div>' if task.last_error else "")
                + log_drawer
                + "</div>"
            )
        return (
            '<div class="group-card">'
            f'<div class="group-title"><span>{_h(label)}</span><span class="badge">{len(tasks)} 个任务</span></div>'
            f'{"".join(cards)}</div>'
        )

    def _render_task_log_drawer(self, run: StoredRun | None) -> str:
        if not run:
            return '<details><summary>查看日志</summary><p class="muted">暂无运行记录。</p></details>'
        log_path = Path(run.log_path)
        log_text = _tail_text(log_path, max_lines=60) if log_path.is_file() else "日志文件不存在。"
        return (
            "<details>"
            f"<summary>查看日志 #{run.id}</summary>"
            f"<p class=\"muted\">开始: {_h(run.started_at)} | 结束: {_h(run.finished_at)} | exit: {_h(run.exit_code)}</p>"
            f"<p class=\"muted\">{_h(run.log_path)}</p>"
            f"<pre>{_h(log_text)}</pre>"
            "</details>"
        )


def serve_schedule_admin(
    *,
    project_root: Path,
    host: str,
    port: int,
    open_browser: bool = False,
    start_scheduler: bool = True,
    poll_seconds: int = 30,
) -> None:
    server = ScheduleAdminServer(
        (host, port),
        project_root=project_root,
        start_scheduler=start_scheduler,
        poll_seconds=poll_seconds,
    )
    url = f"http://{host}:{port}/"
    print(f"Schedule admin: {url}")
    print(f"SQLite: {server.store.db_path}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nSchedule admin stopped.")
    finally:
        server.server_close()
