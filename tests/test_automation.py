from __future__ import annotations

from pathlib import Path

from touzifenxi.automation import (
    DEFAULT_AUTOMATION_HOURS,
    AutomationPaths,
    build_calendar_intervals,
    parse_launchctl_print,
    render_launchd_plist,
)


def test_build_calendar_intervals_covers_weekday_evening_slots() -> None:
    intervals = build_calendar_intervals()

    assert len(intervals) == 30
    assert intervals[0] == {"Weekday": 1, "Hour": 18, "Minute": 30}
    assert intervals[-1] == {"Weekday": 5, "Hour": 23, "Minute": 30}
    assert sorted({item["Hour"] for item in intervals}) == DEFAULT_AUTOMATION_HOURS
    assert sorted({item["Weekday"] for item in intervals}) == [1, 2, 3, 4, 5]


def test_render_launchd_plist_uses_runtime_runner_and_logs() -> None:
    paths = AutomationPaths(
        source_root=Path("/Users/xusheng/Documents/project/touzifenxi"),
        runtime_root=Path("/Users/xusheng/Projects/touzifenxi-auto"),
        runner_path=Path("/Users/xusheng/Library/Scripts/touzifenxi/run_daily_cycle.sh"),
        plist_path=Path("/Users/xusheng/Library/LaunchAgents/com.touzifenxi.daily-cycle.plist"),
    )

    plist = render_launchd_plist(paths)

    assert "com.touzifenxi.daily-cycle" in plist
    assert "/Users/xusheng/Projects/touzifenxi-auto" in plist
    assert "/Users/xusheng/Library/Scripts/touzifenxi/run_daily_cycle.sh" in plist
    assert "daily-cycle-launchd.log" in plist
    assert "daily-cycle-launchd.err.log" in plist
    assert plist.count("<key>Weekday</key>") == 30


def test_parse_launchctl_print_extracts_operational_fields() -> None:
    output = """
gui/501/com.touzifenxi.daily-cycle = {
    active count = 0
    path = /Users/xusheng/Library/LaunchAgents/com.touzifenxi.daily-cycle.plist
    state = not running
    program = /bin/zsh
    working directory = /Users/xusheng/Projects/touzifenxi-auto
    stdout path = /Users/xusheng/Projects/touzifenxi-auto/runtime/daily-cycle-launchd.log
    stderr path = /Users/xusheng/Projects/touzifenxi-auto/runtime/daily-cycle-launchd.err.log
    runs = 3
    last exit code = 0
}
"""

    status = parse_launchctl_print(output)

    assert status["state"] == "not running"
    assert status["runs"] == "3"
    assert status["last_exit_code"] == "0"
    assert status["program"] == "/bin/zsh"
    assert status["working_directory"] == "/Users/xusheng/Projects/touzifenxi-auto"


def test_parse_launchctl_print_keeps_top_level_state_when_timers_have_state() -> None:
    output = """
gui/501/com.touzifenxi.daily-cycle = {
    state = not running
    runs = 4
    last exit code = 0
    event triggers = {
        com.apple.xpc.activity = {
            state = active
        }
    }
}
"""

    status = parse_launchctl_print(output)

    assert status["state"] == "not running"
