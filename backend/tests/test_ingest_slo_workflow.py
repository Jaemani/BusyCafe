from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_ingest_slo_workflow_is_manual_read_only_and_production_scoped() -> None:
    workflow = (
        ROOT / ".github/workflows/analyze-ingest-slo-production.yml"
    ).read_text(encoding="utf-8")
    trigger = workflow[workflow.index("on:\n") : workflow.index("\npermissions:")]

    assert "workflow_dispatch:" in trigger
    assert "schedule:" not in trigger
    assert "environment: Production" in workflow
    assert "DATABASE_URL: ${{ secrets.DATABASE_URL }}" in workflow
    assert "analyze_ingest_slo.py" in workflow
    assert '--window-hours "$WINDOW_HOURS"' in workflow
    assert "--apply" not in workflow
