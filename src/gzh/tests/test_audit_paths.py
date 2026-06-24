from pathlib import Path

from gzh_pipeline.audit.paths import build_audit_trace_path


def test_export_audit_trace_path():
    p = build_audit_trace_path(
        Path("output/reports/gzh"),
        "2026-05-20",
        phase="export",
        run_id="20260521T120000Z_abcd",
        account="证券时报",
    )
    assert p == Path(
        "output/reports/gzh/20260520/export_20260521T120000Z_abcd/20260520_证券时报.trace.json"
    )


def test_parse_audit_trace_path():
    p = build_audit_trace_path(
        Path("audit"),
        "20260520",
        phase="parse",
        run_id="run1",
        account="A",
    )
    assert p == Path("audit/20260520/parse_run1/20260520_A.trace.json")
