"""Poll latest parse trace JSON and print step progress + large gaps."""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def find_trace(audit_root: Path, biz_date: str, account: str) -> Path | None:
    pattern = f"{biz_date}_{account}.trace.json"
    candidates = sorted(audit_root.glob(f"*/{pattern}"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def report(path: Path) -> None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    steps = doc.get("steps") or []
    summary = doc.get("summary") or {}
    if not steps:
        return
    last = steps[-1]
    n_http = sum(1 for s in steps if s.get("kind") == "http_request")
    http_ms = sum(
        s["detail"]["response"]["elapsed_ms"]
        for s in steps
        if s.get("kind") == "http_request"
    )
    t0 = parse_ts(steps[0]["ts"])
    t1 = parse_ts(last["ts"])
    wall = (t1 - t0).total_seconds()
    max_gap = 0.0
    max_gap_desc = ""
    for i in range(1, len(steps)):
        g = (parse_ts(steps[i]["ts"]) - parse_ts(steps[i - 1]["ts"])).total_seconds()
        if g > max_gap:
            max_gap = g
            max_gap_desc = f"{steps[i-1].get('kind')}|{steps[i-1].get('label','')[:40]} -> {steps[i].get('kind')}|{steps[i].get('label','')[:40]}"
    status = summary.get("status", "running")
    print(
        f"[monitor] {path.parent.name} status={status} steps={len(steps)} http={n_http} "
        f"http_min={http_ms/60000:.1f} span_min={wall/60:.1f} max_gap={max_gap:.0f}s",
        flush=True,
    )
    if max_gap >= 60:
        print(f"  !! gap>=60s: {max_gap_desc}", flush=True)
    print(f"  last: {last.get('kind')} | {last.get('label','')[:60]}", flush=True)


def main() -> int:
    audit_root = Path(sys.argv[1] if len(sys.argv) > 1 else "audit_traces")
    biz_date = sys.argv[2] if len(sys.argv) > 2 else "20260518"
    account = sys.argv[3] if len(sys.argv) > 3 else "证券时报"
    seen: Path | None = None
    for _ in range(600):  # up to ~50 min at 5s interval
        p = find_trace(audit_root, biz_date, account)
        if p and p != seen:
            seen = p
            print(f"[monitor] trace file: {p}", flush=True)
        if p:
            report(p)
            doc = json.loads(p.read_text(encoding="utf-8"))
            if (doc.get("summary") or {}).get("status") in ("success", "failed", "skipped"):
                print("[monitor] job finished.", flush=True)
                return 0
        time.sleep(5)
    print("[monitor] timeout waiting for job end", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
